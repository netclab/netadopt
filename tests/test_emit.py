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
