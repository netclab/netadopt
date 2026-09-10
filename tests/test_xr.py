"""FabricInput objects, from vars files that were already read."""

from __future__ import annotations

from pathlib import Path

import yaml

from netadopt.varfiles import (
    GROUP_VARS,
    HOST_VARS,
    INVENTORY_ROOT,
    PLAYBOOK_ROOT,
    VarFile,
    VarFiles,
)
from netadopt.xr import (
    API_VERSION,
    FABRIC,
    FABRIC_INPUT,
    fabric,
    fabric_inputs,
    rfc1123,
    to_yaml,
)


def one(
    name: str,
    scope: str,
    data: dict | None = None,
    vars_dir: str = GROUP_VARS,
    root: str = INVENTORY_ROOT,
    problem: str | None = None,
) -> VarFile:
    return VarFile(
        path=Path(f"/repo/{vars_dir}/{name}"),
        scope=scope,
        vars_dir=vars_dir,
        root=root,
        data=data,
        problem=problem,
    )


def test_a_group_vars_directory_becomes_one_object():
    # AVD's single-dc-l3ls writes group_vars/FABRIC/ as two files. Ansible merges them
    # into one namespace for the group, and one object carries both.
    found = VarFiles(
        files=(
            one("FABRIC/connectivity.yml", "FABRIC", {"ansible_connection": "httpapi"}),
            one("FABRIC/main.yml", "FABRIC", {"fabric_name": "FABRIC"}),
        )
    )

    emitted = fabric_inputs(found)

    assert len(emitted.documents) == 1
    assert emitted.documents[0]["spec"]["design"] == {
        "ansible_connection": "httpapi",
        "fabric_name": "FABRIC",
    }


def test_transport_keys_travel_like_any_other_key():
    # Dropping them would put them on the fidelity test's exclusion list, and that
    # list is a place to hide things.
    found = VarFiles(files=(one("FABRIC.yml", "FABRIC", {"ansible_user": "arista"}),))

    design = fabric_inputs(found).documents[0]["spec"]["design"]

    assert design == {"ansible_user": "arista"}


def test_a_key_written_twice_in_one_scope_is_noted():
    found = VarFiles(
        files=(
            one("FABRIC/a.yml", "FABRIC", {"mtu": 1500}),
            one("FABRIC/b.yml", "FABRIC", {"mtu": 9000}),
        )
    )

    emitted = fabric_inputs(found)

    assert emitted.documents[0]["spec"]["design"] == {"mtu": 9000}  # file order decides
    assert any("mtu" in note for note in emitted.notes)


def test_a_host_vars_file_applies_to_the_host_and_says_so_in_its_name():
    found = VarFiles(files=(one("dc1-spine1.yml", "dc1-spine1", {"a": 1}, vars_dir=HOST_VARS),))

    document = fabric_inputs(found).documents[0]

    assert document["metadata"]["name"] == "host-dc1-spine1"
    assert document["spec"]["appliesTo"] == {"hosts": ["dc1-spine1"]}


def test_the_name_is_rfc_1123_while_the_spec_keeps_his_spelling():
    # AVD's twodc scenario really has DC1.POD1.LEAF2A, and two of the eight bundled
    # examples write hostnames entirely in capitals.
    found = VarFiles(files=(one("DC1_SPINES.yml", "DC1_SPINES", {"type": "spine"}),))

    document = fabric_inputs(found).documents[0]

    assert document["metadata"]["name"] == "dc1-spines"
    assert document["spec"]["appliesTo"]["group"] == "DC1_SPINES"


def test_rfc_1123_survives_dots_capitals_and_edges():
    assert rfc1123("DC1.POD1.LEAF2A") == "dc1-pod1-leaf2a"
    assert rfc1123("_FABRIC_") == "fabric"
    assert rfc1123("A" * 300) == "a" * 253


def test_a_file_that_did_not_read_is_not_emitted_and_is_named():
    found = VarFiles(
        files=(
            one("SECRET.yml", "SECRET", problem="is str, not a mapping"),
            one("FABRIC.yml", "FABRIC", {"fabric_name": "FABRIC"}),
        )
    )

    emitted = fabric_inputs(found)

    assert [doc["metadata"]["name"] for doc in emitted.documents] == ["fabric"]
    assert any("SECRET.yml" in note for note in emitted.notes)


