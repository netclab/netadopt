"""repo', rebuilt from what `emit` writes, as Ansible lists it.

`ansible-inventory --list` on both sides: the groups, and every host's vars. The vars
beside a playbook are not in that answer, so they are left to the fidelity test.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from netadopt.ansible import Ansible
from netadopt.ansiblecfg import read_ansible_cfg
from netadopt.inventory import Inventory, read_inventory
from netadopt.reconstruct import reconstruct


def test_the_rebuilt_repository_lists_as_the_source_does(
    ansible: Ansible, repo: Path, listed: Inventory, emitted: list[dict], tmp_path: Path
):
    named = read_ansible_cfg(repo).sections.get("defaults", {}).get("vault_password_file")
    if named:
        pytest.skip(f"ansible.cfg names {named}, and files named by path are not carried yet")

    built = reconstruct(emitted, tmp_path / "rebuilt")
    assert built.usable, built.problem
    again = read_inventory(ansible, built.root, built.inventory)
    assert again.usable, again.problem

    # Groups as well as hostvars: a host with no vars is left out of hostvars.
    assert again.groups == listed.groups
    assert again.hostvars == listed.hostvars
