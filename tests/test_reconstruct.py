"""repo', rebuilt from the objects `emit` writes."""

from __future__ import annotations

import textwrap
from collections import defaultdict
from pathlib import Path

import yaml
from typer.testing import CliRunner

from netadopt import ansible_yaml
from netadopt.ansible_yaml import VAULT_KEY
from netadopt.ansiblecfg import read_ansible_cfg
from netadopt.cli import app
from netadopt.inventoryfile import read_inventory_file
from netadopt.playbook import read_playbook
from netadopt.reconstruct import reconstruct
from netadopt.varfiles import read_vars
from netadopt.xr import API_VERSION, FABRIC_INPUT, FABRIC_LABEL, fabric

PLAY = {"name": "Build", "hosts": "FABRIC", "gather_facts": False, "tasks": []}
GROUPS = {"all": {"children": {"FABRIC": {"hosts": {"dc1-spine1": {"ansible_host": "10.0.0.1"}}}}}}


def fabric_doc(cfg: dict | None = None, name: str = "lab") -> dict:
    return fabric(name, PLAY, GROUPS, cfg or {})


def input_doc(
    scope: str,
    design: dict,
    beside: str = "inventory",
    host: bool = False,
    fabric_name: str = "lab",
) -> dict:
    return {
        "apiVersion": API_VERSION,
        "kind": FABRIC_INPUT,
        "metadata": {
            "name": f"{fabric_name}-{'host-' if host else ''}{scope.lower()}",
            "labels": {FABRIC_LABEL: fabric_name},
        },
        "spec": {
            "appliesTo": {"hosts": [scope]} if host else {"group": scope},
            "beside": beside,
            "design": design,
        },
    }


def read(root: Path, name: str) -> object:
    return ansible_yaml.load((root / name).read_text())


def test_the_inventory_goes_where_ansible_cfg_names_it(tmp_path: Path):
    cfg = {"defaults": {"inventory": "inventory.yml"}}
    documents = [
        fabric_doc(cfg),
        input_doc("FABRIC", {"fabric_name": "FABRIC"}),
        input_doc("dc1-spine1", {"id": 1}, host=True),
    ]

    built = reconstruct(documents, tmp_path / "repo")

    assert built.usable, built.problem
    assert built.files == (
        "ansible.cfg",
        "inventory.yml",
        "playbook.yml",
        "group_vars/FABRIC.yml",
        "host_vars/dc1-spine1.yml",
    )
    assert built.inventory == "inventory.yml"
    assert read(built.root, "inventory.yml") == GROUPS
    assert read(built.root, "playbook.yml") == [PLAY]
    assert read(built.root, "group_vars/FABRIC.yml") == {"fabric_name": "FABRIC"}
    assert read_ansible_cfg(built.root).sections == cfg


def test_with_no_inventory_in_ansible_cfg_it_gets_a_directory_of_its_own(tmp_path: Path):
    # twodc: no ansible.cfg, vars beside the inventory, and a playbook outside it
    documents = [
        fabric_doc(),
        input_doc("FABRIC", {"a": 1}),
        input_doc("DC1", {"b": 2}, beside="playbook"),
    ]

    built = reconstruct(documents, tmp_path / "repo")

    assert built.usable, built.problem
    assert built.inventory == "inventory/hosts.yml"
    assert "ansible.cfg" not in built.files
    assert "inventory/group_vars/FABRIC.yml" in built.files
    assert "group_vars/DC1.yml" in built.files


def test_an_inventory_directory_named_in_ansible_cfg_gets_its_file_inside(tmp_path: Path):
    documents = [fabric_doc({"defaults": {"inventory": "./inventory/"}}), input_doc("FABRIC", {})]

    built = reconstruct(documents, tmp_path / "repo")

    assert built.inventory == "inventory/hosts.yml"
    assert "inventory/group_vars/FABRIC.yml" in built.files


def test_only_the_inputs_the_fabric_selects_are_written(tmp_path: Path):
    documents = [
        fabric_doc(),
        input_doc("FABRIC", {"selected": True}),
        input_doc("FABRIC", {"selected": False}, fabric_name="other"),
    ]

    built = reconstruct(documents, tmp_path / "repo")

    assert built.usable, built.problem
    assert read(built.root, "inventory/group_vars/FABRIC.yml") == {"selected": True}


def test_a_vaulted_value_goes_back_as_a_vault_tag(tmp_path: Path):
    secret = {VAULT_KEY: "$ANSIBLE_VAULT;1.1;AES256\n616263\n"}
    documents = [fabric_doc(), input_doc("FABRIC", {"ansible_password": secret})]

    built = reconstruct(documents, tmp_path / "repo")

    written = (built.root / "inventory/group_vars/FABRIC.yml").read_text()
    assert "ansible_password: !vault |" in written
    assert ansible_yaml.load(written) == {"ansible_password": secret}


