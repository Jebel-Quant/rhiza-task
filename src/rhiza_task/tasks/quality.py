"""The language-neutral gates: quality.mk and bootstrap.mk's ``clean``.

Nothing here needs to know how the project declares its dependencies, which is why the
template made ``core`` rather than a language layer own them.

The interesting one is ``rhiza-test``. quality.mk runs ``pytest .rhiza/tests`` -- a folder
synced from the template -- and prints a WARN and exits 0 when the folder is absent. Since
that folder was replaced by the ``pytest-rhiza`` distribution, consumers who excluded it
got a green gate measuring nothing, and jointview carries a 60-line Makefile override to
fix that for itself. Here the plugin *is* the implementation, so the override is not
needed and the silent-pass branch does not exist.

``docs-examples`` is registered here and implemented in :mod:`rhiza_task.tasks.fences`.
The split is worth knowing rather than discovering: the checker had grown to two thirds of
this module and pulled its maintainability index from 62 to 36, at which point the docstring
above described half a file. The task keeps the argument for *why* the gate exists, which is
what a reader looking for a gate wants; that module holds the argument for how it checks.
"""

from __future__ import annotations

import re
import shutil

# The hook-path probe below is a fixed argument vector, with no shell -- which is what bandit's
# B404 asks about. The reason sits here rather than on the suppression comment itself: bandit
# reads everything after that marker as a comma-separated list of test IDs, so a trailing
# explanation becomes one `Test in comment:` warning per word.
import subprocess  # nosec B404
from pathlib import Path

from ..config import Config
from ..spec import Guard, Skip, task
from ..uv import uv_run, uvx
from . import fences

TODO_PATTERN = re.compile(r"\b(TODO|FIXME|HACK):")
TODO_SUFFIXES = frozenset({".py", ".mk", ".sh", ".md", ".yml", ".yaml", ".toml", ".rs", ".go"})
TODO_SKIP_DIRS = frozenset(
    {
        ".git",
        ".venv",
        "node_modules",
        ".tox",
        "build",
        "dist",
        "_book",
        "_tests",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        "__pycache__",
    }
)

CLEAN_ARTIFACTS = ("dist", "build", ".coverage", ".pytest_cache", ".benchmarks", "_tests", "_book")


@task("fmt", "run the pre-commit hooks over all files", section="Quality")
def fmt(cfg: Config) -> None:
    """Run every configured hook via prek.

    ``--config`` is not decoration. By default prek treats every directory below the root
    holding a ``.pre-commit-config.yaml`` as a separate project and runs each one's hooks
    -- useful in a monorepo, wrong in rhiza's own repo where three bundles ship one as
    template content. Naming the config disables that discovery, so ``fmt`` means "this
    repo's config, once". A consumer wanting the monorepo behaviour drops the flag here and
    in the hook install.

    prek rather than pre-commit: a Rust reimplementation reading the same config file,
    which provisions each hook's toolchain itself. That is what removed the
    ``-p ${PYTHON_VERSION}`` this recipe used to need, and with it the coupling that made
    the language-neutral half of the template depend on a Python version being resolvable.

    Args:
        cfg: The resolved config.

    Raises:
        Skip: When the project has no pre-commit config.
    """
    if not (cfg.root / ".pre-commit-config.yaml").is_file():
        raise Skip("no .pre-commit-config.yaml")
    uvx("prek", "run", "--all-files", "--config", ".pre-commit-config.yaml", cwd=cfg.root)


@task("semgrep", "run the semgrep static analysis rules", section="Quality", guards=(Guard("source_folder"),))
def semgrep(cfg: Config) -> None:
    """Run semgrep against the source folder with rhiza's rule set.

    Args:
        cfg: The resolved config.

    Raises:
        Skip: When the rule file is absent.
    """
    rules = cfg.root / ".rhiza" / "semgrep.yml"
    if not rules.is_file():
        raise Skip("no .rhiza/semgrep.yml")
    uvx("semgrep", "--config", str(rules), cfg.source_folder, cwd=cfg.root)


