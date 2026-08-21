"""The Python language layer: python.mk, as tasks.

python.mk is 312 lines, over half of the synced make. Most of it converts to the
declarative form in :mod:`rhiza_task.spec`; ``test`` is the one recipe that does not, and
it is written out in full below.

``complexity`` is the one task here with no make ancestor. It lives in this module because
radon is a Python tool and the gate is therefore Python-layer, even though its section is
``Quality`` alongside the neutral gates it reads like.
"""

from __future__ import annotations

import json
import shutil

from ..config import Config
from ..spec import REGISTRY, Failed, Guard, Skip, task
from ..uv import uv, uv_run, uvx
from .quality import install_hooks

PYTEST_WITHS = (
    "pytest",
    "pytest-cov",
    "pytest-xdist",
    "pytest-html",
    "pytest-timeout",
    "pytest-mock",
)
"""What ``test`` injects.

A named tuple of packages rather than a literal in the call, so CI and this package's own
tests can assert on it. The make recipe's six ``--with`` flags are invisible to anything
but a human reading the recipe.
"""

PYTEST_INTERNAL_ERROR = 3
"""pytest's INTERNALERROR.

Distinct from test failure (1), interruption (2) and usage error (4), which is what makes
retrying on it safe: it means the *runner* broke during worker or session teardown -- the
xdist ``worker_workerfinished`` KeyError, or a pytest-html report-write race -- not that a
test failed.
"""

MAX_ATTEMPTS = 2


@task("install", "create the venv and sync dependencies", section="Python", layer="python")
def install(cfg: Config) -> None:
    """Create ``.venv`` if absent, sync from the lock file, install the git hooks.

    Args:
        cfg: The resolved config.

    Raises:
        Skip: When the project has no ``pyproject.toml``.
        Failed: When the lock file is out of sync, or a step exits non-zero.
    """
    venv = cfg.root / ".venv"
    if not venv.is_dir():
        uv("venv", "--python", cfg.python_version, str(venv), cwd=cfg.root)
    else:
        print(f"[INFO] using existing virtual environment at {venv}")

    if not (cfg.root / "pyproject.toml").is_file():
        raise Skip("no pyproject.toml")

    frozen: tuple[str, ...] = ()
    if (cfg.root / "uv.lock").is_file():
        # python.mk runs this check, swallows its output and prints three lines of
        # guidance on failure. The check is worth keeping; the guidance belongs with it
        # rather than in a shell heredoc.
        if uv("lock", "--check", cwd=cfg.root, check=False):
            raise Failed(1, "uv.lock is out of sync with pyproject.toml -- run `uv lock`")
        frozen = ("--frozen",)

    # --inexact: leave packages uv did not manage in place instead of pruning them on
    # every run, so repeated task invocations do not churn the environment. Per-task
    # tooling is provisioned on the fly by uv.py, so there is no separate step for it.
    uv("sync", *cfg.uv_sync_args, "--inexact", *frozen, cwd=cfg.root)

    install_hooks(cfg)


@task(
    "test",
    "run all tests",
    section="Python",
    layer="python",
    needs=("install",),
    guards=(Guard("tests_folder", glob="test_*.py", reason="no test files found"),),
)
def test(cfg: Config) -> None:
    """Run the suite with coverage, retrying once on a pytest-internal teardown error.

    This is the recipe that justifies a real language. In python.mk it is a 40-line shell
    ``while :; do ... done`` inside a make recipe, with ``$$`` escaping on every variable,
    ``set --`` used to build the argument list because make cannot hold an array, and the
    retry condition spelled ``if [ $$status -ne 3 ]; then exit $$status; fi``.

    Args:
        cfg: The resolved config.

    Raises:
        Failed: When pytest reports test failures, or reports an internal error twice.
    """
    reports = cfg.root / "_tests"
    shutil.rmtree(reports, ignore_errors=True)

    args = [*_pytest_args(cfg)]
    if cfg.path("source_folder").is_dir():
        args += coverage_args(cfg)
    else:
        # Not a Skip: the tests exist and must run. Only coverage is unavailable.
        print(f"[WARN] source folder '{cfg.source_folder}' not found; running without coverage")
    args.append("--html=_tests/html-report/report.html")

    for attempt in range(1, MAX_ATTEMPTS + 1):
        # Stale data first: a crashed run can leave a corrupt .coverage file, which then
        # reports a false 0% on the next run.
        for stale in cfg.root.glob(".coverage*"):
            stale.unlink(missing_ok=True)
        (reports / "html-coverage").mkdir(parents=True, exist_ok=True)
        (reports / "html-report").mkdir(parents=True, exist_ok=True)

        code = uv_run("pytest", *args, cwd=cfg.root, withs=PYTEST_WITHS, check=False)
        if code != PYTEST_INTERNAL_ERROR:
            if code:
                raise Failed(code, "tests failed")
            return
        if attempt == MAX_ATTEMPTS:
            raise Failed(code, f"pytest reported an internal (teardown) error {attempt}x")
        print(f"[WARN] pytest exited {code} (xdist teardown race); retrying {attempt + 1}/{MAX_ATTEMPTS}")


