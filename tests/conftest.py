"""Fixtures: a throwaway repository, and a recorder that stands in for uv.

No test in this suite runs uv. The point of extracting the make layer into a package is
that its logic becomes testable *without* provisioning a toolchain, so every test patches
:mod:`rhiza_task.uv`'s three entry points and asserts on the argument vectors that would
have been executed. Those vectors are the contract the make recipes expressed in shell.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from rhiza_task import cli
from rhiza_task.config import Config


@dataclass
class Call:
    """One recorded invocation.

    Attributes:
        kind: Which entry point was used -- ``uv``, ``uvx``, ``uv_run`` or ``tool``.
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

    def make(self, kind: str) -> Callable[..., int]:
        """Build a stand-in for one uv entry point.

        Args:
            kind: ``uv``, ``uvx``, ``uv_run`` or ``tool``.

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


@pytest.fixture
def recorder(monkeypatch: pytest.MonkeyPatch) -> Recorder:
    """Patch uv in every task module and return the recorder.

    The task modules do ``from ..uv import uv, uv_run, uvx``, binding the functions into
    their own namespace, so patching :mod:`rhiza_task.uv` alone would not take effect. The
    Rust and Go modules bind ``tool`` the same way, so it is patched alongside.

    Args:
        monkeypatch: pytest's patcher.

    Returns:
        The recorder collecting every call.
    """
    rec = Recorder()
    modules = ("python", "quality", "extras", "book", "rust", "go")
    for name in modules:
        module = pytest.importorskip(f"rhiza_task.tasks.{name}")
        for kind in ("uv", "uvx", "uv_run", "tool"):
            if hasattr(module, kind):
                monkeypatch.setattr(module, kind, rec.make(kind))
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
