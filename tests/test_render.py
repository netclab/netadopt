"""The render on a copy -- no real Ansible and no collections.

ansible-playbook is a stub that records how it was called and writes the structured
configurations a plan file beside it names, as the real role writes them into the
`structured_dir` of the extra vars.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
import yaml

from netadopt.ansible import Ansible
from netadopt.render import RENDER_PLAYBOOK, RENDER_ROLE, Rendered, render

STUB = """\
import json, os, pathlib, sys
here = pathlib.Path(__file__).parent
args = sys.argv[1:]
(here / "call.json").write_text(json.dumps({
    "args": args,
    "cwd": os.getcwd(),
    "collections": os.environ.get("ANSIBLE_COLLECTIONS_PATH"),
}))
plan = json.loads((here / "plan.json").read_text())
extra = json.loads(args[args.index("-e") + 1])
out = pathlib.Path(extra["structured_dir"])
out.mkdir(parents=True, exist_ok=True)
for host, config in plan.get("hosts", {}).items():
    (out / f"{host}.yml").write_text(config if isinstance(config, str) else json.dumps(config))
sys.stderr.write(plan.get("stderr", ""))
sys.exit(plan.get("exit", 0))
"""

CABLED = {"hostname": "dc1-spine1", "ethernet_interfaces": [{"name": "Ethernet1"}]}


@pytest.fixture
def ansible(tmp_path: Path) -> Ansible:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    exe = bin_dir / "ansible-playbook"
    exe.write_text(f"#!{sys.executable}\n{STUB}")
    exe.chmod(0o755)
    plan(Ansible(exe=str(exe)), hosts={"dc1-spine1": CABLED})
    return Ansible(exe=str(exe), core="2.21.3")


def plan(ansible: Ansible, **what: object) -> None:
    Path(ansible.exe).with_name("plan.json").write_text(json.dumps(what))


def call(ansible: Ansible) -> dict:
    return json.loads(Path(ansible.exe).with_name("call.json").read_text())


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "source"
    (root / "group_vars").mkdir(parents=True)
    (root / ".git").mkdir()
    (root / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
    (root / "intended" / "structured_configs").mkdir(parents=True)
    (root / "intended" / "structured_configs" / "dc1-spine1.yml").write_text("hostname: committed\n")
    (root / "inventory.yml").write_text("all:\n  children:\n    FABRIC:\n      hosts:\n        dc1-spine1:\n")
    (root / "group_vars" / "FABRIC.yml").write_text("fabric_name: FABRIC\n")
    (root / "build.yml").write_text(
        yaml.safe_dump(
            [
                {
                    "name": "Build",
                    "hosts": "FABRIC",
                    "gather_facts": False,
                    "connection": "httpapi",
                    "vars": {"output_dir_name": "intended"},
                    "tasks": [{"ansible.builtin.import_role": {"name": "arista.avd.eos_designs"}}],
                }
            ],
            sort_keys=False,
        )
    )
    return root


@pytest.fixture
def collections(tmp_path: Path) -> Path:
    return tmp_path / "cache" / "avd-6.4.0"


def built(ansible: Ansible, repo: Path, collections: Path, work: Path, **rest: object) -> Rendered:
    return render(ansible, repo, "build.yml", collections, work, **rest)


def rendered_play(work: Path) -> dict:
    [play] = yaml.safe_load((work / "repo" / RENDER_PLAYBOOK).read_text())
    return play


def test_the_render_reads_back_what_avd_wrote(ansible, repo, collections, tmp_path):
    out = built(ansible, repo, collections, tmp_path / "work")

    assert out.usable, out.problem
    assert out.hosts == {"dc1-spine1": CABLED}


def test_a_hostname_holding_dots_keeps_them(ansible, repo, collections, tmp_path):
    plan(ansible, hosts={"DC1.POD1.LEAF2A": CABLED})

    out = built(ansible, repo, collections, tmp_path / "work")

    assert list(out.hosts) == ["DC1.POD1.LEAF2A"]


def test_the_copy_holds_the_repository_but_no_git(ansible, repo, collections, tmp_path):
    work = tmp_path / "work"

    built(ansible, repo, collections, work)

    copy = work / "repo"
    assert (copy / "group_vars" / "FABRIC.yml").read_text() == "fabric_name: FABRIC\n"
    assert (copy / "inventory.yml").is_file()
    assert not (copy / ".git").exists()


def test_the_play_is_copied_with_its_tasks_replaced(ansible, repo, collections, tmp_path):
    work = tmp_path / "work"

    built(ansible, repo, collections, work)

    play = rendered_play(work)
    assert play["name"] == "Build"
    assert play["hosts"] == "FABRIC"
    assert play["gather_facts"] is False
    assert play["connection"] == "httpapi"
    assert play["vars"] == {"output_dir_name": "intended"}
    assert play["tasks"] == [{"ansible.builtin.import_role": {"name": RENDER_ROLE}}]


def test_the_roles_keyword_is_replaced_too(ansible, repo, collections, tmp_path):
    (repo / "build.yml").write_text(
        yaml.safe_dump([{"hosts": "FABRIC", "roles": ["arista.avd.eos_designs", "deploy"]}])
    )
    work = tmp_path / "work"

    built(ansible, repo, collections, work)

    play = rendered_play(work)
    assert "roles" not in play
    assert play["tasks"] == [{"ansible.builtin.import_role": {"name": RENDER_ROLE}}]


def test_ansible_runs_in_the_copy_with_the_given_collections(ansible, repo, collections, tmp_path):
    work = tmp_path / "work"

    built(ansible, repo, collections, work)

    made = call(ansible)
    assert Path(made["cwd"]).resolve() == (work / "repo").resolve()
    assert made["collections"] == str(collections)
    assert made["args"][0] == RENDER_PLAYBOOK


def test_the_render_sets_the_three_values_above_the_repositorys_own(ansible, repo, collections, tmp_path):
    work = tmp_path / "work"

    built(ansible, repo, collections, work)

    args = call(ansible)["args"]
    extra = json.loads(args[args.index("-e") + 1])
    assert extra["ansible_connection"] == "local"
    assert extra["ansible_become"] is False
    assert Path(extra["structured_dir"]).resolve() == (work / "structured_configs").resolve()


def test_nothing_is_read_from_a_committed_intended(ansible, repo, collections, tmp_path):
    out = built(ansible, repo, collections, tmp_path / "work")

    assert out.hosts["dc1-spine1"]["hostname"] == "dc1-spine1"
    committed = repo / "intended" / "structured_configs" / "dc1-spine1.yml"
    assert committed.read_text() == "hostname: committed\n"


def test_the_inventory_is_passed_only_when_given(ansible, repo, collections, tmp_path):
    built(ansible, repo, collections, tmp_path / "one")
    assert "-i" not in call(ansible)["args"]

    built(ansible, repo, collections, tmp_path / "two", inventory="inventory/")
    args = call(ansible)["args"]
    assert args[args.index("-i") + 1] == "inventory/"


def test_a_render_that_fails_names_the_lines_ansible_failed_on(ansible, repo, collections, tmp_path):
    plan(ansible, exit=2, stderr='fatal: [dc1-spine1]: FAILED! => {"msg": "no node type"}\nPLAY RECAP\n')

    out = built(ansible, repo, collections, tmp_path / "work")

    assert out.hosts == {}
    assert out.problem == 'fatal: [dc1-spine1]: FAILED! => {"msg": "no node type"}'


def test_a_fabric_that_fails_on_every_host_says_how_many_more(ansible, repo, collections, tmp_path):
    plan(ansible, exit=2, stderr="".join(f"fatal: [host{n}]: FAILED!\n" for n in range(25)))

    out = built(ansible, repo, collections, tmp_path / "work")

    lines = out.problem.splitlines()
    assert len(lines) == 11
    assert lines[-1] == "... and 15 more"


def test_a_failure_ansible_did_not_name_is_its_exit_code(ansible, repo, collections, tmp_path):
    plan(ansible, exit=4)

    out = built(ansible, repo, collections, tmp_path / "work")

    assert out.problem == "exit 4"


def test_a_run_that_writes_nothing_is_a_problem(ansible, repo, collections, tmp_path):
    plan(ansible, hosts={})

    out = built(ansible, repo, collections, tmp_path / "work")

    assert out.problem.startswith(f"{RENDER_ROLE} wrote no structured configuration in ")


def test_an_unreadable_structured_config_is_a_problem_not_a_missing_host(
    ansible, repo, collections, tmp_path
):
    plan(ansible, hosts={"dc1-spine1": CABLED, "dc1-spine2": "hostname: [\n"})

    out = built(ansible, repo, collections, tmp_path / "work")

    assert out.hosts == {}
    assert out.problem.startswith("dc1-spine2: its structured configuration is unreadable -- ")


def test_a_structured_config_that_is_not_a_mapping_is_a_problem(ansible, repo, collections, tmp_path):
    plan(ansible, hosts={"dc1-spine1": "- hostname\n"})

    out = built(ansible, repo, collections, tmp_path / "work")

    assert out.problem == "dc1-spine1: its structured configuration is list, not a mapping"


def test_a_play_that_imports_a_playbook_has_no_hosts_to_render(ansible, repo, collections, tmp_path):
    (repo / "build.yml").write_text(yaml.safe_dump([{"import_playbook": "other.yml"}]))

    out = built(ansible, repo, collections, tmp_path / "work")

    assert out.problem == "play [0] imports another playbook and has no hosts of its own"


def test_a_play_that_is_not_there(ansible, repo, collections, tmp_path):
    out = built(ansible, repo, collections, tmp_path / "work", play=3)

    assert out.problem == "no play [3] -- build.yml holds 1"


def test_a_playbook_that_is_not_there(ansible, repo, collections, tmp_path):
    out = render(ansible, repo, "deploy.yml", collections, tmp_path / "work")

    assert out.problem == f"no such playbook: {repo / 'deploy.yml'}"


def test_a_work_directory_holding_anything_else_is_refused(ansible, repo, collections, tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    (work / "theirs.txt").write_text("")

    out = built(ansible, repo, collections, work)

    assert out.problem == f"{work} is not an empty directory"
    assert not (work / "repo").exists()


def test_no_ansible_is_a_problem_and_nothing_is_copied(repo, collections, tmp_path):
    work = tmp_path / "work"

    out = render(Ansible(problem="no ansible-playbook"), repo, "build.yml", collections, work)

    assert out.problem == "no Ansible to render with: no ansible-playbook"
    assert not work.exists()
