"""What a playbook says, and what we make of it.

The fixtures here are written out rather than taken from AVD: these tests pin our
behaviour, and must not move when somebody else releases.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
import yaml

from netadopt.playbook import (
    IMPORT_ROLE,
    INCLUDE_ROLE,
    ROLES_KEYWORD,
    find_playbooks,
    read_playbook,
)


@pytest.fixture
def repo(tmp_path: Path):
    def write(name: str, body: str) -> Path:
        path = tmp_path / name
        path.write_text(textwrap.dedent(body).lstrip())
        return path

    write.dir = tmp_path  # type: ignore[attr-defined]
    return write


def test_plays_are_listed_in_file_order_including_ones_that_pull_in_nothing(repo):
    # The shape of AVD's own converge.yml: two fabric plays with a bookkeeping play
    # between them. The middle one is reported, not silently dropped -- otherwise
    # play [2] would be renumbered and stop matching the file he wrote.
    repo(
        "converge.yml",
        """
        - name: Converge
          hosts: TWODC
          tasks:
            - ansible.builtin.import_role:
                name: arista.avd.eos_designs
        - name: Clear facts between plays
          hosts: all
          tasks:
            - ansible.builtin.meta: clear_facts
        - name: Converge
          hosts: TWODC
          vars:
            avd_digital_twin_mode: true
          tasks:
            - ansible.builtin.import_role:
                name: arista.avd.eos_designs
        """,
    )

    read = read_playbook(repo.dir, "converge.yml")

    assert read.usable
    assert [play.index for play in read.plays] == [0, 1, 2]
    assert [play.name for play in read.plays] == ["Converge", "Clear facts between plays", "Converge"]
    assert read.plays[1].roles == ()
    assert read.plays[2].var_names == ("avd_digital_twin_mode",)


def test_two_plays_can_share_a_name_and_a_host_pattern(repo):
    # Which is why a play is addressed by index and never by name: in AVD's own
    # scenario both fabric plays are called "Converge" over the same hosts.
    repo(
        "converge.yml",
        """
        - name: Converge
          hosts: TWODC
        - name: Converge
          hosts: TWODC
        """,
    )

    plays = read_playbook(repo.dir, "converge.yml").plays

    assert plays[0].name == plays[1].name
    assert plays[0].hosts == plays[1].hosts
    assert plays[0].index != plays[1].index


def test_where_a_role_comes_from_is_reported_not_flattened(repo):
    # roles: runs before tasks and its parameters outrank host vars; import_role is
    # static; include_role is resolved as the play runs. Three precedences.
    repo(
        "mixed.yml",
        """
        - name: Mixed
          hosts: all
          roles:
            - plain.role
            - role: parametrised.role
              some_param: 1
          tasks:
            - import_role:
                name: short.spelling
            - ansible.builtin.import_role:
                name: fqcn.spelling
            - ansible.builtin.include_role:
                name: dynamic.role
        """,
    )

    roles = read_playbook(repo.dir, "mixed.yml").plays[0].roles

    # Literals, not the constants: comparing a label against the constant it came
    # from still passes when two labels are given the same value, which is the one
    # failure that matters here.
    assert [(role.name, role.how) for role in roles] == [
        ("plain.role", "roles-keyword"),
        ("parametrised.role", "roles-keyword"),
        ("short.spelling", "import_role"),
        ("fqcn.spelling", "import_role"),
        ("dynamic.role", "include_role"),
    ]
    assert len({ROLES_KEYWORD, IMPORT_ROLE, INCLUDE_ROLE}) == 3


def test_a_role_inside_a_block_is_still_found(repo):
    # Reporting a play as roleless because the reference was nested would be worse
    # than reporting one too many.
    repo(
        "blocked.yml",
        """
        - name: Blocked
          hosts: all
          tasks:
            - block:
                - ansible.builtin.import_role:
                    name: in.block
              rescue:
                - ansible.builtin.import_role:
                    name: in.rescue
          handlers:
            - ansible.builtin.import_role:
                name: in.handler
        """,
    )

    play = read_playbook(repo.dir, "blocked.yml").plays[0]

    assert [role.name for role in play.roles] == ["in.block", "in.rescue", "in.handler"]
    assert play.task_count == 3


def test_hosts_is_carried_as_written(repo):
    # A pattern and a list are both legal, and neither is normalised: Ansible
    # resolves them, we do not.
    repo(
        "hosts.yml",
        """
        - hosts: FABRIC:!DC2
        - hosts: [DC1, DC2]
        """,
    )

    plays = read_playbook(repo.dir, "hosts.yml").plays

    assert plays[0].hosts == "FABRIC:!DC2"
    assert plays[1].hosts == ["DC1", "DC2"]
    assert plays[0].name is None  # a play need not be named


def test_raw_is_the_play_verbatim(repo):
    # raw is what Fabric.spec.play carries, so it must survive being written back
    # out unchanged -- keys we never look at included.
    body = """
        - name: Converge
          hosts: TWODC
          gather_facts: false
          connection: local
          strategy: linear
          vars:
            output_dir_name: digital_twin/intended
          tasks:
            - name: Generate
              delegate_to: 127.0.0.1
              ansible.builtin.import_role:
                name: arista.avd.eos_designs
        """
    path = repo("converge.yml", body)

    play = read_playbook(repo.dir, "converge.yml").plays[0]

    assert play.raw == yaml.safe_load(path.read_text())[0]
    assert yaml.safe_load(yaml.safe_dump(play.raw)) == play.raw


def test_a_missing_playbook_names_the_path_it_looked_for(repo):
    read = read_playbook(repo.dir, "nope.yml")

    assert not read.usable
    assert str(repo.dir / "nope.yml") in read.problem


def test_a_mapping_at_the_top_level_is_not_a_playbook(repo):
    # inventory.yml and molecule.yml are the ones this keeps out.
    repo("inventory.yml", "all:\n  children:\n    FABRIC:\n")

    read = read_playbook(repo.dir, "inventory.yml")

    assert not read.usable
    assert "not a playbook" in read.problem


def test_an_empty_file_says_so_rather_than_reporting_no_plays(repo):
    repo("empty.yml", "")

    read = read_playbook(repo.dir, "empty.yml")

    assert not read.usable
    assert "empty" in read.problem


def test_broken_yaml_is_a_problem_and_not_an_exception(repo):
    repo("broken.yml", "- name: x\n  hosts: [unclosed\n")

    read = read_playbook(repo.dir, "broken.yml")

    assert not read.usable
    assert "not readable as YAML" in read.problem


def test_candidates_are_the_files_that_read_as_playbooks(repo):
    repo("build.yml", "- hosts: FABRIC\n")
    repo("deploy.yaml", "- hosts: FABRIC\n- hosts: DC1\n")
    repo("inventory.yml", "all:\n  children:\n    FABRIC:\n")
    repo("notes.txt", "- hosts: FABRIC\n")

    candidates = find_playbooks(repo.dir)

    assert [pb.path.name for pb in candidates] == ["build.yml", "deploy.yaml"]
    assert [len(pb.plays) for pb in candidates] == [1, 2]
