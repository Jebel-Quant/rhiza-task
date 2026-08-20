"""The generated Makefile, exercised with make itself rather than only read as text.

The shim is the one artefact this package ships that is not Python, and its failure modes
are make's, not the CLI's: a circular dependency, a match-anything rule applied to the
makefile itself, a bootstrap that runs twice or never. None of those are visible in the
file, so the tests below run make against a throwaway checkout with a stub ``uvx`` on
PATH -- no network and no uv. Mostly under ``-n``, which prints every recipe rather than
executing it; the exceptions are the PATH-export tests, which need a recipe's actual
environment and so run a ``local.mk`` target that shells out to nothing but ``echo``.
"""

from __future__ import annotations

import os
import shutil
import subprocess  # nosec B404 - fixed argument vector
import sys
from pathlib import Path

import pytest

SHIM = Path(__file__).parent.parent / "src" / "rhiza_task" / "templates" / "Makefile"

pytestmark = pytest.mark.skipif(
    shutil.which("make") is None or sys.platform == "win32",
    reason="needs GNU make and a POSIX shell",
)


def _make(root: Path, *args: str, path: str) -> subprocess.CompletedProcess[str]:
    """Run ``make -n`` in ``root`` with a controlled PATH.

    Args:
        root: Directory holding the shim.
        *args: Goals to pass to make.
        path: The PATH the invocation sees.

    Returns:
        The completed process, with output captured.
    """
    env = dict(os.environ, PATH=path)
    env.pop("MAKEFLAGS", None)
    return subprocess.run(  # nosec B603
        [shutil.which("make") or "make", "-n", *args],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def _make_run(root: Path, *args: str, path: str) -> subprocess.CompletedProcess[str]:
    """Run make for real in ``root`` with a controlled PATH.

    The sibling of :func:`_make`, without ``-n``. The PATH export is only observable in a
    recipe's *environment*, which ``-n`` never builds -- it prints the recipe instead of
    handing it to a shell.

    Args:
        root: Directory holding the shim.
        *args: Goals to pass to make.
        path: The PATH the invocation sees.

    Returns:
        The completed process, with output captured.
    """
    env = dict(os.environ, PATH=path)
    env.pop("MAKEFLAGS", None)
    return subprocess.run(  # nosec B603
        [shutil.which("make") or "make", *args],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.fixture
def shim(tmp_path: Path) -> Path:
    """Write the shim into a throwaway directory.

    Args:
        tmp_path: pytest's temporary directory.

    Returns:
        The directory holding the Makefile.
    """
    (tmp_path / "Makefile").write_text(SHIM.read_text())
    return tmp_path


@pytest.fixture
def stub_path(tmp_path: Path) -> str:
    """Return a PATH whose only ``uvx`` is a stub that does nothing.

    Args:
        tmp_path: pytest's temporary directory.

    Returns:
        A PATH string with the stub directory first.
    """
    stub_dir = tmp_path / "stub-bin"
    stub_dir.mkdir()
    stub = stub_dir / "uvx"
    stub.write_text("#!/bin/sh\nexit 0\n")
    stub.chmod(0o755)
    return os.pathsep.join([str(stub_dir), "/usr/bin", "/bin"])


def test_uvx_on_path_is_used_as_is(shim: Path, stub_path: str) -> None:
    """With uvx already available, the shim delegates and installs nothing.

    Args:
        shim: The directory holding the Makefile.
        stub_path: A PATH whose ``uvx`` is a stub.
    """
    result = _make(shim, "test", path=stub_path)
    assert result.returncode == 0, result.stderr
    assert "rhiza-task@" in result.stdout
    assert result.stdout.rstrip().endswith("test")
    assert "astral.sh" not in result.stdout
    # The regression that made `$(UVX)` the bootstrap rule's own target: with no rule of
    # its own, an on-PATH uvx is matched by the catch-all whose prerequisite it is.
    assert "Circular" not in result.stderr


def test_missing_uvx_is_bootstrapped_before_the_task(shim: Path) -> None:
    """With no uv anywhere, the shim installs one into ``./bin`` and then delegates.

    This is the case that blocks the migration: rhiza's own ``pre-commit`` job is a
    required status check that runs ``make fmt`` on a runner with no uv and no
    ``astral-sh/setup-uv`` step.

    Args:
        shim: The directory holding the Makefile.
    """
    result = _make(shim, "fmt", path=os.pathsep.join(["/usr/bin", "/bin"]))
    assert result.returncode == 0, result.stderr
    printed = result.stdout.splitlines()
    assert any("astral.sh/uv/install.sh" in line for line in printed)
    assert str(shim / "bin" / "uvx") in result.stdout
    # Order matters: provisioning uv after invoking it would help nobody.
    assert next(i for i, line in enumerate(printed) if "astral.sh" in line) < len(printed) - 1
    assert result.stdout.rstrip().endswith("fmt")
    # `-n` prints; nothing may actually be downloaded.
    assert not (shim / "bin").exists()


def test_the_makefile_is_not_remade_through_the_catch_all(shim: Path) -> None:
    """A cold checkout does not open by asking the CLI to build ``Makefile``.

    The bootstrap rule creates a file *newer* than the Makefile, which makes make consider
    the Makefile out of date and remake it through the match-anything rule -- so the first
    ``make`` on a machine without uv died with ``unknown task: Makefile`` immediately after
    installing uv. The empty ``Makefile: ;`` rule is what stops it.

    Args:
        shim: The directory holding the Makefile.
    """
    result = _make(shim, "test", path=os.pathsep.join(["/usr/bin", "/bin"]))
    assert "rhiza-task@" in result.stdout
    assert " Makefile" not in result.stdout
    assert "local.mk" not in result.stdout


def test_local_mk_wins_over_the_catch_all(shim: Path, stub_path: str) -> None:
    """A repo-owned target in ``local.mk`` is an explicit rule and beats the pattern.

    Args:
        shim: The directory holding the Makefile.
        stub_path: A PATH whose ``uvx`` is a stub.
    """
    (shim / "local.mk").write_text("sync-self:\n\t@echo repo-owned\n")
    result = _make(shim, "sync-self", path=stub_path)
    assert result.returncode == 0, result.stderr
    assert "repo-owned" in result.stdout
    assert "rhiza-task@" not in result.stdout


def test_install_dir_is_exported_first_on_path(shim: Path, stub_path: str) -> None:
    """A recipe's environment has ``./bin`` on PATH, ahead of everything inherited.

    The other half of the bootstrap: the shim reaches the CLI by absolute path, but the
    CLI's task bodies shell out to bare ``uv``/``uvx``, so a child that inherits a PATH
    without ``./bin`` dies with ``FileNotFoundError: 'uvx'`` on the first gate that shells
    out -- immediately after a bootstrap that appeared to succeed.

    Args:
        shim: The directory holding the Makefile.
        stub_path: A PATH whose ``uvx`` is a stub.
    """
    (shim / "local.mk").write_text('show-path:\n\t@echo "$$PATH"\n')
    result = _make_run(shim, "show-path", path=stub_path)
    assert result.returncode == 0, result.stderr
    entries = result.stdout.strip().split(os.pathsep)
    # First, not merely present: an older uv earlier on PATH would otherwise win, and
    # RHIZA_TASK would stop being the whole version contract.
    assert entries[0] == str(shim / "bin")
    # And nothing inherited is dropped or reordered behind it.
    assert entries[1:] == stub_path.split(os.pathsep)


def test_the_exported_path_survives_an_overridden_install_dir(shim: Path, stub_path: str) -> None:
    """``INSTALL_DIR=/somewhere`` moves the exported entry too, not just the bootstrap.

    ``INSTALL_DIR`` is a ``?=`` override, and the export is computed from it, so the two
    cannot drift into provisioning one directory and exporting another.

    Args:
        shim: The directory holding the Makefile.
        stub_path: A PATH whose ``uvx`` is a stub.
    """
    (shim / "local.mk").write_text('show-path:\n\t@echo "$$PATH"\n')
    elsewhere = shim / "elsewhere"
    result = _make_run(shim, f"INSTALL_DIR={elsewhere}", "show-path", path=stub_path)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().split(os.pathsep)[0] == str(elsewhere)


NAMED = (
    "fmt",
    "test",
    "typecheck",
    "coverage",
    "all",
    "deps",
    "docs-coverage",
    "license",
    "security",
    "rhiza-test",
    "install",
    "clean",
    "doctor",
    "book",
    "paper",
    "presentation",
)
"""The tasks the shim spells out as rules of their own, rather than leaving to `%:`."""


@pytest.mark.parametrize("task", NAMED)
def test_named_tasks_forward_their_own_name(shim: Path, stub_path: str, task: str) -> None:
    """Each spelled-out rule invokes the task it is named for.

    A copy-paste in fourteen near-identical rules is silent: the recipe still runs and the
    CLI still succeeds, just for the wrong task.

    Args:
        shim: The directory holding the Makefile.
        stub_path: A PATH whose ``uvx`` is a stub.
        task: The target to make.
    """
    result = _make(shim, task, path=stub_path)
    assert result.returncode == 0, result.stderr
    assert result.stdout.rstrip().endswith(f"rhiza-task@0.3.1 {task}")


@pytest.mark.parametrize("task", NAMED)
def test_named_tasks_are_phony(shim: Path, stub_path: str, task: str) -> None:
    """A file or directory sharing a task's name does not shadow it.

    The catch-all could not be protected this way -- ``.PHONY`` cannot name targets make
    has not heard of -- so ``make book`` in a repo with a ``book/`` directory newer than
    ``bin/uvx`` was "nothing to be done for 'book'". Naming the rules is what makes the
    declaration possible.

    Args:
        shim: The directory holding the Makefile.
        stub_path: A PATH whose ``uvx`` is a stub.
        task: The target to make, also created as a directory.
    """
    (shim / task).mkdir()
    result = _make(shim, task, path=stub_path)
    assert result.returncode == 0, result.stderr
    assert result.stdout.rstrip().endswith(f"rhiza-task@0.3.1 {task}")
