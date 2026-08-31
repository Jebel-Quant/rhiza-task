"""The provisioning layer: the argument vectors, and the environment they run in.

These tests patch ``subprocess.call`` rather than :mod:`rhiza_task.uv`, so they assert on
the vector that reaches the OS -- the thing the make recipes could only express as a shell
string, and the reason rhiza.mk needs a Windows shell probe that this package does not.
"""

from __future__ import annotations

import ast
import subprocess
from pathlib import Path

import pytest

from rhiza_task import uv as uv_module
from rhiza_task.spec import Failed
from rhiza_task.tasks import github as github_tasks

from .conftest import SUBPROCESS_ENTRY_POINTS, UV_ENTRY_POINTS, Recorder, starts_a_process, task_modules


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


def test_uv_run_can_pin_the_interpreter(spy: list[dict[str, object]], tmp_path: Path) -> None:
    """``--python`` precedes ``--no-project``, which ``update`` relies on together.

    The scripts that task drives belong to another project, pinned by it to 3.12, and are
    stdlib-only -- so the interpreter is named and the target repo's environment is not
    resolved. Both flags in one vector is the case worth pinning, since either alone would
    leave the other's ordering unasserted.

    Args:
        spy: The subprocess spy.
        tmp_path: Working directory.
    """
    uv_module.uv_run("python", "sync.py", cwd=tmp_path, no_project=True, python="3.12")
    assert spy[0]["argv"] == ["/fake/uv", "run", "--python", "3.12", "--no-project", "python", "sync.py"]


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


