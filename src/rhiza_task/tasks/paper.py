"""The LaTeX tasks: paper.mk, as tasks.

Closer in shape to ``book`` than to the CLI wrappers: a build with an output worth
naming. The engine does the hard part -- it reruns the TeX pass and bibtex until the
citations and cross-references converge -- so the task is a folder, a file choice and a
fixed flag set.

**The engine is tectonic**, which is the one substantive change from paper.mk. paper.mk
drove a full TeX distribution, so a consumer provisioned the distribution *and* the list
of packages their document happened to cite, and the two workflows here each carried their
own copy of that list. tectonic is a single binary that resolves what a document cites out
of its web bundle and caches it, so there is one tool to install and no list to keep in
step. Three consequences the flag set below records rather than restates:

* Convergence and bibtex are the engine's own loop, not a driver's, so neither is asked
  for -- the argument vector is the document and nothing about *how* to build it.
* There is no interaction mode to pin. tectonic never stops for a prompt, so it needs no
  flag saying so; a broken document exits non-zero, which :func:`~rhiza_task.uv.tool`
  turns into :class:`~rhiza_task.spec.Failed`.
* A cold cache needs the network. A provisioned distribution did not, and that is the one
  thing this trade costs; the cache is per-machine and survives between runs, so it is a
  first-run cost rather than a per-build one.

The file choice is the other thing that changed, and it changed earlier. paper.mk reads

    if [ -f $(PAPER_DIR)/basanos.tex ]; then tex_file="basanos.tex"; else <first *.tex>; fi

-- a named preference for one downstream repository's paper, in a template every consumer
syncs. :func:`main_document` replaces it with two conventional names and then alphabetical
order, so the behaviour is the same for a folder with one ``.tex`` (the overwhelmingly
common case) and no longer privileges a stranger's filename.

``-maxdepth 1`` survives as :meth:`~pathlib.Path.glob` rather than
:meth:`~pathlib.Path.rglob`, and deliberately: a LaTeX project's subdirectories hold
included chapters, and the engine must be pointed at the root document, not at a chapter.

**``joss`` is a second task rather than a second branch of ``paper``** (#147). A Journal of
Open Source Software manuscript is Markdown plus BibTeX, typeset by the journal's own
pipeline, and teaching ``paper`` to recognise it would have made one task name answer for
two engines whose outputs are not comparable. So ``paper`` is unchanged, and ``joss`` runs
the journal's pipeline as the journal does: the `inara <https://github.com/openjournals/inara>`_
image, which is what JOSS's draft action runs, over ``paper.md`` in the same
``paper_folder``. :data:`JOSS_IMAGE` says why it is pinned.
"""

from __future__ import annotations

import os
from pathlib import Path

from ..config import Config
from ..spec import Guard, Skip, task
from ..uv import tool
from .docker import HAVE_DOCKER

SECTION = "Paper"

HAVE_TECTONIC = Guard(
    tool="tectonic",
    reason="tectonic not found; see https://tectonic-typesetting.github.io/en-US/install.html",
)

PREFERRED = ("main.tex", "paper.tex")
"""Root-document names tried before falling back to alphabetical order."""

AUX_SUFFIXES = (".aux", ".bbl", ".blg", ".log", ".out", ".synctex.gz", ".toc")
"""What a TeX run leaves beside the document, mirroring .gitignore's list for this folder.

The PDF is deliberately absent: this is the set that is *never* worth keeping, and both
callers want it -- :func:`paper_clean` adds the PDF because removing the output is the
point of a clean, and ``book``'s prune keeps the PDF because publishing it is the point of
the build.

These are the names TeX itself writes. A driver's own bookkeeping files -- the
rebuild-cache and file-list a make-style LaTeX driver keeps -- are not listed, because no
driver runs here: tectonic is the whole engine and writes the ``.log`` (asked for below)
and, only when asked, the rest.

Matched as name suffixes rather than through :attr:`~pathlib.PurePath.suffix`, because
``.synctex.gz`` is two extensions and ``suffix`` would report only ``.gz``.
"""

