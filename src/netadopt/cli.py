"""The command line.

One subcommand per ecosystem, present whether or not its extra is installed -- so
nothing an ecosystem needs is imported at start-up.
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass, field
from importlib.metadata import version
from pathlib import Path
from typing import Annotated

import typer
from rich import box
from rich.console import Console
from rich.padding import Padding
from rich.table import Table
from rich.text import Text

from netadopt import AVD_TESTED
from netadopt.ansible import Ansible, resolve_ansible
from netadopt.ansiblecfg import AnsibleCfg, read_ansible_cfg
from netadopt.files import Named, find_named_files
from netadopt.inventory import Inventory, read_inventory
from netadopt.inventoryfile import read_inventory_file
from netadopt.playbook import IMPORT_ROLE, Play, Playbook, find_playbooks, read_playbook
from netadopt.pools import Pools, find_pools
from netadopt.varfiles import GROUP_VARS, VarFiles, read_vars
from netadopt.xr import (
    CONFIG_MAP,
    CONFIG_MAP_MAX,
    LABEL_MAX,
    VAULT_SECRET_KEY,
    Emitted,
    config_map_size,
    fabric,
    fabric_inputs,
    named_file_objects,
    plain_passwords,
    pool_objects,
    rfc1123,
    to_yaml,
    vault_secret,
)

app = typer.Typer(
    help="Read a network-automation repository and report what is in it.",
    no_args_is_help=True,
    add_completion=False,
)


def _version(value: bool) -> None:
    if value:
        typer.echo(f"netadopt {version('netadopt')} (avd: tested on {AVD_TESTED})")
        raise typer.Exit(0)


@app.callback()
def _root(
    _: Annotated[
        bool,
        typer.Option("--version", callback=_version, is_eager=True, help="print the version"),
    ] = False,
) -> None:
    pass


avd = typer.Typer(help="An Arista AVD repository.", no_args_is_help=True)
app.add_typer(avd, name="avd")

Repo = Annotated[
    Path,
    typer.Argument(exists=True, file_okay=False, dir_okay=True, help="the repository directory"),
]
# One playbook is named; its plays are not -- a playbook can hold two plays with the
# same name, as AVD's twodc scenario does.
PlaybookOption = Annotated[
    str | None, typer.Option(help="playbook to read, relative to the repository")
]
# Only needed when no ansible.cfg names it. Ansible's own -i, passed through
# unchanged: a file, or a directory of them.
InventoryOption = Annotated[
    str | None, typer.Option(metavar="SOURCE", help="inventory file or directory, as -i")
]
AnsibleOption = Annotated[
    str | None, typer.Option(metavar="EXE", help="ansible-playbook to use, overriding PATH")
]
# By position, as the report prints it.
PlayOption = Annotated[int, typer.Option(metavar="N", help="play to carry, by its index")]
NameOption = Annotated[
    str | None, typer.Option(help="the Fabric's name; default the repository directory's")
]


@dataclass(frozen=True)
class Adoption:
    """One run's Fabric: the play it carries, the files that go with it, or why there is none."""

    name: str
    playbook: Playbook | None = None
    play: Play | None = None
    vault_file: str | None = None  # as ansible.cfg names it; never carried
    pools: Pools = field(default_factory=Pools)
    named: Named = field(default_factory=Named)
    documents: tuple[dict, ...] = ()  # the Fabric, then the ConfigMaps it names
    problem: str | None = None  # why there is no Fabric

    @property
    def other_plays(self) -> tuple[Play, ...]:
        """The plays of the playbook this run does not carry."""
        if self.playbook is None or self.play is None:
            return ()
        return tuple(play for play in self.playbook.plays if play is not self.play)


def _adopt(
    repo: Path,
    playbook: str | None,
    source: str | None,
    index: int,
    name: str,
    config: AnsibleCfg,
    found: VarFiles,
) -> Adoption:
    """The Fabric named `name`, carrying play `index`, and the ConfigMaps of its files."""
    if playbook is None:
        return Adoption(name, problem="no playbook named -- pass --playbook")
    read = read_playbook(repo, playbook)
    if not read.usable:
        return Adoption(name, problem=read.problem)
    if not 0 <= index < len(read.plays):
        held = len(read.plays)
        return Adoption(name, read, problem=f"no play [{index}] -- {read.path.name} holds {held}")
    if not config.usable:
        return Adoption(name, read, problem=config.problem)
    written = read_inventory_file(repo, source)
    if not written.usable:
        return Adoption(name, read, problem=written.problem)

    play = read.plays[index]
    pools = find_pools(found, play.raw, repo)
    named = find_named_files(found, play.raw, written.groups, repo, playbook)
    pool_entries, pools_map = pool_objects(name, pools.files)
    file_entries, files_map = named_file_objects(name, named.files)
    vault_file = config.vault_password_file
    document = fabric(
        name,
        play.raw,
        written.groups,
        config.sections,
        vault_password=bool(vault_file),
        pools=pool_entries,
        files=file_entries,
    )
    config_maps = tuple(doc for doc in (pools_map, files_map) if doc)
    return Adoption(
        name,
        read,
        play,
        vault_file=vault_file,
        pools=pools,
        named=named,
        documents=(document, *config_maps),
    )


