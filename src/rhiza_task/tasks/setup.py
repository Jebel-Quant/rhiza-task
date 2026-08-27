"""The repository's own environment hook: the seam a template cannot ship.

A rhiza-managed project may need a **native binary** in place before any gate can run --
graphviz for a docs plugin, ``libpq`` for psycopg, pandoc, an ODBC driver. The template
owns every configuration file in such a repository, so there was nowhere to put that step,
and the answer rhiza documented did not work.

That answer was to shadow ``install`` in ``local.mk``. It never ran. The Makefile is a shim
whose ``%:`` catch-all forwards the *goal* to this CLI, and ``install`` is a prerequisite
**here** rather than at make level -- so ``make test`` resolves it in :mod:`rhiza_task.runner`
and never consults a make rule of that name. CI is worse: every reusable workflow invokes
``uvx rhiza-task <gate>`` directly and never runs make at all. The recipe fired only if
someone typed ``make install`` by hand, which is exactly when a consumer would test it and
conclude it worked.

So the hook belongs where ``install`` is, and this is it. ``install`` is the prerequisite of
essentially every gate, in all three language layers, which is what makes one insertion point
enough: local ``make test``, GitHub Actions, GitLab CI and the devcontainer's ``make install``
all arrive here without a workflow edit between them.

**This is not the package growing a package manager**, which :mod:`rhiza_task.tasks.lfs`
rules out: a runner provisioned by ``uvx`` should not shell out to ``sudo apt-get`` as a
side effect of a target someone typed. That rule is about *this* package owning
apt/brew/winget logic and its liability. Running a script the repository committed inverts
it -- the CLI decides *when*, the repository decides *what*, and the platform detection
stays with the people who know which platforms they build on.

It is also why there is no ``system-packages = [...]`` setting instead. ``graphviz`` happens
to be spelled the same on apt and brew; ``libgl1-mesa-glx`` is not, and a list cannot express
"download this tarball" -- which is precisely what rhiza itself does for tectonic. A list
would be a schema this package then had to keep honest across three package managers, for the
subset of needs that happen to be a package name.

The hook is POSIX shell by name and by nature, and Windows is where that shows -- but not, as
this module first had it, as a wall. Windows cannot ``exec`` a ``.sh`` at all: the OS answers
*%1 is not a valid Win32 application* before the shebang is read, so the first version of this
task reported that as a failure and told the reader their platform needed its own arrangement.
Which was one insertion point short of the design's own claim -- ``install`` is a prerequisite
of every gate, so a consumer whose matrix includes ``windows-latest`` could not adopt the hook
at all, even for a dependency that only matters on one job. See #148.

So on a platform with no execute bit the hook is handed to ``sh`` rather than started
directly, and there is one to hand it to: GitHub's ``windows-latest`` runners ship git-bash,
whose ``sh`` is on PATH and is what :func:`shutil.which` finds. One hook file still covers
every platform, which is the whole point of putting the seam here; a project that provisions
differently per OS branches inside the script, where the platform knowledge already is.

Two things follow from handing it to ``sh`` instead of exec'ing it, and both are why the
POSIX branch is left alone rather than unified with it. The shebang is **not** consulted --
a hook whose first line names ``python3`` runs under python3 on POSIX and under ``sh`` on
Windows -- which is what the ``.sh`` in the name is promising and this docstring is repeating.
And a machine with no ``sh`` on PATH is told *that*, rather than being handed the Win32 error
from an exec that was never going to work.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from ..config import Config
from ..spec import Failed, task
from ..uv import tool

HOOK = "local-setup.sh"
"""The repo-owned hook, at the repository root.

A fixed name rather than a setting, for the same reason the Makefile hard-codes
``-include local.mk``: a seam whose *location* is configurable has to be discovered before it
can be used, and there is nothing a project could express by moving it. It sits beside
``local.mk`` and is committed for the same reason -- anything CI invokes has to be in the
repository.
"""

POSIX = os.name == "posix"
"""Whether this platform can start the hook itself.

One platform fact with two consequences, which is why it is one constant rather than two.

