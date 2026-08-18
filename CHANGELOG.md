# Changelog

## [Unreleased]

### 🐛 Bug Fixes

- *(shim)* Bootstrap uv when it is absent, restoring the make layer's "`make <anything>`
  works on a bare runner" contract (#8). `bootstrap.mk` provisioned uv itself; a shim whose
  only recipe was `uvx ...` needed uv already on PATH, which turned rhiza's `pre-commit`
  required check red on a runner with no `astral-sh/setup-uv` step.

## [0.1.2] - 2026-08-18

### 🐛 Bug Fixes

- *(config)* Treat an empty setting as unset, and floor the CI OS matrix

## [0.1.1] - 2026-08-18

## [0.1.0] - unreleased

Initial extraction. Replaces `.rhiza/rhiza.mk` and `.rhiza/make.d/*.mk` (1023 synced
lines) with a pinned CLI, on the same principle as `pytest-rhiza` replacing
`.rhiza/tests`.

- `core` + `python-core` gate set: install, test, typecheck, security, deps, license,
  docs-coverage, fmt, semgrep, rhiza-test, todos, clean, doctor, all
- `tests` extras: benchmark, hypothesis-test, stress, mutation
- `book` / `marimo`: book, serve, marimo, marimo-validate
- Not ported: `github.mk`'s seven `gh` wrappers (use `gh` directly), `install-uv`
  (it cannot be a task, since it provisions the runtime every task runs under — the shim
  keeps it as a file target)

### Deliberate differences from the make layer

Four places where the port does not reproduce the recipe, each found by running the gates
against this package:

- **`security` passes `--ini .bandit` only when that file exists.** python.mk passes it
  unconditionally, and bandit treats a missing ini as a usage error — so a project without
  one got a red security gate reporting a configuration problem.
- **`doctor` treats GNU make as optional.** doctor.mk requires it, which was honest while
  the task layer *was* make. Only the shim needs it now.
- **The shim declares `local.mk: ;`.** An included makefile is a target make tries to remake
  first, and the catch-all routed that attempt to the CLI — so every invocation began with
  `unknown task: local.mk`.
- **`shim` writes to stdout, not through rich.** Console output word-wraps and expands
  tabs; a Makefile survives neither, so `rhiza-task shim > Makefile` produced a broken file.
