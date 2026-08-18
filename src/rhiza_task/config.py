"""Configuration, and the resolution order that replaces make's ``?=`` and ``+=``.

The make layer builds its settings from three overlapping mechanisms: ``?=`` defaults in
the fragment that owns a setting, ``+=`` accumulation from other fragments
(``DEPTRY_FOLDERS``, ``LICENSE_IGNORE_PACKAGES``, ``RHIZA_CHECKS``), and a repo-owned
Makefile or ``local.mk`` assigning over the top. The precedence is a consequence of
include order, which is why rhiza.mk has to explain that ``-include .rhiza/make.d/*.mk``
comes last and ``-include local.mk`` last of all.

Here the order is explicit and testable, lowest precedence first:

1. The dataclass defaults below.
2. ``.rhiza/.env`` -- kept unchanged, because it is already the file consumers edit and
   the reusable workflows read it too.
3. ``[tool.rhiza-task]`` in ``pyproject.toml`` -- the new home for what used to require
   editing a synced ``.mk`` file or shadowing a target.
4. ``RHIZA_*`` (or bare make-style) environment variables.
5. Command-line flags.

The ``+=`` accumulators do not survive as a mechanism, and do not need to: every one of
them was a bundle contributing something it owned, which the task body can now *derive*
by asking whether the contributing task is registered. See ``tasks/python.py``'s ``deps``
and ``license``.
"""

from __future__ import annotations

import json
import os
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

from dotenv import dotenv_values

TYPECHECKERS = ("ty", "mypy", "both")

DEFAULT_RHIZA_CHECKS = (
    "pytest_rhiza.checks.test_readme",
    "pytest_rhiza.checks.test_release_tags",
    "pytest_rhiza.checks.test_pyproject",
    "pytest_rhiza.checks.test_docstrings",
    "pytest_rhiza.checks.test_readme_validation",
)
"""The Python check set, enumerated rather than globbed.

pytest-rhiza ships the Rust and Go modules (``test_cargo_toml``, ``test_go_module``) in
the same distribution, so ``--pyargs pytest_rhiza.checks`` would collect checks that
cannot pass here. This is jointview's ``RHIZA_CHECKS`` list, promoted from a shadowed make
variable to a default: the 60-line override in its Makefile exists only because the make
layer had nowhere else to put it.
"""


DEFAULT_CI_OS_MATRIX = ("ubuntu-latest",)
"""The OS every consumer gets unless it asks for more.

Named rather than inlined because two callers need the same value: the field default
below, and the floor in ``rhiza-task ci-os-matrix`` that stops an explicitly empty
setting reaching GitHub as a zero-job matrix.
"""


