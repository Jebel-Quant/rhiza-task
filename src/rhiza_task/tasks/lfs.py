"""The Git LFS tasks: lfs.mk, as tasks.

Three of the four are one git subcommand each. The fourth, ``lfs-install``, is 50 lines of
platform shell and is **deliberately not ported as written** -- what it did and what it is
now differ, so the change is stated here rather than discovered.

lfs.mk's ``lfs-install`` has two branches. On Linux it runs ``apt-get install git-lfs``,
with ``sudo`` when not root. On macOS it queries the GitHub releases API for the newest
git-lfs, downloads the architecture-matched zip into ``.local/tmp``, extracts the binary
into ``.local/bin``, and runs ``PATH=$PWD/.local/bin:$PATH git-lfs install``.

The macOS branch does not leave a working installation. ``.local/bin`` is not on PATH
after the recipe exits, and every other target in the fragment -- and every ``git lfs``
anywhere else -- invokes the bare command, so ``make lfs-install && make lfs-pull``
fails on a machine that had no git-lfs. The one thing the branch achieves that survives is
the ``git lfs install`` at the end, which writes the filter and hook configuration into
the repository.

So this task does that part, and *reports* how to install the binary rather than
downloading one. Two reasons beyond the broken branch: a task runner provisioned by
``uvx`` should not be shelling out to ``sudo apt-get`` as a side effect of a target
someone typed, and pinning a download URL to a release-API shape is a maintenance
liability for something ``brew``/``apt``/``winget`` all do properly.

Consumers who relied on the apt branch need one line of their own -- in CI, the
``setup-git-lfs`` action or the distribution's package; locally, their package manager.
"""

from __future__ import annotations

import sys

from ..config import Config
from ..spec import Failed, Guard, have, task
from ..uv import tool

SECTION = "Git LFS"

INSTALL_URL = "https://github.com/git-lfs/git-lfs#installing"

INSTALL_HINTS = {
    "darwin": "brew install git-lfs",
    "linux": "sudo apt-get install git-lfs  (or your distribution's package)",
    "win32": "winget install GitHub.GitLFS",
}
"""How to get the binary, by :data:`sys.platform`. Reported, never run."""

HAVE_LFS = Guard(tool="git-lfs", reason=f"git-lfs not found; see {INSTALL_URL}")
"""``git lfs <cmd>`` needs the ``git-lfs`` binary on PATH; git reports it as an unknown
command otherwise, which is a confusing way to learn that a tool is missing."""


def install_hint() -> str:
    """Return the platform's install command, for the message a missing binary produces.

    Returns:
        A command to run, or the project's install page when the platform is unknown.
    """
    return INSTALL_HINTS.get(sys.platform, f"see {INSTALL_URL}")


@task("lfs-install", "configure git-lfs for this repository", section=SECTION)
def lfs_install(cfg: Config) -> None:
    """Run ``git lfs install``, writing this repository's filter and hook configuration.

    Args:
        cfg: The resolved config.

    Raises:
        Failed: When the git-lfs binary is absent. A failure rather than a skip, unlike
            every other tool guard in this module's siblings: installing is the one thing
            this task exists to do, so it has nothing left to report success about.
    """
    if not have("git-lfs"):
        print(f"[ERROR] git-lfs not found. Install it with:\n    {install_hint()}")
        raise Failed(1, "git-lfs is not installed")
    tool("git", "lfs", "install", cwd=cfg.root)


@task("lfs-pull", "download the LFS files for the current branch", section=SECTION, guards=(HAVE_LFS,))
def lfs_pull(cfg: Config) -> None:
    """Fetch and check out the LFS objects the working tree points at.

    Args:
        cfg: The resolved config.
    """
    tool("git", "lfs", "pull", cwd=cfg.root)


@task("lfs-track", "list the patterns tracked by git-lfs", section=SECTION, guards=(HAVE_LFS,))
def lfs_track(cfg: Config) -> None:
    """Show the ``.gitattributes`` patterns routed through LFS.

    Args:
        cfg: The resolved config.
    """
    tool("git", "lfs", "track", cwd=cfg.root)


@task("lfs-status", "show the status of LFS files", section=SECTION, guards=(HAVE_LFS,))
def lfs_status(cfg: Config) -> None:
    """Show which LFS files are modified, staged, or not yet pushed.

    Args:
        cfg: The resolved config.
    """
    tool("git", "lfs", "status", cwd=cfg.root)
