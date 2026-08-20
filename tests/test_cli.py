"""The CLI surface, including the bare-task shorthand that the Makefile shim relies on."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from rhiza_task import cli

runner = CliRunner()


def test_list_shows_every_task() -> None:
    """``list`` replaces the awk help parser, and names the real gates."""
    result = runner.invoke(cli.app, ["list"])
    assert result.exit_code == 0
    for expected in ("test", "typecheck", "rhiza-test", "book", "doctor"):
        assert expected in result.stdout


def test_print_resolves_either_spelling(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``print`` accepts the make variable name or the field name.

    Args:
        repo: The throwaway repository.
        monkeypatch: pytest's patcher.
    """
    monkeypatch.chdir(repo)
    assert "src" in runner.invoke(cli.app, ["print", "SOURCE_FOLDER"]).stdout
    assert "src" in runner.invoke(cli.app, ["print", "source_folder"]).stdout
    # RHIZA_CHECKS is the one name where the prefix is part of the field, not a prefix.
    assert "pytest_rhiza" in runner.invoke(cli.app, ["print", "RHIZA_CHECKS"]).stdout


def test_print_rejects_an_unknown_setting(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A typo is a usage error, not an empty line.

    Args:
        repo: The throwaway repository.
        monkeypatch: pytest's patcher.
    """
    monkeypatch.chdir(repo)
    assert runner.invoke(cli.app, ["print", "nonsense"]).exit_code == 2


def test_ci_os_matrix_emits_json(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The matrix must be parseable by GitHub Actions' ``fromJSON``.

    Args:
        repo: The throwaway repository.
        monkeypatch: pytest's patcher.
    """
    (repo / ".rhiza").mkdir()
    (repo / ".rhiza" / ".env").write_text('RHIZA_CI_OS_MATRIX=["ubuntu-latest","windows-latest"]\n')
    monkeypatch.chdir(repo)
    result = runner.invoke(cli.app, ["ci-os-matrix"])
    assert json.loads(result.stdout) == ["ubuntu-latest", "windows-latest"]


def test_ci_os_matrix_never_emits_an_empty_array(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An explicitly empty setting floors to the default rather than to zero jobs.

    GitHub does not fail a workflow over an empty matrix; it expands it to no jobs at all,
    so the gate goes green having tested nothing.

    Args:
        repo: The throwaway repository.
        monkeypatch: pytest's patcher.
    """
    (repo / ".rhiza").mkdir()
    (repo / ".rhiza" / ".env").write_text("RHIZA_CI_OS_MATRIX=[]\n")
    monkeypatch.chdir(repo)
    result = runner.invoke(cli.app, ["ci-os-matrix"])
    assert result.exit_code == 0
    assert json.loads(result.stdout) == ["ubuntu-latest"]


def test_ci_os_matrix_ignores_an_empty_environment_override(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The consumer's ``.rhiza/.env`` still answers when the workflow exports an empty var.

    This is the contract rhiza_ci.yml's ``generate-matrix`` job depends on once it calls
    the CLI instead of ``make -f .rhiza/rhiza.mk -s ci-os-matrix``.

    Args:
        repo: The throwaway repository.
        monkeypatch: pytest's patcher.
    """
    (repo / ".rhiza").mkdir()
    (repo / ".rhiza" / ".env").write_text('RHIZA_CI_OS_MATRIX=["macos-latest"]\n')
    monkeypatch.setenv("RHIZA_CI_OS_MATRIX", "")
    monkeypatch.chdir(repo)
    result = runner.invoke(cli.app, ["ci-os-matrix"])
    assert json.loads(result.stdout) == ["macos-latest"]


def test_unknown_task_exits_two_and_suggests_list(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A typo names itself and points at ``list``, rather than doing nothing.

    Args:
        repo: The throwaway repository.
        monkeypatch: pytest's patcher.
    """
    monkeypatch.chdir(repo)
    result = runner.invoke(cli.app, ["run", "tset"])
    assert result.exit_code == 2


def test_invalid_config_exits_two(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A bad setting is a usage error, reported before any tool is provisioned.

    Args:
        repo: The throwaway repository.
        monkeypatch: pytest's patcher.
    """
    (repo / ".rhiza").mkdir()
    (repo / ".rhiza" / ".env").write_text("TYPECHECKER=tpye\n")
    monkeypatch.chdir(repo)
    assert runner.invoke(cli.app, ["run", "typecheck"]).exit_code == 2


def test_bare_task_is_rewritten_to_run(monkeypatch: pytest.MonkeyPatch) -> None:
    """``rhiza-task test`` becomes ``rhiza-task run test``.

    This is the compatibility contract, not sugar: the reusable workflows and the Makefile
    shim both invoke ``rhiza-task <task>``.

    Args:
        monkeypatch: pytest's patcher.
    """
    seen: list[list[str]] = []
    monkeypatch.setattr(cli, "app", lambda: seen.append(list(__import__("sys").argv)))
    monkeypatch.setattr("sys.argv", ["rhiza-task", "test", "--strict"])
    cli.main()
    assert seen == [["rhiza-task", "run", "test", "--strict"]]


@pytest.mark.parametrize("subcommand", sorted(cli.RESERVED))
def test_reserved_subcommands_are_not_rewritten(subcommand: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """A real subcommand is passed through untouched.

    Args:
        subcommand: One of the reserved names.
        monkeypatch: pytest's patcher.
    """
    seen: list[list[str]] = []
    monkeypatch.setattr(cli, "app", lambda: seen.append(list(__import__("sys").argv)))
    monkeypatch.setattr("sys.argv", ["rhiza-task", subcommand])
    cli.main()
    assert seen == [["rhiza-task", subcommand]]


def test_a_broken_task_module_does_not_break_the_cli(monkeypatch: pytest.MonkeyPatch) -> None:
    """A third-party plugin that fails to import is reported, not fatal.

    Args:
        monkeypatch: pytest's patcher.
    """

    class Boom:
        """A stand-in entry point that raises on load."""

        name = "boom"

        def load(self) -> None:
            """Fail to import.

            Raises:
                ImportError: Always.
            """
            msg = "no such module"
            raise ImportError(msg)

    monkeypatch.setattr(cli, "entry_points", lambda group: [Boom()])
    cli.load_tasks()  # must not raise


def test_run_reports_each_outcome_and_exits_zero(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A successful run prints one line per task and exits 0.

    Args:
        repo: The throwaway repository.
        monkeypatch: pytest's patcher.
    """
    from rhiza_task.config import Config
    from rhiza_task.spec import task

    @task("t-cli-ok", "succeeds", section="Test")
    def ok(cfg: Config) -> None:
        """Do nothing successfully.

        Args:
            cfg: Unused.
        """

    monkeypatch.chdir(repo)
    result = runner.invoke(cli.app, ["run", "t-cli-ok"])
    assert result.exit_code == 0
    assert "ok" in result.stdout
    assert "t-cli-ok" in result.stdout


def test_run_exits_one_on_failure(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A failing gate exits non-zero, so CI notices.

    Args:
        repo: The throwaway repository.
        monkeypatch: pytest's patcher.
    """
    from rhiza_task.config import Config
    from rhiza_task.spec import Failed, task

    @task("t-cli-bad", "fails", section="Test")
    def bad(cfg: Config) -> None:
        """Fail.

        Args:
            cfg: Unused.

        Raises:
            Failed: Always.
        """
        raise Failed(1, "nope")

    monkeypatch.chdir(repo)
    result = runner.invoke(cli.app, ["run", "t-cli-bad"])
    assert result.exit_code == 1
    assert "failed" in result.stdout


def test_run_propagates_the_failing_task_s_own_status(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``typer.Exit`` carries the task's own 2, not a flattened 1.

    The end of the chain :class:`~rhiza_task.spec.Failed` starts: a consumer's CI reads
    this process's status and nothing else.

    Args:
        repo: The throwaway repository.
        monkeypatch: pytest's patcher.
    """
    from rhiza_task.config import Config
    from rhiza_task.spec import Failed, task

    @task("t-cli-code", "fails the way pytest fails", section="Test")
    def collection_error(cfg: Config) -> None:
        """Fail with pytest's collection-error status.

        Args:
            cfg: Unused.

        Raises:
            Failed: Always, with code 2.
        """
        raise Failed(2, "tests failed")

    monkeypatch.chdir(repo)
    result = runner.invoke(cli.app, ["run", "t-cli-code"])
    assert result.exit_code == 2
    assert "failed" in result.stdout


def test_root_option_targets_another_repository(repo: Path, tmp_path_factory: pytest.TempPathFactory) -> None:
    """``--root`` operates on a repository other than the working directory.

    Args:
        repo: The throwaway repository.
        tmp_path_factory: pytest's directory factory.
    """
    elsewhere = tmp_path_factory.mktemp("elsewhere")
    (repo / ".rhiza").mkdir()
    (repo / ".rhiza" / ".env").write_text("SOURCE_FOLDER=marker\n")
    from rhiza_task.config import Config

    assert Config.load(root=repo).source_folder == "marker"
    assert Config.load(root=elsewhere).source_folder == "src"


def test_list_shows_this_repository_s_layer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A Go module is shown Go's gates, not Python's testing extras.

    The make layer got this for free by syncing exactly one language fragment; a CLI
    carrying all three has to decide, and showing a Go developer ``mutation`` and
    ``marimo-validate`` would be showing them tasks that cannot run.

    Args:
        tmp_path: The repository root.
        monkeypatch: pytest's patcher.
    """
    (tmp_path / "go.mod").write_text("module example.com/demo\n\ngo 1.23\n")
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(cli.app, ["list"])
    assert result.exit_code == 0
    assert "go-tools" in result.stdout
    assert "mutation" not in result.stdout
    assert "fmt" in result.stdout  # the neutral tasks answer everywhere

    every = runner.invoke(cli.app, ["list", "--all"])
    assert "mutation" in every.stdout
    assert "cargo-tools" in every.stdout


def test_run_blocked_task_exits_one(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A task whose prerequisite failed is blocked; the CLI exits 1.

    Args:
        repo: The throwaway repository.
        monkeypatch: pytest's patcher.
    """
    from rhiza_task.config import Config
    from rhiza_task.spec import Failed, task

    @task("t-cli-broken-req", "always fails", section="Test")
    def broken_req(cfg: Config) -> None:
        """Fail.

        Args:
            cfg: Unused.

        Raises:
            Failed: Always.
        """
        raise Failed(1, "boom")

    @task("t-cli-downstream", "depends on the failure", section="Test", needs=("t-cli-broken-req",))
    def downstream(cfg: Config) -> None:
        """Should not run.

        Args:
            cfg: Unused.
        """

    monkeypatch.chdir(repo)
    result = runner.invoke(cli.app, ["run", "t-cli-downstream"])
    assert result.exit_code == 1
    assert "blocked" in result.stdout


def test_run_skipped_task_exits_zero(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A skipped gate is green in lenient mode; the CLI exits 0.

    Args:
        repo: The throwaway repository.
        monkeypatch: pytest's patcher.
    """
    from rhiza_task.config import Config
    from rhiza_task.spec import Guard, task

    @task("t-cli-skipped", "skips via guard", section="Test", guards=(Guard("marimo_folder"),))
    def skipped(cfg: Config) -> None:
        """Should be skipped by the guard.

        Args:
            cfg: Unused.
        """

    monkeypatch.chdir(repo)
    result = runner.invoke(cli.app, ["run", "t-cli-skipped"])
    assert result.exit_code == 0
    assert "skipped" in result.stdout


def test_run_skipped_under_strict_exits_one(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``--strict`` turns a skip into a failure; the CLI exits 1.

    Args:
        repo: The throwaway repository.
        monkeypatch: pytest's patcher.
    """
    from rhiza_task.config import Config
    from rhiza_task.spec import Guard, task

    @task("t-cli-strict-skip", "skips via guard", section="Test", guards=(Guard("marimo_folder"),))
    def strict_skip(cfg: Config) -> None:
        """Should be skipped by the guard, then escalated by --strict.

        Args:
            cfg: Unused.
        """

    monkeypatch.chdir(repo)
    result = runner.invoke(cli.app, ["run", "--strict", "t-cli-strict-skip"])
    assert result.exit_code == 1
    assert "failed" in result.stdout


def test_run_propagates_the_failing_tasks_exit_code(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A task's own exit code reaches the process exit status.

    The CLI boundary is where propagation would be lost if it were lost anywhere: pytest
    exits 5 for "no tests collected", and ``rhiza-task run`` exits 5 too, so a caller
    reading ``$?`` sees what the gate actually reported.

    Args:
        repo: The throwaway repository.
        monkeypatch: pytest's patcher.
    """
    from rhiza_task.config import Config
    from rhiza_task.spec import Failed, task

    @task("t-cli-exits-5", "fails with a distinctive code", section="Test")
    def exits_5(cfg: Config) -> None:
        """Fail with exit code 5.

        Args:
            cfg: Unused.

        Raises:
            Failed: Always, with code 5.
        """
        raise Failed(5, "no tests collected")

    monkeypatch.chdir(repo)
    result = runner.invoke(cli.app, ["run", "t-cli-exits-5"])
    assert result.exit_code == 5


def test_version_prints_the_package_version(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``version`` is what a consumer's CI echoes to record which pin it ran.

    Args:
        repo: The throwaway repository.
        monkeypatch: pytest's patcher.
    """
    from rhiza_task import __version__

    monkeypatch.chdir(repo)
    result = runner.invoke(cli.app, ["version"])
    assert result.exit_code == 0
    assert __version__ in result.stdout


def test_list_survives_a_config_it_cannot_resolve(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``list`` is what you run *because* something is wrong, so it must still print.

    A config error resolving the layers falls back to showing every layer -- a superset,
    where the alternative is showing nothing at the moment the user most needs the list.

    Args:
        repo: The throwaway repository.
        monkeypatch: pytest's patcher.
    """
    (repo / ".rhiza").mkdir()
    (repo / ".rhiza" / ".env").write_text("TYPECHECKER=tpye\n")
    monkeypatch.chdir(repo)
    result = runner.invoke(cli.app, ["list"])
    assert result.exit_code == 0
    # Every layer, not just Python's: the fallback is deliberately a superset.
    for expected in ("cargo-tools", "go-tools", "test"):
        assert expected in result.stdout


def test_the_module_entry_point_exposes_main() -> None:
    """``python -m rhiza_task`` works in a checkout with no console script installed.

    Importing the module is enough to assert the wiring: ``__name__`` is
    ``rhiza_task.__main__`` rather than ``__main__``, so the guard does not fire and no
    CLI runs.
    """
    from rhiza_task import __main__ as entry

    assert entry.main is cli.main
