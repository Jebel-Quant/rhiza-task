"""The book and notebook tasks: book.mk and marimo.mk, as tasks.

``book`` is the third recipe that resists the declarative form: it aggregates the report-
producing gates, copies their output into the docs tree, exports every notebook, builds
the site, and generates a coverage badge.

Its prerequisite list is also where make's no-op stubs came from. book.mk has to declare
``test:: ; @:``, ``benchmark:: ; @:``, ``stress:: ; @:`` and ``hypothesis-test:: ; @:``
so that ``book`` can depend on gates the ``tests`` bundle may not have contributed. The
runner skips unregistered prerequisites, so all four stubs are gone.
"""

from __future__ import annotations

import shutil

from ..config import Config
from ..spec import Failed, Guard, Skip, task
from ..uv import uv_run, uvx


@task(
    "book",
    "build the companion book",
    section="Book",
    needs=("test", "benchmark", "stress", "hypothesis-test"),
)
def book(cfg: Config) -> None:
    """Build the MkDocs/Zensical site, with test reports and notebooks folded in.

    Args:
        cfg: The resolved config.

    Raises:
        Skip: When there is no ``mkdocs.yml`` to build.
    """
    if not (cfg.root / "mkdocs.yml").is_file():
        raise Skip("no mkdocs.yml")

    _copy_reports(cfg)
    _export_notebooks(cfg)

    output = cfg.root / cfg.book_output
    shutil.rmtree(output, ignore_errors=True)
    uvx(
        f"zensical{cfg.zensical_version}",
        "build",
        "-f",
        str(cfg.root / "mkdocs.yml"),
        cwd=cfg.root,
        withs=cfg.mkdocs_extra_packages,
    )
    output.mkdir(parents=True, exist_ok=True)
    (output / ".nojekyll").touch()

    coverage = cfg.root / "_tests" / "coverage.xml"
    if coverage.is_file():
        uvx(
            "genbadge[coverage]",
            "coverage",
            "-i",
            str(coverage),
            "-o",
            str(output / "coverage-badge.svg"),
            cwd=cfg.root,
            check=False,
        )
    print(f"[SUCCESS] book built at {cfg.book_output}/")


@task("serve", "build the book and serve it on port 8000", section="Book", needs=("book",))
def serve(cfg: Config) -> None:
    """Serve the built book over HTTP.

    Python's own server rather than an editor's built-in one, because the JetBrains server
    refuses to serve gitignored directories and ``_book`` is one.

    Args:
        cfg: The resolved config.
    """
    print("[INFO] serving at http://localhost:8000 (Ctrl-C to stop)")
    uv_run("python", "-m", "http.server", "8000", cwd=cfg.path("book_output"))


@task(
    "marimo",
    "start the Marimo editor",
    section="Book",
    needs=("install",),
    guards=(Guard("marimo_folder"),),
)
def marimo(cfg: Config) -> None:
    """Start a headless Marimo server on the notebook folder.

    ``--no-project`` is marimo.mk's: the editor runs against its own provisioned marimo
    rather than the project environment.

    Args:
        cfg: The resolved config.
    """
    uv_run(
        "marimo",
        "edit",
        "--no-token",
        "--headless",
        cwd=cfg.path("marimo_folder"),
        withs=("marimo",),
        no_project=True,
    )


@task(
    "marimo-validate",
    "check that every Marimo notebook runs",
    section="Book",
    needs=("install",),
    guards=(Guard("marimo_folder"),),
)
def marimo_validate(cfg: Config) -> None:
    """Run each notebook as a script, reporting per-notebook pass or fail.

    Args:
        cfg: The resolved config.

    Raises:
        Skip: When the folder holds no notebooks.
        Failed: When any notebook fails to run.
    """
    notebooks = sorted(cfg.path("marimo_folder").glob("*.py"))
    if not notebooks:
        raise Skip(f"no notebooks in '{cfg.marimo_folder}'")

    failures: list[str] = []
    for notebook in notebooks:
        artefacts = cfg.root / "results" / notebook.stem
        artefacts.mkdir(parents=True, exist_ok=True)
        print(f"[INFO] validating {notebook.name} (artefacts -> {artefacts})")
        code = uv_run(
            "python",
            str(notebook),
            cwd=cfg.root,
            check=False,
            env={"NOTEBOOK_OUTPUT_FOLDER": str(artefacts)},
        )
        if code:
            failures.append(notebook.name)

    if failures:
        raise Failed(1, f"{len(failures)} notebook(s) failed: {', '.join(failures)}")
    print(f"[SUCCESS] all {len(notebooks)} notebook(s) valid")


def _copy_reports(cfg: Config) -> None:
    """Copy the test-report tree into the docs folder, if the gates produced one.

    Args:
        cfg: The resolved config.
    """
    reports = cfg.root / "_tests"
    if not reports.is_dir() or not any(reports.iterdir()):
        print("[WARN] no _tests output to fold into the book")
        return
    destination = cfg.root / "docs" / "reports"
    destination.mkdir(parents=True, exist_ok=True)
    shutil.copytree(reports, destination, dirs_exist_ok=True)


def _export_notebooks(cfg: Config) -> None:
    """Export each notebook to a self-contained HTML file under ``docs/notebooks``.

    Args:
        cfg: The resolved config.
    """
    folder = cfg.path("marimo_folder")
    if not folder.is_dir():
        print("[WARN] no marimo folder; skipping notebook export")
        return
    destination = cfg.root / "docs" / "notebooks"
    destination.mkdir(parents=True, exist_ok=True)
    for notebook in sorted(folder.glob("*.py")):
        target = destination / f"{notebook.stem}.html"
        print(f"[INFO] exporting {notebook.name} -> {target}")
        uv_run(
            "marimo",
            "export",
            "html",
            "--sandbox",
            notebook.name,
            "-o",
            str(target),
            cwd=folder,
            withs=("marimo",),
        )