JOSS_DOCUMENT = "paper.md"
"""The manuscript ``joss`` compiles: the name JOSS's submission guidelines and its draft action
both default to, and the only one inara reads without being told otherwise."""

JOSS_IMAGE = "openjournals/inara:1.3.1"
"""The journal's typesetting image, pinned to a release tag.

JOSS's own draft action runs ``:latest``, so a pin can lag what the journal renders. The pin
wins anyway, for the reason the whole package exists: an unpinned image changes the PDF
under a green pull request with no commit touching the repository, and a typesetting
pipeline's output moves between releases. Nothing automated moves a Python string, so this
is one of the hand-reviewed pins ``.github/dependabot.yml`` lists at its foot -- bump it
deliberately, and look at the PDF when you do.
"""


def has_joss_paper(cfg: Config) -> bool:
    """Return whether the repository has a JOSS manuscript, for ``list``.

    Narrower than :func:`has_paper` on purpose: a LaTeX-only paper folder should not list a
    task that would only ever skip.

    Args:
        cfg: The resolved config.

    Returns:
        True when ``<paper_folder>/paper.md`` exists.
    """
    return (cfg.root / cfg.paper_folder / JOSS_DOCUMENT).is_file()


def user_flags() -> list[str]:
    """Return the ``--user`` flag that makes the container write files the caller owns.

    inara runs as root by default, so on Linux the PDF it writes into the mounted folder
    would be root-owned and undeletable by ``paper-clean`` or the next build -- which is why
    inara's own instructions pass ``-u $(id -u):$(id -g)``. Windows has no uid to pass, and
    Docker Desktop maps ownership on the way out regardless, so there the flag is omitted
    rather than invented.

    Returns:
        ``["--user", "<uid>:<gid>"]`` where the platform has them, else an empty list.
    """
    if not hasattr(os, "getuid"):
        return []
    return ["--user", f"{os.getuid()}:{os.getgid()}"]


def main_document(folder: Path) -> Path | None:
    """Choose the root ``.tex`` file in a folder.

    Args:
        folder: The paper folder.

    Returns:
        The document to compile, or None when the folder holds no top-level ``.tex``.
    """
    candidates = sorted(p for p in folder.glob("*.tex") if p.is_file())
    if not candidates:
        return None
    by_name = {p.name: p for p in candidates}
    return next((by_name[name] for name in PREFERRED if name in by_name), candidates[0])


def has_paper(cfg: Config) -> bool:
    """Return whether the repository has a paper, for ``list``.

    Args:
        cfg: The resolved config.

    Returns:
        True when ``paper_folder`` exists -- the folder both tasks skip without.
    """
    return (cfg.root / cfg.paper_folder).is_dir()


@task(
    "paper",
    "compile the LaTeX paper to PDF",
    section=SECTION,
    guards=(HAVE_TECTONIC, Guard("paper_folder")),
    applies=has_paper,
)
def paper(cfg: Config) -> None:
    """Run tectonic over the paper folder's root document.

    Args:
        cfg: The resolved config.

    Raises:
        Skip: When the folder holds no top-level ``.tex`` file.
    """
    folder = cfg.path("paper_folder")
    document = main_document(folder)
    if document is None:
        raise Skip(f"no .tex files in '{cfg.paper_folder}'")

    print(f"[INFO] compiling {document.name}")
    # cwd is the paper folder, as `cd $(PAPER_DIR) && <engine>` was: tectonic resolves
    # \input paths relative to the document and writes its output beside it, which is what
    # lets `book` publish the PDF with no copy step.
    #
    # `--keep-logs` is the one flag, and it is not cosmetic: tectonic writes only the PDF by
    # default, and on a runner the log is the artefact you upload when a compile fails. It
    # is also the file `book`'s prune exists to keep out of the published site, since it
    # records absolute paths from whichever machine built it.
    tool("tectonic", "--keep-logs", document.name, cwd=folder)
    print(f"[SUCCESS] {cfg.paper_folder}/{document.stem}.pdf")


