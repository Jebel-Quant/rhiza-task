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

# The hook-path probe below is a fixed argument vector, with no shell -- which is what bandit's
# B404 asks about. The reason sits here rather than on the suppression comment itself: bandit
# reads everything after that marker as a comma-separated list of test IDs, so a trailing
# explanation becomes one `Test in comment:` warning per word.
import subprocess  # nosec B404
import textwrap
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from ..config import Config
from ..spec import Failed, Guard, Skip, task
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

# Deliberately permissive about what follows the language: mkdocs-material accepts
# ```python title="x", and an opening fence this pattern failed to match would have its
# *closing* fence read as the next opening one, cascading the misparse through the rest of
# the file. Matching any fence line and keeping only the first word cannot do that.
DOC_FENCE_OPEN = re.compile(r"^(?P<indent>[ \t]*)```(?P<language>[^`\s]*)")
# A closing fence carries nothing but backticks. A bare ``` therefore matches both patterns,
# which is why :func:`_fences` tracks state instead of classifying lines independently.
DOC_FENCE_CLOSE = re.compile(r"^[ \t]*```[ \t]*$")
# `bash` and `sh` only. `console` and `shell-session` are excluded on purpose: their content
# is a transcript -- prompts, output and all -- so `bash -n` would reject the very thing that
# makes them correct. A language this set does not name is counted as unchecked and reported
# rather than guessed at.
SHELL_FENCE_LANGUAGES = frozenset({"bash", "sh"})
PYTHON_FENCE_LANGUAGE = "python"
# The convention README.md already uses and pytest-rhiza's `test_readme_validation` already
# checks *there*: a ```result``` block holds the expected stdout of the python fence above it.
RESULT_FENCE_LANGUAGE = "result"
CHECKED_FENCE_LANGUAGES = SHELL_FENCE_LANGUAGES | {PYTHON_FENCE_LANGUAGE, RESULT_FENCE_LANGUAGE}


@dataclass(frozen=True)
class Fence:
    """One fenced code block, located and dedented.

    Attributes:
        path: Repository-relative path, posix-separated, because this reaches report output
            a reader pastes into an editor.
        line: 1-based line number of the opening fence.
        language: The info string's first word, lowercased; empty when the fence carries none.
        code: The block's content, dedented.
    """

    path: str
    line: int
    language: str
    code: str


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

    Three kinds of fence are checked and the rest are counted:

    * ``python`` -- :func:`compile`, so a fence that is a *fragment* still passes. Names need
      not resolve; only the syntax is asserted.
    * ``bash``/``sh`` -- ``bash -n``, which parses without executing. Never executed, because
      a README's shell is routinely ``rm -rf`` and ``git push``, and an unparseable fence is a
      documentation bug without running it.
    * ``result`` -- executed and diffed against the python fences above it.

    Anything else -- ``toml``, ``mermaid``, ``makefile``, ``yaml``, and fences carrying no
    language at all -- is reported as unchecked. Naming the count is the point: silence there
    would read as "everything was checked".

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
    scratch = cfg.root / "_tests" / "docs-examples"
    scratch.mkdir(parents=True, exist_ok=True)
    # Resolved once: `bash` is absent on a stock Windows runner, and its absence must leave
    # the shell fences *unchecked and counted* rather than failing the gate for a fact about
    # the machine. Same reasoning as `Guard(tool=...)` raising Skip rather than Failed.
    bash = shutil.which("bash")

    per_file = [
        _fences(md.relative_to(cfg.root).as_posix(), md.read_text(errors="replace"))
        for md in sorted(cfg.path("docs_folder").rglob("*.md"))
    ]
    fences = [fence for one_file in per_file for fence in one_file]

    broken = [
        *_syntax_violations(fences),
        *(_shell_violations(fences, bash, scratch) if bash else []),
        *_result_violations(per_file, cfg, scratch),
    ]
    for violation in broken:
        print(violation)

    if not _report(fences, bash, len(per_file)):
        raise Skip(f"no checkable fence under {cfg.docs_folder}")
    if broken:
        raise Failed(1, f"{len(broken)} broken example(s) under {cfg.docs_folder}")


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


def _fences(path: str, text: str) -> list[Fence]:
    """Return every fenced code block in one markdown file, in document order.

    Indentation is why this is a state machine rather than a regex over the whole file:
    mkdocs admonitions and content tabs indent their fences by four spaces, and ``faq.md``
    indents one by three inside a numbered list. ``textwrap.dedent`` on the collected body is
    what makes those compile -- without it every fence inside an admonition is an
    ``IndentationError``, which would be a finding against this checker rather than the docs.

    Nested fences are not handled, and cannot be: distinguishing them needs the four-backtick
    form, which no file in this repository uses. If one appears, its inner fence closes the
    outer block early and the languages reported go wrong -- visible in the inventory line
    rather than silent, which is the reason that line prints a per-language count.

    Args:
        path: Repository-relative path, stored on each returned fence.
        text: The file's content.

    Returns:
        The fences found, dedented.
    """
    fences: list[Fence] = []
    language: str | None = None
    start = 0
    body: list[str] = []
    for number, line in enumerate(text.splitlines(), start=1):
        if language is None:
            opening = DOC_FENCE_OPEN.match(line)
            if opening:
                language, start, body = opening.group("language").lower(), number, []
            continue
        if DOC_FENCE_CLOSE.match(line):
            fences.append(Fence(path, start, language, textwrap.dedent("\n".join(body))))
            language = None
            continue
        body.append(line)
    return fences


