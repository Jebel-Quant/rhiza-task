"""Fixtures: a throwaway repository, and a recorder that stands in for uv.

No test in this suite runs uv. The point of extracting the make layer into a package is
that its logic becomes testable *without* provisioning a toolchain, so every test patches
**all five** of :mod:`rhiza_task.uv`'s entry points -- ``uv``, ``uvx``, ``uv_run``, ``tool``
and ``capture`` -- and asserts on the argument vectors that would have been executed. Those
vectors are the contract the make recipes expressed in shell.

Five, not three: ``tool`` is the form rust.mk and go.mk added for a toolchain binary
already on PATH, which uv neither provisions nor knows about, and ``capture`` is the one
that returns stdout rather than a status. :mod:`rhiza_task.uv` explains why each is its own
form rather than a variant of the others.

``capture`` is the late addition, and the reason it matters is worth keeping. It was left
out while the other four were patched here, so the twelve tests that needed it patched it
by hand -- and every one of them did, so nothing ever leaked. But the guarantee at the top
of this docstring was four fifths true, and it failed in the wrong direction: a *new* test
reaching a ``capture`` path without its own patch did not error, it ran ``gh`` or ``go`` for
real, and on an authenticated machine it would pass. A guarantee that holds because each
author remembers is not the guarantee this file claims to provide. See issue #116.

The module list is derived rather than written down, for the same reason. It used to be a
hand-maintained tuple of twelve names, which was complete but only by inspection; a new
module binding an entry point and not added here would have been handed real subprocesses
rather than an error -- the same fail-open shape one level up.

**And uv is not the only door.** Four task modules call :mod:`subprocess` directly, because
what they run is not a uv form: git, a ``--version`` probe, ``bash -n``, a cobertura pipe.
Those were patched per test, by hand, for the same reason ``capture`` was -- and the same way
it failed. :func:`no_real_subprocess` closes them for the whole suite, autouse and refusing
rather than recording, so the opening sentence of this docstring is now true of *any* tool
rather than of uv alone. It caught five tests on the first run: ``TestQuality``'s
``rhiza-test`` group was shelling out to real ``git tag --list``, and passing only because a
tmp directory is not usually inside a repository. See issue #151.
"""

from __future__ import annotations

import importlib
import pkgutil

# Imported to be *closed*, not to be used: `no_real_subprocess` below replaces this module's
# two entry points for the whole suite. Nothing here starts a process.
import subprocess  # nosec B404
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType

import pytest

from rhiza_task import cli, tasks
from rhiza_task.config import Config

UV_ENTRY_POINTS = ("uv", "uvx", "uv_run", "tool")
"""The four entry points returning an exit status. ``capture`` returns stdout, so it needs a
different stand-in and is handled separately."""

SUBPROCESS_ENTRY_POINTS = ("run", "call")
"""The two ways this package reaches a process without going through :mod:`rhiza_task.uv`.

``uv.py`` is not the only door. Four task modules import :mod:`subprocess` directly, because
what they run is not a uv form at all: ``tasks/quality.py``'s ``_git``, ``tasks/doctor.py``'s
version probe, ``tasks/fences.py``'s ``bash -n`` and ``tasks/go.py``'s cobertura pipe. Between
them they use exactly these two functions, which is why the guard below can be a pair of names
rather than a wrapper each module has to adopt.
"""


@dataclass
class Call:
    """One recorded invocation.

    Attributes:
        kind: Which entry point was used -- ``uv``, ``uvx``, ``uv_run``, ``tool`` or
            ``capture``.
        args: The positional arguments, tool name first for uvx/uv_run/tool.
        kwargs: The keyword arguments, notably ``withs`` and ``check``.
    """

    kind: str
    args: tuple[str, ...]
    kwargs: dict[str, object]

    @property
    def tool(self) -> str:
        """Return the tool or subcommand name.

        Returns:
            The first positional argument, or an empty string.
        """
        return self.args[0] if self.args else ""

    @property
    def flags(self) -> list[str]:
        """Return the arguments after the tool name.

        Returns:
            The remaining positional arguments.
        """
        return list(self.args[1:])


