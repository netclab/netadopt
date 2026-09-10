"""A repository rebuilt from the objects `emit` writes -- the inverse of `emit`.

Not the source repository: one Ansible resolves identically. The layout is fixed, and
the source's own is kept only where a path in it is load-bearing:

    ansible.cfg                      spec.ansibleCfg, when it holds anything
    <inventory>                      spec.groups -- where ansible.cfg names it,
                                     else inventory/hosts.yml
    <inventory dir>/group_vars/ ...  every FabricInput beside the inventory
    playbook.yml                     [spec.play], at the root
    group_vars/, host_vars/          every FabricInput beside the playbook

Paths inside `design` and ansible.cfg are relative to the source's playbook and
inventory, and this layout keeps them true. Every FabricInput is one file; Ansible
ranks them itself, as it did on disk.
"""

from __future__ import annotations

import configparser
import io
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from netadopt import ansible_yaml
from netadopt.ansiblecfg import CONFIG_FILE, AnsibleCfg
from netadopt.varfiles import GROUP_VARS, HOST_VARS, INVENTORY_ROOT, PLAYBOOK_ROOT
from netadopt.xr import FABRIC, FABRIC_INPUT

PLAYBOOK = "playbook.yml"
DEFAULT_INVENTORY = "inventory/hosts.yml"
# What the yaml inventory plugin takes as a file; anything else ansible.cfg names is
# a directory, and the inventory file goes inside it.
INVENTORY_SUFFIXES = (".yml", ".yaml", ".json")

_ROOT = PurePosixPath(".")


@dataclass(frozen=True)
class Reconstructed:
    root: Path
    inventory: str | None = None  # the -i argument, relative to root
    playbook: str | None = None  # relative to root
    files: tuple[str, ...] = ()  # everything written, relative to root
    problem: str | None = None

    @property
    def usable(self) -> bool:
        return self.problem is None


def reconstruct(documents: Iterable[dict], root: Path, fabric: str | None = None) -> Reconstructed:
    """Write repo' for one Fabric under `root`, which must be empty or absent.

    `fabric` names the Fabric when the documents hold several. Its inputs are the
    FabricInputs its `spec.inputs` selects, and no others. Nothing is written unless
    everything can be.
    """
    documents = list(documents)
    fabrics = [doc for doc in documents if doc.get("kind") == FABRIC]
    if fabric is not None:
        fabrics = [doc for doc in fabrics if _name(doc) == fabric]
    if len(fabrics) != 1:
        named = ", ".join(_name(doc) for doc in fabrics) or "none"
        return Reconstructed(root, problem=f"one Fabric wanted, found {len(fabrics)}: {named}")
    spec = fabrics[0].get("spec") or {}

    if root.exists() and (not root.is_dir() or any(root.iterdir())):
        return Reconstructed(root, problem=f"{root} is not an empty directory")

    problems: list[str] = []
    planned: dict[PurePosixPath, str] = {}

    def plan(path: PurePosixPath, text: str, what: str) -> None:
        if path in planned:
            problems.append(f"{what}: {path} is written by another object already")
        planned[path] = text

    config = spec.get("ansibleCfg") or {}
    inventory, problem = _inventory(AnsibleCfg(sections=config))
    if problem:
        return Reconstructed(root, problem=problem)
    if not spec.get("play"):
        problems.append("the Fabric carries no play")
    if not spec.get("groups"):
        problems.append("the Fabric carries no inventory")

    if config:
        plan(PurePosixPath(CONFIG_FILE), _cfg_text(config), "ansibleCfg")
    plan(inventory, ansible_yaml.dump(spec.get("groups") or {}), "groups")
    plan(PurePosixPath(PLAYBOOK), ansible_yaml.dump([spec.get("play") or {}]), "play")

    selector = (spec.get("inputs") or {}).get("matchLabels") or {}
    if not selector:
        # an empty selector selects everything, other fabrics' inputs included
        problems.append("the Fabric selects no inputs: spec.inputs.matchLabels is empty")
    bases = {INVENTORY_ROOT: inventory.parent, PLAYBOOK_ROOT: _ROOT}

    for doc in documents:
        labels = (doc.get("metadata") or {}).get("labels") or {}
        if doc.get("kind") != FABRIC_INPUT or not selector or not selector.items() <= labels.items():
            continue
        what = _name(doc)
        input_spec = doc.get("spec") or {}

        beside = input_spec.get("beside")
        if beside not in bases:
            problems.append(f"{what}: beside is {beside!r}, not inventory or playbook")
            continue
        if beside == PLAYBOOK_ROOT and inventory.parent == _ROOT:
            # One directory cannot be both roots and keep their two precedences.
            problems.append(f"{what}: beside the playbook, and the inventory sits there too")
            continue

        design = input_spec.get("design")
        if not isinstance(design, dict):
            problems.append(f"{what}: design is {type(design).__name__}, not a mapping")
            continue

        applies = input_spec.get("appliesTo") or {}
        if "group" in applies:
            targets = [(GROUP_VARS, applies["group"])]
        elif isinstance(applies.get("hosts"), list):
            targets = [(HOST_VARS, host) for host in applies["hosts"]]
        else:
            problems.append(f"{what}: appliesTo names neither a group nor hosts")
            continue

        for vars_dir, scope in targets:
            if not isinstance(scope, str) or not scope or "/" in scope or scope in (".", ".."):
                problems.append(f"{what}: {scope!r} cannot be a file name")
                continue
            plan(bases[beside] / vars_dir / f"{scope}.yml", ansible_yaml.dump(design), what)

    if problems:
        return Reconstructed(root, problem="; ".join(problems))

    root.mkdir(parents=True, exist_ok=True)
    for path, text in planned.items():
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    return Reconstructed(
        root,
        inventory=str(inventory),
        playbook=PLAYBOOK,
        files=tuple(str(path) for path in planned),
    )


def _inventory(config: AnsibleCfg) -> tuple[PurePosixPath, str | None]:
    """Where the inventory file goes, or why it cannot go anywhere."""
    named = config.inventory
    if not named:
        return PurePosixPath(DEFAULT_INVENTORY), None
    if len(named) > 1:
        # one groups tree was carried, and the other sources would be missing
        return _ROOT, f"ansible.cfg names several inventories: {', '.join(named)}"

    path = PurePosixPath(named[0])
    if path.is_absolute() or named[0].startswith("~") or ".." in path.parts:
        return _ROOT, f"ansible.cfg names an inventory outside the repository: {named[0]}"
    if path.suffix not in INVENTORY_SUFFIXES:
        path = path / "hosts.yml"
    return path, None


def _cfg_text(sections: dict[str, dict[str, str]]) -> str:
    # The settings ansiblecfg reads with, so that what it reads is what is written.
    parser = configparser.ConfigParser(interpolation=None, default_section="")
    parser.read_dict(sections)
    out = io.StringIO()
    parser.write(out)
    return out.getvalue()


def _name(doc: dict) -> str:
    return str((doc.get("metadata") or {}).get("name", "<unnamed>"))
