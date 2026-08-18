# Changelog

## [Unreleased]

### 🚀 Features

- *(tasks)* Add `coverage` to all three layers (#10). It is named in the gate-parity
  contract and writes `_tests/coverage.xml`, the path `book`'s badge step reads and CI
  uploads. Python gains the name it was missing -- the flags its `test` already carried --
  Rust gets `cargo llvm-cov --cobertura`, and Go gets the profile, the Cobertura
  conversion, and the floor check `go test` has no flag for.
- *(tasks)* Add the Rust and Go language layers (#9). `rhiza_task/tasks/` served one of
  rhiza's three layers, so `core` could not drop the make layer without leaving `rust-core`
  and `go-core` with no targets at all. Tasks now carry a layer, the registry is keyed
  `layer:name`, and a bare `test` resolves against the manifests a repository actually has.
  `RHIZA_CHECKS` is derived from the layer set, replacing the `+=` accumulator each
  language fragment used.

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
  (running under `uvx` means uv is already present)

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
