"""The inventory tree as the repository writes it."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from netadopt.inventoryfile import read_inventory_file

TREE = """
all:
  children:
    FABRIC:
      children:
        DC1_SPINES:
          hosts:
            dc1-spine1: {ansible_host: 172.16.1.11, type: spine}
      vars:
        ansible_connection: httpapi
"""


@pytest.fixture
def repo(tmp_path: Path):
    def write(name: str, body: str = "") -> Path:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(body).lstrip())
        return path

    write.dir = tmp_path  # type: ignore[attr-defined]
    return write


def test_the_tree_arrives_as_written(repo):
    # The two things ansible-inventory cannot hand back: a group `vars:` block and a
    # host written inline. They are the inventory's own levels, 3 and 8.
    repo("inventory.yml", TREE)

    found = read_inventory_file(repo.dir, "inventory.yml")

    assert found.usable
    fabric = found.groups["all"]["children"]["FABRIC"]
    assert fabric["vars"] == {"ansible_connection": "httpapi"}
    assert fabric["children"]["DC1_SPINES"]["hosts"]["dc1-spine1"] == {
        "ansible_host": "172.16.1.11",
        "type": "spine",
    }


def test_a_top_level_that_is_not_all_is_read_too(repo):
    # AVD's campus-fabric starts at DC1: and l2ls-fabric at FABRIC:. Requiring `all`
    # would drop both.
    repo("inventory.yml", "DC1:\n  children:\n    DC1_SPINES:\n      hosts:\n        s1:\n")

    found = read_inventory_file(repo.dir, "inventory.yml")

    assert found.usable
    assert list(found.groups) == ["DC1"]


def test_a_directory_is_read_by_its_one_source(repo):
    # group_vars/ and host_vars/ live in there as well, and are not sources.
    repo("inventory/hosts.yml", TREE)
    repo("inventory/group_vars/FABRIC.yml", "fabric_name: FABRIC\n")
    repo("inventory/host_vars/dc1-spine1.yml", "a: 1\n")

    found = read_inventory_file(repo.dir, "inventory/")

    assert found.usable
    assert found.path.name == "hosts.yml"
    assert "fabric_name" not in str(found.groups)  # the vars files are FabricInputs


def test_several_sources_are_reported_and_not_merged(repo):
    # One `groups` field cannot hold two trees, and merging them would be a rule of
    # ours rather than Ansible's.
    repo("inventory/one.yml", TREE)
    repo("inventory/two.yml", "OTHER:\n  hosts:\n    h1:\n")

    found = read_inventory_file(repo.dir, "inventory/")

    assert not found.usable
    assert "one.yml" in found.problem and "two.yml" in found.problem


def test_an_ini_inventory_is_a_problem_naming_the_file(repo):
    # No file to copy: the route for these is --export with the vars files subtracted.
    repo("hosts", "[FABRIC]\ndc1-spine1 ansible_host=172.16.1.11\n")

    found = read_inventory_file(repo.dir, "hosts")

    assert not found.usable
    assert "hosts" in found.problem


def test_an_inventory_nobody_named_says_so(repo):
    found = read_inventory_file(repo.dir, None)

    assert not found.usable
    assert "--inventory" in found.problem


def test_a_missing_inventory_names_the_path(repo):
    found = read_inventory_file(repo.dir, "nope.yml")

    assert not found.usable
    assert "nope.yml" in found.problem
