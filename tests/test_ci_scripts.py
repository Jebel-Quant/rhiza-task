"""The Python under ``.github/scripts/``, which this repo's own gates do not otherwise reach.

``typecheck`` runs ``ty``/``mypy`` over ``src``, ``security`` runs bandit over ``src``, and the
coverage floor measures ``--cov=src``. So the complexity-ceiling script added by #156 -- the
one *enforcing* a gate -- sat outside all three, and a planted ``def broken(x: int) -> str:
return x`` passed every one of them. Only ruff saw the file, and ruff does not typecheck. See
issue #161.

The half that is a *class* of problem is fixed in ``ci.yml``, which now runs ``mypy --strict``
over the directory, so a second script inherits the check rather than the hole. This file is
the other half: the logic itself, asserted rather than verified once by hand at the moment it
landed.

Loaded by path rather than imported, because ``.github/scripts`` is not a package and must not
become one -- a ``__init__.py`` there would be a package GitHub Actions has no use for, and the
directory's whole point is that it holds scripts rather than library code.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / ".github" / "scripts" / "accumulation_ceiling.py"


def load(path: Path) -> ModuleType:
    """Import a module from a path outside the package tree.

    Args:
        path: The script to load.

    Returns:
        The imported module.
    """
    spec = importlib.util.spec_from_file_location(path.stem, path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[path.stem] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def ceiling() -> ModuleType:
    """Load the accumulation-ceiling script.

    Returns:
        The loaded module.
    """
    return load(SCRIPT)


def block(complexity: int) -> dict[str, object]:
    """Build one radon block entry.

    Args:
        complexity: The block's cyclomatic complexity.

    Returns:
        The subset of radon's block shape the script reads.
    """
    return {"name": f"b{complexity}", "complexity": complexity}


class TestAccumulationCeiling:
    """``over_ceiling``: which modules hold too many complex blocks."""

    def test_a_module_at_the_ceiling_is_not_over_it(self, ceiling: ModuleType) -> None:
        """The comparison is strict, so the ceiling itself is allowed.

        The off-by-one that would make this gate fire one block early, or one block late,
        and which the manual run at the time could not distinguish -- it only ever saw a
        repository comfortably under the limit.

        Args:
            ceiling: The loaded script.
        """
        exactly = {"m.py": [block(ceiling.HARD) for _ in range(ceiling.CEILING)]}
        assert ceiling.over_ceiling(exactly) == {}

        one_more = {"m.py": [block(ceiling.HARD) for _ in range(ceiling.CEILING + 1)]}
        assert ceiling.over_ceiling(one_more) == {"m.py": ceiling.CEILING + 1}

    def test_blocks_below_the_rank_boundary_do_not_count(self, ceiling: ModuleType) -> None:
        """A module of trivial blocks is not complex, however many of them there are.

        This is the property that makes the metric prose-insensitive *and* size-insensitive:
        adding twenty getters to a module must not push it toward the ceiling.

        Args:
            ceiling: The loaded script.
        """
        trivial = {"m.py": [block(ceiling.HARD - 1) for _ in range(ceiling.CEILING * 4)]}
        assert ceiling.over_ceiling(trivial) == {}

    def test_a_module_radon_could_not_parse_is_skipped(self, ceiling: ModuleType) -> None:
        """Radon reports an unparseable file as a dict, not a list of blocks.

        Skipped rather than crashed *and* rather than counted: a syntax error is ruff's
        finding, and a second gate answering for the first reports one fact twice.

        Args:
            ceiling: The loaded script.
        """
        broken = {"bad.py": {"error": "invalid syntax"}, "m.py": [block(ceiling.HARD)]}
        assert ceiling.over_ceiling(broken) == {}

    def test_every_module_over_the_ceiling_is_named(self, ceiling: ModuleType) -> None:
        """Reporting only the worst would hide the rest behind one fix.

        Args:
            ceiling: The loaded script.
        """
        over = [block(ceiling.HARD)] * (ceiling.CEILING + 1)
        report = {"a.py": over, "b.py": over, "fine.py": [block(1)]}
        assert set(ceiling.over_ceiling(report)) == {"a.py", "b.py"}

    def test_main_exits_non_zero_only_when_a_module_is_over(self, ceiling: ModuleType, tmp_path: Path) -> None:
        """The exit status is the gate; radon has none of its own.

        Args:
            ceiling: The loaded script.
            tmp_path: pytest's temporary directory.
        """
        clean = tmp_path / "clean.json"
        clean.write_text('{"m.py": [{"name": "b", "complexity": 1}]}')
        assert ceiling.main(["_", str(clean)]) == 0

        dirty = tmp_path / "dirty.json"
        blocks = ", ".join(['{"name": "b", "complexity": 9}'] * (ceiling.CEILING + 1))
        dirty.write_text(f'{{"m.py": [{blocks}]}}')
        assert ceiling.main(["_", str(dirty)]) == 1

    def test_this_repository_is_under_its_own_ceiling(self, ceiling: ModuleType) -> None:
        """A sanity check on the constants, not a second complexity gate.

        ``CEILING`` sits one above the current worst module. If a refactor ever set it below
        what ``src/`` already carries, every CI run would fail and this test says which end
        of the comparison was wrong.

        Args:
            ceiling: The loaded script.
        """
        assert ceiling.HARD >= 1
        assert ceiling.CEILING >= 1
