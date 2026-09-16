"""`netadopt avd lab`, over a repository written in the test, with a stub Ansible.

The collections are stubbed as well: fetching them is galaxy's own test, and what is
measured here is the wiring -- what reaches stdout, what reaches stderr, and what is
refused before anything is rendered.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from netadopt import cli
from netadopt.cli import app
from netadopt.galaxy import Collections, cache_root

STUB = """\
import json, pathlib, sys
here = pathlib.Path(__file__).parent
args = sys.argv[1:]
if "--version" in args:
    print("ansible-playbook [core 2.21.3]")
    raise SystemExit(0)
(here / "call.json").write_text(json.dumps(args))
plan = json.loads((here / "plan.json").read_text())
extra = json.loads(args[args.index("-e") + 1])
out = pathlib.Path(extra["structured_dir"])
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
- name: Build the twin
  hosts: FABRIC
  tasks:
    - ansible.builtin.import_role: {name: arista.avd.eos_designs}
- name: Clear facts
  hosts: all
  tasks: []
"""

INVENTORY = """
all:
  children:
    FABRIC:
      hosts:
        dc1-spine1:
"""


def cabled(*ends: tuple[str, str, str]) -> dict:
    return {
        "ethernet_interfaces": [
            {"name": name, "metadata": {"peer": peer, "peer_interface": peer_interface}}
            for name, peer, peer_interface in ends
        ]
    }


FABRIC = {
    "dc1-spine1": cabled(("Ethernet1", "dc1-leaf1a", "Ethernet1")),
    "dc1-leaf1a": cabled(
        ("Ethernet1", "dc1-spine1", "Ethernet1"), ("Ethernet5", "server1", "PCI1")
    ),
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
    plan(bin_dir, hosts=FABRIC)
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
    root.mkdir()
    (root / "ansible.cfg").write_text("[defaults]\ninventory=inventory.yml\n")
    (root / "inventory.yml").write_text(INVENTORY)
    (root / "build.yml").write_text(BUILD)
    return root


def plan(ansible: Path, **what: object) -> None:
    (ansible / "plan.json").write_text(json.dumps(what))


def call(ansible: Path) -> list[str]:
    return json.loads((ansible / "call.json").read_text())


def lab(repo: Path, *args: str):
    return CliRunner().invoke(app, ["avd", "lab", str(repo), *args])


def nodes(stdout: str) -> list[dict]:
    return yaml.safe_load(stdout)["topology"]["nodes"]


def test_the_values_go_to_stdout_and_everything_else_to_stderr(repo, ansible, collections):
    result = lab(repo, "--playbook", "build.yml")

    assert result.exit_code == 0, result.stderr
    assert [node["name"] for node in nodes(result.stdout)] == [
        "dc1-leaf1a",
        "dc1-spine1",
        "server1",
    ]
    assert "rendered 2 hosts" in result.stderr
    assert "topology" not in result.stderr


def test_the_nodes_are_counted_because_the_rendered_hosts_are_not_all_of_them(
    repo, ansible, collections
):
    result = lab(repo, "--playbook", "build.yml")

    assert "rendered 2 hosts" in result.stderr
    assert "3 nodes (2 ceos, 1 linux), 2 networks" in result.stderr


def test_a_play_that_was_not_rendered_is_named(repo, ansible, collections):
    # Another play is another fabric over the same hosts, and one lab must not read as
    # the lab of the repository. A play pulling in no role is named without advice.
    result = lab(repo, "--playbook", "build.yml")

    assert "play [1] Build the twin: not rendered -- render it with --play 1" in result.stderr
    assert "play [2] Clear facts: not rendered -- pulls in no role" in result.stderr
    assert "--play 2" not in result.stderr
    assert "play [0]" not in result.stderr.replace("rendering play [0]", "")


def test_a_peer_outside_the_fabric_is_the_node_connected_says(repo, ansible, collections):
    result = lab(repo, "--playbook", "build.yml", "--connected", "ceos")

    assert {node["name"]: node["type"] for node in nodes(result.stdout)}["server1"] == "ceos"


def test_what_the_lab_leaves_out_is_said_on_stderr(repo, ansible, collections):
    result = lab(repo, "--playbook", "build.yml", "--connected", "none")

    assert result.exit_code == 0, result.stderr
    assert "server1 is outside the fabric, --connected none" in result.stderr
    assert [node["name"] for node in nodes(result.stdout)] == ["dc1-leaf1a", "dc1-spine1"]


def test_the_ceos_values_are_written_only_when_given(repo, ansible, collections):
    plain = nodes(lab(repo, "--playbook", "build.yml").stdout)[0]
    assert set(plain) == {"name", "type", "interfaces"}

    given = lab(
        repo, "--playbook", "build.yml", "--ceos-image", "ceos:4.34.0F", "--ceos-cpu", "2"
    )
    node = nodes(given.stdout)[0]
    assert (node["image"], node["cpu"]) == ("ceos:4.34.0F", "2")
    assert "memory" not in node


def test_the_play_and_the_inventory_reach_the_render(repo, ansible, collections):
    result = lab(repo, "--playbook", "build.yml", "--play", "1", "--inventory", "inventory/")

    assert "rendering play [1] of build.yml" in result.stderr
    args = call(ansible)
    assert args[args.index("-i") + 1] == "inventory/"


def test_the_first_use_says_the_collections_are_being_fetched(repo, ansible, collections):
    result = lab(repo, "--playbook", "build.yml")

    assert f"fetching arista.avd 6.4.0 and its collections into {cache_root()}" in result.stderr


def test_a_cache_that_is_there_says_nothing(repo, ansible, collections):
    cache_root().mkdir(parents=True)

    result = lab(repo, "--playbook", "build.yml")

    assert "fetching" not in result.stderr


def test_a_target_that_is_not_built_is_refused_before_anything_runs(repo, ansible, collections):
    result = lab(repo, "--playbook", "build.yml", "--for", "containerlab")

    assert result.exit_code == 2
    assert result.stderr.strip() == "no lab: --for containerlab is not built -- netclab"
    assert result.stdout == ""
    assert not (ansible / "call.json").exists()


def test_a_connected_that_is_not_one_of_the_three_is_refused(repo, ansible, collections):
    result = lab(repo, "--playbook", "build.yml", "--connected", "vm")

    assert result.exit_code == 2
    assert result.stderr.strip() == "no lab: --connected vm is not one of linux, ceos, none"
    assert not (ansible / "call.json").exists()


def test_no_playbook_named(repo, ansible, collections):
    result = lab(repo)

    assert result.exit_code == 2
    assert result.stderr.strip() == "no lab: no playbook named -- pass --playbook"


def test_a_render_that_fails_writes_no_values(repo, ansible, collections):
    plan(ansible, exit=2, stderr='fatal: [dc1-spine1]: FAILED! => {"msg": "no node type"}\n')

    result = lab(repo, "--playbook", "build.yml")

    assert result.exit_code == 2
    assert result.stdout == ""
    assert 'no lab: fatal: [dc1-spine1]: FAILED! => {"msg": "no node type"}' in result.stderr


def test_a_lab_that_cannot_be_built_writes_no_values(repo, ansible, collections):
    plan(ansible, hosts={"dc1-spine1": cabled(), "DC1-SPINE1": cabled()})

    result = lab(repo, "--playbook", "build.yml")

    assert result.exit_code == 2
    assert result.stdout == ""
    assert "no lab: DC1-SPINE1 and dc1-spine1 are both spelled dc1-spine1" in result.stderr


def test_collections_that_cannot_be_installed_write_no_values(repo, ansible, monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
    monkeypatch.setattr(
        cli, "ensure_collections", lambda found: Collections(problem="galaxy.ansible.com is down")
    )

    result = lab(repo, "--playbook", "build.yml")

    assert result.exit_code == 2
    assert result.stdout == ""
    assert "no lab: galaxy.ansible.com is down" in result.stderr
    assert not (ansible / "call.json").exists()


def test_no_ansible_is_an_incomplete_install_of_netadopt(repo, collections, tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "executable", str(tmp_path / "empty" / "python"))

    result = lab(repo, "--playbook", "build.yml")

    assert result.exit_code == 1
    assert 'install as: uvx "netadopt[avd]"' in result.stderr
