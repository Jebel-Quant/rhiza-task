# Changelog

## [0.3.1] - 2026-08-19

### 🐛 Bug Fixes

- *(book)* Provision mkdocstrings, which the book bundle's own config requires

## [0.3.0] - 2026-08-19

### 🚀 Features

- *(tasks)* Tasks for the five bundles `.rhiza/make.d` could not retire (#22). One module
  per bundle — `github`, `docker`, `lfs`, `paper`, `presentation` — covering all eighteen
  targets under their original names, so `make view-prs` keeps working through the shim's
  catch-all. This reverses 0.1.0's "not ported: github.mk's seven `gh` wrappers (use `gh`
  directly)": `github` is in rhiza's `github-project` profile, so leaving it out kept the
  whole `.rhiza/make.d/` folder alive for the flagship profile.
- *(spec)* `Guard(tool=...)`, which is what `require-gh` and `require-marp` were — a target
  whose entire body is `command -v X >/dev/null || exit 1`, declared as a prerequisite of
  every helper. A missing tool is a **skip** carrying the install hint rather than the
  fragment's hard exit, because nothing in these five bundles is a gate; `--strict` gives
  the failure back to a caller who wants it.
- *(config)* `docker_folder`, `docker_image`, `paper_folder`, `presentation_file` and
  `marp_package`. `docker.mk`'s `DOCKER_FOLDER` was a `:=` — not configurable at all — and
  `PRESENTATION.md` was a literal.

### Deliberate differences from the fragments

Three, each recorded in its module docstring:

- **`lfs-install` configures the repository; it no longer downloads a binary.** lfs.mk's
  macOS branch queried the releases API, unzipped git-lfs into `.local/bin`, and ran
  `git-lfs install` — but `.local/bin` is not on PATH afterwards and every other target
  invokes the bare command, so `lfs-install` followed by `lfs-pull` still failed on a
  machine that had none. The `git lfs install` at the end is the part that worked and is
  what survives; the binary is now reported per platform, not fetched. Consumers who
  relied on the Linux `sudo apt-get` branch need one line of their own.
- **Marp comes from `npx --yes`, not `npm install -g`.** `require-marp` did not check for
  Marp, it installed it globally as a side effect of typing `make presentation`. `npx`
  keeps the convenience without mutating the machine; Marp on PATH still wins, and
  `marp_package` pins the spec.
- **`paper` no longer prefers `basanos.tex`** — one downstream repository's filename,
  hard-coded in a template every consumer syncs. Now `main.tex`, then `paper.tex`, then
  alphabetical, which is identical behaviour for a folder holding one `.tex`.

Dropped with the fragments: `require-gh`/`gh-install`, the same question spelled twice
because make cannot say it once; `require-marp`; and `FORGE_TYPE`, which github.mk computed
at parse time and no target ever read. The six gh templates are carried over byte-identical
— reproducing `timeago` and gh's colour handling in Python would be a worse table.

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
