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
managed repo would belong upstream. No `uses:` in this repository points at
`jebel-quant/rhiza`; `grep -rn 'uses:.*jebel-quant/rhiza' .github/` is how you confirm it,
and it should stay empty.

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

### The `rhiza_` filename prefix is residue, not a sync

Three workflows still carry it — `rhiza_book.yml`, `rhiza_paper.yml` and
`rhiza_release.yml` — and exactly one of them, `rhiza_release.yml`, still opens with
upstream's *This file is part of the jebel-quant/rhiza repository* boilerplate;
`grep -rn 'This file is part of the jebel-quant/rhiza' .github/` returns that one line. The
other two open with a local *Adapted from* note naming what was changed and why, which is
the honest header for what they now are. Either way the prefix is the history of how they
arrived (a copy, once) rather than a claim about how they are maintained: each is a full
local file with real jobs, nothing reconciles them against upstream, and editing them is
the normal way to change them.

`rhiza_release.yml` is the one not to rename, and the reason is in its own header:
**PyPI Trusted Publishing validates the exact workflow file path.** The trusted publisher
registered for this project names that filename, so renaming the file breaks publishing at
the next tag — and no gate here can catch it, because every job is green until a release
actually runs. Renaming it means editing PyPI's publisher entry in the same change.

**`rhiza_codeql.yml` and `rhiza_scorecard.yml` used to be the exception** — thin stubs that
`uses:`-delegated to reusable workflows in `jebel-quant/rhiza`, pinned at v1.4.2. Both are
now removed, which is what makes the paragraph above unqualified. Two consequences worth
knowing:

- **CodeQL security analysis runs from GitHub's default setup, not from a workflow.**
  GitHub's dynamic *code-quality* scan also reports on pull requests, but that is a
  different product from the security analysis. Default setup was enabled in repository
  settings on 2026-08-21 and covers `actions` and `python`, which restores the coverage
  with no workflow file and no upstream dependency — strictly better than either
  delegating to a stub or vendoring the workflow. So there is deliberately no `codeql.yml`
  to find, and its absence is not a gap to fill:
  `gh api repos/Jebel-Quant/rhiza-task/code-scanning/default-setup` reporting
  `"state":"configured"` is how you confirm the coverage exists. That endpoint may report
  `"languages":[]` straight after a settings change even while both analyses run; the
  run's own jobs are the authority.
- **The OSSF Scorecard badge and SARIF upload are gone with the scorecard workflow.**
  `rhiza_release.yml` still generates SBOMs and build attestations, and `CODEOWNERS` still
  gates review — but those are kept for consumers rather than for a scorer, and their
  comments now say so on their own terms. Nothing here is scored by Scorecard; a comment
  that implies otherwise is stale and should be rewritten, not left as a hint that a run
  exists somewhere.

A local `rhiza-task scorecard` task was considered as a replacement and rejected: Scorecard's
checks are forge-API queries about the *remote* repository, so a local task could not give a
pre-push signal the way `bandit` or `pytest` do — it would restate what the workflow already
reported, while costing a public task name on a released package.

### The one local composite action

`.github/actions/setup-tectonic/` is the first and only `uses: ./` in this repository, and it
exists for a reason worth stating rather than copying: **two workflows compile the same
document.** `rhiza_paper.yml` publishes the PDF as an artifact and on the `paper` branch,
`rhiza_book.yml` the copy inside the book, and a typesetting engine's output moves between
versions — so a version skew between them would show up as two different PDFs of one paper,
with nothing to catch it. The distribution this replaced had the same hazard in a worse form:
a four-package apt list in each file, kept in step by hand.

So the action is where the engine's version, URL and sha256 live, exactly once. Two things
follow. `apt-get install tectonic` is **not** the alternative — tectonic is packaged for
Debian but not for Ubuntu noble, which is what `ubuntu-latest` is, and a first attempt at
this failed exactly there. And the composite action needs its **own** `dependabot.yml`
entry, because `directory: /` covers `.github/workflows` and not actions nested below
`.github/actions`; without it the `actions/cache` SHA inside would be the only unwatched
action pin in the repository.

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
`install_hooks` from `tasks/quality.py`, and `quality.py` imports `check` from
`tasks/fences.py`. That is one layer, not an inversion.

