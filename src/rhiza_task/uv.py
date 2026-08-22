"""The ways rhiza reaches a tool, and nothing else.

Every recipe in the retired *Python* make layer used one of exactly three forms:

* ``uv <subcommand>`` -- uv itself (``venv``, ``sync``, ``lock --check``).
* ``uvx <tool>`` -- an isolated one-shot tool run: prek, deptry, bandit, semgrep,
  zensical, genbadge.
* ``uv run --with a --with b <tool>`` -- a tool run *against the project environment*,
  because it imports the project's own code: pytest, interrogate, hypothesis, ty, mypy.

The second and third are a real distinction that the make layer already gets right, so it
is preserved here rather than unified.

rust.mk and go.mk add a fourth: ``$(CARGO) nextest run``, ``$(GO) test`` -- a toolchain
binary that is already on PATH, because uv does not provision cargo or go and nothing here
pretends otherwise. :func:`tool` is that form. It shares this module's environment handling
and echoing rather than being a bare ``subprocess.call`` in each language module, so
``$ cargo clippy`` is printed the same way ``$ uvx bandit`` is.

go.mk contributes one more, and it is the one that gets missed when these are counted:
:func:`capture`, which returns *stdout* rather than an exit status, for the recipe that
needs a value back rather than a verdict -- the licence gate, which has to interpolate
``go list -m`` into its own arguments. It is easy to overlook precisely because it is the
only form whose caller reads the result instead of just its status, and #131 is what that
cost: every prose total for this module disagreed with the code, and with the others. So no
sentence here gives one -- the public functions below are the authority, and a total in
prose goes stale the moment a form is added.

Two things disappear:

``install-uv`` as a *task*. bootstrap.mk curls ``https://astral.sh/uv/install.sh`` into
``./bin`` because make cannot assume uv exists. A process launched by ``uvx rhiza-task``
runs *because* uv exists, so nothing in this package can be the thing that provisions uv --
it would already be too late. The problem does not disappear with it, though: the make
layer's contract was that ``make <anything>`` works on a bare runner, so the bootstrap
lives on in the Makefile shim as three lines and one file target. What is gone is the 30
lines of probe-and-branch shell, and the ``bin/uv`` nobody ran directly.

The shell. Commands are argument vectors, never shell strings. rhiza.mk carries a 40-line
probe to detect make falling back to ``cmd.exe`` on Windows, because its recipes are
POSIX shell; with no shell there is nothing to detect.
"""

from __future__ import annotations

import os
import shutil

# Every call in this module is a fixed argument vector, and `shell=True` appears nowhere -- which
# is what bandit's B404 asks about. The reason sits here rather than on the suppression comment
# itself: bandit reads everything after that marker as a comma-separated list of test IDs, so a
# trailing explanation becomes one `Test in comment:` warning per word.
import subprocess  # nosec B404
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

from .spec import Failed

BLUE = "\033[36m"
RESET = "\033[0m"


def _bin(name: str, env_var: str) -> str:
    """Resolve a uv executable, honouring an override.

    Args:
        name: ``uv`` or ``uvx``.
        env_var: The override variable, e.g. ``RHIZA_UV_BIN``.

    Returns:
        An absolute path when one is found, else the bare name so the OS reports the
        failure with its own message.
    """
    return os.environ.get(env_var) or shutil.which(name) or name


def _run(argv: Sequence[str], cwd: Path, env: Mapping[str, str] | None = None) -> int:
    """Run a command, streaming its output, and return the exit status.

    Args:
        argv: The full argument vector.
        cwd: Working directory.
        env: Extra environment variables, merged over the current environment.

    Returns:
        The process exit status.
    """
    # Built as an explicitly typed dict rather than a `{**a, **b}` literal: subprocess's
    # `env` parameter is narrowly typed, and the inferred type of the literal is not
    # assignable to it.
    merged: dict[str, str] = dict(os.environ)
    merged.update(env or {})
    # uv warns when VIRTUAL_ENV points somewhere other than the project venv. rhiza.mk
    # handles this with `unexport VIRTUAL_ENV`; this is the same fix.
    merged.pop("VIRTUAL_ENV", None)
    merged.setdefault("UV_NO_MODIFY_PATH", "1")
    print(f"{BLUE}$ {' '.join(argv)}{RESET}", file=sys.stderr, flush=True)
    # `list(argv)` on its own line, not inlined into the call below. Inlined, the line held
    # two call nodes, and bandit runs every test against each: B603 fired on the subprocess
    # call and was suppressed, then returned None for `list(...)` and -- seeing B603 named in
    # the line's suppression comment -- warned `encountered (B603), but no failed test` on
    # every clean run. The suppression is live: B603 is a real finding here, low severity and
    # so below `bandit -ll`'s threshold. Hence one call per line rather than a deletion.
    command = list(argv)
    return subprocess.call(command, cwd=cwd, env=merged)  # noqa: S603  # nosec B603


