"""A lab from what the render just wrote: the cabling as nodes and networks.

netclab-chart's values first; a containerlab topology is written from those values, so
both labs are cabled alike.

`rendered` maps each fabric host to the structured config netadopt's own render produced
for it. Every host is a cEOS node. A cable is what an `ethernet_interfaces` entry names
in `metadata.peer` and `metadata.peer_interface`, seen from either end. The ends a
cable joins -- (node, interface) -- form one network: exactly two ends on two nodes are
a `veth`, anything else a `bridge`, because netclab-chart makes a veth pair only for a
network with exactly two ends and names it after the node.

Only `EthernetN` is cabled on a cEOS node, in any case or as `EthN`, as `ethN`; a
subinterface rides on its parent's cable; any other interface is left out and named.
A peer outside the fabric -- a server, a firewall -- becomes the node `connected`
says, or nothing.
"""

from __future__ import annotations

import ipaddress
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from importlib.resources import files

import yaml

from netadopt.xr import LABEL_MAX, rfc1123

# What a lab is written for, as `--for` names it.
NETCLAB = "netclab"
CONTAINERLAB = "containerlab"

# containerlab's kinds for the two node types, and for a network of more than two ends.
CLAB_CEOS = "arista_ceos"
CLAB_LINUX = "linux"
CLAB_BRIDGE = "bridge"
# netclab-chart's image for a linux node (templates/linux.yaml), so both labs run one.
LINUX_IMAGE = "bash:5.3.20"
# containerlab's own default management subnet; docker takes its first address.
MGMT_SUBNET = "172.20.20.0/24"
# netclab-chart's startup-config for every cEOS node, as the chart renders it: the user
# the push logs in as, eAPI, and a control plane that lets the fabric's protocols in.
CEOS_STARTUP_CONFIG = "ceos-startup-config.cfg"
# containerlab hands eth0 to EOS as Management0 (MGMT_INTF=eth0, MAPETH0=1) and writes
# its address into Management0's config; a push replaces the whole configuration with
# the design's, and EOS takes the address off eth0. netclab-chart sets neither, so eth0
# stays the container's, and so it does here: cEOS reads both as set when they are not
# empty, "0" included (FruPlugin/cEOSLab.py, CEosHelper.py).
CEOS_ENV = {"MGMT_INTF": "", "MAPETH0": ""}

# The user the push logs in as on every node, the startup-config's, with its password's
# hash: it stays when a push replaces the design's own AAA.
LAB_USER = "arista"
LAB_PASSWORD = "arista"
LAB_LOCAL_USER = {
    "name": LAB_USER,
    "privilege": 15,
    "role": "network-admin",
    "sha512_password": (
        "$6$Enl0WfE32FthwyiJ$yTyGaEJ2uPKLU.F7314YtB7J1jrzrMi7ogXIRTEHQfLdLgKWWmr1UvNlZLN6AyuxET7G5aH3AI9OYRzxVTkB1."
    ),
}

CEOS = "ceos"
LINUX = "linux"
NONE = "none"
CONNECTED_TYPES = (LINUX, CEOS, NONE)

BRIDGE = "bridge"

# EOS reads Ethernet1, ethernet1 and Eth1 as one interface.
_ETHERNET = re.compile(r"eth(?:ernet)?(\d+)", re.IGNORECASE)
_SUBINTERFACE = re.compile(r"eth(?:ernet)?(\d+)\.\d+", re.IGNORECASE)
# A Linux interface name holds 15 bytes; "/" and whitespace are not allowed in one.
LINUX_INTERFACE_MAX = 15
_NOT_LINUX_INTERFACE = re.compile(r"[^a-z0-9_.-]")

End = tuple[str, str]  # (host, interface)


@dataclass(frozen=True)
class Ceos:
    """What a cEOS node sets over netclab-chart's own defaults; None leaves the chart's."""

    image: str | None = None
    memory: str | None = None
    cpu: str | None = None


CHART_DEFAULTS = Ceos()


@dataclass(frozen=True)
class Lab:
    """netclab-chart values, what was left out of them, or why there are none."""

    values: dict | None = None
    notes: tuple[str, ...] = ()
    problem: str | None = None
    hosts: Mapping[str, str] = field(default_factory=dict)  # fabric host -> its node


