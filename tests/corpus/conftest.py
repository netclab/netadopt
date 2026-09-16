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

import json
import os
import re
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

import pytest
import yaml
from typer.testing import CliRunner

from netadopt import ansible_yaml
from netadopt.ansible import Ansible, resolve_ansible
from netadopt.ansiblecfg import read_ansible_cfg
from netadopt.cli import app
from netadopt.galaxy import ensure_collections
from netadopt.inventory import Inventory, read_inventory
from netadopt.lab import Lab, netclab_values, values_yaml
from netadopt.playbook import IMPORT_PLAYBOOK_KEYS, TASK_KEYS, read_playbook
from netadopt.reconstruct import Reconstructed, reconstruct
from netadopt.render import Rendered, render

AVD_ENV = "NETADOPT_AVD"
MOLECULE_FILE = "molecule.yml"

# tests/corpus/conftest.py -> the repository root
AVD_SUBMODULE = Path(__file__).resolve().parents[2] / "avd"

# Where the collection sits inside a clone of aristanetworks/avd. A path pointing
# straight at the collection works too, so either can be exported.
AVD_COLLECTION = Path("ansible_collections/arista/avd")


def _avd_collection() -> Path | None:
    raw = os.environ.get(AVD_ENV)
    root = Path(raw).expanduser() if raw else AVD_SUBMODULE
    return next(
        (path for path in (root / AVD_COLLECTION, root) if (path / "examples").is_dir()),
        None,
    )


def _avd_repos() -> list[tuple[str, Path]]:
    collection = _avd_collection()
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
def emitted(
    repo: Path, playbook: str, inventory_source: str | None, listed: Inventory
) -> list[dict]:
    """What `emit` writes for the repository's first play.

    `listed` first: a repository with no inventory of its own is skipped, not failed.
    """
    args = ["avd", "emit", str(repo), "--playbook", playbook]
    if inventory_source:
        args += ["--inventory", inventory_source]
    result = CliRunner().invoke(app, args)
    assert result.exit_code == 0, result.stderr
    return list(yaml.safe_load_all(result.stdout))


@pytest.fixture
def reconstructed(emitted: list[dict], tmp_path: Path) -> Reconstructed:
    """repo', rebuilt from what `emit` writes."""
    built = reconstruct(emitted, tmp_path / "rebuilt")
    assert built.usable, built.problem
    return built


@pytest.fixture
def rebuilt_env(repo: Path, reconstructed: Reconstructed, tmp_path: Path) -> dict[str, str]:
    """The environment repo' is run with: its vault password, from outside it.

    The source's file stands in for the Secret `vault_password` names. Ansible's own
    variable outranks the vault_password_file ansible.cfg names.
    """
    if reconstructed.vault_password is None:
        return {}
    named = read_ansible_cfg(repo).vault_password_file
    password = tmp_path / "vault-password"
    password.write_bytes((repo / named).read_bytes())
    password.chmod(0o600)
    return {"ANSIBLE_VAULT_PASSWORD_FILE": str(password)}


@pytest.fixture(scope="session")
def collections(ansible: Ansible) -> Path:
    """The collections netadopt renders with, in its own cache -- fetched once, if at all."""
    found = ensure_collections(ansible)
    if not found.usable:
        pytest.skip(f"no collections to render with: {found.problem}")
    return found.path


@pytest.fixture(scope="session")
def _renders() -> dict[Path, Rendered]:
    """One render per repository, kept for every test that asks: it is the slow part."""
    return {}


@pytest.fixture
def rendered(
    ansible: Ansible,
    repo: Path,
    playbook: str,
    inventory_source: str | None,
    collections: Path,
    _renders: dict[Path, Rendered],
    tmp_path_factory: pytest.TempPathFactory,
) -> Rendered:
    """What AVD writes for the repository's first play, on the copy netadopt renders on.

    A first play that is no eos_designs fabric renders nothing, and that is a fact about
    the repository: it is skipped with what Ansible said.
    """
    if repo not in _renders:
        work = tmp_path_factory.mktemp("render") / repo.name
        _renders[repo] = render(
            ansible, repo, playbook, collections, work, inventory=inventory_source
        )
    out = _renders[repo]
    if not out.usable:
        # A skip reason is read in a one-line summary, and Ansible names every host.
        pytest.skip(f"nothing rendered: {out.problem.splitlines()[0]}")
    return out


@pytest.fixture
def lab(rendered: Rendered) -> Lab:
    """The values for the repository's fabric. Nothing reads them without this."""
    built = netclab_values(rendered.hosts)
    assert built.problem is None, built.problem
    return built


