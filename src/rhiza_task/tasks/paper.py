"""The LaTeX tasks: paper.mk, as tasks.

Closer in shape to ``book`` than to the CLI wrappers: a build with an output worth
naming. latexmk does the hard part -- it reruns pdflatex and bibtex until the references
converge -- so the task is a folder, a file choice and a fixed flag set.

The file choice is the one thing that changes. paper.mk reads

    if [ -f $(PAPER_DIR)/basanos.tex ]; then tex_file="basanos.tex"; else <first *.tex>; fi

-- a named preference for one downstream repository's paper, in a template every consumer
syncs. :func:`main_document` replaces it with two conventional names and then alphabetical
order, so the behaviour is the same for a folder with one ``.tex`` (the overwhelmingly
common case) and no longer privileges a stranger's filename.

``-maxdepth 1`` survives as :meth:`~pathlib.Path.glob` rather than
:meth:`~pathlib.Path.rglob`, and deliberately: a LaTeX project's subdirectories hold
included chapters, and latexmk must be pointed at the root document, not at a chapter.
"""

from __future__ import annotations

from pathlib import Path

from ..config import Config
from ..spec import Guard, Skip, task
from ..uv import tool

SECTION = "Paper"

HAVE_LATEXMK = Guard(
    tool="latexmk",
    reason="latexmk not found; install a LaTeX distribution (MacTeX, TeX Live)",
)

PREFERRED = ("main.tex", "paper.tex")
"""Root-document names tried before falling back to alphabetical order."""


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


@task("paper", "compile the LaTeX paper to PDF", section=SECTION, guards=(HAVE_LATEXMK, Guard("paper_folder")))
def paper(cfg: Config) -> None:
    """Run latexmk over the paper folder's root document.

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
    # cwd is the paper folder, as `cd $(PAPER_DIR) && latexmk` was: latexmk writes its
    # auxiliary files beside the document, and \input paths are relative to it.
    tool("latexmk", "-pdf", "-bibtex", "-interaction=nonstopmode", document.name, cwd=folder)
    print(f"[SUCCESS] {cfg.paper_folder}/{document.stem}.pdf")


@task("paper-clean", "remove the latexmk build artifacts", section=SECTION, guards=(HAVE_LATEXMK,))
def paper_clean(cfg: Config) -> None:
    """Run ``latexmk -C``, removing the generated PDF and every auxiliary file.

    Args:
        cfg: The resolved config.

    Raises:
        Skip: When there is no paper folder to clean.
    """
    folder = cfg.path("paper_folder")
    if not folder.is_dir():
        raise Skip(f"paper_folder '{cfg.paper_folder}' not found")
    # `|| true`, as paper.mk has it: cleaning a folder that was never built is the
    # expected state of a clean target.
    tool("latexmk", "-C", cwd=folder, check=False)
    print(f"[SUCCESS] cleaned {cfg.paper_folder}")
