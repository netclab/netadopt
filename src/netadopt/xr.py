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
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import PurePosixPath

import yaml

from netadopt.pools import PoolFile
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

# The key of the vault password in its Secret.
VAULT_SECRET_KEY = "password"

# Every variable ansible-core 2.21's connection and become plugins read a password or
# passphrase from. Collections may add their own; these are Ansible's.
PASSWORD_VARS = frozenset(
    {
        "ansible_password",
        "ansible_ssh_pass",
        "ansible_ssh_password",
        "ansible_become_pass",
        "ansible_become_password",
        "ansible_su_pass",
        "ansible_sudo_pass",
        "ansible_runas_pass",
        "ansible_winrm_pass",
        "ansible_winrm_password",
        "ansible_private_key_passphrase",
        "ansible_ssh_private_key_passphrase",
        "ansible_psrp_certificate_key_password",
    }
)

# RFC 1123: lower case, digits and "-", starting and ending alphanumeric.
_NOT_NAME = re.compile(r"[^a-z0-9-]+")

CONFIG_MAP = "ConfigMap"
# What a ConfigMap key may hold.
_NOT_KEY = re.compile(r"[^-._a-zA-Z0-9]+")


@dataclass(frozen=True)
class Emitted:
    documents: tuple[dict, ...] = ()
    notes: tuple[str, ...] = field(default_factory=tuple)
    problems: tuple[str, ...] = ()  # objects refused, which fail the run


def fabric(
    name: str,
    play: dict,
    groups: dict,
    ansible_cfg: dict,
    vault_password: bool = False,
    pools: list[dict] | None = None,
) -> dict:
    """The Fabric object: one play, the inventory and ansible.cfg, each as written.

    `name` is already a Kubernetes name, and the FabricInputs emitted with it carry it
    in the label `spec.inputs` selects. `vault_password` is set when ansible.cfg names
    a vault password file: the password is never carried, and `spec.vaultPassword`
    names the Secret holding it, in the Fabric's own namespace. `pools` are the
    entries `pool_objects` makes.
    """
    spec: dict = {"inputs": {"matchLabels": {FABRIC_LABEL: name}}}
    if vault_password:
        spec["vaultPassword"] = {
            "secretRef": {"name": vault_secret(name), "key": VAULT_SECRET_KEY}
        }
    if pools:
        spec["pools"] = pools
    spec |= {"play": play, "ansibleCfg": ansible_cfg, "groups": groups}
    return {"apiVersion": API_VERSION, "kind": FABRIC, "metadata": {"name": name}, "spec": spec}


def vault_secret(fabric_name: str) -> str:
    """The name of the Secret a Fabric's vault password is kept in."""
    return f"{fabric_name}-vault"


def pool_objects(fabric_name: str, files: Iterable[PoolFile]) -> tuple[list[dict], dict | None]:
    """`spec.pools`, and the ConfigMap it names: every pool file, one key each.

    The key is the file's name, and the entry says where the file goes back: a
    ConfigMap key cannot hold a "/".
    """
    name = f"{fabric_name}-pools"
    entries: list[dict] = []
    data: dict[str, str] = {}
    for file in files:
        base = _NOT_KEY.sub("-", PurePosixPath(file.path).name)
        key, number = base, 2
        while key in data:
            key, number = f"{number}-{base}", number + 1
        data[key] = file.text
        entries.append(
            {"configMapRef": {"name": name, "key": key}, "path": file.path, "beside": file.beside}
        )
    if not entries:
        return [], None
    config_map = {
        "apiVersion": "v1",
        "kind": CONFIG_MAP,
        "metadata": {"name": name, "labels": {FABRIC_LABEL: fabric_name}},
        "data": data,
    }
    return entries, config_map


def plain_passwords(documents: tuple[dict, ...]) -> list[tuple[str, list[str]]]:
    """Per object, the password variables it carries as plain text, as the repository has them.

    A `!vault` value is ciphertext and a `{{ ... }}` value an expression; neither is a
    password in plain text.
    """
    found = []
    for doc in documents:
        names = sorted(_plain(doc.get("spec") or {}))
        if names:
            found.append((f"{doc.get('kind')} {(doc.get('metadata') or {}).get('name')}", names))
    return found


def _plain(node: object) -> set[str]:
    names: set[str] = set()
    if isinstance(node, dict):
        for key, value in node.items():
            if key in PASSWORD_VARS and isinstance(value, str) and value and "{{" not in value:
                names.add(key)
            else:
                names |= _plain(value)
    elif isinstance(node, list):
        for item in node:
            names |= _plain(item)
    return names


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


class _Dumper(yaml.SafeDumper):
    """SafeDumper writing a string of several lines as a literal block, line by line."""


def _str(dumper: _Dumper, data: str) -> yaml.Node:
    # A quoted scalar can only show a line break as an empty line.
    style = "|" if "\n" in data else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", data, style=style)


_Dumper.add_representer(str, _str)


def to_yaml(documents: tuple[dict, ...]) -> str:
    """The documents as one stream, ready for `kubectl apply -f -`; long lines never folded."""
    return yaml.dump_all(
        documents, Dumper=_Dumper, sort_keys=False, explicit_start=True, width=float("inf")
    )


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
