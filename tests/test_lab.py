"""netclab-chart values from a render: dicts stand in for the structured configs it writes."""

from __future__ import annotations

from netadopt.lab import BRIDGE, CEOS, LINUX, NONE, Ceos, netclab_values


def cable(name: str, peer: str, peer_interface: str | None, peer_type: str = "spine") -> dict:
    metadata = {"peer": peer, "peer_type": peer_type}
    if peer_interface is not None:
        metadata["peer_interface"] = peer_interface
    return {"name": name, "metadata": metadata}


def rendered(*cables: dict) -> dict:
    return {"ethernet_interfaces": list(cables)}


def nodes(lab) -> dict[str, dict]:
    return {node["name"]: node for node in lab.values["topology"]["nodes"]}


def test_a_cable_seen_from_both_ends_is_one_veth_network():
    lab = netclab_values(
        {
            "dc1-leaf1a": rendered(cable("Ethernet1", "dc1-spine1", "Ethernet1")),
            "dc1-spine1": rendered(cable("Ethernet1", "dc1-leaf1a", "Ethernet1", "l3leaf")),
        }
    )

    assert lab.problem is None
    assert lab.values["topology"]["networks"] == [{"name": "n1"}]
    assert nodes(lab)["dc1-leaf1a"] == {
        "name": "dc1-leaf1a",
        "type": CEOS,
        "interfaces": [{"name": "eth1", "network": "n1"}],
    }
    assert nodes(lab)["dc1-spine1"]["interfaces"] == [{"name": "eth1", "network": "n1"}]
    assert lab.notes == ()


def test_a_device_with_no_cable_is_still_a_node():
    lab = netclab_values({"dc1-spine1": rendered(), "dc1-spine2": {}})

    assert sorted(nodes(lab)) == ["dc1-spine1", "dc1-spine2"]
    assert lab.values["topology"]["networks"] == []


def test_interfaces_are_in_natural_order():
    lab = netclab_values(
        {
            "leaf": rendered(
                cable("Ethernet10", "spine", "Ethernet2"), cable("Ethernet2", "spine", "Ethernet1")
            ),
            "spine": rendered(),
        }
    )

    assert [i["name"] for i in nodes(lab)["leaf"]["interfaces"]] == ["eth2", "eth10"]


def test_only_ethernet_n_is_cabled_on_ceos_and_the_rest_is_named():
    lab = netclab_values(
        {
            "leaf": rendered(
                cable("Ethernet1/1", "spine", "Ethernet1"),
                cable("Ethernet2.10", "spine", "Ethernet2"),
            ),
            "spine": rendered(),
        }
    )

    assert lab.values["topology"]["networks"] == []
    assert lab.notes == (
        "leaf Ethernet1/1: not cabled -- only EthernetN is",
        "leaf Ethernet2.10: not cabled -- only EthernetN is, and eth2 has no cable",
    )


def test_ethernet_in_any_case_and_eth_are_one_interface_on_ceos():
    # EOS reads Ethernet1, ethernet1 and Eth1 alike
    lab = netclab_values(
        {
            "leaf": rendered(
                cable("ethernet1", "spine", "Eth1"), cable("Eth2.10", "spine", "ETHERNET2.10")
            ),
            "spine": rendered(),
        }
    )

    assert nodes(lab)["leaf"]["interfaces"] == [{"name": "eth1", "network": "n1"}]
    assert nodes(lab)["spine"]["interfaces"] == [{"name": "eth1", "network": "n1"}]
    assert lab.notes == ("leaf Eth2.10: not cabled -- only EthernetN is, and eth2 has no cable",)


def test_a_subinterface_rides_its_cabled_parent_without_a_word():
    lab = netclab_values(
        {
            "leaf": rendered(
                cable("Ethernet1", "spine", "Ethernet1"),
                cable("Ethernet1.100", "spine", "Ethernet1.100"),
                cable("Ethernet3.200", "spine", "Ethernet3.200"),
            ),
            "spine": rendered(),
        }
    )

    assert nodes(lab)["leaf"]["interfaces"] == [{"name": "eth1", "network": "n1"}]
    assert lab.notes == (
        "leaf Ethernet3.200: not cabled -- only EthernetN is, and eth3 has no cable",
    )


