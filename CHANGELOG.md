# Changelog

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