`fences.py` is the one module under `tasks/` that registers no task — it holds the
fenced-example checker that `docs-examples` runs, extracted when it had grown to two thirds
of `quality.py`. It is also the worked example of **rule 4 deciding a design**: keeping the
`@task` in `quality.py` while moving the helpers meant either exporting seven
underscore-prefixed names, which rule 4 forbids, or giving the module a single public entry
point that takes the gate's whole body. It has one, `check`, and the helpers stayed private.
Reach for the same shape rather than promoting a handful of privates the next time a task
body outgrows its module.

Check the whole invariant in one command:

```bash
grep -rnE '^\s+(from|import) ' src/          # deferred imports
grep -rnE 'from \.{1,2}[a-z_.]* import .*\b_[a-z]' src/   # private cross-module imports
```

The first returns **two** lines, and only one is an import: `spec.py`'s `TYPE_CHECKING`
guard, which rule 1 permits. The other is prose inside a `config.py` docstring that happens
to begin with the word "from" — a false positive of matching text rather than syntax, and
worth knowing so it is not read as a violation to fix. The second command should return
nothing at all.

**Both greps read `src/`, and the two rules do not carry over to `tests/` identically.**
Worth stating, because running them repo-wide returns about twenty-five hits and nothing
explains them:

- **Rule 3 (no deferred imports) does not apply to `tests/`, and should not.** A test that
  registers a task with `@task` wants that import *inside* the test, so the registry is
  mutated within the scope the `registered` fixture cleans up; hoisting it to module level
  would leak a task into every other test in the file. `test_cli.py` is most of the count and
  every occurrence is that pattern. The rule exists because a deferred import in `src/` hides
  a cycle for years — a test function has no cycle to hide.
- **Rule 4 (no private name crosses a module boundary) *does* carry over**, because its
  reason is about callers rather than layers. `tests/test_uv.py` importing `_task_modules`
  from `conftest.py` was the one violation, introduced by #118 and caught by running the
  second grep over `tests/` rather than by any gate. It is now `task_modules`, public with a
  docstring saying why — the promotion the rule asks for. So: `grep … tests/` for the second
  command should also return nothing, and that is worth checking when you touch `conftest.py`.

## Tests assert argument vectors, and never run uv

**No test in this suite runs `uv`, or any other tool.** Every test patches all **five** of
`rhiza_task.uv`'s entry points — `uv`, `uvx`, `uv_run`, `tool`, `capture` — through the
`Recorder` fixture in `tests/conftest.py`, and asserts on the argument vector that *would*
have been executed.

Five, and it was four until #116. `capture` was left out of the fixture while the other four
were patched, so each test that needed it patched it by hand; all of them did, so nothing
ever leaked, but the guarantee this paragraph makes was four fifths true and failed *open* —
a forgotten patch ran `gh` for real rather than erroring. `tests/test_uv.py` now asserts that
no task module holds a real entry point once the fixture has run, so the sentence above is
checked by a test rather than by this file being kept up to date.

**"Or any other tool" was the half that was still prose, and #151 is what it cost.** `uv.py`
is not the only door: four modules import `subprocess` directly, because what they run is not
a uv form at all — `tasks/quality.py`'s `_git`, `tasks/doctor.py`'s version probe,
`tasks/fences.py`'s `bash -n`, `tasks/go.py`'s cobertura pipe. Those were patched per test, by
hand, in exactly the shape #116 had already diagnosed one level up — and the fix for `capture`
did not generalise, because the leak assertion it added is built from `rhiza_task.uv`'s five
names and so cannot see a module that never bound one.

`conftest`'s `no_real_subprocess` closes them, and two things about its shape are the reusable
part. It is **autouse**, so a test that asks for no fixture at all is still covered — needing
to request `recorder` was itself a way to forget. And it **refuses rather than records**:
there is no single vector shape across a git call, a `--version` probe, a `bash -n` and a pipe
holding `stdin`/`stdout`, so a recorder would have to hand back a plausible zero to a test that
never said what it expected, which is a pass invented by the fixture. An `AssertionError`
naming the vector cannot be read as green.

It found five on the first run: `TestQuality`'s `rhiza-test` group was calling real
`git tag --list`, and passing only because a tmp directory is not usually inside a git
repository — on a machine where it is, those tests read that repository's tags. **That is the
argument for a guard that fails closed over a convention that holds**, and the rule to carry
forward is narrower than "patch your subprocesses": a new direct `subprocess` user needs
nothing, but a module switching to `from subprocess import run` binds the function into its
own namespace and slips the guard in silence — which is why `test_uv.py` pins the import
*form* as well as the guard itself.

