"""The Marp tasks: presentation.mk, as tasks.

The fragment's ``require-marp`` does not check for Marp, it *installs* it:

    if ! command -v marp; then npm install -g @marp-team/marp-cli; fi

-- a global npm install, triggered by typing ``make presentation``, mutating a machine
outside the repository. :func:`marp_argv` keeps the property that made that acceptable (a
consumer with Node but no Marp can still build slides) without the mutation: ``npx --yes``
runs the CLI from npm's cache instead. The precedence is Marp on PATH first, so a
deliberately installed or pinned Marp still wins.

:attr:`~rhiza_task.config.Config.marp_package` is what npx is given, unpinned by default
because ``npm install -g @marp-team/marp-cli`` was unpinned too. Pin it to
``@marp-team/marp-cli@4.2.3`` when reproducible slides matter more than current ones.

``PRESENTATION.md`` becomes a setting rather than a constant, and the output name is
derived from it -- lower-cased, so the default still produces ``presentation.html`` and
``presentation.pdf`` exactly as the fragment does.
"""

from __future__ import annotations

from pathlib import Path

from ..config import Config
from ..spec import Skip, have, task
from ..uv import tool

SECTION = "Presentation"

NODE_URL = "https://nodejs.org/"


def marp_argv(cfg: Config) -> tuple[str, tuple[str, ...]]:
    """Resolve how to reach the Marp CLI on this machine.

    Args:
        cfg: The resolved config.

    Returns:
        The executable to run and the arguments that must precede Marp's own.

    Raises:
        Skip: When neither marp nor npx is available.
    """
    if have("marp"):
        return "marp", ()
    if have("npx"):
        return "npx", ("--yes", cfg.marp_package)
    raise Skip(f"neither marp nor npx found; install Node.js ({NODE_URL})")


def source(cfg: Config) -> Path:
    """Return the slide deck's source file.

    Args:
        cfg: The resolved config.

    Returns:
        The absolute path to the configured Markdown file.

    Raises:
        Skip: When the file does not exist.
    """
    path = cfg.root / cfg.presentation_file
    if not path.is_file():
        raise Skip(f"no {cfg.presentation_file}")
    return path


def output(cfg: Config, suffix: str) -> str:
    """Return the output filename for a format.

    Lower-cased so that the default ``PRESENTATION.md`` yields ``presentation.html``,
    which is the name presentation.mk hard-codes and the one a consumer's ``.gitignore``
    and links already point at.

    Args:
        cfg: The resolved config.
        suffix: The output extension, with its dot.

    Returns:
        A repository-relative filename.
    """
    return Path(cfg.presentation_file).with_suffix(suffix).name.lower()


@task("presentation", "generate the HTML slides with Marp", section=SECTION)
def presentation(cfg: Config) -> None:
    """Export the deck to a single HTML file.

    Args:
        cfg: The resolved config.
    """
    binary, prefix = marp_argv(cfg)
    target = output(cfg, ".html")
    tool(binary, *prefix, source(cfg).name, "-o", target, cwd=cfg.root)
    print(f"[SUCCESS] {target} — open it in a browser to view the slides")


@task("presentation-pdf", "generate the PDF slides with Marp", section=SECTION)
def presentation_pdf(cfg: Config) -> None:
    """Export the deck to PDF.

    ``--allow-local-files`` is presentation.mk's and is required rather than optional:
    Marp renders the PDF through headless Chrome, which refuses ``file://`` images
    without it, so a deck with a local logo silently loses it.

    Args:
        cfg: The resolved config.
    """
    binary, prefix = marp_argv(cfg)
    target = output(cfg, ".pdf")
    tool(binary, *prefix, source(cfg).name, "-o", target, "--allow-local-files", cwd=cfg.root)
    print(f"[SUCCESS] {target}")


@task("presentation-serve", "serve the slides with Marp's live preview", section=SECTION)
def presentation_serve(cfg: Config) -> None:
    """Start Marp's watching server over the repository.

    Args:
        cfg: The resolved config.
    """
    binary, prefix = marp_argv(cfg)
    print("[INFO] starting the Marp server (Ctrl-C to stop)")
    tool(binary, *prefix, "-s", ".", cwd=cfg.root)
