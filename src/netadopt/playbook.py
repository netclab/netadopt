"""Reading a playbook, as written.

The file, not `ansible-playbook --list-tasks`: with no collections installed it fails
on the first import_role -- measured on AVD's example -- and where it works it prints
the tasks inside the role, not the ones the play holds.

A play that pulls in a role reports the role and where the reference sits. Nothing
here decides what a role means.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from netadopt import ansible_yaml

# Where a role reference sits. Three things, three precedences, so three names.
ROLES_KEYWORD = "roles-keyword"  # runs before tasks; its params outrank host vars
IMPORT_ROLE = "import_role"      # static, resolved when the playbook is parsed
INCLUDE_ROLE = "include_role"    # resolved as the play runs

# Play keys whose value is a list of tasks. handlers are read too -- a role reference
# can sit in one.
TASK_KEYS = ("pre_tasks", "tasks", "post_tasks", "handlers")

# Task keys holding nested tasks.
BLOCK_KEYS = ("block", "rescue", "always")


@dataclass(frozen=True)
class RoleRef:
    """A role a play pulls in, and how."""

    name: str
    how: str  # ROLES_KEYWORD | IMPORT_ROLE | INCLUDE_ROLE


@dataclass(frozen=True)
class Play:
    index: int
    name: str | None
    hosts: object  # verbatim: a string, a list, or a pattern like FABRIC:!DC2
    roles: tuple[RoleRef, ...]
    task_count: int
    var_names: tuple[str, ...]
    raw: dict  # the play as written -- what Fabric.spec.play carries


@dataclass(frozen=True)
class Playbook:
    path: Path | None = None
    plays: tuple[Play, ...] = ()
    problem: str | None = None

    @property
    def usable(self) -> bool:
        return self.problem is None


def read_playbook(repo: Path, name: str) -> Playbook:
    """Load `repo/name` and list its plays, in file order."""
    path = repo / name
    if not path.is_file():
        return Playbook(path=path, problem=f"no such playbook: {path}")

    try:
        text = path.read_text(encoding="utf-8")
    except OSError as err:
        return Playbook(path=path, problem=f"{path} could not be read: {err}")

    try:
        # Ansible's dialect: a play's vars: can carry !vault like any other vars.
        document = ansible_yaml.load(text, path)
    except yaml.YAMLError as err:
        return Playbook(path=path, problem=f"{path} is not readable as YAML: {err}")

    if document is None:
        return Playbook(path=path, problem=f"{path} is empty")
    if not isinstance(document, list):
        return Playbook(
            path=path,
            problem=f"{path} is not a playbook: its top level is "
            f"{type(document).__name__}, not a list of plays",
        )

    plays = []
    for index, entry in enumerate(document):
        if not isinstance(entry, dict):
            return Playbook(
                path=path,
                problem=f"{path}: play [{index}] is {type(entry).__name__}, not a mapping",
            )
        plays.append(_play(index, entry))
    return Playbook(path=path, plays=tuple(plays))


def find_playbooks(repo: Path) -> tuple[Playbook, ...]:
    """Every top-level file that reads as a list of plays, by name.

    A repository holds several -- build, deploy, validate. Which one builds the fabric
    is not guessed here; these are the files that could be an answer.
    """
    candidates = sorted(
        path for pattern in ("*.yml", "*.yaml") for path in repo.glob(pattern)
    )
    found = [read_playbook(repo, path.name) for path in candidates]
    return tuple(pb for pb in found if pb.usable)


def _play(index: int, entry: dict) -> Play:
    roles: list[RoleRef] = []

    # The roles: keyword. An entry is a name, or a mapping carrying role:/name: plus
    # parameters -- and those parameters outrank host vars, which is exactly why this
    # is not interchangeable with an import_role task.
    for item in _as_list(entry.get("roles")):
        if isinstance(item, str):
            roles.append(RoleRef(item, ROLES_KEYWORD))
        elif isinstance(item, dict):
            named = item.get("role") or item.get("name")
            if named:
                roles.append(RoleRef(str(named), ROLES_KEYWORD))

    tasks = [task for key in TASK_KEYS for task in _as_list(entry.get(key))]
    task_count = 0
    for task in _flatten(tasks):
        task_count += 1
        for how in (IMPORT_ROLE, INCLUDE_ROLE):
            # Both spellings: the short name and the fully qualified one.
            for key in (how, f"ansible.builtin.{how}"):
                value = task.get(key)
                if isinstance(value, dict) and value.get("name"):
                    roles.append(RoleRef(str(value["name"]), how))
                elif isinstance(value, str):  # free-form: `import_role: name=x`
                    roles.append(RoleRef(value, how))

    play_vars = entry.get("vars")
    var_names = tuple(play_vars) if isinstance(play_vars, dict) else ()

    return Play(
        index=index,
        name=entry.get("name"),
        hosts=entry.get("hosts"),
        roles=tuple(roles),
        task_count=task_count,
        var_names=var_names,
        raw=entry,
    )


def _flatten(tasks: list) -> list[dict]:
    """Tasks, with block/rescue/always opened out, in file order."""
    out: list[dict] = []
    for task in tasks:
        if not isinstance(task, dict):
            continue
        nested = [inner for key in BLOCK_KEYS for inner in _as_list(task.get(key))]
        if nested:
            out.extend(_flatten(nested))
        else:
            out.append(task)
    return out


def _as_list(value: object) -> list:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]
