"""The Go language layer: go.mk, as tasks.

The third sibling of python.py and rust.py, with the same gate names for the same reason:
``rhiza_ci.yml`` calls ``make security`` without knowing the language, and ``book``
consumes ``_tests/`` whatever produced it.

Two differences from the Rust layer are Go's own, not this port's. There is no ``rustup``
step, because ``go.mod``'s ``go`` and ``toolchain`` directives make the go command
download a matching toolchain itself. And the helper tools are ordinary modules installed
with ``go install`` rather than cargo subcommands, so they land in a directory this module
has to name -- ``bin/``, the same one the Makefile shim provisions uv into, rather than
whatever the developer's ``GOPATH`` happens to be.
"""

from __future__ import annotations

import shutil

# The one call below is a fixed argument vector, and `shell=True` appears nowhere -- which is what
# bandit's B404 asks about. The reason sits here rather than on the suppression comment itself:
# bandit reads everything after that marker as a comma-separated list of test IDs, so a trailing
# explanation becomes one `Test in comment:` warning per word.
import subprocess  # nosec B404
from pathlib import Path

from ..config import Config
from ..spec import Failed, Guard, have, task
from ..uv import capture, tool
from .quality import install_hooks

GO_TOOLS = (
    "github.com/golangci/golangci-lint/v2/cmd/golangci-lint@latest",
    "golang.org/x/vuln/cmd/govulncheck@latest",
    "github.com/google/go-licenses@latest",
    "github.com/boumenot/gocover-cobertura@latest",
    "github.com/mgechev/revive@latest",
)
"""What ``go-tools`` installs, as go.mk lists them.

The versions are ``@latest`` because go.mk's are: it holds each in its own
``*_VERSION ?=`` variable so that Renovate has one line to bump, and every one of those
lines currently says ``latest``. Pinning them is a decision for the template to make in
one place, not for this port to make silently on the way past.
"""

COVERAGE_PROFILE = "_tests/coverage.out"
"""Where ``go test -coverprofile`` writes, spelled with forward slashes on every OS.

Not ``Path.relative_to``: this string is an *argument to go*, not a filesystem operation,
and a backslash-separated path is a different argument. go accepts the forward-slash
spelling on Windows, and the gates run there.
"""

MANIFEST = Guard(file="go.mod", reason="no go.mod")
"""What every Go gate is guarded on: the module file, not a source folder."""


@task("install", "install the toolchain and download dependencies", section="Go", layer="go")
def install(cfg: Config) -> None:
    """Download the module's dependencies and install the git hooks.

    Args:
        cfg: The resolved config.

    Raises:
        Failed: When go is absent, or a step exits non-zero.
    """
    if not have("go"):
        raise Failed(1, "go not found -- install it from https://go.dev/doc/install (or: brew install go)")

    if (cfg.root / "go.mod").is_file():
        print("[INFO] downloading dependencies")
        tool("go", "mod", "download", cwd=cfg.root)
    else:
        print("[WARN] no go.mod; skipping download")

    install_hooks(cfg)


@task("go-tools", "install the Go tools the gates need", section="Go", layer="go")
def go_tools(cfg: Config) -> None:
    """Install each missing tool into the repository's ``bin/``.

    ``GOBIN`` rather than the developer's ``GOPATH``, so a gate never depends on what
    happens to be installed globally -- go.mk's reason, and the same directory the Makefile
    shim uses for uv.

    Args:
        cfg: The resolved config.
    """
    target = _bin_dir(cfg)
    target.mkdir(parents=True, exist_ok=True)
    for spec in GO_TOOLS:
        name = spec.rsplit("@", 1)[0].rsplit("/", 1)[-1]
        if (target / name).exists():
            continue
        print(f"[INFO] installing {name}")
        tool("go", "install", spec, cwd=cfg.root, env={"GOBIN": str(target)})
    print(f"[INFO] all Go tools available in {target}")


@task(
    "test",
    "run the test suite",
    section="Go",
    layer="go",
    needs=("install",),
    guards=(MANIFEST,),
)
def test(cfg: Config) -> None:
    """Run ``go test ./...`` with the race detector and shuffled order.

    Args:
        cfg: The resolved config.
    """
    reports = cfg.root / "_tests"
    reports.mkdir(parents=True, exist_ok=True)
    tool("go", "test", "./...", *cfg.go_test_flags, *cfg.go_flags, cwd=cfg.root)


