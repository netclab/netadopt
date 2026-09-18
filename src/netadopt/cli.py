"""The command line.

One subcommand per ecosystem, present whether or not its extra is installed -- so
nothing an ecosystem needs is imported at start-up.
"""

from __future__ import annotations

import shlex
import tempfile
from collections import Counter
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
from netadopt.galaxy import cache_root, ensure_collections
from netadopt.inventory import Inventory, read_inventory
from netadopt.inventoryfile import read_inventory_file
from netadopt.lab import (
    CONNECTED_TYPES,
    LINUX,
    NETCLAB,
    Ceos,
    netclab_values,
    values_yaml,
)
from netadopt.playbook import IMPORT_ROLE, Play, Playbook, find_playbooks, read_playbook
from netadopt.pools import Pools, find_pools
from netadopt.render import RENDER_ROLE, render
from netadopt.repocode import Code, code_directories, find_code
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
# By position, as the report prints it.
PlayOption = Annotated[int, typer.Option(metavar="N", help="play to carry, by its index")]
NameOption = Annotated[
    str | None, typer.Option(help="the Fabric's name; default the repository directory's")
]

# What a lab is written for. netclab-chart is built; containerlab is the second emitter
# and is not offered before it exists.
LAB_TARGETS = (NETCLAB,)
ForOption = Annotated[str, typer.Option("--for", metavar="TARGET", help="what the lab is for")]
ConnectedOption = Annotated[
    str, typer.Option(metavar="TYPE", help="the node a peer outside the fabric becomes")
]
# Written only when given, so that the chart's own defaults hold otherwise.
CeosImageOption = Annotated[str | None, typer.Option(help="the image every cEOS node runs")]
CeosMemoryOption = Annotated[str | None, typer.Option(help="the memory every cEOS node asks for")]
CeosCpuOption = Annotated[str | None, typer.Option(help="the CPU every cEOS node asks for")]


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
    uncarried_code: tuple[Code, ...] = ()  # code directories ansible.cfg names; never carried
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
    inputs: Emitted,
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
        (doc["metadata"]["name"] for doc in inputs.documents),
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
        uncarried_code=code_directories(repo, config),
    )


@avd.command()
def report(
    repo: Repo,
    playbook: PlaybookOption = None,
    inventory: InventoryOption = None,
    play: PlayOption = 0,
    name: NameOption = None,
) -> None:
    """Say what is in the repository, and what emit carries of it.

    `--play` and `--name` are emit's, and mean the same here: a playbook holding two
    fabrics has a report for each, under the name each would be emitted with.
    """
    found = resolve_ansible()
    if not found.usable:
        # An install without the extra, not a fact about the repository: a report
        # without Ansible would skip checks and still read as whole.
        typer.echo(f'no Ansible: {found.problem} -- install as: uvx "netadopt[avd]" ...', err=True)
        raise typer.Exit(1)
    config = read_ansible_cfg(repo)
    listed = read_inventory(found, repo, inventory)
    source = _inventory_source(inventory, config)
    var_files = read_vars(repo, source, playbook)
    read = read_playbook(repo, playbook) if playbook else None
    code = find_code(repo, config, (inventory,) if inventory else config.inventory)

    # What emit carries for this play, under the name it would be emitted with.
    wanted = name or repo.resolve().name
    refused = _name_refused(wanted)
    spelled = rfc1123(wanted) or "fabric"
    inputs = fabric_inputs(var_files, spelled)
    adoption = (
        _adopt(repo, playbook, source, play, spelled, config, var_files, inputs)
        if playbook
        else None
    )

    # Every part is reported before anything decides the run failed: an unreadable
    # inventory does not hide a readable playbook.
    console = _console()
    console.print(Text(repo.resolve().name, style="bold"))
    console.print()
    overview = [
        _ansible_row(found),
        _config_row(config),
        *_inventory_rows(listed),
        _vars_row(var_files),
        _playbook_row(repo, playbook, read),
        _renders_row(repo, playbook, inventory, play),
    ]
    console.print(_grid(overview, label_style="bold"))
    if read is not None and read.usable:
        console.print()
        console.print(_plays_table(read))

    if adoption is not None:
        _print_section(console, "Carried", "green", _carried(adoption, inputs))
        _print_section(
            console, "Not carried", "red", _not_carried(repo, adoption, var_files, inputs, refused)
        )
        if adoption.vault_file:
            # on a line of its own, never wrapped, so that it can be copied
            command = _vault_secret_command(repo, adoption)
            console.print(f"    {command}", soft_wrap=True, markup=False)
    warnings = _warnings(repo, code, listed, var_files, inputs, adoption)
    _print_section(console, "Warnings", "yellow", warnings)

    unreadable = read is None or not read.usable or not listed.usable or not config.usable
    # Other plays and the vault password are left out by design; these are model parts missing.
    incomplete = (
        not adoption
        or not adoption.documents
        or adoption.uncarried_code
        or var_files.problems
        or inputs.problems
        or refused is not None  # emit would emit nothing under this name
    )
    if unreadable or incomplete:
        raise typer.Exit(2)
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
        typer.echo(
            f"nothing emitted: no name can be spelled from {wanted!r} -- pass --name", err=True
        )
        raise typer.Exit(2)
    if len(spelled) > LABEL_MAX:
        # the name is also the value of the label its inputs carry
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
    adoption = _adopt(repo, playbook, source, play, spelled, config, found, inputs)
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

    if not adoption.documents or adoption.uncarried_code or found.problems or inputs.problems:
        raise typer.Exit(2)  # emitted, but a part of the model is missing
    raise typer.Exit(0)


