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

The hook is POSIX shell by name and by nature, and Windows is where that shows. The
executable check is skipped there -- ``os.access(X_OK)`` reports every existing file as
executable, so asking would be a guard that guarantees nothing -- and the OS refusing to run
a ``.sh`` is reported as a failure with the reason attached rather than as a traceback. A
project that must provision on Windows needs its own arrangement; this task will tell you
clearly that it could not.
"""

from __future__ import annotations

import os

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
        Failed: When the hook is not executable, or exits non-zero.
    """
    hook = cfg.root / HOOK
    if not hook.is_file():
        print(f"[INFO] no {HOOK}; nothing to provision")
        return
    # POSIX-only, because `os.access(X_OK)` cannot answer this on Windows: there is no
    # execute bit, so it reports *every* existing file as executable and the check would
    # pass vacuously. Asking anyway is worse than not asking -- it reads like a guard while
    # guaranteeing nothing. What covers Windows is the OSError below.
    if os.name == "posix" and not os.access(hook, os.X_OK):
        raise Failed(1, f"{HOOK} is not executable -- run `chmod +x {HOOK}`")
    try:
        tool(str(hook), cwd=cfg.root)
    except OSError as exc:
        # Not a Windows special case, though it is how Windows arrives: the OS refuses to
        # execute the file at all. A malformed shebang on POSIX raises ENOEXEC through the
        # same path. Either way an unhandled OSError would surface as a traceback, which is
        # a poor way to learn that a provisioning script cannot start.
        raise Failed(1, f"could not run {HOOK}: {exc}") from exc