**#157 is the third instalment of the same mistake, and where it was finally derived.** The
guard's own scope was written down by hand — `("run", "call")`, accurate for what `src/`
called that week — which is #116's tuple of twelve module names wearing a different hat.
`SUBPROCESS_ENTRY_POINTS` now comes from `subprocess.__all__`, minus three *properties*
rather than three names: the ints (`PIPE`, `DEVNULL`, `STDOUT`), the exception types, and
`CompletedProcess`, which the tests construct themselves. What is left starts a process, and
a starter this package begins using is covered before anyone notices they had to.

`os` is the door that stays open, because it cannot be closed the same way: every module uses
it for paths, so stubbing its attributes would break the suite rather than protect it. So the
guarantee is asserted from the other side — `test_uv.py` walks `src/`'s **AST** (not a grep;
a docstring naming `os.system` is not a call) and fails on any `os.system`, `os.popen`,
`exec*` or `spawn*`. **bandit does not already cover this**, which is the assumption worth
checking rather than repeating: `B605` reports a literal `os.system` at LOW severity and
`security` runs `bandit -ll`, so a planted one passes that gate today. Verified by planting
one.

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

The same argument applied twice over to `docs/`. `README.md`'s fences are covered by
pytest-rhiza's `test_readme_validation` under `rhiza-test`; the docs tree — sixty-odd fences
across eleven files, the largest share of them in `getting_started.md` — was covered by
markdownlint asking whether the
*markdown* parses, and by nothing asking whether the commands did. `rhiza-task docs-examples`
is that gate, named by `ci.yml`'s `gates` job because, like `complexity`, it is deliberately
not an `all` prerequisite.

**The two gates divide by language, not by file**, and that is a correction to how this
worked. The rule they protect is unchanged — two gates reporting one verdict would make a
single fact read as two — but dividing by *file* left a hole: `README.md` was pytest-rhiza's
entirely, and its `test_readme_validation` never looks at `toml` or `yaml`, so the README's
two `[tool.rhiza-task]` examples were checked by **nothing**. Confirm it for yourself —
that module's source contains no reference to either language.

So pytest-rhiza owns README's **code** fences (`python`, `bash`) and `docs-examples` owns
**data** fences (`toml`, `yaml`) everywhere, README included. Nothing is checked twice, and
`DATA_FENCE_LANGUAGES` in `tasks/fences.py` is the one place to narrow if pytest-rhiza ever
learns toml. A broken python fence in the README still fails `rhiza-test`; a broken toml one
now fails `docs-examples`, which reports it on its own line so that two of the README's ten
fences are never mistaken for the whole file.

It is a **task and not a test**, and that placement is the rule in this repo rather than a
preference: checking a shell fence means running `bash -n`, and no test here runs a tool. So
the logic belongs in a task body, exactly as the note above about wanting a real subprocess
in a test says. Its own tests then patch `bash` and `uv_run` and assert the vectors, like
every other task's.

Two things a change to `docs/` should know. A ```` ```result ```` block is **executed and
diffed** against the `python` fences above it, so an example that goes stale fails a build
rather than quietly outdating — and the prelude is every earlier `python` fence in that
file, because `adding_a_task.md`'s pair needs the first fence's `@task` before the second's
`lookup`. There are **two** such blocks — that pair, and `layers.md`'s shadowing example,
whose answers used to sit in trailing `# 'python:test'` comments until they were made a
`result` block — and between them that is **every python fence in the tree which produces
output**. The rest define a task, bind a name or quote a `Guard` fragment, and print nothing,
so a `result` block on them would be ceremony rather than a check. That invariant is the
thing to preserve, and it is checkable rather than remembered: the fences which print are
exactly the fences which are diffed.

Count them with `grep -rcE '^\s*```(python|py)\s*$' docs/*.md`, and note the leading
`\s*` — some fences are **indented** inside a tabbed admonition (`adding_a_task.md:77` and
`:86`), so a grep anchored at the line start misses them in silence. The `N python` on the
gate's own summary line is the figure to reconcile against. One of the undiffed fences is
also why a diffed block is not free: `adding_a_task.md`'s later fences sit *after* its
`result` block, and moving one above would put a printing fence in the prelude — the
assumption `_result_violations` documents and deliberately does not loosen.
And fences in a language it cannot check (`mermaid`,
`makefile`, and those carrying no language) are **reported with a count** rather
than passed over in silence, because a green line with no numbers reads as full coverage.