@avd.command()
def lab(
    repo: Repo,
    playbook: PlaybookOption = None,
    inventory: InventoryOption = None,
    play: PlayOption = 0,
    for_: ForOption = NETCLAB,
    connected: ConnectedOption = LINUX,
    ceos_image: CeosImageOption = None,
    ceos_memory: CeosMemoryOption = None,
    ceos_cpu: CeosCpuOption = None,
) -> None:
    """Write the lab topology of the repository's fabric: cabled nodes, no configuration.

    The values go to stdout and everything else to stderr, so the stream pipes into
    `helm install ... --values -` whether or not there was something to say.

    AVD renders the cabling, on a copy of the repository, with the collections netadopt
    pins -- fetched into its own cache the first time and never again.
    """
    if for_ not in LAB_TARGETS:
        typer.echo(f"no lab: --for {for_} is not built -- {', '.join(LAB_TARGETS)}", err=True)
        raise typer.Exit(2)
    if connected not in CONNECTED_TYPES:
        typer.echo(
            f"no lab: --connected {connected} is not one of {', '.join(CONNECTED_TYPES)}", err=True
        )
        raise typer.Exit(2)
    if playbook is None:
        typer.echo("no lab: no playbook named -- pass --playbook", err=True)
        raise typer.Exit(2)

    found = resolve_ansible()
    if not found.usable:
        typer.echo(f'no Ansible: {found.problem} -- install as: uvx "netadopt[avd]" ...', err=True)
        raise typer.Exit(1)

    root = cache_root()
    if not root.exists():
        # The one slow run: four archives, and afterwards nothing reaches the network.
        typer.echo(f"fetching arista.avd {AVD_TESTED} and its collections into {root}", err=True)
    collections = ensure_collections(found)
    for note in collections.notes:
        typer.echo(note, err=True)
    if not collections.usable:
        typer.echo(f"no lab: {collections.problem}", err=True)
        raise typer.Exit(2)

    typer.echo(f"rendering play [{play}] of {playbook} with {RENDER_ROLE}", err=True)
    with tempfile.TemporaryDirectory(prefix="netadopt-") as work:
        # The structured configurations are read before the copy goes; nothing else is kept.
        rendered = render(
            found, repo, playbook, collections.path, Path(work) / "render", play, inventory
        )
    if not rendered.usable:
        typer.echo(f"no lab: {rendered.problem}", err=True)
        raise typer.Exit(2)
    typer.echo(f"rendered {_count(len(rendered.hosts), 'host')}", err=True)
    # Another play is another fabric over the same hosts -- twodc's second one is a
    # digital twin -- and a lab of one of them must not read as the lab of the repository.
    read = read_playbook(repo, playbook)
    for other in read.plays if read.usable else ():
        if other.index != play:
            what = NO_ROLE if _no_role(other) else f"render it with --play {other.index}"
            typer.echo(f"{_label(other)}: not rendered -- {what}", err=True)

    built = netclab_values(
        rendered.hosts, connected, Ceos(image=ceos_image, memory=ceos_memory, cpu=ceos_cpu)
    )
    for note in built.notes:
        typer.echo(note, err=True)
    if built.problem:
        typer.echo(f"no lab: {built.problem}", err=True)
        raise typer.Exit(2)

    # What the values hold, which is not what the render wrote: a peer outside the
    # fabric is a node too, and AVD rendered no structured configuration for it.
    typer.echo(_lab_summary(built.values), err=True)
    typer.echo(values_yaml(built.values), nl=False)
    raise typer.Exit(0)


