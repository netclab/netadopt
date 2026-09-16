"""A lab for netclab-chart, from what the render just wrote: the cabling as nodes and networks.

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

import re
from collections.abc import Mapping
from dataclasses import dataclass

from netadopt.xr import LABEL_MAX, rfc1123

CEOS = "ceos"
LINUX = "linux"
NONE = "none"
CONNECTED_KINDS = (LINUX, CEOS, NONE)

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


def netclab_values(
    rendered: Mapping[str, dict], connected: str = LINUX, ceos: Ceos = CHART_DEFAULTS
) -> Lab:
    """The values for netclab-chart from each fabric host's rendered structured config."""
    notes: list[str] = []
    kinds: dict[str, str] = {host: CEOS for host in rendered}
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
                notes.append(f"{where}: not cabled -- {peer} is outside the fabric, --connected none")
                continue
            elif connected == CEOS:
                far = _ceos_interface(peer_interface)
                kinds.setdefault(peer, CEOS)
            else:
                kinds.setdefault(peer, LINUX)
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
                notes.append(f"{where}: not cabled -- no interface for {peer} from {peer_interface!r}")
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
            notes.append(f"{host} {name}: not cabled -- only EthernetN is, and {carrier} has no cable")

    spelled, problem = _spell(kinds, notes)
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
        node: dict = {"name": spelled[host], "type": kinds[host]}
        if kinds[host] == CEOS:
            node.update({k: v for k, v in vars(ceos).items() if v is not None})
        node["interfaces"] = [
            {"name": interface, "network": network}
            for interface, network in sorted(interfaces[host], key=lambda pair: _natural(pair[0]))
        ]
        nodes.append(node)

    return Lab(values={"topology": {"networks": networks, "nodes": nodes}}, notes=tuple(notes))


def _ceos_interface(name: object) -> str | None:
    match = _ETHERNET.fullmatch(name) if isinstance(name, str) else None
    return f"eth{match.group(1)}" if match else None


def _linux_interface(name: object) -> str | None:
    """`PCI1` as pci1, `NIC 1` as nic_1; None when nothing or more than 15 bytes is left."""
    if not isinstance(name, str):
        return None
    spelled = _NOT_LINUX_INTERFACE.sub("_", name.lower())
    return spelled if 0 < len(spelled.encode()) <= LINUX_INTERFACE_MAX else None


def _spell(kinds: Mapping[str, str], notes: list[str]) -> tuple[dict[str, str], str | None]:
    """Each host's node name: a Pod's and a Service's, so lowercase, a letter first, at most 63.

    A host no name can be spelled from is left out and named; two hosts spelled alike
    stop the lab, as either one left out would be a guess.
    """
    spelled: dict[str, str] = {}
    owner: dict[str, str] = {}
    for host in sorted(kinds):
        name = rfc1123(host)
        if not name or not name[0].isalpha() or len(name) > LABEL_MAX:
            notes.append(f"{host}: left out, with its cables -- no node name can be spelled from it")
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