@avd.command()
def report(
    repo: Repo,
    playbook: PlaybookOption = None,
    inventory: InventoryOption = None,
    ansible: AnsibleOption = None,
) -> None:
    """Say what is in the repository, and what emit carries of it."""
    found = resolve_ansible(ansible)
    config = read_ansible_cfg(repo)
    listed = read_inventory(found, repo, inventory)
    source = _inventory_source(inventory, config)
    var_files = read_vars(repo, source, playbook)
    read = read_playbook(repo, playbook) if playbook else None

    # What emit carries with no --play and no --name: play 0, under the directory's name.
    name = rfc1123(repo.resolve().name) or "fabric"
    inputs = fabric_inputs(var_files, name)
    adoption = _adopt(repo, playbook, source, 0, name, config, var_files) if playbook else None

    # Every part is reported before anything decides the run failed: an unreadable
    # inventory does not hide a readable playbook.
    console = _console()
    console.print(Text(repo.resolve().name, style="bold"))
    console.print()
    overview = [
        *_ansible_rows(found),
        _config_row(config),
        *_inventory_rows(listed),
        _vars_row(var_files),
        _playbook_row(repo, playbook, read),
    ]
    console.print(_grid(overview, label_style="bold"))
    if read is not None and read.usable:
        console.print()
        console.print(_plays_table(read))

    if adoption is not None:
        _print_section(console, "Carried", "green", _carried(adoption, inputs))
        _print_section(console, "Not carried", "red", _not_carried(repo, adoption, var_files, inputs))
        if adoption.vault_file:
            # on a line of its own, never wrapped, so that it can be copied
            command = _vault_secret_command(repo, adoption)
            console.print(f"    {command}", soft_wrap=True, markup=False)
    _print_section(console, "Warnings", "yellow", _warnings(repo, listed, var_files, inputs, adoption))

    if not found.usable:
        raise typer.Exit(1)  # no Ansible
    if read is None or not read.usable or not listed.usable or not config.usable:
        raise typer.Exit(2)  # no playbook named, or it, the inventory or ansible.cfg did not read
    raise typer.Exit(0)


@avd.command()
def emit(
    repo: Repo,
    playbook: PlaybookOption = None,
    inventory: InventoryOption = None,
    play: PlayOption = 0,
    name: NameOption = None,
) -> None:
    """Write the Fabric and FabricInput objects of the repository, as YAML.

    The objects go to stdout and everything else to stderr, so the stream pipes into
    `kubectl apply --server-side -f -` whether or not there was something to say.
    Server-side: a plain apply drops every explicit null in a map.
    """
    wanted = name or repo.resolve().name
    spelled = rfc1123(wanted)
    if not spelled:
        typer.echo(f"nothing emitted: no name can be spelled from {wanted!r} -- pass --name", err=True)
        raise typer.Exit(2)
    if len(spelled) > LABEL_MAX:
        # the name is also the value of the label a Fabric selects its inputs by
        typer.echo(
            f"nothing emitted: {spelled} is {len(spelled)} characters, a label value "
            f"holds {LABEL_MAX} -- pass a shorter --name",
            err=True,
        )
        raise typer.Exit(2)

    config = read_ansible_cfg(repo)
    source = _inventory_source(inventory, config)
    found = read_vars(repo, source, playbook)
    inputs = fabric_inputs(found, spelled)
    adoption = _adopt(repo, playbook, source, play, spelled, config, found)
    said = _emit_notes(repo, adoption)
    if name and spelled != name:
        said.insert(0, f"--name {name} is spelled {spelled}")

    documents = adoption.documents + inputs.documents
    too_big = [
        doc
        for doc in adoption.documents
        if doc.get("kind") == CONFIG_MAP and config_map_size(doc) > CONFIG_MAP_MAX
    ]
    if too_big:
        # a Fabric naming a ConfigMap the cluster refuses would be a Fabric half there
        for doc in too_big:
            typer.echo(
                f"nothing emitted: ConfigMap {doc['metadata']['name']} would hold "
                f"{config_map_size(doc)} bytes, over the {CONFIG_MAP_MAX} a ConfigMap holds",
                err=True,
            )
        raise typer.Exit(2)
    if documents:
        typer.echo(to_yaml(documents), nl=False)

    unread = [f"{file.path}: not emitted -- {file.problem}" for file in found.problems]
    for line in (*said, *unread, *inputs.notes, *inputs.problems):
        typer.echo(line, err=True)
    plain = plain_passwords(documents)
    if plain:
        typer.echo(
            "carried as plain text, as the repository has them -- "
            "readable by anyone who can read these objects:",
            err=True,
        )
        for where, names in plain:
            typer.echo(f"  {where}: {', '.join(names)}", err=True)
    if not inputs.documents:
        typer.echo(f"no FabricInput: no group_vars or host_vars in {repo}", err=True)

    if not adoption.documents or found.problems or inputs.problems:
        raise typer.Exit(2)  # emitted, but a part of the model is missing
    raise typer.Exit(0)