def _lab_summary(values: dict) -> str:
    """`10 nodes (8 ceos, 2 linux), 22 networks`."""
    topology = values["topology"]
    counted = Counter(node["type"] for node in topology["nodes"])
    by_type = ", ".join(f"{number} {node_type}" for node_type, number in sorted(counted.items()))
    return (
        f"{_count(sum(counted.values()), 'node')} ({by_type}), "
        f"{_count(len(topology['networks']), 'network')}"
    )


def _emit_notes(repo: Path, adoption: Adoption) -> list[str]:
    """What emit says on stderr about the Fabric: the play it carries, what it leaves."""
    if adoption.problem:
        return [f"Fabric not emitted: {adoption.problem}"]

    said = [f"Fabric {adoption.name} <- {_label(adoption.play)}"]
    # Another play is another run, and it needs its own --name: under the same one it
    # would replace this Fabric in the cluster.
    for other in adoption.other_plays:
        what = (
            NO_ROLE if _no_role(other) else f"carry it with --play {other.index} and its own --name"
        )
        said.append(f"{_label(other)}: not carried -- {what}")
    if adoption.vault_file:
        said.append(
            f"not carried: the vault password file {adoption.vault_file}, named by ansible.cfg -- "
            "spec.vaultPassword names the Secret, create it:"
        )
        said.append(f"  {_vault_secret_command(repo, adoption)}")
    for piece in adoption.uncarried_code:
        said.append(
            f"not carried: {piece.path}, named by {piece.setting} in ansible.cfg -- "
            "missing from the rebuilt repository"
        )
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


def _no_role(play: Play) -> bool:
    """A play that pulls in no role at all, which is said and then left alone.

    AVD's twodc scenario keeps a `meta: clear_facts` play between its two fabrics, and
    telling anyone to carry that one as a fabric of its own is advice with nothing
    behind it. Saying only this says no more than the report's own Roles column does.
    """
    return not play.roles


NO_ROLE = "pulls in no role"


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


def _ansible_row(found: Ansible) -> tuple[str, ...]:
    return ("Ansible", f"ansible-core {found.core}", found.exe or "")


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
    return (
        "Variables",
        _count(len(found.files), "file"),
        f"group_vars {groups}, host_vars {hosts}",
    )


def _playbook_row(repo: Path, playbook: str | None, read: Playbook | None) -> tuple[str, ...]:
    if read is None:
        candidates = [candidate.path.name for candidate in find_playbooks(repo)]
        if not candidates:
            return ("Playbook", "not named", "no file here reads as a playbook")
        return ("Playbook", "not named", f"name one with --playbook: {', '.join(candidates)}")
    if not read.usable:
        return ("Playbook", playbook or "", read.problem or "")
    return ("Playbook", playbook or "", _count(len(read.plays), "play"))


