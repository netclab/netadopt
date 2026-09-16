"""The Ansible collections AVD renders with, fetched once into netadopt's cache.

Four archives, pinned by URL and sha256 to the AVD netadopt is tested on, installed
with `ansible-galaxy collection install --no-deps`: installing the same set by name
waits minutes on Galaxy resolving it, the archives take seconds. When an archive cannot
be fetched or does not match, `arista.avd` is installed by name instead, from the
galaxy servers the user's Ansible is configured with.

The cache is complete or absent: an install lands beside it and is renamed into place.
The answer is always a value -- never an exception.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import urllib.request
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from netadopt import AVD_TESTED
from netadopt.ansible import Ansible

# Looked up next to ansible-playbook, not on PATH.
GALAXY_EXE = "ansible-galaxy"

ARTIFACTS = "https://galaxy.ansible.com/api/v3/plugin/ansible/content/published/collections/artifacts"

# Seconds: one archive, and one install -- by name, which resolves against Galaxy.
DOWNLOAD_TIMEOUT = 120
INSTALL_TIMEOUT = 1200


@dataclass(frozen=True)
class Archive:
    namespace: str
    name: str
    version: str
    sha256: str

    @property
    def collection(self) -> str:
        return f"{self.namespace}.{self.name}"

    @property
    def file(self) -> str:
        return f"{self.namespace}-{self.name}-{self.version}.tar.gz"

    @property
    def url(self) -> str:
        return f"{ARTIFACTS}/{self.file}"


# arista.avd first, then the collections ansible-galaxy resolves for it. Moved with the
# avd submodule, by hand.
ARCHIVES = (
    Archive("arista", "avd", "6.4.0", "99342247e89bb15ddfbb08a4014d1e3c7e92f0c9b34e901a43dc39ad1bda28bf"),
    Archive("arista", "eos", "12.2.0", "eaf585c7fdcf8d10c920f3a47409ef010edfae15fde7bae901d66538400b4c17"),
    Archive("ansible", "netcommon", "8.6.2", "4040e782c26ddb97979e03a07f4894a2dc82f59c1e71e7f5c5cb2d5c6a31628a"),
    Archive("ansible", "utils", "6.1.0", "e29255d2a41e90b4de68524df4124100b51a0525167459e5e2def00deed97799"),
)


@dataclass(frozen=True)
class Collections:
    """The directory to give ANSIBLE_COLLECTIONS_PATH, or why there is none."""

    path: Path | None = None
    notes: tuple[str, ...] = ()
    problem: str | None = None

    @property
    def usable(self) -> bool:
        return self.problem is None


def cache_root() -> Path:
    """netadopt's collections for the AVD it is tested on, under XDG_CACHE_HOME."""
    base = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
    return base / "netadopt" / "collections" / f"avd-{AVD_TESTED}"


def download(url: str) -> bytes:
    """The bytes at `url`; the proxy variables of the environment apply."""
    with urllib.request.urlopen(url, timeout=DOWNLOAD_TIMEOUT) as response:
        return response.read()


def ensure_collections(
    ansible: Ansible,
    root: Path | None = None,
    archives: Sequence[Archive] = ARCHIVES,
    fetch: Callable[[str], bytes] = download,
) -> Collections:
    """The collections in `root`, installed first if they are not there yet."""
    root = root or cache_root()
    avd = archives[0]
    if _installed(root, avd):
        return Collections(path=root)

    if not ansible.usable:
        return Collections(problem=f"no Ansible to install collections with: {ansible.problem}")
    exe = ansible.beside(GALAXY_EXE)
    if exe is None:
        return Collections(problem=f"{GALAXY_EXE} is missing beside {ansible.exe}")

    notes: list[str] = []
    try:
        root.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=root.parent, prefix=f".{root.name}-") as work:
            staging = Path(work) / "collections"
            files, why = _fetch_all(archives, Path(work), fetch)
            if why is None:
                why = _install(exe, [*(str(file) for file in files), "--no-deps"], staging)
            if why is not None:
                shutil.rmtree(staging, ignore_errors=True)
                by_name = _install(exe, [f"{avd.collection}:=={avd.version}"], staging)
                if by_name is not None:
                    return Collections(
                        problem=f"{avd.collection} {avd.version} not installed: "
                        f"by URL -- {why}; by name -- {by_name}"
                    )
                notes.append(
                    f"{avd.collection} {avd.version}: not installed by URL -- {why}; "
                    "installed by name from the configured galaxy servers"
                )
            if not _installed(staging, avd):
                return Collections(
                    notes=tuple(notes),
                    problem=f"{GALAXY_EXE} succeeded, but {avd.collection} {avd.version} is not in {staging}",
                )
            try:
                staging.rename(root)
            except OSError:
                # another run renamed its own install into place first
                if not _installed(root, avd):
                    raise
    except OSError as err:
        return Collections(notes=tuple(notes), problem=f"collections not installed in {root}: {err}")
    return Collections(path=root, notes=tuple(notes))


def _installed(root: Path, archive: Archive) -> bool:
    manifest = root / "ansible_collections" / archive.namespace / archive.name / "MANIFEST.json"
    try:
        info = json.loads(manifest.read_text())["collection_info"]
    except (OSError, ValueError, KeyError, TypeError):
        return False
    return info.get("version") == archive.version


def _fetch_all(
    archives: Sequence[Archive], into: Path, fetch: Callable[[str], bytes]
) -> tuple[list[Path], str | None]:
    """Every archive written into `into` once its sha256 matches, or why not."""

    def one(archive: Archive) -> tuple[Path | None, str | None]:
        try:
            data = fetch(archive.url)
        except (OSError, ValueError) as err:  # urllib's errors are OSErrors
            return None, f"{archive.url}: {err}"
        digest = hashlib.sha256(data).hexdigest()
        if digest != archive.sha256:
            return None, f"{archive.file} has sha256 {digest}, not {archive.sha256}"
        path = into / archive.file
        path.write_bytes(data)
        return path, None

    with ThreadPoolExecutor(max_workers=len(archives)) as pool:
        results = list(pool.map(one, archives))
    problems = [why for _, why in results if why]
    return [path for path, _ in results if path], "; ".join(problems) or None


def _install(exe: Path, args: list[str], into: Path) -> str | None:
    """Run `ansible-galaxy collection install` into `into`; None, or why it failed."""
    command = [str(exe), "collection", "install", *args, "-p", str(into)]
    try:
        done = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=INSTALL_TIMEOUT,
            # closed, so a galaxy server asking for a token fails instead of hanging
            stdin=subprocess.DEVNULL,
        )
    except OSError as err:
        return f"{exe} could not be run: {err}"
    except subprocess.TimeoutExpired:
        return f"{exe} did not return in {INSTALL_TIMEOUT}s"
    if done.returncode != 0:
        return (done.stderr or done.stdout).strip() or f"exit {done.returncode}"
    return None
