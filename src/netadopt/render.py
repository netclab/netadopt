"""AVD's own render, on a copy of the repository.

The cabling a lab needs is in AVD's structured configuration, and only AVD writes it.
So the repository is copied, the play's tasks are replaced by the one role that renders
-- `arista.avd.eos_designs` -- and Ansible runs it with the collections netadopt pins,
against a `structured_dir` of netadopt's own.

Pointed there, the render neither reads nor overwrites a committed `intended/`: what
comes back is what the repository renders today. Everything else the role writes -- the
fabric documentation, its temporary files, the node-ID pool it rewrites -- lands in the
copy and goes with it.

The copy carries no `.git`, so a repository resolving from its own git location fails
here instead of reading the checkout it was copied from.

A host missing from the render is a device missing its cables, so a run that does not
finish is a problem, never a partial answer.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from netadopt import ansible_yaml
from netadopt.ansible import Ansible
from netadopt.playbook import IMPORT_PLAYBOOK_KEYS, TASK_KEYS, read_playbook

RENDER_ROLE = "arista.avd.eos_designs"
RENDER_PLAYBOOK = "netadopt-render.yml"

# Seconds: the whole fabric, and the corpus holds plays of 501 devices.
RENDER_TIMEOUT = 1800

# Ansible writes one fatal: line per host, and a fabric holds hundreds.
ERRORS_SHOWN = 10

COPY = "repo"
STRUCTURED = "structured_configs"


@dataclass(frozen=True)
class Rendered:
    """Each host's structured configuration as AVD wrote it, or why there is none."""

    hosts: dict[str, dict] = field(default_factory=dict)
    problem: str | None = None

    @property
    def usable(self) -> bool:
        return self.problem is None


def render(
    ansible: Ansible,
    repo: Path,
    playbook: str,
    collections: Path,
    work: Path,
    play: int = 0,
    inventory: str | None = None,
) -> Rendered:
    """Render play `play` of `repo` under `work`, and read what AVD wrote.

    `work` must be empty or absent: the copy and the structured configurations are
    netadopt's own, and a directory holding somebody else's files is not.
    """
    if not ansible.usable:
        return Rendered(problem=f"no Ansible to render with: {ansible.problem}")
    if work.exists() and (not work.is_dir() or any(work.iterdir())):
        return Rendered(problem=f"{work} is not an empty directory")

    read = read_playbook(repo, playbook)
    if not read.usable:
        return Rendered(problem=read.problem)
    if not 0 <= play < len(read.plays):
        held = len(read.plays)
        return Rendered(problem=f"no play [{play}] -- {read.path.name} holds {held}")
    raw = read.plays[play].raw
    if any(key in raw for key in IMPORT_PLAYBOOK_KEYS):
        return Rendered(problem=f"play [{play}] imports another playbook and has no hosts of its own")

    root = work / COPY
    out = work / STRUCTURED
    try:
        shutil.copytree(repo, root, ignore=shutil.ignore_patterns(".git"))
    except (OSError, shutil.Error) as err:
        return Rendered(problem=f"{repo} could not be copied to {root}: {err}")

    # Beside the source playbook, so that playbook_dir is the directory it names.
    book = (root / playbook).parent / RENDER_PLAYBOOK
    try:
        book.write_text(ansible_yaml.dump([_render_play(raw)]), encoding="utf-8")
    except OSError as err:
        return Rendered(problem=f"{book} could not be written: {err}")

    done = _run(ansible, root, book, collections, out, inventory)
    if isinstance(done, str):
        return Rendered(problem=done)
    if done.returncode != 0:
        return Rendered(problem=_why(done))

    return _read(out)


def _run(
    ansible: Ansible,
    root: Path,
    book: Path,
    collections: Path,
    out: Path,
    inventory: str | None,
) -> subprocess.CompletedProcess | str:
    """The finished ansible-playbook, or why there is none."""
    command = [str(ansible.exe), str(book.relative_to(root))]
    if inventory:
        command += ["-i", inventory]
    command += ["-e", _extra_vars(out)]
    try:
        return subprocess.run(
            command,
            check=False,
            cwd=root,
            # The collections netadopt pins, for this subprocess and no other.
            env={**os.environ, "ANSIBLE_COLLECTIONS_PATH": str(collections)},
            capture_output=True,
            text=True,
            timeout=RENDER_TIMEOUT,
            # closed, so a vault prompt cannot stop the render in the middle
            stdin=subprocess.DEVNULL,
        )
    except OSError as err:
        return f"{ansible.exe} could not be run: {err}"
    except subprocess.TimeoutExpired:
        return f"{RENDER_ROLE} did not finish in {RENDER_TIMEOUT}s"


def _extra_vars(out: Path) -> str:
    """The three values the render sets over the repository's own.

    An inventory's ansible_connection outranks -c, and extra vars are the one
    precedence level above inventory vars. As JSON, because a directory may hold a
    space and `key=value` may not.
    """
    return json.dumps(
        {"ansible_connection": "local", "ansible_become": False, "structured_dir": str(out)}
    )


def _render_play(raw: dict) -> dict:
    """The play as written, with every task list replaced by the role that renders.

    `roles:` goes with them: a lab is built from the cabling eos_designs computes, and
    a deploy task left in the play would reach for a device that does not exist yet.
    """
    play = {key: value for key, value in raw.items() if key not in (*TASK_KEYS, "roles")}
    play["tasks"] = [{"ansible.builtin.import_role": {"name": RENDER_ROLE}}]
    return play


def _read(out: Path) -> Rendered:
    """Every structured configuration in `out`, by the host its file is named after."""
    files = sorted(out.iterdir()) if out.is_dir() else []
    hosts: dict[str, dict] = {}
    for file in files:
        if not file.is_file():
            continue
        try:
            loaded = ansible_yaml.load(file.read_text(encoding="utf-8"), file)
        except (OSError, ValueError, yaml.YAMLError) as err:
            return Rendered(problem=f"{file.stem}: its structured configuration is unreadable -- {err}")
        if not isinstance(loaded, dict):
            what = type(loaded).__name__
            return Rendered(problem=f"{file.stem}: its structured configuration is {what}, not a mapping")
        # A hostname may hold dots, and only the format's suffix comes off.
        hosts[file.stem] = loaded
    if not hosts:
        return Rendered(problem=f"{RENDER_ROLE} wrote no structured configuration in {out}")
    return Rendered(hosts=hosts)


def _why(done: subprocess.CompletedProcess) -> str:
    """The lines Ansible failed on, or its exit code when it named none."""
    said = done.stdout + done.stderr
    errors = [line for line in said.splitlines() if "fatal:" in line or "ERROR" in line]
    if not errors:
        return f"exit {done.returncode}"
    shown = errors[:ERRORS_SHOWN]
    if len(errors) > ERRORS_SHOWN:
        shown.append(f"... and {len(errors) - ERRORS_SHOWN} more")
    return "\n".join(shown)