def _syntax_violations(fences: list[Fence]) -> list[str]:
    """Return one message per python fence that does not parse.

    :func:`compile` rather than execution, so a fence holding a *fragment* -- ``guards =
    (Guard("source_folder"),)`` in ``adding_a_task.md``, with ``Guard`` never imported --
    passes. Undefined names are not the question; syntax is.

    Args:
        fences: Every fence in the tree.

    Returns:
        Violation messages, one per broken fence.
    """
    broken: list[str] = []
    for fence in fences:
        if fence.language != PYTHON_FENCE_LANGUAGE:
            continue
        try:
            compile(fence.code, f"{fence.path}:{fence.line}", "exec")
        except SyntaxError as exc:
            # The *offending* line, not the fence's: `getting_started.md` holds 24 fences, and
            # "somewhere in this file" is the part of a report a reader has to redo by hand.
            # `fence.line` is the opening backticks, so body line 1 sits one below it, which is
            # what makes this sum the absolute line rather than one short of it.
            broken.append(f"{fence.path}:{fence.line + (exc.lineno or 0)}: python fence does not parse: {exc.msg}")
    return broken


def _shell_violations(fences: list[Fence], bash: str, scratch: Path) -> list[str]:
    """Return one message per shell fence that does not parse.

    ``-n`` is the whole point: bash reads and parses the script and exits without running a
    command of it. So this validates ``rm -rf`` and ``git push`` fences without their
    consequences.

    Captured rather than streamed through :func:`~rhiza_task.uv.tool`, which every other
    binary in this package goes through, for two reasons that both come from this being a
    checker rather than a gate over one command: the message is wanted *per fence* and lives
    on stderr, and echoing ``$ bash -n ...`` once per fence would bury the report it exists to
    produce under twenty invocation lines. ``_git`` above captures for the same reason.

    Args:
        fences: Every fence in the tree.
        bash: Absolute path to bash, already resolved by the caller.
        scratch: Directory for the throwaway script.

    Returns:
        Violation messages, one per broken fence.
    """
    broken: list[str] = []
    script = scratch / "fence.sh"
    for fence in fences:
        if fence.language not in SHELL_FENCE_LANGUAGES:
            continue
        script.write_text(fence.code)
        checked = subprocess.run(  # noqa: S603  # nosec B603
            [bash, "-n", str(script)],
            capture_output=True,
            text=True,
            check=False,
        )
        if checked.returncode:
            # bash reports against the throwaway path and its own line numbers, neither of
            # which the reader can act on. The fence's own location is already the prefix, so
            # the script path is stripped to leave the diagnosis.
            detail = checked.stderr.strip().splitlines()
            message = detail[-1].replace(str(script), "fence") if detail else f"exit {checked.returncode}"
            broken.append(f"{fence.path}:{fence.line}: shell fence does not parse: {message}")
    return broken


def _result_violations(per_file: list[list[Fence]], cfg: Config, scratch: Path) -> list[str]:
    """Return one message per ``result`` block that no longer matches what its python prints.

    This is the half that catches an example gone *stale* rather than malformed, which is the
    failure with the longest half-life: the fence still parses, still renders, and is wrong.

    The prelude is every python fence earlier in the same file, concatenated, because that is
    what ``README.md``'s pair needs -- the first fence defines the ``audit`` task with
    ``@task`` and the second calls ``lookup("audit")``, so running the second alone raises.
    The whole captured stdout is then compared against the block.

    That comparison is exact but for surrounding whitespace, and it carries one assumption
    worth stating: a prelude fence that *prints* would have its output counted as part of the
    result. No file here has one. If that changes, the fix is to fence off the prelude's
    output rather than to loosen the diff, because a loosened diff is how a stale example
    starts passing again.

    Args:
        per_file: Fences grouped by file, so a prelude cannot reach across files.
        cfg: The resolved config.
        scratch: Directory for the throwaway script and its captured stdout.

    Returns:
        Violation messages, one per stale or unrunnable block.
    """
    broken: list[str] = []
    for one_file in per_file:
        for index, fence in enumerate(one_file):
            if fence.language != RESULT_FENCE_LANGUAGE:
                continue
            prelude = [f.code for f in one_file[:index] if f.language == PYTHON_FENCE_LANGUAGE]
            if not prelude:
                broken.append(f"{fence.path}:{fence.line}: result block with no python fence above it")
                continue
            printed = _run_fences(cfg, scratch, prelude)
            if printed is None:
                broken.append(f"{fence.path}:{fence.line}: the python above this block exited non-zero")
            elif printed.strip() != fence.code.strip():
                broken.append(
                    f"{fence.path}:{fence.line}: result block is stale\n"
                    f"           expected: {fence.code.strip()!r}\n"
                    f"           actual:   {printed.strip()!r}"
                )
    return broken


