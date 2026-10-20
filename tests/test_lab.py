"""netclab-chart values from a render: dicts stand in for the structured configs it writes."""

from __future__ import annotations

import yaml

from netadopt.lab import (
    ADDRESSES,
    BRIDGE,
    CEOS,
    CEOS_ENV,
    CLAB_BRIDGE,
    CLAB_CEOS,
    CLAB_LINUX,
    LAB_LOCAL_USER,
    LINUX,
    LINUX_IMAGE,
    MGMT_SUBNET,
    NONE,
    SERVICES,
    Ceos,
    Lab,
    containerlab_topology,
    lab_extra_vars,
    netclab_values,
    startup_config,
    topology_yaml,
)


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


# The fabric of the containerlab tests: two switches and a server, cabled as AVD writes it.
SPINE_LEAF = netclab_values(
    {
        "dc1-spine1": rendered(cable("Ethernet1", "dc1-leaf1a", "Ethernet1", "l3leaf")),
        "dc1-leaf1a": rendered(
            cable("Ethernet1", "dc1-spine1", "Ethernet1"),
            cable("Ethernet5", "server1", "PCI1", "server"),
        ),
    }
)


def test_a_veth_network_is_a_link_between_its_two_ends():
    clab = containerlab_topology(SPINE_LEAF, "dc1", "ceos:4.36.1F")

    assert clab.problem is None
    assert clab.topology["topology"]["links"] == [
        {"endpoints": ["dc1-leaf1a:eth1", "dc1-spine1:eth1"]},
        {"endpoints": ["dc1-leaf1a:eth5", "server1:pci1"]},
    ]


def test_every_ceos_node_runs_the_image_given_and_the_charts_startup_config():
    clab = containerlab_topology(SPINE_LEAF, "dc1", "ceos:4.36.1F")

    kinds = clab.topology["topology"]["kinds"]
    assert kinds[CLAB_CEOS] == {
        "image": "ceos:4.36.1F",
        "startup-config": startup_config(),
        "env": CEOS_ENV,
    }
    assert kinds[CLAB_LINUX] == {"image": LINUX_IMAGE}
    assert {name: node["kind"] for name, node in clab.topology["topology"]["nodes"].items()} == {
        "dc1-leaf1a": CLAB_CEOS,
        "dc1-spine1": CLAB_CEOS,
        "server1": CLAB_LINUX,
    }


def test_eth0_stays_the_containers_as_in_netclab():
    # cEOS reads each as set when it is not empty, "0" included: MAPETH0="0" makes eth0
    # Ethernet510 and takes its address. Empty is netclab-chart's: neither is set there.
    assert CEOS_ENV == {"MGMT_INTF": "", "MAPETH0": ""}


def test_the_startup_config_is_the_charts_user_and_eapi():
    config = startup_config()

    assert config.startswith("!\nusername arista privilege 15 secret 0 arista\n")
    assert "management api http-commands\n  protocol https\n  no shutdown\n" in config
    assert config.endswith("end\n")


def test_no_linux_node_and_no_linux_kind():
    clab = containerlab_topology(
        netclab_values({"dc1-spine1": rendered(), "dc1-leaf1a": rendered()}), "dc1", "ceos"
    )

    assert set(clab.topology["topology"]["kinds"]) == {CLAB_CEOS}


def test_every_node_takes_a_fixed_address_in_name_order_after_dockers():
    clab = containerlab_topology(SPINE_LEAF, "dc1", "ceos")

    nodes_ = clab.topology["topology"]["nodes"]
    assert [nodes_[name]["mgmt-ipv4"] for name in sorted(nodes_)] == [
        "172.20.20.2",
        "172.20.20.3",
        "172.20.20.4",
    ]
    assert clab.topology["mgmt"] == {"network": "dc1", "ipv4-subnet": MGMT_SUBNET}
    # The fabric's hosts only: the server is no host the extra vars push to.
    assert clab.addresses == {"dc1-leaf1a": "172.20.20.2", "dc1-spine1": "172.20.20.3"}


