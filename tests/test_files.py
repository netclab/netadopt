"""Files the vars read through Ansible's file lookup, which nothing carries."""

from __future__ import annotations

from pathlib import Path

import pytest

from netadopt.files import INVENTORY, PLAY_VARS, file_lookups
from netadopt.varfiles import GROUP_VARS, INVENTORY_ROOT, VarFile, VarFiles

FABRIC = Path("group_vars/FABRIC.yml")


def found(data: dict | None) -> VarFiles:
    return VarFiles(files=(VarFile(FABRIC, "FABRIC", GROUP_VARS, INVENTORY_ROOT, data),))


@pytest.mark.parametrize(
    "value",
    [
        "{{ lookup('file', 'mpls_evpn.rcf') }}",
        '{{ lookup("file", "mpls_evpn.rcf") }}',
        "{{ lookup('ansible.builtin.file', 'mpls_evpn.rcf') }}",
        "{{ query('file', 'mpls_evpn.rcf') | first }}",
        "{{ q('file', 'mpls_evpn.rcf') | first }}",
        "{{ lookup('file', rcf_name) }}",
    ],
    ids=["single", "double", "fqcn", "query", "q", "by-a-var"],
)
def test_each_spelling_of_the_file_lookup_is_found(value):
    [(where, call)] = file_lookups(found({"route_maps": value}), {}, {})

    assert where == FABRIC
    assert value.startswith("{{ " + call)


def test_a_lookup_deep_in_a_list_or_on_a_second_line_is_found():
    data = {"tenants": [{"vrfs": [{"raw": "first line\n{{ lookup('file', 'x.cfg') }}\n"}]}]}

    assert file_lookups(found(data), {}, {}) == ((FABRIC, "lookup('file', 'x.cfg')"),)


def test_the_play_and_the_inventory_are_places_too():
    play = {"vars": {"banner": "{{ lookup('file', 'motd.txt') }}"}}
    groups = {"all": {"vars": {"key": "{{ lookup('file', 'key.pem') }}"}}}

    assert file_lookups(found(None), play, groups) == (
        (PLAY_VARS, "lookup('file', 'motd.txt')"),
        (INVENTORY, "lookup('file', 'key.pem')"),
    )


@pytest.mark.parametrize(
    "value",
    [
        "{{ lookup('env', 'HOME') }}",
        "{{ lookup('template', 'x.j2') }}",
        "{{ lookup('fileglob', '*.cfg') }}",
        "mpls_evpn.rcf",
        "a file lookup is lookup('file', ...)",
    ],
    ids=["env", "template", "fileglob", "a-bare-path", "prose"],
)
def test_nothing_but_the_file_lookup_is_named(value):
    assert file_lookups(found({"x": value}), {}, {}) == ()


def test_one_lookup_written_twice_in_one_place_is_named_once():
    data = {"a": "{{ lookup('file', 'x') }}", "b": "{{ lookup('file', 'x') }}"}

    assert len(file_lookups(found(data), {}, {})) == 1
