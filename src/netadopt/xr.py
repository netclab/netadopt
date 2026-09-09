"""FabricInput objects, built from the vars files as read.

One object per scope, not per file: a `group_vars/FABRIC/` directory is several files
and one namespace, so its keys arrive in one `design`. `design` is the repository's
own content -- transport keys included, nothing lifted out, nothing renamed.

Names are spelled twice on purpose. `metadata.name` is RFC 1123, because Kubernetes
requires it; `spec.appliesTo` keeps the repository's spelling, because Ansible
resolves against that.

A group_vars file beside the inventory and one beside the playbook are different
precedence levels, so they stay in different objects and never merge here.
`spec.beside` is the root the object goes back to.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import yaml

from netadopt.varfiles import GROUP_VARS, VarFiles

API_VERSION = "avd.netclab.dev/v1alpha1"
FABRIC_INPUT = "FabricInput"

# RFC 1123: lower case, digits and "-", starting and ending alphanumeric, 253 max.
_NOT_NAME = re.compile(r"[^a-z0-9-]+")


@dataclass(frozen=True)
class Emitted:
    documents: tuple[dict, ...] = ()
    notes: tuple[str, ...] = field(default_factory=tuple)


def fabric_inputs(found: VarFiles) -> Emitted:
    """One FabricInput per scope, in the order the files were found."""
    designs: dict[tuple[str, str, str], dict] = {}
    notes: list[str] = []

    for file in found.files:
        if file.data is None:
            notes.append(f"{file.path}: not emitted -- {file.problem}")
            continue

        design = designs.setdefault((file.root, file.vars_dir, file.scope), {})
        for key in file.data:
            if key in design:
                # Ansible merges a group_vars directory in file order, so a key
                # written twice in one scope means the later file decides.
                notes.append(f"{file.path}: {key} was already set for {file.scope}")
        design.update(file.data)

    documents = []
    taken: set[str] = set()
    for (root, vars_dir, scope), design in designs.items():
        wanted = _name(vars_dir, scope)
        name = _free(wanted, root, taken)
        if name != wanted:
            # Same scope under both roots, or two spellings that are one RFC 1123
            # name -- FABRIC and fabric, DC1.POD1 and DC1-POD1.
            notes.append(f"{scope}: {wanted} is taken, so this object becomes {name}")
        taken.add(name)

        applies = {"group": scope} if vars_dir == GROUP_VARS else {"hosts": [scope]}
        documents.append(
            {
                "apiVersion": API_VERSION,
                "kind": FABRIC_INPUT,
                "metadata": {"name": name},
                # `beside` is the root this object is reconstructed into -- the only
                # thing Ansible's two group_vars levels turn on. Where the two roots
                # are one directory the choice has no effect: a scope has nothing to
                # collide with there. Always written, so that a missing field never
                # has to be read as a default.
                "spec": {"appliesTo": applies, "beside": root, "design": design},
            }
        )
    return Emitted(documents=tuple(documents), notes=tuple(notes))


def to_yaml(documents: tuple[dict, ...]) -> str:
    """The documents as one stream, ready for `kubectl apply -f -`."""
    return yaml.safe_dump_all(documents, sort_keys=False, explicit_start=True)


def rfc1123(name: str) -> str:
    """A Kubernetes object name from a group or host name of any spelling."""
    spelled = _NOT_NAME.sub("-", name.lower()).strip("-")
    return spelled[:253].rstrip("-")


def _free(name: str, root: str, taken: set[str]) -> str:
    """`name`, or the first spelling of it that no other object has."""
    if name not in taken:
        return name
    # The root is what usually tells two objects of one name apart; the number is for
    # the case it does not, because a duplicate name is an object overwritten in the
    # cluster rather than an error.
    candidate = f"{name}-{root}"
    number = 2
    while candidate in taken:
        candidate = f"{name}-{root}-{number}"
        number += 1
    return candidate


def _name(vars_dir: str, scope: str) -> str:
    if vars_dir == GROUP_VARS:
        return rfc1123(scope)
    # host_vars/dc1-leaf1a.yml and a group of the same name are two scopes, and a
    # prefix is what keeps them two objects.
    return f"host-{rfc1123(scope)}"