@task(
    "joss",
    "compile the JOSS Markdown paper to a draft PDF",
    section=SECTION,
    guards=(HAVE_DOCKER, Guard("paper_folder")),
    applies=has_joss_paper,
)
def joss(cfg: Config) -> None:
    """Run the journal's inara image over ``<paper_folder>/paper.md``.

    The output is a **draft** -- watermarked and line-numbered, which is inara's default
    and what JOSS's draft action produces. The production PDF is the journal's to build
    at acceptance, so there is no flag here for it.

    Args:
        cfg: The resolved config.

    Raises:
        Skip: When the folder holds no ``paper.md``.
    """
    folder = cfg.path("paper_folder")
    if not (folder / JOSS_DOCUMENT).is_file():
        raise Skip(f"no {JOSS_DOCUMENT} in '{cfg.paper_folder}'")

    print(f"[INFO] compiling {JOSS_DOCUMENT} with {JOSS_IMAGE}")
    # The folder is mounted on /data, inara's working directory, so the manuscript is named
    # relative to it and `paper.bib`, figures and the PDF all sit beside it -- the same
    # "output lands next to the source" rule `paper` follows. `JOURNAL=joss` is the image's
    # default too; it is passed anyway, as the draft action passes it, so the vector says
    # which journal's template it renders rather than relying on an ENV line upstream.
    # `-o pdf` because the default also writes JATS XML, which is the journal's artefact
    # rather than the author's.
    tool(
        "docker",
        "run",
        "--rm",
        "--volume",
        f"{folder}:/data",
        *user_flags(),
        "--env",
        "JOURNAL=joss",
        JOSS_IMAGE,
        "-o",
        "pdf",
        JOSS_DOCUMENT,
        cwd=folder,
    )
    print(f"[SUCCESS] {cfg.paper_folder}/{Path(JOSS_DOCUMENT).stem}.pdf")


@task("paper-clean", "remove the LaTeX build artifacts", section=SECTION, applies=has_paper)
def paper_clean(cfg: Config) -> None:
    """Remove the PDF and auxiliary files belonging to the folder's top-level documents.

    Pure Python, and unguarded on any tool: tectonic has no clean subcommand to delegate
    to, so there is nothing to be absent. That makes this the one task in the section that
    works on a machine which cannot build the paper at all -- an improvement over
    delegating, where cleaning required the very toolchain you were cleaning up after.

    **Scoped by document stem, not by extension sweep.** ``paper.tex`` authorises deleting
    ``paper.pdf`` and ``paper.log``; a ``figures/`` diagram exported to ``diagram.pdf`` and
    committed beside the source has no ``diagram.tex`` and survives. An extension sweep
    would be one line shorter and would delete a consumer's checked-in artwork, which is
    not recoverable by rebuilding.

    Args:
        cfg: The resolved config.

    Raises:
        Skip: When there is no paper folder to clean.
    """
    folder = cfg.path("paper_folder")
    if not folder.is_dir():
        raise Skip(f"paper_folder '{cfg.paper_folder}' not found")

    removed = 0
    for stem in sorted({p.stem for p in folder.glob("*.tex") if p.is_file()}):
        for suffix in (*AUX_SUFFIXES, ".pdf"):
            artifact = folder / f"{stem}{suffix}"
            if artifact.is_file():
                artifact.unlink()
                removed += 1
    # Cleaning a folder that was never built leaves nothing to report and is not a failure,
    # which is what paper.mk's `|| true` bought; here it falls out of there being no tool
    # to fail.
    print(f"[SUCCESS] cleaned {cfg.paper_folder} ({removed} file(s))")