@dataclass
class Recorder:
    """Records uv invocations and replays canned exit statuses.

    Attributes:
        calls: Every recorded invocation, in order.
        codes: Exit statuses to return, consumed in order; 0 once exhausted.
    """

    calls: list[Call] = field(default_factory=list)
    codes: list[int] = field(default_factory=list)
    outputs: list[str] = field(default_factory=list)

    def make_capture(self) -> Callable[..., str]:
        """Build a stand-in for ``capture``, which returns stdout rather than a status.

        Its own builder rather than a fifth name in :data:`UV_ENTRY_POINTS`, because the
        shape genuinely differs: :meth:`make` returns a callable typed ``-> int`` that
        replays :attr:`codes` and raises :class:`~rhiza_task.spec.Failed` on a non-zero one,
        and neither behaviour means anything for a function whose whole purpose is to hand
        back a string. Conflating them would have needed a union return type at every call
        site to save one line here.

        Returns:
            A callable with the same shape as the real function, recording each call and
            replaying :attr:`outputs`.
        """

        def fake(*args: str, **kwargs: object) -> str:
            """Record the call and return the next canned stdout.

            Args:
                *args: The tool name and its arguments.
                **kwargs: The keyword arguments, notably ``cwd``.

            Returns:
                The next canned string, or an empty one once they are exhausted -- which is
                what the real ``capture`` returns for a command that printed nothing, so a
                test that does not care need not say so.
            """
            self.calls.append(Call("capture", args, kwargs))
            return self.outputs.pop(0) if self.outputs else ""

        return fake

    def make(self, kind: str) -> Callable[..., int]:
        """Build a stand-in for one uv entry point.

        Args:
            kind: One of :data:`UV_ENTRY_POINTS`. ``capture`` has its own builder.

        Returns:
            A callable with the same shape as the real function.
        """

        def fake(*args: str, **kwargs: object) -> int:
            """Record the call and return the next canned exit status.

            Args:
                *args: The tool name and its arguments.
                **kwargs: The keyword arguments, notably ``withs`` and ``check``.

            Returns:
                The canned exit status.

            Raises:
                Failed: When the status is non-zero and the caller asked for checking,
                    matching what the real functions do.
            """
            self.calls.append(Call(kind, args, kwargs))
            code = self.codes.pop(0) if self.codes else 0
            if code and kwargs.get("check", True):
                from rhiza_task.spec import Failed

                raise Failed(code, f"{args[0] if args else kind} failed")
            return code

        return fake

    def tools(self) -> list[str]:
        """Return the tool name of each recorded call.

        Returns:
            Tool names in invocation order.
        """
        return [c.tool for c in self.calls]

    def find(self, tool: str) -> Call:
        """Return the first recorded call for a tool.

        Args:
            tool: The tool name.

        Returns:
            The matching call.

        Raises:
            AssertionError: When the tool was never invoked.
        """
        for call in self.calls:
            if call.tool == tool:
                return call
        msg = f"{tool!r} was never invoked; got {self.tools()}"
        raise AssertionError(msg)


def task_modules() -> list[ModuleType]:
    """Import and return every module under :mod:`rhiza_task.tasks`.

    Public, and named without a leading underscore on purpose. It has two callers -- the
    ``recorder`` fixture below, which patches what it finds, and ``test_uv``'s assertion that
    nothing was left unpatched -- and ``CLAUDE.md``'s fourth layering rule says that a private
    helper needed in a second place gets promoted and documented rather than imported as-is.
    That rule is written about ``src/``, and the grep that checks it only reads ``src/``, but
    the reason it exists does not stop at a directory boundary: the pair below is exactly the
    "two callers need the same spelling" case the rule names.

    Discovered rather than listed. This used to be a tuple of twelve module names, which was
    accurate but only by inspection -- and wrong in the direction that does not announce
    itself: a module binding an entry point and not named there kept the *real* function, so
    its tests ran the real tool instead of failing. Walking the package cannot go stale.

    Importing them all is harmless and already happens twice over in this suite -- the
    ``registered`` fixture below does it, and ``test_doctests`` imports every module under
    ``src/`` -- because importing a task module only registers its tasks.

    Returns:
        Every submodule of :mod:`rhiza_task.tasks`, imported.
    """
    return [importlib.import_module(f"rhiza_task.tasks.{found.name}") for found in pkgutil.iter_modules(tasks.__path__)]


