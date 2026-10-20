"""repo', rebuilt from what `emit` writes, rendered as its source is -- `compare`'s verdict.

Every host's structured config, whole, on both sides: what the objects leave out or
carry wrongly reaches the render, so it is caught here without being named.
"""

from __future__ import annotations

from pathlib import Path

from netadopt.ansible import Ansible
from netadopt.ansiblecfg import read_ansible_cfg
from netadopt.compare import compare, render_rebuilt
from netadopt.render import Rendered


def test_the_rebuilt_repository_renders_as_the_repository_does(
    ansible: Ansible,
    repo: Path,
    emitted: list[dict],
    rendered: Rendered,
    collections: Path,
    tmp_path: Path,
):
    fabric = next(doc for doc in emitted if doc.get("kind") == "Fabric")
    named = read_ansible_cfg(repo).vault_password_file
    rebuilt = render_rebuilt(
        ansible,
        emitted,
        fabric["metadata"]["name"],
        collections,
        tmp_path / "rebuilt",
        repo / named if named else None,
    )

    assert rebuilt.usable, rebuilt.problem
    result = compare(rendered.hosts, rebuilt.hosts)
    assert result.identical, [host for host in result.hosts if not host.identical]
