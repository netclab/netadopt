"""Code in the repository that Ansible runs, found from the files alone."""

from __future__ import annotations

from pathlib import Path

from netadopt.ansiblecfg import AnsibleCfg
from netadopt.repocode import Code, find_code


def executable(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    path.chmod(0o755)


def test_an_executable_inventory_is_found(tmp_path: Path):
    executable(tmp_path / "inventory.py", "#!/usr/bin/env python3\nprint('{}')\n")

    assert find_code(tmp_path, AnsibleCfg(), ("inventory.py",)) == (Code("inventory.py"),)


def test_a_yaml_inventory_with_the_executable_bit_set_is_not(tmp_path: Path):
    executable(tmp_path / "inventory.yml", "all: {}\n")

    assert find_code(tmp_path, AnsibleCfg(), ("inventory.yml",)) == ()


def test_every_file_of_an_inventory_directory_is_looked_at(tmp_path: Path):
    executable(tmp_path / "inventory" / "netbox.py", "#!/bin/sh\n")
    (tmp_path / "inventory" / "hosts.yml").write_text("all: {}\n")

    assert find_code(tmp_path, AnsibleCfg(), ("inventory/",)) == (Code("inventory/netbox.py"),)


def test_a_code_directory_in_the_repository_is_found_and_one_elsewhere_is_not(tmp_path: Path):
    config = AnsibleCfg(
        sections={
            "defaults": {
                "vars_plugins": "plugins/vars:/usr/share/ansible/plugins/vars",
                "library": "~/library",
            }
        }
    )

    assert find_code(tmp_path, config, ()) == (Code("plugins/vars", "vars_plugins"),)
