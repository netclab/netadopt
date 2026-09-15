"""`netadopt --version`, and the AVD it names."""

from __future__ import annotations

import tomllib
from importlib.metadata import version
from pathlib import Path

from typer.testing import CliRunner

from netadopt import AVD_TESTED
from netadopt.cli import app

PYPROJECT = Path(__file__).parent.parent / "pyproject.toml"


def test_version_names_netadopt_and_the_avd_tested():
    result = CliRunner().invoke(app, ["--version"])

    assert result.exit_code == 0
    assert result.output == f"netadopt {version('netadopt')} (avd: tested on {AVD_TESTED})\n"


def test_avd_tested_is_the_avd_extra_pin():
    extra = tomllib.loads(PYPROJECT.read_text())["project"]["optional-dependencies"]["avd"]

    assert f"pyavd=={AVD_TESTED}" in extra
