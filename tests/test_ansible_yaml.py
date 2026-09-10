"""Reading and writing YAML the way Ansible does."""

from __future__ import annotations

from netadopt import ansible_yaml
from netadopt.ansible_yaml import VAULT_KEY

TAGGED = """\
password: !vault |
  $ANSIBLE_VAULT;1.1;AES256
  6162636465
motd: !unsafe '{{ not a template }}'
"""


def test_vault_and_unsafe_are_written_back_as_their_tags():
    loaded = ansible_yaml.load(TAGGED)

    written = ansible_yaml.dump(loaded)

    assert "password: !vault |" in written
    assert "motd: !unsafe" in written
    assert ansible_yaml.load(written) == loaded


def test_a_mapping_with_more_keys_is_an_ordinary_mapping():
    data = {VAULT_KEY: "x", "other": 1}

    written = ansible_yaml.dump(data)

    assert "!vault" not in written
    assert ansible_yaml.load(written) == data


def test_keys_keep_their_order():
    assert ansible_yaml.dump({"b": 1, "a": 2}) == "b: 1\na: 2\n"


def test_an_object_met_twice_is_written_twice_and_not_as_an_alias():
    shared = {"x": 1}

    written = ansible_yaml.dump({"a": shared, "b": shared})

    assert "&" not in written and "*" not in written


def test_a_long_string_is_not_folded():
    long = "word " * 40

    assert ansible_yaml.dump({"a": long.strip()}).count("\n") == 1