@task(
    "coverage",
    "measure coverage and write _tests/coverage.xml",
    section="Python",
    layer="python",
    needs=("install",),
    guards=(
        Guard("tests_folder", glob="test_*.py", reason="no test files found"),
        Guard("source_folder"),
    ),
)
def coverage(cfg: Config) -> None:
    """Run the suite for its coverage reports.

    python.mk has no ``coverage`` target: its ``test`` recipe carries the ``--cov`` flags,
    so the Cobertura file CI uploads and ``book`` badges is a side effect of the test gate.
    rust.mk and go.mk both name ``coverage`` separately, and the gate-parity contract lists
    it for all three layers -- so the Python layer grows the name it was missing rather than
    the other two losing it.

    It is not a second test run in any meaningful sense: same suite, same floor, same
    output path. What it buys is a caller that wants the report without asserting anything
    about the HTML test report, and one name that means the same thing in all three layers.

    Args:
        cfg: The resolved config.
    """
    (cfg.root / "_tests" / "html-coverage").mkdir(parents=True, exist_ok=True)
    for stale in cfg.root.glob(".coverage*"):
        stale.unlink(missing_ok=True)
    uv_run("pytest", *_pytest_args(cfg), *coverage_args(cfg), cwd=cfg.root, withs=PYTEST_WITHS)


def _pytest_args(cfg: Config) -> list[str]:
    """Return the arguments both pytest-running gates share.

    Args:
        cfg: The resolved config.

    Returns:
        Parallelism, and the two folders the testing extras own.
    """
    return [
        "-n",
        "auto",
        f"--ignore={cfg.tests_folder}/benchmarks",
        f"--ignore={cfg.tests_folder}/stress",
    ]


def coverage_args(cfg: Config) -> list[str]:
    """Return the ``--cov`` flags, including the Cobertura path the other layers write to.

    Shared by ``test`` and ``coverage`` so the two cannot drift: ``_tests/coverage.xml`` is
    the file book.mk's badge step reads and CI uploads, and rust.mk and go.mk go out of
    their way to write it at exactly that path.

    Args:
        cfg: The resolved config.

    Returns:
        The coverage flags.
    """
    return [
        f"--cov={cfg.source_folder}",
        "--cov-report=term",
        "--cov-report=html:_tests/html-coverage",
        "--cov-report=json:_tests/coverage.json",
        "--cov-report=xml:_tests/coverage.xml",
        f"--cov-fail-under={cfg.coverage_fail_under}",
    ]


@task(
    "typecheck",
    "run ty and/or mypy (typechecker = ty | mypy | both)",
    section="Python",
    layer="python",
    needs=("install",),
    guards=(Guard("source_folder"),),
)
def typecheck(cfg: Config) -> None:
    """Run the configured type checker(s) over the source folder.

    The make recipe is a shell ``case`` with four branches, the fourth of which validates
    the setting and errors. Validation moved to :meth:`Config.__post_init__`, so an
    invalid value fails before a tool is provisioned, and what is left is a loop.

    Args:
        cfg: The resolved config.
    """
    checkers = ("ty", "mypy") if cfg.typechecker == "both" else (cfg.typechecker,)
    for checker in checkers:
        # The asymmetry is preserved from python.mk: mypy runs --strict, ty does not.
        args = ("check", cfg.source_folder) if checker == "ty" else ("--strict", cfg.source_folder)
        uv_run(checker, *args, cwd=cfg.root, withs=(checker,))


