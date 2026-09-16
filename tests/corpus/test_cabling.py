"""The lab, from AVD's own render of somebody else's repository.

Properties only, no counts: what the render wrote either reaches the values or is
named, and what reaches them is a topology netclab-chart can make.

netclab-chart names both the Pod and the Service `{{ .name }}`, the node's own name
with nothing prefixed (`templates/ceos/ceos.yaml`, `templates/linux.yaml`), so a node
name has to be what a Service takes: a letter first, then letters, digits and dashes,
at most 63.

The render runs once per repository and every test here reads that one result.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from netadopt.lab import BRIDGE, Lab
from netadopt.render import Rendered
from netadopt.xr import LABEL_MAX, rfc1123

# The interfaces a cEOS node can be cabled on, as lab.py takes them and spells them:
# EthernetN in any case, never a subinterface.
CEOS_INTERFACE = re.compile(r"eth(?:ernet)?(\d+)", re.IGNORECASE)

# lab.py's own words for an end it did not carry.
NOT_CABLED = "not cabled"

End = tuple[str, str]  # (node, interface)


def test_a_node_name_is_one_the_chart_can_give_a_service(lab: Lab):
    nodes = _nodes(lab)

    assert len(nodes) == len(lab.values["topology"]["nodes"])  # two hosts, never one name
    for name in nodes:
        assert name == rfc1123(name), name
        assert name[:1].isalpha() and len(name) <= LABEL_MAX, name


def test_every_rendered_host_is_a_node_or_is_named(rendered: Rendered, lab: Lab):
    nodes = _nodes(lab)

    for host in rendered.hosts:
        assert rfc1123(host) in nodes or _names(lab.notes, host), host


def test_every_interface_names_a_network_that_is_there(lab: Lab):
    networks = _networks(lab)

    for node in lab.values["topology"]["nodes"]:
        named = [interface["name"] for interface in node["interfaces"]]
        assert len(named) == len(set(named)), node["name"]  # one port, one cable
        for interface in node["interfaces"]:
            assert interface["network"] in networks, (node["name"], interface)


def test_a_network_is_a_veth_only_with_two_ends_on_two_nodes(lab: Lab):
    """netclab-chart makes a veth pair for exactly two ends; anything else is a bridge."""
    ends = _ends(lab)

    for name, network in _networks(lab).items():
        joined = ends.get(name, [])
        assert len(joined) > 1, f"{name} joins {joined}"
        if network.get("type") != BRIDGE:
            assert len(joined) == 2 and joined[0][0] != joined[1][0], f"{name} joins {joined}"


def test_every_cable_is_an_interface_of_its_node_or_is_named(rendered: Rendered, lab: Lab):
    """Nothing is dropped in silence, the property `report` and `emit` hold too.

    Membership, never a count: a node also carries the ends its peers name, and
    `inet-cloud` of cv-pathfinder has eight of those and declares none itself.
    """
    nodes = _nodes(lab)

    for host, config in rendered.hosts.items():
        node = nodes.get(rfc1123(host))
        if node is None:  # a host left out takes its cables with it, and is named above
            continue
        ends = {interface["name"] for interface in node["interfaces"]}
        for name in _cabled(config):
            spelled = CEOS_INTERFACE.fullmatch(name)
            if spelled is None:  # a subinterface or a breakout, which lab.py never cables
                continue
            assert f"eth{spelled.group(1)}" in ends or _not_cabled(lab.notes, host, name), (
                f"{host} {name}"
            )


def _nodes(lab: Lab) -> dict[str, dict]:
    return {node["name"]: node for node in lab.values["topology"]["nodes"]}


def _networks(lab: Lab) -> dict[str, dict]:
    return {network["name"]: network for network in lab.values["topology"]["networks"]}


def _ends(lab: Lab) -> dict[str, list[End]]:
    """The (node, interface) pairs each network joins."""
    ends: dict[str, list[End]] = {}
    for node in lab.values["topology"]["nodes"]:
        for interface in node["interfaces"]:
            ends.setdefault(interface["network"], []).append((node["name"], interface["name"]))
    return ends


def _cabled(config: dict) -> list[str]:
    """Each `ethernet_interfaces` entry naming a peer, by its interface name."""
    found = []
    for entry in config.get("ethernet_interfaces") or []:
        peer = (entry.get("metadata") or {}).get("peer")
        if peer and isinstance(entry.get("name"), str):
            found.append(entry["name"])
    return found


def _names(notes: Iterable[str], host: str) -> bool:
    return any(host in note for note in notes)


def _not_cabled(notes: Iterable[str], host: str, interface: str) -> bool:
    """A note about this end of this host, saying it was not cabled.

    A note names the interface and then either ends the clause or names the peer, and
    the name has to end there too: `Ethernet1/4` must never answer for `Ethernet1`.
    """
    starts = (f"{host} {interface}:", f"{host} {interface} ")
    return any(note.startswith(starts) and NOT_CABLED in note for note in notes)