def _emit_notes(repo: Path, adoption: Adoption) -> list[str]:
    """What emit says on stderr about the Fabric: the play it carries, what it leaves."""
    if adoption.problem:
        return [f"Fabric not emitted: {adoption.problem}"]

    said = [f"Fabric {adoption.name} <- {_label(adoption.play)}"]
    # Another play is another run, and it needs its own --name: under the same one it
    # would replace this Fabric in the cluster.
    for other in adoption.other_plays:
        said.append(
            f"{_label(other)}: not carried -- carry it with --play {other.index} and its own --name"
        )
    if adoption.vault_file:
        said.append(
            f"not carried: the vault password file {adoption.vault_file}, named by ansible.cfg -- "
            "spec.vaultPassword names the Secret, create it:"
        )
        said.append(f"  {_vault_secret_command(repo, adoption)}")
    return said + list(adoption.pools.notes) + list(adoption.named.notes)


def _vault_secret_command(repo: Path, adoption: Adoption) -> str:
    """The kubectl command creating the Secret spec.vaultPassword names."""
    named = adoption.vault_file or ""
    # a relative path is relative to ansible.cfg, at the repository's root
    where = named if named.startswith(("/", "~")) else str(repo / named)
    return (
        f"kubectl create secret generic {vault_secret(adoption.name)} "
        f"--from-file={VAULT_SECRET_KEY}={shlex.quote(where)}"
    )


def _inventory_source(given: str | None, config: AnsibleCfg) -> str | None:
    """The -i argument: as given, or the one inventory ansible.cfg names."""
    if given:
        return given
    named = config.inventory
    return named[0] if len(named) == 1 else None


def _label(play: Play) -> str:
    return f"play [{play.index}] {play.name}" if play.name else f"play [{play.index}]"


# The report. Each part of the repository is a row of plain strings; rich lays them out.


def _console() -> Console:
    """The report's console. Off a terminal rich prints no colour; the width is lifted
    too, because a path wrapped in two cannot be copied."""
    console = Console(highlight=False)
    if not console.is_terminal:
        console.width = 1000
    return console


def _grid(rows: list[tuple[str, ...]], label_style: str = "") -> Table:
    """Rows as borderless, aligned columns, the first one in `label_style`.

    Every cell is a Text, never markup: Ansible writes `[WARNING]`, and rich would
    read that as a style.
    """
    grid = Table.grid(padding=(0, 3))
    grid.add_column(style=label_style)
    for _ in range(max(len(row) for row in rows) - 1):
        grid.add_column()
    for row in rows:
        grid.add_row(*(Text(cell) for cell in row))
    return grid


def _print_section(console: Console, title: str, colour: str, rows: list[tuple[str, ...]]) -> None:
    """A titled, indented block of rows; nothing at all when there are no rows."""
    if not rows:
        return
    console.print()
    console.print(Text(title, style=f"bold {colour}"))
    console.print(Padding(_grid(rows), (0, 0, 0, 2), expand=False))


def _count(number: int, one: str) -> str:
    """`1 play`, `3 plays`."""
    return f"{number} {one if number == 1 else one + 's'}"


def _ansible_rows(found: Ansible) -> list[tuple[str, ...]]:
    if not found.usable:
        return [
            ("Ansible", "not usable", found.problem or ""),
            ("", "", 'install Ansible, or run: uvx "netadopt[ansible]" ...'),
        ]

    rows = [("Ansible", f"ansible-core {found.core}", found.exe or "")]
    if found.bundled:
        rows.append(("", "", "the bundled ansible-core, not the Ansible the repository is run with"))
    return rows


def _config_row(config: AnsibleCfg) -> tuple[str, ...]:
    if config.path is None:
        return ("Config", "no ansible.cfg", "")
    return ("Config", "ansible.cfg", config.problem or "")


def _inventory_rows(listed: Inventory) -> list[tuple[str, ...]]:
    where = listed.source or "named by ansible.cfg"
    if not listed.usable:
        return [("Inventory", where, listed.problem or "")]

    counted = f"{_count(len(listed.hosts), 'host')}, {_count(len(listed.groups), 'group')}"
    rows = [("Inventory", where, counted)]
    # Ansible's own stderr, under the numbers it explains.
    rows += [("", "", warning) for warning in listed.warnings]
    return rows


