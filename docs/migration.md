---
icon: material/swap-horizontal
---

# Migrating from the make layer

## What is being deleted

| before, per consumer repo | after |
|---|---|
| `.rhiza/rhiza.mk` — 200 lines, synced | *gone* |
| `.rhiza/make.d/*.mk` — 1023 lines in 15 files, synced | *gone* |
| `exclude:` entries in `template.yml`, because a deletion alone is undone by the next sync | not needed |
| targets shadowed in the repo `Makefile` | `[tool.rhiza-task]` |
| ~40 lines of GNU-make guard and Windows POSIX-shell probe | gone — no make, no shell |
| `install-uv` — 30 lines of `bootstrap.mk` shell | gone — `uvx` provisions the runtime |
| `Makefile` | repo-owned, if a repo wants one |

Task names are **unchanged**, which is what makes this a mechanical migration rather than a
rewrite.

## The three steps

### 1. Replace the synced layer with direct calls

Either invoke the CLI directly in CI and locally:

```bash
uvx rhiza-task@1.4.1 all --strict
```

…or keep `make test` working with a repo-owned `Makefile` that forwards — one rule:

```makefile
# Repo-owned. Nothing syncs this file, and nothing overwrites it.
RHIZA_TASK := rhiza-task@1.4.1

.PHONY: test fmt typecheck all
test fmt typecheck all:
	uvx $(RHIZA_TASK) $@
```

### 2. Exclude the make layer in `template.yml`

Exactly as `.rhiza/tests` already is:

```yaml
exclude:
  - .rhiza/make.d
  - .rhiza/rhiza.mk
```

A deletion alone is undone by the next sync, which is why the exclusion — not the
`rm` — is the step that matters.

### 3. Relocate your own fragments **first**

!!! danger "This is the step that bites"
    Deleting `rhiza.mk` removes the `-include .rhiza/make.d/*.mk` that was reaching your
    own fragments, so **repo-owned fragments stop being loaded without anything saying
    so**. No error, no warning — the targets simply are not there any more.

Do this before step 1. Each repo-owned fragment goes to one of two places:

| the fragment holds… | it belongs in… |
|---|---|
| developer-local convenience targets | `local.mk` (gitignored) |
| anything CI invokes | the repository's own `Makefile` (committed) |

## Moving your settings

Whatever you used to set by editing a synced `.mk` file or shadowing a target now goes in
one table:

=== "before"

    ```makefile
    # in the repo Makefile, shadowing the synced target
    COVERAGE_FAIL_UNDER := 95
    SOURCE_FOLDER := src
    TYPECHECKER := ty
    LICENSE_IGNORE_PACKAGES += docutils
    ```

=== "after"

    ```toml
    [tool.rhiza-task]
    coverage_fail_under = 95
    source_folder = "src"
    typechecker = "ty"
    license_ignore_packages = ["docutils"]
    ```

Field names are the lowercased make variables, so the mapping stays one-to-one and
greppable. A Go module — which has no manifest to hide a table in — uses `rhiza.toml`
instead. See [Configuration](configuration.md).

!!! warning "`.rhiza/.env` is now developer-local"
    rhiza no longer ships the `.rhiza/.gitignore` whose entire content was the `!.env`
    negation keeping that file tracked. It now falls under the shipped `.gitignore`'s
    `.env` rule, so **a CI checkout never contains it**. Anything CI depends on must move
    to `rhiza.toml` or the manifest table.

## The `+=` accumulators

`DEPTRY_FOLDERS`, `LICENSE_IGNORE_PACKAGES` and `RHIZA_CHECKS` have **no successor, and
need none**. Each was a bundle contributing something it owned, which a task body now
*derives* by asking whether the contributing task is registered.

If you accumulated onto one of these from your own fragment, the equivalent is either the
plain list setting (`license_ignore_packages`) or [your own task](adding_a_task.md).

## Two behaviour changes to know about

Both are deliberate, and both are recorded in their module docstring — the convention for
any change that is not a straight port:

| task | change |
|---|---|
| `lfs-install` | configures the repository and **reports** how to install the binary, rather than downloading one |
| `presentation` | reaches Marp through `npx --yes` |
| `paper` | compiles with [tectonic](https://tectonic-typesetting.github.io/) — one binary, no TeX-distribution package list, but a cold cache needs the network |
| `paper-clean` | deletes the artifacts in Python rather than delegating, so it needs no toolchain and keeps a committed PDF that no `.tex` claims |

And one that is not a change so much as a name losing its special status: `paper.mk`
preferred a file called `basanos.tex` — one downstream repository's paper, named in a
template every consumer synced. It is now `main.tex`, then `paper.tex`, then alphabetical
order.

## After the migration

Add `--strict` in CI once the migration is quiet:

```bash
uvx rhiza-task@1.4.1 all --strict
```

A skip in a consumer repository means a gate lost its subject, and `--strict` is what turns
that from a yellow line nobody reads into a red build. Check the run summary first — some
skips will be correct for your repository, and those are the ones to fix by adding the
missing config, not by dropping the flag.

## Why not a Taskfile (or `just`)

Considered and rejected.

**go-task** is a genuinely better make, and its **remote includes** would even attack the
same root problem. But that feature is experimental and env-var-gated, and it would become
the single load-bearing dependency of the whole multi-repo task layer — whereas
`uvx pkg@version` is boring and already used around fifteen times per repository. The three
procedural recipes would also stay embedded shell inside YAML, improving the syntax
*around* the mess without removing it, and keeping the Windows problem.

**`just` and `poe`** do not apply: a Justfile or a noxfile still has to be copied into
every repository, which is the problem being deleted.

## Open questions

- **Python as a prerequisite for a Rust repo.** `rust.mk` and `go.mk` needed only make;
  the Rust and Go layers here are Python calling `cargo` and `go`, so a crate now needs uv
  to run its gates. That is the trade the whole package makes, and the layer where it costs
  the most.
- **Nested uv cost.** `uvx rhiza-task test` then internally `uv run --with pytest ...`.
  Cached this should be milliseconds; measure before rolling out widely.
