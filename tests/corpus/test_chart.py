"""The values, judged by netclab-chart rather than by a reading of it.

`test_cabling.py` holds the values against rules read out of the chart's templates;
this one hands them to helm. A chart release that changes a rule shows up here and
nowhere else.

Only what is netadopt's is asserted: that the chart renders the values at all, that
each node gets its Service, and that every network a Pod asks for is one the same
render made. How many NetworkAttachmentDefinitions there are, what the DaemonSets do
and what Multus needs are the chart's business.

helm and a checkout of the chart come from the environment: with neither, these skip.

    NETADOPT_NETCLAB_CHART=~/netclab-chart uv run pytest tests/corpus/test_chart.py
"""

from __future__ import annotations

import subprocess

import yaml

from netadopt.lab import Lab

POD = "Pod"
SERVICE = "Service"
NETWORK_DEFINITION = "NetworkAttachmentDefinition"

# What Multus reads: "<network>@<interface>,<network>@<interface>".
NETWORKS_ANNOTATION = "k8s.v1.cni.cncf.io/networks"


def test_the_chart_renders_the_values(templated: subprocess.CompletedProcess):
    """Every `fail` the chart holds passes on the names netadopt spells."""
    assert templated.returncode == 0, (templated.stderr or templated.stdout).strip()


def test_every_node_gets_a_service_of_its_own_name(lab: Lab, templated):
    documents = _documents(templated)

    assert sorted(_named(documents, SERVICE)) == sorted(
        node["name"] for node in lab.values["topology"]["nodes"]
    )


def test_every_network_a_pod_asks_for_is_one_the_chart_made(lab: Lab, templated):
    documents = _documents(templated)
    made = _named(documents, NETWORK_DEFINITION)

    asked = set()
    for document in documents:
        if document.get("kind") != POD:
            continue
        annotations = (document.get("metadata") or {}).get("annotations") or {}
        for entry in (annotations.get(NETWORKS_ANNOTATION) or "").split(","):
            name = entry.split("@")[0].strip()
            if name:
                asked.add(name)

    if lab.values["topology"]["networks"]:
        # otherwise a lab whose Pods ask for nothing passes this in silence
        assert asked, "the values hold networks and no Pod asks for one"
    assert asked <= made, sorted(asked - made)


def _documents(templated: subprocess.CompletedProcess) -> list[dict]:
    assert templated.returncode == 0, (templated.stderr or templated.stdout).strip()
    return [doc for doc in yaml.safe_load_all(templated.stdout) if isinstance(doc, dict)]


def _named(documents: list[dict], kind: str) -> set[str]:
    """The names of every object of one Kubernetes kind."""
    return {(doc.get("metadata") or {}).get("name") for doc in documents if doc.get("kind") == kind}
