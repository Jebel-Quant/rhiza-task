---
icon: material/rocket-launch
---

# Getting Started

## Nothing to install

`uvx` provisions the CLI per invocation, so there is no install step and no entry in your
project's dependencies:

```bash
uvx rhiza-task@1.5.0 list          # what is available
uvx rhiza-task@1.5.0 all           # every gate, as CI runs them
uvx rhiza-task@1.5.0 test --strict # fail rather than skip when a gate measures nothing
```

!!! tip "Pin the version"
    `rhiza-task@1.5.0` rather than `rhiza-task`. The pin is the whole point of the
    package — an unpinned task layer is a layer that can change under a green pull
    request without a commit touching your repository. Bumping it is then a deliberate
    commit that carries whatever the new version wants.

The only prerequisite is [uv](https://github.com/astral-sh/uv). A CI runner that ships no
uv adds an `astral-sh/setup-uv` step; there is deliberately no `install-uv` task, because
it would have to provision the runtime that every task already runs under.

## The first run

Start by asking what this repository has:

```bash
uvx rhiza-task@1.5.0 list
```

```text
 task                section         needs                 does
 all                 Python          fmt deps test         run every gate, as
                                     docs-coverage         CI does
                                     security license
                                     typecheck rhiza-test
 coverage            Python          install               measure coverage and
                                                           write
                                                           _tests/coverage.xml
 deps                Python          install               run deptry over the
                                                           contributed folders
 …
```

The listing is generated from the same registry the runner uses, so help and behaviour
cannot drift. By default it shows **this** repository's language layers plus the
language-neutral tasks — a Go module is not helped by being shown `benchmark` and
`marimo-validate`. Add `--all` for the question the make layer could not answer: what the
other layers call things.

Then run the aggregate:

```bash
uvx rhiza-task@1.5.0 all
```

## `run`, and the bare shorthand

`rhiza-task <task>` is shorthand for `rhiza-task run <task>`:

```bash
uvx rhiza-task@1.5.0 test          # shorthand
uvx rhiza-task@1.5.0 run test      # the same thing
uvx rhiza-task@1.5.0 run fmt test  # several, in order, prerequisites deduplicated
```

This is a compatibility contract rather than sugar. The reusable workflows and a
repo-owned forwarding `Makefile` both invoke `rhiza-task test`, and a consumer's muscle
memory is `make test`; requiring the extra word would gain nothing.

Because of that rewrite, `rhiza-task --help` lists only the five real subcommands —
`list`, `print`, `run`, `ci-os-matrix` and `version`. The tasks themselves are arguments to
`run`, and `list` is how you enumerate them.

| flag | on | what |
|---|---|---|
| `--strict` | `run` | treat a skipped gate as a failure |
| `--root <path>` | `run` | operate on another repository |
| `--all` | `list` | include the other languages' layers |

## Skip, and why `--strict` exists

A task whose subject is absent **skips** rather than fails. `fmt` with no
`.pre-commit-config.yaml`, `marimo-validate` with no notebooks, `paper` with no LaTeX
folder — each reports `skipped` and a reason:

```text
 skipped  fmt  no .pre-commit-config.yaml
```

Skip being a first-class outcome is what lets one aggregate serve repositories that have
different bundles installed. But a skip is also how a gate silently stops measuring
anything — so `--strict` turns every skip into a failure:

```bash
uvx rhiza-task@1.5.0 all --strict
```

!!! note "Which one belongs in your CI"
    `--strict` is the right setting in a **consumer** repository, where a skip means a
    gate lost its subject and nobody noticed. This repository runs its own dogfood job
    *without* it, because several of its gates skip on purpose — asserting otherwise
    would only assert that it is something it is not.

## Exit codes

`run` propagates the **first failing task's own exit status** rather than collapsing
everything to 1 — pytest's 2 or 4, `cargo`'s 101 — falling back to 1 when the tool has
none. A usage error is 2.

| status | meaning |
|---|---|
| `0` | everything passed (or skipped, without `--strict`) |
| `2` | usage error — unknown task, invalid configuration — *or* a task that itself exited 2 |
| *other* | the first failing task's own code |

A usage error and a task that exited 2 therefore share a status. The run summary printed
above the exit distinguishes them, and the alternative — discarding the code — is the one
thing every consumer's CI actually wants.

## The pre-push loop

For this repository, and for any project that adopts the same layout:

```bash
uv sync --all-groups
uv run pytest              # the fast inner loop
uv run rhiza-task all      # run before pushing: every gate, as CI runs them
```

`uv run pytest` alone is strictly weaker than the check that will fail a pull request:
`all` adds deptry, the coverage floor, interrogate, bandit, the copyleft scan, the
typechecker and `rhiza-test` on top of the suite. A green pytest is not yet a green PR.

## Keeping `make test` working

Task names are unchanged from the retired make layer, so a repository that wants
`make test` to keep working owns that `Makefile` itself and forwards each target — one
rule:

```makefile
# Repo-owned. Nothing syncs this file, and nothing overwrites it.
RHIZA_TASK := rhiza-task@1.5.0

.PHONY: test fmt typecheck all
test fmt typecheck all:
	uvx $(RHIZA_TASK) $@
```

That `Makefile` is now yours: a repo-owned target CI invokes has a committed home, and no
sync will shadow it.

## In CI

The gates run as one step, and `ci-os-matrix` feeds a GitHub Actions matrix input from the
same configuration the tasks read:

```yaml
- uses: astral-sh/setup-uv@v10
  with:
    version: "0.11.16"
    enable-cache: true

- name: Gates
  run: uvx rhiza-task@1.5.0 all --strict
```

```bash
uvx rhiza-task@1.5.0 ci-os-matrix
```

```text
["ubuntu-latest"]
```

Set `ci_os_matrix` to widen it — see [Configuration](configuration.md).

## Next

- [Tasks](tasks.md) — the full catalogue, and what each one guards on
- [Configuration](configuration.md) — the six layers, and every setting
- [Language Layers](layers.md) — polyglot repositories, and `rhiza-task rust:test`
