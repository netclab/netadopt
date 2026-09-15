"""Reading `ansible-playbook --version`.

Stub executables, not a real Ansible: the interesting outputs cannot be produced on
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
    """Write an executable that prints and exits as the test says.

    A Python script with an absolute shebang, not a shell script: it must run whatever
    PATH a test sets, and a test may replace sys.executable.
    """
    interpreter = sys.executable  # captured now; a test may replace sys.executable

    def make(stdout: str = "", stderr: str = "", code: int = 0, where: Path = tmp_path) -> str:
        where.mkdir(parents=True, exist_ok=True)
        path = where / "ansible-playbook"
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
    # 2.9 has no [core ...].
    found = find_ansible(stub("ansible-playbook 2.9.27\n  config file = None\n"))

    assert found.usable
    assert found.core == "2.9.27"


def test_unreadable_version_is_a_problem_carrying_the_line(stub):
    found = find_ansible(stub("something else entirely\n"))

    assert not found.usable
    assert "something else entirely" in found.problem


def test_a_broken_install_reports_its_own_stderr(stub):
    # an install broken by its own dependencies: its stderr, whole
    message = "ERROR: Ansible requires the locale encoding to be UTF-8"
    found = find_ansible(stub(stderr=message + "\n", code=1))

    assert not found.usable
    assert message in found.problem


def test_a_missing_path_is_named():
    found = find_ansible("/nowhere/ansible-playbook")

    assert not found.usable
    assert found.problem == "/nowhere/ansible-playbook not found"


def test_the_one_beside_the_interpreter_answers(stub, tmp_path, monkeypatch):
    # Under uvx the environment's bin need not be on PATH, so it is found by location.
    stub(REAL)
    monkeypatch.setenv("PATH", "")
    monkeypatch.setattr(sys, "executable", str(tmp_path / "python"))

    found = resolve_ansible()

    assert found.usable
    assert found.core == "2.16.3"
    assert found.exe == str(tmp_path / "ansible-playbook")


def test_an_ansible_on_path_is_never_asked(stub, tmp_path, monkeypatch):
    on_path = stub(REAL, where=tmp_path / "on-path")
    monkeypatch.setenv("PATH", str(Path(on_path).parent))
    monkeypatch.setattr(sys, "executable", str(tmp_path / "env" / "python"))

    found = resolve_ansible()

    assert not found.usable
    assert found.problem == f"{tmp_path / 'env' / 'ansible-playbook'} not found"
