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

import re
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
    _scrub_local_paths(cfg.root, destination)


SCRUBBED_SUFFIXES = (".html", ".htm", ".xml", ".json", ".js", ".css", ".txt", ".svg")
"""Which report files are rewritten. Text formats only, so no binary is touched."""


def _scrub_local_paths(root: Path, destination: Path) -> None:
    """Mask absolute build paths in the *published* copy of the reports.

    A report is written for the machine that produced it and then published to the web,
    which is a change of audience nothing in the toolchain notices. Two paths leak:

    * the repository root, which pytest records as its ``rootdir``;
    * the home directory, because pytest-xdist stamps every test with the worker banner
      ``[gw0] darwin -- Python 3.11.15 <interpreter>``, and under ``uv run --with`` that
      interpreter lives in the user's uv cache. In this repository that was 300-odd
      occurrences in one ``report.html``.

    Neither is fixable upstream from here. coverage's own ``relative_files`` handles the
    coverage artefacts and is set in ``pyproject.toml``; the xdist banner has no setting,
    and dropping ``-n auto`` to avoid it would slow every consumer's suite to protect a
    report. So the copy is rewritten and ``_tests/`` is left exactly as produced, which is
    what a developer reads locally and where absolute paths are the useful form.

    Ordering matters: the root is replaced before the home directory, because on CI the
    root lives *inside* it and masking the shorter prefix first would leave a half-path.

    Known limit: matching is textual, so a Windows path embedded in JSON arrives
    backslash-escaped and is not recognised. The gate that publishes a book runs on Linux,
    so this is a real gap rather than a closed one.

    Args:
        root: The repository root, as the reports spell it.
        destination: The published copy, under ``docs/``.
    """
    # `Path.home()` rather than $HOME: on Windows the variable is often unset, and the
    # masking has to be harmless there rather than crash.
    masks = ((str(root), "."), (str(Path.home()), "~"))
    for path in sorted(destination.rglob("*")):
        if not path.is_file() or not path.name.endswith(SCRUBBED_SUFFIXES):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            # A report file that cannot be read as text is one this function has no opinion
            # about; skipping it must not cost the book its build.
            continue
        scrubbed = text
        for absolute, mask in masks:
            scrubbed = scrubbed.replace(absolute, mask)
        if scrubbed != text:
            path.write_text(scrubbed, encoding="utf-8")


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


_NAV_KEY = re.compile(r"^nav:\s*(?:#.*)?$")
"""The ``nav:`` mapping's own line: top-level, so no leading whitespace."""


def _nav_targets(text: str) -> list[str]:
    """Extract the file targets from a mkdocs ``nav:`` block.

    Hand-parsed rather than loaded with a YAML library, for the reason
    :func:`~rhiza_task.tasks.quality.docs_examples` hand-parses fences instead of pulling a
    markdown parser: this package declares three runtime dependencies and adding a fourth to
    read eleven lines of one file is the wrong trade. The subset relied on is the one mkdocs
    documents -- ``- Title: path`` and ``- path``, nested under section keys -- and the
    parser is deliberately shallow: it collects targets and does not reconstruct the tree,
    because the tree is not what a missing file is about.

    Two things are skipped rather than reported. A section header (``- Guides:``, no value)
    names no file. An external target (anything carrying ``://``) is not this gate's to
    check -- reaching the network would make a docs build fail on someone else's outage,
    which is what ``weekly.yml``'s link checker is for.

    Args:
        text: The contents of ``mkdocs.yml``.

    Returns:
        Every in-repository nav target, in document order, with duplicates kept.
    """
    lines = text.splitlines()
    start = next((i for i, line in enumerate(lines) if _NAV_KEY.match(line)), None)
    if start is None:
        return []

    targets: list[str] = []
    for line in lines[start + 1 :]:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        # A non-indented line is the next top-level key, which ends the nav block.
        if not line[:1].isspace():
            break
        target = _nav_target(stripped)
        if target:
            targets.append(target)
    return targets