# netclab-chart, and helm, judge the values the way Ansible judges the model: by
# consuming them. Both are named by the environment, so neither is a dependency of the
# suite and neither is a path written into a test.
CHART_ENV = "NETADOPT_NETCLAB_CHART"
HELM_EXE = "helm"
# The chart refuses to render unless Multus's CRD is in the cluster, and there is none.
HELM_API_VERSIONS = "k8s.cni.cncf.io/v1"
HELM_RELEASE = "lab"
HELM_TIMEOUT = 120


@pytest.fixture(scope="session")
def chart() -> tuple[str, Path]:
    """helm, and a checkout of netclab-chart, or the reason there is nothing to ask."""
    helm = shutil.which(HELM_EXE)
    if helm is None:
        pytest.skip(f"no {HELM_EXE} on PATH")
    raw = os.environ.get(CHART_ENV)
    if not raw:
        pytest.skip(f"no netclab-chart: set {CHART_ENV} to a checkout of it")
    root = Path(raw).expanduser()
    if not (root / "Chart.yaml").is_file():
        pytest.skip(f"{CHART_ENV}={raw} holds no Chart.yaml")
    return helm, root


@pytest.fixture(scope="session")
def _templates() -> dict[Path, subprocess.CompletedProcess]:
    """One helm run per repository, as the render is kept per repository."""
    return {}


@pytest.fixture
def templated(
    repo: Path,
    lab: Lab,
    chart: tuple[str, Path],
    _templates: dict[Path, subprocess.CompletedProcess],
    tmp_path_factory: pytest.TempPathFactory,
) -> subprocess.CompletedProcess:
    """What helm makes of the values -- the finished run, so that a test can judge it."""
    if repo not in _templates:
        helm, root = chart
        values = tmp_path_factory.mktemp("chart") / "values.yaml"
        values.write_text(values_yaml(lab.values), encoding="utf-8")
        _templates[repo] = subprocess.run(
            [helm, "template", HELM_RELEASE, str(root),
             "--values", str(values), "--api-versions", HELM_API_VERSIONS],
            check=False,
            capture_output=True,
            text=True,
            timeout=HELM_TIMEOUT,
            stdin=subprocess.DEVNULL,
        )
    return _templates[repo]


@pytest.fixture
def pool_file() -> Callable[[dict], str | None]:
    """Where AVD's pool manager keeps a host's node IDs, by the host's resolved vars.

    pyavd's own rule (`node_id_pools.py`): `pools_file` as given, else
    `<output_dir>/data/<fabric_name>-ids.yml`, output_dir being the role's
    `{{ root_dir }}/{{ output_dir_name }}` and root_dir the inventory's directory.
    """

    def where(hostvars: dict) -> str | None:
        numbering = hostvars.get("fabric_numbering") or {}
        node_id = (numbering.get("node_id") or {}) if isinstance(numbering, dict) else {}
        if node_id.get("algorithm") != "pool_manager":
            return None
        if node_id.get("pools_file"):
            return str(node_id["pools_file"])
        root_dir = hostvars.get("root_dir") or hostvars.get("inventory_dir")
        output_dir = hostvars.get("output_dir") or f"{root_dir}/{hostvars.get('output_dir_name') or 'intended'}"
        return f"{output_dir}/data/{hostvars.get('fabric_name')}-ids.yml"

    return where


@pytest.fixture
def named_files() -> Callable[[dict[str, dict], Path], set[str]]:
    """Every file the hosts' resolved vars name by a relative path, where AVD opens it.

    `<playbook dir>/templates/<path>` first, then `<playbook dir>/<path>`
    (`compile_searchpath`). A host's pools_file is a pool, and compared as one.
    """

    def found(hosts: dict[str, dict], base: Path) -> set[str]:
        values: set[str] = set()
        pools: set[str] = set()
        for hostvars in hosts.values():
            _strings_into(hostvars, values)
            numbering = hostvars.get("fabric_numbering") or {}
            node_id = (numbering.get("node_id") or {}) if isinstance(numbering, dict) else {}
            if node_id.get("pools_file"):
                pools.add(str(node_id["pools_file"]))
        out: set[str] = set()
        for value in values - pools:
            pure = PurePosixPath(value)
            if "{{" in value or "<" in value or pure.is_absolute() or ".." in pure.parts:
                continue
            for path in (PurePosixPath("templates") / pure, pure):
                try:
                    if (base / path).is_file():
                        out.add(str(path))
                        break
                except OSError:
                    break
        return out

    return found


def _strings_into(node: object, out: set[str]) -> None:
    if isinstance(node, dict):
        for value in node.values():
            _strings_into(value, out)
    elif isinstance(node, list):
        for value in node:
            _strings_into(value, out)
    elif isinstance(node, str) and node and "\n" not in node and len(node) <= 1024:
        out.add(node)


