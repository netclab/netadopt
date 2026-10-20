"""One fabric rendered twice, and compared host by host.

Once from the repository, as `lab` renders it, and once from the repository rebuilt
from the objects `emit` writes -- repo', which is what a cluster renders. Both renders
are AVD's own role with the collections netadopt pins, so where the two agree on every
host's structured config, the objects carry the repository; where they do not, what
differs is named, never smoothed over.

A host's structured config is compared whole. Where it differs, the top-level keys that
differ are named: AVD's structured config is keyed by what it configures, so the key is
the part of the switch's configuration that would change.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

from netadopt.ansible import Ansible
from netadopt.reconstruct import reconstruct
from netadopt.render import Rendered, render

REPOSITORY = "repository"
REBUILT = "rebuilt"


@dataclass(frozen=True)
class Host:
    """One host of the two renders: identical, differing in some keys, or on one side."""

    name: str
    differs: tuple[str, ...] = ()  # the top-level keys whose values differ
    only: str | None = None  # REPOSITORY or REBUILT, when the other render lacks the host

    @property
    def identical(self) -> bool:
        return not self.differs and self.only is None


@dataclass(frozen=True)
class Comparison:
    hosts: tuple[Host, ...] = ()

    @property
    def identical(self) -> bool:
        return all(host.identical for host in self.hosts)

    @property
    def same(self) -> int:
        """How many hosts are identical."""
        return sum(1 for host in self.hosts if host.identical)


def compare(repository: Mapping[str, dict], rebuilt: Mapping[str, dict]) -> Comparison:
    """Each host's structured config from the repository's render against repo''s."""
    hosts = []
    for name in sorted(set(repository) | set(rebuilt)):
        if name not in rebuilt:
            hosts.append(Host(name, only=REPOSITORY))
        elif name not in repository:
            hosts.append(Host(name, only=REBUILT))
        else:
            source, copy = repository[name], rebuilt[name]
            keys = sorted(
                key for key in set(source) | set(copy) if source.get(key) != copy.get(key)
            )
            hosts.append(Host(name, differs=tuple(keys)))
    return Comparison(tuple(hosts))


def render_rebuilt(
    ansible: Ansible,
    documents: Iterable[dict],
    fabric: str,
    collections: Path,
    work: Path,
    vault_password_file: Path | None = None,
) -> Rendered:
    """repo', rebuilt under `work` from `documents`, rendered as a cluster renders it.

    The Fabric's one play, with the collections netadopt pins. A Fabric naming a vault
    Secret gets `vault_password_file` for it, which stands in for the Secret.
    """
    rebuilt = reconstruct(documents, work / "rebuilt", fabric=fabric)
    if not rebuilt.usable:
        return Rendered(problem=f"the repository did not rebuild: {rebuilt.problem}")
    password = vault_password_file if rebuilt.vault_password is not None else None
    return render(
        ansible,
        rebuilt.root,
        rebuilt.playbook or "",
        collections,
        work / "render",
        inventory=rebuilt.inventory,
        vault_password_file=password,
    )
