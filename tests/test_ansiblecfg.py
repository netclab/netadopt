"""ansible.cfg as the repository writes it."""

from __future__ import annotations

import textwrap
from pathlib import Path

from netadopt.ansiblecfg import read_ansible_cfg


def write(repo: Path, body: str) -> None:
    (repo / "ansible.cfg").write_text(textwrap.dedent(body).lstrip())


def test_every_section_arrives_with_its_values_as_written(tmp_path: Path):
    # AVD's vars_global_vars integration target, whole.
    write(
        tmp_path,
        """
        [defaults]
        inventory =./hosts.yml
        gathering = explicit
        vars_plugins_enabled = arista.avd.global_vars, host_group_vars

        [vars_global_vars]
        paths = ./global_vars.yml,./zz_global_vars
        """,
    )

    found = read_ansible_cfg(tmp_path)

    assert found.usable
    assert found.path == tmp_path / "ansible.cfg"
    assert found.sections == {
        "defaults": {
            "inventory": "./hosts.yml",
            "gathering": "explicit",
            "vars_plugins_enabled": "arista.avd.global_vars, host_group_vars",
        },
        "vars_global_vars": {"paths": "./global_vars.yml,./zz_global_vars"},
    }


def test_a_semicolon_after_a_value_is_a_comment_and_a_hash_is_not(tmp_path: Path):
    write(
        tmp_path,
        """
        [defaults]
        # a whole line is a comment either way
        inventory = inventory.yml ; the fabric
        vault_password_file = .vault # kept out of git
        """,
    )

    defaults = read_ansible_cfg(tmp_path).sections["defaults"]

    assert defaults == {
        "inventory": "inventory.yml",
        "vault_password_file": ".vault # kept out of git",
    }


def test_a_percent_sign_is_left_for_ansible(tmp_path: Path):
    write(tmp_path, "[defaults]\nlocal_tmp = %(here)s/.ansible/tmp\n")

    defaults = read_ansible_cfg(tmp_path).sections["defaults"]

    assert defaults == {"local_tmp": "%(here)s/.ansible/tmp"}


def test_default_stays_a_section_of_its_own(tmp_path: Path):
    write(tmp_path, "[DEFAULT]\nforks = 5\n\n[defaults]\ninventory = inventory.yml\n")

    found = read_ansible_cfg(tmp_path)

    assert found.sections == {
        "DEFAULT": {"forks": "5"},
        "defaults": {"inventory": "inventory.yml"},
    }


def test_keys_are_folded_to_lower_case_as_ansible_folds_them(tmp_path: Path):
    write(tmp_path, "[defaults]\nInventory = inventory.yml\n")

    assert read_ansible_cfg(tmp_path).sections == {"defaults": {"inventory": "inventory.yml"}}


def test_the_inventory_it_names_is_split_on_commas_as_ansible_splits_it(tmp_path: Path):
    write(tmp_path, "[defaults]\ninventory = inventory.yml, extra/ \n")

    assert read_ansible_cfg(tmp_path).inventory == ("inventory.yml", "extra/")


def test_no_inventory_named_is_none(tmp_path: Path):
    write(tmp_path, "[defaults]\ngathering = explicit\n")

    assert read_ansible_cfg(tmp_path).inventory == ()


def test_no_file_is_no_problem(tmp_path: Path):
    found = read_ansible_cfg(tmp_path)

    assert found.usable
    assert found.path is None
    assert found.sections == {}


def test_a_key_written_twice_is_a_problem_naming_the_file(tmp_path: Path):
    write(tmp_path, "[defaults]\ninventory = a.yml\ninventory = b.yml\n")

    found = read_ansible_cfg(tmp_path)

    assert not found.usable
    assert "ansible.cfg" in found.problem and "inventory" in found.problem


def test_a_line_before_any_section_is_a_problem(tmp_path: Path):
    write(tmp_path, "inventory = inventory.yml\n")

    found = read_ansible_cfg(tmp_path)

    assert not found.usable
    assert "ansible.cfg" in found.problem


def test_a_file_that_is_not_utf8_is_a_problem(tmp_path: Path):
    (tmp_path / "ansible.cfg").write_bytes(b"[defaults]\ninventory = \xff\n")

    found = read_ansible_cfg(tmp_path)

    assert not found.usable
    assert "UTF-8" in found.problem
