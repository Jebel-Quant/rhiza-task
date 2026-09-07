"""The rhiza developer tasks, as a pinned CLI instead of a synced make layer.

What this replaces, per consumer repository: ``.rhiza/rhiza.mk`` (200 lines) and the ten
fragments in ``.rhiza/make.d/`` (823 lines), synced at a template tag and excluded,
shadowed or patched wherever a project disagreed with them. Here they are a dependency
pin -- ``uvx rhiza-task@1.6.0 test`` -- so there is nothing to copy, nothing to exclude in
``template.yml``, and nothing to drift.

Sibling to ``pytest-rhiza``, which did the same for ``.rhiza/tests``.

Layout:

* :mod:`rhiza_task.spec` -- the task model: ``Task``, ``Guard``, ``Skip``/``Failed``, and
  the registry that replaces make's double-colon rules.
* :mod:`rhiza_task.config` -- six-layer settings resolution, replacing ``?=`` and ``+=``.
* :mod:`rhiza_task.uv` -- the ways rhiza reaches a tool.
* :mod:`rhiza_task.runner` -- prerequisite dedup, guard evaluation, outcome bookkeeping.
* :mod:`rhiza_task.cli` -- the Typer app, generated from the registry.
* :mod:`rhiza_task.tasks` -- the task modules themselves, loaded by entry point.
"""

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _installed_version

__all__ = ["__version__"]

# Read from the installed distribution's metadata rather than written here. hatch-vcs
# derives the version from the git tag at build time, so this file has no number to keep
# in step and bump-my-version has no [[files]] entry for it -- which is the point: a
# version written in two places is a version that can disagree with itself, and the release
# that shipped 1.0.0 with a stale uv.lock is what that looks like.
#
# The fallback is for a source tree that was never installed -- someone running out of a
# clone with `python -c "import rhiza_task"` and no `uv sync`. Every real invocation is
# `uvx rhiza-task@X.Y.Z`, which installs the distribution, so metadata is present. A
# literal here would be a second source of truth for exactly the case that does not
# matter, so it says what it knows instead.
try:
    __version__ = _installed_version("rhiza-task")
except PackageNotFoundError:  # pragma: no cover - requires an uninstalled source tree
    __version__ = "0+unknown"
