"""The files AVD's pool manager keeps its assignments in, as the repository has them.

With `fabric_numbering.node_id.algorithm: pool_manager`, AVD assigns node IDs from
pools and writes every assignment back to a file the repository versions: state to
keep, not output. pyavd opens `pools_file` as the path it is given, relative to the
directory Ansible runs in. Without one the file is
`<root_dir>/intended/data/<fabric_name>-ids.yml`, and `root_dir` is the inventory's
directory unless the vars set it to the playbook's.

Only those spellings are followed. Any other `pools_file`, `root_dir`, `output_dir` or
`output_dir_name` is named in a note, and the file is not guessed.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from netadopt.varfiles import INVENTORY_ROOT, PLAYBOOK_ROOT, VarFiles

POOL_MANAGER = "pool_manager"
# The role's default output directory under root_dir.
OUTPUT_DIR_NAME = "intended"
# root_dir as the role default and the corpus write it, and the root each points at.
_ROOT_DIRS = {"{{inventory_dir}}": INVENTORY_ROOT, "{{playbook_dir}}": PLAYBOOK_ROOT}


@dataclass(frozen=True)
class PoolFile:
    path: str  # relative to `beside`
    beside: str  # INVENTORY_ROOT | PLAYBOOK_ROOT, the root it goes back into
    text: str


@dataclass(frozen=True)
class Pools:
    files: tuple[PoolFile, ...] = ()
    notes: tuple[str, ...] = ()


def find_pools(found: VarFiles, play: dict, repo: Path) -> Pools:
    """Every pool file the vars point pool_manager at, read; what could not be, in notes."""
    scopes = [file.data for file in found.files if file.data]
    if isinstance(play.get("vars"), dict):
        scopes.append(play["vars"])
    node_ids = [_node_id(data) for data in scopes]
    if not any(node_id.get("algorithm") == POOL_MANAGER for node_id in node_ids):
        return Pools()

    dirs = dict(found.roots)
    dirs.setdefault(PLAYBOOK_ROOT, dirs.get(INVENTORY_ROOT, repo))
    dirs.setdefault(INVENTORY_ROOT, dirs[PLAYBOOK_ROOT])

    notes: list[str] = []
    wanted: list[tuple[str, str, Path]] = []  # path, beside, where the source keeps it
    named = [node_id["pools_file"] for node_id in node_ids if "pools_file" in node_id]
    for path in dict.fromkeys(str(value) for value in named):
        pure = PurePosixPath(path)
        if "{{" in path:
            notes.append(f"pools_file {path}: an expression -- the pool file is not carried")
        elif pure.is_absolute() or path.startswith("~") or ".." in pure.parts:
            notes.append(f"pools_file {path}: outside the repository -- not carried")
        else:
            # Relative to where Ansible runs: the repository, and the root of repo'.
            wanted.append((path, PLAYBOOK_ROOT, repo / path))
    if not named:
        default, why = _default(scopes)
        if why:
            notes.append(f"pool_manager without pools_file, and {why} -- the pool file is not carried")
        wanted += [(path, beside, dirs[beside] / path) for path, beside in default]

    files: list[PoolFile] = []
    missing: list[str] = []
    for path, beside, source in wanted:
        if not source.is_file():
            missing.append(path)
            continue
        try:
            files.append(PoolFile(path=path, beside=beside, text=source.read_text(encoding="utf-8")))
        except (OSError, UnicodeDecodeError) as err:
            notes.append(f"pool file {path}: could not be read -- {err}")
    # With no pools_file every fabric_name is a candidate, and only one need exist.
    if missing and (named or not files):
        shown = ", ".join(missing[:3]) + (f" and {len(missing) - 3} more" if len(missing) > 3 else "")
        notes.append(f"pool_manager is set, and there is no pool file at {shown} -- AVD assigns node IDs afresh")
    return Pools(files=tuple(files), notes=tuple(notes))


def _node_id(data: dict) -> dict:
    numbering = data.get("fabric_numbering")
    node_id = numbering.get("node_id") if isinstance(numbering, dict) else None
    return node_id if isinstance(node_id, dict) else {}


def _default(scopes: list[dict]) -> tuple[list[tuple[str, str]], str | None]:
    """The default pool files, one per fabric_name; or why they cannot be told."""
    if any("output_dir" in data for data in scopes):
        return [], "output_dir is set"
    names = {data["output_dir_name"] for data in scopes if "output_dir_name" in data}
    if names - {OUTPUT_DIR_NAME}:
        return [], f"output_dir_name is {min(map(str, names - {OUTPUT_DIR_NAME}))}"
    roots = {str(data["root_dir"]).replace(" ", "") for data in scopes if "root_dir" in data}
    if roots - _ROOT_DIRS.keys():
        return [], f"root_dir is {min(roots - _ROOT_DIRS.keys())}"
    besides = {_ROOT_DIRS[root] for root in roots}
    if len(besides) > 1:
        return [], "root_dir is set both ways"
    beside = besides.pop() if besides else INVENTORY_ROOT

    fabric_names = sorted(
        {data["fabric_name"] for data in scopes if isinstance(data.get("fabric_name"), str)}
    )
    plain = [name for name in fabric_names if "{{" not in name]
    if not plain:
        return [], "no fabric_name names it"
    return [(f"{OUTPUT_DIR_NAME}/data/{name}-ids.yml", beside) for name in plain], None