def netclab_values(
    rendered: Mapping[str, dict], connected: str = LINUX, ceos: Ceos = CHART_DEFAULTS
) -> Lab:
    """The values for netclab-chart from each fabric host's rendered structured config."""
    notes: list[str] = []
    node_types: dict[str, str] = {host: CEOS for host in rendered}
    parent: dict[End, End] = {}
    # a linux node's port name, spelled, and the peer_interface first spelled so
    ports: dict[End, str] = {}
    renamed: set[End] = set()
    # a subinterface rides on its parent's cable: (host, the parent as ethN, its own name)
    riding: list[tuple[str, str, str]] = []
    # a cable to a linux node that names no port there: (local end, peer, where)
    unnamed: list[tuple[End, str, str]] = []

    def root(end: End) -> End:
        parent.setdefault(end, end)
        while parent[end] != end:
            parent[end] = parent[parent[end]]
            end = parent[end]
        return end

    def join(one: End, other: End) -> None:
        parent[root(one)] = root(other)

    for host in sorted(rendered):
        for entry in rendered[host].get("ethernet_interfaces") or []:
            metadata = entry.get("metadata") or {}
            peer = metadata.get("peer")
            if not peer:
                continue
            name = entry.get("name", "")
            local = _ceos_interface(name)
            if local is None:
                sub = _SUBINTERFACE.fullmatch(name) if isinstance(name, str) else None
                if sub:
                    riding.append((host, f"eth{sub.group(1)}", name))
                else:
                    notes.append(f"{host} {name}: not cabled -- only EthernetN is")
                continue

            peer_interface = metadata.get("peer_interface")
            where = f"{host} {name} - {peer} {peer_interface}"
            if peer in rendered:
                far = _ceos_interface(peer_interface)
            elif connected == NONE:
                notes.append(
                    f"{where}: not cabled -- {peer} is outside the fabric, --connected none"
                )
                continue
            elif connected == CEOS:
                far = _ceos_interface(peer_interface)
                node_types.setdefault(peer, CEOS)
            else:
                node_types.setdefault(peer, LINUX)
                if peer_interface is None:
                    unnamed.append(((host, local), peer, f"{host} {name} - {peer}"))
                    continue
                far = _linux_interface(peer_interface)
                if far is not None:
                    first = ports.setdefault((peer, far), peer_interface)
                    if first.lower() != peer_interface.lower():
                        # two ports of one node under one name would be one port
                        notes.append(f"{where}: not cabled -- {first!r} is spelled {far} too")
                        continue
                    if far != peer_interface.lower() and (peer, far) not in renamed:
                        renamed.add((peer, far))
                        notes.append(f"{peer} {peer_interface} is spelled {far}")
            if far is None:
                notes.append(
                    f"{where}: not cabled -- no interface for {peer} from {peer_interface!r}"
                )
                continue

            join((host, local), (peer, far))

    # After every named port is known, so a given name never takes one written elsewhere.
    for local, peer, where in unnamed:
        number = 1
        while (peer, f"eth{number}") in ports:
            number += 1
        ports[(peer, f"eth{number}")] = ""
        join(local, (peer, f"eth{number}"))
        notes.append(f"{where}: {peer} names no port, cabled as eth{number}")

    for host, carrier, name in riding:
        if (host, carrier) not in parent:
            notes.append(
                f"{host} {name}: not cabled -- only EthernetN is, and {carrier} has no cable"
            )

    spelled, problem = _spell(node_types, notes)
    if problem:
        return Lab(notes=tuple(notes), problem=problem)

    groups: dict[End, list[End]] = {}
    for end in parent:
        if end[0] in spelled:  # a node left out takes its cables with it
            groups.setdefault(root(end), []).append(end)
    networks: list[dict] = []
    interfaces: dict[str, list[tuple[str, str]]] = {host: [] for host in spelled}
    for ends in sorted(sorted(group) for group in groups.values() if len(group) > 1):
        network = {"name": f"n{len(networks) + 1}"}
        if len(ends) != 2 or ends[0][0] == ends[1][0]:
            network["type"] = BRIDGE
        networks.append(network)
        for node, interface in ends:
            interfaces[node].append((interface, network["name"]))

    nodes = []
    for host in sorted(spelled, key=lambda host: spelled[host]):
        node: dict = {"name": spelled[host], "type": node_types[host]}
        if node_types[host] == CEOS:
            node.update({k: v for k, v in vars(ceos).items() if v is not None})
        node["interfaces"] = [
            {"name": interface, "network": network}
            for interface, network in sorted(interfaces[host], key=lambda pair: _natural(pair[0]))
        ]
        nodes.append(node)

    return Lab(
        values={"topology": {"networks": networks, "nodes": nodes}},
        notes=tuple(notes),
        hosts={host: spelled[host] for host in rendered if host in spelled},
    )


