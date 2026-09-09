"""The command line.

One subcommand per ecosystem, present whether or not its extra is installed -- so
nothing an ecosystem needs is imported at start-up.

The default verb is the report.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from netadopt.ansible import Ansible, resolve_ansible
from netadopt.inventory import Inventory, read_inventory
from netadopt.playbook import Playbook, find_playbooks, read_playbook

app = typer.Typer(
    help="Read a network-automation repository and report what is in it.",
    no_args_is_help=True,
    add_completion=False,
)


@app.callback()
def _root() -> None:
    """No options of its own, and load-bearing.

    Without it typer collapses a one-command app into the root command: `netadopt avd`
    would not exist until a second subcommand appeared.
    """


@app.command()
def avd(
    repo: Annotated[
        Path,
        typer.Argument(
            exists=True,
            file_okay=False,
            dir_okay=True,
            help="the repository directory",
        ),
    ],
    # One playbook is named; its plays are not -- a playbook can hold two plays with
    # the same name, as AVD's twodc scenario does.
    playbook: Annotated[
        str | None,
        typer.Option(help="playbook to read, relative to the repository"),
    ] = None,
    # Only needed when no ansible.cfg names it. Ansible's own -i, passed through
    # unchanged: a file, or a directory of them.
    inventory: Annotated[
        str | None,
        typer.Option(metavar="SOURCE", help="inventory file or directory, as -i"),
    ] = None,
    ansible: Annotated[
        str | None,
        typer.Option(metavar="EXE", help="ansible-playbook to use, overriding PATH"),
    ] = None,
) -> None:
    """Read an Arista AVD repository."""
    found = resolve_ansible(ansible)
    typer.echo(_ansible_report(found))

    # Every part is reported before anything decides the run failed: an unreadable
    # inventory does not hide a readable playbook.
    listed = read_inventory(found, repo, inventory)
    typer.echo(_inventory_report(listed))

    if playbook is None:
        typer.echo(_candidates_report(repo))
        read = None
    else:
        read = read_playbook(repo, playbook)
        typer.echo(_playbook_report(read))

    if not found.usable:
        raise typer.Exit(1)  # no Ansible
    if read is None or not read.usable or not listed.usable:
        raise typer.Exit(2)  # no playbook named, or it or the inventory did not read
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