`toml` and `yaml` used to be in that uncheckable list and are now parsed, which is worth
knowing for two reasons beyond the drop in the uncheckable count. The `toml` half is
in-process `tomllib` — stdlib at this package's `>=3.11` floor, so it is checked on every
machine that can run the gate — and it covers every fence quoting this repo's *own*
`[tool.rhiza-task]` and `[tool.bumpversion]` settings, which is the class that goes stale
when a setting is renamed. The `yaml` half is a **provisioned subprocess**
(`uv run --no-project --with pyyaml`), deliberately not a dependency: `rhiza-task` is a
published CLI, so a runtime dependency is an install cost every consumer pays on every
`uvx` invocation, and two fences do not justify one. That makes yaml the second check
after `bash -n` that can go *unavailable* on a working machine, and both follow the same
rule — the fences are counted out of `checked` and named on their own `[INFO]` line, never
assumed sound. Parsing is not validation: a fence that parses may still name a setting
this package does not have, and for the workflow snippets actionlint already owns that
question over the real files.

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

`ci.yml`'s jobs are the ones below, and its comments explain each in detail. The count is
deliberately not written here: the list *is* the enumeration, so a reader can count it, and
an integer in this sentence would be one more measured figure with nothing reading it back
(see the rule further down). In short:

- **`test`** — a 12-cell `pytest` matrix (3 OS × Python 3.11–3.14). **Windows is
  deliberate**, as the assertion that the shell dependency is gone, and the job carries a
  CLI smoke step because the suite stubs every subprocess and so never starts the CLI.
- **`lint`** — `ruff check` and `ruff format --check`, both halves always running.
- **`gates`** — the dogfood `uv run rhiza-task all`, plus the gates deliberately outside
  `all`: `complexity`, `docs-examples` and `test-pyproject`. The last is outside it for a
  different reason than the other two — `rhiza-test` already runs pytest-rhiza's pyproject
  checks, so this is the same assertions at higher verbosity — and looking redundant is how
  it came to run nowhere at all until #152. Its `--pyargs` selection is its own, and nothing
  else would notice it breaking.
- **`links`** — lychee with `--offline` over `README.md` and `docs/`, so only *local*
  targets are resolved. The network half stays in `weekly.yml`: an external 404 is somebody
  else's deploy, and a gate that fails for reasons its author cannot fix is one people learn
  to ignore. It is also the only thing checking docs cross-links at all — `docs-examples`
  asks whether fences parse, markdownlint whether the markdown is well-formed, and neither
  follows a link.
- **`python-layer`**, **`go-layer`** and **`rust-layer`** — the three layers' vectors against
  a *real* toolchain, on a throwaway project built in `$RUNNER_TEMP`. **`python-layer` runs on
  Windows as well as Linux; the others do not, and that is a decision rather than an
  omission** — see #158 and the comment on the job. The precedent is #148: a vector that was
  well-formed, unit-asserted and unrunnable on Windows shipped in a release, and a consumer's
  red matrix caught it rather than anything here. Layer jobs exist to catch exactly that, so
  all of them looking at one platform meant the class of bug was guarded on Linux only.
  `python-layer` is the cheapest place to fix that and the most exposed — no docker, no `gh`,
  no cargo, no go, just uv and a scratch project, and `test` and `coverage` are the vectors
  that touch paths hardest. The rest stay Linux-only: docker and git-lfs on a Windows runner
  are more runner than vector, and cargo and go would each need a toolchain install per cell.

  One thing to keep if that job is edited: `${RUNNER_TEMP//\//}`. On Windows that variable
  is a backslash path, and bash reads backslashes as literal characters where Python's `Path`
  reads them as separators — so the raw value means two different things inside one step.
  Rewriting to forward slashes is understood identically by both, and is a no-op on Linux. These exist because
  those layers' coverage is entirely argument-vector assertions, so a vector that is
  well-formed and *wrong* passes every other gate here and breaks in the first consumer that
  runs it. The Python one was added last and is the least obvious: `gates` already runs
  `rhiza-task all`, but against *this* repository — the package's own tree, with a manifest
  tuned to it — so the shipped defaults a consumer actually gets were the part going
  unexercised.