# What the extra vars look each host's address up in: a Service's name in netclab, the
# management address containerlab gives the node.
SERVICES = "lab_services"
ADDRESSES = "lab_addresses"


def lab_extra_vars(
    addresses: Mapping[str, str], radius: Mapping[str, object], key: str = SERVICES
) -> dict:
    """What `ansible-playbook -e` needs to push to the lab's devices instead of the real ones.

    The address is looked up per host under `key`, and a host left out of the lab stops
    with its name rather than being pushed to an address that is someone else's. eAPI
    moves to the default VRF: a cEOS node has no management interface for the design's
    own VRF.

    The twin's AAA is the lab's alone. The push logs in as LAB_USER, and aaa_settings is
    that one local user -- so that no TACACS+ or RADIUS server the lab cannot reach stands
    between a push and the switch, and the user is still there after a push replaces the
    configuration. The design's own RADIUS stays on the hosts in `radius`: 802.1X does not
    render without a server.
    """
    return {
        key: dict(sorted(addresses.items())),
        "ansible_host": (
            f"{{{{ {key}[inventory_hostname]"
            " | mandatory(inventory_hostname ~ ' has no node in the lab') }}"
        ),
        # `enabled` is written, because AVD reads a block without it as disabled.
        "management_eapi": {"enabled": True, "vrfs": [{"name": "default", "enabled": True}]},
        "ansible_user": LAB_USER,
        "ansible_password": LAB_PASSWORD,
        "lab_local_user": LAB_LOCAL_USER,
        "lab_radius": dict(sorted(radius.items())),
        "aaa_settings": (
            "{{ {'local_users': [lab_local_user]}"
            " | combine({'radius': lab_radius[inventory_hostname]}"
            " if inventory_hostname in lab_radius else {}) }}"
        ),
    }


@dataclass(frozen=True)
class Containerlab:
    """A containerlab topology, each fabric host's management address in it, or why none."""

    topology: dict | None = None
    addresses: Mapping[str, str] = field(default_factory=dict)  # fabric host -> its address
    problem: str | None = None


def containerlab_topology(
    lab: Lab, name: str, ceos_image: str, subnet: str = MGMT_SUBNET, network: str | None = None
) -> Containerlab:
    """netclab-chart's values as a containerlab topology, cabled alike.

    A network of two ends is a link. A bridge is containerlab's bridge inside a linux
    node of the network's own name, so that nothing has to exist on the host first. Every
    node gets a fixed management address, in name order, so that the extra vars can name
    it before the lab is up; docker keeps the subnet's first address.
    """
    topology = (lab.values or {}).get("topology") or {}
    ends: dict[str, list[tuple[str, str]]] = {
        network["name"]: [] for network in topology.get("networks", [])
    }
    for node in topology.get("nodes", []):
        for interface in node["interfaces"]:
            ends[interface["network"]].append((node["name"], interface["name"]))

    nodes: dict[str, dict] = {
        node["name"]: {"kind": CLAB_CEOS if node["type"] == CEOS else CLAB_LINUX}
        for node in topology.get("nodes", [])
    }
    links: list[dict] = []
    for each in topology.get("networks", []):
        joined = sorted(ends[each["name"]])
        if each.get("type") != BRIDGE:
            links.append({"endpoints": [f"{node}:{interface}" for node, interface in joined]})
            continue
        holder = each["name"]
        if holder in nodes:
            return Containerlab(problem=f"node {holder} and network {holder} share a name")
        nodes[holder] = {"kind": CLAB_LINUX}
        bridge = f"{holder}|{holder}"
        nodes[bridge] = {"kind": CLAB_BRIDGE, "network-mode": f"container:{holder}"}
        for port, (node, interface) in enumerate(joined, start=1):
            links.append({"endpoints": [f"{node}:{interface}", f"{bridge}:p{port}"]})

    try:
        within = ipaddress.ip_network(subnet)
    except ValueError as err:
        return Containerlab(problem=f"--mgmt-subnet {subnet}: {err}")
    containers = sorted(node for node, spec in nodes.items() if spec["kind"] != CLAB_BRIDGE)
    free = within.hosts()
    next(free, None)  # docker's gateway
    given: dict[str, str] = {}
    for node in containers:
        address = next(free, None)
        if address is None:
            return Containerlab(problem=f"{len(containers)} nodes, and {within} holds fewer")
        given[node] = str(address)
        nodes[node]["mgmt-ipv4"] = given[node]

    kinds: dict[str, dict] = {
        CLAB_CEOS: {"image": ceos_image, "startup-config": startup_config(), "env": CEOS_ENV}
    }
    if any(spec["kind"] == CLAB_LINUX for spec in nodes.values()):
        kinds[CLAB_LINUX] = {"image": LINUX_IMAGE}
    return Containerlab(
        topology={
            "name": name,
            "mgmt": {"network": network or name, "ipv4-subnet": str(within)},
            "topology": {"kinds": kinds, "nodes": dict(sorted(nodes.items())), "links": links},
        },
        addresses={host: given[node] for host, node in lab.hosts.items() if node in given},
    )


