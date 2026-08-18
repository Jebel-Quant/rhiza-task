"""The language-neutral gates: quality.mk and bootstrap.mk's ``clean``.

Nothing here needs to know how the project declares its dependencies, which is why the
template made ``core`` rather than a language layer own them.

The interesting one is ``rhiza-test``. quality.mk runs ``pytest .rhiza/tests`` -- a folder
synced from the template -- and prints a WARN and exits 0 when the folder is absent. Since
that folder was replaced by the ``pytest-rhiza`` distribution, consumers who excluded it
got a green gate measuring nothing, and jointview carries a 60-line Makefile override to
fix that for itself. Here the plugin *is* the implementation, so the override is not
needed and the silent-pass branch does not exist.
"""

from __future__ import annotations

import re
import shutil
import subprocess  # nosec B404 - fixed argument vectors
from pathlib import Path

from ..config import Config
from ..spec import Guard, Skip, task
from ..uv import uv_run, uvx

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

    Args:
        cfg: The resolved config.
    """
    uv_run("pytest", "--pyargs", *cfg.rhiza_checks, cwd=cfg.root, withs=(cfg.pytest_rhiza,))


@task("test-pyproject", "run the pyproject.toml structure checks, verbosely", section="Quality", needs=("install",))
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
        except OSError:  # pragma: no cover - unreadable file
            continue
        for number, line in enumerate(lines, start=1):
            if TODO_PATTERN.search(line):
                rel = path.relative_to(cfg.root)
                print(f"{rel}:{number}: {line.strip()}")
                hits += 1
    print(f"\n[INFO] {hits} item(s) found.")


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