# The task AVD writes every host's resolved vars from, as templated/<host>.json.
ORACLE_TASK = "arista.avd.validate_inputs"
ORACLE_PLAYBOOK = "netadopt-oracle.yml"
# An inventory's ansible_connection outranks -c; extra vars are the one level above it.
ORACLE_EXTRA_VARS = ("ansible_connection=local", "ansible_become=false")
ORACLE_TIMEOUT = 600
# How a file ansible-vault encrypted begins.
VAULT_HEADER = b"$ANSIBLE_VAULT;"

# The task names every host it could not template, then fails. A host name may hold dots.
_FAILED_HOSTS = re.compile(r"processing \d+ host\(s\): (.+?)\.(?:\"|$)", re.MULTILINE)


@dataclass(frozen=True)
class Resolved:
    hosts: dict[str, dict] = field(default_factory=dict)  # host -> its vars, templated
    failed: frozenset[str] = frozenset()  # hosts Ansible could not template
    problem: str | None = None  # set when there is nothing to compare


Resolve = Callable[..., Resolved]


@pytest.fixture
def resolve(ansible: Ansible) -> Resolve:
    """Every host's vars as Ansible resolves them for the play, or why it could not.

    The playbook's first play keeps everything but its tasks, which become the one task
    that writes the vars out. A path under the repository reads `<root>`, so that two
    directories compare.
    """
    collection = _avd_collection()
    if collection is None:
        pytest.skip("no AVD checkout")
    env = {
        **os.environ,
        # ansible_collections/arista/avd -> the directory holding ansible_collections
        "ANSIBLE_COLLECTIONS_PATH": str(collection.parents[2]),
        # A git checkout of AVD otherwise imports pyavd from its own source tree, whose
        # schema store is only built for a release.
        "AVD_NEVER_RUN_FROM_SOURCE": "1",
    }

    def run(
        root: Path, inventory: str | None, playbook: str, more_env: dict[str, str] | None = None
    ) -> Resolved:
        out = root.parent / f"{root.name}-oracle"
        raw = read_playbook(root, playbook).plays[0].raw
        if any(key in raw for key in IMPORT_PLAYBOOK_KEYS):
            return Resolved(problem="play [0] imports another playbook and has no hosts of its own")
        play = {key: value for key, value in raw.items() if key not in (*TASK_KEYS, "roles")}
        play["tasks"] = [
            {
                ORACLE_TASK: {"tmp_dir": str(out), "schema_name": "avd_design"},
                "delegate_to": "localhost",
                "run_once": True,
            }
        ]
        book = (root / playbook).parent / ORACLE_PLAYBOOK
        book.write_text(ansible_yaml.dump([play]), encoding="utf-8")

        command = [str(ansible.exe), str(book.relative_to(root))]
        if inventory:
            command += ["-i", inventory]
        for extra in ORACLE_EXTRA_VARS:
            command += ["-e", extra]
        run_env = {**env, **(more_env or {})}
        done = subprocess.run(
            command,
            check=False,
            cwd=root,
            env=run_env,
            capture_output=True,
            text=True,
            timeout=ORACLE_TIMEOUT,
            stdin=subprocess.DEVNULL,
        )
        said = done.stdout + done.stderr
        failed: frozenset[str] = frozenset()
        if done.returncode != 0:
            named = _FAILED_HOSTS.search(said)
            if named is None:
                errors = [line for line in said.splitlines() if "fatal:" in line or "ERROR" in line]
                return Resolved(problem="\n".join(errors) or f"exit {done.returncode}")
            # the rest of the hosts were templated, and are compared
            failed = frozenset(host.strip() for host in named.group(1).split(","))

        files = sorted((out / "templated").glob("*.json"))
        vaulted = [str(file) for file in files if file.read_bytes().startswith(VAULT_HEADER)]
        if vaulted:
            # validate_inputs encrypts what it writes whenever a vault password is set;
            # the same directory and environment open it with the same password
            exe = ansible.beside("ansible-vault")
            if exe is None:
                return Resolved(problem=f"ansible-vault is missing beside {ansible.exe}")
            opened = subprocess.run(
                [str(exe), "decrypt", *vaulted],
                check=False,
                cwd=root,
                env=run_env,
                capture_output=True,
                text=True,
                timeout=ORACLE_TIMEOUT,
                stdin=subprocess.DEVNULL,
            )
            if opened.returncode != 0:
                return Resolved(problem=f"ansible-vault decrypt: {opened.stderr.strip()}")

        hosts = {
            file.stem: json.loads(file.read_text(encoding="utf-8").replace(str(root), "<root>"))
            for file in files
        }
        return Resolved(hosts=hosts, failed=failed)

    return run
