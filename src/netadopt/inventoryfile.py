"""The inventory as written, for `Fabric.spec.groups`.

`ansible-inventory` cannot answer this one. `--list` flattens the tree into hostvars,
and `--list --export` folds every vars file a level down: `group_vars/FABRIC.yml` into
the group's own `vars:` (6 to 3), `host_vars/h1.yml` into the host's inline vars
(9 to 8), `group_vars/all.yml` into `all` (4 to 3). Neither separates what the
inventory sets itself -- a group `vars:` block, a host written inline -- from what a
vars file sets, and only the first belongs in this object.

Read, never merged: several sources are reported as several, because one `groups`
field cannot hold two trees without a merge rule that would be ours and not Ansible's.

Only YAML. An INI inventory and an inventory plugin have no file to copy, and the
route for them is `--export` with the vars files subtracted back out.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from netadopt import ansible_yaml
from netadopt.varfiles import SKIPPED, SUFFIXES


@dataclass(frozen=True)
class InventoryFile:
    path: Path | None = None
    groups: dict = field(default_factory=dict)  # the tree as written, `all:` included
    problem: str | None = None

    @property
    def usable(self) -> bool:
        return self.problem is None


def read_inventory_file(repo: Path, source: str | None) -> InventoryFile:
    """The inventory tree as the repository writes it.

    `source` is the -i argument. A directory is Ansible's other spelling of an
    inventory and the files in it are the sources; `group_vars/` and `host_vars/` sit
    there too and are directories, so they are passed over.
    """
    if source is None:
        return InventoryFile(problem="no inventory named -- pass --inventory")

    where = repo / source
    if where.is_file():
        return _read(where)
    if not where.is_dir():
        return InventoryFile(path=where, problem=f"no such inventory: {where}")

    sources = [path for path in sorted(where.iterdir()) if _is_source(path)]
    if not sources:
        return InventoryFile(path=where, problem=f"no inventory file in {where}")
    if len(sources) > 1:
        named = ", ".join(path.name for path in sources)
        return InventoryFile(path=where, problem=f"{where} holds several sources: {named}")
    return _read(sources[0])


def _is_source(path: Path) -> bool:
    if not path.is_file() or path.name.startswith("."):
        return False
    if any(path.name.endswith(suffix) for suffix in SKIPPED):
        return False
    return path.suffix in SUFFIXES


def _read(path: Path) -> InventoryFile:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as err:
        return InventoryFile(path=path, problem=f"{path} could not be read: {err}")

    try:
        # Ansible's dialect: an inventory can carry !vault, and does in repositories
        # that write a password beside the host.
        loaded = ansible_yaml.load(text, path)
    except yaml.YAMLError as err:
        # An INI inventory lands here, and so does a plugin configuration.
        return InventoryFile(path=path, problem=f"{path} is not readable as YAML: {err}")

    if not isinstance(loaded, dict):
        what = "empty" if loaded is None else f"{type(loaded).__name__}, not a mapping"
        return InventoryFile(path=path, problem=f"{path} is {what}")
    return InventoryFile(path=path, groups=loaded)
