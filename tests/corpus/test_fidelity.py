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

# How `resolve` writes the repository's own directory.
ROOT = "<root>"

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
    rebuilt_env: dict[str, str],
    resolve,
    pool_file,
    named_files,
    tmp_path: Path,
):
    # A copy, so that the oracle's playbook is never written into the checkout.
    source = tmp_path / "source"
    shutil.copytree(repo, source, symlinks=True)
    expected = resolve(source, inventory_source, playbook)
    if not expected.hosts:
        pytest.skip(f"nothing to compare: {expected.problem or 'no host was templated'}")

    got = resolve(reconstructed.root, reconstructed.inventory, reconstructed.playbook, rebuilt_env)
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

    # A file read beside the vars, not through them, is judged by its bytes: the pool
    # file, where each side's own resolved vars say it is.
    pairs = {(pool_file(want), pool_file(got.hosts[host])) for host, want in expected.hosts.items()}
    for mine, theirs in sorted(pair for pair in pairs if pair[0] is not None):
        assert _bytes(source, mine) == _bytes(reconstructed.root, theirs), mine

    # Every file the source's resolved vars name by path -- templates above all -- as
    # AVD would open it: the list is the source's, so a file not carried is caught.
    base = (source / playbook).parent
    for path in sorted(named_files(expected.hosts, base)):
        rebuilt = reconstructed.root / path
        assert rebuilt.is_file() and rebuilt.read_bytes() == (base / path).read_bytes(), path


def _bytes(root: Path, path: str | None) -> bytes | None:
    """The file at `path` -- `<root>/...`, or relative to where Ansible ran -- or None."""
    if path is None:
        return None
    file = root / path.removeprefix(ROOT + "/") if path.startswith(ROOT + "/") else root / path
    return file.read_bytes() if file.is_file() else None


def _comparable(hostvars: dict) -> dict:
    out = {key: value for key, value in hostvars.items() if key not in NOT_COMPARED}
    for key in UNORDERED & out.keys():
        out[key] = sorted(out[key])
    if isinstance(out.get("groups"), dict):
        out["groups"] = {name: sorted(hosts) for name, hosts in out["groups"].items()}
    return out
