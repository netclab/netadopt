# Developing netadopt

How to run the checks a change has to pass. For what netadopt does, see the
[README](README.md).

## Setup

```
git clone --recurse-submodules https://github.com/netclab/netadopt
uv sync
```

The `avd` submodule is the AVD release netadopt is tested on. On a clone that does not
have it: `git submodule update --init`.

## Before a commit

```
uvx ruff check
uv run pytest --ignore=tests/corpus
```

When the change touches `src/netadopt/`, the whole suite, corpus tier included:

```
uv run pytest
```

CI runs `uvx ruff check` and `uv run pytest` with the submodule checked out, and the
release workflow calls the same one, so a version is published after exactly these.

## The two test tiers

`tests/` is hermetic: no network, no Ansible, no repository outside the test. It is
seconds.

`tests/corpus/` runs the readers over the example repositories and molecule scenarios
AVD ships, in the `avd` submodule; `NETADOPT_AVD` names another checkout instead, and
with neither these tests skip. It runs Ansible, and it is minutes.

The corpus tier asserts **properties, never counts**. The numbers there belong to
somebody else's release: a test asserting 115 playbooks goes red when AVD adds one,
and says the wrong thing when it does.

## The chart, when there is one to ask

`tests/corpus/test_chart.py` hands what `netadopt avd lab` writes to helm, so that the
values are judged by netclab-chart and not by a reading of its templates. It needs helm
and a checkout of the chart:

```
NETADOPT_NETCLAB_CHART=~/netclab-chart uv run pytest tests/corpus/test_chart.py
```

**Without the variable it skips, and CI never runs it.** Run it before a release that
changes what `lab` writes.

## Two rules a diff has to keep

- **No personal pronouns** in code, comments, docstrings or printed output. Name the
  thing: "the repository", "the playbook", "the user's Ansible".
- **`kind` names a Kubernetes kind and nothing else.** What netclab-chart calls a
  node's `type` is *type*; the label of a carried file is *carried_as*.

## Releasing

```
uv version <version>
git commit -am "Release <version>"
git push
git tag v<version> && git push origin v<version>
```

The tag publishes to PyPI, and `release.yml` refuses a tag that is not
`pyproject.toml`'s version.
