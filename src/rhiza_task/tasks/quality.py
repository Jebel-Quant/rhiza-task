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
import tomllib
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
# `tomllib` is stdlib at the floor this package declares (`requires-python = ">=3.11"`), so
# the toml half costs nothing to provision -- and it is the half that matters most here: the
# toml fences under `docs/` are this package's own `[tool.rhiza-task]` and `[tool.bumpversion]`
# examples, which is precisely the class that goes stale when a setting is renamed or a
# default moves. Eleven of them at the time of writing, against two yaml.
TOML_FENCE_LANGUAGE = "toml"
# `yml` as well as `yaml`: mkdocs-material's own docs spell it both ways, and a fence this set
# failed to name would be silently counted as unchecked rather than parsed -- the exact
# silence this gate exists to break.
YAML_FENCE_LANGUAGES = frozenset({"yaml", "yml"})

# The driver :func:`_yaml_violations` writes out and runs. A module-level constant with its
# body at column 0, rather than an indented literal inside that function, and the indentation
# is the whole point: `CLAUDE.md` documents
# `grep -rnE '^\s+(from|import) ' src/` as the way to check this package for deferred imports,
# and tells the reader it returns two lines. An indented `import yaml` inside a string literal
# matched that pattern and took it to four -- two false positives in the one check the
# layering invariant is verified by, which is worse than the invariant being unchecked because
# it teaches the reader to ignore the output. At column 0 nothing matches and the documented
# count holds.
#
# `started.txt` is written before any parsing and `report.txt` after all of it, so the caller
# can tell three outcomes apart that would otherwise collapse into one: the parser could not
# be provisioned (no marker at all), the checker ran and crashed (marker, no report), and the
# checker finished (both). Without the first marker a bug in this script is indistinguishable
# from a machine with no network, and the gate passes green either way.
YAML_CHECKER_SCRIPT = """\
import pathlib

import yaml

here = pathlib.Path(__file__).parent / "yaml"
(here / "started.txt").write_text("ok")
broken = []
for number, where in enumerate((here / "index.txt").read_text().splitlines()):
    try:
        yaml.safe_load((here / f"{number:04d}.yaml").read_text())
    except yaml.YAMLError as exc:
        # Flattened: a YAMLError's str spans four lines with a caret diagram, and the
        # report this joins is one line per violation.
        detail = " ".join(str(exc).split())
        broken.append(f"{where}: yaml fence does not parse: {detail}")
(here / "report.txt").write_text("\\n".join(broken))
"""
CHECKED_FENCE_LANGUAGES = (
    SHELL_FENCE_LANGUAGES | YAML_FENCE_LANGUAGES | {PYTHON_FENCE_LANGUAGE, RESULT_FENCE_LANGUAGE, TOML_FENCE_LANGUAGE}
)


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


