"""Reading and writing YAML the way Ansible does.

`!vault` and `!unsafe` are Ansible's, and `yaml.safe_load` raises on both. Here they
become the shapes `ansible-inventory --list` prints: {"__ansible_vault": ...} and
{"__ansible_unsafe": ...}. `dump` writes those shapes back as the tags.

Any other tag still raises, with its name and the line it is on.
"""

from __future__ import annotations

import io
from pathlib import Path

import yaml

VAULT_KEY = "__ansible_vault"
UNSAFE_KEY = "__ansible_unsafe"


class AnsibleLoader(yaml.SafeLoader):
    """SafeLoader plus the two tags Ansible puts in ordinary vars files."""


def _vault(loader: AnsibleLoader, node: yaml.Node) -> dict[str, str]:
    # the ciphertext, whole -- carrying it needs no password
    return {VAULT_KEY: loader.construct_scalar(node)}


def _unsafe(loader: AnsibleLoader, node: yaml.Node) -> dict[str, str]:
    # !unsafe means "do not template this". Losing the marker would turn a literal
    # {{ ... }} into something Ansible would try to resolve.
    return {UNSAFE_KEY: loader.construct_scalar(node)}


AnsibleLoader.add_constructor("!vault", _vault)
AnsibleLoader.add_constructor("!unsafe", _unsafe)


def load(text: str, path: Path | str | None = None) -> object:
    """Parse one document. Raises yaml.YAMLError, naming `path` in the message."""
    stream = io.StringIO(text)
    if path is not None:
        # PyYAML reports the stream name in its error marks: the filename, not
        # "<unicode string>".
        stream.name = str(path)
    return yaml.load(stream, AnsibleLoader)


class AnsibleDumper(yaml.SafeDumper):
    """SafeDumper writing the two shapes `load` makes back as Ansible's tags."""

    def ignore_aliases(self, data: object) -> bool:
        # an object met twice is written twice, never as &anchor and *alias
        return True


def _mapping(dumper: AnsibleDumper, data: dict) -> yaml.Node:
    if len(data) == 1:
        ((key, value),) = data.items()
        if key == VAULT_KEY and isinstance(value, str):
            return dumper.represent_scalar("!vault", value, style="|")
        if key == UNSAFE_KEY and isinstance(value, str):
            return dumper.represent_scalar("!unsafe", value)
    return dumper.represent_dict(data)


AnsibleDumper.add_representer(dict, _mapping)


def dump(data: object) -> str:
    """One document, keys in their own order, long strings never folded."""
    return yaml.dump(
        data,
        Dumper=AnsibleDumper,
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
        width=float("inf"),
    )