def test_the_network_and_subnet_given_are_the_labs():
    clab = containerlab_topology(SPINE_LEAF, "dc1", "ceos", "10.99.0.0/29", "kind")

    assert clab.topology["mgmt"] == {"network": "kind", "ipv4-subnet": "10.99.0.0/29"}
    assert clab.addresses == {"dc1-leaf1a": "10.99.0.2", "dc1-spine1": "10.99.0.3"}


def test_a_subnet_too_small_for_every_node_is_a_problem():
    clab = containerlab_topology(SPINE_LEAF, "dc1", "ceos", "10.99.0.0/30")

    assert clab.topology is None
    assert clab.problem == "3 nodes, and 10.99.0.0/30 holds fewer"


def test_a_subnet_that_is_not_one_is_a_problem():
    clab = containerlab_topology(SPINE_LEAF, "dc1", "ceos", "10.99.0.1/24")

    assert clab.topology is None
    assert clab.problem.startswith("--mgmt-subnet 10.99.0.1/24: ")


# Three switches on one segment: a bridge in netclab.
SEGMENT = netclab_values(
    {
        "sw1": rendered(
            cable("Ethernet1", "sw2", "Ethernet1"), cable("Ethernet1", "sw3", "Ethernet1")
        ),
        "sw2": rendered(),
        "sw3": rendered(),
    }
)


def test_a_bridge_is_containerlabs_bridge_inside_a_node_of_the_networks_name():
    assert SEGMENT.values["topology"]["networks"] == [{"name": "n1", "type": BRIDGE}]

    clab = containerlab_topology(SEGMENT, "seg", "ceos")

    nodes_ = clab.topology["topology"]["nodes"]
    assert nodes_["n1"]["kind"] == CLAB_LINUX
    assert nodes_["n1|n1"] == {"kind": CLAB_BRIDGE, "network-mode": "container:n1"}
    assert "mgmt-ipv4" not in nodes_["n1|n1"]  # no container of its own
    assert clab.topology["topology"]["links"] == [
        {"endpoints": ["sw1:eth1", "n1|n1:p1"]},
        {"endpoints": ["sw2:eth1", "n1|n1:p2"]},
        {"endpoints": ["sw3:eth1", "n1|n1:p3"]},
    ]


def test_a_node_named_as_a_bridge_network_is_a_problem():
    taken = Lab(
        values={
            "topology": {
                "networks": [{"name": "n1", "type": BRIDGE}],
                "nodes": [{"name": "n1", "type": CEOS, "interfaces": []}],
            }
        }
    )

    assert containerlab_topology(taken, "x", "ceos").problem == (
        "node n1 and network n1 share a name"
    )


def test_the_topology_file_keeps_the_config_as_a_block_and_a_link_on_one_line():
    clab = containerlab_topology(SPINE_LEAF, "dc1", "ceos")

    text = topology_yaml(clab.topology)

    assert yaml.safe_load(text) == clab.topology
    assert "      startup-config: |\n        !\n        username arista" in text
    assert "  - endpoints: ['dc1-leaf1a:eth1', 'dc1-spine1:eth1']\n" in text


def test_the_extra_vars_point_each_host_at_its_address_under_the_key_given():
    made = lab_extra_vars({"dc1-leaf1a": "172.20.20.2"}, {}, ADDRESSES)

    assert made[ADDRESSES] == {"dc1-leaf1a": "172.20.20.2"}
    assert made["ansible_host"].startswith("{{ lab_addresses[inventory_hostname] | mandatory(")
    assert SERVICES not in made


def test_the_twins_aaa_is_the_lab_user_alone_with_the_designs_radius_kept():
    radius = {"LEAF1A": {"servers": [{"host": "agni.arista.com"}]}}

    made = lab_extra_vars({"LEAF1A": "leaf1a.dc1.svc"}, radius)

    assert (made["ansible_user"], made["ansible_password"]) == ("arista", "arista")
    assert made["lab_local_user"] == LAB_LOCAL_USER
    assert made["lab_radius"] == radius
    assert made["aaa_settings"] == (
        "{{ {'local_users': [lab_local_user]}"
        " | combine({'radius': lab_radius[inventory_hostname]}"
        " if inventory_hostname in lab_radius else {}) }}"
    )
