"""repo', rebuilt from what `emit` writes, as Ansible lists it.

`ansible-inventory --list` on both sides: the groups, and every host's vars. It runs in
the repository's root and takes that as the playbook's directory, so the vars beside a
playbook at the root are in that answer too.
"""

from __future__ import annotations

import pytest

from netadopt.ansible import Ansible
from netadopt.inventory import Inventory, read_inventory
from netadopt.reconstruct import Reconstructed


def test_the_rebuilt_repository_lists_as_the_source_does(
    ansible: Ansible,
    listed: Inventory,
    reconstructed: Reconstructed,
    rebuilt_env: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
):
    # only for repo': the source was listed as it stands
    for name, value in rebuilt_env.items():
        monkeypatch.setenv(name, value)
    again = read_inventory(ansible, reconstructed.root, reconstructed.inventory)
    assert again.usable, again.problem

    # Groups as well as hostvars: a host with no vars is left out of hostvars.
    assert again.groups == listed.groups
    assert again.hostvars == listed.hostvars