@dataclass
class Config:
    """Resolved settings for one repository.

    Field names are the lowercased make variables, so the mapping to what a consumer
    already knows stays one-to-one and greppable.
    """

    source_folder: str = "src"
    tests_folder: str = "tests"
    marimo_folder: str = "docs/notebooks"
    book_output: str = "_book"
    python_version: str = "3.13"

    coverage_fail_under: int = 90

    # ty | mypy | both. python.mk documents that ``both`` masks ty's exit status behind
    # mypy's, and jointview sets ``ty`` in .rhiza/.env for that reason. The shell ``case``
    # whose fourth branch validated this is replaced by __post_init__, so a typo now fails
    # before any tool is provisioned rather than after.
    typechecker: str = "ty"

    # Matched as substrings -- see ``--partial-match`` in the ``license`` task.
    license_fail_on: tuple[str, ...] = ("GPL", "LGPL", "AGPL")
    license_ignore_packages: tuple[str, ...] = ()

    deptry_ignore: tuple[str, ...] = ()
    mkdocs_extra_packages: tuple[str, ...] = ()
    zensical_version: str = ">=0.0.36"
    uv_sync_args: tuple[str, ...] = ("--all-extras", "--all-groups")
    ci_os_matrix: tuple[str, ...] = DEFAULT_CI_OS_MATRIX

    # Pinned to a tag rather than a branch: a gate that moves under you is not a gate.
    pytest_rhiza: str = "pytest-rhiza @ git+https://github.com/Jebel-Quant/pytest-rhiza@v0.2.0"
    rhiza_checks: tuple[str, ...] = DEFAULT_RHIZA_CHECKS

    # Turns Skip into failure. The answer to jointview's own complaint about "a green gate
    # measuring nothing": set it in CI and a missing folder is a red build rather than a
    # yellow line nobody reads.
    strict: bool = False

    root: Path = field(default_factory=Path.cwd)

    def __post_init__(self) -> None:
        """Normalise the list fields, then validate the enumerated and numeric ones.

        :func:`_coerce` sees a string without knowing which field it is destined for, so
        it can only recognise the two shapes that announce themselves -- a JSON array and
        a ``;``-separated list. Everything else it leaves as a ``str``, and a ``str``
        reaching a ``tuple[str, ...]`` field is splatted one *character* per argument at
        the call site: ``UV_SYNC_ARGS="--group test"`` became ``uv sync - - g r o u p``.

        The field's type is known here and nowhere lower, so this is where a string
        becomes a tuple. Splitting on whitespace is the make layer's own format --
        python.mk documented ``LICENSE_IGNORE_PACKAGES`` as space-separated and
        ``RHIZA_CHECKS`` accumulated space-separated module names -- so a ``.rhiza/.env``
        written for make keeps working, as does the ``UV_SYNC_ARGS`` that rhiza's synced
        ``.devcontainer/bootstrap.sh`` exports.

        Raises:
            ValueError: When ``typechecker`` is not one of ty, mypy, both, or
                ``coverage_fail_under`` is outside 0-100.
        """
        for f in fields(self):
            if str(f.type).replace(" ", "") != "tuple[str,...]":
                continue
            value = getattr(self, f.name)
            if isinstance(value, str):
                object.__setattr__(self, f.name, tuple(value.split()))
            elif isinstance(value, list):
                object.__setattr__(self, f.name, tuple(value))

        if self.typechecker not in TYPECHECKERS:
            msg = f"typechecker must be one of {', '.join(TYPECHECKERS)} (got {self.typechecker!r})"
            raise ValueError(msg)
        if not 0 <= int(self.coverage_fail_under) <= 100:
            msg = f"coverage_fail_under must be a percentage (got {self.coverage_fail_under!r})"
            raise ValueError(msg)

    @property
    def folders(self) -> dict[str, str]:
        """Return the folder settings, for :meth:`~rhiza_task.spec.Guard.check`.

        Returns:
            Mapping of field name to configured relative path.
        """
        return {f.name: getattr(self, f.name) for f in fields(self) if f.name.endswith("_folder")}

    def path(self, folder_field: str) -> Path:
        """Resolve a folder field to an absolute path.

        Args:
            folder_field: A field name such as ``source_folder``.

        Returns:
            The absolute path.
        """
        return self.root / getattr(self, folder_field)

    @classmethod
    def load(cls, root: Path | None = None, **overrides: Any) -> Config:
        """Build a config by walking the five layers in order.

        Args:
            root: Repository root; defaults to the current directory.
            **overrides: Layer 5, the command-line flags. ``None`` values are ignored so
                an unset flag does not shadow a configured value.

        Returns:
            The resolved config.
        """
        root = (root or Path.cwd()).absolute()
        raw: dict[str, Any] = {}
        raw.update(_from_env_file(root / ".rhiza" / ".env"))
        raw.update(_from_pyproject(root / "pyproject.toml"))
        raw.update(_from_environ(os.environ))
        raw.update({k: v for k, v in overrides.items() if v is not None})

        # .python-version wins over a configured python_version for the reason python.mk
        # reads it: it is what uv itself honours, so a second source of truth could only
        # ever disagree.
        pv = root / ".python-version"
        if pv.is_file() and (text := pv.read_text().strip()):
            raw["python_version"] = text

        known = {f.name for f in fields(cls)} - {"root"}
        return cls(root=root, **{k: v for k, v in raw.items() if k in known})


_FIELD_NAMES = frozenset(f.name for f in fields(Config))
"""Every field name, for :func:`_key`. Built once rather than per environment variable."""


def _from_env_file(path: Path) -> dict[str, Any]:
    """Read ``.rhiza/.env``.

    An empty assignment (``RHIZA_CI_OS_MATRIX=``) is dropped rather than carried as an
    empty string, for the reason given in :func:`_from_environ`: no field's type has a
    meaningful empty value, so the only thing an empty setting can sensibly mean is
    "leave the lower layer alone".

    Args:
        path: Path to the dotenv file.

    Returns:
        Parsed settings; empty when the file is absent.
    """
    if not path.is_file():
        return {}
    return {_key(k): _coerce(v) for k, v in dotenv_values(path).items() if v and v.strip()}


