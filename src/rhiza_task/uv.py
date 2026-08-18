"""The three ways rhiza reaches a tool, and nothing else.

Every recipe in the retired make layer used one of exactly three forms:

* ``uv <subcommand>`` -- uv itself (``venv``, ``sync``, ``lock --check``).
* ``uvx <tool>`` -- an isolated one-shot tool run: prek, deptry, bandit, semgrep,
  zensical, genbadge.
* ``uv run --with a --with b <tool>`` -- a tool run *against the project environment*,
  because it imports the project's own code: pytest, interrogate, mutmut, ty, mypy.

The second and third are a real distinction that the make layer already gets right, so it
is preserved here rather than unified.

Two things disappear:

``install-uv``. bootstrap.mk curls ``https://astral.sh/uv/install.sh`` into ``./bin``
because make cannot assume uv exists. A process launched by ``uvx rhiza-task`` runs
*because* uv exists, so the bootstrap problem leaves the task layer entirely -- one
``astral-sh/setup-uv`` step in CI, one documented prerequisite locally. That removes 30
lines and a whole ``bin/`` directory from every consumer.

The shell. Commands are argument vectors, never shell strings. rhiza.mk carries a 40-line
probe to detect make falling back to ``cmd.exe`` on Windows, because its recipes are
POSIX shell; with no shell there is nothing to detect.
"""

from __future__ import annotations

import os
import shutil
import subprocess  # nosec B404 - fixed argument vectors, never shell=True
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
    return subprocess.call(list(argv), cwd=cwd, env=merged)  # noqa: S603  # nosec B603


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