@dataclass(frozen=True)
class Tally:
    """How many fences of each kind the tree holds.

    A record rather than the tuple this used to be. Four counts unpacked positionally were
    already at the edge of readable; toml and yaml take it to six, where
    ``python, shell, toml, yaml, diffed, unchecked = _tally(fences)`` stops being checkable by
    eye and a transposed pair would report shell fences as toml with nothing failing. The
    fields carry the meaning instead.

    Attributes:
        python: ``python`` fences, checked by :func:`compile`.
        shell: ``bash``/``sh`` fences, checked by ``bash -n`` when bash is present.
        toml: ``toml`` fences, checked in-process by :mod:`tomllib`.
        yaml: ``yaml``/``yml`` fences, checked by a provisioned parser in a subprocess.
        diffed: ``result`` fences, executed and compared against the python fences above them.
        unchecked: Each remaining language paired with its count, commonest first and then
            alphabetically so the report line is diffable between runs. A fence with no
            language at all is counted under ``(none)``.
    """

    python: int
    shell: int
    toml: int
    yaml: int
    diffed: int
    unchecked: list[tuple[str, int]]


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

    Five kinds of fence are checked and the rest are counted:

    * ``python`` -- :func:`compile`, so a fence that is a *fragment* still passes. Names need
      not resolve; only the syntax is asserted.
    * ``bash``/``sh`` -- ``bash -n``, which parses without executing. Never executed, because
      a README's shell is routinely ``rm -rf`` and ``git push``, and an unparseable fence is a
      documentation bug without running it.
    * ``toml`` -- :func:`tomllib.loads`, in this process. Stdlib at this package's Python
      floor, so it needs nothing provisioned.
    * ``yaml``/``yml`` -- a real parser, provisioned into a subprocess because adding one to
      this package's runtime dependencies to serve two fences would be the wrong trade: it is
      a published CLI whose install cost every consumer pays.
    * ``result`` -- executed and diffed against the python fences above it.

    Anything else -- ``mermaid``, ``makefile``, and fences carrying no language at all -- is
    reported as unchecked. Naming the count is the point: silence there would read as
    "everything was checked".

    Parsing is *not* validation, and the distinction is worth keeping: a ``toml`` fence that
    parses may still name a setting this package does not have, and a ``yaml`` fence that
    parses may still be an invalid workflow. Schema-checking either would need the schema, and
    for the workflow snippets actionlint already owns that question over the real files. What
    this closes is the narrower gap where a fence stopped being the language it claims.

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

    # None, not [], when the parser could not be provisioned: an empty list is
    # "checked, all sound", and reporting a machine's missing network as a clean bill of
    # health is the one thing this gate must never do. `bash` is handled the same way one
    # line above, by the same argument.
    yaml_broken = _yaml_violations(fences, cfg, scratch)

    broken = [
        *_syntax_violations(fences),
        *(_shell_violations(fences, bash, scratch) if bash else []),
        *_toml_violations(fences),
        *(yaml_broken or []),
        *_result_violations(per_file, cfg, scratch),
    ]
    for violation in broken:
        print(violation)

    if not _report(fences, bash, yaml_broken is not None, len(per_file)):
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


def _toml_violations(fences: list[Fence]) -> list[str]:
    """Return one message per toml fence that does not parse.

    In this process and with no subprocess, unlike every other checker here, because
    :mod:`tomllib` is stdlib from 3.11 and this package declares ``requires-python = ">=3.11"``.
    So there is nothing to provision and nothing to skip for: a toml fence is checked on every
    machine that can run the gate at all, which is not true of the shell or yaml halves.

    A *fragment* is accepted the way :func:`_syntax_violations` accepts one -- ``tomllib``
    parses a bare ``key = value`` with no table header perfectly well, which is what most
    configuration examples in ``docs/`` are. Only genuine syntax errors are reported.

    Args:
        fences: Every fence in the tree.

    Returns:
        Violation messages, one per broken fence.
    """
    broken: list[str] = []
    for fence in fences:
        if fence.language != TOML_FENCE_LANGUAGE:
            continue
        try:
            tomllib.loads(fence.code)
        except tomllib.TOMLDecodeError as exc:
            # `exc` already carries "(at line N, column M)", and that N is relative to the
            # fence body. Prefixing the fence's own line would produce two numbers meaning
            # different things on one line, so the message is taken whole and the fence is
            # located by its opening line alone -- the same choice `_shell_violations` makes.
            broken.append(f"{fence.path}:{fence.line}: toml fence does not parse: {exc}")
    return broken


