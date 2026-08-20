"""Prerequisite resolution, guard evaluation, and outcome bookkeeping.

Small on purpose. make gave four behaviours for free, and this module is what buys them
back; nothing else belongs here.

1. **Dedup within one invocation.** Eleven tasks name ``install`` as a prerequisite and
   ``all`` names eight of those. Without a seen-set, ``rhiza-task all`` would sync the
   environment eight times.
2. **Depth-first ordering.** ``book`` needs ``test``, which needs ``install``.
3. **A failed prerequisite stops its dependents.** As make does, rather than running a
   gate against a half-built environment.
4. **A missing prerequisite is not an error.** book.mk declares ``test:: ; @:`` no-op
   stubs so ``book`` can depend on gates that may not have been synced; here a
   prerequisite absent from the registry is simply not run, and the stubs are gone.

Every name goes through :func:`~rhiza_task.spec.lookup` rather than a dict subscript, so
``test`` means pytest in a Python repository and ``cargo nextest`` in a crate. That is the
question the make layer answered by syncing exactly one language fragment.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from .config import Config
from .spec import Failed, Skip, lookup


class Status(StrEnum):
    """The four outcomes a task can have."""

    OK = "ok"
    SKIPPED = "skipped"
    FAILED = "failed"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class Result:
    """What happened to one task.

    Attributes:
        name: The task name.
        status: Its outcome.
        detail: Why, for anything other than :attr:`Status.OK`.
    """

    name: str
    status: Status
    detail: str = ""


@dataclass
class Run:
    """One invocation: the results so far, and the tasks already attempted."""

    results: list[Result] = field(default_factory=list)
    seen: set[str] = field(default_factory=set)

    @property
    def failed(self) -> bool:
        """Whether any task failed or was blocked.

        Returns:
            True when the invocation should exit non-zero.
        """
        return any(r.status in {Status.FAILED, Status.BLOCKED} for r in self.results)

    def status_of(self, name: str) -> Status | None:
        """Return the recorded status of a task, if it ran.

        Args:
            name: Task name.

        Returns:
            The status, or None when the task was not attempted.
        """
        return next((r.status for r in self.results if r.name == name), None)

    def exit_code(self) -> int:
        """Return the aggregate exit status: 0 when nothing failed or was blocked, else 1.

        Uniformly 1, and the example below is what says so. :class:`~rhiza_task.spec.Failed`
        carries the failing process's own code, but :class:`Result` does not record it, so
        nothing here *can* propagate e.g. pytest's 3 or 4 -- and this docstring claimed it
        did until a doctest was put under it. Whether the code should be carried through
        is issue #33; until it is decided, the behaviour and its description agree.

        Returns:
            0 when nothing failed or was blocked, else 1.

        Examples:
            An empty run, and a run whose only entry is a skip, both succeed -- a skip is
            an outcome, not a failure, and ``--strict`` is the switch that changes that:

            >>> state = Run()
            >>> state.exit_code()
            0
            >>> state.results.append(Result("fmt", Status.SKIPPED, "no .pre-commit-config.yaml"))
            >>> state.failed, state.exit_code()
            (False, 0)

            A failure, and the dependent it blocks, are both non-zero -- and both collapse
            to 1 rather than to pytest's own status:

            >>> state.results.append(Result("test", Status.FAILED, "tests failed"))
            >>> state.results.append(Result("book", Status.BLOCKED, "prerequisite failed: test"))
            >>> state.failed, state.exit_code()
            (True, 1)
            >>> state.status_of("book") is Status.BLOCKED
            True
            >>> state.status_of("todos") is None
            True
        """
        return 1 if self.failed else 0


def run(names: list[str], cfg: Config) -> Run:
    """Run the named tasks and their prerequisites, in order.

    Args:
        names: Task names, as typed on the command line.
        cfg: The resolved config.

    Returns:
        The completed :class:`Run`.

    Raises:
        KeyError: When an explicitly requested task does not exist *in this repository's
            layers*. Only for requested names -- an unknown prerequisite is skipped,
            whereas an unknown request is a typo and should say so.
    """
    unknown = [n for n in names if lookup(n, cfg.layers) is None]
    if unknown:
        msg = f"unknown task{'s' if len(unknown) > 1 else ''}: {', '.join(unknown)}"
        raise KeyError(msg)

    run_state = Run()
    for name in names:
        _run_one(name, cfg, run_state)
    return run_state


# radon scores this function C (12), which is one branch per *outcome* a task can have:
# already seen, unknown, blocked by a prerequisite, skipped, skipped under --strict,
# failed, ok. Each writes exactly one line of the run summary, so the branch count is the
# size of :class:`Status` plus the two early returns -- flat dispatch over a closed set,
# and deliberate. Splitting it would put the outcomes in two places.
def _run_one(name: str, cfg: Config, state: Run) -> None:
    """Run one task after its prerequisites, recording the outcome.

    Args:
        name: Task name.
        cfg: The resolved config.
        state: The invocation state, appended to in place.
    """
    spec = lookup(name, cfg.layers)
    # The registry key, not the requested name: `rust:test` and `test` are one task in a
    # crate, and a run that named both would otherwise run it twice.
    if spec is None or spec.key in state.seen:
        return
    state.seen.add(spec.key)

    for need in spec.needs:
        _run_one(need, cfg, state)
    blocked = [n for n in spec.needs if state.status_of(n) in {Status.FAILED, Status.BLOCKED}]
    if blocked:
        state.results.append(Result(spec.name, Status.BLOCKED, f"prerequisite failed: {', '.join(blocked)}"))
        return

    try:
        for guard in spec.guards:
            guard.check(cfg.root, cfg.folders)
        spec.run(cfg)
    except Skip as exc:
        # The strict switch is the whole reason Skip is a distinct outcome rather than a
        # warning printed on the way to exit 0.
        if cfg.strict:
            state.results.append(Result(spec.name, Status.FAILED, f"skipped under --strict: {exc}"))
        else:
            state.results.append(Result(spec.name, Status.SKIPPED, str(exc)))
    except Failed as exc:
        state.results.append(Result(spec.name, Status.FAILED, str(exc)))
    else:
        state.results.append(Result(spec.name, Status.OK))
