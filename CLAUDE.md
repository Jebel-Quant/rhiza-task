# CLAUDE.md

Notes for anyone — human or agent — changing this repository. It records the conventions
that are held by discipline rather than by a gate, because those are the ones a newcomer
breaks first. Everything a gate already enforces is left to the gate.

`README.md` says what this package *is*; `docs/` explains how it works. This file is only
about working *on* it.

## This repository is not rhiza-managed

There is no `.rhiza/` directory, no file is template-owned, and **every file is locally
owned and locally editable** — including `.github/workflows/`, `.pre-commit-config.yaml`,
`ruff.toml` and `pytest.ini`, which in a managed repo would belong upstream.

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

### The two workflows that do reach upstream

"Nothing is synced" is right; "nothing references `jebel-quant/rhiza`" would not be. Two
workflow files are thin wrappers that `uses:` a reusable workflow from the template repo,
and both still carry its `This file is part of the jebel-quant/rhiza repository` header:

| file | delegates to | pin |
|---|---|---|
| `.github/workflows/rhiza_codeql.yml` | `rhiza/.github/workflows/rhiza_codeql.yml` | `31e1a11` (v1.4.2) |
| `.github/workflows/rhiza_scorecard.yml` | `rhiza/.github/workflows/rhiza_scorecard.yml` | `31e1a11` (v1.4.2) |

They are the odd ones out and it is worth knowing why they are tolerated. `rhiza_book.yml`,
`rhiza_paper.yml` and `rhiza_release.yml` are *fully local* files with real jobs — the same
name prefix, none of the delegation — so the pattern in this repo is to vendor, and these
two have simply not been vendored.

**This is not the circularity the section above forbids.** That one is specifically about
*gates*: a repo whose gates run through a published copy of this package cannot test its
own working tree. Neither delegated workflow invokes `rhiza-task`, `make` or `uv` at the
pinned SHA — upstream's codeql is `codeql-action/init` plus `analyze` (its only `make`
mention is inside a `build-mode == 'manual'` step that Python never takes, and which
`exit 1`s if reached), and upstream's scorecard is `ossf/scorecard-action` plus
`upload-sarif`. So no gate of this repository runs through a published `rhiza-task`.

**What the SHA pin is holding back, though, is exactly that.** v1.4.2 is the template
release that *retired the make layer*, which means upstream's gates now are `rhiza-task`.
If a later release adds a `uvx rhiza-task` setup step to either workflow, a routine pin
bump would make this repository gate itself with a published copy of itself, silently and
for the first time. So the pin is load-bearing in a way a version pin usually is not:
**before bumping either SHA, read the upstream workflow and check it still invokes no
`rhiza-task`.** That is the review this table exists to prompt.

Why not simply vendor them and be done: the delegation buys the *publish* half —
`security-events: write`, `upload-sarif`, the Scorecard badge and API. Those are
workflow-level `uses:` actions, so no task in this package can replace them, and a
`rhiza-task codeql` or `rhiza-task scorecard` would complement these files rather than
retire them. Vendoring remains the answer whenever the review above becomes tiresome; it
is a copy of two short files, not a redesign.

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
`lookup` and `Guard.check` carry 39 executable examples that do run. `tests/test_doctests.py`
is what runs them — `doctest.testmod` over every module `pkgutil` finds under
`src/rhiza_task`, one parametrised case each, plus five cases asserting those particular
docstrings still carry examples at all.

`rhiza-test` used to be credited with this and does not do it: pytest-rhiza's
`test_docstrings` asks whether a docstring exists and is well-formed, and `interrogate`
asks the same presence question at 100%. Neither evaluates a `>>>`, so until #66 the 39
examples were unexecuted by any gate — stale-proof only by discipline, in exactly the five
places a refactor is most likely to change quietly.

Two things about the shape of that file, both deliberate. It imports every module under
`src/`, which is the one place the suite's hermeticism bends — harmless, because importing
a task module only registers its tasks, but it is why the rule above is about what a test
*runs* rather than what it imports. And the doctests are collected by a test rather than by
`--doctest-modules`: in the `test` task's vector that flag would ship to consumer repos,
where gating doctests is their call, and in `pytest.ini`'s `addopts` it would also apply to
`rhiza-test`'s `pytest --pyargs pytest_rhiza.checks.*` — gating a dependency's doctests in
this repo's name.

### The prose examples are gated too, and by a task rather than a test