def _run_fences(cfg: Config, scratch: Path, codes: list[str]) -> str | None:
    """Run python fences in the project environment and return their stdout.

    A subprocess, and not :func:`exec` in this process, which would be shorter: the fences in
    ``adding_a_task.md`` call ``@task``, and ``@task`` registers into the live
    :data:`~rhiza_task.spec.REGISTRY`. Running them here would add an ``audit`` task to the
    process running the gate, so a later ``list`` in the same ``rhiza-task all`` would print a
    task that does not exist. Isolation is a requirement here, not caution.

    stdout arrives through a file rather than a pipe for the reason ``complexity`` reads
    radon's ``--output-file``: :func:`~rhiza_task.uv.uv_run` streams rather than captures, and
    adding a capturing variant to ``uv.py`` for one caller would widen that module's surface
    for it. The script redirects its own stdout, so the invocation stays a fixed argument
    vector with no shell -- and the fences' tracebacks still reach the terminal on stderr,
    which is where a reader wants them.

    Args:
        cfg: The resolved config.
        scratch: Directory for the script and its captured stdout.
        codes: The python fences to run, in document order.

    Returns:
        The captured stdout, or None when the script exited non-zero or wrote nothing.
    """
    printed = scratch / "stdout.txt"
    # Stale output first, for the reason `complexity` unlinks its report: output left by an
    # earlier run would be read as this run's, and a diff that passes against last run's
    # stdout is worse than no diff.
    printed.unlink(missing_ok=True)
    script = scratch / "fences.py"
    script.write_text(
        f"import sys\nsys.stdout = open({str(printed)!r}, 'w', encoding='utf-8')\n"
        + "\n".join(codes)
        + "\nsys.stdout.flush()\n"
    )
    code = uv_run("python", script.relative_to(cfg.root).as_posix(), cwd=cfg.root, check=False)
    if code or not printed.is_file():
        return None
    return printed.read_text(errors="replace")


def _tally(fences: list[Fence]) -> tuple[int, int, int, list[tuple[str, int]]]:
    """Count the fences by what can be done with them.

    Split out of :func:`_report` rather than inlined there, which read more directly: the two
    together score C on cyclomatic complexity, and a fifth C block in this package is a
    fifth thing a reader has to accept an argument for. Counting and printing are genuinely
    separate jobs, so this is the decomposition the metric asks for rather than a contortion
    to satisfy it.

    Args:
        fences: Every fence in the tree.

    Returns:
        ``(python, shell, diffed, unchecked)``, where ``unchecked`` pairs each remaining
        language with its count, commonest first and then alphabetically so the line is
        diffable between runs. A fence with no language is counted under ``(none)``.
    """
    tally = Counter(fence.language or "(none)" for fence in fences)
    unchecked = sorted(
        ((language, count) for language, count in tally.items() if language not in CHECKED_FENCE_LANGUAGES),
        key=lambda item: (-item[1], item[0]),
    )
    return (
        tally[PYTHON_FENCE_LANGUAGE],
        sum(tally[language] for language in SHELL_FENCE_LANGUAGES),
        tally[RESULT_FENCE_LANGUAGE],
        unchecked,
    )


def _report(fences: list[Fence], bash: str | None, files: int) -> bool:
    """Print the inventory and report whether anything was checkable.

    The unchecked count is printed rather than dropped because "0 examples" and "43 fences
    nothing looks at" both pass every other gate in this repository while documenting nothing
    verifiable. A reader seeing only a green line would take it for full coverage.

    Args:
        fences: Every fence in the tree.
        bash: Path to bash, or None when it is absent.
        files: How many markdown files were read.

    Returns:
        True when at least one fence was checked, so the caller can skip rather than pass.
    """
    python, shell, diffed, unchecked = _tally(fences)
    checked = python + diffed + (shell if bash else 0)
    print(f"\n[INFO] {files} file(s), {len(fences)} fence(s): {checked} checked")
    print(f"[INFO] {python} python, {shell} shell, {diffed} diffed")
    if bash is None and shell:
        print(f"[INFO] bash not found: {shell} shell fence(s) went unchecked")
    if unchecked:
        listing = ", ".join(f"{count} {language}" for language, count in unchecked)
        print(f"[INFO] {sum(count for _, count in unchecked)} fence(s) not checkable: {listing}")
    return checked > 0


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