- **`bundle-layer`** — the same argument carried past the language layers, which is where it
  always led: `docker`, `presentation`, `marimo`, `lfs` and the GitHub helpers had no real
  execution anywhere, so around eighteen shipped vectors rested on unit assertions alone.
  One job rather than five, because docker, node, git-lfs and `gh` are all preinstalled on
  `ubuntu-latest` and the language layers are separate only because each needs its own
  toolchain. **Four vectors are deliberately absent and cannot be added**: `docker-run`
  (`-it`), `presentation-serve`, `marimo` and `serve` each block forever by design, so a
  step running one would hang to the timeout rather than report. `presentation-pdf` is
  absent for a weaker reason — Marp renders PDF through headless Chrome, so it would
  download a browser per run, and its vector differs from `presentation`'s by one flag the
  suite already asserts. `lfs-pull` needs a remote holding LFS objects, and the fixture has
  no remote.

  **`clean` runs here too, and it is the one with a fixture rather than a flag.** It ran
  nowhere until #152 — the only *deleting* vector in the package, `git clean -d -X -f` plus
  an rmtree plus `git branch -D`, covered by unit tests that patch `subprocess.run` and so
  cannot ask whether real git agrees a branch is gone. The fixture is a real repository with
  a real remote, and the branch is made gone the way it goes gone in life: pushed, then
  deleted upstream. Its three assertions separate the halves that a zero exit cannot —
  and the ignored file it looks for is `scratch.log` rather than `dist/` on purpose, because
  `dist` is in `CLEAN_ARTIFACTS` and the rmtree would remove it whether `git clean` ran or
  not.
- **`extras-layer`** — the same argument again, and the family it had stopped short of. The
  Testing extras bundle was the last set with no real execution: `benchmark`, `stress` and
  `hypothesis-test` ran **nowhere**. It is the sharpest case rather than the mildest, because
  `benchmark` is the only vector in the package that *pins* what it provisions — exactly,
  because benchmark numbers only compare within a tool version — and those pins exist in one
  place, the vector itself, with nothing else installing them.
  `grep -n withs= src/rhiza_task/tasks/extras.py` is where to read them; they are not copied
  here, for the reason the rule below gives. A separate job rather than three steps on
  `python-layer`, whose fixture comment says anything more would be testing the fixture and
  is right: these three need a benchmarks folder, a stress folder, marker registrations and a
  property test. Unlike `bundle-layer`, this job leaves no vector out — every task in the
  section runs here.
- **`lowest-deps`** — resolves `--resolution lowest-direct` to prove the manifest's floors
  are real.

Two additions live outside `ci.yml` and are easy to miss when counting: `python-layer` now
also runs **`coverage`** against the throwaway project — it is not `test` with a flag, it
writes the Cobertura XML the book and any badge reads, so the step checks the *file* rather
than the exit status — and `rhiza_paper.yml` runs **`paper-clean`** as its last step, because
that is the only job in the repository with a compiled paper to clean. Both are #152. The
`paper-clean` step is last for a reason worth knowing before moving it: `paper_clean` removes
the **PDF as well as** the aux files, so the upload and the `paper`-branch push must already
have finished with it. Its assertions name `.pdf` and `.log`, which is what tectonic actually
leaves — looking for a `.aux` would pass against a `paper-clean` that deleted nothing.

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
it records. **One** block ranks C on cyclomatic complexity, and it states why the flat form
is preferred over a decomposition that would satisfy the metric.

The rule it is held to: **if the branch count grows, the comment states a ceiling.**
`_run_one` is bounded by a closed set — the size of `Status` — so its figure cannot drift
far and "deliberate" is a complete answer. A growth rule without a limit is an open licence
rather than a decision.

Two earlier cases were the other C blocks, and both are now worked examples of a ceiling
being *honoured* rather than restated — which is the outcome this rule is for.

`Guard` and `Guard.check`, at C (14) and C (13): at one branch of headroom the comment
stopped claiming "deliberate", named a tuple of `(predicate, message)` as the
decomposition to reach for, and that decomposition is now `_clauses` — which brought the
class back into A. The lesson kept from it is about the *generator*: the property the flat
form was
really protecting was evaluation order — tool before file before folder before glob, each
only reached if the last was satisfied — which a lazily-consumed `yield` keeps and an
eagerly-built tuple of pairs would have lost. That, and needing no `self`, is why the
helper is module-level.

