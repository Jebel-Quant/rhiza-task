"""The task model: what a rhiza gate *is*, independently of how it is invoked.

Reading all ten make fragments back to back, every recipe has the same three parts:

1. A **guard** -- ``if [ -d ${SOURCE_FOLDER} ]``, or a ``find`` for test files. When it
   fails the recipe prints a yellow WARN and exits 0.
2. A **provision** -- ``uvx <tool>`` or ``uv run --with a --with b <tool>``.
3. An **invocation** -- a long, mostly static argument list with a few substitutions.

Only three recipes in the whole layer need more than that: ``test`` (retry on pytest exit
3), ``doctor`` (version comparison) and ``book`` (a per-notebook export loop). So the model
here is declarative, and the task body is the escape hatch those three use.

The split decides what is *data* -- reviewable, diffable, overridable from a consumer's
``pyproject.toml`` -- and what is code.

The other thing a task carries is its **layer**. rhiza has three language layers whose
gates share a name and differ only in engine -- ``test`` is pytest, ``cargo nextest`` or
``go test`` -- and the make layer expressed that by syncing exactly one of python.mk,
rust.mk and go.mk into a repo, so the question never arose at runtime. Here all three are
installed at once, so the layer is part of the key: ``python:test`` and ``rust:test`` are
distinct entries, and :func:`lookup` resolves the bare name against the layers the
repository actually has. A task with no layer -- ``fmt``, ``todos``, ``book`` -- is
language-neutral and answers to its bare name, which is what ``core`` was.
"""

from __future__ import annotations

import shutil
from collections.abc import Callable, Iterator, Sequence
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


