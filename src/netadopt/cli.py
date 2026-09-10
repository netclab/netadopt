"""The command line.

One subcommand per ecosystem, present whether or not its extra is installed -- so
nothing an ecosystem needs is imported at start-up.

The default verb is the report.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Annotated

import typer

from netadopt.ansible import Ansible, resolve_ansible
from netadopt.ansiblecfg import AnsibleCfg, read_ansible_cfg
from netadopt.inventory import Inventory, read_inventory
from netadopt.playbook import Playbook, find_playbooks, read_playbook
from netadopt.varfiles import GROUP_VARS, VarFiles, read_vars
from netadopt.xr import fabric_inputs, to_yaml

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

    typer.echo(_vars_report(read_vars(repo, inventory, playbook), listed))

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
) -> None:
    """Write the FabricInput objects of the repository, as YAML.

    The objects go to stdout and everything else to stderr, so the stream pipes into
    `kubectl apply -f -` whether or not there was something to say.
    """
    found = read_vars(repo, inventory, playbook)
    emitted = fabric_inputs(found)

    if emitted.documents:
        typer.echo(to_yaml(emitted.documents), nl=False)

    for note in emitted.notes:
        typer.echo(note, err=True)
    if not emitted.documents:
        typer.echo(f"nothing to emit: no group_vars or host_vars in {repo}", err=True)

    # Fabric's parts are read -- the play, the inventory file, ansible.cfg -- and
    # nothing assembles them yet, so what comes out here is the inputs and not the
    # whole model.
    typer.echo("note: Fabric is not emitted yet", err=True)

    if found.problems:
        raise typer.Exit(2)  # emitted, but a file that belongs in it did not read
    raise typer.Exit(0)


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
            f"  [{play.index}] {str(play.name):{width}}"
            f"  hosts {str(play.hosts):{hosts_width}}"
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