# Split out from the loop above rather than inlined, which is the opposite of the choice the
# four C-ranked blocks elsewhere in this package defend -- and for the opposite reason. Those
# keep a flat shape because decomposing them would cost the reader something real: the order
# the guards fire in, or the one-branch-per-setting correspondence. Here there is no ordering
# to protect. One line's grammar and the block's extent are genuinely separate questions, and
# inlining both put the function at C (12) for no gain.
def _nav_target(item: str) -> str | None:
    """Return the file target a single nav list item names, if it names one.

    Args:
        item: One stripped line from inside the ``nav:`` block.

    Returns:
        The target, or None for a line that names no file -- a nested key with no ``- ``, a
        section header carrying no value, or an external URL.
    """
    if not item.startswith("- "):
        return None
    value = item[2:].strip()
    if "://" in value:
        return None
    target = value.rsplit(":", 1)[-1].strip() if ":" in value else value
    return target or None


def _built_candidates(target: str) -> tuple[str, ...]:
    """Return the paths a nav target may legitimately have become in the built site.

    A markdown page is not published under its own name: with mkdocs's default
    ``use_directory_urls``, ``faq.md`` is written as ``faq/index.html``, and with it off as
    ``faq.html``. Both are correct, and which one applies is a theme-and-config question this
    function deliberately does not try to resolve -- accepting either is enough to answer
    "was this page built at all?", which is the question. Anything that is not markdown is an
    asset and is copied verbatim, so it has exactly one candidate.

    Args:
        target: A nav target as spelled in ``mkdocs.yml``.

    Returns:
        The candidate paths, relative to the built site, any one of which satisfies the nav
        entry.
    """
    if not target.endswith(".md"):
        return (target,)
    stem = target[: -len(".md")]
    return (f"{stem}.html", f"{stem}/index.html")


@task(
    "book-nav",
    "check that every mkdocs nav entry resolves in the built book",
    section="Book",
    guards=(Guard(file="mkdocs.yml"),),
)
def book_nav(cfg: Config) -> None:
    """Fail when ``mkdocs.yml`` names a nav target the built site does not contain.

    The gap this closes, and it is a published one rather than a hypothetical: zensical
    reports ``No issues found`` for a nav entry whose page does not exist *and* for one whose
    asset does not exist. So `- Paper: paper/paper.pdf` survived a build in which
    ``rhiza-task paper`` had skipped for want of latexmk, and the site deployed with a 404 in
    its own navigation, green the whole way. Every other gate here asks about the source; this
    is the only one that asks whether what was *published* holds together.

    **Not a prerequisite of** :func:`book`, deliberately. Half the nav entries in a repository
    like this one resolve only after the gates that produce them have run -- the two
    ``reports/`` pages need a ``_tests/`` tree, the paper needs a LaTeX distribution -- and a
    repository with no latexmk must keep building its book, which is exactly what a *skipped*
    prerequisite buys. Making that a failure would break every consumer that documents a
    paper it cannot compile locally. So this is a separate gate, named by ``rhiza_book.yml``
    on the ref it deploys, where the entries are supposed to be complete and a dangling one is
    a defect rather than a machine's shape.

    Markdown targets are resolved through :func:`_built_candidates`; assets are matched
    verbatim.

    Args:
        cfg: The resolved config.

    Raises:
        Skip: When the book has not been built, or ``mkdocs.yml`` declares no nav targets.
            Both are "the question is not askable", not "the answer is wrong".
        Failed: When at least one nav target is missing from the built site.
    """
    output = cfg.path("book_output")
    if not output.is_dir():
        raise Skip(f"no built book at '{cfg.book_output}'; run `rhiza-task book` first")

    targets = _nav_targets((cfg.root / "mkdocs.yml").read_text(errors="replace"))
    if not targets:
        raise Skip("mkdocs.yml declares no nav targets")

    missing = [
        target
        for target in targets
        if not any((output / candidate).exists() for candidate in _built_candidates(target))
    ]
    for target in missing:
        print(f"[ERROR] nav target not in the built book: {target}")

    if missing:
        raise Failed(
            1,
            f"{len(missing)} of {len(targets)} nav target(s) missing from '{cfg.book_output}' -- "
            f"the site would publish a 404 in its own navigation",
        )
    print(f"[SUCCESS] all {len(targets)} nav target(s) resolve in {cfg.book_output}/")