def _yaml_violations(fences: list[Fence], cfg: Config, scratch: Path) -> list[str] | None:
    """Return one message per yaml fence that does not parse, or None when unmeasured.

    The one checker here that needs a package this project does not depend on. That is the
    whole reason it is a subprocess: ``rhiza-task`` is a published CLI, so every runtime
    dependency is an install cost paid by every consumer on every ``uvx`` invocation, and
    taking one on to parse two fences in this repository's own docs would be a poor trade.
    ``uv_run(..., withs=("pyyaml",), no_project=True)`` provisions it for the length of one
    call instead -- the same move :func:`~rhiza_task.tasks.book.marimo` makes for marimo, and
    the reason :mod:`rhiza_task.uv` grew ``withs`` at all.

    The fences are written to files rather than embedded in the generated script. Embedding
    would need them escaped into a literal, and a yaml fence is exactly the kind of text --
    quotes, backslashes, indentation that carries meaning -- where an escaping bug would look
    like a parse failure in the document. Files move the bytes without reinterpreting them.

    Three outcomes, deliberately distinguished by two marker files rather than by an exit
    status. ``uv`` exits non-zero both when it cannot resolve ``pyyaml`` and when the script it
    provisioned crashes, so the status alone cannot separate a machine's missing network from
    this repository's bug -- and collapsing them is the worse error, because "unavailable" is a
    pass. ``started.txt`` is written immediately after ``import yaml`` and ``report.txt`` after
    the last fence, so their presence answers it: neither means the parser never arrived, the
    first alone means the checker died mid-run, and both mean it finished.

    Args:
        fences: Every fence in the tree.
        cfg: The resolved config.
        scratch: Directory for the throwaway files.

    Returns:
        Violation messages, one per broken fence; an empty list when the tree holds no yaml
        fence or every one of them parsed; a single-item list naming the script when it started
        and did not finish, which fails the gate; or None when the parser could not be
        provisioned at all, so the caller can report the fences as unchecked rather than sound.
    """
    targets = [fence for fence in fences if fence.language in YAML_FENCE_LANGUAGES]
    if not targets:
        return []

    folder = scratch / "yaml"
    folder.mkdir(parents=True, exist_ok=True)
    for number, fence in enumerate(targets):
        (folder / f"{number:04d}.yaml").write_text(fence.code)
    (folder / "index.txt").write_text("\n".join(f"{fence.path}:{fence.line}" for fence in targets))

    started = folder / "started.txt"
    report = folder / "report.txt"
    # Unlinked first: each file's *existence* is a verdict, so a stale copy from a previous
    # invocation would report last run's outcome as this one's.
    started.unlink(missing_ok=True)
    report.unlink(missing_ok=True)
    script = scratch / "fence_yaml.py"
    script.write_text(YAML_CHECKER_SCRIPT)

    code = uv_run(
        "python",
        script.relative_to(cfg.root).as_posix(),
        cwd=cfg.root,
        withs=("pyyaml",),
        no_project=True,
        check=False,
    )
    if not started.is_file():
        # Never reached the first statement after `import yaml`: no network, no such package,
        # no interpreter. A fact about the machine, so the caller counts these fences as
        # unchecked rather than failing the gate.
        return None
    if not report.is_file():
        # Started and did not finish, which is this repository's bug and not the machine's --
        # so it is a violation, and the gate goes red. Before `started.txt` existed this case
        # returned None and read as "parser unavailable", meaning a broken checker reported
        # itself as a clean skip.
        return [f"{script.name}: the yaml checker started and did not finish (exit {code})"]
    # An empty report file is "ran, found nothing", which splitlines turns into [] -- distinct
    # from the None above, and the distinction is the point of writing the files at all.
    return report.read_text(errors="replace").splitlines()


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


