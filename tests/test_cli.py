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


def test_shim_prints_a_usable_makefile() -> None:
    """``shim`` emits the catch-all Makefile, pinned to this version."""
    from rhiza_task import __version__

    result = runner.invoke(cli.app, ["shim"])
    assert result.exit_code == 0
    assert f"rhiza-task@{__version__}" in result.stdout
    assert "%:" in result.stdout
    assert "-include local.mk" in result.stdout


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


def test_shim_is_emitted_byte_for_byte() -> None:
    """The Makefile is written verbatim: tabs intact, no line rewrapped.

    Rich's Console word-wraps to the terminal width and expands tabs. A wrapped comment is
    merely ugly, but a wrapped recipe line is a syntax error, and a tab expanded to spaces
    stops make recognising the line as a recipe at all -- so this has to be a byte-for-byte
    copy, not pretty-printed output.
    """
    from rhiza_task import cli as cli_module

    template = (Path(cli_module.__file__).parent / "templates" / "Makefile").read_text()
    result = runner.invoke(cli.app, ["shim"])
    assert result.stdout == template
    assert "\t@$(UVX) $(RHIZA_TASK) $@" in result.stdout
