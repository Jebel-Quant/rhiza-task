"""The task model: what a rhiza gate *is*, independently of how it is invoked.

Reading all ten make fragments back to back, every recipe has the same three parts:

1. A **guard** -- ``if [ -d ${SOURCE_FOLDER} ]``, or a ``find`` for test files. When it
   fails the recipe prints a yellow WARN and exits 0.
2. A **provision** -- ``uvx <tool>`` or ``uv run --with a --with b <tool>``.
3. An **invocation** -- a long, mostly static argument list with a few substitutions.

Only four recipes in the whole layer need more than that: ``test`` (retry on pytest exit
3), ``mutation`` (run/html/move/results preserving the first status), ``doctor`` (version
comparison) and ``book`` (a per-notebook export loop). So the model here is declarative,
and the task body is the escape hatch those four use.

The split decides what is *data* -- reviewable, diffable, overridable from a consumer's
``pyproject.toml`` -- and what is code.
"""

from __future__ import annotations

import shutil
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - import cycle: config imports nothing from here
    from .config import Config


class Skip(Exception):  # noqa: N818 - an outcome, not an error; see runner.py
    """Raised by a guard or a task body to report that there was nothing to do.

    The make layer signals this by printing a WARN and exiting 0, which is how jointview
    ended up with a ``rhiza-test`` that "silently passed over nothing" -- its own Makefile
    says so. Making it a distinct outcome rather than a success is the point: ``--strict``
    turns every skip into a failure, so CI can assert that a gate measured something.
    """


class Failed(Exception):  # noqa: N818 - ditto
    """Raised when a task's command exited non-zero. Carries the exit status."""

    def __init__(self, code: int, detail: str = "") -> None:
        """Store the exit status so the CLI can propagate it.

        Args:
            code: The failing process's exit status.
            detail: Human-readable context.
        """
        super().__init__(detail or f"exit {code}")
        self.code = code


@dataclass(frozen=True)
class Guard:
    """A precondition on the repository layout.

    ``folder`` names a :class:`~rhiza_task.config.Config` field rather than a path, so
    ``Guard("source_folder")`` means "SOURCE_FOLDER must exist" without this module
    knowing that jointview sets it to ``src``.

    ``glob`` additionally requires a matching file below that folder -- the declarative
    form of python.mk's ``find ${TESTS_FOLDER} -name 'test_*.py'``.
    """

    folder: str
    glob: str | None = None
    reason: str = ""

    def check(self, root: Path, folders: dict[str, str]) -> None:
        """Raise :class:`Skip` when the guard is not satisfied.

        Args:
            root: Repository root.
            folders: Resolved folder settings, e.g. ``{"source_folder": "src"}``.

        Raises:
            Skip: When the folder is missing, or holds no file matching ``glob``.
        """
        name = folders.get(self.folder, self.folder)
        target = root / name
        if not target.is_dir():
            raise Skip(self.reason or f"{self.folder} '{name}' not found")
        if self.glob and not any(target.rglob(self.glob)):
            raise Skip(self.reason or f"no {self.glob} below '{name}'")


@dataclass(frozen=True)
class Task:
    """One gate: the unit the CLI exposes and the reusable workflows invoke.

    Attributes:
        name: The command name, e.g. ``test``. Deliberately identical to the retired make
            target, so the Makefile shim and a consumer's muscle memory need no
            translation table.
        help: One line, shown by ``rhiza-task list``. Replaces the ``##`` convention that
            rhiza.mk parsed with awk.
        section: Help grouping. Replaces ``##@``.
        run: The task body. Takes a config, returns nothing, raises :class:`Failed` or
            :class:`Skip`.
        needs: Tasks to run first. The runner dedupes within one invocation, which is what
            make gave for free and the reason ``install`` can be named by eleven tasks
            without being run eleven times.
        guards: Evaluated in order before the body.
        hidden: Omit from ``list``.
    """

    name: str
    help: str
    section: str
    run: Callable[[Config], None]
    needs: tuple[str, ...] = ()
    guards: tuple[Guard, ...] = ()
    hidden: bool = False


REGISTRY: dict[str, Task] = {}
"""Every registered task, keyed by name.

This dict replaces make's double-colon rules. book.mk has to declare ``test:: ; @:``
no-op stubs so that ``book`` can depend on ``test`` without knowing whether the ``tests``
bundle was synced; here the same question is ``"test" in REGISTRY``. Four stub
declarations and the whole ``::`` mechanism go away with it.
"""


def task(
    name: str,
    help: str,  # noqa: A002 - matches the CLI's own vocabulary
    section: str,
    needs: Sequence[str] = (),
    guards: Sequence[Guard] = (),
    hidden: bool = False,
) -> Callable[[Callable[[Config], None]], Callable[[Config], None]]:
    """Register a task and return the function unchanged.

    Returning the undecorated function keeps every task body directly unit-testable
    without going through the registry or the CLI.

    Args:
        name: Command name.
        help: One-line description.
        section: Help grouping.
        needs: Prerequisite task names.
        guards: Layout preconditions.
        hidden: Omit from ``list``.

    Returns:
        The decorator.
    """

    def decorate(fn: Callable[[Config], None]) -> Callable[[Config], None]:
        """Add the task to the registry.

        Args:
            fn: The task body.

        Returns:
            ``fn``, unchanged.
        """
        REGISTRY[name] = Task(
            name=name,
            help=help,
            section=section,
            run=fn,
            needs=tuple(needs),
            guards=tuple(guards),
            hidden=hidden,
        )
        return fn

    return decorate


def have(tool: str) -> bool:
    """Return whether ``tool`` is on PATH.

    Args:
        tool: Executable name.

    Returns:
        True when found.
    """
    return shutil.which(tool) is not None