@pytest.fixture(autouse=True)
def no_real_subprocess(monkeypatch: pytest.MonkeyPatch) -> None:
    """Close the four doors :func:`recorder` cannot reach, for every test in the suite.

    ``recorder`` patches the five :mod:`rhiza_task.uv` entry points, and ``test_uv`` asserts
    that none leaked -- so the guarantee at the top of this file is checked rather than
    remembered. It was checked for *uv*. Four task modules reach :mod:`subprocess` directly,
    and each test that touched one patched it by hand; every one of them did, so nothing ever
    leaked, but the guarantee held the way ``capture``'s did before #116 -- by each author
    remembering, and failing **open** when one did not. A test that forgot ran real ``git``,
    ``bash`` or ``go`` and *passed* on a machine that had them. ``_git`` is the sharp end of
    that: it runs ``git branch -D``. See issue #151.

    **Autouse, and it refuses rather than records.** Refusing is what makes the failure mode
    right: there is no single argument-vector shape to record across a ``git`` invocation, a
    ``--version`` probe, a ``bash -n`` and a pipe with ``stdin``/``stdout`` handles, and a
    recorder that accepted all four would hand back a plausible zero to a test that never said
    what it expected. An ``AssertionError`` naming the vector cannot be mistaken for a pass.

    Patched on the :mod:`subprocess` module itself rather than per importer, because that is
    the object all five modules hold: ``quality.subprocess`` *is* this module. The tests that
    legitimately need a stand-in keep working unchanged -- they patch the same attribute from
    a non-autouse fixture or the test body, and a later patch wins.

    Args:
        monkeypatch: pytest's patcher.
    """

    def refuse(name: str) -> Callable[..., object]:
        """Build a stand-in for one subprocess entry point.

        Args:
            name: ``run`` or ``call``.

        Returns:
            A callable that raises rather than starting anything.
        """

        def guard(argv: object = None, *_args: object, **_kwargs: object) -> object:
            """Refuse to start a process, naming what would have been run.

            Args:
                argv: The argument vector the caller passed.
                *_args: Ignored.
                **_kwargs: Ignored.

            Raises:
                AssertionError: Always.
            """
            msg = f"no test in this suite runs a tool: subprocess.{name}({argv!r}) -- patch it"
            raise AssertionError(msg)

        return guard

    for name in SUBPROCESS_ENTRY_POINTS:
        monkeypatch.setattr(subprocess, name, refuse(name))


@pytest.fixture
def recorder(monkeypatch: pytest.MonkeyPatch) -> Recorder:
    """Patch every uv entry point in every task module, and return the recorder.

    The modules do ``from ..uv import uv, uv_run, uvx``, binding the functions into their own
    namespace, so patching :mod:`rhiza_task.uv` alone would not take effect -- each module's
    own binding has to be replaced, which is why this walks modules rather than patching one
    place.

    ``hasattr`` rather than a per-module list of which entry points it uses: the question is
    only whether a name is bound, and asking the module is both shorter and incapable of
    disagreeing with it.

    A test may still override any of these with its own ``monkeypatch.setattr`` -- several do,
    to canned ``capture`` output -- and that keeps working, because a later patch of the same
    attribute wins.

    Args:
        monkeypatch: pytest's patcher.

    Returns:
        The recorder collecting every call.
    """
    rec = Recorder()
    for module in task_modules():
        for kind in UV_ENTRY_POINTS:
            if hasattr(module, kind):
                monkeypatch.setattr(module, kind, rec.make(kind))
        if hasattr(module, "capture"):
            monkeypatch.setattr(module, "capture", rec.make_capture())
    return rec


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """Build a minimal Python project: src, tests with one test file, a pyproject.

    Args:
        tmp_path: pytest's temporary directory.

    Returns:
        The repository root.
    """
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "__init__.py").touch()
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_thing.py").write_text("def test_thing():\n    assert True\n")
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "demo"\nversion = "0.1.0"\n')
    return tmp_path


@pytest.fixture
def cfg(repo: Path) -> Config:
    """Return a config resolved against the throwaway repository.

    Args:
        repo: The repository root.

    Returns:
        The resolved config.
    """
    return Config.load(root=repo)


@pytest.fixture(autouse=True)
def registered() -> Iterator[None]:
    """Ensure every built-in task module is imported, and leave the registry as found.

    Registration is a side effect of import, so a test that inspects the registry needs the
    modules loaded; and a test that manipulates it must not leak into the next one.

    Yields:
        None.
    """
    from rhiza_task.spec import REGISTRY

    cli.load_tasks()
    snapshot = dict(REGISTRY)
    yield
    REGISTRY.clear()
    REGISTRY.update(snapshot)
