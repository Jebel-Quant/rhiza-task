# CLAUDE.md

Notes for anyone — human or agent — changing this repository. It records the conventions
that are held by discipline rather than by a gate, because those are the ones a newcomer
breaks first. Everything a gate already enforces is left to the gate.

`README.md` says what this package *is*; `docs/` explains how it works. This file is only
about working *on* it.

## This repository is not rhiza-managed

There is no `.rhiza/` directory. Nothing here is synced from `jebel-quant/rhiza`, no file
is template-owned, and **every file is locally owned and locally editable** — including
`.github/workflows/`, `.pre-commit-config.yaml`, `ruff.toml` and `pytest.ini`, which in a
managed repo would belong upstream.

That is deliberate, and it is not an oversight to correct. This package is the thing that
*replaces* the template's make layer: a repo that consumed the template would run its
gates through a published, pre-release copy of this code instead of the working tree, which
is circular. Two visible consequences, both intended:

- **There is no `Makefile`.** Run gates as `uv run rhiza-task <task>`; `uv run rhiza-task
  list` prints the registry. Tools that look for `make` will report every gate unavailable,
  which is a fact about this repo's shape, not a broken checkout.
- **`rhiza-task semgrep` skips**, because the rule file it needs is `.rhiza/semgrep.yml`.
  `weekly.yml` records why that job is not lifted from upstream.

`rhiza-test` and `test-pyproject` are *this repo's own* tasks running `pytest-rhiza`, and
are unrelated to the absent template. Do not read them as evidence of a sync.

## The layering invariant

The import graph is strictly layered, and nothing enforces it:

```
config, spec  →  uv  →  tasks/*  →  runner  →  cli
```

Four rules follow. All four currently hold, and each has been broken at least once:

1. **A lower layer never imports an upper one.** `config.py` imports nothing internal at
   all; `spec.py`'s single reference to `Config` is a forward one, `TYPE_CHECKING`-guarded
   with a comment saying why.
2. **No import cycles.**
3. **No function-local (deferred) imports.** A module-level cycle crashes and gets fixed; a
   deferred one survives for years. There are none — keep it that way rather than reaching
   for one to break a cycle you have just introduced.
4. **No underscore-prefixed name crosses a module boundary.** `cli.py` importing
   `config._key` was the one violation; it is now `Config.field_for`, public because two
   callers need the same spelling rule. If a private helper turns out to be needed
   elsewhere, promote it and document why it is public — do not import it as-is.

Within `tasks/`, siblings may share: `python.py`, `rust.py` and `go.py` all import
`install_hooks` from `tasks/quality.py`. That is one layer, not an inversion.

Check the whole invariant in one command:

```bash
grep -rnE '^\s+(from|import) ' src/          # deferred imports (expect only the guarded one)
grep -rnE 'from \.{1,2}[a-z_.]* import .*\b_[a-z]' src/   # private cross-module imports
```

## Tests assert argument vectors, and never run uv

**No test in this suite runs `uv`, or any other tool.** Every test patches
`rhiza_task.uv`'s entry points — `uv`, `uvx`, `uv_run`, `tool` — through the `Recorder`
fixture in `tests/conftest.py`, and asserts on the argument vector that *would* have been
executed.

This is the point of the package rather than a shortcut: the make recipes expressed their
contract as shell, and the vectors are that contract made assertable without provisioning a
toolchain. So a new task's test asserts *what it would run*, not that running it works.

If you find yourself wanting a real subprocess in a test, that is a signal the logic under
test belongs in a task body rather than in the plumbing.

The exception is the doctests: `Config.load`, `Config.field_for`, `Run.exit_code`,
`lookup` and `Guard.check` carry executable examples that do run. They are checked by
`rhiza-test`, so an example that goes stale fails a gate.

## The coverage floor is 100, and it is load-bearing

`[tool.rhiza-task] coverage_fail_under = 100` in `pyproject.toml`. `rhiza-task test` and
`rhiza-task coverage` both fail below it.

It is not a vanity number — **it is what justifies the test-layout opt-out.** The suite is
organised by *behaviour*, not as a 1:1 mirror of `src/`: `test_tasks.py`,
`test_bundle_tasks.py`, `test_dev_tasks.py` and `test_language_layers.py` cover the twelve
task modules as groups. `[tool.check_test_layout] enforce = false` declares that, and its
required `reason` argues that per-module coverage is guaranteed by the floor instead.

**So lowering the floor invalidates the opt-out.** The two move together, or neither moves.

Four lines are excluded by `# pragma: no cover`, each carrying its reason. Two are
structural; two are testable and retiring them would be a real improvement.

## Where the gates run

`ci.yml` has four jobs, and its comments explain each in detail. In short: a 12-cell
`pytest` matrix (3 OS × Python 3.11–3.14 — **Windows is deliberate**, as the assertion that
the shell dependency is gone), a `ruff` job, a `gates` job running the dogfood `uv run
rhiza-task all`, and a `lowest-deps` job that resolves `--resolution lowest-direct` to prove
the manifest's floors are real.

Two things follow for a change to `pyproject.toml`:

- **A declared floor is tested, so it is a claim.** Raising `typer>=0.15` needs a reason.
- **A new test-group dependency needs a floor that works at the floor.** `lowest-deps`
  installs the oldest version every range allows, together — so a dependency whose floor
  predates the resolved `pytest 8.0.0` will fail there and nowhere else.

`ruff` is pinned to the same version in `ci.yml` and `.pre-commit-config.yaml`, on purpose,
and dependabot moves neither. `.github/dependabot.yml` records at its foot which pins are
manual and why.

## House style

The comments here are unusually dense, and that is the convention rather than an accident.
The rule they follow: **say why, especially when the code looks wrong.** A flat
`if ... raise` sequence that radon scores C, a `--partial-match` flag, a `# nosec`, a
version pinned one patch above upstream — each carries the bug it prevents or the decision
it records. Four blocks rank C on cyclomatic complexity and each states why the flat form
is preferred over a decomposition that would satisfy the metric.

When changing such code, update the comment with it. A stale comment is worse than none:
`ci.yml` once asserted that `rhiza-task fmt` skipped for want of a pre-commit config, for
several commits after that config was added.

Docstring coverage is enforced at **100% over `src/` *and* `tests/`** — test functions and
fixtures need docstrings too, with an `Args:` section for every parameter.