@task("rhiza-test", "run the rhiza repository checks", section="Quality", needs=("install",))
def rhiza_test(cfg: Config) -> None:
    """Run the ``pytest-rhiza`` checks against this repository.

    The check modules are enumerated rather than globbed: pytest-rhiza ships the Rust and
    Go modules in the same distribution, so ``--pyargs pytest_rhiza.checks`` would collect
    checks that cannot pass on a Python project. See
    :data:`~rhiza_task.config.DEFAULT_RHIZA_CHECKS`.

    ``install`` is a prerequisite because the docstring check imports the project's own
    packages to run their doctests, which needs the dependencies present.

    ``RHIZA_DOCTEST_FOLDERS`` is what tells ``test_docstrings`` where to look, and it has
    to be passed: the check falls back to ``SOURCE_FOLDER`` in ``.rhiza/.env`` and then to
    a literal ``src``, so a repo whose Python lives anywhere else got
    ``SKIPPED  No doctest folder found (looked for: src)`` and a **green** gate -- the
    doctests went unchecked with nothing failing to say so. ``.rhiza/.env`` cannot cover
    for it either: since rhiza stopped shipping ``.rhiza/.gitignore``, whose only content
    was the ``!.env`` negation, that file is gitignored and a CI checkout never has one.
    ``quality.mk`` exported the variable from ``DOCSTRING_FOLDERS``; this is that export.

    Args:
        cfg: The resolved config.
    """
    uv_run(
        "pytest",
        "--pyargs",
        *cfg.rhiza_checks,
        cwd=cfg.root,
        withs=(cfg.pytest_rhiza,),
        env={"RHIZA_DOCTEST_FOLDERS": cfg.source_folder},
    )


@task(
    "test-pyproject",
    "run the pyproject.toml structure checks, verbosely",
    section="Quality",
    layer="python",
    needs=("install",),
)
def test_pyproject(cfg: Config) -> None:
    """Run just the pyproject check, with full reporting.

    A narrower, louder view of one module that ``rhiza-test`` also runs -- kept because it
    is what you want when that check is the thing you are fixing. The reporting flags are
    python.mk's verbatim.

    Args:
        cfg: The resolved config.
    """
    uv_run(
        "pytest",
        "--pyargs",
        "pytest_rhiza.checks.test_pyproject",
        "-v",
        "--tb=long",
        "--showlocals",
        "-rA",
        "--durations=0",
        "--no-header",
        cwd=cfg.root,
        withs=(cfg.pytest_rhiza,),
    )


@task("todos", "list every TODO, FIXME and HACK comment", section="Quality")
def todos(cfg: Config) -> None:
    """Report TODO/FIXME/HACK comments with file and line.

    quality.mk implements this as ``find -print0 | xargs -0 grep -nHE | grep -v | awk``,
    with a ``grep -v "make todos"`` filter to stop the recipe matching itself. Reading the
    files directly needs no such filter and no shell.

    Args:
        cfg: The resolved config.
    """
    hits = 0
    for path in sorted(_walk(cfg.root)):
        try:
            lines = path.read_text(errors="replace").splitlines()
        except OSError:
            # Skip, not fail: a file the gate cannot open is not a TODO, and one unreadable
            # path in a tree must not cost the report every hit after it. `errors="replace"`
            # already absorbs undecodable *bytes*, so what reaches here is the file that
            # could not be opened at all -- a permission bit, a dangling symlink, a name the
            # OS accepted and cannot serve.
            continue
        for number, line in enumerate(lines, start=1):
            if TODO_PATTERN.search(line):
                # as_posix, not str: this is report output a reader copies into a grep or an
                # editor's go-to-file, so the separator must not depend on the OS that ran
                # the gate. The paths are repo-relative and never touch the filesystem again.
                rel = path.relative_to(cfg.root).as_posix()
                print(f"{rel}:{number}: {line.strip()}")
                hits += 1
    print(f"\n[INFO] {hits} item(s) found.")


@task(
    "docs-examples",
    "check the fenced examples in the docs tree",
    section="Quality",
    needs=("install",),
    guards=(Guard("docs_folder"),),
)
def docs_examples(cfg: Config) -> None:
    """Parse every checkable fence under the docs folder, and diff the executed ones.

    The gap this closes: ``docs-coverage`` asks whether a docstring *exists* and
    markdownlint asks whether the markdown is *well-formed*. Neither asks whether what the
    documentation **claims** is still true, and a stale command keeps rendering perfectly --
    so the reader who finds out is a newcomer, at the worst moment. ``README.md`` was already
    covered, by pytest-rhiza's ``test_readme_validation`` under :func:`rhiza_test`; the docs
    tree had nothing, and it is the larger half.

    Not a second check of ``README.md``, deliberately: that file is pytest-rhiza's subject,
    and counting one verdict twice would make two gates report one fact.

    Which languages are checked, how, and why two of them can go unavailable on a working
    machine all live in :mod:`rhiza_task.tasks.fences`, which holds the checker. This is the
    registration and the argument for the gate; that module is the implementation.

    ``install`` is a prerequisite because the executed half imports the project's own
    packages, exactly as :func:`rhiza_test`'s docstring check does.

    Args:
        cfg: The resolved config.

    Raises:
        Skip: When the tree holds no checkable fence, so nothing was measured. A docs tree
            documenting nothing runnable would otherwise score a silent pass, which is the
            failure this gate exists to make visible.
        Failed: When at least one example is broken or stale.
    """
    fences.check(cfg)


