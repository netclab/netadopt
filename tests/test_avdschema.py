"""Keys AVD removed, found by pyavd in each vars file as written."""

from __future__ import annotations

from pathlib import Path

import pytest

from netadopt.avdschema import removed_keys
from netadopt.varfiles import GROUP_VARS, INVENTORY_ROOT, VarFile, VarFiles

VAULT = {"__ansible_vault": "$ANSIBLE_VAULT;1.1;AES256\n6162"}


def files(*data: dict | None) -> VarFiles:
    return VarFiles(
        files=tuple(
            VarFile(Path(f"group_vars/G{n}.yml"), f"G{n}", GROUP_VARS, INVENTORY_ROOT, one)
            for n, one in enumerate(data)
        )
    )


SAID = (
    "The input data model 'name_servers' was removed in AVD version 6.0.0. "
    "Use 'dns_settings.servers' instead."
)


def test_a_removed_key_is_named_with_what_pyavd_says_of_it():
    found = removed_keys(files({"fabric_name": "FABRIC"}, {"name_servers": ["192.168.1.1"]}))

    assert found == ((Path("group_vars/G1.yml"), SAID),)


@pytest.mark.parametrize(
    "data",
    [
        {"dns_settings": {"servers": [{"ip_address": "192.168.1.1"}]}},
        {"type": "l3leaf"},
        {"mgmt_gateway": "{{ gateway }}"},
        {"bgp_peer_groups": {"evpn_overlay_peers": {"password": VAULT}}},
        None,
    ],
    ids=[
        "its-replacement",
        "one-part-of-the-inputs",
        "jinja-unrendered",
        "a-vault-value",
        "no-data",
    ],
)
def test_nothing_else_pyavd_finds_in_one_file_is_named(data):
    assert removed_keys(files(data)) == ()