def uv(*args: str, cwd: Path, check: bool = True, env: Mapping[str, str] | None = None) -> int:
    """Run uv itself.

    Args:
        *args: uv subcommand and arguments, e.g. ``("sync", "--frozen")``.
        cwd: Working directory.
        check: Raise on non-zero rather than returning the status.
        env: Extra environment variables.

    Returns:
        The exit status.

    Raises:
        Failed: When ``check`` and uv exited non-zero.
    """
    code = _run([_bin("uv", "RHIZA_UV_BIN"), *args], cwd, env)
    if check and code:
        raise Failed(code, f"uv {args[0] if args else ''} failed")
    return code


def uvx(
    tool: str,
    *args: str,
    cwd: Path,
    withs: Sequence[str] = (),
    python: str | None = None,
    check: bool = True,
    env: Mapping[str, str] | None = None,
) -> int:
    """Run an isolated tool via ``uvx``.

    Args:
        tool: The tool spec, e.g. ``deptry`` or ``'zensical>=0.0.36'``.
        *args: Arguments for the tool.
        cwd: Working directory.
        withs: Extra packages injected into the tool's environment. book.mk's
            ``MKDOCS_EXTRA_PACKAGES`` is the only current user.
        python: Interpreter for the tool itself. Usually omitted -- prek and the other
            language-neutral tools provision their own toolchains, which is why
            quality.mk was able to drop its ``-p ${PYTHON_VERSION}``.
        check: Raise on non-zero rather than returning the status.
        env: Extra environment variables.

    Returns:
        The exit status.

    Raises:
        Failed: When ``check`` and the tool exited non-zero.
    """
    argv = [_bin("uvx", "RHIZA_UVX_BIN")]
    if python:
        argv += ["-p", python]
    for w in withs:
        argv += ["--with", w]
    argv += [tool, *args]
    code = _run(argv, cwd, env)
    if check and code:
        raise Failed(code, f"{tool} failed")
    return code


def uv_run(
    tool: str,
    *args: str,
    cwd: Path,
    withs: Sequence[str] = (),
    no_project: bool = False,
    check: bool = True,
    env: Mapping[str, str] | None = None,
) -> int:
    """Run a tool against the project environment via ``uv run --with``.

    Args:
        tool: The executable, e.g. ``pytest``.
        *args: Arguments for the tool.
        cwd: Working directory.
        withs: Packages to inject, e.g. ``("pytest", "pytest-cov")``.
        no_project: Pass ``--no-project``, for a tool that must not see the project
            environment. marimo.mk's ``marimo`` target is the one case.
        check: Raise on non-zero rather than returning the status.
        env: Extra environment variables.

    Returns:
        The exit status.

    Raises:
        Failed: When ``check`` and the tool exited non-zero.
    """
    argv = [_bin("uv", "RHIZA_UV_BIN"), "run"]
    if no_project:
        argv.append("--no-project")
    for w in withs:
        argv += ["--with", w]
    argv += [tool, *args]
    code = _run(argv, cwd, env)
    if check and code:
        raise Failed(code, f"{tool} failed")
    return code


def tool(
    name: str,
    *args: str,
    cwd: Path,
    check: bool = True,
    env: Mapping[str, str] | None = None,
) -> int:
    """Run a toolchain binary that is expected to be on PATH.

    The Rust and Go layers' engines, which uv neither provisions nor knows about: cargo,
    rustup, go, and the binaries ``cargo-tools`` and ``go-tools`` install. Nothing is
    injected and nothing is isolated -- that is what makes it different from :func:`uvx`,
    not an oversight.

    Args:
        name: The executable, or an absolute path to one.
        *args: Its arguments.
        cwd: Working directory.
        check: Raise on non-zero rather than returning the status.
        env: Extra environment variables, e.g. ``RUSTDOCFLAGS``.

    Returns:
        The exit status.

    Raises:
        Failed: When ``check`` and the tool exited non-zero.
    """
    code = _run([shutil.which(name) or name, *args], cwd, env)
    if check and code:
        raise Failed(code, f"{Path(name).name} {args[0] if args else ''} failed".strip())
    return code


def capture(name: str, *args: str, cwd: Path) -> str:
    """Run a tool and return its stdout, for the one recipe that needs a value back.

    go.mk's licence gate is that recipe: ``go-licenses check ./... --ignore "$(go list -m)"``
    -- without the module's own path, go-licenses walks the project's own packages and fails
    a freshly synced project for having no LICENSE of its own. Found by rhiza's e2e suite
    rather than by a dry run, which is why it is carried over rather than rediscovered.

    Args:
        name: The executable.
        *args: Its arguments.
        cwd: Working directory.

    Returns:
        Stripped stdout, or an empty string when the tool failed or is absent.
    """
    try:
        result = subprocess.run(  # noqa: S603  # nosec B603
            [shutil.which(name) or name, *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""
