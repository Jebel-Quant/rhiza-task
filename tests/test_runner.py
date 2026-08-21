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
    # The failure's own 2, not the blocked dependent's nothing: see the test below.
    assert state.exit_code() == 2


def test_a_failing_task_s_own_exit_status_is_what_the_run_exits_with(cfg: Config) -> None:
    """A failing task's own 2 survives to the caller, rather than collapsing to a flat 1.

    This is the point of :attr:`~rhiza_task.runner.Result.code`: a consumer's CI can tell
    "tests failed" (pytest 1) from "collection errored" (2) or "usage error" (4), which is
    what :class:`~rhiza_task.spec.Failed` has always carried and nothing used to read.

    Args:
        cfg: The resolved config.
    """

    @task("t-pytest-2", "fails the way pytest fails", section="Test")
    def collection_error(cfg: Config) -> None:
        """Fail with pytest's collection-error status.

        Args:
            cfg: Unused.

        Raises:
            Failed: Always, with code 2.
        """
        raise Failed(2, "tests failed")

    @task("t-guard-verdict", "fails on its own verdict", section="Test")
    def guard_verdict(cfg: Config) -> None:
        """Fail without a child process behind it.

        Args:
            cfg: Unused.

        Raises:
            Failed: Always, with code 1.
        """
        raise Failed(1, "coverage 87.0% is below the 100% floor")

    @task("t-killed", "reports a signal, not a status", section="Test")
    def killed(cfg: Config) -> None:
        """Fail with what ``subprocess`` reports for a child killed by SIGKILL.

        Args:
            cfg: Unused.

        Raises:
            Failed: Always, with an unusable code.
        """
        raise Failed(-9, "killed")

    assert run(["t-pytest-2"], cfg).exit_code() == 2
    assert run(["t-guard-verdict"], cfg).exit_code() == 1
    # -9 is not a status a shell can report; 1 is the honest floor.
    assert run(["t-killed"], cfg).exit_code() == 1

    # The *first* real failure speaks, so a later gate's code cannot mask it.
    both = run(["t-pytest-2", "t-guard-verdict"], cfg)
    assert both.exit_code() == 2


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


def test_a_skipped_prerequisite_does_not_block_its_dependent(repo: Config) -> None:
    """A prerequisite with nothing to measure lets the dependent run; a failed one does not.

    The distinction `book` rests on. It names five prerequisites, three of which guard on
    folders most repositories do not have -- benchmarks, stress tests, a paper -- so if a
    skip blocked a dependent, `book` would be unbuildable almost everywhere. Only FAILED and
    BLOCKED propagate.

    Under ``--strict`` the skip becomes a failure and *does* block, which is the same test
    read the other way and is asserted here so the interaction cannot regress silently.

    Args:
        repo: The throwaway repository root.
    """

    @task("t-absent", "nothing to measure", section="Test", guards=(Guard("marimo_folder"),))
    def absent(cfg: Config) -> None:
        """Do nothing; the guard skips first.

        Args:
            cfg: Unused.
        """

    @task("t-dependent", "needs the skipper", section="Test", needs=("t-absent",))
    def dependent(cfg: Config) -> None:
        """Run, despite the prerequisite having skipped.

        Args:
            cfg: Unused.
        """

    lenient = run(["t-dependent"], Config.load(root=repo))
    assert lenient.status_of("t-absent") == Status.SKIPPED
    assert lenient.status_of("t-dependent") == Status.OK
    assert lenient.exit_code() == 0

    strict = run(["t-dependent"], Config.load(root=repo, strict=True))
    assert strict.status_of("t-absent") == Status.FAILED
    assert strict.status_of("t-dependent") == Status.BLOCKED


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


def test_exit_code_propagates_the_failing_tasks_own_code(cfg: Config) -> None:
    """A failing task's own exit code survives into the aggregate.

    :class:`~rhiza_task.spec.Failed` carries e.g. pytest's 3 or 5, and :meth:`Run.exit_code`
    hands the first real failure's code back rather than flattening it, so a caller can
    still tell "no tests collected" from "a gate exited 1".

    Args:
        cfg: The resolved config.
    """

    @task("t-exits-5", "exits with a non-1 code", section="Test")
    def exits_5(cfg: Config) -> None:
        """Fail with exit code 5 (pytest's "no tests collected").

        Args:
            cfg: Unused.

        Raises:
            Failed: Always, with code 5.
        """
        raise Failed(5, "no tests collected")

    state = run(["t-exits-5"], cfg)
    assert state.status_of("t-exits-5") == Status.FAILED
    assert state.exit_code() == 5


def test_exit_code_collapses_a_code_no_shell_can_carry(cfg: Config) -> None:
    """A code outside 1-255 becomes 1, because ``exit`` cannot be handed it as-is.

    ``subprocess`` reports a child killed by a signal as the negative signal number, which
    is the shape that reaches here in practice.

    Args:
        cfg: The resolved config.
    """

    @task("t-killed", "exits with a signal number", section="Test")
    def killed(cfg: Config) -> None:
        """Fail the way a SIGKILLed child does.

        Args:
            cfg: Unused.

        Raises:
            Failed: Always, with code -9.
        """
        raise Failed(-9, "killed")

    state = run(["t-killed"], cfg)
    assert state.status_of("t-killed") == Status.FAILED
    assert state.exit_code() == 1


def test_blocked_status_produces_exit_code_one(cfg: Config) -> None:
    """A task blocked by a failed prerequisite contributes to a non-zero exit code.

    Args:
        cfg: The resolved config.
    """

    @task("t-prereq-fail", "fails", section="Test")
    def prereq_fail(cfg: Config) -> None:
        """Fail.

        Args:
            cfg: Unused.

        Raises:
            Failed: Always.
        """
        raise Failed(1, "boom")

    @task("t-blocked-task", "depends on failure", section="Test", needs=("t-prereq-fail",))
    def blocked_task(cfg: Config) -> None:
        """Should not run.

        Args:
            cfg: Unused.
        """

    state = run(["t-blocked-task"], cfg)
    assert state.status_of("t-blocked-task") == Status.BLOCKED
    assert state.failed
    assert state.exit_code() == 1


def test_skipped_status_produces_exit_code_zero(cfg: Config) -> None:
    """A skipped task (lenient mode) does not cause a non-zero exit code.

    Args:
        cfg: The resolved config.
    """
    from rhiza_task.spec import Guard

    @task("t-skipped-task", "skips", section="Test", guards=(Guard("marimo_folder"),))
    def skipped_task(cfg: Config) -> None:
        """Should be skipped by the guard.

        Args:
            cfg: Unused.
        """

    state = run(["t-skipped-task"], cfg)
    assert state.status_of("t-skipped-task") == Status.SKIPPED
    assert not state.failed
    assert state.exit_code() == 0
