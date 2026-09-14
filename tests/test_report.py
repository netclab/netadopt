"""`netadopt avd report`, over a repository written in the test, with a stub Ansible."""

from __future__ import annotations

import json
import sys
import textwrap
from pathlib import Path

import pytest
from typer.testing import CliRunner

from netadopt.cli import app

BUILD = """
- name: Build
  hosts: FABRIC
  gather_facts: false
  tasks:
    - name: Generate
      ansible.builtin.import_role:
        name: arista.avd.eos_designs
- name: Build the twin
  hosts: FABRIC
  vars:
    avd_digital_twin_mode: true
  tasks: []
"""

INVENTORY = """
all:
  children:
    FABRIC:
      hosts:
        dc1-spine1: {ansible_host: 172.16.1.11}
"""

LISTED = {
    "_meta": {"hostvars": {"dc1-spine1": {"ansible_host": "172.16.1.11"}}},
    "all": {"children": ["FABRIC"]},
    "FABRIC": {"hosts": ["dc1-spine1"]},
}


def write(root: Path, files: dict[str, str]) -> None:
    for path, text in files.items():
        (root / path).parent.mkdir(parents=True, exist_ok=True)
        (root / path).write_text(textwrap.dedent(text).lstrip())


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "single-dc-l3ls"
    write(
        root,
        {
            "ansible.cfg": "[defaults]\ninventory=inventory.yml\n",
            "inventory.yml": INVENTORY,
            "build.yml": BUILD,
            "group_vars/FABRIC.yml": "fabric_name: FABRIC\n",
        },
    )
    return root


@pytest.fixture
def ansible(tmp_path: Path):
    """A pretend Ansible: ansible-playbook says its version, and ansible-inventory
    lists LISTED and writes `stderr`. Returns the ansible-playbook to pass."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()

    def make(stderr: str = "") -> str:
        playbook = bin_dir / "ansible-playbook"
        playbook.write_text(f"#!{sys.executable}\nprint('ansible-playbook [core 2.21.3]')\n")
        inventory = bin_dir / "ansible-inventory"
        inventory.write_text(
            f"#!{sys.executable}\nimport sys\n"
            f"sys.stdout.write({json.dumps(LISTED)!r})\n"
            f"sys.stderr.write({stderr!r})\n"
        )
        for exe in (playbook, inventory):
            exe.chmod(0o755)
        return str(playbook)

    return make


def report(repo: Path, ansible: str, *args: str):
    return CliRunner().invoke(app, ["avd", "report", str(repo), "--ansible", ansible, *args])


def squeezed(lines: list[str]) -> list[str]:
    """Each line with its runs of spaces made one, as the columns are not the point."""
    return [" ".join(line.split()) for line in lines]


def section(output: str, title: str) -> list[str]:
    """The lines under a section's title, up to the blank line that ends it."""
    lines = squeezed(output.splitlines())
    if title not in lines:
        return []
    body = lines[lines.index(title) + 1 :]
    return body[: body.index("")] if "" in body else body


def test_what_emit_carries_is_under_carried(repo, ansible):
    result = report(repo, ansible(), "--playbook", "build.yml")

    assert result.exit_code == 0, result.output
    assert section(result.output, "Carried") == [
        "Fabric single-dc-l3ls, from play 0",
        "FabricInputs 1",
    ]


def test_another_play_and_the_vault_password_are_under_not_carried(repo, ansible):
    write(repo, {"ansible.cfg": "[defaults]\ninventory=inventory.yml\nvault_password_file=.vault\n"})

    result = report(repo, ansible(), "--playbook", "build.yml")

    assert section(result.output, "Not carried") == [
        "play 1 each a separate run: --play N --name NAME",
        ".vault the vault password; create its Secret:",
        f"kubectl create secret generic single-dc-l3ls-vault --from-file=password={repo / '.vault'}",
    ]


def test_a_vars_file_that_did_not_read_is_not_carried(repo, ansible):
    write(repo, {"group_vars/FABRIC.yml": "$ANSIBLE_VAULT;1.1;AES256\n6162\n"})

    result = report(repo, ansible(), "--playbook", "build.yml")

    assert "group_vars/FABRIC.yml is str, not a mapping" in section(result.output, "Not carried")


def test_a_host_vars_file_for_no_host_is_a_warning(repo, ansible):
    write(repo, {"host_vars/all.yml": "root_dir: '{{ playbook_dir }}'\n"})

    result = report(repo, ansible(), "--playbook", "build.yml")

    assert "host_vars/all.yml all is no host in the inventory" in section(result.output, "Warnings")


def test_a_plain_text_password_is_a_warning(repo, ansible):
    write(repo, {"group_vars/FABRIC.yml": "fabric_name: FABRIC\nansible_password: arista\n"})

    result = report(repo, ansible(), "--playbook", "build.yml")

    assert (
        "ansible_password plain text in FabricInput single-dc-l3ls-fabric, "
        "carried as the repository has it"
    ) in section(result.output, "Warnings")


def test_without_a_playbook_the_candidates_are_named_and_nothing_is_carried(repo, ansible):
    result = report(repo, ansible())

    assert result.exit_code == 2
    assert "Playbook not named name one with --playbook: build.yml" in squeezed(
        result.output.splitlines()
    )
    assert section(result.output, "Carried") == []


def test_what_ansible_writes_in_brackets_is_printed_as_written(repo, ansible):
    warning = "[WARNING]: Found both group and host with same name: all"

    result = report(repo, ansible(stderr=warning + "\n"), "--playbook", "build.yml")

    assert warning in result.output