class TestTheSuiteStubsEveryEntryPoint:
    """The hermeticity guarantee, asserted rather than documented.

    ``conftest``'s docstring opens with "No test in this suite runs uv", and until issue #116
    that was four fifths true: the ``recorder`` fixture patched four of the five entry points
    and ``capture`` was left to each test to patch by hand. Every test that needed it did, so
    nothing leaked -- but the shape of the mistake is what matters. A forgotten patch did not
    raise, it ran ``gh`` or ``go`` for real and passed on an authenticated machine.

    A prose guarantee cannot fail; these can. That is the whole point of moving it here.
    """

    def test_the_recorder_replaces_every_entry_point_every_module_binds(self, recorder: Recorder) -> None:
        """No task module keeps a real uv entry point once the fixture has run.

        The assertion that would have caught #116, and the one that catches the next module
        added with an entry point the fixture does not know about -- both fail-open cases,
        because an unstubbed binding is a real subprocess rather than an error.

        Args:
            recorder: The uv recorder, whose construction is what patches the modules.
        """
        real = {name: getattr(uv_module, name) for name in (*UV_ENTRY_POINTS, "capture")}
        leaked = [
            f"{module.__name__}.{name}"
            for module in task_modules()
            for name, original in real.items()
            if getattr(module, name, None) is original
        ]
        assert leaked == [], f"unstubbed entry point(s) would run the real tool: {leaked}"

    def test_no_module_can_reach_a_real_subprocess(self) -> None:
        """The other four doors, closed by ``conftest``'s autouse guard.

        The assertion above covers :mod:`rhiza_task.uv`'s entry points. It does **not** cover
        the four modules that import :mod:`subprocess` directly, because what they run is not
        a uv form -- and until #151 those were patched per test, by hand, which is the same
        fail-open shape #116 fixed for ``capture``. This asserts the guard is in place rather
        than trusting that every future test remembers.

        Requests no fixture: the guard is autouse, so a test that asks for nothing at all is
        exactly the case that must still be covered.

        Raises:
            AssertionError: Through the guard, which is what is being asserted.
        """
        for name in SUBPROCESS_ENTRY_POINTS:
            with pytest.raises(AssertionError, match="no test in this suite runs a tool"):
                getattr(subprocess, name)(["definitely-not-a-tool"])

    def test_the_guard_covers_every_way_subprocess_starts_a_process(self) -> None:
        """The patched set is derived from the stdlib, so it cannot go stale as `src/` changes.

        `("run", "call")` was the hand-written pair this replaces. It was accurate, and
        accurate-by-inspection is the property that failed at #116 and again at #151 -- so
        #157 applied the cure `conftest` already applies to the module list. The sanity
        check is that the derivation did not quietly collapse: if the exclusion rules ever
        matched everything, the guard would patch nothing and every test would pass.
        """
        assert {"run", "call", "Popen", "check_output"} <= set(SUBPROCESS_ENTRY_POINTS)
        assert not [n for n in SUBPROCESS_ENTRY_POINTS if isinstance(getattr(subprocess, n), int)]

    def test_src_starts_no_process_the_guard_cannot_see(self) -> None:
        """`src/` reaches a process through subprocess and nothing else -- checked by shape.

        The derived set above closes :mod:`subprocess`. It cannot close :mod:`os`, which is
        not stubbable here -- every module uses it for paths and the environment, so
        replacing its attributes would break the suite rather than protect it. This asserts
        the same guarantee from the other side.

        **Matched by shape rather than against a list of names**, which is #162: the list
        this replaces enumerated 22 `os` names and was already missing `os.startfile`. A
        `.startswith(("exec", "spawn", ...))` cannot be short in that way, and it also covers
        the doors the list never considered -- `asyncio.create_subprocess_exec`, `pty.spawn`.

        Two node kinds, because a name can arrive either way: `os.system(...)` is an
        attribute, and `from os import system` followed by `system(...)` is not. The second
        is the one a list-based check reading only attributes would have missed entirely.

        Reading the AST rather than grepping, because a comment or a docstring mentioning
        `os.system` -- this one, twice -- is not a call, and a grep cannot tell the
        difference.
        """
        offenders = []
        for path in sorted(Path("src").rglob("*.py")):
            for node in ast.walk(ast.parse(path.read_text())):
                if isinstance(node, ast.Attribute) and starts_a_process(node.attr):
                    offenders.append(f"{path}:{node.lineno}: .{node.attr}")
                elif isinstance(node, ast.ImportFrom):
                    offenders += [
                        f"{path}:{node.lineno}: from {node.module} import {alias.name}"
                        for alias in node.names
                        if starts_a_process(alias.name)
                    ]
        assert offenders == [], f"process started outside the guard's reach: {offenders}"

    @pytest.mark.parametrize(
        ("name", "expected"),
        [
            ("system", True),
            ("popen", True),
            ("startfile", True),
            ("execvp", True),
            ("spawnl", True),
            ("create_subprocess_exec", True),
            ("path", False),
            ("environ", False),
            ("which", False),
        ],
    )
    def test_the_shape_check_recognises_a_starter(self, name: str, expected: bool) -> None:
        """The predicate itself, including the name the list it replaced had missed.

        `startfile` is the case with history: it is Windows-only, so a set derived from
        `dir(os)` on the machine running the tests would omit it on Linux and macOS --
        silently, and in the direction that passes. Pinned here so the shape check cannot
        regress to a platform-dependent answer.

        Args:
            name: The attribute name.
            expected: Whether it should be recognised as a way to start a process.
        """
        assert starts_a_process(name) is expected

    def test_every_direct_subprocess_user_is_covered_by_that_guard(self) -> None:
        """Each module reaching subprocess directly holds the guarded module, not a copy.

        ``import subprocess`` binds the module object, so patching its attributes reaches
        every importer -- but that is a property of how these modules import it, not a law.
        A module that switched to ``from subprocess import run`` would bind the *function*
        into its own namespace and slip the guard silently, which is the regression this
        pins.
        """
        holders = [uv_module, *task_modules()]
        direct = [m for m in holders if getattr(m, "subprocess", None) is not None]
        assert [m.__name__ for m in direct if m.subprocess is not subprocess] == []
        assert len(direct) >= 5, f"expected uv.py and the four direct users, found {len(direct)}"

    def test_capture_is_recorded_and_replays_canned_output(self, recorder: Recorder) -> None:
        """``capture`` records like the others and hands back the next canned string.

        Its stand-in is built separately because it returns stdout rather than a status, so
        this pins both halves: that the call is recorded under its own kind, and that the
        queue is consumed.

        Args:
            recorder: The uv recorder.
        """
        recorder.outputs.append("v1.2.3")
        assert github_tasks.capture("gh", "release", "view", cwd=Path()) == "v1.2.3"
        # Exhausted, not repeated: the real `capture` returns "" for a command that printed
        # nothing, so that is the honest default rather than replaying the last answer.
        assert github_tasks.capture("gh", "release", "view", cwd=Path()) == ""
        assert [call.kind for call in recorder.calls] == ["capture", "capture"]
