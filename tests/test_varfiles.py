"""Which vars files Ansible would read, and what we make of them.

Written-out fixtures, not AVD's repositories. The corpus holds one `!vault` and one
`!unsafe` in 1331 files, and none at all of the cases decided here -- a file
encrypted whole, a vars file that is not a mapping, group_vars under both roots at
once. Variety that arrives by luck cannot pin behaviour.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from netadopt.ansible_yaml import UNSAFE_KEY, VAULT_KEY
from netadopt.varfiles import (
    GROUP_VARS,
    HOST_VARS,
    INVENTORY_ROOT,
    PLAYBOOK_ROOT,
    read_vars,
)


@pytest.fixture
def repo(tmp_path: Path):
    def write(name: str, body: str = "") -> Path:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(body).lstrip())
        return path

    write.dir = tmp_path  # type: ignore[attr-defined]
    return write


def test_a_directory_of_files_is_one_scope_not_several(repo):
    # AVD's single-dc-l3ls writes group_vars/FABRIC/ as two files. Ansible merges
    # them into one namespace and one FabricInput is per GROUP, so both files report
    # the same scope -- while staying separate here, because merging is not ours.
    repo("group_vars/FABRIC/connectivity.yml", "ansible_user: arista\n")
    repo("group_vars/FABRIC/fabric.yml", "fabric_name: FABRIC\n")

    found = read_vars(repo.dir)

    assert found.scopes(GROUP_VARS) == ("FABRIC",)
    assert {file.path.name for file in found.files} == {"connectivity.yml", "fabric.yml"}


def test_both_roots_are_read_and_stay_apart(repo):
    # Ansible consults group_vars beside the inventory AND beside the playbook, at
    # different precedences. Merging them, or picking one, would be us writing down
    # a precedence we promised not to write down.
    repo("inventory/hosts.yml", "all:\n  children:\n    FABRIC:\n")
    repo("inventory/group_vars/FABRIC.yml", "fabric_name: FROM_INVENTORY\n")
    repo("group_vars/FABRIC.yml", "fabric_name: FROM_PLAYBOOK\n")

    found = read_vars(repo.dir, inventory="inventory/", playbook="build.yml")

    assert [(file.root, file.data["fabric_name"]) for file in found.files] == [
        (INVENTORY_ROOT, "FROM_INVENTORY"),
        (PLAYBOOK_ROOT, "FROM_PLAYBOOK"),
    ]


def test_one_root_when_the_inventory_sits_beside_the_playbook(repo):
    # single-dc-l3ls: inventory.yml at the top of the repo. The same directory must
    # not be read twice and reported as two files.
    repo("inventory.yml", "all:\n  children:\n    FABRIC:\n")
    repo("group_vars/FABRIC.yml", "fabric_name: FABRIC\n")

    found = read_vars(repo.dir, inventory="inventory.yml", playbook="build.yml")

    assert len(found.roots) == 1
    assert len(found.files) == 1


def test_hosts_and_groups_are_kept_apart(repo):
    repo("group_vars/FABRIC.yml", "fabric_name: FABRIC\n")
    repo("host_vars/dc1-leaf1a.yml", "type: l3leaf\n")

    found = read_vars(repo.dir)

    assert found.scopes(GROUP_VARS) == ("FABRIC",)
    assert found.scopes(HOST_VARS) == ("dc1-leaf1a",)


def test_a_hostname_with_dots_keeps_them(repo):
    # AVD's twodc scenario really has DC1.POD1.LEAF2A. Cutting the name at the first
    # dot would silently address a different host.
    repo("host_vars/DC1.POD1.LEAF2A.yml", "use_cv_topology: true\n")

    found = read_vars(repo.dir)

    assert found.scopes(HOST_VARS) == ("DC1.POD1.LEAF2A",)


def test_vault_and_unsafe_survive_in_ansibles_own_shape(repo):
    # The shapes `ansible-inventory --list` prints, so the reader that runs Ansible
    # and the reader that opens the file say the same thing.
    repo(
        "group_vars/WAN.yml",
        """
        bgp_password: !vault |
          $ANSIBLE_VAULT;1.1;AES256
          62313365396662
        prompt: !unsafe "%H__%D{%H:%M:%S}"
        """,
    )

    data = read_vars(repo.dir).files[0].data

    assert data["bgp_password"][VAULT_KEY].startswith("$ANSIBLE_VAULT;1.1;AES256")
    assert data["prompt"][UNSAFE_KEY] == "%H__%D{%H:%M:%S}"


def test_an_unknown_tag_is_a_problem_naming_the_tag_and_the_file(repo):
    # An unknown tag is something we have not understood yet, and understanding it
    # later is cheap. A value silently changed in transit is not detectable at all.
    repo("group_vars/FABRIC.yml", "thing: !surprise value\n")

    file = read_vars(repo.dir).files[0]

    assert not file.usable
    assert "!surprise" in file.problem
    assert "FABRIC.yml" in file.problem


def test_a_file_encrypted_whole_is_reported_against_that_file_only(repo):
    # Its first line is the vault header, so it is not YAML at all. Every other file
    # in the repository is still read.
    repo("group_vars/SECRET.yml", "$ANSIBLE_VAULT;1.1;AES256\n62313365396662\n")
    repo("group_vars/FABRIC.yml", "fabric_name: FABRIC\n")

    files = {file.scope: file for file in read_vars(repo.dir).files}

    assert not files["SECRET"].usable
    assert files["FABRIC"].data == {"fabric_name": "FABRIC"}


def test_an_empty_file_sets_nothing_and_is_not_a_problem(repo):
    repo("group_vars/EMPTY.yml", "")

    file = read_vars(repo.dir).files[0]

    assert file.usable
    assert file.data == {}


def test_a_vars_file_that_is_not_a_mapping_is_a_problem(repo):
    repo("group_vars/LIST.yml", "- one\n- two\n")

    file = read_vars(repo.dir).files[0]

    assert not file.usable
    assert "not a mapping" in file.problem


def test_leftovers_are_passed_over_the_way_ansible_passes_over_them(repo):
    repo("group_vars/FABRIC.yml", "fabric_name: FABRIC\n")
    repo("group_vars/FABRIC.yml.bak", "fabric_name: STALE\n")
    repo("group_vars/.hidden.yml", "fabric_name: HIDDEN\n")
    repo("group_vars/notes.txt", "fabric_name: NOTES\n")

    found = read_vars(repo.dir)

    assert [file.path.name for file in found.files] == ["FABRIC.yml"]


def test_a_file_with_no_suffix_is_a_vars_file(repo):
    # Ansible accepts an extensionless file, and a repository that uses them would
    # otherwise be reported as having no variables at all.
    repo("group_vars/FABRIC", "fabric_name: FABRIC\n")

    found = read_vars(repo.dir)

    assert found.scopes(GROUP_VARS) == ("FABRIC",)


def test_nothing_at_all_is_an_empty_answer_and_not_a_failure(repo):
    repo("build.yml", "- hosts: all\n")

    found = read_vars(repo.dir)

    assert found.files == ()
    assert found.problems == ()
