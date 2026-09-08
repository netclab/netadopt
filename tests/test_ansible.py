"""What `ansible-playbook --version` says, and what we make of it.

Every case here is a stub executable rather than a real Ansible: the point is to
pin what we do with output we do not control, including output we cannot produce on
demand -- a 2.9 install, a broken one, none at all.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from netadopt.ansible import find_ansible, resolve_ansible

REAL = """\
ansible-playbook [core 2.16.3]
  config file = /home/x/repo/ansible.cfg
  configured module search path = ['/home/x/.ansible/plugins/modules']
  ansible python module location = /usr/lib/python3/dist-packages/ansible
  ansible collection location = /home/x/.ansible/collections:/usr/share/ansible/collections
  executable location = /usr/bin/ansible-playbook
  python version = 3.12.3 (main, Feb  4 2025, 14:48:35) [GCC 13.3.0] (/usr/bin/python3)
  jinja version = 3.1.2
  libyaml = True
"""


@pytest.fixture
def stub(tmp_path: Path):
    """Write an executable that prints what we tell it, and exits how we tell it.

    A Python script with an absolute shebang, not a shell script: several tests
    empty PATH, and a /bin/sh stub calling `cat` would then print nothing and fail
    for a reason that has nothing to do with what is being tested.
    """
    interpreter = sys.executable  # captured now; a test may replace sys.executable

    def make(stdout: str = "", stderr: str = "", code: int = 0, name="ansible-playbook") -> str:
        path = tmp_path / name
        path.write_text(
            f"#!{interpreter}\n"
            "import sys\n"
            f"sys.stdout.write({stdout!r})\n"
            f"sys.stderr.write({stderr!r})\n"
            f"sys.exit({code})\n"
        )
        path.chmod(0o755)
        return str(path)

    return make


def test_reads_a_modern_version(stub):
    found = find_ansible(stub(REAL))

    assert found.usable
    assert found.core == "2.16.3"
    assert found.python == "3.12.3"
    assert found.config_file == "/home/x/repo/ansible.cfg"
    assert found.collections == (
        "/home/x/.ansible/collections",
        "/usr/share/ansible/collections",
    )


def test_no_ansible_cfg_is_none_not_the_word_none(stub):
    # Ansible prints "config file = None" when it found no ansible.cfg. Carrying
    # that through as a string would make a repo look configured when it is not.
    found = find_ansible(stub("ansible-playbook [core 2.16.3]\n  config file = None\n"))

    assert found.usable
    assert found.config_file is None


def test_reads_the_2_9_spelling(stub):
    # 2.9 has no [core ...]. Old, but it is what an old repository's pipeline runs.
    found = find_ansible(stub("ansible-playbook 2.9.27\n  config file = None\n"))

    assert found.usable
    assert found.core == "2.9.27"


def test_unreadable_version_is_a_problem_carrying_the_line(stub):
    found = find_ansible(stub("something else entirely\n"))

    assert not found.usable
    assert "something else entirely" in found.problem


def test_a_broken_install_reports_its_own_stderr(stub):
    # The stderr of an install broken by its own dependencies is the only useful
    # thing anyone can say about it, so it is carried whole.
    message = "ERROR: Ansible requires the locale encoding to be UTF-8"
    found = find_ansible(stub(stderr=message + "\n", code=1))

    assert not found.usable
    assert message in found.problem


def test_a_name_and_a_path_fail_differently(tmp_path, monkeypatch):
    # Telling him "not found on PATH" about a path he typed sends him to fix the
    # wrong thing.
    monkeypatch.setenv("PATH", str(tmp_path))

    assert find_ansible().problem.endswith("not found on PATH")
    assert find_ansible("/nowhere/ansible-playbook").problem.endswith("not found")
    assert not find_ansible("/nowhere/ansible-playbook").problem.endswith("on PATH")


def test_his_ansible_on_path_wins(stub, tmp_path, monkeypatch):
    stub(REAL)
    monkeypatch.setenv("PATH", str(tmp_path))

    found = resolve_ansible()

    assert found.source == "PATH"
    assert found.his
    assert found.core == "2.16.3"  # the stub answered, not some ansible on the box


def test_an_explicit_exe_is_neither_path_nor_ours(stub, tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path))

    found = resolve_ansible(stub(REAL, name="somewhere-else"))

    assert found.source == "given"
    assert found.his


def test_ours_is_used_only_when_path_has_none_and_says_so(stub, tmp_path, monkeypatch):
    # The extra installs ansible-playbook beside our own interpreter, which is not
    # necessarily on his PATH -- so it is found by location, not by name.
    stub(REAL)
    monkeypatch.setenv("PATH", "")
    monkeypatch.setattr(sys, "executable", str(tmp_path / "python"))

    found = resolve_ansible()

    assert found.source == "bundled"
    assert found.core == "2.16.3"
    assert not found.his  # the report has to say the numbers are not from his pipeline


def test_with_nothing_anywhere_the_path_problem_is_the_one_reported(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", "")
    monkeypatch.setattr(sys, "executable", str(tmp_path / "python"))

    found = resolve_ansible()

    assert not found.usable
    assert found.source is None
    assert "on PATH" in found.problem  # not "ours is missing": that is not his to fix
