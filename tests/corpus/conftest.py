"""Wiring for the corpus tier: the readers, run over somebody else's repositories.

Finding repositories is specific -- AVD keeps its examples inside the collection,
netascode ships whole example repositories instead -- so it is done here, in the
wiring. pytest allows one conftest per directory, so a second ecosystem is a second
function beside `_avd_repos` and a second line in `_repos`, not a second file.

No test file names an ecosystem: what they assert has to hold for any repository.
Properties only, no counts -- a count goes red on somebody else's release.

A checkout is named by an environment variable and these tests skip without one; the
path is somebody's laptop, so it is not written down here.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

AVD_ENV = "NETADOPT_AVD"

# Where the collection sits inside a clone of aristanetworks/avd. A path pointing
# straight at the collection works too, so either can be exported.
AVD_COLLECTION = Path("ansible_collections/arista/avd")


def _avd_repos() -> list[tuple[str, Path]]:
    raw = os.environ.get(AVD_ENV)
    if not raw:
        return []

    root = Path(raw).expanduser()
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


def pytest_generate_tests(metafunc: pytest.Metafunc) -> None:
    if "repo" not in metafunc.fixturenames:
        return

    repos = _repos()
    if not repos:
        # One skipped case rather than none: a suite that quietly collects nothing
        # reads as a suite that passed.
        skip = pytest.mark.skip(reason=f"set {AVD_ENV} to a checkout to run the corpus tier")
        metafunc.parametrize("repo", [pytest.param(None, marks=skip)])
        return

    # The corpus tags the id, so a second ecosystem cannot collide with a scenario
    # name from the first.
    metafunc.parametrize(
        "repo",
        [path for _, path in repos],
        ids=[f"{corpus}/{path.name}" for corpus, path in repos],
    )


@pytest.fixture
def inventory_source(repo: Path) -> str | None:
    """The -i argument, as the repository states it: a directory, a file, or nothing."""
    if (repo / "inventory").is_dir():
        return "inventory/"
    if (repo / "inventory.yml").is_file():
        return "inventory.yml"
    return None
