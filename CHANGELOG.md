# Changelog

## [0.2.0] - 2026-08-18

### 🚀 Features

- *(tasks)* Add the Rust and Go language layers
- *(config)* Add rhiza.toml, and read the table from Cargo.toml too
- *(tasks)* Add coverage to all three layers

### 🐛 Bug Fixes

- *(config)* Split a string reaching a tuple field, and stop print eating markup
- *(config)* Let RHIZA_CHECKS name the rhiza_checks field
- *(shim)* Bootstrap uv when it is absent
- *(go)* Pass the coverage profile path with forward slashes

### 📚 Documentation

- *(changelog)* Note the Rust and Go layers

### 🎨 Styling

- *(config)* Restore the import order two merges shuffled

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