# This class used to hold five flat ``if ... raise Skip`` clauses, which scored C (14)
# against `complexity_max = 15` -- one branch of headroom, so the sixth guard kind would
# have tripped `rhiza-task complexity` rather than merely approached it. It is now the
# decomposition that comment named: :func:`_clauses` yields one ``(unmet, message)`` pair
# per precondition and :meth:`Guard.check` raises on the first unmet one.
#
# Three properties the flat form had, kept deliberately:
#
# * **The order still reads off the source** -- tool before file before folder before glob,
#   cheapest and most likely first. That ordering was the whole reason the flat form was
#   defended, and a generator preserves it where a dict of predicates would not.
# * **Evaluation is still lazy.** Because :func:`_clauses` is a generator consumed one pair
#   at a time, a guard whose tool is absent never stats the filesystem -- the same work the
#   flat form did, in the same order. A tuple of eagerly-built pairs would have run every
#   predicate before testing the first.
# * **A sixth guard kind now costs one ``yield``**, not two branches in an already-full
#   block. That is the point of having done this: the next kind is an edit, not a
#   decomposition.
#
# `_clauses` is a module-level function rather than a second method, and that is load-bearing
# rather than stylistic: radon scores a *class* as the sum of its methods, so moving these
# branches to `Guard._clauses` would have relocated the 14 and reduced nothing. As written
# the class scores A. Being module-private it also stays inside this module, which rule 4 of
# CLAUDE.md's layering invariant requires of any underscore-prefixed name.
@dataclass(frozen=True)
class Guard:
    """A precondition on the repository layout.

    ``folder`` names a :class:`~rhiza_task.config.Config` field rather than a path, so
    ``Guard("source_folder")`` means "SOURCE_FOLDER must exist" without this module
    knowing that jointview sets it to ``src``.

    ``glob`` additionally requires a matching file below that folder -- the declarative
    form of python.mk's ``find ${TESTS_FOLDER} -name 'test_*.py'``.

    ``file`` is the flat case the Rust and Go layers need: their gates are guarded on a
    manifest rather than a folder, because ``cargo`` and ``go`` find the sources
    themselves. It is a literal path, not a config field -- ``Cargo.toml`` and ``go.mod``
    are named by their toolchains and are not a repository's choice to make.

    ``tool`` is a precondition on the *machine* rather than on the repository, and it is
    what github.mk's ``require-gh`` was: a target whose whole body is
    ``command -v gh >/dev/null || exit 1``, declared as a prerequisite of every helper.
    The five bundle-owned fragments are mostly wrappers over a CLI nobody can assume is
    installed -- gh, docker, git-lfs, latexmk, marp -- so the check is declared once here
    rather than repeated as the first three lines of a dozen task bodies.

    A missing tool is a :class:`Skip`, not a failure, which is a deliberate change from
    ``require-gh``'s hard exit. Nothing here is a gate, so a machine without docker should
    not fail a run that asked for something else too -- and ``--strict`` is the switch for
    a caller who does want it to.
    """

    folder: str | None = None
    glob: str | None = None
    reason: str = ""
    file: str | None = None
    tool: str | None = None

    def check(self, root: Path, folders: dict[str, str]) -> None:
        """Raise :class:`Skip` when the guard is not satisfied.

        Args:
            root: Repository root.
            folders: Resolved folder settings, e.g. ``{"source_folder": "src"}``.

        Raises:
            Skip: When the tool is absent, or the file is missing, or the folder is
                missing, or the folder holds no file matching ``glob``.

        Examples:
            A satisfied guard returns nothing, which is the whole of its success case:

            >>> import tempfile
            >>> from pathlib import Path
            >>> tmp = tempfile.TemporaryDirectory()
            >>> root = Path(tmp.name)
            >>> (root / "src").mkdir()
            >>> folders = {"source_folder": "src", "tests_folder": "tests"}
            >>> Guard("source_folder").check(root, folders)

            Each way of not being satisfied raises :class:`Skip` carrying the line the
            runner prints, and ``folder`` is resolved through *folders* -- so the guard
            names a setting and never a path:

            >>> for guard in (
            ...     Guard("tests_folder"),
            ...     Guard("source_folder", glob="test_*.py"),
            ...     Guard(file="Cargo.toml"),
            ...     Guard(tool="a-tool-nobody-has"),
            ... ):
            ...     try:
            ...         guard.check(root, folders)
            ...     except Skip as exc:
            ...         print(exc)
            tests_folder 'tests' not found
            no test_*.py below 'src'
            no Cargo.toml
            a-tool-nobody-has not found

            ``reason`` replaces the generated message wherever a task has something more
            useful to say:

            >>> try:
            ...     Guard("tests_folder", glob="test_*.py", reason="no test files found").check(root, folders)
            ... except Skip as exc:
            ...     print(exc)
            no test files found
            >>> tmp.cleanup()
        """
        # The first unmet precondition wins, so `reason` overrides whichever message that
        # clause generated -- see the note above the class for why the clauses live in a
        # module-level generator rather than in this body or in a second method.
        for unmet, message in _clauses(self, root, folders):
            if unmet:
                raise Skip(self.reason or message)


def _clauses(guard: Guard, root: Path, folders: dict[str, str]) -> Iterator[tuple[bool, str]]:
    """Yield each of *guard*'s preconditions as ``(unmet, message)``, in firing order.

    The order is the contract: tool before file before folder before glob, cheapest and
    most likely first. Being a generator, a pair is only produced -- and its predicate only
    evaluated -- once the caller has consumed every earlier one, so the filesystem is left
    alone when an earlier clause has already decided the outcome.

    ``folder`` is tested with ``is not None`` rather than for truthiness, unlike the other
    three: ``Guard(folder="")`` means the repository root, which is a satisfiable guard,
    where an empty ``tool`` or ``file`` names nothing at all.

    Args:
        guard: The guard whose fields describe the preconditions.
        root: Repository root.
        folders: Resolved folder settings, e.g. ``{"source_folder": "src"}``.

    Yields:
        One ``(unmet, message)`` pair per precondition the guard declares. ``unmet`` is
        True when the precondition fails; ``message`` is the line the runner prints unless
        the guard carries its own ``reason``.
    """
    if guard.tool:
        yield not have(guard.tool), f"{guard.tool} not found"
    if guard.file:
        yield not (root / guard.file).is_file(), f"no {guard.file}"
    if guard.folder is not None:
        name = folders.get(guard.folder, guard.folder)
        target = root / name
        yield not target.is_dir(), f"{guard.folder} '{name}' not found"
        if guard.glob:
            yield not any(target.rglob(guard.glob)), f"no {guard.glob} below '{name}'"