def _from_pyproject(path: Path) -> dict[str, Any]:
    """Read ``[tool.rhiza-task]``.

    Values here are already typed by TOML, so they bypass :func:`_coerce` -- a TOML array
    arrives as a list and is tupled, nothing is parsed out of a string.

    Args:
        path: Path to pyproject.toml.

    Returns:
        Parsed settings; empty when the file or the table is absent.
    """
    if not path.is_file():
        return {}
    table = tomllib.loads(path.read_text()).get("tool", {}).get("rhiza-task", {})
    return {k.replace("-", "_"): tuple(v) if isinstance(v, list) else v for k, v in table.items()}


def _from_environ(environ: Mapping[str, str]) -> dict[str, Any]:
    """Read settings from the process environment.

    Both ``RHIZA_CI_OS_MATRIX`` and bare ``SOURCE_FOLDER`` are accepted, because the
    reusable workflows currently pass bare make-style names on the command line and those
    jobs must keep working through the transition.

    **An empty value counts as unset.** This is not a nicety, it is the make semantics
    this layer replaces. rhiza_ci.yml's ``generate-matrix`` job exports

        RHIZA_CI_OS_MATRIX: ${{ github.repository == 'jebel-quant/rhiza'
                               && '["ubuntu-latest","macos-latest"]' || '' }}

    -- one variable, set for the mother repo and *deliberately empty* for every consumer,
    whose own ``.rhiza/.env`` is then meant to answer. make's ``?=`` treats an exported
    empty string as set, which is why the retired ``ci-os-matrix`` recipe resolved through
    ``$(or ...)``; dropping empties here is the same rule, applied one layer earlier so
    every setting gets it rather than only the one whose recipe remembered to ask.

    Args:
        environ: The environment mapping.

    Returns:
        Parsed settings.

    """
    settings = ((_key(k), v) for k, v in environ.items())
    return {k: _coerce(v) for k, v in settings if k in _FIELD_NAMES and v.strip()}


def _key(name: str) -> str:
    """Normalise a make-style variable name to a field name.

    The ``RHIZA_`` prefix is optional, so it is stripped -- but only when what remains is
    actually a field. Stripping unconditionally made ``RHIZA_CHECKS`` resolve to the
    unknown field ``checks``, so the setting was silently dropped and ``rhiza_checks`` was
    reachable from the environment only as ``RHIZA_RHIZA_CHECKS``. Trying the whole name
    as a fallback fixes that without disturbing the fields whose prefix *is* redundant:
    ``RHIZA_CI_OS_MATRIX`` still resolves to ``ci_os_matrix``, and the doubled spelling
    keeps working for anyone who found it.

    Args:
        name: e.g. ``RHIZA_CI_OS_MATRIX``, ``SOURCE_FOLDER`` or ``rhiza-checks``.

    Returns:
        e.g. ``ci_os_matrix``, ``source_folder``, ``rhiza_checks``.
    """
    lowered = name.lower().replace("-", "_")
    stripped = lowered.removeprefix("rhiza_")
    if stripped in _FIELD_NAMES or lowered not in _FIELD_NAMES:
        return stripped
    return lowered


def _coerce(value: str) -> Any:
    """Turn a string setting into the field's type.

    Three shapes exist in ``.rhiza/.env`` today and all must keep parsing: a JSON array
    (``RHIZA_CI_OS_MATRIX=["ubuntu-latest","macos-latest"]``), a semicolon-separated list
    (``LICENSE_FAIL_ON=GPL;LGPL;AGPL``), and a plain scalar.

    Args:
        value: The raw string.

    Returns:
        A ``str``, ``int``, ``bool`` or ``tuple[str, ...]``.
    """
    value = value.strip()
    if value.startswith("["):
        return tuple(json.loads(value))
    if ";" in value:
        return tuple(p for p in value.split(";") if p)
    if value.lower() in {"true", "false"}:
        return value.lower() == "true"
    if value.lstrip("-").isdigit():
        return int(value)
    return value