@task(
    "coverage",
    "measure coverage and write _tests/coverage.xml",
    section="Go",
    layer="go",
    needs=("install", "go-tools"),
    guards=(MANIFEST,),
)
def coverage(cfg: Config) -> None:
    """Measure coverage, convert it to Cobertura, and enforce the floor.

    Three steps because Go's tooling splits them, and a fourth thing go.mk does in awk:
    ``go test`` has no ``--fail-under``, so the floor is enforced by reading the ``total:``
    line out of ``go tool cover -func``. That awk one-liner is the whole reason this is a
    task body rather than three argument vectors.

    ``-covermode=atomic`` because the default ``set`` mode is not race-safe and ``test``
    runs a race build.

    Args:
        cfg: The resolved config.

    Raises:
        Failed: When coverage is below ``coverage_fail_under``.
    """
    reports = cfg.root / "_tests"
    (reports / "html-coverage").mkdir(parents=True, exist_ok=True)
    profile = cfg.root / COVERAGE_PROFILE
    print(f"[INFO] measuring coverage (floor: {cfg.coverage_fail_under}%)")
    tool(
        "go",
        "test",
        "./...",
        "-covermode=atomic",
        f"-coverprofile={COVERAGE_PROFILE}",
        *cfg.go_flags,
        cwd=cfg.root,
    )
    _cobertura(cfg, profile, reports / "coverage.xml")
    tool(
        "go",
        "tool",
        "cover",
        f"-html={COVERAGE_PROFILE}",
        "-o",
        "_tests/html-coverage/index.html",
        cwd=cfg.root,
    )

    measured = _total_coverage(cfg)
    if measured is None:
        print("[WARN] could not read a total from `go tool cover -func`; floor not enforced")
        return
    if measured < cfg.coverage_fail_under:
        raise Failed(1, f"coverage {measured:.1f}% is below the {cfg.coverage_fail_under}% floor")
    print(f"[INFO] coverage {measured:.1f}% (floor: {cfg.coverage_fail_under}%)")


@task(
    "typecheck",
    "vet and lint (the compiler already type-checks)",
    section="Go",
    layer="go",
    needs=("install", "go-tools"),
    guards=(MANIFEST,),
)
def typecheck(cfg: Config) -> None:
    """Run ``go vet`` and golangci-lint.

    Args:
        cfg: The resolved config.
    """
    tool("go", "vet", "./...", *cfg.go_flags, cwd=cfg.root)
    tool(_tool_path(cfg, "golangci-lint"), "run", cwd=cfg.root)


@task(
    "docs-coverage",
    "fail on any undocumented exported item",
    section="Go",
    layer="go",
    needs=("install", "go-tools"),
    guards=(MANIFEST,),
)
def docs_coverage(cfg: Config) -> None:
    """Run revive's ``exported`` rule over the module.

    The closest analogue of interrogate that Go has: pass/fail on a missing doc comment
    rather than a percentage, exactly as rust-core's ``-D missing_docs`` is. ``revive.toml``
    is what enables that rule and no other, so its absence is a configuration gap rather
    than something to paper over -- revive says so itself.

    Args:
        cfg: The resolved config.
    """
    tool(_tool_path(cfg, "revive"), "-config", "revive.toml", "-set_exit_status", "./...", cwd=cfg.root)


@task(
    "security",
    "scan dependencies for known vulnerabilities",
    section="Go",
    layer="go",
    needs=("install", "go-tools"),
    guards=(MANIFEST,),
)
def security(cfg: Config) -> None:
    """Run govulncheck over the module.

    Args:
        cfg: The resolved config.
    """
    tool(_tool_path(cfg, "govulncheck"), "./...", cwd=cfg.root)


