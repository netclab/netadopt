"""The command line.

One subcommand per ecosystem, present whether or not its extra is installed -- so
nothing an ecosystem needs is imported at start-up.

The default verb is the report.
"""

from __future__ import annotations

import shlex
from collections import Counter
from pathlib import Path
from typing import Annotated

import typer

from netadopt.ansible import Ansible, resolve_ansible
from netadopt.ansiblecfg import AnsibleCfg, read_ansible_cfg
from netadopt.inventory import Inventory, read_inventory
from netadopt.inventoryfile import read_inventory_file
from netadopt.playbook import Play, Playbook, find_playbooks, read_playbook
from netadopt.pools import find_pools
from netadopt.varfiles import GROUP_VARS, VarFiles, read_vars
from netadopt.xr import (
    LABEL_MAX,
    VAULT_SECRET_KEY,
    fabric,
    fabric_inputs,
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


@avd.command()
def report(
    repo: Repo,
    playbook: PlaybookOption = None,
    inventory: InventoryOption = None,
    ansible: AnsibleOption = None,
) -> None:
    """Say what is in the repository."""
    found = resolve_ansible(ansible)
    typer.echo(_ansible_report(found))

    # Every part is reported before anything decides the run failed: an unreadable
    # inventory does not hide a readable playbook.
    config = read_ansible_cfg(repo)
    typer.echo(_config_report(config, repo))

    listed = read_inventory(found, repo, inventory)
    typer.echo(_inventory_report(listed))

    source = _inventory_source(inventory, config)
    typer.echo(_vars_report(read_vars(repo, source, playbook), listed))

    if playbook is None:
        typer.echo(_candidates_report(repo))
        read = None
    else:
        read = read_playbook(repo, playbook)
        typer.echo(_playbook_report(read))

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
    `kubectl apply -f -` whether or not there was something to say.
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
    fabric_documents, said = _fabric(repo, playbook, source, play, spelled, config, found)
    if name and spelled != name:
        said.insert(0, f"--name {name} is spelled {spelled}")

    documents = fabric_documents + inputs.documents
    if documents:
        typer.echo(to_yaml(documents), nl=False)

    for line in (*said, *inputs.notes, *inputs.problems):
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

    if not fabric_documents or found.problems or inputs.problems:
        raise typer.Exit(2)  # emitted, but a part of the model is missing
    raise typer.Exit(0)


def _fabric(
    repo: Path,
    playbook: str | None,
    source: str | None,
    index: int,
    name: str,
    config: AnsibleCfg,
    found: VarFiles,
) -> tuple[tuple[dict, ...], list[str]]:
    """The Fabric named `name` and the ConfigMap of its pools, or nothing; and the lines
    saying what became of them."""
    if playbook is None:
        return (), ["Fabric not emitted: no playbook named -- pass --playbook"]
    read = read_playbook(repo, playbook)
    if not read.usable:
        return (), [f"Fabric not emitted: {read.problem}"]
    if not 0 <= index < len(read.plays):
        held = len(read.plays)
        return (), [f"Fabric not emitted: no play [{index}] -- {read.path.name} holds {held}"]
    if not config.usable:
        return (), [f"Fabric not emitted: {config.problem}"]
    written = read_inventory_file(repo, source)
    if not written.usable:
        return (), [f"Fabric not emitted: {written.problem}"]

    chosen = read.plays[index]
    said = [f"Fabric {name} <- {_label(chosen)}"]
    # Another play is another run, and it needs its own --name: under the same one it
    # would replace this Fabric in the cluster.
    said += [
        f"{_label(other)}: not carried -- carry it with --play {other.index} and its own --name"
        for other in read.plays
        if other is not chosen
    ]
    named = config.vault_password_file
    if named:
        # a relative path is relative to ansible.cfg, at the repository's root
        where = named if named.startswith(("/", "~")) else str(repo / named)
        said += [
            (
                f"not carried: the vault password file {named}, named by ansible.cfg -- "
                "spec.vaultPassword names the Secret, create it:"
            ),
            (
                f"  kubectl create secret generic {vault_secret(name)} "
                f"--from-file={VAULT_SECRET_KEY}={shlex.quote(where)}"
            ),
        ]
    pooled = find_pools(found, chosen.raw, repo)
    said += pooled.notes
    entries, config_map = pool_objects(name, pooled.files)
    document = fabric(
        name,
        chosen.raw,
        written.groups,
        config.sections,
        vault_password=bool(named),
        pools=entries,
    )
    return ((document, config_map) if config_map else (document,)), said


def _inventory_source(given: str | None, config: AnsibleCfg) -> str | None:
    """The -i argument: as given, or the one inventory ansible.cfg names."""
    if given:
        return given
    named = config.inventory
    return named[0] if len(named) == 1 else None


def _label(play: Play) -> str:
    return f"play [{play.index}] {play.name}" if play.name else f"play [{play.index}]"


def _ansible_report(found: Ansible) -> str:
    if not found.usable:
        return (
            f"ansible    unusable -- {found.problem}\n"
            f'           install Ansible, or run: uvx "netadopt[ansible]" ...'
        )

    lines = [f"ansible    {found.core}  {found.exe}  (python {found.python})"]
    if found.bundled:
        lines.append(
            "           note: bundled ansible-core, not the Ansible this repo is run with"
        )
    for path in found.collections:
        lines.append(f"           collections {path}")
    return "\n".join(lines)


def _config_report(config: AnsibleCfg, repo: Path) -> str:
    if config.path is None:
        return f"config     no ansible.cfg in {repo}"
    if not config.usable:
        return f"config     {config.problem}"

    count = len(config.sections)
    lines = [f"config     {config.path.name}  -- {count} section{'s'[: count != 1]}"]
    # Key names only: a value can be a token, as under [galaxy_server.*].
    for name, values in config.sections.items():
        lines.append(f"           [{name}] {', '.join(values)}")
    return "\n".join(lines)


def _playbook_report(read: Playbook) -> str:
    if not read.usable:
        return f"playbook   {read.problem}"

    plays = read.plays
    lines = [f"playbook   {read.path.name}  -- {len(plays)} play{'s'[: len(plays) != 1]}"]
    width = max((len(str(play.name)) for play in plays), default=0)
    hosts_width = max((len(str(play.hosts)) for play in plays), default=0)
    for play in plays:
        vars_seen = f"  vars {len(play.var_names)}" if play.var_names else ""
        lines.append(
            f"  [{play.index}] {play.name!s:{width}}"
            f"  hosts {play.hosts!s:{hosts_width}}"
            f"  tasks {play.task_count}{vars_seen}"
        )
        # how the role is pulled in, printed: three different precedences
        for role in play.roles:
            lines.append(f"       {role.how:14} {role.name}")
    return "\n".join(lines)


def _inventory_report(listed: Inventory) -> str:
    where = listed.source or "from ansible.cfg"
    if not listed.usable:
        return f"inventory  {listed.problem}"

    groups = len(listed.groups)
    lines = [f"inventory  {where}  -- {groups} groups, {len(listed.hosts)} hosts"]
    # Ansible's own stderr, printed under the count it explains.
    for warning in listed.warnings:
        lines.append(f"           {warning}")
    return "\n".join(lines)


def _vars_report(found: VarFiles, listed: Inventory) -> str:
    if not found.files:
        where = ", ".join(str(path) for _, path in found.roots)
        return f"vars       no group_vars or host_vars in {where}"

    counted = Counter((file.root, file.vars_dir, file.scope) for file in found.files)
    total = len(found.files)
    groups = sum(1 for file in found.files if file.vars_dir == GROUP_VARS)
    lines = [f"vars       {total} files  -- group_vars {groups}, host_vars {total - groups}"]

    width = max(len(scope) for _, _, scope in counted)
    for (root, vars_dir, scope), count in counted.items():
        # Printed because Ansible allows a group and a host of the same name, at
        # different precedence levels.
        level = "group" if vars_dir == GROUP_VARS else "host"
        # A scope Ansible does not know is a file that reaches no host -- a group
        # renamed in the inventory, or a host_vars file for a host that is gone.
        known = "" if _knows(listed, vars_dir, scope) else "  -- unknown to Ansible"
        lines.append(f"           {root:9}  {level:5}  {scope:{width}}  {count}{known}")

    for file in found.problems:
        lines.append(f"           {file.path.name} {file.problem}")
    return "\n".join(lines)


def _knows(listed: Inventory, vars_dir: str, scope: str) -> bool:
    if not listed.usable:  # nothing to check against, so nothing is flagged
        return True
    return scope in (listed.groups if vars_dir == GROUP_VARS else listed.hostvars)


def _candidates_report(repo: Path) -> str:
    candidates = find_playbooks(repo)
    if not candidates:
        return f"playbook   not named, and no file in {repo} reads as a playbook"

    lines = [f"playbook   not named -- {len(candidates)} candidates in {repo}"]
    width = max(len(pb.path.name) for pb in candidates)
    for pb in candidates:
        count = len(pb.plays)
        lines.append(f"           {pb.path.name:{width}}  {count} play{'s'[: count != 1]}")
    lines.append("           name one with --playbook")
    return "\n".join(lines)


def main() -> None:
    app()
