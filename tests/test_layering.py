r"""Layering rule 4, asserted over the tree rather than by a grep someone has to run.

Rule 4 says no underscore-prefixed name crosses a module boundary, and CLAUDE.md says it
holds in ``tests/`` as well as ``src/``, because its reason is about callers rather than
layers. CLAUDE.md used to check it with a grep, ``from \.{1,2}[a-z_.]* import .*\b_[a-z]``.
That needs a leading dot, so it matched only *relative* imports. It caught #118's
``from .conftest import _task_modules``, but it could not see how a test normally reaches
``src/``, which is an absolute ``from rhiza_task.config import _coerce``. Two such imports
went unnoticed from the initial import until #224.

So this reads the AST, as ``test_uv.py`` does for process starters. A grep cannot tell an
import from a docstring that mentions one, and it has to be told every spelling in advance.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

SCANNED = ("src", "tests")
"""Both trees rule 4 covers. ``src/`` imports relatively, ``tests/`` mostly absolutely."""


def is_internal(node: ast.ImportFrom) -> bool:
    """Say whether an import reads from this package or from a sibling test module.

    Rule 4 is about this package's own boundaries. A third-party private name is that
    project's business, so ``from _pytest…`` and the like are left alone.

    Args:
        node: The ``from … import …`` statement.

    Returns:
        True for a relative import, or an absolute one from ``rhiza_task`` or ``tests``.
    """
    module = node.module or ""
    return node.level > 0 or module.split(".")[0] in {"rhiza_task", "tests"}


def private_imports(source: str, filename: str) -> list[str]:
    """List every internal import of an underscore-prefixed name in one module.

    Dunder names such as ``__version__`` are public by convention and are not counted.

    Args:
        source: The module's text.
        filename: Used only to label each offender.

    Returns:
        One ``file:line: from module import name`` entry per offending name.
    """
    return [
        f"{filename}:{node.lineno}: from {'.' * node.level}{node.module or ''} import {alias.name}"
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.ImportFrom) and is_internal(node)
        for alias in node.names
        if alias.name.startswith("_") and not alias.name.startswith("__")
    ]


def test_no_private_name_crosses_a_module_boundary() -> None:
    """No module in ``src/`` or ``tests/`` imports another module's private name.

    If this fails, promote the name and document why it is public, or test it through the
    public path that calls it. Do not add an exemption here.
    """
    offenders = [
        hit
        for root in SCANNED
        for path in sorted(Path(root).rglob("*.py"))
        for hit in private_imports(path.read_text(encoding="utf-8"), str(path))
    ]
    assert offenders == [], f"private names imported across a module boundary: {offenders}"


@pytest.mark.parametrize(
    ("source", "caught"),
    [
        ("from rhiza_task.config import Config, _coerce", True),
        ("from .conftest import Recorder, _task_modules", True),
        ("from ..spec import _clauses", True),
        ("from tests.conftest import _helper", True),
        ("from rhiza_task.config import Config", False),
        ("from rhiza_task import __version__", False),
        ("from _pytest.monkeypatch import MonkeyPatch", False),
        ("from hypothesis import _private", False),
        ('"""from rhiza_task.config import _coerce"""', False),
    ],
)
def test_the_check_sees_both_spellings(source: str, caught: bool) -> None:
    """The scan above reports nothing, so show that it would have reported the real cases.

    The first two rows are the two violations this repository actually had: the absolute one
    the grep missed (#224) and the relative one it caught (#118). The last row is the reason
    for reading the AST: a docstring that quotes an import is not an import.

    Args:
        source: One line of Python.
        caught: Whether it should be reported.
    """
    assert bool(private_imports(source, "<snippet>")) is caught
