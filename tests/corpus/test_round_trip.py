"""repo', rebuilt from what `emit` writes, as Ansible lists it.

`ansible-inventory --list` on both sides: the groups, and every host's vars. The vars
beside a playbook are not in that answer, so they are left to the fidelity test.
"""

from __future__ import annotations

from netadopt.ansible import Ansible
from netadopt.inventory import Inventory, read_inventory
from netadopt.reconstruct import Reconstructed


def test_the_rebuilt_repository_lists_as_the_source_does(
    ansible: Ansible, listed: Inventory, reconstructed: Reconstructed
):
    again = read_inventory(ansible, reconstructed.root, reconstructed.inventory)
    assert again.usable, again.problem

    # Groups as well as hostvars: a host with no vars is left out of hostvars.
    assert again.groups == listed.groups
    assert again.hostvars == listed.hostvars
