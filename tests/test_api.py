"""The contract with function-avd: each name netadopt.api exports, and its shape."""

from __future__ import annotations

import dataclasses
import inspect

import pytest

from netadopt import api


def signature(function, drop: tuple[str, ...] = ()) -> str:
    whole = inspect.signature(function)
    kept = [p for p in whole.parameters.values() if p.name not in drop]
    return str(whole.replace(parameters=kept))


def test_the_contract_is_these_names():
    assert sorted(api.__all__) == [
        "API_VERSION",
        "AVD_TESTED",
        "Ansible",
        "Collections",
        "FABRIC_LABEL",
        "Inventory",
        "Reconstructed",
        "Rendered",
        "ensure_collections",
        "read_inventory",
        "reconstruct",
        "render",
        "render_extra_vars",
        "render_play",
        "resolve_ansible",
    ]


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("API_VERSION", "avd.netclab.dev/v1alpha1"),
        ("FABRIC_LABEL", "avd.netclab.dev/fabric"),
    ],
)
def test_constant(name, value):
    assert getattr(api, name) == value


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("resolve_ansible", "() -> 'Ansible'"),
        (
            "read_inventory",
            "(ansible: 'Ansible', repo: 'Path', source: 'str | None' = None) -> 'Inventory'",
        ),
        (
            "reconstruct",
            "(documents: 'Iterable[dict]', root: 'Path', fabric: 'str | None' = None) -> 'Reconstructed'",
        ),
        (
            "render",
            (
                "(ansible: 'Ansible', repo: 'Path', playbook: 'str', collections: 'Path', work: 'Path', "
                "play: 'int' = 0, inventory: 'str | None' = None) -> 'Rendered'"
            ),
        ),
        ("render_play", "(raw: 'dict') -> 'dict'"),
        ("render_extra_vars", "(out: 'Path') -> 'dict'"),
    ],
)
def test_function_signature(name, expected):
    assert signature(getattr(api, name)) == expected


def test_ensure_collections_signature():
    # archives and fetch are the tests' seams, not the contract
    assert (
        signature(api.ensure_collections, drop=("archives", "fetch"))
        == "(ansible: 'Ansible', root: 'Path | None' = None) -> 'Collections'"
    )


@pytest.mark.parametrize(
    ("name", "fields", "properties"),
    [
        ("Ansible", ["exe", "core", "python", "config_file", "collections", "problem"], ["usable"]),
        ("Collections", ["path", "notes", "problem"], ["usable"]),
        ("Inventory", ["source", "groups", "hostvars", "warnings", "problem"], ["usable", "hosts"]),
        (
            "Reconstructed",
            ["root", "inventory", "playbook", "files", "vault_password", "problem"],
            ["usable"],
        ),
        ("Rendered", ["hosts", "problem"], ["usable"]),
    ],
)
def test_result_shape(name, fields, properties):
    result = getattr(api, name)

    assert [f.name for f in dataclasses.fields(result)] == fields
    assert [n for n, v in vars(result).items() if isinstance(v, property)] == properties
