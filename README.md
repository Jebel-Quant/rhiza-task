# rhiza-task

[![PyPI](https://img.shields.io/pypi/v/rhiza-task.svg)](https://pypi.org/project/rhiza-task/)
[![Downloads](https://static.pepy.tech/personalized-badge/rhiza-task?period=total&units=international_system&left_color=grey&right_color=blue&left_text=downloads)](https://pepy.tech/project/rhiza-task)
[![Python](https://img.shields.io/pypi/pyversions/rhiza-task.svg)](https://pypi.org/project/rhiza-task/)
[![CI](https://github.com/Jebel-Quant/rhiza-task/actions/workflows/ci.yml/badge.svg)](https://github.com/Jebel-Quant/rhiza-task/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![uv](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/uv/main/assets/badge/v0.json)](https://github.com/astral-sh/uv)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![Docs](https://img.shields.io/badge/docs-jebel--quant.github.io-blue)](https://jebel-quant.github.io/rhiza-task/)

<!--
The downloads badge counts `uvx rhiza-task` runs, and that is the point of having it rather
than an aside: this package is invoked far more often than it is installed into a project, so
a counter that missed uv would report a small fraction of its use. It does not miss it --
`uv` is by a wide margin the dominant installer here, and pip is a rounding error. Check it
rather than believing this comment:

  curl -sS 'https://sql-clickhouse.clickhouse.com/?user=demo&default_format=TabSeparatedWithNames' \
    --data-binary "SELECT installer, sum(count) FROM pypi.pypi_downloads_per_day_by_version_by_installer_by_type \
                   WHERE project='rhiza-task' GROUP BY installer ORDER BY 2 DESC"

Two things about the choice of service. It is pepy rather than shields because shields has no
PyPI *total* endpoint -- `img.shields.io/pypi/dt/...` 404s, and only `dm`/`dw`/`dd` exist -- so
a total means one more third-party service than the badge row already depends on. And what
either service counts is *file downloads*, which for a CI-invoked CLI is closer to a count of
pipeline runs than of people; mirror traffic is excluded, but a re-run is not.
-->

The rhiza developer tasks as a **pinned CLI** rather than a synced make layer — **one set
of task names across Python, Rust and Go**.

```bash
uvx rhiza-task@1.4.1 test
```

📖 **[Documentation](https://jebel-quant.github.io/rhiza-task/)** — the task catalogue,
the six configuration layers, and how to add a task of your own.

Sibling to [`pytest-rhiza`](https://github.com/jebel-quant/pytest-rhiza), which did the
same thing for `.rhiza/tests`.

## Why

The pain was never make's syntax — it was **distribution by copying**, which make
structurally cannot fix, because `include` cannot reach a remote file. Every consumer got
a full copy at a template tag, and everything downstream was damage control. Version
pinning becomes a dependency pin, which is a real mechanism instead of "copy files at tag
v1.3.3 and hope nobody edited them."

| before, per consumer repo | after |
|---|---|
| `.rhiza/rhiza.mk` — 200 lines, synced | *gone* |
| `.rhiza/make.d/*.mk` — 1023 lines in 15 files, synced | *gone* |
| `exclude:` entries in `template.yml`, because a deletion alone is undone by the next sync | not needed |
| targets shadowed in the repo Makefile | `[tool.rhiza-task]` |
| ~40 lines of GNU-make guard and Windows POSIX-shell probe | gone — no make, no shell |
| `install-uv` — 30 lines of `bootstrap.mk` shell | gone — `uvx` provisions the runtime |
| `Makefile` | repo-owned, if a repo wants one |

## Install

Nothing to install. `uvx` provisions it per invocation:

```bash
uvx rhiza-task@1.4.1 list          # what is available
uvx rhiza-task@1.4.1 all           # every gate, as CI runs them
uvx rhiza-task@1.4.1 test --strict # fail rather than skip when a gate measures nothing
```

Task names are unchanged from the retired make layer, so a repo that wants `make test` to
keep working owns that `Makefile` itself and forwards each target to `uvx rhiza-task
<task>` — one rule.

## Tasks

| section | tasks |
|---|---|
| Python | `install` `test` `coverage` `typecheck` `security` `deps` `license` `docs-coverage` `all` |
| Rust | `install` `cargo-tools` `test` `coverage` `typecheck` `security` `deps` `license` `docs-coverage` `all` |
| Go | `install` `go-tools` `test` `coverage` `typecheck` `security` `deps` `license` `docs-coverage` `all` |
| Quality | `fmt` `semgrep` `rhiza-test` `test-pyproject` `todos` `complexity` `docs-examples` |
| Testing extras | `benchmark` `hypothesis-test` `stress` |
| Book | `book` `serve` `marimo` `marimo-validate` |
| Dev | `doctor` `clean` |
| GitHub Helpers | `view-prs` `view-issues` `failed-workflows` `workflow-status` `latest-release` `whoami` |
| Docker | `docker-build` `docker-run` `docker-clean` |
| Git LFS | `lfs-install` `lfs-pull` `lfs-track` `lfs-status` |
| Paper | `paper` `paper-clean` |
| Presentation | `presentation` `presentation-pdf` `presentation-serve` |

The last five sections are bundle-owned fragments. None is a gate — no `all` names them —
so each is guarded on the CLI it wraps and **skips** when that tool is absent, with
`--strict` for a caller who wants the hard failure instead. Three changed behaviour on
purpose, and say so in their module docstring: `lfs-install` configures the repository and
reports how to install the binary rather than downloading one; `presentation` reaches Marp
through `npx --yes`; `paper` compiles with [tectonic](https://tectonic-typesetting.github.io/)
rather than a TeX distribution's driver, which makes `paper-clean` the one task here guarded
on no tool at all — it deletes the artifacts itself, so a machine that cannot build the paper
can still tidy up after one.

### Three layers, one set of names

`test` is pytest in a Python project, `cargo nextest` in a crate and `go test` in a
module — the gate-parity contract that lets `rhiza_ci.yml` call `typecheck` without
knowing the language.

The make layer answered "which one?" at sync time, by copying exactly one of `python.mk`,
`rust.mk` and `go.mk` into a repository. A pinned CLI carries all three, so the answer is
the **manifest present**: `pyproject.toml` → python, `Cargo.toml` → rust, `go.mod` → go.
A repository with two gets both layers, in that order; `layers = ["rust"]` pins it. The
other stays addressable as `rhiza-task rust:test`, and `rhiza-task list --all` shows what
the layers you do not have call things.

`RHIZA_CHECKS` derives the same way: the neutral checks, plus `test_pyproject` and
`test_docstrings` for python, `test_cargo_toml` for rust, `test_go_module` for go.

`coverage` writes `_tests/coverage.xml` in every layer, at that exact path, because that
is what `book`'s badge step reads and what CI uploads — the `--cov` flags in Python,
`cargo llvm-cov --cobertura` in Rust, and in Go a coverage profile piped through
`gocover-cobertura` plus the floor check `go test` has no flag for.

`install-uv` is not a task and cannot be one: it provisions the runtime every task already
runs under. A runner shipping no uv adds an `astral-sh/setup-uv` step instead.

## Design

Reading all ten make fragments back to back, **every recipe has the same three parts**: a
guard on a folder existing, a provision via `uv run --with` or `uvx`, and a long, mostly
static argument list. So the model is declarative, with an escape hatch for the three
recipes that genuinely are not: `test` (retry once on pytest exit 3, the xdist teardown
race, never on 1/2/4), `doctor` (semantic version comparison, formerly an awk function
inside a make recipe) and `book` (aggregate gates, copy reports, export notebooks, build,
badge).

| module | what |
|---|---|
| `spec.py` | `Task`, `Guard`, `Skip`/`Failed`, the `@task` registry, layer resolution |
| `config.py` | six-layer resolution, replacing `?=` and `+=` |
| `uv.py` | the ways rhiza reaches a tool: `uv`, `uvx`, `uv run --with`, `tool` for a cargo/go binary already on PATH, and `capture` for the one recipe that needs stdout back |
| `runner.py` | prerequisite dedup, guards, outcome bookkeeping |
| `cli.py` | Typer app, generated from the registry |
| `tasks/*.py` | the gates themselves, loaded by entry point |

Three things fall out for free. **Double-colon rules disappear** — a `book` that depends
on gates the `tests` bundle may not have contributed just asks `"test" in REGISTRY`.
**Skip is a first-class outcome**, so `--strict` turns every skip into a failure and CI
can assert a gate actually measured something. And **help stops being a parser**: Typer
reads the same registry the runner uses, so the two cannot drift.

### Configuration

Six layers, lowest precedence first: dataclass defaults → `.rhiza/.env` (kept unchanged)
→ `rhiza.toml` → `[tool.rhiza-task]` in the language manifest (`Cargo.toml`, then
`pyproject.toml`) → `RHIZA_*` or bare make-style environment variables → command-line
flags.

```toml
[tool.rhiza-task]
source_folder = "src"
typechecker = "ty"
coverage_fail_under = 95
license_ignore_packages = ["docutils"]
```

`rhiza.toml` is the language-neutral file, and the only committed settings surface a Go
module can have — it has no manifest to hide a table in, and `.rhiza/.env` is now
developer-local. Settings sit at the top level; a `[tool.rhiza-task]` table is honoured
too and wins when both are present. It ranks *below* the manifest so that adding it to a
Python repo cannot silently outrank the table already there.

```toml
# rhiza.toml — a Go module or a Rust crate, or any repo that would rather not
# thread settings through a manifest
source_folder = "cmd"
coverage_fail_under = 95
```

An **empty value is unset** in the two string-valued layers: an empty
`RHIZA_CI_OS_MATRIX` leaves the layer below it alone rather than resolving to `""`. That
is make's `$(or ...)` rule, and the reusable workflows depend on it — `rhiza_ci.yml`
exports one for every caller and deliberately leaves it empty for consumers, whose own
`.rhiza/.env` is meant to answer.

The `+=` accumulators (`DEPTRY_FOLDERS`, `LICENSE_IGNORE_PACKAGES`, `RHIZA_CHECKS`) need no
successor: each was a bundle contributing something it owned, which a task body now
*derives* by asking whether the contributing task is registered. See `deps` and `license`
in `tasks/python.py`.

### Adding a task

Register a module under the `rhiza_task.tasks` entry-point group — the same mechanism the
built-ins use, so a project's own task is a first-class citizen rather than an override.

```python
from rhiza_task.spec import Guard, task
from rhiza_task.uv import uvx


@task("audit", "run the in-house audit", section="Quality", needs=("install",), guards=(Guard("source_folder"),))
def audit(cfg):
    """Audit the source tree."""
    uvx("my-auditor", cfg.source_folder, cwd=cfg.root)
```

The decorator has already done the registering, so the task is now reachable by the same
`lookup` the runner and the CLI use — no override, no second path:

```python
from rhiza_task.spec import lookup

spec = lookup("audit")
print(spec.key, "-", spec.help)
print(spec.needs, spec.guards[0].folder, spec.section)
```

```result
audit - run the in-house audit
('install',) source_folder Quality
```

That last pair of blocks is executed and diffed, not just rendered: `rhiza-task rhiza-test`
runs the `python` fences and compares their real output against the `result` block. The copy
of the same pair in the book's [Adding a Task](https://jebel-quant.github.io/rhiza-task/adding_a_task/)
page is diffed by `rhiza-task docs-examples`, which owns the docs tree for the same reason.
A change to `lookup`, to `Task`, or to the decorator above breaks a build rather than quietly
outdating a page.

A one-off that is only ever a make target goes in `local.mk` — but that file is in core's
`.gitignore`, so it holds developer-local targets only. A repo-owned target CI invokes
needs a committed home, and the repo's own `Makefile` is the only committed make surface
there is.

## Migrating from the make layer

1. Replace the synced make layer with direct `uvx rhiza-task` calls, or a repo-owned
   `Makefile` that forwards to them.
2. Exclude `.rhiza/make.d` and `.rhiza/rhiza.mk` in `template.yml`, exactly as
   `.rhiza/tests` already is.
3. **Relocate your own fragments first.** Deleting `rhiza.mk` removes the
   `-include .rhiza/make.d/*.mk` that was reaching them, so repo-owned fragments stop
   being loaded without anything saying so. They belong in `local.mk`, or in the
   `Makefile` if CI invokes them.

## Why not a Taskfile (or `just`)

Considered and rejected. go-task is a genuinely better make, and its **remote includes**
would even attack the same root problem — but that feature is experimental and
env-var-gated, and it would be the single load-bearing dependency of the whole multi-repo
task layer, whereas `uvx pkg@version` is boring and already used ~15 times per repo. The
three procedural recipes above would also stay embedded shell in YAML, improving the syntax
*around* the mess without removing it — and keeping the Windows problem.

`just` and `poe` don't apply: a Justfile or a noxfile still has to be copied into every
repo, which is the problem being deleted.

## Open questions

- **Python as a prerequisite for a Rust repo.** `rust.mk`/`go.mk` needed only make; the
  Rust and Go layers here are Python calling `cargo` and `go`, so a crate now needs uv to
  run its gates. That is the trade the whole package makes, and the layer where it costs
  the most.
- **Nested uv cost.** `uvx rhiza-task test` then internally `uv run --with pytest ...`.
  Cached this should be milliseconds; measure before rolling out widely.

## Documentation

The [book](https://jebel-quant.github.io/rhiza-task/) expands this README rather than
repeating it:

| page | what is there that is not here |
|---|---|
| [Getting Started](https://jebel-quant.github.io/rhiza-task/getting_started/) | exit-code semantics, the `Makefile` shim, when `--strict` is the right setting |
| [Tasks](https://jebel-quant.github.io/rhiza-task/tasks/) | every task in all three layers, what each guards on, and why two sit outside `all` |
| [Configuration](https://jebel-quant.github.io/rhiza-task/configuration/) | all six layers, and every setting with its default |
| [Language Layers](https://jebel-quant.github.io/rhiza-task/layers/) | polyglot repos, `rust:test`, and the three contracts every layer must honour |
| [Adding a Task](https://jebel-quant.github.io/rhiza-task/adding_a_task/) | guards, outcomes, and which of the four provisioning forms to reach for |
| [Migrating from make](https://jebel-quant.github.io/rhiza-task/migration/) | the three steps, and the fragment-relocation trap that silently drops targets |
| [FAQ](https://jebel-quant.github.io/rhiza-task/faq/) | the failure modes, and what each one actually means |
| [Design](https://jebel-quant.github.io/rhiza-task/design/) | where the evidence came from — jointview, the repo the comments cite by name — and why exactly three recipes resisted the declarative form |
| [API Reference](https://jebel-quant.github.io/rhiza-task/api/) | the modules, generated from the docstrings that carry the reasoning |
| [Paper](https://jebel-quant.github.io/rhiza-task/paper/paper.pdf) | the argument written up long-form, compiled by `rhiza-task paper` into the book |

It is built by the `book` task itself — `mkdocs.yml` at the root is what turns that task
from a skip into a build — and published by `.github/workflows/rhiza_book.yml` on every
push, to Pages from `main` only.

```bash
uv run rhiza-task book     # build into _book/
uv run rhiza-task serve    # build, then serve on http://localhost:8000
```

## Development

```bash
uv sync --all-groups
uv run pytest              # the fast inner loop
uv run rhiza-task all      # every gate `all` names, as CI runs them
```

`uv run rhiza-task all` is the pre-push check, and it is what `ci.yml`'s `gates` job runs
— the pre-commit hooks, deptry, the 100% coverage floor, interrogate, bandit, the copyleft
scan, `ty` and `rhiza-test` on top of the suite. That job then names the two gates that sit
outside `all` on purpose, `complexity` and `docs-examples`, because adding either as an
`all` prerequisite would fail builds in consumer repositories that changed nothing. So the
full pre-push sequence is three commands:

```bash
uv run rhiza-task all
uv run rhiza-task complexity
uv run rhiza-task docs-examples
```

`uv run pytest` alone is strictly weaker than the check that will fail a pull request, so a
green pytest is not yet a green PR. This repo exists to provide that aggregate, so it gates
itself with it.

The examples in this file are checked, not decorative, and three different gates own three
different sets of them:

| examples | gate |
|---|---|
| this README's `python` fences, diffed against its `result` block | `rhiza-task rhiza-test`, via pytest-rhiza's `test_readme_validation` |
| every fence under `docs/` — `python` compiled, `bash` parsed, `result` diffed | `rhiza-task docs-examples` |
| the 39 `>>>` examples in `config.py`, `runner.py` and `spec.py` | `tests/test_doctests.py`, under plain `pytest` |

`rhiza-test` is not what runs the docstring examples, which is worth knowing because it
reads as though it should: pytest-rhiza's `test_docstrings` asks whether a docstring exists
and is well-formed, and `interrogate` asks the same presence question at 100%. Neither
evaluates a `>>>`. All three gates need the project environment, since the examples import
the package: `uv run` rather than a bare interpreter.

No test in the suite runs uv. Every task test patches `uv.py`'s entry points — `uv`,
`uvx`, `uv_run`, `tool`, and `capture` for the recipe that needs stdout back — and asserts
on the argument vector that would have been executed, which is exactly what the make recipes
expressed in `$$`-escaped shell and could not assert. No total is given here on purpose:
`uv.py`'s public functions are the authority, and a count in prose is read back by nothing.

[`CONTRIBUTING.md`](CONTRIBUTING.md) is the short version of all of this, and the place to
start: how to run the gates without a `Makefile`, what the gates will hold you to, and the
two invariants nothing checks. [`CLAUDE.md`](CLAUDE.md) carries the rest — the layering
invariant the import graph holds by discipline, why the 100% coverage floor is load-bearing
rather than decorative, and the house rule on comments. Read one of the two before a first
change; read `CLAUDE.md` before a large one.
