"""The command line, generated from the registry rather than hand-maintained.

rhiza.mk builds its help by running awk over ``$(MAKEFILE_LIST)`` looking for ``##`` and
``##@`` comments -- a parser for a documentation convention that exists only because make
has no notion of a task description. Typer has one, so help text, sections, per-task help
and the "unknown task" error all come from the same registry the runner uses, and cannot
drift from it.
"""

from __future__ import annotations

import json
import sys
from importlib.metadata import entry_points
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from . import __version__, runner
from .config import DEFAULT_CI_OS_MATRIX, Config, _key
from .runner import Status
from .spec import REGISTRY

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="rhiza developer tasks. Run `rhiza-task list` to see what is available.",
)
console = Console()
err = Console(stderr=True)

STATUS_COLOUR = {
    Status.OK: "green",
    Status.SKIPPED: "yellow",
    Status.FAILED: "red",
    Status.BLOCKED: "red",
}

RESERVED = frozenset({"list", "print", "run", "ci-os-matrix", "shim", "version"})
"""Subcommand names, so the bare-task shorthand in :func:`main` can tell them apart."""


def load_tasks() -> None:
    """Import every module registered under the ``rhiza_task.tasks`` entry-point group.

    Failures are reported and skipped rather than fatal: a broken third-party task module
    should not take the built-in gates down with it.
    """
    for entry in entry_points(group="rhiza_task.tasks"):
        try:
            entry.load()
        except Exception as exc:  # noqa: BLE001 - a plugin must not break the runner
            err.print(f"[yellow]could not load task module {entry.name}: {exc}[/yellow]")


@app.command("list")
def list_tasks() -> None:
    """Show every available task, grouped by section."""
    table = Table("task", "section", "needs", "does", box=None, header_style="bold")
    for name, spec in sorted(REGISTRY.items(), key=lambda kv: (kv[1].section, kv[0])):
        if not spec.hidden:
            table.add_row(name, spec.section, " ".join(spec.needs), spec.help)
    console.print(table)


@app.command("print")
def print_setting(name: str) -> None:
    """Print one resolved setting, replacing make's ``print-%`` pattern rule.

    Args:
        name: A config field, spelled either way -- ``source_folder`` or ``SOURCE_FOLDER``.

    Raises:
        typer.Exit: With status 2 when the setting does not exist.
    """
    cfg = Config.load()
    field = _key(name)
    if not hasattr(cfg, field):
        err.print(f"[red]unknown setting: {name}[/red]")
        raise typer.Exit(2)
    value = getattr(cfg, field)
    # markup=False, highlight=False: ``print`` is the command you reach for when a setting
    # is not doing what you expect, so it must show the stored value and nothing else.
    # ``mkdocs_extra_packages = ("mkdocstrings[python]",)`` printed as ``mkdocstrings``
    # otherwise, rich having read ``[python]`` as a style tag.
    console.print(
        " ".join(map(str, value)) if isinstance(value, tuple) else str(value),
        markup=False,
        highlight=False,
    )


@app.command("ci-os-matrix")
def ci_os_matrix() -> None:
    """Emit the CI OS matrix as a JSON array, for a GitHub Actions matrix input.

    Never emits ``[]``. A GitHub matrix with no OS in it does not fail the workflow -- it
    expands to zero jobs, so the ``test`` job disappears and CI goes green having run
    nothing. The retired make recipe guarded that with ``$(or $(RHIZA_CI_OS_MATRIX),
    ["ubuntu-latest"])`` and this is the same floor: after :func:`~rhiza_task.config`
    resolution an empty value can only come from an explicit ``RHIZA_CI_OS_MATRIX=[]``,
    which is a mistake in every case a caller has ever meant.
    """
    print(json.dumps(list(Config.load().ci_os_matrix) or list(DEFAULT_CI_OS_MATRIX)))


@app.command("shim")
def shim() -> None:
    """Print the Makefile that forwards to this CLI.

    ``uvx rhiza-task shim > Makefile`` is the whole migration for a consumer repository:
    it is what replaces ``.rhiza/rhiza.mk`` and the ten fragments in ``.rhiza/make.d/``.
    Generated rather than synced, so it cannot drift from the version that produced it.
    """
    template = Path(__file__).parent / "templates" / "Makefile"
    # sys.stdout, not the rich Console: Console word-wraps to the terminal width and
    # expands tabs, and a Makefile survives neither. A wrapped comment is merely ugly, but
    # a wrapped recipe line is a syntax error and a tab expanded to spaces stops make
    # recognising the line as a recipe at all -- so `rhiza-task shim > Makefile` would
    # write a broken file.
    sys.stdout.write(template.read_text())


@app.command("version")
def version() -> None:
    """Print the rhiza-task version."""
    console.print(__version__)


@app.command("run", no_args_is_help=True)
def run_tasks(
    names: list[str] = typer.Argument(..., help="Tasks to run, in order"),
    strict: bool = typer.Option(False, "--strict", help="Treat a skipped gate as a failure"),
    root: Path | None = typer.Option(None, "--root", help="Repository to operate on"),
) -> None:
    """Run one or more tasks, with their prerequisites.

    Args:
        names: Task names.
        strict: Fail rather than skip when a gate has nothing to measure.
        root: Repository root; defaults to the current directory.

    Raises:
        typer.Exit: With 0 when everything passed, 1 on failure, 2 on a usage error.
    """
    try:
        cfg = Config.load(root=root, strict=strict or None)
    except ValueError as exc:  # invalid configuration, e.g. typechecker=tpye
        err.print(f"[red]{exc}[/red]")
        raise typer.Exit(2) from exc

    try:
        state = runner.run(names, cfg)
    except KeyError as exc:
        err.print(f"[red]{exc.args[0]}[/red]  (try `rhiza-task list`)")
        raise typer.Exit(2) from exc

    console.print()
    for result in state.results:
        colour = STATUS_COLOUR[result.status]
        detail = f"  [dim]{result.detail}[/dim]" if result.detail else ""
        console.print(f"[{colour}]{result.status.value:>8}[/{colour}]  {result.name}{detail}")
    raise typer.Exit(state.exit_code())


def main() -> None:
    """Entry point. A bare ``rhiza-task <task>`` is shorthand for ``rhiza-task run <task>``.

    Not sugar -- it is the compatibility contract. The reusable workflows and the Makefile
    shim both invoke ``rhiza-task test``, and a consumer's muscle memory is ``make test``.
    Requiring ``run`` would put a word between the two for no gain.
    """
    load_tasks()
    argv = sys.argv[1:]
    if argv and argv[0] not in RESERVED and not argv[0].startswith("-"):
        sys.argv = [sys.argv[0], "run", *argv]
    app()