@task(
    "security",
    "run the bandit security scan",
    section="Python",
    layer="python",
    needs=("install",),
    guards=(Guard("source_folder"),),
)
def security(cfg: Config) -> None:
    """Scan the source folder with bandit.

    The scan scope lives in ``.bandit`` rather than in this argument list, so that every
    runner -- this task, the pre-commit hook, CI -- sees the same one. ``--ini`` is passed
    only when that file exists: python.mk passes it unconditionally, and bandit treats a
    missing ini as a usage error, so a project without one gets a red gate reporting a
    configuration problem as if it were a security finding.

    ``security`` does not mean the same thing in all three layers, and the asymmetry is
    inherited rather than introduced here. Rust runs ``cargo deny check advisories`` and Go
    runs ``govulncheck ./...`` -- both scan *dependencies* against an advisory database.
    Bandit is SAST: it lints the source this repository owns and never looks at what is
    installed. So Python, which has the largest advisory surface of the three, is the one
    layer whose ``security`` gate is not a dependency scan.

    No ``pip-audit`` here is a decision taken upstream, not an omission: ``jebel-quant/rhiza``
    dropped it in #1416 along with rhiza-tools, and pins its absence with a test
    (``tests/docs/test_doc_consistency.py`` -- "pip-audit is deliberately not wired up;
    this pins the fact the gate depends on"). This module is owned by this repository and
    nothing syncs it, so adding a scan here is *possible* -- but it would put a gate in
    consumers' CI that the template they also follow says is not there, and a transitive
    advisory with no fix available would then fail a run the template would have passed.
    Closing the gap belongs upstream, where both halves move together. Recorded here so
    the next reader does not have to rediscover which of the two it is.

    Args:
        cfg: The resolved config.
    """
    ini = ("--ini", ".bandit") if (cfg.root / ".bandit").is_file() else ()
    uvx("bandit", "-r", cfg.source_folder, "-ll", "-q", *ini, cwd=cfg.root)


@task("deps", "run deptry over the contributed folders", section="Python", layer="python", needs=("install",))
def deps(cfg: Config) -> None:
    """Check declared dependencies against actual imports.

    ``DEPTRY_FOLDERS`` and ``DEPTRY_IGNORE`` were make accumulators that each bundle
    appended to, which worked only because of include order. Here the folder set is
    *derived*: the source folder when it exists, plus the marimo folder when the marimo
    tasks are registered and that folder exists. DEP004 (misplaced development dependency)
    is ignored for the same reason marimo.mk ignores it -- notebooks legitimately import
    development dependencies.

    Args:
        cfg: The resolved config.

    Raises:
        Skip: When no contributed folder exists.
    """
    folders = [cfg.source_folder] if cfg.path("source_folder").is_dir() else []
    ignores = list(cfg.deptry_ignore)
    if "marimo" in REGISTRY and cfg.path("marimo_folder").is_dir():
        folders.append(cfg.marimo_folder)
        ignores += ["--ignore", "DEP004"]
    if not folders:
        raise Skip("no deptry folders")
    uvx("deptry", *folders, *ignores, cwd=cfg.root)


@task("license", "scan for copyleft licences", section="Python", layer="python", needs=("install",))
def license_(cfg: Config) -> None:
    """Fail on GPL/LGPL/AGPL among the installed distributions.

    ``--partial-match`` is load-bearing: without it pip-licenses compares against the whole
    licence string, and ``GPL`` never equals a real classifier such as "GNU General Public
    License v2 or later (GPLv2+)", so the gate passed with a GPL package installed.

    The docutils exemption is derived rather than accumulated. marimo depends on docutils,
    which is offered under a *choice* of licences and reports all of them as one string --
    "BSD License; GNU General Public License (GPL); Public Domain". pip-licenses has no
    notion of *or*, so ``--partial-match`` fires on the copyleft option even where a
    permissive one is taken.

    Args:
        cfg: The resolved config.
    """
    ignored = list(cfg.license_ignore_packages)
    if "marimo" in REGISTRY and "docutils" not in ignored:
        ignored.append("docutils")
    args = [f"--fail-on={';'.join(cfg.license_fail_on)}", "--partial-match"]
    if ignored:
        # --ignore-packages errors on a bare flag, so it is omitted entirely when nothing
        # is exempted.
        args += ["--ignore-packages", *ignored]
    uv_run("pip-licenses", *args, cwd=cfg.root, withs=("pip-licenses",))


@task(
    "docs-coverage",
    "check docstring coverage with interrogate",
    section="Python",
    layer="python",
    needs=("install",),
    guards=(Guard("source_folder"),),
)
def docs_coverage(cfg: Config) -> None:
    """Require 100% docstring coverage over the source and test folders.

    Args:
        cfg: The resolved config.
    """
    folders = [f for f in (cfg.source_folder, cfg.tests_folder) if (cfg.root / f).is_dir()]
    uv_run(
        "interrogate",
        "-vv",
        "--fail-under",
        "100",
        "--ignore-init-method",
        "--ignore-magic",
        *folders,
        cwd=cfg.root,
        withs=("interrogate",),
    )


