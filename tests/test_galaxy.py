"""Fetching the collections AVD renders with -- no network and no real ansible-galaxy.

The download is a function the test passes. ansible-galaxy is a stub that logs its
arguments and, like the real one, writes each collection's MANIFEST.json under -p.
"""

from __future__ import annotations

import hashlib
import json
import sys
import urllib.error
from pathlib import Path

import pytest

from netadopt import AVD_TESTED
from netadopt.ansible import Ansible
from netadopt.galaxy import ARCHIVES, Archive, cache_root, ensure_collections

STUB = """\
import json, pathlib, re, sys
here = pathlib.Path(__file__).parent
args = sys.argv[1:]
with (here / "calls.jsonl").open("a") as log:
    log.write(json.dumps(args) + "\\n")
into = pathlib.Path(args[args.index("-p") + 1])
wanted = args[2:args.index("-p")]
by_name = [a for a in wanted if ":==" in a]
if (here / ("fail-name" if by_name else "fail-files")).exists():
    sys.stderr.write("boom\\n")
    sys.exit(1)
for arg in wanted:
    if arg.endswith(".tar.gz"):
        namespace, name, version = re.fullmatch(r"(\\w+)-(\\w+)-(.+)\\.tar\\.gz", pathlib.Path(arg).name).groups()
    elif ":==" in arg:
        collection, version = arg.split(":==")
        namespace, name = collection.split(".")
    else:
        continue
    manifest = into / "ansible_collections" / namespace / name / "MANIFEST.json"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(json.dumps({"collection_info": {"namespace": namespace, "name": name, "version": version}}))
"""

AVD = Archive("arista", "avd", "6.4.0", hashlib.sha256(b"avd").hexdigest())
EOS = Archive("arista", "eos", "12.2.0", hashlib.sha256(b"eos").hexdigest())
CONTENT = {AVD.url: b"avd", EOS.url: b"eos"}


@pytest.fixture
def ansible(tmp_path: Path) -> Ansible:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "ansible-playbook").write_text("")
    galaxy = bin_dir / "ansible-galaxy"
    galaxy.write_text(f"#!{sys.executable}\n{STUB}")
    galaxy.chmod(0o755)
    return Ansible(exe=str(bin_dir / "ansible-playbook"), core="2.21.3")


def calls(ansible: Ansible) -> list[list[str]]:
    log = Path(ansible.exe).with_name("calls.jsonl")
    return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []


def fail(ansible: Ansible, what: str) -> None:
    Path(ansible.exe).with_name(what).write_text("")


def served(url: str) -> bytes:
    return CONTENT[url]


def unreachable(url: str) -> bytes:
    raise urllib.error.URLError("Name or service not known")


def test_archives_that_match_are_installed_without_resolving(ansible, tmp_path):
    root = tmp_path / "cache" / "avd-6.4.0"

    found = ensure_collections(ansible, root, (AVD, EOS), served)

    assert found.usable, found.problem
    assert found.path == root
    assert (root / "ansible_collections" / "arista" / "avd" / "MANIFEST.json").is_file()
    [call] = calls(ansible)
    assert call[:2] == ["collection", "install"]
    assert "--no-deps" in call
    assert [Path(arg).name for arg in call if arg.endswith(".tar.gz")] == [AVD.file, EOS.file]
    assert found.notes == ()


def test_a_complete_cache_is_used_with_no_download_and_no_galaxy(ansible, tmp_path):
    root = tmp_path / "cache" / "avd-6.4.0"
    ensure_collections(ansible, root, (AVD, EOS), served)

    def forbidden(url: str) -> bytes:
        raise AssertionError(f"downloaded {url}")

    found = ensure_collections(ansible, root, (AVD, EOS), forbidden)

    assert found.path == root
    assert len(calls(ansible)) == 1  # the first install only


def test_a_wrong_sha256_installs_by_name_and_says_why(ansible, tmp_path):
    root = tmp_path / "cache" / "avd-6.4.0"

    found = ensure_collections(ansible, root, (AVD, EOS), lambda url: b"tampered")

    assert found.usable, found.problem
    assert calls(ansible) == [["collection", "install", "arista.avd:==6.4.0", "-p", calls(ansible)[0][-1]]]
    [note] = found.notes
    assert f"{AVD.file} has sha256 {hashlib.sha256(b'tampered').hexdigest()}, not {AVD.sha256}" in note
    assert note.endswith("installed by name from the configured galaxy servers")


def test_an_unreachable_url_installs_by_name(ansible, tmp_path):
    found = ensure_collections(ansible, tmp_path / "cache" / "avd-6.4.0", (AVD, EOS), unreachable)

    assert found.usable, found.problem
    assert "Name or service not known" in found.notes[0]
    assert "arista.avd:==6.4.0" in calls(ansible)[0]


def test_an_install_by_url_that_fails_installs_by_name(ansible, tmp_path):
    fail(ansible, "fail-files")

    found = ensure_collections(ansible, tmp_path / "cache" / "avd-6.4.0", (AVD, EOS), served)

    assert found.usable, found.problem
    assert len(calls(ansible)) == 2
    assert "boom" in found.notes[0]


def test_when_both_fail_the_problem_names_both_and_no_cache_is_left(ansible, tmp_path):
    fail(ansible, "fail-name")
    root = tmp_path / "cache" / "avd-6.4.0"

    found = ensure_collections(ansible, root, (AVD, EOS), unreachable)

    assert not found.usable
    assert found.problem.startswith("arista.avd 6.4.0 not installed: by URL -- ")
    assert "Name or service not known" in found.problem
    assert found.problem.endswith("by name -- boom")
    assert not root.exists()
    assert list(root.parent.iterdir()) == []  # the work directory went too


def test_no_galaxy_beside_ansible_is_a_problem(tmp_path):
    (tmp_path / "ansible-playbook").write_text("")
    ansible = Ansible(exe=str(tmp_path / "ansible-playbook"), core="2.21.3")

    found = ensure_collections(ansible, tmp_path / "cache", (AVD, EOS), served)

    assert found.problem == f"ansible-galaxy is missing beside {ansible.exe}"


def test_the_first_archive_is_the_avd_netadopt_is_tested_on():
    assert (ARCHIVES[0].collection, ARCHIVES[0].version) == ("arista.avd", AVD_TESTED)


def test_the_cache_is_under_xdg_cache_home(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))

    assert cache_root() == tmp_path / "netadopt" / "collections" / f"avd-{AVD_TESTED}"
