# Contributing

Two facts first, because both contradict what a Python repository normally looks like and
both cost time to rediscover.

## There is no `Makefile`

Run the gates through the CLI this package ships:

```bash
uv run rhiza-task list          # the whole registry, with prerequisites
uv run rhiza-task all           # every gate, exactly as CI runs them
uv run rhiza-task <task>        # one gate
```

`make test` will tell you there is no such file, and tools that probe for `make` will report
every gate unavailable. That is this repository's shape, not a broken checkout: this package
*is* the replacement for the template's make layer, so a repo consuming the template would
run its gates through a published copy of this code rather than the working tree — which is
circular. [`CLAUDE.md`](CLAUDE.md) has the full argument under *This repository is not
rhiza-managed*.

`uv run rhiza-task all` is the same entry point `ci.yml`'s `gates` job uses, so a green run
locally means the same thing it means on CI.

Two gates sit deliberately **outside** `all` and are named separately by CI —
`rhiza-task complexity` and `rhiza-task docs-examples`. A third, `rhiza-task book-nav`, runs
only on the branch that publishes the book. Running `all` alone will not exercise any of
them.

## Getting set up

```bash
uv sync --all-extras --all-groups     # or: uv run rhiza-task install
uv run rhiza-task all
```

Nothing else is required. There is no `.rhiza/` directory to sync, no secrets, and no
private index — `ci.yml` uses none either.

## What the gates will hold you to

Most of it is enforced, so this is a short list of what to expect rather than a set of rules
to remember:

| Expectation | Enforced by |
|---|---|
| 100% line coverage | `rhiza-task test`, `coverage_fail_under = 100` |
| 100% docstring coverage over `src/` **and** `tests/`, with an `Args:` per parameter | `rhiza-task docs-coverage` |
| No block above cyclomatic complexity 15 | `rhiza-task complexity` |
| `ruff` clean, formatted; markdown, shell and workflows linted | `rhiza-task fmt` |
| `ty` **and** `mypy --strict` clean | `rhiza-task typecheck` |
| Every fenced example under `docs/` parses, and `result` blocks match | `rhiza-task docs-examples` |

The coverage floor is load-bearing rather than decorative: it is what justifies the
test-layout opt-out in `pyproject.toml`, so **lowering it invalidates that opt-out**. The
two move together or neither moves.

## Two invariants no gate checks

These are the ones a newcomer breaks first, which is why they are here rather than left to
the tooling.

**The import graph is strictly layered**, and nothing enforces it:

```text
config, spec  →  uv  →  tasks/*  →  runner  →  cli
```

A lower layer never imports an upper one; there are no cycles; there are no function-local
(deferred) imports; and no underscore-prefixed name crosses a module boundary. Check all
four in two commands:

```bash
grep -rnE '^\s+(from|import) ' src/                          # deferred imports
grep -rnE 'from \.{1,2}[a-z_.]* import .*\b_[a-z]' src/      # private cross-module imports
```

The first returns **two** lines, of which only one is an import — `spec.py`'s
`TYPE_CHECKING` guard, which the rules permit. The other is prose in a `config.py` docstring
that happens to begin with the word "from", so it is a false positive of matching text
rather than syntax. The second should return nothing.

**No test runs `uv`, or any other tool.** Every test patches `rhiza_task.uv`'s entry points
through the `Recorder` fixture in `tests/conftest.py` and asserts on the argument vector that
*would* have run. That is the point of the package rather than a shortcut: the make recipes
expressed their contract as shell, and the vectors are that contract made assertable without
provisioning a toolchain. So a new task's test asserts *what it would run*.

If you find yourself wanting a real subprocess in a test, that is the signal that the logic
belongs in a task body instead — which is why `docs-examples` is a task and not a test.

## House style

The comments in this repository are unusually dense, and that is the convention. The rule
they follow: **say why, especially when the code looks wrong.** A flat `if ... raise`
sequence that radon scores C, a `# nosec`, a version pinned one patch above upstream — each
carries the bug it prevents or the decision it records. When you change such code, change the
comment with it; a stale comment is worse than none.

One placement rule that is easy to get wrong: a suppression comment's reason goes *above* the
line, never after the marker, because bandit reads everything following `# nosec` as a list
of test IDs.

## Sending a change

Branch, commit, open a PR against `main`. `ci.yml` runs on every pull request; `rhiza-task
all` locally is the fastest way to know in advance what it will say.

[`CLAUDE.md`](CLAUDE.md) is the deeper reference — it records the conventions held by
discipline rather than by a gate, and the reasoning behind the decisions above. It is written
for anyone changing this repository, human or agent.
