"""Two renders compared, and repo' rendered -- no real Ansible and no collections.

ansible-playbook is a stub that records how it was called and where, and writes the
structured configurations a plan file beside it names.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from netadopt.ansible import Ansible
from netadopt.compare import REBUILT, REPOSITORY, Host, compare, render_rebuilt
from netadopt.reconstruct import PLAYBOOK
from netadopt.xr import API_VERSION

STUB = """\
import json, os, pathlib, sys
here = pathlib.Path(__file__).parent
args = sys.argv[1:]
(here / "call.json").write_text(json.dumps({
    "args": args,
    "files": sorted(str(p) for p in pathlib.Path().rglob("*")),
    "vault": os.environ.get("ANSIBLE_VAULT_PASSWORD_FILE"),
}))
plan = json.loads((here / "plan.json").read_text())
out = pathlib.Path(json.loads(args[args.index("-e") + 1])["structured_dir"])
out.mkdir(parents=True, exist_ok=True)
for host, config in plan.get("hosts", {}).items():
    (out / f"{host}.yml").write_text(json.dumps(config))
sys.exit(plan.get("exit", 0))
"""

SPINE = {"hostname": "dc1-spine1", "router_bgp": {"as": "65100"}}


@pytest.fixture
def ansible(tmp_path: Path) -> Ansible:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    exe = bin_dir / "ansible-playbook"
    exe.write_text(f"#!{sys.executable}\n{STUB}")
    exe.chmod(0o755)
    (bin_dir / "plan.json").write_text(json.dumps({"hosts": {"dc1-spine1": SPINE}}))
    return Ansible(exe=str(exe), core="2.21.3")


def call(ansible: Ansible) -> dict:
    return json.loads(Path(ansible.exe).with_name("call.json").read_text())


def documents(**spec: object) -> list[dict]:
    """A Fabric as emit writes one, with one FabricInput."""
    return [
        {
            "apiVersion": API_VERSION,
            "kind": "Fabric",
            "metadata": {"name": "dc1"},
            "spec": {
                "inputs": ["dc1-fabric"],
                "play": {"hosts": "FABRIC", "tasks": []},
                "groups": {"all": {"children": {"FABRIC": {"hosts": {"dc1-spine1": None}}}}},
                **spec,
            },
        },
        {
            "apiVersion": API_VERSION,
            "kind": "FabricInput",
            "metadata": {"name": "dc1-fabric"},
            "spec": {
                "appliesTo": {"group": "FABRIC"},
                "beside": "inventory",
                "design": {"fabric_name": "FABRIC"},
            },
        },
    ]


def test_identical_hosts_are_identical():
    result = compare({"dc1-spine1": SPINE}, {"dc1-spine1": dict(SPINE)})

    assert result.identical
    assert result.hosts == (Host("dc1-spine1"),)
    assert result.same == 1


def test_a_difference_is_named_by_the_top_level_keys_that_differ():
    rebuilt = {**SPINE, "router_bgp": {"as": "65101"}, "vlans": [{"id": 11}]}

    result = compare({"dc1-spine1": SPINE}, {"dc1-spine1": rebuilt})

    assert not result.identical
    assert result.hosts == (Host("dc1-spine1", differs=("router_bgp", "vlans")),)
    assert result.same == 0


def test_a_host_one_render_lacks_is_named_with_the_side_it_is_on():
    result = compare({"dc1-spine1": SPINE, "dc1-spine2": SPINE}, {"dc1-spine1": SPINE, "x": {}})

    assert [(host.name, host.only) for host in result.hosts] == [
        ("dc1-spine1", None),
        ("dc1-spine2", REPOSITORY),
        ("x", REBUILT),
    ]
    assert result.same == 1


def test_repo_prime_is_rebuilt_and_its_one_play_rendered(ansible, tmp_path):
    work = tmp_path / "work"

    got = render_rebuilt(ansible, documents(), "dc1", tmp_path / "collections", work)

    assert got.usable, got.problem
    assert got.hosts == {"dc1-spine1": SPINE}
    made = call(ansible)
    # No ansible.cfg: the inventory and the inputs beside it go under inventory/.
    assert {PLAYBOOK, "inventory/hosts.yml", "inventory/group_vars/FABRIC.yml"} <= set(
        made["files"]
    )
    assert made["args"][made["args"].index("-i") + 1] == "inventory/hosts.yml"


def test_a_fabric_that_does_not_rebuild_is_a_problem_and_nothing_renders(ansible, tmp_path):
    broken = documents(inputs=["dc1-fabric", "dc1-missing"])

    got = render_rebuilt(ansible, broken, "dc1", tmp_path / "collections", tmp_path / "work")

    assert not got.usable
    assert got.problem.startswith("the repository did not rebuild: ")
    assert "dc1-missing" in got.problem
    assert not Path(ansible.exe).with_name("call.json").exists()


def test_the_vault_password_reaches_ansible_when_the_fabric_names_its_secret(ansible, tmp_path):
    password = tmp_path / "vault-password"
    vaulted = documents(
        vaultPassword={"secretRef": {"name": "dc1-vault", "key": "password"}},
        ansibleCfg={"defaults": {"vault_password_file": ".vault"}},
    )

    got = render_rebuilt(
        ansible, vaulted, "dc1", tmp_path / "collections", tmp_path / "w", password
    )

    assert got.usable, got.problem
    assert call(ansible)["vault"] == str(password)


def test_no_secret_named_and_no_password_is_passed(ansible, tmp_path, monkeypatch):
    monkeypatch.delenv("ANSIBLE_VAULT_PASSWORD_FILE", raising=False)
    password = tmp_path / "vault-password"

    render_rebuilt(ansible, documents(), "dc1", tmp_path / "collections", tmp_path / "w", password)

    assert call(ansible)["vault"] is None
