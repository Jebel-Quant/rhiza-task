"""The book and notebook tasks: book.mk and marimo.mk, as tasks.

``book`` is the third recipe that resists the declarative form: it aggregates the report-
producing gates, copies their output into the docs tree, exports every notebook, builds
the site, and generates a coverage badge.

The one artefact it does *not* copy is the paper's PDF. latexmk writes it beside its
source, and ``paper_folder`` is already inside ``docs_dir``, so the site build finds it
where it lies -- a prerequisite plus a ``nav`` entry, and no plumbing.

Its prerequisite list is also where make's no-op stubs came from. book.mk has to declare
``test:: ; @:``, ``benchmark:: ; @:``, ``stress:: ; @:`` and ``hypothesis-test:: ; @:``
so that ``book`` can depend on gates the ``tests`` bundle may not have contributed. The
runner skips unregistered prerequisites, so all four stubs are gone.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from ..config import Config
from ..spec import Failed, Guard, Skip, task
from ..uv import uv_run, uvx


# `paper` is a prerequisite for the same reason the other four are: it produces something
# the book publishes, and the book should be one command. It needs no copy step, unlike the
# `_tests/` tree -- latexmk writes the PDF beside its source, and `paper_folder` defaults to
# `docs/paper`, which is already inside `docs_dir`. So the build picks it up as an asset and
# mkdocs.yml only has to name it in `nav`.
#
# Safe to add because a *skipped* prerequisite does not block a dependent -- only FAILED and
# BLOCKED do (see `_run_one`) -- so a repository with no paper, or no latexmk, still builds
# its book. Under `--strict` a skip becomes a failure and would block `book`, which is worth
# knowing but is not new: `benchmark` and `stress` guard on folders most repositories do not
# have, so `--strict book` already required a repo that has all of them.
@task(
    "book",
    "build the companion book",
    section="Book",
    needs=("test", "benchmark", "stress", "hypothesis-test", "paper"),
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
    _prune_latex_artifacts(cfg, output)

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


LATEX_ARTIFACTS = (".aux", ".fdb_latexmk", ".fls", ".log", ".out", ".toc", ".bbl", ".blg", ".synctex.gz")
"""What latexmk leaves beside the document, mirroring .gitignore's list for the same folder.

Matched as name suffixes rather than through :attr:`~pathlib.PurePath.suffix`, because
``.synctex.gz`` is two extensions and ``suffix`` would report only ``.gz``.
"""


def _prune_latex_artifacts(cfg: Config, output: Path) -> None:
    """Remove latexmk's auxiliary files from the built site, keeping the PDF and the source.

    The paper's source sits inside ``docs_dir`` so that its PDF needs no copy step, and the
    price is that everything *else* latexmk leaves beside it is copied into the site too.
    ``paper.log`` is the one that matters: some 20 KB of build trace which records absolute
    paths from whichever machine ran the build.

    mkdocs would answer this with ``exclude_docs``. zensical does not implement it -- the
    note in ``docs/mkdocs-base.yml`` records that an excluded page is still written -- and
    deleting them at the source would defeat latexmk's incremental rebuild, which reads
    ``.aux`` and ``.fdb_latexmk`` to decide what to redo. So they are pruned here, from the
    output, where nothing reads them again.

    Scoped to the paper folder rather than swept over the whole site: ``.log`` and ``.out``
    are not LaTeX-specific names, and a consumer with a genuine ``debug.log`` under ``docs/``
    should keep it. ``docs`` is spelled out for the reason :func:`_copy_reports` spells it
    out -- ``docs_dir`` is mkdocs's setting, not one this package resolves.

    Args:
        cfg: The resolved config.
        output: The built site directory.
    """
    paper = cfg.path("paper_folder")
    docs = cfg.root / "docs"
    if not paper.is_relative_to(docs):
        # The paper lives outside docs_dir, so the build never copied it and there is
        # nothing in the site to prune.
        return

    published = output / paper.relative_to(docs)
    if not published.is_dir():
        return
    for path in sorted(published.iterdir()):
        if path.is_file() and path.name.endswith(LATEX_ARTIFACTS):
            path.unlink(missing_ok=True)


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
