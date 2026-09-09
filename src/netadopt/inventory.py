"""The inventory, as Ansible resolves it.

`ansible-inventory --list` answers with no collection installed -- measured on AVD's
examples. It resolves what guessing would get wrong: a directory of files instead of
one, several sources named in ansible.cfg, host ranges, inventory plugins, group_vars
merged in.

What comes back is the tree Ansible built: group names, membership, and merged
hostvars. Nothing is renamed and nothing is flattened.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from netadopt.ansible import Ansible

# Looked up next to ansible-playbook, not on PATH.
INVENTORY_EXE = "ansible-inventory"

# Matched in stderr: Ansible prints it, then exits 0 with a valid, empty answer.
NOTHING_PARSED = "No inventory was parsed"

# Far above what even a large inventory needs, measured in seconds.
TIMEOUT = 60


@dataclass(frozen=True)
class Inventory:
    source: str | None = None  # what -i was given; None means ansible.cfg decided
    groups: dict[str, dict] = field(default_factory=dict)
    hostvars: dict[str, dict] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()  # Ansible's own stderr, kept whole
    problem: str | None = None

    @property
    def usable(self) -> bool:
        return self.problem is None

    @property
    def hosts(self) -> tuple[str, ...]:
        return tuple(self.hostvars)


def read_inventory(ansible: Ansible, repo: Path, source: str | None = None) -> Inventory:
    """Run `ansible-inventory --list` in `repo` and return what it says.

    `source` is the -i argument: a file, a directory, or nothing at all when
    ansible.cfg names it. The command runs with `repo` as its working directory --
    the only place Ansible looks for an ansible.cfg.
    """
    if not ansible.usable:
        return Inventory(source=source, problem=f"no Ansible to ask: {ansible.problem}")

    exe = ansible.beside(INVENTORY_EXE)
    if exe is None:
        return Inventory(
            source=source, problem=f"{INVENTORY_EXE} is missing beside {ansible.exe}"
        )

    command = [str(exe), "--list"]
    if source:
        command += ["-i", source]

    try:
        done = subprocess.run(
            command,
            cwd=repo,
            capture_output=True,
            text=True,
            timeout=TIMEOUT,
            # closed, so a vault password prompt becomes an error instead of a hang
            stdin=subprocess.DEVNULL,
        )
    except OSError as err:
        return Inventory(source=source, problem=f"{exe} could not be run: {err}")
    except subprocess.TimeoutExpired:
        return Inventory(source=source, problem=f"{exe} did not return in {TIMEOUT}s")

    warnings = tuple(line for line in done.stderr.splitlines() if line.strip())

    if done.returncode != 0:
        # Ansible's own stderr, whatever failed -- a bad -i path, a missing inventory
        # plugin, a whole-file vault with no password; the exit code if it said
        # nothing.
        detail = done.stderr.strip() or f"exit {done.returncode}"
        return Inventory(source=source, warnings=warnings, problem=detail)

    try:
        listed = json.loads(done.stdout)
    except json.JSONDecodeError as err:
        return Inventory(
            source=source,
            warnings=warnings,
            problem=f"{exe} --list did not return JSON: {err}",
        )

    if any(NOTHING_PARSED in line for line in warnings):
        return Inventory(
            source=source,
            warnings=warnings,
            problem="no inventory was parsed -- name one with --inventory, "
            "or set inventory= in ansible.cfg",
        )

    # A !vault value needs no password: Ansible hands over
    # {"__ansible_vault": "$ANSIBLE_VAULT;1.1;AES256\n..."} -- exit 0, no prompt.
    meta = listed.pop("_meta", {})
    return Inventory(
        source=source,
        groups=listed,
        hostvars=meta.get("hostvars", {}),
        warnings=warnings,
    )
