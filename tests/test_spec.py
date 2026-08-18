"""The task model: guards, and the registry that replaced double-colon rules."""

from __future__ import annotations

from pathlib import Path

import pytest

from rhiza_task.config import Config
from rhiza_task.spec import REGISTRY, Guard, Skip, Task, task


def test_guard_passes_when_folder_exists(cfg: Config) -> None:
    """A satisfied guard raises nothing.

    Args:
        cfg: Config for a repository that has ``src``.
    """
    Guard("source_folder").check(cfg.root, cfg.folders)


def test_guard_skips_on_missing_folder(cfg: Config) -> None:
    """A missing folder is a skip, naming the setting rather than the path.

    Args:
        cfg: Config for the throwaway repository.
    """
    with pytest.raises(Skip, match="marimo_folder"):
        Guard("marimo_folder").check(cfg.root, cfg.folders)


def test_guard_skips_when_glob_matches_nothing(tmp_path: Path) -> None:
    """An existing but empty folder still skips when a glob is required.

    This is the declarative form of python.mk's ``find ${TESTS_FOLDER} -name 'test_*.py'``,
    and the distinction matters: an empty ``tests/`` is the case that used to make ``test``
    pass green over nothing.

    Args:
        tmp_path: The repository root.
    """
    (tmp_path / "tests").mkdir()
    cfg = Config.load(root=tmp_path)
    with pytest.raises(Skip):
        Guard("tests_folder", glob="test_*.py").check(cfg.root, cfg.folders)


def test_guard_finds_nested_matches(cfg: Config) -> None:
    """The glob is recursive, so a test in a subdirectory counts.

    Args:
        cfg: Config for the throwaway repository.
    """
    nested = cfg.path("tests_folder") / "unit"
    nested.mkdir()
    (nested / "test_nested.py").touch()
    Guard("tests_folder", glob="test_*.py").check(cfg.root, cfg.folders)


def test_task_decorator_registers_and_returns_the_function() -> None:
    """The decorator registers a :class:`Task` and hands back the undecorated callable.

    Returning the function unchanged is what keeps every task body unit-testable without
    going through the registry.
    """

    @task("demo", "a demo task", section="Test", needs=("install",))
    def demo(cfg: Config) -> None:
        """Do nothing.

        Args:
            cfg: Unused.
        """

    assert isinstance(REGISTRY["demo"], Task)
    assert REGISTRY["demo"].needs == ("install",)
    assert demo.__name__ == "demo"


def test_every_builtin_task_is_documented() -> None:
    """Each registered task carries help text and a section.

    rhiza.mk enforced this with an awk convention that silently omitted anything missing a
    ``##`` comment; here it is asserted.
    """
    for name, spec in REGISTRY.items():
        assert spec.help, f"{name} has no help text"
        assert spec.section, f"{name} has no section"


def test_prerequisites_that_exist_are_registered_tasks() -> None:
    """Every prerequisite is either a real task or deliberately optional.

    ``all`` and ``book`` both name gates that live in optional task modules. This asserts
    the *spelling* is right -- a typo'd prerequisite would otherwise be silently skipped by
    the runner, which is the price of tolerating absent ones.
    """
    for spec in REGISTRY.values():
        for need in spec.needs:
            assert need in REGISTRY, f"{spec.name} needs unknown task {need!r}"
