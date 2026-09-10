"""Fabric and FabricInput objects, built from what was read.

One FabricInput per scope, not per file: a `group_vars/FABRIC/` directory is several files
and one namespace, so its keys arrive in one `design`. `design` is the repository's
own content -- transport keys included, nothing lifted out, nothing renamed.

Names are spelled twice on purpose. `metadata.name` is RFC 1123, because Kubernetes
requires it; `spec.appliesTo` keeps the repository's spelling, because Ansible
resolves against that.

Every FabricInput carries the label `avd.netclab.dev/fabric`, naming the Fabric it was
emitted with, and its name begins with that Fabric's -- so two fabrics share a
namespace without taking each other's inputs. A Fabric picks its inputs by that label,
through `spec.inputs`; a second Fabric pointed at the same label shares them.

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
FABRIC = "Fabric"
FABRIC_INPUT = "FabricInput"

# The label a Fabric selects its FabricInputs by.
FABRIC_LABEL = "avd.netclab.dev/fabric"
# The longest label value Kubernetes accepts; a name may be longer, a label may not.
LABEL_MAX = 63

# The longest object name Kubernetes accepts.
NAME_MAX = 253

# RFC 1123: lower case, digits and "-", starting and ending alphanumeric.
_NOT_NAME = re.compile(r"[^a-z0-9-]+")


@dataclass(frozen=True)
class Emitted:
    documents: tuple[dict, ...] = ()
    notes: tuple[str, ...] = field(default_factory=tuple)
    problems: tuple[str, ...] = ()  # objects refused, which fail the run


def fabric(name: str, play: dict, groups: dict, ansible_cfg: dict) -> dict:
    """The Fabric object: one play, the inventory and ansible.cfg, each as written.

    `name` is already a Kubernetes name, and the FabricInputs emitted with it carry it
    in the label `spec.inputs` selects.
    """
    return {
        "apiVersion": API_VERSION,
        "kind": FABRIC,
        "metadata": {"name": name},
        "spec": {
            "inputs": {"matchLabels": {FABRIC_LABEL: name}},
            "play": play,
            "ansibleCfg": ansible_cfg,
            "groups": groups,
        },
    }


def fabric_inputs(found: VarFiles, fabric_name: str) -> Emitted:
    """One FabricInput per scope, in the order the files were found.

    `fabric_name` is the Fabric they are emitted with, already a Kubernetes name.
    """
    designs: dict[tuple[str, str, str], dict] = {}
    notes: list[str] = []
    problems: list[str] = []

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
        wanted = _name(fabric_name, vars_dir, scope)
        name = _free(wanted, root, taken)
        if name != wanted:
            # Same scope under both roots, or two spellings that are one RFC 1123
            # name -- FABRIC and fabric, DC1.POD1 and DC1-POD1.
            notes.append(f"{scope}: {wanted} is taken, so this object becomes {name}")
        taken.add(name)
        if len(name) > NAME_MAX:
            problems.append(
                f"{scope}: not emitted -- {name} is {len(name)} characters, "
                f"a name holds {NAME_MAX}"
            )
            continue

        applies = {"group": scope} if vars_dir == GROUP_VARS else {"hosts": [scope]}
        documents.append(
            {
                "apiVersion": API_VERSION,
                "kind": FABRIC_INPUT,
                "metadata": {"name": name, "labels": {FABRIC_LABEL: fabric_name}},
                # `beside` is the root this object is reconstructed into -- the only
                # thing Ansible's two group_vars levels turn on. Where the two roots
                # are one directory the choice has no effect: a scope has nothing to
                # collide with there. Always written, so that a missing field never
                # has to be read as a default.
                "spec": {"appliesTo": applies, "beside": root, "design": design},
            }
        )
    return Emitted(documents=tuple(documents), notes=tuple(notes), problems=tuple(problems))


def to_yaml(documents: tuple[dict, ...]) -> str:
    """The documents as one stream, ready for `kubectl apply -f -`."""
    return yaml.safe_dump_all(documents, sort_keys=False, explicit_start=True)


def rfc1123(name: str) -> str:
    """A Kubernetes object name from a group or host name of any spelling.

    Never shortened: a cut name can equal another, so the length is checked where the
    name is used, and a name too long is refused there.
    """
    return _NOT_NAME.sub("-", name.lower()).strip("-")


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


def _name(fabric_name: str, vars_dir: str, scope: str) -> str:
    if vars_dir == GROUP_VARS:
        return rfc1123(f"{fabric_name}-{scope}")
    # host_vars/dc1-leaf1a.yml and a group of the same name are two scopes, and a
    # prefix is what keeps them two objects.
    return rfc1123(f"{fabric_name}-host-{scope}")
