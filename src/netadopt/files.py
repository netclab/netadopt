"""Files the design names by path, as the repository has them: AVD's templates.

AVD opens a template named in its inputs -- `ip_addressing`, `interface_descriptions`,
`eos_designs_custom_templates`, `custom_templates` -- through Ansible's search path,
each entry tried as `<entry>/templates` first and then as itself
(`compile_searchpath`); for the playbook, that is its own directory. So every string in
the design that is a relative path to a file there is carried, whatever key holds it.
A pool file is carried as a pool, and an absolute path belongs to a machine, not to the
repository.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from netadopt.varfiles import PLAYBOOK_ROOT, VarFiles

TEMPLATES = "templates"
TEMPLATE_SUFFIXES = (".j2", ".jinja", ".jinja2")
# A template that pulls in another one, which no design names.
_PULLS_IN = re.compile(r"\{%-?\s*(include|import|from|extends)\b")
# Longer than any path, and a path holds no line break.
_LONGEST = 1024


@dataclass(frozen=True)
class NamedFile:
    path: str  # where it sits, relative to the playbook's directory
    beside: str  # PLAYBOOK_ROOT, the root it goes back into
    text: str


@dataclass(frozen=True)
class Named:
    files: tuple[NamedFile, ...] = ()
    notes: tuple[str, ...] = ()


def find_named_files(
    found: VarFiles, play: dict, groups: dict, repo: Path, playbook: str
) -> Named:
    """Every file a string in the vars, the play or the inventory names, read.

    Templates that could not be carried are in the notes.
    """
    scopes = [file.data for file in found.files if file.data]
    if isinstance(play.get("vars"), dict):
        scopes.append(play["vars"])
    pools = {str(_pools_file(data)) for data in scopes if _pools_file(data)}
    base = (repo / playbook).parent

    files: dict[str, NamedFile] = {}
    notes: list[str] = []
    for value in dict.fromkeys(value for scope in (*scopes, groups) for value in _strings(scope)):
        if value in pools:
            continue
        pure = PurePosixPath(value)
        template = value.endswith(TEMPLATE_SUFFIXES)
        if "{{" in value:
            if template:
                notes.append(f"{value}: a template named by an expression -- not carried")
            continue
        if pure.is_absolute() or value.startswith("~") or ".." in pure.parts:
            if template:
                notes.append(f"{value}: a template outside the repository -- not carried")
            continue
        at = next((str(path) for path in (PurePosixPath(TEMPLATES) / pure, pure) if _is_file(base / path)), None)
        if at is None:
            if template:
                notes.append(f"{value}: no such template beside the playbook -- not carried")
            continue
        if at in files:
            continue
        try:
            text = (base / at).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as err:
            notes.append(f"{at}: could not be read -- {err}")
            continue
        if _PULLS_IN.search(text):
            notes.append(f"{at}: pulls in other templates, which no design names -- those are not carried")
        files[at] = NamedFile(path=at, beside=PLAYBOOK_ROOT, text=text)
    return Named(files=tuple(files.values()), notes=tuple(notes))


def _strings(node: object):
    if isinstance(node, dict):
        for value in node.values():
            yield from _strings(value)
    elif isinstance(node, list):
        for value in node:
            yield from _strings(value)
    elif isinstance(node, str) and node and "\n" not in node and len(node) <= _LONGEST:
        yield node


def _pools_file(data: dict) -> object:
    numbering = data.get("fabric_numbering")
    node_id = numbering.get("node_id") if isinstance(numbering, dict) else None
    return node_id.get("pools_file") if isinstance(node_id, dict) else None


def _is_file(path: Path) -> bool:
    try:
        return path.is_file()
    except OSError:  # a name too long for the file system
        return False