@task("clean", "remove build artifacts and stale local branches", section="Dev")
def clean(cfg: Config) -> None:
    """Remove ignored files, build artifacts, and local branches whose remote is gone.

    ``.env`` files are preserved: they hold local configuration that is expensive to
    reconstruct and is not an artifact.

    Args:
        cfg: The resolved config.
    """
    git = shutil.which("git") or "git"
    _git(git, ["clean", "-d", "-X", "-f", "-e", "!.env", "-e", "!.env.*"], cfg.root)

    for name in CLEAN_ARTIFACTS:
        target = cfg.root / name
        if target.is_dir():
            shutil.rmtree(target, ignore_errors=True)
        elif target.exists():
            target.unlink(missing_ok=True)
    for egg in cfg.root.glob("*.egg-info"):
        shutil.rmtree(egg, ignore_errors=True)

    print("[INFO] removing local branches with no remote counterpart")
    _git(git, ["fetch", "--prune"], cfg.root)
    listing = _git(git, ["branch", "-vv"], cfg.root, capture=True)
    for line in listing.splitlines():
        # A leading * or + marks the current or a worktree-checked-out branch; neither can
        # be deleted, and attempting it is how the make recipe's xargs used to fail.
        if ": gone]" in line and not line.startswith(("*", "+")):
            branch = line.strip().split()[0]
            _git(git, ["branch", "-D", branch], cfg.root)


def _walk(root: Path) -> list[Path]:
    """Return the files worth scanning for TODO comments.

    Args:
        root: Repository root.

    Returns:
        Matching files, with skipped directories pruned.
    """
    found: list[Path] = []
    stack = [root]
    while stack:
        current = stack.pop()
        for entry in current.iterdir():
            if entry.is_dir():
                if entry.name not in TODO_SKIP_DIRS:
                    stack.append(entry)
            elif entry.suffix in TODO_SUFFIXES:
                found.append(entry)
    return found


def _git(git: str, args: list[str], cwd: Path, capture: bool = False) -> str:
    """Run a git command, tolerating failure.

    Args:
        git: The git executable.
        args: Arguments.
        cwd: Working directory.
        capture: Return stdout instead of streaming it.

    Returns:
        Captured stdout, or an empty string.
    """
    result = subprocess.run(  # noqa: S603  # nosec B603
        [git, *args],
        cwd=cwd,
        check=False,
        text=True,
        capture_output=capture,
    )
    return result.stdout or "" if capture else ""


def install_hooks(cfg: Config) -> None:
    """Install the prek git hooks unless an external manager owns ``core.hooksPath``.

    Neutral, and here rather than in a language module, because all three ``install``
    recipes carry it verbatim -- python.mk, rust.mk and go.mk each end with the same
    twelve lines of shell. prek provisions each hook's own toolchain, so there is nothing
    language-specific left in it.

    ``-c`` must be passed here *and* in :func:`fmt`: prek bakes the flag into the generated
    shim, so without it the commit-time gate rediscovers nested projects and stops meaning
    what ``fmt`` means.

    Args:
        cfg: The resolved config.
    """
    if not (cfg.root / ".pre-commit-config.yaml").is_file():
        return
    git = shutil.which("git") or "git"
    hooks_path = _git(git, ["config", "--get", "core.hooksPath"], cfg.root, capture=True).strip()
    if hooks_path:
        print("[INFO] skipping hook install: core.hooksPath is set")
        return
    # A hook-install failure warns rather than fails, as the make recipes do: it does not
    # invalidate the environment that was just built.
    uvx("prek", "install", "-c", ".pre-commit-config.yaml", cwd=cfg.root, check=False)