def _tally(fences: list[Fence]) -> Tally:
    """Count the fences by what can be done with them.

    Split out of :func:`_report` rather than inlined there, which read more directly: the two
    together score C on cyclomatic complexity, and a fifth C block in this package is a
    fifth thing a reader has to accept an argument for. Counting and printing are genuinely
    separate jobs, so this is the decomposition the metric asks for rather than a contortion
    to satisfy it.

    Since toml and yaml joined, that argument has stopped being about taste: at B (7) here and
    B (10) there, one function doing both would land above ``complexity_max`` and fail
    ``rhiza-task complexity`` outright. So this split is now held by the gate rather than by
    the reader who remembers why it is here.

    Args:
        fences: Every fence in the tree.

    Returns:
        The counts, as a :class:`Tally`.
    """
    tally = Counter(fence.language or "(none)" for fence in fences)
    unchecked = sorted(
        ((language, count) for language, count in tally.items() if language not in CHECKED_FENCE_LANGUAGES),
        key=lambda item: (-item[1], item[0]),
    )
    # B604 matches the *keyword name* `shell=` below and reads this as a subprocess call being
    # handed a shell. It is a field assignment on a frozen dataclass of integers, and the
    # nearest subprocess is two hundred lines away. Renaming the field to dodge the pattern
    # would cost the symmetry with `python`, `toml`, `yaml` and `diffed` -- which is the whole
    # reason the tuple became a record -- so the suppression is the cheaper trade.
    #
    # The marker sits on this line and not on `shell=` itself, which is where the finding is
    # reported and where it was first written. bandit locates a B604 at the keyword but does
    # its nosec bookkeeping against the enclosing call node, so a marker on the keyword line
    # suppresses the finding *and* then reports itself as unmatched -- one
    # `nosec encountered ... but no failed test` line in every `fmt` and `security` run, which
    # is noise in the two gates whose value is a clean signal. Here both agree and neither
    # fires. The marker carries no prose for the reason this file's header gives: bandit reads
    # anything after it as more test IDs.
    return Tally(  # nosec B604
        python=tally[PYTHON_FENCE_LANGUAGE],
        shell=sum(tally[language] for language in SHELL_FENCE_LANGUAGES),
        toml=tally[TOML_FENCE_LANGUAGE],
        yaml=sum(tally[language] for language in YAML_FENCE_LANGUAGES),
        diffed=tally[RESULT_FENCE_LANGUAGE],
        unchecked=unchecked,
    )


def _report(fences: list[Fence], bash: str | None, yaml_ran: bool, files: int) -> bool:
    """Print the inventory and report whether anything was checkable.

    The unchecked count is printed rather than dropped because "0 examples" and "43 fences
    nothing looks at" both pass every other gate in this repository while documenting nothing
    verifiable. A reader seeing only a green line would take it for full coverage.

    Two of the five kinds can go unchecked on a machine that runs the gate fine otherwise --
    shell without bash, yaml without a provisioned parser -- and each gets its own line saying
    so. They are counted out of ``checked`` in that case rather than assumed sound, which is
    the same rule the tool guards elsewhere in this package follow.

    B (10) after toml and yaml were added, from B (6). The growth is one branch per kind that
    can be unavailable, which is open-ended rather than closed, so it gets a ceiling: **a
    sixth checkable language that needs provisioning takes this to C (12)**, and at that point
    the availability lines want a loop over ``(count, ran, noun)`` triples rather than a
    branch each. A kind that cannot go unavailable -- anything stdlib, as toml is -- costs
    nothing here and does not count against that.

    Args:
        fences: Every fence in the tree.
        bash: Path to bash, or None when it is absent.
        yaml_ran: Whether the yaml parser could be provisioned.
        files: How many markdown files were read.

    Returns:
        True when at least one fence was checked, so the caller can skip rather than pass.
    """
    tally = _tally(fences)
    checked = tally.python + tally.diffed + tally.toml + (tally.shell if bash else 0) + (tally.yaml if yaml_ran else 0)
    print(f"\n[INFO] {files} file(s), {len(fences)} fence(s): {checked} checked")
    print(
        f"[INFO] {tally.python} python, {tally.shell} shell, "
        f"{tally.toml} toml, {tally.yaml} yaml, {tally.diffed} diffed"
    )
    if bash is None and tally.shell:
        print(f"[INFO] bash not found: {tally.shell} shell fence(s) went unchecked")
    if not yaml_ran and tally.yaml:
        print(f"[INFO] yaml parser unavailable: {tally.yaml} yaml fence(s) went unchecked")
    if tally.unchecked:
        listing = ", ".join(f"{count} {language}" for language, count in tally.unchecked)
        print(f"[INFO] {sum(count for _, count in tally.unchecked)} fence(s) not checkable: {listing}")
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
