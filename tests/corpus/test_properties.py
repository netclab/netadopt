"""What must hold for any repository, whoever wrote it.

Properties only: no counts, no ecosystem names. These repositories move on their own
release schedule, so a red test here means one was read wrongly.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from netadopt.ansible import Ansible, resolve_ansible
from netadopt.inventory import Inventory, read_inventory
from netadopt.playbook import find_playbooks, read_playbook
from netadopt.varfiles import GROUP_VARS, INVENTORY_ROOT, read_vars


@pytest.fixture(scope="session")
def ansible() -> Ansible:
    found = resolve_ansible()
    if not found.usable:
        pytest.skip(f"no Ansible to ask: {found.problem}")
    return found


@pytest.fixture
def listed(ansible: Ansible, repo: Path, inventory_source: str | None) -> Inventory:
    inventory = read_inventory(ansible, repo, inventory_source)
    if not inventory.usable:
        # no inventory of its own is a fact about the repository; problem says why
        pytest.skip(f"no inventory: {inventory.problem}")
    return inventory


def test_every_vars_file_is_either_read_or_reported(repo: Path, inventory_source: str | None):
    # Never both, never neither: no data and no problem means a file went missing
    # unnoticed.
    found = read_vars(repo, inventory_source)

    for file in found.files:
        assert (file.data is None) != (file.problem is None), file.path


def test_every_playbook_is_either_read_or_reported(repo: Path):
    for path in sorted(repo.glob("*.yml")) + sorted(repo.glob("*.yaml")):
        read = read_playbook(repo, path.name)

        assert read.usable or read.problem, path
        # Plays are addressed by position, so the positions have to be the file's.
        assert [play.index for play in read.plays] == list(range(len(read.plays))), path


def test_a_candidate_playbook_can_be_read(repo: Path):
    # every candidate has to survive being read for real
    for candidate in find_playbooks(repo):
        again = read_playbook(repo, candidate.path.name)

        assert again.usable, candidate.path
        assert len(again.plays) == len(candidate.plays)


def test_group_vars_reach_the_hosts_ansible_puts_in_that_group(
    repo: Path, inventory_source: str | None, listed: Inventory
):
    """The differential check: the file-to-group mapping against Ansible's merge.

    A key set for a group must appear in every member host's merged vars. A higher
    precedence can change the value -- so only presence is asserted -- but nothing
    removes the key. A file attributed to the wrong scope shows up here.
    """
    # Only what Ansible was in a position to see: ansible-inventory reads the
    # group_vars beside the inventory, and not the ones beside a playbook.
    beside = [
        file
        for file in read_vars(repo, inventory_source).files
        if file.vars_dir == GROUP_VARS and file.root == INVENTORY_ROOT and file.data
    ]
    if not beside:
        pytest.skip("no group_vars beside the inventory")

    # One file naming a group that does not exist is dead weight in the repository.
    # All of them missing means the scope was read from the wrong place -- and without
    # this line the loop below turns into a silent skip.
    known = [file for file in beside if file.scope in listed.groups]
    assert known, f"none of {len(beside)} group_vars files names a group Ansible knows"

    for file in known:
        for host in _members(listed, file.scope):
            merged = listed.hostvars.get(host, {})
            for key in file.data:
                assert key in merged, f"{file.path}: {key} never reached {host}"


def _members(listed: Inventory, group: str) -> set[str]:
    """Hosts of a group and of every group under it, as Ansible nests them."""
    hosts: set[str] = set()
    pending = [group]
    seen: set[str] = set()
    while pending:
        name = pending.pop()
        if name in seen:
            continue
        seen.add(name)
        node = listed.groups.get(name, {})
        hosts.update(node.get("hosts", []))
        pending.extend(node.get("children", []))
    return hosts
