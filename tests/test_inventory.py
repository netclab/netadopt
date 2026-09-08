"""What `ansible-inventory --list` says, and what we make of it.

Stubs again, for the same reason as in test_ansible: the interesting answers are
the ones a healthy install will not give on demand -- a repository whose vault is
locked, an inventory nobody named, output that is not JSON.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from netadopt.ansible import Ansible, find_ansible
from netadopt.inventory import NOTHING_PARSED, read_inventory

LISTED = {
    "_meta": {"hostvars": {"dc1-leaf1a": {"type": "l3leaf"}, "dc1-spine1": {}}},
    "all": {"children": ["FABRIC"]},
    "FABRIC": {"hosts": ["dc1-leaf1a", "dc1-spine1"]},
}


@pytest.fixture
def install(tmp_path: Path):
    """A whole pretend Ansible install: both executables, side by side.

    read_inventory takes ansible-inventory from beside the ansible-playbook we
    settled on, so the fixture has to build the pair rather than one file.
    """
    interpreter = sys.executable
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    record = tmp_path / "called.json"

    def make(stdout: str = "", stderr: str = "", code: int = 0, inventory: bool = True) -> Ansible:
        (bin_dir / "ansible-playbook").write_text(
            f"#!{interpreter}\nprint('ansible-playbook [core 2.16.3]')\n"
        )
        (bin_dir / "ansible-playbook").chmod(0o755)
        if inventory:
            # The stub writes down how it was called: argv and working directory are
            # half of what read_inventory is responsible for getting right.
            (bin_dir / "ansible-inventory").write_text(
                f"#!{interpreter}\n"
                "import json, os, sys\n"
                f"open({str(record)!r}, 'w').write("
                "json.dumps({'argv': sys.argv[1:], 'cwd': os.getcwd()}))\n"
                f"sys.stdout.write({stdout!r})\n"
                f"sys.stderr.write({stderr!r})\n"
                f"sys.exit({code})\n"
            )
            (bin_dir / "ansible-inventory").chmod(0o755)
        return find_ansible(str(bin_dir / "ansible-playbook"))

    make.called = lambda: json.loads(record.read_text())  # type: ignore[attr-defined]
    return make


def test_groups_and_hostvars_come_back_split(install, tmp_path):
    ansible = install(json.dumps(LISTED))

    listed = read_inventory(ansible, tmp_path)

    assert listed.usable
    assert listed.hosts == ("dc1-leaf1a", "dc1-spine1")
    assert listed.hostvars["dc1-leaf1a"] == {"type": "l3leaf"}
    assert set(listed.groups) == {"all", "FABRIC"}
    assert "_meta" not in listed.groups  # it is not one of his groups


def test_it_runs_in_the_repository_because_that_is_where_ansible_cfg_is(install, tmp_path):
    ansible = install(json.dumps(LISTED))
    repo = tmp_path / "repo"
    repo.mkdir()

    read_inventory(ansible, repo)

    assert install.called()["cwd"] == str(repo)


def test_a_named_source_is_passed_through_as_ansibles_own_flag(install, tmp_path):
    ansible = install(json.dumps(LISTED))

    listed = read_inventory(ansible, tmp_path, "inventory/")

    assert install.called()["argv"] == ["--list", "-i", "inventory/"]
    assert listed.source == "inventory/"


def test_with_no_source_ansible_is_left_to_read_ansible_cfg(install, tmp_path):
    ansible = install(json.dumps(LISTED))

    read_inventory(ansible, tmp_path)

    assert install.called()["argv"] == ["--list"]


def test_an_inventory_nobody_named_is_not_an_empty_inventory(install, tmp_path):
    # Ansible warns and still exits 0 with a valid, empty answer. Reporting that as
    # "no hosts" would describe his repository; the truth is about our invocation.
    empty = {"_meta": {"hostvars": {}}, "all": {"children": ["ungrouped"]}}
    ansible = install(json.dumps(empty), stderr=f"[WARNING]: {NOTHING_PARSED}\n")

    listed = read_inventory(ansible, tmp_path)

    assert not listed.usable
    assert "--inventory" in listed.problem
    assert "ansible.cfg" in listed.problem


def test_a_locked_vault_reaches_him_in_ansibles_own_words(install, tmp_path):
    message = "[ERROR]: Attempting to decrypt but no vault secrets found."
    ansible = install(stderr=message + "\n", code=4)

    listed = read_inventory(ansible, tmp_path)

    assert not listed.usable
    assert listed.problem == message


def test_output_that_is_not_json_is_a_problem_not_a_traceback(install, tmp_path):
    ansible = install("not json at all\n")

    listed = read_inventory(ansible, tmp_path)

    assert not listed.usable
    assert "did not return JSON" in listed.problem


def test_warnings_are_kept_even_when_the_answer_is_good(install, tmp_path):
    warning = "[WARNING]: Found both group and host with same name: DC1"
    ansible = install(json.dumps(LISTED), stderr=warning + "\n")

    listed = read_inventory(ansible, tmp_path)

    assert listed.usable
    assert listed.warnings == (warning,)


def test_without_ansible_inventory_beside_it_we_say_which_install_lacks_it(install, tmp_path):
    ansible = install(inventory=False)

    listed = read_inventory(ansible, tmp_path)

    assert not listed.usable
    assert "ansible-inventory is missing beside" in listed.problem


def test_with_no_ansible_at_all_the_ansible_problem_is_carried_forward(tmp_path):
    # Not a second, weaker complaint of our own: the reason there is no answer is
    # the one already established, and repeating it keeps the report honest.
    ansible = find_ansible("/nowhere/ansible-playbook")

    listed = read_inventory(ansible, tmp_path)

    assert not listed.usable
    assert "no Ansible to ask" in listed.problem
    assert ansible.problem in listed.problem