@task(
    "complexity",
    "fail on a block above the cyclomatic-complexity ceiling",
    section="Quality",
    layer="python",
    guards=(Guard("source_folder"),),
)
def complexity(cfg: Config) -> None:
    """Fail when any block's cyclomatic complexity exceeds :attr:`Config.complexity_max`.

    The one task here that is not a python.mk port. It exists because this repository's own
    convention -- a C-ranked block carries a comment arguing why the flat form is preferred
    -- committed to a *number* in ``config.py``, and nothing read it back. A stated ceiling
    that only a human checks is the same shape as a doctest no gate executes: correct today,
    stale-proof only by discipline, in the one place growth is expected.

    Why the report goes through a file rather than a pipe: radon's verdict is a number per
    block, so the gate has to read its output, and ``-O`` is how radon hands output to
    something other than a terminal. That keeps the invocation a fixed argument vector with
    no shell and no capturing variant of :func:`~rhiza_task.uv.uvx` -- the same reason every
    other call in this package is one.

    ``closures`` is deliberately not walked. radon only fills it under ``--show-closures``,
    which is not passed, so a nested function's complexity is already counted in its
    parent's -- walking the empty list would suggest a coverage this gate does not have.

    Args:
        cfg: The resolved config.

    Raises:
        Skip: When radon produced no report, so nothing was measured.
        Failed: When at least one block is above the ceiling.
    """
    report = cfg.root / "_tests" / "complexity.json"
    report.parent.mkdir(parents=True, exist_ok=True)
    # Stale data first, for the reason `test` unlinks `.coverage*`: a report left by an
    # earlier run would be read as this run's verdict if radon failed to write.
    report.unlink(missing_ok=True)

    uvx("radon", "cc", cfg.source_folder, "--json", "--output-file", str(report), cwd=cfg.root)
    if not report.is_file():
        raise Skip("radon wrote no report")

    over = _over_ceiling(json.loads(report.read_text()), cfg.complexity_max)
    for label, score in over:
        print(f"{label}: {score}")
    if over:
        raise Failed(1, f"{len(over)} block(s) above the complexity ceiling of {cfg.complexity_max}")
    print(f"[INFO] no block above the complexity ceiling of {cfg.complexity_max}")


def _over_ceiling(measured: dict[str, object], ceiling: int) -> list[tuple[str, int]]:
    """Return the blocks above ``ceiling``, worst first.

    radon keys its JSON by path, and the value is either a list of blocks or -- for a file
    it could not parse -- a dict carrying an ``error``. The dict is skipped rather than
    raised on: an unparseable file is ruff's finding to report, and failing the complexity
    gate for it would put one syntax error behind two red gates.

    Args:
        measured: radon's parsed ``cc --json`` output.
        ceiling: The highest complexity a block may have.

    Returns:
        ``(label, complexity)`` pairs, highest complexity first, then by label so the
        ordering is total and the output is diffable between runs.
    """
    over: list[tuple[str, int]] = []
    for path, blocks in measured.items():
        if not isinstance(blocks, list):
            continue
        for block in blocks:
            score = int(block["complexity"])
            if score <= ceiling:
                continue
            qualified = f"{block['classname']}.{block['name']}" if block.get("classname") else block["name"]
            over.append((f"{path}:{block['lineno']} {qualified}", score))
    return sorted(over, key=lambda item: (-item[1], item[0]))


# `complexity` is deliberately *not* a prerequisite below, and the reason is semver rather
# than doubt about the gate. `all` is the aggregate every consumer's CI invokes, so adding a
# prerequisite to it fails builds in repositories that changed nothing -- a breaking change
# shipped as a feature. A consumer opts in by naming it, in `all`'s own `[tool.rhiza-task]`
# repo or in a workflow step, and this repository does the latter in ci.yml's `gates` job.
#
# Stated here for the reason ci.yml states the same thing about `semgrep`: a reader has to
# be able to tell "outside `all` on purpose" from "forgotten".
@task(
    "all",
    "run every gate, as CI does",
    section="Python",
    layer="python",
    needs=("fmt", "deps", "test", "docs-coverage", "security", "license", "typecheck", "rhiza-test"),
)
def all_(cfg: Config) -> None:
    """Aggregate. The body is empty because ``needs`` *is* the definition.

    python.mk's ``all`` named four gates that lived in the optional ``tests`` bundle, so a
    project syncing ``core + python-core`` without it had an ``all`` that could not run.
    Here an unregistered prerequisite is skipped by the runner, so the failure mode does
    not exist.

    Args:
        cfg: Unused; the prerequisites do the work.
    """
