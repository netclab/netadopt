"""Reading a playbook, as written.

We read the file rather than asking Ansible. A copied repository usually has no
collections installed, and `ansible-playbook --list-tasks` fails there on the first
import_role -- measured, on AVD's own example. And where it does work it prints the
tasks inside the role, not the two tasks he wrote; what a Fabric carries is his play
verbatim.

Nothing here decides what a play means. A play that pulls in a role reports the role
and where the reference sits; naming the role that matters is the caller's job, so a
renamed role changes the report instead of emptying it.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from netadopt import ansible_yaml

# Where a role reference sits. Three different things with three different
# precedences: the roles: keyword runs before tasks and its parameters outrank host
# vars, import_role is static, include_role is resolved as the play runs. Flattening
# them into one "roles" column would be a lie, so they keep their names.
ROLES_KEYWORD = "roles-keyword"
IMPORT_ROLE = "import_role"
INCLUDE_ROLE = "include_role"

# Play keys whose value is a list of tasks. handlers are read too: a role reference
# can sit in one, and reporting a play as roleless because we did not look is worse
# than reporting a handler.
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
        # Ansible's dialect, not plain YAML -- a play's vars: can carry !vault like
        # any other vars. See ansible_yaml: an unknown tag still fails loudly.
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

    A repository holds several playbooks -- build, deploy, validate -- and only he
    knows which one builds the fabric. This does not guess: it narrows the question
    down to the files that could be an answer.
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
