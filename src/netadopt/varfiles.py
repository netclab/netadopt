"""group_vars and host_vars, as files.

This is the one thing Ansible will not tell us. `ansible-inventory --list` flattens
everything into per-host values -- measured: a group node comes back carrying only
`children` or `hosts`, and even a `vars:` block written inside the inventory file
arrives merged into every host. What is wanted here is the input to that merge, per
group, because that is what one FabricInput is.

Nothing is merged and no precedence is written down. Each file is reported with its
scope and the root it was found under; Ansible applies its own rule later, to its own
tree.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path

import yaml

from netadopt import ansible_yaml

# Directory names, used as such: one value both classifies a file and locates it.
GROUP_VARS = "group_vars"
HOST_VARS = "host_vars"

# Which root a file was found under. Ansible consults both, at different
# precedences, so they are kept apart rather than added together.
INVENTORY_ROOT = "inventory"
PLAYBOOK_ROOT = "playbook"

# What Ansible accepts as a vars file: these suffixes, and no suffix at all.
SUFFIXES = ("", ".yml", ".yaml", ".json")

# What it passes over whatever the suffix -- editor and tooling leftovers.
SKIPPED = (".orig", ".bak", ".ini", ".cfg", ".retry", ".pyc", ".pyo", "~")


@dataclass(frozen=True)
class VarFile:
    path: Path
    scope: str  # the group or host name the file is named for
    vars_dir: str  # GROUP_VARS | HOST_VARS -- with `root` and `scope`, the path
    root: str  # INVENTORY_ROOT | PLAYBOOK_ROOT
    data: dict | None = None
    problem: str | None = None

    @property
    def usable(self) -> bool:
        return self.problem is None


@dataclass(frozen=True)
class VarFiles:
    roots: tuple[tuple[str, Path], ...] = ()  # what was looked in, in order
    files: tuple[VarFile, ...] = field(default_factory=tuple)

    def scopes(self, vars_dir: str) -> tuple[str, ...]:
        """The group or host names that have files, first seen first."""
        seen = {file.scope: None for file in self.files if file.vars_dir == vars_dir}
        return tuple(seen)

    @property
    def problems(self) -> tuple[VarFile, ...]:
        return tuple(file for file in self.files if not file.usable)


def read_vars(repo: Path, inventory: str | None = None, playbook: str | None = None) -> VarFiles:
    """Find every group_vars and host_vars file Ansible would read, and read it.

    Ansible looks beside the inventory AND beside the playbook, and both are in play
    at once -- which is why neither is guessed from the other. Both arrangements
    occur in AVD's own corpus: single-dc-l3ls keeps them beside the playbook, the
    twodc molecule scenario beside the inventory.
    """
    roots: list[tuple[str, Path]] = []
    if inventory:
        source = repo / inventory
        roots.append((INVENTORY_ROOT, source if source.is_dir() else source.parent))
    playbook_dir = (repo / playbook).parent if playbook else repo
    if playbook_dir not in [path for _, path in roots]:
        roots.append((PLAYBOOK_ROOT, playbook_dir))

    files: list[VarFile] = []
    for root, directory in roots:
        for vars_dir in (GROUP_VARS, HOST_VARS):
            files.extend(_read_directory(directory / vars_dir, vars_dir, root))
    return VarFiles(roots=tuple(roots), files=tuple(files))


def _read_directory(directory: Path, vars_dir: str, root: str) -> list[VarFile]:
    if not directory.is_dir():
        return []

    files: list[VarFile] = []
    for entry in sorted(directory.iterdir()):
        # A scope is one file or a directory of them. Ansible merges such a directory
        # into a single namespace; here the files stay separate under the same scope.
        if entry.is_dir():
            for inner in sorted(path for path in entry.rglob("*") if path.is_file()):
                if _is_vars_file(inner):
                    files.append(_read_file(inner, entry.name, vars_dir, root))
        elif _is_vars_file(entry):
            files.append(_read_file(entry, entry.stem, vars_dir, root))
    return files


def _is_vars_file(path: Path) -> bool:
    name = path.name
    if name.startswith("."):
        return False
    if any(name.endswith(suffix) for suffix in SKIPPED):
        return False
    return path.suffix in SUFFIXES


def _read_file(path: Path, scope: str, vars_dir: str, root: str) -> VarFile:
    here = VarFile(path=path, scope=scope, vars_dir=vars_dir, root=root)
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as err:
        return replace(here, problem=f"is not UTF-8: {err}")
    except OSError as err:
        return replace(here, problem=f"could not be read: {err}")

    try:
        loaded = ansible_yaml.load(text, path)
    except yaml.YAMLError as err:
        # broken YAML, or a tag nothing constructs: `thing: !secret x`
        return replace(here, problem=str(err).replace("\n", " "))

    if loaded is None:  # an empty file is legal, and sets nothing
        return replace(here, data={})
    if not isinstance(loaded, dict):
        # `ansible-vault encrypt` on the whole file gives one scalar, not vars
        return replace(here, problem=f"is {type(loaded).__name__}, not a mapping")
    return replace(here, data=loaded)
