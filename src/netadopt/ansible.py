"""Finding the Ansible to run.

PATH first, the `ansible` extra behind it; `source` says which one answered.
The answer is always a value -- never an exception, never sys.exit.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, replace
from pathlib import Path

# Probed rather than `ansible`: it is the one being run, and on a broken install the
# two do not always agree.
PLAYBOOK_EXE = "ansible-playbook"

_CORE = re.compile(r"\[core ([^\]]+)\]")          # 2.10+  "ansible-playbook [core 2.16.3]"
_OLD = re.compile(r"^\S+\s+([0-9][^\s]*)")        # 2.9    "ansible-playbook 2.9.27"


@dataclass(frozen=True)
class Ansible:
    """What `ansible-playbook --version` said, or why it said nothing."""

    exe: str | None = None
    core: str | None = None
    python: str | None = None
    config_file: str | None = None
    collections: tuple[str, ...] = ()
    problem: str | None = None  # set if and only if unusable
    source: str | None = None  # "given" | "PATH" | "bundled"

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

    @property
    def bundled(self) -> bool:
        """True when the fallback answered, and not an Ansible already installed."""
        return self.source == "bundled"


def find_ansible(exe: str | None = None) -> Ansible:
    """Locate an ansible-playbook and read its --version.

    `exe` overrides PATH -- a name or a path; a venv is selected by pointing here.
    """
    target = exe or PLAYBOOK_EXE
    found = shutil.which(target)
    if found is None:
        # a typed path and a name looked up on PATH fail for different reasons
        where = "not found" if Path(target).name != target else "not found on PATH"
        return Ansible(problem=f"{target} {where}")

    try:
        done = subprocess.run(
            [found, "--version"],
            check=False,
            capture_output=True,
            text=True,
            timeout=60,
            # closed, so a subprocess cannot stop for a prompt
            stdin=subprocess.DEVNULL,
        )
    except OSError as err:  # not executable, wrong architecture, bad interpreter
        return Ansible(exe=found, problem=f"{found} could not be run: {err}")
    except subprocess.TimeoutExpired:
        return Ansible(exe=found, problem=f"{found} --version did not return in 60s")

    if done.returncode != 0:
        # an install broken by its own dependencies fails here; its stderr, whole
        detail = (done.stderr or done.stdout).strip() or f"exit {done.returncode}"
        return Ansible(exe=found, problem=f"{found} --version failed: {detail}")

    return _parse(found, done.stdout)


def resolve_ansible(exe: str | None = None) -> Ansible:
    """PATH first, the bundled ansible-core second."""
    if exe:
        return replace(find_ansible(exe), source="given")

    found = find_ansible()
    if found.usable:
        return replace(found, source="PATH")

    # The extra installs ansible-playbook beside this interpreter. Under uvx that
    # directory need not be on PATH, so it is found by location and not by name.
    bundled = Path(sys.executable).parent / PLAYBOOK_EXE
    if not bundled.exists():
        return found  # keep the PATH problem: with no fallback it is the one to report
    return replace(find_ansible(str(bundled)), source="bundled")


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
