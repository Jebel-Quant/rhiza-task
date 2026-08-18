"""The four behaviours make gave for free, asserted directly.

Each test here corresponds to a numbered claim in :mod:`rhiza_task.runner`'s docstring.
"""

from __future__ import annotations

import pytest

from rhiza_task.config import Config
from rhiza_task.runner import Status, run
from rhiza_task.spec import Failed, Guard, Skip, task


@pytest.fixture
def graph() -> None:
    """Register a small task graph over the built-ins, recording execution order.

    Returns:
        None; the tasks append to ``ORDER``.
    """
    ORDER.clear()

    @task("t-base", "base", section="Test")
    def base(cfg: Config) -> None:
        """Record a run.

        Args:
            cfg: Unused.
        """
        ORDER.append("t-base")

    @task("t-left", "left", section="Test", needs=("t-base",))
    def left(cfg: Config) -> None:
        """Record a run.

        Args:
            cfg: Unused.
        """
        ORDER.append("t-left")

    @task("t-right", "right", section="Test", needs=("t-base",))
    def right(cfg: Config) -> None:
        """Record a run.

        Args:
            cfg: Unused.
        """
        ORDER.append("t-right")

    @task("t-top", "top", section="Test", needs=("t-left", "t-right"))
    def top(cfg: Config) -> None:
        """Record a run.

        Args:
            cfg: Unused.
        """
        ORDER.append("t-top")


ORDER: list[str] = []


def test_shared_prerequisite_runs_once(cfg: Config, graph: None) -> None:
    """A diamond runs its base exactly once.

    Without this, ``rhiza-task all`` would sync the environment eight times: eleven tasks
    name ``install`` and ``all`` names eight of them.

    Args:
        cfg: The resolved config.
        graph: The registered task graph.
    """
    state = run(["t-top"], cfg)
    assert ORDER.count("t-base") == 1
    assert [r.name for r in state.results] == ["t-base", "t-left", "t-right", "t-top"]


def test_prerequisites_run_depth_first(cfg: Config, graph: None) -> None:
    """A prerequisite completes before the task naming it.

    Args:
        cfg: The resolved config.
        graph: The registered task graph.
    """
    run(["t-top"], cfg)
    assert ORDER.index("t-base") < ORDER.index("t-left") < ORDER.index("t-top")


def test_missing_prerequisite_is_not_an_error(cfg: Config) -> None:
    """A prerequisite absent from the registry is simply not run.

    This is what removes book.mk's four ``test:: ; @:`` no-op stubs, and with them the
    entire double-colon mechanism.

    Args:
        cfg: The resolved config.
    """

    @task("t-needs-ghost", "names a task that does not exist", section="Test", needs=("t-ghost",))
    def needs_ghost(cfg: Config) -> None:
        """Do nothing.

        Args:
            cfg: Unused.
        """

    state = run(["t-needs-ghost"], cfg)
    assert state.status_of("t-needs-ghost") == Status.OK
    assert state.status_of("t-ghost") is None
    assert not state.failed


def test_failed_prerequisite_blocks_its_dependents(cfg: Config) -> None:
    """A dependent does not run against a half-built environment.

    Args:
        cfg: The resolved config.
    """

    @task("t-broken", "always fails", section="Test")
    def broken(cfg: Config) -> None:
        """Fail.

        Args:
            cfg: Unused.

        Raises:
            Failed: Always.
        """
        raise Failed(2, "boom")

    @task("t-dependent", "depends on the failure", section="Test", needs=("t-broken",))
    def dependent(cfg: Config) -> None:
        """Record that it should not have run.

        Args:
            cfg: Unused.
        """
        ORDER.append("t-dependent")

    ORDER.clear()
    state = run(["t-dependent"], cfg)
    assert state.status_of("t-broken") == Status.FAILED
    assert state.status_of("t-dependent") == Status.BLOCKED
    assert "t-dependent" not in ORDER
    assert state.exit_code() == 1


def test_skip_is_green_by_default_and_red_under_strict(repo: Config) -> None:
    """A gate with nothing to measure skips, unless ``--strict`` says otherwise.

    The make layer only had the first half of this, which is how a consumer who excluded
    ``.rhiza/tests`` got "a green gate measuring nothing".

    Args:
        repo: The throwaway repository root.
    """

    @task("t-nothing", "nothing to do", section="Test", guards=(Guard("marimo_folder"),))
    def nothing(cfg: Config) -> None:
        """Do nothing; the guard skips first.

        Args:
            cfg: Unused.
        """

    lenient = run(["t-nothing"], Config.load(root=repo))
    assert lenient.status_of("t-nothing") == Status.SKIPPED
    assert lenient.exit_code() == 0

    strict = run(["t-nothing"], Config.load(root=repo, strict=True))
    assert strict.status_of("t-nothing") == Status.FAILED
    assert strict.exit_code() == 1


def test_a_task_body_may_skip_itself(cfg: Config) -> None:
    """:class:`Skip` raised from the body is the same outcome as a failed guard.

    Args:
        cfg: The resolved config.
    """

    @task("t-self-skip", "skips itself", section="Test")
    def self_skip(cfg: Config) -> None:
        """Skip.

        Args:
            cfg: Unused.

        Raises:
            Skip: Always.
        """
        raise Skip("nothing here")

    assert run(["t-self-skip"], cfg).status_of("t-self-skip") == Status.SKIPPED


def test_unknown_requested_task_raises(cfg: Config) -> None:
    """An unknown *request* is a typo and says so, unlike an unknown prerequisite.

    Args:
        cfg: The resolved config.
    """
    with pytest.raises(KeyError, match="unknown task"):
        run(["definitely-not-a-task"], cfg)


def test_several_requested_tasks_run_in_order(cfg: Config, graph: None) -> None:
    """Requesting two tasks runs both, still deduping their shared prerequisite.

    Args:
        cfg: The resolved config.
        graph: The registered task graph.
    """
    state = run(["t-left", "t-right"], cfg)
    assert ORDER.count("t-base") == 1
    assert state.status_of("t-left") == Status.OK
    assert state.status_of("t-right") == Status.OK