def test_every_object_says_which_root_its_files_sat_in():
    # Not a precedence: it is where the file was, and Ansible ranks the two roots
    # itself once the repository is reconstructed. Written even with one root.
    found = VarFiles(
        files=(
            one("FABRIC.yml", "FABRIC", {"a": 1}),
            one("DC1.yml", "DC1", {"b": 2}, root=PLAYBOOK_ROOT),
        )
    )

    emitted = fabric_inputs(found)

    assert [doc["spec"]["beside"] for doc in emitted.documents] == ["inventory", "playbook"]


def test_one_scope_under_both_roots_stays_two_objects():
    # Beside the inventory and beside the playbook are different precedence levels,
    # so merging them here would write down a precedence that is Ansible's.
    found = VarFiles(
        files=(
            one("FABRIC.yml", "FABRIC", {"a": 1}),
            one("FABRIC.yml", "FABRIC", {"a": 2}, root=PLAYBOOK_ROOT),
        )
    )

    emitted = fabric_inputs(found)

    assert [doc["metadata"]["name"] for doc in emitted.documents] == [
        "fabric",
        "fabric-playbook",
    ]
    assert [doc["spec"]["design"] for doc in emitted.documents] == [{"a": 1}, {"a": 2}]


def test_no_two_objects_share_a_name_even_when_the_suffix_is_taken():
    # A duplicate metadata.name is one object overwriting another in the cluster, so
    # the suffixed spelling is checked as well.
    found = VarFiles(
        files=(
            one("FABRIC.yml", "FABRIC", {"a": 1}),
            one("FABRIC-playbook.yml", "FABRIC-playbook", {"b": 2}, root=PLAYBOOK_ROOT),
            one("FABRIC.yml", "FABRIC", {"c": 3}, root=PLAYBOOK_ROOT),
        )
    )

    names = [doc["metadata"]["name"] for doc in fabric_inputs(found).documents]

    assert names == ["fabric", "fabric-playbook", "fabric-playbook-2"]
    assert len(set(names)) == len(names)


def test_a_group_and_a_host_of_one_name_are_two_objects():
    found = VarFiles(
        files=(
            one("DC1.yml", "DC1", {"a": 1}),
            one("DC1.yml", "DC1", {"b": 2}, vars_dir=HOST_VARS),
        )
    )

    names = [doc["metadata"]["name"] for doc in fabric_inputs(found).documents]

    assert names == ["dc1", "host-dc1"]  # the prefix keeps them apart on its own


def test_the_stream_is_one_document_per_object_in_order():
    found = VarFiles(
        files=(
            one("FABRIC.yml", "FABRIC", {"fabric_name": "FABRIC"}),
            one("DC1.yml", "DC1", {"mgmt_gateway": "172.16.1.1"}),
        )
    )
    emitted = fabric_inputs(found)

    stream = to_yaml(emitted.documents)

    assert list(yaml.safe_load_all(stream)) == list(emitted.documents)
    assert stream.startswith("---\n")
    assert all(doc["apiVersion"] == API_VERSION for doc in emitted.documents)
    assert all(doc["kind"] == FABRIC_INPUT for doc in emitted.documents)


def test_a_fabric_carries_its_parts_verbatim_under_an_rfc_1123_name():
    play = {"name": "Converge", "hosts": "TWODC_5STAGE_CLOS", "gather_facts": False}
    groups = {"all": {"children": {"TWODC_5STAGE_CLOS": {"hosts": {"DC1.POD1.LEAF2A": None}}}}}
    config = {"defaults": {"inventory": "inventory/"}}

    document = fabric("eos_designs-twodc-5stage-clos", play, groups, config)

    assert document == {
        "apiVersion": API_VERSION,
        "kind": FABRIC,
        "metadata": {"name": "eos-designs-twodc-5stage-clos"},
        "spec": {"play": play, "ansibleCfg": config, "groups": groups},
    }


def test_nothing_read_is_an_empty_stream_and_not_a_failure():
    emitted = fabric_inputs(VarFiles())

    assert emitted.documents == ()
    assert to_yaml(emitted.documents) == ""
