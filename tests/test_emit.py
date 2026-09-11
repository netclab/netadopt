"""`netadopt avd emit`, over a repository written in the test."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from netadopt.cli import app

BUILD = """
- name: Build
  hosts: FABRIC
  gather_facts: false
  tasks:
    - name: Generate
      ansible.builtin.import_role:
        name: arista.avd.eos_designs
- name: Build the twin
  hosts: FABRIC
  vars:
    avd_digital_twin_mode: true
  tasks: []
"""

INVENTORY = """
all:
  children:
    FABRIC:
      hosts:
        dc1-spine1: {ansible_host: 172.16.1.11}
"""


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "single-dc-l3ls"
    (root / "group_vars").mkdir(parents=True)
    (root / "ansible.cfg").write_text("[defaults]\ninventory=inventory.yml\n")
    (root / "inventory.yml").write_text(textwrap.dedent(INVENTORY).lstrip())
    (root / "build.yml").write_text(textwrap.dedent(BUILD).lstrip())
    (root / "group_vars" / "FABRIC.yml").write_text("fabric_name: FABRIC\n")
    return root


def emit(*args: object):
    result = CliRunner().invoke(app, ["avd", "emit", *map(str, args)])
    return result, list(yaml.safe_load_all(result.stdout))


def test_the_fabric_comes_first_named_for_the_directory_with_the_first_play(repo):
    result, documents = emit(repo, "--playbook", "build.yml")

    assert result.exit_code == 0, result.stderr
    first_play = yaml.safe_load(textwrap.dedent(BUILD))[0]
    assert documents[0] == {
        "apiVersion": "avd.netclab.dev/v1alpha1",
        "kind": "Fabric",
        "metadata": {"name": "single-dc-l3ls"},
        "spec": {
            "inputs": {"matchLabels": {"avd.netclab.dev/fabric": "single-dc-l3ls"}},
            "play": first_play,
            "ansibleCfg": {"defaults": {"inventory": "inventory.yml"}},
            "groups": yaml.safe_load(INVENTORY),
        },
    }
    assert [doc["kind"] for doc in documents[1:]] == ["FabricInput"]
    assert documents[1]["metadata"] == {
        "name": "single-dc-l3ls-fabric",
        "labels": {"avd.netclab.dev/fabric": "single-dc-l3ls"},
    }


def test_the_inventory_ansible_cfg_names_is_the_inventory_root(repo):
    # group_vars/ sits beside inventory.yml, which only ansible.cfg names here
    _, documents = emit(repo, "--playbook", "build.yml")

    assert documents[1]["spec"]["beside"] == "inventory"


def test_a_play_not_carried_is_named_with_the_way_to_carry_it(repo):
    result, _ = emit(repo, "--playbook", "build.yml")

    assert "play [1] Build the twin: not carried" in result.stderr
    assert "--play 1" in result.stderr


def test_play_and_name_carry_another_play_under_its_own_name(repo):
    result, documents = emit(repo, "--playbook", "build.yml", "--play", 1, "--name", "DC1 twin")

    assert result.exit_code == 0, result.stderr
    assert documents[0]["metadata"]["name"] == "dc1-twin"
    assert documents[0]["spec"]["play"]["vars"] == {"avd_digital_twin_mode": True}
    assert "is spelled dc1-twin" in result.stderr
    # its own inputs, so the first run's are neither overwritten nor taken
    assert documents[1]["metadata"]["name"] == "dc1-twin-fabric"


def test_a_vault_password_file_is_named_as_a_secret_and_never_carried(repo):
    (repo / "ansible.cfg").write_text("[defaults]\ninventory=inventory.yml\nvault_password_file=.vault\n")
    (repo / ".vault").write_text("not-a-real-password\n")

    result, documents = emit(repo, "--playbook", "build.yml")

    assert result.exit_code == 0, result.stderr
    assert documents[0]["spec"]["vaultPassword"] == {
        "secretRef": {"name": "single-dc-l3ls-vault", "key": "password"}
    }
    assert "not-a-real-password" not in result.stdout
    assert (
        f"kubectl create secret generic single-dc-l3ls-vault --from-file=password={repo / '.vault'}"
        in result.stderr
    )


def test_a_vaulted_value_is_written_line_by_line(repo):
    ciphertext = "$ANSIBLE_VAULT;1.1;AES256\n" + "6162636465" * 8 + "\n" + "6162" + "\n"
    body = "\n".join("  " + line for line in ciphertext.splitlines())
    (repo / "group_vars" / "FABRIC.yml").write_text(f"fabric_name: FABRIC\nsecret: !vault |\n{body}\n")

    result, documents = emit(repo, "--playbook", "build.yml")

    assert result.exit_code == 0, result.stderr
    assert documents[1]["spec"]["design"]["secret"] == {"__ansible_vault": ciphertext}
    assert "__ansible_vault: |\n" in result.stdout
    assert "\n\n" not in result.stdout


def test_passwords_carried_as_plain_text_are_named_per_object(repo):
    (repo / "inventory.yml").write_text(
        "all:\n  children:\n    FABRIC:\n      hosts:\n"
        "        dc1-spine1: {ansible_host: 172.16.1.11, ansible_ssh_pass: arista}\n"
    )
    (repo / "group_vars" / "FABRIC.yml").write_text(
        "fabric_name: FABRIC\n"
        "ansible_password: arista\n"
        "ansible_become_password: \"{{ lookup('env', 'ENABLE') }}\"\n"
        "ansible_become_pass: !vault |\n  $ANSIBLE_VAULT;1.1;AES256\n  6162\n"
    )

    result, _ = emit(repo, "--playbook", "build.yml")

    assert result.exit_code == 0, result.stderr
    assert "carried as plain text" in result.stderr
    assert "  Fabric single-dc-l3ls: ansible_ssh_pass\n" in result.stderr
    assert "  FabricInput single-dc-l3ls-fabric: ansible_password\n" in result.stderr
    assert "ansible_become" not in result.stderr


POOL = "node_id_pools:\n  fabric_name=FABRIC/type=spine:\n    hostname=dc1-spine1: 1\n"


def write_pool(repo, name: str) -> None:
    (repo / "intended" / "data").mkdir(parents=True)
    (repo / "intended" / "data" / name).write_text(POOL)


def test_a_pool_file_is_carried_in_a_config_map_the_fabric_names(repo):
    (repo / "group_vars" / "FABRIC.yml").write_text(
        "fabric_name: FABRIC\n"
        "fabric_numbering: {node_id: {algorithm: pool_manager, pools_file: intended/data/ids.yml}}\n"
    )
    write_pool(repo, "ids.yml")

    result, documents = emit(repo, "--playbook", "build.yml")

    assert result.exit_code == 0, result.stderr
    assert documents[0]["spec"]["pools"] == [
        {
            "configMapRef": {"name": "single-dc-l3ls-pools", "key": "ids.yml"},
            "path": "intended/data/ids.yml",
            "beside": "playbook",
        }
    ]
    assert documents[1] == {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {
            "name": "single-dc-l3ls-pools",
            "labels": {"avd.netclab.dev/fabric": "single-dc-l3ls"},
        },
        "data": {"ids.yml": POOL},
    }


def test_without_pools_file_the_default_beside_the_inventory_is_carried(repo):
    (repo / "group_vars" / "FABRIC.yml").write_text(
        "fabric_name: FABRIC\nfabric_numbering: {node_id: {algorithm: pool_manager}}\n"
    )
    write_pool(repo, "FABRIC-ids.yml")

    result, documents = emit(repo, "--playbook", "build.yml")

    assert result.exit_code == 0, result.stderr
    (entry,) = documents[0]["spec"]["pools"]
    assert (entry["path"], entry["beside"]) == ("intended/data/FABRIC-ids.yml", "inventory")


def test_a_root_dir_not_followed_is_named_and_nothing_is_guessed(repo):
    (repo / "group_vars" / "FABRIC.yml").write_text(
        "fabric_name: FABRIC\nroot_dir: /srv/avd\n"
        "fabric_numbering: {node_id: {algorithm: pool_manager}}\n"
    )
    write_pool(repo, "FABRIC-ids.yml")

    result, documents = emit(repo, "--playbook", "build.yml")

    assert result.exit_code == 0, result.stderr
    assert "pools" not in documents[0]["spec"]
    assert "root_dir is /srv/avd" in result.stderr


def test_a_missing_pool_file_is_named_and_nothing_is_carried(repo):
    (repo / "group_vars" / "FABRIC.yml").write_text(
        "fabric_name: FABRIC\n"
        "fabric_numbering: {node_id: {algorithm: pool_manager, pools_file: intended/data/ids.yml}}\n"
    )

    result, documents = emit(repo, "--playbook", "build.yml")

    assert result.exit_code == 0, result.stderr
    assert [doc["kind"] for doc in documents] == ["Fabric", "FabricInput"]
    assert "pools" not in documents[0]["spec"]
    assert "no pool file at intended/data/ids.yml -- AVD assigns node IDs afresh" in result.stderr


def test_an_input_whose_name_is_too_long_is_refused_and_fails_the_run(repo):
    group = "G" * 240  # single-dc-l3ls- in front makes 255
    (repo / "group_vars" / f"{group}.yml").write_text("a: 1\n")

    result, documents = emit(repo, "--playbook", "build.yml")

    assert result.exit_code == 2
    assert group.lower() not in [doc["metadata"]["name"] for doc in documents]
    assert "253" in result.stderr


def test_a_name_too_long_for_a_label_emits_nothing(repo):
    result, documents = emit(repo, "--playbook", "build.yml", "--name", "x" * 64)

    assert result.exit_code == 2
    assert documents == []
    assert "63" in result.stderr


def test_a_play_that_is_not_there_still_emits_the_inputs_and_fails(repo):
    result, documents = emit(repo, "--playbook", "build.yml", "--play", 5)

    assert result.exit_code == 2
    assert [doc["kind"] for doc in documents] == ["FabricInput"]
    assert "no play [5]" in result.stderr


def test_no_playbook_named_still_emits_the_inputs_and_says_so(repo):
    result, documents = emit(repo)

    assert result.exit_code == 2
    assert [doc["kind"] for doc in documents] == ["FabricInput"]
    assert "--playbook" in result.stderr