def test_a_peer_outside_the_fabric_is_a_linux_node_by_default():
    lab = netclab_values(
        {"leaf": rendered(cable("Ethernet5", "dc1-leaf1-server1", "PCI1", "server"))}
    )

    assert nodes(lab)["dc1-leaf1-server1"] == {
        "name": "dc1-leaf1-server1",
        "type": LINUX,
        "interfaces": [{"name": "pci1", "network": "n1"}],
    }
    assert lab.notes == ()  # lowercase alone is not worth a line


def test_a_linux_port_name_is_spelled_for_linux_and_named():
    lab = netclab_values(
        {
            "leaf": rendered(
                cable("Ethernet5", "server1", "NIC 1", "server"),
                cable("Ethernet6", "server1", "Ethernet1/1", "server"),
            )
        }
    )

    assert [i["name"] for i in nodes(lab)["server1"]["interfaces"]] == ["ethernet1_1", "nic_1"]
    assert "server1 NIC 1 is spelled nic_1" in lab.notes
    assert "server1 Ethernet1/1 is spelled ethernet1_1" in lab.notes


def test_a_linux_port_name_over_15_bytes_is_not_cabled():
    lab = netclab_values(
        {"leaf": rendered(cable("Ethernet5", "server1", "Management-Port-1", "server"))}
    )

    assert lab.values["topology"]["networks"] == []
    assert lab.notes == (
        (
            "leaf Ethernet5 - server1 Management-Port-1: not cabled -- "
            "no interface for server1 from 'Management-Port-1'"
        ),
    )


def test_one_linux_port_written_in_two_cases_is_one_port():
    lab = netclab_values(
        {
            "leaf1": rendered(cable("Ethernet5", "peer1", "Ethernet1", "other")),
            "leaf2": rendered(cable("Ethernet5", "peer1", "ethernet1", "other")),
        }
    )

    assert nodes(lab)["peer1"]["interfaces"] == [{"name": "ethernet1", "network": "n1"}]
    assert lab.values["topology"]["networks"] == [{"name": "n1", "type": BRIDGE}]
    assert lab.notes == ()


def test_two_linux_ports_spelled_alike_cable_only_the_first():
    # one name for two ports would make them one port
    lab = netclab_values(
        {
            "leaf": rendered(
                cable("Ethernet5", "server1", "NIC 1", "server"),
                cable("Ethernet6", "server1", "NIC/1", "server"),
            )
        }
    )

    assert nodes(lab)["server1"]["interfaces"] == [{"name": "nic_1", "network": "n1"}]
    assert "leaf Ethernet6 - server1 NIC/1: not cabled -- 'NIC 1' is spelled nic_1 too" in lab.notes


def test_a_linux_peer_naming_no_port_gets_the_first_free_eth_and_it_is_named():
    lab = netclab_values(
        {
            "leaf1": rendered(cable("Ethernet5", "server1", None, "server")),
            "leaf2": rendered(
                cable("Ethernet5", "server1", "eth1", "server"),
                cable("Ethernet6", "server1", None, "server"),
            ),
        }
    )

    # eth1 is written for leaf2, so the unnamed ports take eth2 and eth3
    assert nodes(lab)["server1"]["interfaces"] == [
        {"name": "eth1", "network": "n2"},
        {"name": "eth2", "network": "n1"},
        {"name": "eth3", "network": "n3"},
    ]
    assert lab.notes == (
        "leaf1 Ethernet5 - server1: server1 names no port, cabled as eth2",
        "leaf2 Ethernet6 - server1: server1 names no port, cabled as eth3",
    )


