"""The rhiza developer tasks, as a pinned CLI instead of a synced make layer.

What this replaces, per consumer repository: ``.rhiza/rhiza.mk`` (200 lines) and the ten
fragments in ``.rhiza/make.d/`` (823 lines), synced at a template tag and excluded,
shadowed or patched wherever a project disagreed with them. Here they are a dependency
pin -- ``uvx rhiza-task@1.5.0 test`` -- so there is nothing to copy, nothing to exclude in
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

__all__ = ["__version__"]

# Kept in step with [project].version by bump-my-version, which needs a [[files]] entry
# for this file but not for pyproject.toml itself.
__version__ = "1.5.0"
