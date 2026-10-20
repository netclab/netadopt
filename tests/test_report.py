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
  tasks:
    - name: Generate
      ansible.builtin.import_role:
        name: arista.avd.eos_designs
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
def ansible(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A pretend Ansible installed beside the interpreter, as the avd extra installs it:
    ansible-playbook says its version, and ansible-inventory lists `listed` and writes
    `stderr`."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    interpreter = sys.executable  # captured now; make() replaces sys.executable

    def make(stderr: str = "", listed: dict = LISTED) -> None:
        playbook = bin_dir / "ansible-playbook"
        playbook.write_text(f"#!{interpreter}\nprint('ansible-playbook [core 2.21.3]')\n")
        inventory = bin_dir / "ansible-inventory"
        inventory.write_text(
            f"#!{interpreter}\nimport sys\n"
            f"sys.stdout.write({json.dumps(listed)!r})\n"
            f"sys.stderr.write({stderr!r})\n"
        )
        for exe in (playbook, inventory):
            exe.chmod(0o755)
        monkeypatch.setattr(sys, "executable", str(bin_dir / "python"))

    return make


def report(repo: Path, *args: str):
    return CliRunner().invoke(app, ["avd", "report", str(repo), *args])


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
    ansible()
    result = report(repo, "--playbook", "build.yml")

    assert result.exit_code == 0, result.output
    assert section(result.output, "Carried") == [
        "Fabric single-dc-l3ls, from play 0",
        "FabricInputs 1",
    ]


def test_another_play_and_the_vault_password_are_under_not_carried(repo, ansible):
    write(
        repo, {"ansible.cfg": "[defaults]\ninventory=inventory.yml\nvault_password_file=.vault\n"}
    )

    ansible()
    result = report(repo, "--playbook", "build.yml")

    assert section(result.output, "Not carried") == [
        "play [1] Build the twin a separate run: --play 1 --name NAME",
        ".vault the vault password; create its Secret:",
        f"kubectl create secret generic single-dc-l3ls-vault --from-file=password={repo / '.vault'}",
    ]


def test_a_vars_file_that_did_not_read_is_not_carried(repo, ansible):
    write(repo, {"group_vars/FABRIC.yml": "$ANSIBLE_VAULT;1.1;AES256\n6162\n"})

    ansible()
    result = report(repo, "--playbook", "build.yml")

    assert "group_vars/FABRIC.yml is str, not a mapping" in section(result.output, "Not carried")


def test_a_host_vars_file_for_no_host_is_a_warning(repo, ansible):
    write(repo, {"host_vars/all.yml": "root_dir: '{{ playbook_dir }}'\n"})

    ansible()
    result = report(repo, "--playbook", "build.yml")

    assert "host_vars/all.yml all is no host in the inventory" in section(result.output, "Warnings")


def test_a_plain_text_password_is_a_warning(repo, ansible):
    write(repo, {"group_vars/FABRIC.yml": "fabric_name: FABRIC\nansible_password: arista\n"})

    ansible()
    result = report(repo, "--playbook", "build.yml")

    assert (
        "ansible_password plain text in FabricInput single-dc-l3ls-fabric, "
        "carried as the repository has it"
    ) in section(result.output, "Warnings")


def test_without_a_playbook_the_candidates_are_named_and_nothing_is_carried(repo, ansible):
    ansible()
    result = report(repo)

    assert result.exit_code == 2
    assert "Playbook not named name one with --playbook: build.yml" in squeezed(
        result.output.splitlines()
    )
    assert section(result.output, "Carried") == []


def test_another_play_has_a_report_of_its_own(repo, ansible):
    ansible()
    result = report(repo, "--playbook", "build.yml", "--play", "1", "--name", "the-twin")

    assert result.exit_code == 0, result.output
    assert section(result.output, "Carried")[0] == "Fabric the-twin, from play 1"
    assert section(result.output, "Not carried") == [
        "play [0] Build a separate run: --play 0 --name NAME"
    ]


def test_a_play_pulling_in_no_role_is_named_without_advice(repo, ansible):
    # twodc keeps a `meta: clear_facts` play between its two fabrics, and carrying that
    # one as a fabric of its own is advice with nothing behind it.
    write(repo, {"build.yml": BUILD + "- name: Clear facts\n  hosts: all\n  tasks: []\n"})

    ansible()
    result = report(repo, "--playbook", "build.yml")

    assert "play [2] Clear facts pulls in no role" in section(result.output, "Not carried")
    assert "--play 2" not in result.output


def test_the_renders_row_carries_the_play_it_reported(repo, ansible):
    ansible()
    result = report(repo, "--playbook", "build.yml", "--play", "1")

    assert f"Renders not measured netadopt avd compare {repo} --playbook build.yml --play 1" in (
        squeezed(result.output.splitlines())
    )


def test_a_name_emit_would_refuse_is_not_carried_and_fails_the_report(repo, ansible):
    ansible()
    result = report(repo, "--playbook", "build.yml", "--name", "a" * 70)

    assert result.exit_code == 2
    assert section(result.output, "Not carried")[0] == (
        f"{'a' * 70} 70 characters, and a label value holds 63 -- "
        "emit refuses it, pass a shorter --name"
    )


def test_a_name_nothing_can_be_spelled_from_is_not_carried(repo, ansible):
    ansible()
    result = report(repo, "--playbook", "build.yml", "--name", "///")

    assert result.exit_code == 2
    assert section(result.output, "Not carried")[0] == (
        "/// no name can be spelled from it -- emit refuses it, pass --name"
    )


def test_whether_the_design_renders_is_not_measured_and_the_row_says_what_measures_it(
    repo, ansible
):
    ansible()
    result = report(repo, "--playbook", "build.yml", "--inventory", "inventory/")

    assert (
        f"Renders not measured netadopt avd compare {repo} --playbook build.yml "
        "--inventory inventory/" in squeezed(result.output.splitlines())
    )


def test_the_renders_row_names_no_playbook_it_was_not_given(repo, ansible):
    ansible()
    result = report(repo)

    assert f"Renders not measured netadopt avd compare {repo}" in squeezed(
        result.output.splitlines()
    )


def test_a_code_directory_is_a_warning_is_not_carried_and_fails_the_report(repo, ansible):
    write(repo, {"ansible.cfg": "[defaults]\ninventory=inventory.yml\nvars_plugins=plugins/vars\n"})

    ansible()
    result = report(repo, "--playbook", "build.yml")

    assert "plugins/vars code Ansible loads, named by vars_plugins in ansible.cfg" in section(
        result.output, "Warnings"
    )
    assert (
        "plugins/vars named by vars_plugins in ansible.cfg; missing from the rebuilt repository"
        in section(result.output, "Not carried")
    )
    assert result.exit_code == 2


@pytest.mark.parametrize(
    ("callback", "warned"),
    [("yaml", True), ("community.general.yaml", True), ("default", False)],
)
def test_a_stdout_callback_other_than_the_default_is_a_warning_and_fails_nothing(
    repo, ansible, callback, warned
):
    write(
        repo, {"ansible.cfg": f"[defaults]\ninventory=inventory.yml\nstdout_callback={callback}\n"}
    )

    ansible()
    result = report(repo, "--playbook", "build.yml")

    row = (
        f"{callback} the callback Ansible prints with, named by stdout_callback in ansible.cfg; "
        "lab and the cluster print with default"
    )
    assert (row in section(result.output, "Warnings")) is warned
    assert result.exit_code == 0, result.output


@pytest.mark.parametrize(
    ("task_at", "row"),
    [
        (0, 'play [0] more than eos_designs: task "Import global vars"'),
        (1, None),
    ],
    ids=["before-the-role", "after-the-role"],
)
def test_a_task_before_eos_designs_is_not_carried_and_one_after_it_is_not_named(
    repo, ansible, task_at, row
):
    tasks = [
        "    - ansible.builtin.import_role:\n        name: arista.avd.eos_designs\n",
    ]
    tasks.insert(
        task_at, "    - name: Import global vars\n      ansible.builtin.include_vars: global.yml\n"
    )
    write(repo, {"build.yml": "- hosts: FABRIC\n  tasks:\n" + "".join(tasks)})

    ansible()
    result = report(repo, "--playbook", "build.yml")

    rows = [line for line in section(result.output, "Not carried") if line.startswith("play [0]")]
    assert rows == ([row] if row else [])
    assert result.exit_code == (2 if row else 0), result.output


@pytest.mark.parametrize(
    ("hosts", "row"),
    [
        ("AVD_FABRIC", "play [0] hosts 'AVD_FABRIC' is no group or host of the inventory"),
        ("FABRIC", None),
        ("dc1-spine1", None),
        ("FABRIC:!DC2", None),
        ("localhost", None),
    ],
    ids=["no-such-group", "a-group", "a-host", "a-pattern-is-ansibles", "implicit-localhost"],
)
def test_hosts_naming_nothing_in_the_inventory_is_not_carried(repo, ansible, hosts, row):
    write(
        repo,
        {
            "build.yml": f"""
            - hosts: "{hosts}"
              tasks:
                - ansible.builtin.import_role:
                    name: arista.avd.eos_designs
            """
        },
    )

    ansible()
    result = report(repo, "--playbook", "build.yml")

    rows = [line for line in section(result.output, "Not carried") if line.startswith("play [0]")]
    assert rows == ([row] if row else [])
    assert result.exit_code == (2 if row else 0), result.output


def test_both_findings_of_one_play_share_its_row(repo, ansible):
    write(
        repo,
        {
            "build.yml": """
            - hosts: AVD_FABRIC
              tasks:
                - name: Import global vars
                  ansible.builtin.include_vars: global.yml
                - ansible.builtin.import_role:
                    name: arista.avd.eos_designs
            """
        },
    )

    ansible()
    result = report(repo, "--playbook", "build.yml")

    assert (
        'play [0] more than eos_designs: task "Import global vars"; '
        "hosts 'AVD_FABRIC' is no group or host of the inventory"
    ) in section(result.output, "Not carried")


def test_a_play_pulling_in_no_eos_designs_is_not_carried(repo, ansible):
    write(
        repo,
        {
            "build.yml": """
            - hosts: localhost
              tasks:
                - name: Generate host_vars
                  ansible.builtin.copy: {dest: host_vars/x.yml, content: ""}
            - hosts: FABRIC
              tasks:
                - ansible.builtin.import_role:
                    name: arista.avd.eos_designs
            """
        },
    )

    ansible()
    result = report(repo, "--playbook", "build.yml")

    assert "play [0] more than eos_designs: no eos_designs" in section(result.output, "Not carried")
    assert result.exit_code == 2


def test_a_collections_path_is_a_warning_and_fails_nothing(repo, ansible):
    write(
        repo,
        {"ansible.cfg": "[defaults]\ninventory=inventory.yml\ncollections_paths=../ansible-avd\n"},
    )

    ansible()
    result = report(repo, "--playbook", "build.yml")

    assert (
        "../ansible-avd code Ansible loads, named by collections_paths in ansible.cfg; "
        "lab and the cluster render with the collections netadopt pins"
    ) in section(result.output, "Warnings")
    assert section(result.output, "Not carried") == [
        "play [1] Build the twin a separate run: --play 1 --name NAME"
    ]
    assert result.exit_code == 0, result.output


def test_a_key_avd_removed_is_not_carried_and_fails_the_report(repo, ansible):
    write(repo, {"group_vars/FABRIC.yml": "fabric_name: FABRIC\nname_servers: [192.168.1.1]\n"})

    ansible()
    result = report(repo, "--playbook", "build.yml")

    assert (
        "group_vars/FABRIC.yml The input data model 'name_servers' was removed in AVD "
        "version 6.0.0. Use 'dns_settings.servers' instead."
    ) in section(result.output, "Not carried")
    assert result.exit_code == 2


def test_a_play_without_eos_designs_is_not_judged_by_its_schema(repo, ansible):
    write(
        repo,
        {
            "group_vars/FABRIC.yml": "local_users: [{name: admin}]\n",
            "build.yml": """
            - hosts: FABRIC
              tasks:
                - ansible.builtin.import_role:
                    name: arista.avd.eos_cli_config_gen
            """,
        },
    )

    ansible()
    result = report(repo, "--playbook", "build.yml")

    assert "was removed in AVD version" not in result.output


# rsp-demo's shape: a host another play renders with eos_cli_config_gen alone, whose
# vars hold a key that model keeps.
OUTSIDE = {**LISTED, "all": {"children": ["FABRIC", "CGNAT"]}, "CGNAT": {"hosts": ["cgnat1"]}}


def test_a_removed_key_in_the_vars_of_a_host_outside_the_play_is_not_judged(repo, ansible):
    write(
        repo,
        {
            "host_vars/cgnat1.yml": "local_users: [{name: admin}]\n",
            "group_vars/CGNAT.yml": "name_servers: [192.168.1.1]\n",
        },
    )

    ansible(listed=OUTSIDE)
    result = report(repo, "--playbook", "build.yml")

    assert "was removed in AVD version" not in result.output
    assert result.exit_code == 0, result.output


@pytest.mark.parametrize(
    "path", ["host_vars/dc1-spine1.yml", "group_vars/all.yml"], ids=["its-host", "all"]
)
def test_a_removed_key_in_vars_reaching_the_plays_hosts_is_judged(repo, ansible, path):
    write(repo, {path: "name_servers: [192.168.1.1]\n"})

    ansible(listed=OUTSIDE)
    result = report(repo, "--playbook", "build.yml")

    assert any(
        line.startswith(f"{path} The input data model 'name_servers' was removed")
        for line in section(result.output, "Not carried")
    )
    assert result.exit_code == 2


def test_a_file_lookup_is_not_carried_and_fails_the_report(repo, ansible):
    # rsp-ref-arch's shape: the copy holds the file, repo' does not.
    write(
        repo,
        {
            "group_vars/FABRIC.yml": """
            fabric_name: FABRIC
            route_maps: "{{ lookup('file', 'mpls_evpn.rcf') }}"
            """,
            "mpls_evpn.rcf": "router general\n",
        },
    )

    ansible()
    result = report(repo, "--playbook", "build.yml")

    assert (
        "group_vars/FABRIC.yml lookup('file', 'mpls_evpn.rcf'): the file it reads is not carried"
    ) in section(result.output, "Not carried")
    assert result.exit_code == 2


def test_a_part_of_the_model_not_carried_fails_the_report(repo, ansible):
    write(repo, {"group_vars/FABRIC.yml": "$ANSIBLE_VAULT;1.1;AES256\n6162\n"})

    ansible()
    result = report(repo, "--playbook", "build.yml")

    assert result.exit_code == 2


def test_with_no_ansible_beside_it_only_that_is_said(repo, tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "executable", str(tmp_path / "env" / "python"))

    result = report(repo, "--playbook", "build.yml")

    assert result.exit_code == 1
    assert result.stdout == ""
    assert "no Ansible: " in result.stderr
    assert 'install as: uvx "netadopt[avd]"' in result.stderr


def test_what_ansible_writes_in_brackets_is_printed_as_written(repo, ansible):
    warning = "[WARNING]: Found both group and host with same name: all"

    ansible(stderr=warning + "\n")
    result = report(repo, "--playbook", "build.yml")

    assert warning in result.output
