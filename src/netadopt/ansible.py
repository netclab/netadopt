"""Finding the Ansible to run.

Only the ansible-playbook installed beside this interpreter -- by the `avd` extra, or
in the same environment as netadopt. Never PATH, so which Ansible answered is never a
question. The answer is always a value -- never an exception, never sys.exit.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

# Probed rather than `ansible`: it is the one being run, and on a broken install the
# two do not always agree.
PLAYBOOK_EXE = "ansible-playbook"

_CORE = re.compile(r"\[core ([^\]]+)\]")  # 2.10+  "ansible-playbook [core 2.16.3]"
_OLD = re.compile(r"^\S+\s+([0-9][^\s]*)")  # 2.9    "ansible-playbook 2.9.27"


@dataclass(frozen=True)
class Ansible:
    """What `ansible-playbook --version` said, or why it said nothing."""

    exe: str | None = None
    core: str | None = None
    python: str | None = None
    config_file: str | None = None
    collections: tuple[str, ...] = ()
    problem: str | None = None  # set if and only if unusable

    @property
    def usable(self) -> bool:
        return self.problem is None

    def beside(self, name: str) -> Path | None:
        """Another executable from this same install, or None if it is not there.

        Taken from this install's own directory, not from PATH.
        """
        if self.exe is None:
            return None
        found = Path(self.exe).with_name(name)
        return found if found.exists() else None


def find_ansible(exe: str) -> Ansible:
    """Read the --version of the ansible-playbook at the path `exe`."""
    if not Path(exe).is_file() or not os.access(exe, os.X_OK):
        return Ansible(problem=f"{exe} not found")

    try:
        done = subprocess.run(
            [exe, "--version"],
            check=False,
            capture_output=True,
            text=True,
            timeout=60,
            # closed, so a subprocess cannot stop for a prompt
            stdin=subprocess.DEVNULL,
        )
    except OSError as err:  # not executable, wrong architecture, bad interpreter
        return Ansible(exe=exe, problem=f"{exe} could not be run: {err}")
    except subprocess.TimeoutExpired:
        return Ansible(exe=exe, problem=f"{exe} --version did not return in 60s")

    if done.returncode != 0:
        # an install broken by its own dependencies fails here; its stderr, whole
        detail = (done.stderr or done.stdout).strip() or f"exit {done.returncode}"
        return Ansible(exe=exe, problem=f"{exe} --version failed: {detail}")

    return _parse(exe, done.stdout)


def resolve_ansible() -> Ansible:
    """The ansible-playbook installed beside this interpreter."""
    # Under uvx that directory need not be on PATH, so it is found by location and
    # not by name.
    return find_ansible(str(Path(sys.executable).parent / PLAYBOOK_EXE))


def _parse(exe: str, out: str) -> Ansible:
    lines = out.splitlines()
    head = lines[0] if lines else ""
    match = _CORE.search(head) or _OLD.match(head)
    core = match.group(1) if match else None

    # The rest is "  key = value", and unknown keys are ignored rather than
    # rejected: the set has changed across releases and will change again.
    fields: dict[str, str] = {}
    for line in lines[1:]:
        key, sep, value = line.partition("=")
        if sep:
            fields[key.strip()] = value.strip()

    python = fields.get("python version", "").split(" ", 1)[0] or None
    config = fields.get("config file") or None
    if config == "None":  # Ansible's own word for "no ansible.cfg was found"
        config = None
    # "ansible collection location", with the prefix -- unlike "config file".
    raw = fields.get("ansible collection location", "")
    collections = tuple(p for p in raw.split(":") if p)

    if core is None:
        return Ansible(
            exe=exe,
            python=python,
            config_file=config,
            collections=collections,
            problem=f"could not read a version from: {head!r}",
        )
    return Ansible(
        exe=exe,
        core=core,
        python=python,
        config_file=config,
        collections=collections,
    )