`Config.__post_init__`, at C (13): one branch per validated setting, which is *open-ended*
— every future setting adds one — so its comment named C (15), roughly two more settings,
as the point where per-group helpers win. #124 took it there at two branches of headroom
rather than waiting for the gate, and it now has no branches of its own: a flat sequence of
calls to
`_coerce_sequence_fields`, `_validate_layers`, `_validate_typechecker`,
`_validate_coverage` and `_validate_complexity_max`. The readable one-field-per-step order
survived; each step is just bounded. **A new validated setting now adds a call here and its
branches to its own helper**, which is what stops "one branch per validated setting" being
a growth rule at all.

**One correction that decomposition forced, and it matters because it is load-bearing
advice.** This file used to say the `Guard` helper was module-level because *radon scores a
class as the sum of its methods*, so a second method would relocate the figure and reduce
nothing. **That is wrong.** radon scores a class as the **mean** of its methods, so a small
method *lowers* the class score. The check is a two-method probe under `uvx radon cc -s`: a
lone method of 5 scores its class 6, and adding a second of 1 scores it **4**, not 6. So
`Config`'s five new helpers are *methods* — they need `self` — and the class score went
*down* rather than up. Prefer a module-level function when it needs no `self` or when
laziness matters, as `_clauses` does; not to dodge a class score that works the other way.

**Accumulation has a ceiling too, and it is one metric replacing another.** `complexity`
gates the worst *block*; nothing gated a module whose blocks are each defensible and
collectively a lot. #153 filled that with `radon mi -n C`, and #156 is the correction: MI
counts length, comments count as length, and dense comments are the house style two sections
up — so writing the note that explained the ceiling moved the figure **down**, 1.78 points
for 19 lines of prose with no branch touched. A gate the convention erodes is one people
raise rather than heed.

The measurement that settled it is the part worth keeping, because it says MI was not merely
fragile here but wrong: it ranked `config.py` B and `tasks/fences.py` A, while fences.py
carries *more* blocks at rank B or worse and the same total complexity. So the ceiling is now
a count of blocks at CC ≥ 6 per module — `.github/scripts/accumulation_ceiling.py`, run from
the `gates` job, prose-insensitive by construction. **A module reaching the ceiling is the
point to decompose, not the point to raise the number**, which is the same rule this section
already states for a block's own figure. The lesson generalises past radon: when a gate and
the convention it runs beside pull in opposite directions, one of them is measuring the wrong
thing, and it is worth finding out which before adjusting either.

Unlike the layering invariant above, this one **is** gated: `rhiza-task complexity` fails on
any block above `complexity_max`, which `pyproject.toml` leaves at the shipped 15, and
`ci.yml`'s `gates` job names it. So a ceiling a comment commits to is read back by a build
rather than by a reader who remembers to — which is the whole reason
`Config.__post_init__`'s note could say "roughly two more settings" and be held to it.
`uvx radon cc src -a -s` remains how you read the figures by hand, and the gate reports the
worst *block*, classes included. **`_run_one` is the block to watch** — it is the only one
still ranking C, and the one whose comment claims a closed set rather than a ceiling. No
class ranks worse than A. Both of those are claims a refactor either preserves or visibly
breaks; the integers behind them are not restated here, because that is what the command
above is for.

### Which figures this file is allowed to quote

That last paragraph used to restate four measured numbers, and it is the reason this section
now has a rule. A measured figure in prose is **read back by nothing**: `docs-examples` diffs
a `result` block, `test_doctests.py` evaluates a `>>>`, and `complexity` reads its ceiling
back from a build — but a block score written into a sentence is discipline alone, and the
discipline failed three times in three consecutive reviews. Twice the wrong number was
introduced *by the edit that was correcting a different wrong number*, and once the file
contradicted itself, with the newer statement being the wrong one. Every gate was green
throughout, because no gate was looking.

So the rule, and it is about *which* figures rather than about being careful:

