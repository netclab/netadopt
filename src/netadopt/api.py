"""What function-avd imports from netadopt: a change here is a breaking change.

function-avd reconstructs a repository from a Fabric and renders it with AVD's own
role; its image installs the collections, and its tests read the inventory. Each name
is pinned by tests/test_api.py.
"""

from __future__ import annotations

from netadopt import AVD_TESTED
from netadopt.ansible import Ansible, resolve_ansible
from netadopt.galaxy import Collections, ensure_collections
from netadopt.inventory import Inventory, read_inventory
from netadopt.reconstruct import Reconstructed, reconstruct
from netadopt.render import Rendered, render
from netadopt.xr import API_VERSION, FABRIC_LABEL

__all__ = [
    "API_VERSION",
    "AVD_TESTED",
    "FABRIC_LABEL",
    "Ansible",
    "Collections",
    "Inventory",
    "Reconstructed",
    "Rendered",
    "ensure_collections",
    "read_inventory",
    "reconstruct",
    "render",
    "resolve_ansible",
]