@task(
    "license",
    "run the licence compliance scan",
    section="Go",
    layer="go",
    needs=("install", "go-tools"),
    guards=(MANIFEST,),
)
def license_(cfg: Config) -> None:
    """Run go-licenses, ignoring the module's own packages.

    ``--ignore $(go list -m)`` is the load-bearing part, and it was found by rhiza's e2e
    suite rather than by reading the tool's help: go-licenses walks the project's own
    packages alongside its dependencies, so without it a repo with no LICENSE file of its
    own fails the gate on *itself* -- which every freshly synced project is.

    Args:
        cfg: The resolved config.
    """
    args = ["check", "./..."]
    if module := capture("go", "list", "-m", cwd=cfg.root):
        args += ["--ignore", module]
    else:
        print("[WARN] could not read the module path; go-licenses may fail on the project itself")
    tool(_tool_path(cfg, "go-licenses"), *args, cwd=cfg.root)


@task(
    "deps",
    "report dependency drift",
    section="Go",
    layer="go",
    needs=("install",),
    guards=(MANIFEST,),
)
def deps(cfg: Config) -> None:
    """Run ``go mod tidy -diff``.

    Both halves of deptry's job in one command, and no tool to install: it reports what
    tidy *would* change -- an unused requirement or a missing one -- and exits non-zero.

    Args:
        cfg: The resolved config.
    """
    tool("go", "mod", "tidy", "-diff", cwd=cfg.root)


@task(
    "all",
    "run every gate, as CI does",
    section="Go",
    layer="go",
    needs=("fmt", "test", "docs-coverage", "security", "deps", "license", "typecheck", "rhiza-test"),
)
def all_(cfg: Config) -> None:
    """Aggregate, with go.mk's prerequisite list. The body is empty because ``needs`` is it.

    Args:
        cfg: Unused; the prerequisites do the work.
    """


def _cobertura(cfg: Config, profile: Path, target: Path) -> None:
    """Convert a Go coverage profile to Cobertura XML.

    The one recipe in the whole port that genuinely needs a pipe: gocover-cobertura reads
    stdin and writes stdout, so there is no argument vector that expresses it. Handled here
    with two file handles rather than by giving :mod:`~rhiza_task.uv` a redirection feature
    nothing else would use -- and still no shell.

    Args:
        cfg: The resolved config.
        profile: The ``go test -coverprofile`` output.
        target: Where to write the Cobertura XML.

    Raises:
        Failed: When the conversion exits non-zero.
    """
    converter = _tool_path(cfg, "gocover-cobertura")
    print(f"[INFO] writing {target.name}")
    with profile.open("rb") as source, target.open("wb") as out:
        code = subprocess.call(  # noqa: S603  # nosec B603
            [shutil.which(converter) or converter],
            cwd=cfg.root,
            stdin=source,
            stdout=out,
        )
    if code:
        raise Failed(code, "gocover-cobertura failed")


def _total_coverage(cfg: Config) -> float | None:
    """Return the total coverage percentage, or None when it cannot be read.

    Replaces go.mk's awk over ``go tool cover -func``: the total line is the only place Go
    reports a single number, and ``go test`` has no floor of its own to set.

    Args:
        cfg: The resolved config.

    Returns:
        The percentage, or None.
    """
    report = capture("go", "tool", "cover", f"-func={COVERAGE_PROFILE}", cwd=cfg.root)
    for line in reversed(report.splitlines()):
        if line.startswith("total:"):
            try:
                return float(line.split()[-1].rstrip("%"))
            except ValueError:  # pragma: no cover - a malformed total line
                return None
    return None


def _bin_dir(cfg: Config) -> Path:
    """Return the directory ``go-tools`` installs into.

    Args:
        cfg: The resolved config.

    Returns:
        ``<root>/bin``.
    """
    return cfg.root / "bin"


def _tool_path(cfg: Config, name: str) -> str:
    """Return how to invoke a Go tool: from PATH if it is there, else from ``bin/``.

    go.mk always spells these ``$(GO_BIN_DIR)/<name>``, which is right for CI and wrong for
    a developer who installed golangci-lint through their package manager. Preferring PATH
    costs nothing -- ``go-tools`` only ever fills the gaps.

    Args:
        cfg: The resolved config.
        name: The tool's binary name.

    Returns:
        The name, when it is on PATH, else the absolute path under ``bin/``.
    """
    return name if shutil.which(name) else str(_bin_dir(cfg) / name)