@dataclass(frozen=True)
class Task:
    """One gate: the unit the CLI exposes and the reusable workflows invoke.

    Attributes:
        name: The command name, e.g. ``test``. Deliberately identical to the retired make
            target, so the Makefile shim and a consumer's muscle memory need no
            translation table.
        layer: ``python``, ``rust``, ``go``, or None for a language-neutral task. Three
            layers can define ``test``; which one answers is decided per repository by
            :func:`lookup`, not by which bundle happened to be synced.
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
    layer: str | None = None

    @property
    def key(self) -> str:
        """Return the registry key: ``layer:name``, or ``name`` when neutral.

        Returns:
            The key this task is registered under.
        """
        return key(self.name, self.layer)


REGISTRY: dict[str, Task] = {}
"""Every registered task, keyed by ``layer:name`` -- or by bare ``name`` when neutral.

This dict replaces make's double-colon rules. book.mk has to declare ``test:: ; @:``
no-op stubs so that ``book`` can depend on ``test`` without knowing whether the ``tests``
bundle was synced; here the same question is :func:`lookup`. Four stub declarations and
the whole ``::`` mechanism go away with it.
"""


def task(
    name: str,
    help: str,  # noqa: A002 - matches the CLI's own vocabulary
    section: str,
    needs: Sequence[str] = (),
    guards: Sequence[Guard] = (),
    hidden: bool = False,
    layer: str | None = None,
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
        layer: The language layer this task belongs to, or None for a neutral task.

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
        spec = Task(
            name=name,
            help=help,
            section=section,
            run=fn,
            needs=tuple(needs),
            guards=tuple(guards),
            hidden=hidden,
            layer=layer,
        )
        REGISTRY[spec.key] = spec
        return fn

    return decorate


def key(name: str, layer: str | None = None) -> str:
    """Return the registry key for a task name in a layer.

    Args:
        name: The task name, e.g. ``test``.
        layer: The layer, or None for a neutral task.

    Returns:
        ``layer:name``, or ``name`` when there is no layer.
    """
    return f"{layer}:{name}" if layer else name


def lookup(name: str, layers: Sequence[str] = ()) -> Task | None:
    """Resolve a task name against the repository's language layers.

    A layered task shadows a neutral one of the same name, and the layers are tried in
    order, so a repository that is both -- a crate with a Python binding package -- gets a
    single answer rather than an ambiguity. ``rust:test`` addresses one layer explicitly,
    which is the only way to reach the layer that did not win.

    Args:
        name: A bare task name, or a ``layer:name`` key.
        layers: The active layers, most significant first.

    Returns:
        The task, or None when nothing matches.

    Examples:
        Importing a task module is what registers its tasks -- the entry point group in
        ``pyproject.toml`` only decides *which* modules the CLI imports:

        >>> from rhiza_task.tasks import python, quality, rust
        >>> lookup("test", ["python"]).help
        'run all tests'
        >>> lookup("test", ["rust"]).help
        'run the test suite with nextest, then the doctests'

        The layers are tried in order, so a crate that has grown a Python package gets one
        answer rather than an ambiguity -- and the explicit key is how the layer that lost
        is still reachable:

        >>> lookup("test", ["python", "rust"]).key
        'python:test'
        >>> lookup("test", ["rust", "python"]).key
        'rust:test'
        >>> lookup("rust:test", ["python"]).key
        'rust:test'

        A neutral task answers to its bare name whatever the layers are, and a name no
        active layer has is None rather than an error -- which is what lets ``book``
        depend on gates a repository may not have, in place of make's ``test:: ; @:``
        no-op stubs:

        >>> lookup("fmt", ["rust"]).key
        'fmt'
        >>> lookup("cargo-tools", ["python"]) is None
        True
    """
    if ":" in name:
        return REGISTRY.get(name)
    for layer in layers:
        if (spec := REGISTRY.get(key(name, layer))) is not None:
            return spec
    return REGISTRY.get(name)


def have(tool: str) -> bool:
    """Return whether ``tool`` is on PATH.

    Args:
        tool: Executable name.

    Returns:
        True when found.
    """
    return shutil.which(tool) is not None
