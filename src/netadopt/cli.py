"""The command line.

One tool, one subcommand per ecosystem. The subcommand exists whether or not its
extra is installed: one missing from --help tells the user nothing, one that is
there and names what is missing tells him what to install. So nothing an ecosystem
needs is imported at start-up.

The default verb is the report. Deeper verbs are the same computation carried
further, and they come later.
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
    """A callback with no options of its own, and it is load-bearing.

    Without it typer collapses a one-command app into the root command: `netadopt
    avd` would not exist today and would appear by itself the day `nac` is added.
    The shape of the CLI must not depend on how many ecosystems are implemented.
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
    # A repository has several playbooks and only he knows which one builds the
    # fabric. Its PLAYS are not named: they are enumerated and reported, because a
    # playbook can hold two plays with the same name -- ours does.
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

    # Every part is reported before anything decides the run failed: a repository
    # with no readable inventory can still have a playbook worth showing him, and a
    # report that stops at the first bad news is the one nobody can act on.
    listed = read_inventory(found, repo, inventory)
    typer.echo(_inventory_report(listed))

    if playbook is None:
        typer.echo(_candidates_report(repo))
        read = None
    else:
        read = read_playbook(repo, playbook)
        typer.echo(_playbook_report(read))

    if not found.usable:
        raise typer.Exit(1)  # nothing measured here can be trusted without Ansible
    if read is None or not read.usable or not listed.usable:
        raise typer.Exit(2)  # something he has to name or fix
    raise typer.Exit(0)


def _ansible_report(found: Ansible) -> str:
    if not found.usable:
        return (
            f"ansible    unusable -- {found.problem}\n"
            f'           install Ansible, or run: uvx "netadopt[ansible]" ...'
        )

    lines = [f"ansible    {found.core}  {found.exe}  (python {found.python})"]
    if not found.his:
        # Not a complaint about our install -- a statement about what every number
        # below it means. His CI runs an ansible-core we did not measure.
        lines.append("           note: ours, not the Ansible this repo is run with")
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
        # How the role is pulled in is printed, not summarised away: the roles:
        # keyword, import_role and include_role are three different precedences.
        for role in play.roles:
            lines.append(f"       {role.how:14} {role.name}")
    return "\n".join(lines)


def _inventory_report(listed: Inventory) -> str:
    where = listed.source or "from ansible.cfg"
    if not listed.usable:
        return f"inventory  {listed.problem}"

    groups = len(listed.groups)
    lines = [f"inventory  {where}  -- {groups} groups, {len(listed.hosts)} hosts"]
    # Ansible's warnings are about his repository, not about us, and they are the
    # kind of thing that explains a host count he did not expect.
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