The same argument applied twice over to `docs/`. `README.md`'s fences were covered by
pytest-rhiza's `test_readme_validation` under `rhiza-test`; the docs tree — 62 fences, 24 of
them in `getting_started.md` — was covered by markdownlint asking whether the *markdown*
parses, and by nothing asking whether the commands did. `rhiza-task docs-examples` is that
gate, named by `ci.yml`'s `gates` job because, like `complexity`, it is deliberately not an
`all` prerequisite.

It is a **task and not a test**, and that placement is the rule in this repo rather than a
preference: checking a shell fence means running `bash -n`, and no test here runs a tool. So
the logic belongs in a task body, exactly as the note above about wanting a real subprocess
in a test says. Its own tests then patch `bash` and `uv_run` and assert the vectors, like
every other task's.

Two things a change to `docs/` should know. A ```` ```result ```` block is **executed and
diffed** against the `python` fences above it, so an example that goes stale fails a build
rather than quietly outdating — and the prelude is every earlier `python` fence in that
file, because `README.md`'s pair needs the first fence's `@task` before the second's
`lookup`. And fences in a language it cannot check (`toml`, `mermaid`, `makefile`, `yaml`,
and those carrying no language) are **reported with a count** rather than passed over in
silence, because a green line with no numbers reads as full coverage.

## The coverage floor is 100, and it is load-bearing

`[tool.rhiza-task] coverage_fail_under = 100` in `pyproject.toml`. `rhiza-task test` and
`rhiza-task coverage` both fail below it.

It is not a vanity number — **it is what justifies the test-layout opt-out.** The suite is
organised by *behaviour*, not as a 1:1 mirror of `src/`: `test_tasks.py`,
`test_bundle_tasks.py`, `test_dev_tasks.py` and `test_language_layers.py` cover the twelve
task modules as groups. `[tool.check_test_layout] enforce = false` declares that, and its
required `reason` argues that per-module coverage is guaranteed by the floor instead.

**So lowering the floor invalidates the opt-out.** The two move together, or neither moves.

Two lines are excluded by `# pragma: no cover`, each carrying its reason, and both are
structural: the `TYPE_CHECKING` guard in `spec.py` and the `__main__` delegation. Neither
is reachable under test by construction, so there is no exclusion left that a test could
retire — a new `# pragma: no cover` should be argued for rather than added.

## Where the gates run

`ci.yml` has six jobs, and its comments explain each in detail. In short:

- **`test`** — a 12-cell `pytest` matrix (3 OS × Python 3.11–3.14). **Windows is
  deliberate**, as the assertion that the shell dependency is gone, and the job carries a
  CLI smoke step because the suite stubs every subprocess and so never starts the CLI.
- **`lint`** — `ruff check` and `ruff format --check`, both halves always running.
- **`gates`** — the dogfood `uv run rhiza-task all`, plus the two gates deliberately outside
  `all`: `complexity` and `docs-examples`.
- **`go-layer`** and **`rust-layer`** — the Rust and Go vectors against a *real* toolchain,
  on a throwaway module built in `$RUNNER_TEMP`. These exist because those layers' coverage
  is entirely argument-vector assertions, so a vector that is well-formed and *wrong* passes
  every other gate here and breaks in the first consumer that has cargo or go installed.
- **`lowest-deps`** — resolves `--resolution lowest-direct` to prove the manifest's floors
  are real.

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

One rule those four are held to: **if the branch count grows, the comment states a
ceiling.** Three of them are bounded by closed sets — `_run_one` by the size of `Status`,
`Guard` and `Guard.check` by the number of guard kinds — so their figures cannot drift far
and "deliberate" is a complete answer. `Config.__post_init__` is one branch per validated
setting, which is open-ended, so it names C (15) as the point where per-group helpers win.
A growth rule without a limit is an open licence rather than a decision; `uvx radon cc src
-a -s` is how you check, and nothing gates it.

When changing such code, update the comment with it. A stale comment is worse than none:
`ci.yml` once asserted that `rhiza-task fmt` skipped for want of a pre-commit config, for
several commits after that config was added.

One placement rule the convention has to bend to: a suppression comment's reason goes
*above* the line, never after the marker. bandit reads everything following `# nosec` as a
comma-separated list of test IDs, so `# nosec B404 - fixed argument vectors` cost six
`Test in comment:` warnings per occurrence. For the same reason the prose explaining it
must not spell the marker itself — bandit scans every comment for it, wherever it sits.

Docstring coverage is enforced at **100% over `src/` *and* `tests/`** — test functions and
fixtures need docstrings too, with an `Args:` section for every parameter.
