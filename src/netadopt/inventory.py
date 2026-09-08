"""The inventory, as Ansible resolves it.

Here we do ask Ansible, and it answers with no collection installed at all --
measured on AVD's own examples. That matters, because the inventory is where
guessing would go wrong: a directory of files instead of one, several sources named
in ansible.cfg, host ranges, inventory plugins, and group_vars merged in by rules
that are Ansible's and not ours.

What comes back is his tree: group names, membership, and the hostvars Ansible
already merged. Nothing is renamed and nothing is flattened.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from netadopt.ansible import INVENTORY_EXE, Ansible

# Ansible says this on stderr and still exits 0 with a valid, empty answer. Passing
# that on as "no hosts" would be a different statement from "nobody said where the
# inventory is", and only the second one is true.
NOTHING_PARSED = "No inventory was parsed"

# Measured on the largest inventories in AVD's corpus -- 340 host_vars files, 501
# devices -- at 0.8 to 2.4 seconds. This is a "something is wrong" limit, not a
# capacity limit, so it is generous against that and still short enough to hit.
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

    `source` is his -i argument: a file, a directory, or nothing at all when
    ansible.cfg names it. The command runs with `repo` as its working directory,
    because that is the only place Ansible looks for an ansible.cfg.
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
            # Nothing we run may ever ask the user a question. A vault password
            # prompt reaching an interactive terminal would stop the tool dead in
            # the middle of a report; closed, it becomes an error we can print.
            stdin=subprocess.DEVNULL,
        )
    except OSError as err:
        return Inventory(source=source, problem=f"{exe} could not be run: {err}")
    except subprocess.TimeoutExpired:
        return Inventory(source=source, problem=f"{exe} did not return in {TIMEOUT}s")

    warnings = tuple(line for line in done.stderr.splitlines() if line.strip())

    if done.returncode != 0:
        # His inventory failing is a result, not an accident of ours, and Ansible's
        # own message is the one that says where to look. An encrypted group_vars
        # file with no password reaches him here as
        # "Attempting to decrypt but no vault secrets found." -- measured.
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

    # A !vault value survives this without a password: Ansible hands it over as
    # {"__ansible_vault": "$ANSIBLE_VAULT;1.1;AES256\n..."} -- the ciphertext whole,
    # no prompt, no error. Measured. So it can be carried faithfully and spotted.
    meta = listed.pop("_meta", {})
    return Inventory(
        source=source,
        groups=listed,
        hostvars=meta.get("hostvars", {}),
        warnings=warnings,
    )