> **Quote a figure only when it cannot drift.** Historical figures — what a block scored
> *before* a decomposition — record a decision and are immutable. Configured figures — the
> `complexity_max` of 15, the coverage floor of 100 — live in `pyproject.toml`, and a gate
> fails when the code disagrees with them. **Current measured state gets a claim, not an
> integer:** "the only block still ranking C", "no class worse than A", "the fences which
> print are the fences which are diffed". Name the command that reads the live value and
> stop there.

The three figures left in this section are all of the first two kinds. A claim of the third
kind is worth more than the integer it replaces, because it says what the number was *for* —
and a refactor either preserves it or visibly breaks it, which an integer transcribed into a
sentence does neither.

Gating the prose instead was considered and not done: it means a bespoke parser for one
file's sentences, plus a public task name on a published CLI, to police figures that mostly
did not need to be there. Deleting them was cheaper and removes the failure surface rather
than watching it.

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

## Commit types are release evidence, not decoration

`/rhiza:release` deliberately refuses to recommend a bump. It prints the `patch`/`minor`/
`major` candidates with the commit counts behind each and stops, because whether a change is
breaking is a judgement about API-stability intent that no log records. That design has a
consequence for how commits are written here: **the log is the only evidence the human
choosing the version gets.** A conventional type that understates a change silently biases
that choice, and `uvx git-cliff --bumped-version` — which some other flow may reach for —
would derive the understated answer outright.

So one rule, and it is about gates specifically because this package *is* gates:

> **A change that makes a gate reject input it previously accepted is `feat:`, and needs at
> least a `minor`** — even when it reads like a fix, and even when it closes a bug.

The failure it prevents: a consumer upgrades on what they were told was a patch, a gate they
never touched goes red, and it reads as a regression rather than as the gate working. Three
changes landed as `fix:`/`refactor:` before this was written down — `docs-examples` gaining
`toml` and `yaml` (#107), then README's data fences (#112), then a crashed yaml checker
failing instead of skipping (#111). All three are strictness increases; none says so in its
subject line. See #115.

That was discharged by **v1.1.0**, a `minor` carrying an `⚠️ Upgrade note` at the top of its
`CHANGELOG.md` section that names all three. The dated reminder that stood here is gone, as it
said it would be.

Two things that release established, both worth keeping. **`git-cliff` files a strictness
increase wherever its commit type puts it, so the upgrade note is always manual** — and
`CHANGELOG.md` is where to put it, because `rhiza_release.yml` generates the *GitHub release
body* separately with `git-cliff --latest` from commits, so a note added here does **not** reach
it. And **`git-cliff --prepend` eats this file's `# Changelog` heading**: prior sections survive,
the title does not, so restore it and diff before committing. Both of v1.1.0's prepends did it.

### What counts as public, when the question is a name rather than a gate

The rule above is about gate *strictness*, and v1.4.1 found the gap it leaves: that release
renamed `tasks/setup.py`'s `CHECKS_EXECUTABLE_BIT` to `POSIX` under a **patch**. Nothing
imports it and no consumer could have noticed, but nothing written down said so either, and
the answer was being decided per change by whoever was making it. #154 is where it was
settled, and the split is not new — it is what the repository already documents, now said
out loud:

> **Public: the task names, their flags, and the five modules `docs/api.md` documents**
> (`spec`, `uv`, `config`, `runner`, `cli`). **Internal: everything under `tasks/`.**

Both halves are checkable rather than asserted. `__init__.py`'s `__all__` exports
`__version__` and nothing else; `docs/api.md`'s table lists exactly those five modules and no
task module; and `adding_a_task.md` — the one page telling a consumer to import anything —
imports from `spec` and `uv`. A task is reached by *name*, through the CLI, which is why
renaming a task, dropping one of its flags, or changing what a setting is spelled is
breaking, while renaming the Python object behind it is not.

So: a module-level name under `tasks/` may be renamed in a `refactor:` or alongside the
`fix:` that motivated it, with no minor. A name in those five modules may not — `Config`,
`Guard`, `task`, `uvx` and their kin are what `adding_a_task.md` teaches, and moving one is a
`feat!:` at least. **The underscore prefix is not the signal here**; layering rule 4 governs
who may import a private name across a module boundary *inside* this package, which is a
different question from what a consumer may rely on, and a name can be public to the fourth
rule and internal to this one. `install_hooks` is exactly that: public because three sibling
modules need the same spelling, internal because no consumer was ever told it exists.