def _vars_row(found: VarFiles) -> tuple[str, ...]:
    if not found.files:
        return ("Variables", "none", "no group_vars or host_vars")

    groups = sum(1 for file in found.files if file.vars_dir == GROUP_VARS)
    hosts = len(found.files) - groups
    return ("Variables", _count(len(found.files), "file"), f"group_vars {groups}, host_vars {hosts}")


def _playbook_row(repo: Path, playbook: str | None, read: Playbook | None) -> tuple[str, ...]:
    if read is None:
        candidates = [candidate.path.name for candidate in find_playbooks(repo)]
        if not candidates:
            return ("Playbook", "not named", "no file here reads as a playbook")
        return ("Playbook", "not named", f"name one with --playbook: {', '.join(candidates)}")
    if not read.usable:
        return ("Playbook", playbook or "", read.problem or "")
    return ("Playbook", playbook or "", _count(len(read.plays), "play"))


def _plays_table(read: Playbook) -> Table:
    table = Table(box=box.SIMPLE_HEAD, show_edge=False, pad_edge=False)
    for header in ("#", "Play", "Hosts", "Roles"):
        table.add_column(header)
    for play in read.plays:
        cells = (str(play.index), play.name or "", str(play.hosts), _roles(play))
        table.add_row(*(Text(cell) for cell in cells))
    return table


def _roles(play: Play) -> str:
    """The roles a play pulls in, by short name, and how many vars the play sets.

    import_role is the usual way and goes unsaid. The roles keyword and include_role
    are named, because each one is a different precedence.
    """
    parts = []
    for role in play.roles:
        short = role.name.rsplit(".", 1)[-1]
        parts.append(short if role.how == IMPORT_ROLE else f"{short} ({role.how})")
    if play.var_names:
        parts.append(_count(len(play.var_names), "play var"))
    return ", ".join(parts) or "-"


def _carried(adoption: Adoption, inputs: Emitted) -> list[tuple[str, ...]]:
    if adoption.problem:
        return []

    rows = [
        ("Fabric", f"{adoption.name}, from play {adoption.play.index}"),
        ("FabricInputs", str(len(inputs.documents))),
    ]
    if adoption.pools.files:
        rows.append(("pool files", str(len(adoption.pools.files))))
    if adoption.named.files:
        rows.append(("files the design names", str(len(adoption.named.files))))
    return rows


def _not_carried(
    repo: Path, adoption: Adoption, found: VarFiles, inputs: Emitted
) -> list[tuple[str, ...]]:
    rows: list[tuple[str, ...]] = []

    # An unreadable playbook is already in the Playbook row.
    if adoption.problem and adoption.playbook is not None:
        rows.append(("Fabric", adoption.problem))

    if adoption.other_plays:
        plays = ", ".join(f"play {play.index}" for play in adoption.other_plays)
        rows.append((plays, "each a separate run: --play N --name NAME"))

    if adoption.vault_file:
        rows.append((adoption.vault_file, "the vault password; create its Secret:"))

    for file in found.problems:
        rows.append((_relative(file.path, repo), file.problem or ""))
    for note in adoption.pools.notes:
        rows.append(("pool file", note))
    for note in (*inputs.problems, *adoption.named.notes):
        rows.append(_split(note))
    return rows


def _warnings(
    repo: Path,
    listed: Inventory,
    found: VarFiles,
    inputs: Emitted,
    adoption: Adoption | None,
) -> list[tuple[str, ...]]:
    rows: list[tuple[str, ...]] = []

    fabric_documents = adoption.documents if adoption else ()
    for where, names in plain_passwords(fabric_documents + inputs.documents):
        rows.append((", ".join(names), f"plain text in {where}, carried as the repository has it"))

    # A group renamed in the inventory, or a host that is gone: the file reaches no host.
    for file in found.files:
        if not _knows(listed, file.vars_dir, file.scope):
            level = "group" if file.vars_dir == GROUP_VARS else "host"
            rows.append((_relative(file.path, repo), f"{file.scope} is no {level} in the inventory"))

    for note in inputs.notes:
        rows.append(_split(note))
    return rows


def _knows(listed: Inventory, vars_dir: str, scope: str) -> bool:
    if not listed.usable:  # nothing to check against, so nothing is flagged
        return True
    return scope in (listed.groups if vars_dir == GROUP_VARS else listed.hostvars)


def _split(note: str) -> tuple[str, str]:
    """A note written as "<what>: <why>", as two cells."""
    what, colon, why = note.partition(": ")
    return (what, why) if colon else ("", note)


def _relative(path: Path, repo: Path) -> str:
    try:
        return str(path.relative_to(repo))
    except ValueError:
        return str(path)


def main() -> None:
    app()
