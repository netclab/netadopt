"""Wiring for the corpus tier: the readers, run over somebody else's repositories.

Finding repositories is specific -- AVD keeps its examples inside the collection,
netascode ships whole example repositories instead -- so it is done here, in the
wiring. pytest allows one conftest per directory, so a second ecosystem is a second
function beside `_avd_repos` and a second line in `_repos`, not a second file.

No test file names an ecosystem: what they assert has to hold for any repository.
Properties only, no counts -- a count goes red on somebody else's release.

The corpus is the `avd` submodule of this repository, at the AVD release netadopt is
tested on. An environment variable names another checkout instead; with neither
checked out these tests skip.

A molecule scenario says in molecule.yml how ansible-playbook runs it, and that is
read rather than guessed from the directories: the -i it passes, and whether its
inventory or playbook lives in another directory. A scenario that borrows one is a
wrapper around a repository tested elsewhere, and is skipped with the reason.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from netadopt.ansible import Ansible, resolve_ansible
from netadopt.cli import app
from netadopt.inventory import Inventory, read_inventory

AVD_ENV = "NETADOPT_AVD"
MOLECULE_FILE = "molecule.yml"

# tests/corpus/conftest.py -> the repository root
AVD_SUBMODULE = Path(__file__).resolve().parents[2] / "avd"

# Where the collection sits inside a clone of aristanetworks/avd. A path pointing
# straight at the collection works too, so either can be exported.
AVD_COLLECTION = Path("ansible_collections/arista/avd")


def _avd_repos() -> list[tuple[str, Path]]:
    raw = os.environ.get(AVD_ENV)
    root = Path(raw).expanduser() if raw else AVD_SUBMODULE
    collection = next(
        (path for path in (root / AVD_COLLECTION, root) if (path / "examples").is_dir()),
        None,
    )
    if collection is None:
        return []

    found = sorted((collection / "examples").iterdir())
    found += sorted((collection / "extensions" / "molecule").iterdir())
    return [("avd", path) for path in found if path.is_dir()]


def _repos() -> list[tuple[str, Path]]:
    return _avd_repos()


def _molecule(path: Path) -> dict[str, str | None]:
    """The -i molecule passes to ansible-playbook, and its converge playbook."""
    file = path / MOLECULE_FILE
    try:
        data = yaml.safe_load(file.read_text(encoding="utf-8")) if file.is_file() else None
    except yaml.YAMLError:
        data = None
    if not isinstance(data, dict):
        return {}

    # `ansible:` in current molecule, `provisioner:` in older files
    ansible = data.get("ansible") or data.get("provisioner") or {}
    args = ((ansible.get("executor") or {}).get("args") or {}).get("ansible_playbook") or []
    inventory = next(
        (arg.split("=", 1)[1] for arg in args if isinstance(arg, str) and arg.startswith("--inventory=")),
        None,
    )
    return {"inventory": inventory, "converge": (ansible.get("playbooks") or {}).get("converge")}


def _borrowed(path: Path) -> str | None:
    """Why a scenario has no repository of its own to read, or None."""
    for what, value in _molecule(path).items():
        if isinstance(value, str) and ".." in Path(value).parts:
            return f"molecule runs it with the {what} {value}"
    return None


def pytest_generate_tests(metafunc: pytest.Metafunc) -> None:
    if "repo" not in metafunc.fixturenames:
        return

    repos = _repos()
    if not repos:
        # One skipped case rather than none: a suite that quietly collects nothing
        # reads as a suite that passed.
        skip = pytest.mark.skip(
            reason=f"no AVD checkout: run `git submodule update --init`, or set {AVD_ENV}"
        )
        metafunc.parametrize("repo", [pytest.param(None, marks=skip)])
        return

    # The corpus tags the id, so a second ecosystem cannot collide with a scenario
    # name from the first.
    params = []
    for corpus, path in repos:
        reason = _borrowed(path)
        marks = [pytest.mark.skip(reason=reason)] if reason else []
        params.append(pytest.param(path, marks=marks, id=f"{corpus}/{path.name}"))
    metafunc.parametrize("repo", params)


@pytest.fixture
def inventory_source(repo: Path) -> str | None:
    """The -i argument, as the repository states it: molecule's, a directory, a file, or nothing.

    molecule's comes first: a scenario can keep generated files under inventory/, and
    only the -i it really passes keeps them out.
    """
    if told := _molecule(repo).get("inventory"):
        return told
    if (repo / "inventory").is_dir():
        return "inventory/"
    if (repo / "inventory.yml").is_file():
        return "inventory.yml"
    return None


@pytest.fixture(scope="session")
def ansible() -> Ansible:
    found = resolve_ansible()
    if not found.usable:
        pytest.skip(f"no Ansible to ask: {found.problem}")
    return found


@pytest.fixture
def listed(ansible: Ansible, repo: Path, inventory_source: str | None) -> Inventory:
    inventory = read_inventory(ansible, repo, inventory_source)
    if not inventory.usable:
        # no inventory of its own is a fact about the repository; problem says why
        pytest.skip(f"no inventory: {inventory.problem}")
    return inventory


@pytest.fixture
def playbook(repo: Path) -> str:
    """The playbook the repository is built with: molecule's converge, else the file's."""
    for name in (_molecule(repo).get("converge"), "converge.yml", "build.yml"):
        if name and (repo / name).is_file():
            return name
    pytest.skip("no playbook the repository is built with")


@pytest.fixture
def emitted(repo: Path, playbook: str, inventory_source: str | None) -> list[dict]:
    """What `emit` writes for the repository's first play."""
    args = ["avd", "emit", str(repo), "--playbook", playbook]
    if inventory_source:
        args += ["--inventory", inventory_source]
    result = CliRunner().invoke(app, args)
    assert result.exit_code == 0, result.stderr
    return list(yaml.safe_load_all(result.stdout))