def startup_config() -> str:
    """netclab-chart's startup-config for a cEOS node, as the package carries it."""
    return files("netadopt").joinpath(CEOS_STARTUP_CONFIG).read_text(encoding="utf-8")


class _TopologyDumper(yaml.SafeDumper):
    """A string of several lines -- the startup-config -- as a block, and a link's two
    endpoints on one line, as containerlab's own topologies write them."""


def _block(dumper: yaml.SafeDumper, data: str) -> yaml.Node:
    style = "|" if "\n" in data else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", data, style=style)


def _endpoints(dumper: yaml.SafeDumper, data: list) -> yaml.Node:
    flow = bool(data) and all(isinstance(item, str) for item in data)
    return dumper.represent_sequence("tag:yaml.org,2002:seq", data, flow_style=flow)


_TopologyDumper.add_representer(str, _block)
_TopologyDumper.add_representer(list, _endpoints)


def topology_yaml(topology: dict) -> str:
    """The topology as a `.clab.yml`: keys in their own order, the config as a block."""
    return yaml.dump(topology, Dumper=_TopologyDumper, sort_keys=False, width=float("inf"))


def values_yaml(values: dict) -> str:
    """The values as a file for `helm -f`: keys in their own order, long lines never folded."""
    return yaml.safe_dump(values, sort_keys=False, width=float("inf"))


def _ceos_interface(name: object) -> str | None:
    match = _ETHERNET.fullmatch(name) if isinstance(name, str) else None
    return f"eth{match.group(1)}" if match else None


def _linux_interface(name: object) -> str | None:
    """`PCI1` as pci1, `NIC 1` as nic_1; None when nothing or more than 15 bytes is left."""
    if not isinstance(name, str):
        return None
    spelled = _NOT_LINUX_INTERFACE.sub("_", name.lower())
    return spelled if 0 < len(spelled.encode()) <= LINUX_INTERFACE_MAX else None


def _spell(node_types: Mapping[str, str], notes: list[str]) -> tuple[dict[str, str], str | None]:
    """Each host's node name: a Pod's and a Service's, so lowercase, a letter first, at most 63.

    A host no name can be spelled from is left out and named; two hosts spelled alike
    stop the lab, as either one left out would be a guess.
    """
    spelled: dict[str, str] = {}
    owner: dict[str, str] = {}
    for host in sorted(node_types):
        name = rfc1123(host)
        if not name or not name[0].isalpha() or len(name) > LABEL_MAX:
            notes.append(
                f"{host}: left out, with its cables -- no node name can be spelled from it"
            )
            continue
        if name in owner:
            return spelled, f"{owner[name]} and {host} are both spelled {name}"
        if name != host:
            notes.append(f"{host} is spelled {name}")
        owner[name] = host
        spelled[host] = name
    return spelled, None


def _natural(interface: str) -> tuple[str, int]:
    """eth2 before eth10."""
    match = re.fullmatch(r"(\D*)(\d*)", interface)
    head, digits = match.groups() if match else (interface, "")
    return head, int(digits) if digits else -1