def test_a_host_no_node_name_can_be_spelled_from_is_left_out_with_its_cables():
    # a Service's name starts with a letter
    lab = netclab_values(
        {
            "7010tx-leaf1": rendered(cable("Ethernet1", "spine1", "Ethernet1")),
            "leaf2": rendered(cable("Ethernet1", "spine1", "Ethernet2")),
            "spine1": rendered(),
        }
    )

    assert lab.problem is None
    assert sorted(nodes(lab)) == ["leaf2", "spine1"]
    assert nodes(lab)["spine1"]["interfaces"] == [{"name": "eth2", "network": "n1"}]
    assert (
        "7010tx-leaf1: left out, with its cables -- no node name can be spelled from it"
        in lab.notes
    )


def test_connected_none_leaves_the_peer_out_and_names_it():
    lab = netclab_values(
        {"leaf": rendered(cable("Ethernet5", "server1", "PCI1", "server"))}, connected=NONE
    )

    assert sorted(nodes(lab)) == ["leaf"]
    assert lab.notes == (
        "leaf Ethernet5 - server1 PCI1: not cabled -- server1 is outside the fabric, --connected none",
    )


def test_connected_ceos_cables_only_an_ethernet_n_peer_interface():
    lab = netclab_values(
        {
            "leaf": rendered(
                cable("Ethernet5", "server1", "PCI1", "server"),
                cable("Ethernet6", "firewall1", "Ethernet3", "firewall"),
            )
        },
        connected=CEOS,
    )

    assert nodes(lab)["firewall1"]["interfaces"] == [{"name": "eth3", "network": "n1"}]
    assert (
        "leaf Ethernet5 - server1 PCI1: not cabled -- no interface for server1 from 'PCI1'"
        in lab.notes
    )


def test_more_than_two_ends_are_one_bridge():
    # one server port written to two switches
    lab = netclab_values(
        {
            "leaf1": rendered(cable("Ethernet5", "server1", "eth0", "server")),
            "leaf2": rendered(cable("Ethernet5", "server1", "eth0", "server")),
        }
    )

    assert lab.values["topology"]["networks"] == [{"name": "n1", "type": BRIDGE}]
    assert nodes(lab)["server1"]["interfaces"] == [{"name": "eth0", "network": "n1"}]


def test_a_cable_between_two_ports_of_one_node_is_a_bridge():
    # netclab-chart names a veth after its node, so both ends would get one name
    lab = netclab_values({"leaf": rendered(cable("Ethernet1", "leaf", "Ethernet2"))})

    assert lab.values["topology"]["networks"] == [{"name": "n1", "type": BRIDGE}]


def test_a_name_outside_the_charts_rule_is_lowercased_and_named():
    lab = netclab_values(
        {
            "DC1-POD1-SPINE1": rendered(cable("Ethernet1", "DC1.POD1.LEAF2A", "Ethernet1")),
            "DC1.POD1.LEAF2A": rendered(),
        }
    )

    assert sorted(nodes(lab)) == ["dc1-pod1-leaf2a", "dc1-pod1-spine1"]
    assert "DC1-POD1-SPINE1 is spelled dc1-pod1-spine1" in lab.notes
    assert "DC1.POD1.LEAF2A is spelled dc1-pod1-leaf2a" in lab.notes


def test_two_names_spelled_alike_are_refused():
    lab = netclab_values({"DC1.LEAF1": rendered(), "dc1-leaf1": rendered()})

    assert lab.values is None
    assert lab.problem == "DC1.LEAF1 and dc1-leaf1 are both spelled dc1-leaf1"


def test_ceos_settings_are_written_only_when_given():
    fabric = {"leaf": rendered(cable("Ethernet1", "server1", "eth0", "server"))}

    plain = netclab_values(fabric)
    set_ = netclab_values(fabric, ceos=Ceos(image="ceos:4.35.0F", memory="2Gi", cpu="1000m"))

    assert "memory" not in nodes(plain)["leaf"]
    assert nodes(set_)["leaf"]["image"] == "ceos:4.35.0F"
    assert nodes(set_)["leaf"]["memory"] == "2Gi"
    assert nodes(set_)["leaf"]["cpu"] == "1000m"
    assert "memory" not in nodes(set_)["server1"]  # a linux node keeps the chart's defaults