def test_default_and_percent_in_ansible_cfg_survive(tmp_path: Path):
    cfg = {
        "DEFAULT": {"forks": "5"},
        "defaults": {"inventory": "inventory.yml", "local_tmp": "%(here)s/tmp"},
    }

    built = reconstruct([fabric_doc(cfg)], tmp_path / "repo")

    assert read_ansible_cfg(built.root).sections == cfg


def test_nothing_is_written_when_something_cannot_be(tmp_path: Path):
    documents = [fabric_doc(), input_doc("FABRIC", {"a": 1}), input_doc("FABRIC", {"b": 2})]

    built = reconstruct(documents, tmp_path / "repo")

    assert not built.usable
    assert "FABRIC.yml" in built.problem
    assert not (tmp_path / "repo").exists()


def test_a_directory_that_is_not_empty_is_refused(tmp_path: Path):
    (tmp_path / "kept.txt").write_text("kept\n")

    built = reconstruct([fabric_doc()], tmp_path)

    assert not built.usable
    assert (tmp_path / "kept.txt").read_text() == "kept\n"


def test_an_inventory_outside_the_repository_is_refused(tmp_path: Path):
    built = reconstruct([fabric_doc({"defaults": {"inventory": "/etc/ansible/hosts"}})], tmp_path / "r")

    assert not built.usable
    assert "/etc/ansible/hosts" in built.problem


def test_vars_beside_the_playbook_cannot_share_the_inventorys_directory(tmp_path: Path):
    documents = [
        fabric_doc({"defaults": {"inventory": "inventory.yml"}}),
        input_doc("FABRIC", {"a": 1}, beside="playbook"),
    ]

    built = reconstruct(documents, tmp_path / "repo")

    assert not built.usable
    assert "beside the playbook" in built.problem


def test_of_several_fabrics_one_is_named(tmp_path: Path):
    documents = [fabric_doc(name="a"), fabric_doc(name="b")]

    assert "found 2" in reconstruct(documents, tmp_path / "one").problem
    assert reconstruct(documents, tmp_path / "two", fabric="b").usable


REPO = {
    "ansible.cfg": "[defaults]\ninventory = inventory.yml\n",
    "inventory.yml": """
        all:
          children:
            FABRIC:
              children:
                DC1_SPINES:
                  hosts:
                    dc1-spine1: {ansible_host: 172.16.1.11}
              vars:
                ansible_connection: httpapi
        """,
    "build.yml": """
        - name: Build
          hosts: FABRIC
          gather_facts: false
          tasks:
            - ansible.builtin.import_role:
                name: arista.avd.eos_designs
        """,
    "group_vars/FABRIC/connection.yml": """
        ansible_user: arista
        ansible_password: !vault |
          $ANSIBLE_VAULT;1.1;AES256
          6162636465
        """,
    "group_vars/FABRIC/main.yml": "fabric_name: FABRIC\nqos_profile: null\n",
    "group_vars/DC1_SPINES.yml": "type: spine\n",
    "host_vars/dc1-spine1.yml": "motd: !unsafe '{{ literal }}'\n",
}


def test_emit_then_reconstruct_reads_back_the_same_through_every_reader(tmp_path: Path):
    source = tmp_path / "single-dc-l3ls"
    for name, body in REPO.items():
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(body).lstrip())

    result = CliRunner().invoke(app, ["avd", "emit", str(source), "--playbook", "build.yml"])
    assert result.exit_code == 0, result.stderr
    built = reconstruct(yaml.safe_load_all(result.stdout), tmp_path / "rebuilt")
    assert built.usable, built.problem

    assert read_ansible_cfg(built.root).sections == read_ansible_cfg(source).sections
    assert (
        read_inventory_file(built.root, built.inventory).groups
        == read_inventory_file(source, "inventory.yml").groups
    )
    assert (
        read_playbook(built.root, built.playbook).plays[0].raw
        == read_playbook(source, "build.yml").plays[0].raw
    )
    # A group_vars directory becomes one file; what Ansible merges from it is the same.
    assert _scopes(built.root, built.inventory, built.playbook) == _scopes(
        source, "inventory.yml", "build.yml"
    )


def _scopes(repo: Path, inventory: str, playbook: str) -> dict:
    merged: dict = defaultdict(dict)
    for file in read_vars(repo, inventory, playbook).files:
        assert file.usable, file.problem
        merged[(file.root, file.vars_dir, file.scope)].update(file.data)
    return dict(merged)
