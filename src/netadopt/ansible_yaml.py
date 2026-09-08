"""Reading YAML the way Ansible reads it.

Ansible's YAML is not plain YAML: it carries `!vault` and `!unsafe`, and
`yaml.safe_load` raises on both. Dropping the tag instead would be worse than
raising -- `yq -o=json` does exactly that, and a vault value comes out looking like
an ordinary string that nothing downstream can tell apart.

The shapes below are Ansible's own, not ours: `ansible-inventory --list` prints
{"__ansible_vault": ...} and {"__ansible_unsafe": ...} for the same values, so the
reader that runs Ansible and the reader that opens the file agree.

Any other tag still raises, with its name and the line it is on. An unknown tag is
something we have not understood, and understanding it later is cheap; a value
silently changed in transit is not detectable at all.
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
    # The ciphertext, whole. No password is needed to carry it, and none is asked
    # for: a prompt in the middle of a report is not something we do.
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
        # PyYAML reports the stream's name in its error marks, so this is what puts
        # his filename into the message instead of "<unicode string>".
        stream.name = str(path)
    return yaml.load(stream, AnsibleLoader)
