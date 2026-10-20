"""`netadopt avd compare`, over a repository written in the test, with a stub Ansible.

The stub tells the two renders apart by what it runs in: the copy of repo' holds
`playbook.yml`, the copy of the repository here does not. What is measured is the
wiring -- which objects are rebuilt, what each render is given, the verdict and the
exit code -- and not AVD, which the corpus tier runs.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from netadopt import cli
from netadopt.cli import app
from netadopt.galaxy import Collections

STUB = """\
import json, os, pathlib, sys
here = pathlib.Path(__file__).parent
args = sys.argv[1:]
if "--version" in args:
    print("ansible-playbook [core 2.21.3]")
    raise SystemExit(0)
side = "rebuilt" if pathlib.Path("playbook.yml").exists() else "repository"
calls = here / "calls.json"
made = json.loads(calls.read_text()) if calls.exists() else []
made.append({"side": side, "args": args, "vault": os.environ.get("ANSIBLE_VAULT_PASSWORD_FILE")})
calls.write_text(json.dumps(made))
plan = json.loads((here / "plan.json").read_text()).get(side, {})
out = pathlib.Path(json.loads(args[args.index("-e") + 1])["structured_dir"])
out.mkdir(parents=True, exist_ok=True)
for host, config in plan.get("hosts", {}).items():
    (out / f"{host}.yml").write_text(json.dumps(config))
sys.stderr.write(plan.get("stderr", ""))
sys.exit(plan.get("exit", 0))
"""

BUILD = """
- name: Build
  hosts: FABRIC
  gather_facts: false
  tasks:
    - ansible.builtin.import_role: {name: arista.avd.eos_designs}
"""

INVENTORY = """
all:
  children:
    FABRIC:
      hosts:
        dc1-spine1:
        dc1-leaf1a:
"""

FABRIC = {
    "dc1-spine1": {"hostname": "dc1-spine1", "router_bgp": {"as": "65100"}},
    "dc1-leaf1a": {"hostname": "dc1-leaf1a", "router_bgp": {"as": "65101"}},
}


@pytest.fixture
def ansible(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A pretend Ansible beside the interpreter: it says its version, and it renders."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    exe = bin_dir / "ansible-playbook"
    exe.write_text(f"#!{sys.executable}\n{STUB}")  # before sys.executable is replaced
    exe.chmod(0o755)
    monkeypatch.setattr(sys, "executable", str(bin_dir / "python"))
    monkeypatch.delenv("ANSIBLE_VAULT_PASSWORD_FILE", raising=False)
    plan(bin_dir, repository={"hosts": FABRIC}, rebuilt={"hosts": FABRIC})
    return bin_dir


@pytest.fixture
def collections(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The cache, already installed. Where it comes from is galaxy's own test."""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
    installed = tmp_path / "collections"
    monkeypatch.setattr(cli, "ensure_collections", lambda found: Collections(path=installed))
    return installed


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "single-dc-l3ls"
    (root / "group_vars").mkdir(parents=True)
    (root / "ansible.cfg").write_text("[defaults]\ninventory=inventory.yml\n")
    (root / "inventory.yml").write_text(INVENTORY)
    (root / "group_vars" / "FABRIC.yml").write_text("fabric_name: FABRIC\n")
    (root / "build.yml").write_text(BUILD)
    return root


def plan(ansible: Path, **sides: object) -> None:
    (ansible / "plan.json").write_text(json.dumps(sides))


def calls(ansible: Path) -> list[dict]:
    path = ansible / "calls.json"
    return json.loads(path.read_text()) if path.exists() else []


def compare(repo: Path, *args: str):
    return CliRunner().invoke(app, ["avd", "compare", str(repo), *args])


def squeezed(text: str) -> str:
    """The report with its column padding taken out."""
    return re.sub(r"[ \t]+", " ", text)


def test_two_identical_renders_are_the_verdict_and_exit_0(repo, ansible, collections):
    result = compare(repo, "--playbook", "build.yml")

    assert result.exit_code == 0, result.output
    said = squeezed(result.stdout)
    assert "Repository rendered 2 hosts" in said
    assert "Rebuilt rendered 2 hosts" in said
    assert "Compared identical 2 of 2 hosts identical" in said
    assert "Differs" not in said
    assert [call["side"] for call in calls(ansible)] == ["repository", "rebuilt"]


def test_an_inventory_given_reaches_both_sides(repo, ansible, collections):
    (repo / "ansible.cfg").write_text("[defaults]\n")

    result = compare(repo, "--playbook", "build.yml", "--inventory", "inventory.yml")

    assert result.exit_code == 0, result.output
    repository, rebuilt = calls(ansible)
    assert repository["args"][repository["args"].index("-i") + 1] == "inventory.yml"
    # Carried into the Fabric, and back where reconstruct puts an inventory no cfg names.
    assert rebuilt["args"][rebuilt["args"].index("-i") + 1] == "inventory/hosts.yml"


