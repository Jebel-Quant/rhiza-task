---
icon: material/puzzle-plus
---

# Adding a Task

Register a module under the `rhiza_task.tasks` entry-point group — **the same mechanism the
built-ins use**, so a project's own task is a first-class citizen rather than an override.

There is one mechanism, not a built-in path and a plugin path. This is the replacement for
make's `-include local.mk`.

## Declare the entry point

```toml
# your project's pyproject.toml
[project.entry-points."rhiza_task.tasks"]
acme = "acme_tasks.gates"
```

## Write the task

```python
from rhiza_task.spec import Guard, task
from rhiza_task.uv import uvx


@task("audit", "run the in-house audit", section="Quality", needs=("install",), guards=(Guard("source_folder"),))
def audit(cfg):
    """Audit the source tree."""
    uvx("my-auditor", cfg.source_folder, cwd=cfg.root)
```

That is the whole thing. The decorator has already done the registering, so the task is
reachable by the same `lookup` the runner and the CLI use:

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

!!! tip "That example is executed — here as well as in the README"
    The pair above is **run and diffed against its `result` block**, by `rhiza-task
    docs-examples` for this page and by `rhiza-task rhiza-test` for the copy in the
    repository's `README.md`. A change to `lookup`, to `Task`, or to the decorator breaks
    both rather than quietly outdating either.

    It used to be only the README: this page said so, and said it was "a copy for reading".
    That is what `docs-examples` was added to fix — every `python` and `bash` fence under
    `docs/` is now checked too, so a stale command here fails a build instead of waiting
    for a newcomer to run it.

## `@task` parameters

| parameter | what |
|---|---|
| `name` | the CLI name — `rhiza-task audit` |
| `help` | the one-line description, shown by `list` and `--help` |
| `section` | the `list` grouping (`Python`, `Quality`, `Book`, or your own) |
| `needs` | prerequisites, run first and deduplicated across one invocation |
| `guards` | conditions that turn the run into a `skipped` instead of a failure |

## Guards

A guard is the first of the three parts every recipe has. Two forms:

=== "A folder must exist"

    ```python
    guards = (Guard("source_folder"),)
    ```

    Names a **setting**, not a path — so it follows the six-layer resolution and a
    consumer can point it elsewhere.

=== "A binary must be on PATH"

    ```python
    Guard(tool="tectonic", reason="tectonic not found; install it")
    ```

    The `reason` is what the user sees on the `skipped` line, so make it actionable.

When a guard fails the task reports:

```text
 skipped  audit  source_folder 'src' not found
```

…and `--strict` turns that into a failure.

## Reaching your tool

Pick the form that matches what the tool needs — the distinction is real and worth getting
right:

| use | when | example |
|---|---|---|
| `uvx("my-auditor", ...)` | an isolated one-shot tool | linters, scanners, formatters |
| `uv_run("pytest", ..., withs=("pytest-cov",))` | the tool **imports your project's code** | pytest, mypy, interrogate |
| `tool("cargo", "clippy", ...)` | a toolchain binary already on `PATH` | `cargo`, `go`, `tectonic`, `npx` |
| `uv("sync", "--frozen", ...)` | uv itself | `venv`, `sync`, `lock --check` |

All four take `cwd=` and echo the command they run, so `$ cargo clippy` is printed the same
way `$ uvx bandit` is.

```python
from rhiza_task.uv import tool, uv_run, uvx


@task("audit", "run the in-house audit", section="Quality", needs=("install",))
def audit(cfg):
    """Audit the source tree against the project's own dependencies."""
    uv_run("my-auditor", cfg.source_folder, cwd=cfg.root, withs=("my-auditor",))
```

## Signalling an outcome

Raise, rather than returning a status:

```python
from rhiza_task.spec import Failed, Skip


@task("audit", "run the in-house audit", section="Quality")
def audit(cfg):
    """Audit every rule file, if there are any."""
    rules = sorted((cfg.root / "rules").glob("*.yml"))
    if not rules:
        raise Skip("no rule files")

    failures = [r.name for r in rules if not _check(r)]
    if failures:
        raise Failed(1, f"{len(failures)} rule(s) failed: {', '.join(failures)}")
```

| raise | means |
|---|---|
| `Skip(reason)` | nothing to measure — yellow, unless `--strict` |
| `Failed(code, message)` | a real failure, with the exit status to propagate |
| *nothing* | success |

Pass the tool's **own** exit code to `Failed` where you have one. `run` propagates the
first failing task's status rather than collapsing everything to 1, and that is what a
consumer's CI reads.

## A broken module will not take the gates down

`load_tasks()` reports an import failure and moves on:

```text
could not load task module acme: ModuleNotFoundError: No module named 'acme_internal'
```

Your gate stops running, and everyone else's keeps working.

## When *not* to use an entry point

A one-off that is only ever a make target belongs in `local.mk` — but that file is in
core's `.gitignore`, so it holds **developer-local** targets only.

A repo-owned target that **CI invokes** needs a committed home, and the repository's own
`Makefile` is the only committed make surface there is:

```makefile
# Repo-owned. Nothing syncs this file.
.PHONY: release-notes
release-notes:
	uv run python tools/release_notes.py
```

Use an entry point when the task is a *gate* — something `all` should include, something
another repository might want, something that deserves a guard and a skip. Use the
`Makefile` when it is a local convenience.
