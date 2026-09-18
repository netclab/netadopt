"""Code the repository holds that Ansible runs when asked about it.

`report` runs `ansible-inventory --list` in the repository, so Ansible reads the
repository's own ansible.cfg and inventory: an executable inventory is run, and a
plugin directory ansible.cfg names is loaded -- both measured on ansible-core 2.21.3.

Neither is carried. An executable inventory has no tree to copy, and a code directory
stays behind while ansible.cfg travels: repo' names a directory that is not there, and
Ansible passes over it without a word, resolving fewer vars than the source -- measured
with a vars plugin that adds one.

Only what the files show is found. A plugin can also arrive in a collection.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path, PurePath

from netadopt.ansiblecfg import AnsibleCfg

# [defaults] keys naming directories Ansible loads Python code from.
CODE_PATH_KEYS = (
    "action_plugins",
    "become_plugins",
    "cache_plugins",
    "callback_plugins",
    "cliconf_plugins",
    "collections_path",
    "collections_paths",
    "connection_plugins",
    "doc_fragment_plugins",
    "filter_plugins",
    "httpapi_plugins",
    "inventory_plugins",
    "library",
    "lookup_plugins",
    "module_utils",
    "netconf_plugins",
    "strategy_plugins",
    "terminal_plugins",
    "test_plugins",
    "vars_plugins",
)


@dataclass(frozen=True)
class Code:
    path: str  # relative to the repository, as the inventory or ansible.cfg names it
    setting: str | None = None  # the ansible.cfg key naming a directory; None for an inventory


def find_code(repo: Path, config: AnsibleCfg, sources: tuple[str, ...]) -> tuple[Code, ...]:
    """Executable inventories among `sources`, then the code directories ansible.cfg names."""
    return executable_inventories(repo, sources) + code_directories(repo, config)


def executable_inventories(repo: Path, sources: tuple[str, ...]) -> tuple[Code, ...]:
    return tuple(
        Code(_relative(path, repo)) for path in _inventory_files(repo, sources) if is_script(path)
    )


def code_directories(repo: Path, config: AnsibleCfg) -> tuple[Code, ...]:
    """Each path in the repository that an ansible.cfg code setting names."""
    defaults = config.sections.get("defaults", {})
    found: list[Code] = []
    for key in CODE_PATH_KEYS:
        for value in defaults.get(key, "").split(os.pathsep):
            value = value.strip()
            if value and _in_repository(repo, value):
                found.append(Code(value, key))
    return tuple(found)


def is_script(path: Path) -> bool:
    """Executable and starting with `#!`. A YAML file with the executable bit set is
    tried as a script too, fails to start, and runs nothing."""
    if not os.access(path, os.X_OK):
        return False
    try:
        with path.open("rb") as file:
            return file.read(2) == b"#!"
    except OSError:
        return False


def _inventory_files(repo: Path, sources: tuple[str, ...]) -> Iterator[Path]:
    """Each source that is a file, and every file of a source that is a directory."""
    for source in sources:
        where = repo / source
        if where.is_file():
            yield where
        elif where.is_dir():
            yield from sorted(path for path in where.iterdir() if path.is_file())


def _in_repository(repo: Path, value: str) -> bool:
    """Whether a path ansible.cfg names lies in the repository, where a relative one
    starts: ansible.cfg sits at the repository's root."""
    if value.startswith("~"):
        return False
    if not PurePath(value).is_absolute():
        return True
    return Path(value).resolve().is_relative_to(repo.resolve())


def _relative(path: Path, repo: Path) -> str:
    try:
        return str(path.relative_to(repo))
    except ValueError:
        return str(path)