def test_the_rebuilt_side_renders_repo_prime_from_the_objects(repo, ansible, collections):
    compare(repo, "--playbook", "build.yml")

    repository, rebuilt = calls(ansible)
    # The repository's own inventory as ansible.cfg names it; repo''s where reconstruct put it.
    assert "-i" not in repository["args"]
    assert rebuilt["args"][rebuilt["args"].index("-i") + 1] == "inventory.yml"


def test_a_host_that_differs_is_named_with_its_keys_and_exits_2(repo, ansible, collections):
    other = {**FABRIC, "dc1-leaf1a": {"hostname": "dc1-leaf1a", "router_bgp": {"as": "1"}}}
    plan(ansible, repository={"hosts": FABRIC}, rebuilt={"hosts": other})

    result = compare(repo, "--playbook", "build.yml")

    assert result.exit_code == 2
    said = squeezed(result.stdout)
    assert "Compared differs 1 of 2 hosts identical" in said
    assert "Differs\n dc1-leaf1a differs in router_bgp" in said
    assert "dc1-spine1 differs" not in said


def test_a_host_only_one_render_has_is_named(repo, ansible, collections):
    plan(
        ansible,
        repository={"hosts": FABRIC},
        rebuilt={"hosts": {"dc1-spine1": FABRIC["dc1-spine1"]}},
    )

    result = compare(repo, "--playbook", "build.yml")

    assert result.exit_code == 2
    assert "dc1-leaf1a rendered from the repository only" in squeezed(result.stdout)


def test_a_rebuilt_render_that_fails_is_said_line_by_line(repo, ansible, collections):
    failed = "[ERROR]: eos1: The lookup plugin 'file' failed\n[ERROR]: eos2: the same\n"
    plan(ansible, repository={"hosts": FABRIC}, rebuilt={"exit": 2, "stderr": failed})

    result = compare(repo, "--playbook", "build.yml")

    assert result.exit_code == 2
    said = squeezed(result.stdout)
    assert "Rebuilt fails [ERROR]: eos1: The lookup plugin 'file' failed" in said
    assert "Compared not measured a render failed" in said
    assert "The rebuilt repository does not render\n [ERROR]: eos2: the same" in said


def test_a_repository_that_does_not_render_is_no_verdict(repo, ansible, collections):
    plan(ansible, repository={"exit": 2, "stderr": "[ERROR]: local_users was removed\n"})

    result = compare(repo, "--playbook", "build.yml")

    assert result.exit_code == 2
    said = squeezed(result.stdout)
    assert "Repository fails [ERROR]: local_users was removed" in said
    assert "Compared not measured" in said


def test_objects_emit_cannot_write_are_the_rebuilt_side_failing(repo, ansible, collections):
    # An INI inventory is not carried, so there is no Fabric -- while Ansible reads it.
    (repo / "ansible.cfg").write_text("[defaults]\ninventory=hosts.ini\n")
    (repo / "hosts.ini").write_text("[FABRIC]\ndc1-spine1\ndc1-leaf1a\n")

    result = compare(repo, "--playbook", "build.yml")

    assert result.exit_code == 2
    assert "Rebuilt fails no Fabric: " in squeezed(result.stdout)
    assert [call["side"] for call in calls(ansible)] == ["repository"]


def test_the_vault_password_reaches_the_rebuilt_render_only(repo, ansible, collections):
    (repo / "ansible.cfg").write_text(
        "[defaults]\ninventory=inventory.yml\nvault_password_file=.vault\n"
    )
    (repo / ".vault").write_text("secret\n")

    result = compare(repo, "--playbook", "build.yml")

    assert result.exit_code == 0, result.output
    repository, rebuilt = calls(ansible)
    # The copy of the repository holds its own file, as ansible.cfg names it.
    assert repository["vault"] is None
    assert rebuilt["vault"] == str(repo / ".vault")


def test_no_playbook_named_compares_nothing(repo, ansible, collections):
    result = compare(repo)

    assert result.exit_code == 2
    assert "not compared: no playbook named -- pass --playbook" in result.stderr
    assert calls(ansible) == []


def test_collections_that_cannot_be_installed_compare_nothing(repo, ansible, monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
    monkeypatch.setattr(
        cli, "ensure_collections", lambda found: Collections(problem="galaxy.ansible.com is down")
    )

    result = compare(repo, "--playbook", "build.yml")

    assert result.exit_code == 2
    assert "not compared: galaxy.ansible.com is down" in result.stderr
    assert calls(ansible) == []


def test_no_ansible_is_an_incomplete_install_of_netadopt(repo, collections, tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "executable", str(tmp_path / "empty" / "python"))

    result = compare(repo, "--playbook", "build.yml")

    assert result.exit_code == 1
    assert 'install as: uvx "netadopt[avd]"' in result.stderr
