"""The provisioning layer: the argument vectors, and the environment they run in.

These tests patch ``subprocess.call`` rather than :mod:`rhiza_task.uv`, so they assert on
the vector that reaches the OS -- the thing the make recipes could only express as a shell
string, and the reason rhiza.mk needs a Windows shell probe that this package does not.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from rhiza_task import uv as uv_module
from rhiza_task.spec import Failed


@pytest.fixture
def spy(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, object]]:
    """Patch ``subprocess.call`` and collect what it was asked to run.

    Args:
        monkeypatch: pytest's patcher.

    Returns:
        A list that each invocation appends to.
    """
    seen: list[dict[str, object]] = []

    def fake_call(argv: list[str], cwd: Path, env: dict[str, str]) -> int:
        """Record the vector, the working directory and the environment.

        Args:
            argv: The argument vector.
            cwd: Working directory.
            env: The environment the command would run in.

        Returns:
            0, so the caller proceeds.
        """
        seen.append({"argv": argv, "cwd": cwd, "env": env})
        return 0

    monkeypatch.setattr(uv_module.subprocess, "call", fake_call)
    monkeypatch.setattr(uv_module, "_bin", lambda name, env_var: f"/fake/{name}")
    return seen


def test_uv_builds_a_plain_subcommand(spy: list[dict[str, object]], tmp_path: Path) -> None:
    """``uv sync --frozen`` is three argv entries, not a string to be parsed.

    Args:
        spy: The subprocess spy.
        tmp_path: Working directory.
    """
    uv_module.uv("sync", "--frozen", cwd=tmp_path)
    assert spy[0]["argv"] == ["/fake/uv", "sync", "--frozen"]


def test_uv_run_injects_each_with_flag(spy: list[dict[str, object]], tmp_path: Path) -> None:
    """Each injected package gets its own ``--with``, before the tool name.

    Args:
        spy: The subprocess spy.
        tmp_path: Working directory.
    """
    uv_module.uv_run("pytest", "-q", cwd=tmp_path, withs=("pytest", "pytest-cov"))
    assert spy[0]["argv"] == [
        "/fake/uv",
        "run",
        "--with",
        "pytest",
        "--with",
        "pytest-cov",
        "pytest",
        "-q",
    ]


def test_uv_run_can_bypass_the_project(spy: list[dict[str, object]], tmp_path: Path) -> None:
    """``--no-project`` precedes the injections, as marimo's editor needs.

    Args:
        spy: The subprocess spy.
        tmp_path: Working directory.
    """
    uv_module.uv_run("marimo", "edit", cwd=tmp_path, withs=("marimo",), no_project=True)
    assert spy[0]["argv"][:4] == ["/fake/uv", "run", "--no-project", "--with"]


def test_uvx_passes_the_interpreter_and_extras(spy: list[dict[str, object]], tmp_path: Path) -> None:
    """``-p`` and ``--with`` both land before the tool spec.

    Args:
        spy: The subprocess spy.
        tmp_path: Working directory.
    """
    uv_module.uvx("zensical>=0.0.36", "build", cwd=tmp_path, withs=("mkdocstrings[python]",), python="3.12")
    assert spy[0]["argv"] == [
        "/fake/uvx",
        "-p",
        "3.12",
        "--with",
        "mkdocstrings[python]",
        "zensical>=0.0.36",
        "build",
    ]


def test_a_stale_virtualenv_is_unset(
    spy: list[dict[str, object]], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``VIRTUAL_ENV`` is removed, which is rhiza.mk's ``unexport VIRTUAL_ENV``.

    Left in place, uv warns on every invocation that the activated environment is not the
    project's.

    Args:
        spy: The subprocess spy.
        tmp_path: Working directory.
        monkeypatch: pytest's patcher.
    """
    monkeypatch.setenv("VIRTUAL_ENV", "/somewhere/else")
    uv_module.uv("sync", cwd=tmp_path)
    env = spy[0]["env"]
    assert "VIRTUAL_ENV" not in env
    assert env["UV_NO_MODIFY_PATH"] == "1"


def test_extra_environment_is_merged(spy: list[dict[str, object]], tmp_path: Path) -> None:
    """A task's own variables are added rather than replacing the environment.

    Args:
        spy: The subprocess spy.
        tmp_path: Working directory.
    """
    uv_module.uv_run("pytest", cwd=tmp_path, env={"PYTEST_HTML_TITLE": "Hypothesis tests"})
    env = spy[0]["env"]
    assert env["PYTEST_HTML_TITLE"] == "Hypothesis tests"
    assert "PATH" in env


@pytest.mark.parametrize("entry", ["uv", "uvx", "uv_run"])
def test_non_zero_raises_by_default(entry: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """All three entry points raise :class:`Failed` unless asked not to.

    Args:
        entry: The function under test.
        tmp_path: Working directory.
        monkeypatch: pytest's patcher.
    """
    monkeypatch.setattr(uv_module.subprocess, "call", lambda *a, **k: 7)
    func = getattr(uv_module, entry)
    with pytest.raises(Failed) as excinfo:
        func("sync", cwd=tmp_path)
    assert excinfo.value.code == 7
    assert func("sync", cwd=tmp_path, check=False) == 7


def test_bin_resolution_prefers_the_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """``RHIZA_UV_BIN`` wins over PATH, for a pinned or vendored uv.

    Args:
        monkeypatch: pytest's patcher.
    """
    monkeypatch.setenv("RHIZA_UV_BIN", "/opt/uv")
    assert uv_module._bin("uv", "RHIZA_UV_BIN") == "/opt/uv"
    monkeypatch.delenv("RHIZA_UV_BIN")
    assert uv_module._bin("definitely-not-a-tool", "RHIZA_NOPE") == "definitely-not-a-tool"