It decides whether there is an **execute bit worth asking about**. Windows has none:
``os.access(path, os.X_OK)`` reports *every* existing file as executable there, so the check
below would pass vacuously -- a guard that reads like protection while guaranteeing nothing,
which is worse than not asking.

And it decides **how the hook is started**: exec'd directly where the OS honours a shebang,
handed to ``sh`` where it does not. The two answers move together because they are the same
question asked twice -- a platform that has no execute bit is a platform that will not start
the file for you.

Named rather than inlined as ``os.name == "posix"`` so the assumption is visible at module
level, and so a test can reach *both* branches on either platform. Patching ``os.name`` to
get there is not an option -- it is read by :mod:`pathlib` at import, and forcing it makes
``Path`` unconstructible.
"""

SHELL = "sh"
"""The interpreter the hook is handed to where the platform cannot start it.

``sh`` rather than ``bash``, because the hook's contract is POSIX shell and ``sh`` is the
spelling git-bash, WSL and every POSIX system agree on. Not a setting: a repository that
needs a different interpreter says so in its shebang, which is honoured everywhere the
platform starts the file itself.
"""


def _command(hook: Path) -> tuple[str, ...]:
    """Build the argument vector that starts the hook on this platform.

    Args:
        hook: The absolute path to the hook.

    Returns:
        The vector to hand to :func:`~rhiza_task.uv.tool`.

    Raises:
        Failed: When the platform cannot start the file and has no ``sh`` to do it.
    """
    if POSIX:
        return (str(hook),)
    # A missing shell fails rather than skipping, for the same reason a missing execute bit
    # does: the repository asked for provisioning and it did not happen. It is deliberately
    # not a `Guard` either -- a guard is declared on the task and would skip the *common*
    # case, a repository with no hook at all, on any machine without `sh`.
    shell = shutil.which(SHELL)
    if shell is None:
        raise Failed(
            1,
            f"could not run {HOOK}: no `{SHELL}` on PATH to run it with -- "
            "install Git for Windows, whose git-bash provides one",
        )
    return (shell, str(hook))


@task("setup", "run the repository's own environment setup hook", section="Dev")
def setup(cfg: Config) -> None:
    """Run ``local-setup.sh`` if the repository has one.

    The asymmetry between the two failure modes is the whole design. A repository with no
    hook is a genuine no-op, so it skips. A hook that exists but is not executable is a
    mistake someone made while expecting it to run, so it fails rather than passing quietly
    -- that is the case a skip would turn into the silent-green outcome this hook exists to
    remove.

    **An absent hook succeeds rather than skipping**, and that is not the usual call in this
    package. :class:`~rhiza_task.spec.Skip` means work was asked for and did not happen,
    which is why ``--strict`` promotes it to a failure: CI can then assert that a gate
    measured something. Nothing was asked for here. Most repositories need no native
    provisioning at all, so skipping would make every ``--strict`` invocation fail on the
    common case -- ``book --strict`` reaches this through five prerequisites -- and a switch
    that cannot be used is worth less than the line it prints. The INFO line is what keeps
    the outcome legible.

    Args:
        cfg: The resolved config.

    Raises:
        Failed: When the hook is not executable, cannot be started, or exits non-zero.
    """
    hook = cfg.root / HOOK
    if not hook.is_file():
        print(f"[INFO] no {HOOK}; nothing to provision")
        return
    if POSIX and not os.access(hook, os.X_OK):
        raise Failed(1, f"{HOOK} is not executable -- run `chmod +x {HOOK}`")
    command = _command(hook)
    try:
        tool(*command, cwd=cfg.root)
    except OSError as exc:
        # Windows no longer arrives here -- `_command` hands it a shell rather than the
        # script. What still does is a malformed shebang on POSIX, which raises ENOEXEC
        # through this same path, and a resolved `sh` that will not start. Either way an
        # unhandled OSError would surface as a traceback, which is a poor way to learn that
        # a provisioning script cannot begin.
        raise Failed(1, f"could not run {HOOK}: {exc}") from exc