def _name_refused(wanted: str) -> tuple[str, str] | None:
    """The name and why `emit` would refuse it, or None -- said without running emit."""
    spelled = rfc1123(wanted)
    if not spelled:
        return (wanted, "no name can be spelled from it -- emit refuses it, pass --name")
    if len(spelled) > LABEL_MAX:
        # the name is also the value of the label its inputs carry
        why = (
            f"{len(spelled)} characters, and a label value holds {LABEL_MAX} -- "
            "emit refuses it, pass a shorter --name"
        )
        return (spelled, why)
    return None


def _renders_row(
    repo: Path, playbook: str | None, inventory: str | None, play: int = 0
) -> tuple[str, ...]:
    """Whether the design renders on the AVD netadopt pins -- which this report never asks.

    Rendering runs everything the vars hold and fetches AVD's collections on its first
    use, so it belongs to `lab` and not to a report that is seconds and touches nothing.
    The row is the command, so that a report is never read as the whole measurement.
    """
    command = f"netadopt avd lab {shlex.quote(str(repo))}"
    if playbook:
        command += f" --playbook {shlex.quote(playbook)}"
    if inventory:
        command += f" --inventory {shlex.quote(inventory)}"
    if play:
        command += f" --play {play}"
    return ("Renders", "not measured", command)


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
    repo: Path,
    adoption: Adoption,
    found: VarFiles,
    inputs: Emitted,
    refused: tuple[str, str] | None = None,
) -> list[tuple[str, ...]]:
    rows: list[tuple[str, ...]] = []

    # First: a name emit refuses means it emits nothing at all, whatever else reads.
    if refused:
        rows.append(refused)

    # An unreadable playbook is already in the Playbook row.
    if adoption.problem and adoption.playbook is not None:
        rows.append(("Fabric", adoption.problem))

    for other in adoption.other_plays:
        what = NO_ROLE if _no_role(other) else f"a separate run: --play {other.index} --name NAME"
        rows.append((_label(other), what))

    if adoption.vault_file:
        rows.append((adoption.vault_file, "the vault password; create its Secret:"))

    for piece in adoption.uncarried_code:
        why = f"named by {piece.setting} in ansible.cfg; missing from the rebuilt repository"
        rows.append((piece.path, why))

    for file in found.problems:
        rows.append((_relative(file.path, repo), file.problem or ""))
    for note in adoption.pools.notes:
        rows.append(("pool file", note))
    for note in (*inputs.problems, *adoption.named.notes):
        rows.append(_split(note))
    return rows


def _warnings(
    repo: Path,
    code: tuple[Code, ...],
    listed: Inventory,
    found: VarFiles,
    inputs: Emitted,
    adoption: Adoption | None,
) -> list[tuple[str, ...]]:
    rows: list[tuple[str, ...]] = []

    # First: Ansible has already run this code to answer the report.
    for piece in code:
        if piece.setting is None:
            rows.append((piece.path, "an executable inventory, run whenever Ansible reads it"))
        else:
            rows.append(
                (piece.path, f"code Ansible loads, named by {piece.setting} in ansible.cfg")
            )

    fabric_documents = adoption.documents if adoption else ()
    for where, names in plain_passwords(fabric_documents + inputs.documents):
        rows.append((", ".join(names), f"plain text in {where}, carried as the repository has it"))

    # A group renamed in the inventory, or a host that is gone: the file reaches no host.
    for file in found.files:
        if not _knows(listed, file.vars_dir, file.scope):
            level = "group" if file.vars_dir == GROUP_VARS else "host"
            rows.append(
                (_relative(file.path, repo), f"{file.scope} is no {level} in the inventory")
            )

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
