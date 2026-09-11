"""repo', rebuilt from what `emit` writes, resolved by Ansible as its source is.

Every host's vars for the play, templated, on both sides. Whatever reaches them is
covered without being named here: the inventory, group and host vars at their
precedence, ansible.cfg, the play's own vars. What two directories differ in by
construction is left out, and nothing else.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from netadopt.reconstruct import Reconstructed

MISSING = object()

# `ansible_inventory_sources` is where the inventory was read from, not what it says:
# repo' has one file where the source may name a directory. `vars` holds every var once
# more, and each is compared on its own.
NOT_COMPARED = frozenset({"ansible_inventory_sources", "vars"})

# Host lists whose order Ansible leaves to hashing: it moves with PYTHONHASHSEED on one
# repository. The hosts are compared, their order is not.
UNORDERED = frozenset({"ansible_play_batch", "ansible_play_hosts", "ansible_play_hosts_all", "play_hosts"})


def test_the_rebuilt_repository_resolves_as_the_source_does(
    repo: Path,
    playbook: str,
    inventory_source: str | None,
    reconstructed: Reconstructed,
    resolve,
    tmp_path: Path,
):
    # A copy, so that the oracle's playbook is never written into the checkout.
    source = tmp_path / "source"
    shutil.copytree(repo, source, symlinks=True)
    expected = resolve(source, inventory_source, playbook)
    if not expected.hosts:
        pytest.skip(f"nothing to compare: {expected.problem or 'no host was templated'}")

    got = resolve(reconstructed.root, reconstructed.inventory, reconstructed.playbook)
    assert got.problem is None, got.problem
    # A host the source cannot template either is compared by failing the same way.
    assert got.failed == expected.failed
    assert sorted(got.hosts) == sorted(expected.hosts)

    differing: dict[str, list[str]] = {}
    for host, want in expected.hosts.items():
        a, b = _comparable(want), _comparable(got.hosts[host])
        for key in sorted(a.keys() | b.keys()):
            if a.get(key, MISSING) != b.get(key, MISSING):
                differing.setdefault(key, []).append(host)
    assert not differing, {key: f"{hosts[0]} and {len(hosts) - 1} more" for key, hosts in differing.items()}


def _comparable(hostvars: dict) -> dict:
    out = {key: value for key, value in hostvars.items() if key not in NOT_COMPARED}
    for key in UNORDERED & out.keys():
        out[key] = sorted(out[key])
    if isinstance(out.get("groups"), dict):
        out["groups"] = {name: sorted(hosts) for name, hosts in out["groups"].items()}
    return out
