"""The provisioning layer: the argument vectors, and the environment they run in.

These tests patch ``subprocess.call`` rather than :mod:`rhiza_task.uv`, so they assert on
the vector that reaches the OS -- the thing the make recipes could only express as a shell
string, and the reason rhiza.mk needs a Windows shell probe that this package does not.
"""

from __future__ import annotations

import subprocess
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


def test_tool_runs_a_toolchain_binary_off_path(
    spy: list[dict[str, object]], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``tool`` is the fourth form: a binary uv does not provision.

    rust.mk and go.mk add exactly this one shape -- ``$(CARGO) nextest run``, ``$(GO)
    test`` -- because uv provisions neither cargo nor go. It shares this module's echoing
    and environment handling rather than being a bare ``subprocess.call`` per language
    module.

    Args:
        spy: The subprocess spy.
        tmp_path: Working directory.
        monkeypatch: pytest's patcher.
    """
    monkeypatch.setattr(uv_module.shutil, "which", lambda name: f"/fake/{name}")
    uv_module.tool("cargo", "clippy", "--", "-Dwarnings", cwd=tmp_path)
    assert spy[0]["argv"] == ["/fake/cargo", "clippy", "--", "-Dwarnings"]


def test_tool_falls_back_to_the_bare_name(
    spy: list[dict[str, object]], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unresolvable name is still passed, so the OS produces the error.

    Args:
        spy: The subprocess spy.
        tmp_path: Working directory.
        monkeypatch: pytest's patcher.
    """
    monkeypatch.setattr(uv_module.shutil, "which", lambda name: None)
    uv_module.tool("go", "vet", "./...", cwd=tmp_path)
    assert spy[0]["argv"] == ["go", "vet", "./..."]


def test_tool_propagates_the_toolchain_s_own_status(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``cargo``'s 101 reaches the caller, rather than collapsing to 1.

    Args:
        tmp_path: Working directory.
        monkeypatch: pytest's patcher.
    """
    monkeypatch.setattr(uv_module.subprocess, "call", lambda *a, **k: 101)
    with pytest.raises(Failed, match="cargo clippy failed") as excinfo:
        uv_module.tool("cargo", "clippy", cwd=tmp_path)
    assert excinfo.value.code == 101
    assert uv_module.tool("cargo", "clippy", cwd=tmp_path, check=False) == 101


def test_tool_failure_names_the_binary_not_its_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A tool reached by absolute path still reports under its own name.

    The Go layer resolves its helpers to ``$GOBIN/<tool>``, so without this the message
    would be a path a reader has to parse.

    Args:
        tmp_path: Working directory.
        monkeypatch: pytest's patcher.
    """
    monkeypatch.setattr(uv_module.subprocess, "call", lambda *a, **k: 3)
    with pytest.raises(Failed, match="govulncheck") as excinfo:
        uv_module.tool("/home/runner/go/bin/govulncheck", cwd=tmp_path)
    assert excinfo.value.code == 3
    assert "/home/runner" not in str(excinfo.value)


def test_capture_returns_stripped_stdout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The one recipe that needs a value back: ``go list -m``.

    Without the module's own path, go-licenses walks the project's own packages and fails
    a freshly synced project for having no LICENSE of its own.

    Args:
        tmp_path: Working directory.
        monkeypatch: pytest's patcher.
    """
    monkeypatch.setattr(uv_module.shutil, "which", lambda name: f"/fake/{name}")
    monkeypatch.setattr(
        uv_module.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(a[0], 0, "example.com/demo\n", ""),
    )
    assert uv_module.capture("go", "list", "-m", cwd=tmp_path) == "example.com/demo"


def test_capture_is_empty_when_the_tool_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A non-zero status yields no value, so the caller can warn rather than guess.

    Args:
        tmp_path: Working directory.
        monkeypatch: pytest's patcher.
    """
    monkeypatch.setattr(
        uv_module.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(a[0], 1, "half a line", "boom"),
    )
    assert uv_module.capture("go", "list", "-m", cwd=tmp_path) == ""


def test_capture_is_empty_when_the_tool_is_absent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An absent binary is an empty value, not an OSError escaping the gate.

    Args:
        tmp_path: Working directory.
        monkeypatch: pytest's patcher.
    """

    def absent(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        """Raise as the OS does for a missing executable.

        Args:
            *_args: Ignored.
            **_kwargs: Ignored.

        Returns:
            Never returns.

        Raises:
            OSError: Always.
        """
        raise OSError(2, "No such file or directory")

    monkeypatch.setattr(uv_module.subprocess, "run", absent)
    assert uv_module.capture("definitely-not-a-tool", cwd=tmp_path) == ""
