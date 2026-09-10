"""ansible.cfg as the repository writes it, for `Fabric.spec.ansibleCfg`.

The one in the repository's root, and only that one: it is where Ansible looks when
run in the repository. `ANSIBLE_CONFIG`, `~/.ansible.cfg` and `/etc/ansible/ansible.cfg`
belong to whoever runs it.

Parsed as Ansible parses it -- stdlib configparser, `;` as an inline comment, keys
folded to lower case, a duplicate key an error -- so a value here is the value Ansible
reads. Two settings keep the text as written: no interpolation, so a `%(name)s` is
left for Ansible to expand; and `[DEFAULT]` stays a section of its own instead of
being copied into every other one.
"""

from __future__ import annotations

import configparser
from dataclasses import dataclass, field
from pathlib import Path

CONFIG_FILE = "ansible.cfg"


@dataclass(frozen=True)
class AnsibleCfg:
    path: Path | None = None  # None when the repository has no ansible.cfg
    sections: dict[str, dict[str, str]] = field(default_factory=dict)
    problem: str | None = None

    @property
    def usable(self) -> bool:
        return self.problem is None

    @property
    def inventory(self) -> tuple[str, ...]:
        """The sources `[defaults] inventory` names, split on commas as Ansible splits them."""
        raw = self.sections.get("defaults", {}).get("inventory", "")
        return tuple(part.strip() for part in raw.split(",") if part.strip())


def read_ansible_cfg(repo: Path) -> AnsibleCfg:
    """`repo/ansible.cfg`, section by section, every value the string it is written as.

    No file is not a problem: Ansible then runs on its defaults, and `sections` is empty.
    """
    path = repo / CONFIG_FILE
    if not path.is_file():
        return AnsibleCfg()

    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as err:
        return AnsibleCfg(path=path, problem=f"{path} is not UTF-8: {err}")
    except OSError as err:
        return AnsibleCfg(path=path, problem=f"{path} could not be read: {err}")

    parser = configparser.ConfigParser(
        inline_comment_prefixes=(";",),
        interpolation=None,
        # A section header is never empty, so no section is the default one.
        default_section="",
    )
    try:
        parser.read_string(text, source=str(path))
    except configparser.Error as err:
        # what Ansible stops on as well: a duplicate key, a line before any section
        return AnsibleCfg(path=path, problem=str(err).replace("\n", " "))

    sections = {name: dict(parser[name]) for name in parser.sections()}
    return AnsibleCfg(path=path, sections=sections)
