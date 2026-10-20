"""Keys AVD has removed from its inputs, found in the vars files as written.

pyavd validates against the schema of the AVD netadopt pins. One file is one scope's
part of the inputs, so most of what pyavd finds there says nothing: a required key
another file holds, a value Jinja has yet to render. A key AVD removed says the
repository was written for an older AVD, and the render fails on it.
"""

from __future__ import annotations

from pathlib import Path

from netadopt.varfiles import VarFiles

# How pyavd words it: "The input data model 'name_servers' was removed in AVD version 6.0.0."
REMOVED = "was removed in AVD version"


def removed_keys(found: VarFiles) -> tuple[tuple[Path, str], ...]:
    """Each vars file holding a key AVD removed, and what pyavd says of the key."""
    from pyavd import validate_inputs  # the avd extra's, imported only when asked

    said: list[tuple[Path, str]] = []
    for file in found.files:
        if not file.data:
            continue
        violations = validate_inputs(file.data).validation_result.violations
        said.extend((file.path, v.message) for v in violations if REMOVED in v.message)
    return tuple(said)
