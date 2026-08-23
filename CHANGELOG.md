# Changelog

## [1.3.0] - 2026-08-23

### ⚠️ Upgrade note

**`rhiza-task paper` compiles with [tectonic](https://tectonic-typesetting.github.io/), and
no longer with a TeX distribution's driver.** A machine that could build the paper before and
has no tectonic will now report `skipped  paper  tectonic not found` instead of producing a
PDF — a machine that previously succeeded, so this is breaking even though the gate reports it
as a skip rather than a failure. [Install tectonic](https://tectonic-typesetting.github.io/en-US/install.html)
— `brew install tectonic`, `cargo install tectonic`, or a release binary; note that Ubuntu does
not package it, which is what CI here installs a pinned tarball for — or pin `rhiza-task<1.3`.

What the trade buys, and what it costs:

- **One binary instead of a distribution plus a package list.** The old install asked each
  workflow to name the TeX packages the document happened to cite, and this repository carried
  two such lists — one per workflow, compiling the same document, with nothing checking that
  they agreed. tectonic resolves what the document cites from its own bundle, so there is no
  list to diverge.
- **The argument vector is the document and one flag.** Convergence and bibtex are tectonic's
  own loop rather than a driver's, and there is no interaction mode to pin because tectonic
  never prompts. `--keep-logs` is the flag, kept because tectonic writes only the PDF by
  default and the log is what you upload when a CI compile fails.
- **A cold cache needs the network**, where a provisioned distribution did not. The cache is
  per-machine and survives between runs, so it is a first-run cost; both workflows here now
  cache it.

**`rhiza-task paper-clean` no longer guards on a tool, and no longer sweeps by extension.**
tectonic has no clean subcommand, so the task deletes the artifacts itself — which makes it the
one task in the section that works on a machine that cannot build the paper at all. It removes
the PDF and auxiliary files **belonging to the folder's top-level `.tex` documents**, so a
committed `diagram.pdf` with no `diagram.tex` now survives a clean that would previously have
been the driver's business. If you relied on `paper-clean` clearing every `.pdf` in the folder,
it no longer does.

**`rhiza-task doctor` no longer probes for GNU make**, and has no optional tier left. It names
uv and git, and a miss is a failure. `doctor.mk` required make because the whole task layer was
make; this package has none, and nothing in the registry needs it. Every other binary the
package reaches for — docker, gh, git-lfs, tectonic, marp — is a `Guard` on the task that wraps
it and reports itself on that task's `skipped` line with an install URL, which is where an
optional prerequisite belongs. `Tool.required` is gone from `rhiza_task.tasks.doctor` with it.

### 🚀 Features

- Compile the paper with tectonic, and stop probing for make

### 🐛 Bug Fixes

- *(ci)* Install tectonic from a pinned release, not from apt

### 📚 Documentation

- Add a total-downloads badge, and record what it counts

## [1.2.0] - 2026-08-22

### ⚠️ Upgrade note

**The `mutation` task is gone.** `rhiza-task mutation` now exits with the CLI's unknown-task
error, and `make mutation` falls through the shim's `%:` catch-all to the same error. That is
the intended outcome rather than a regression: the ecosystem no longer offers a mutation gate.

It is a removal rather than a port, and the reasons are worth stating because they decide
what a consumer should do instead:

- **The task had been broken since mutmut 3 shipped.** Its vector passed
  `--paths-to-mutate`, `--tests-dir` and the `html` subcommand, all three of which mutmut 3
  removed, and it provisioned `mutmut` unpinned — so the breakage was time-triggered rather
  than version-triggered, and reached every consumer at every pin with no sync involved.
- **The replacement is not a flag rename.** mutmut 3 takes source paths from a
  `[tool.mutmut]` table and offers no CLI path for them, so a port means either requiring
  every consumer to declare that table or writing into their `pyproject.toml` — and the HTML
  report the task relocated no longer exists, its successor being `export-cicd-stats` JSON.
- **Nothing invoked it.** No `all` named it, and the guarded upstream workflow was unset in
  every repository we could see, so the port would have been paid for here and verified
  nowhere.

A repository that wants mutation testing runs mutmut directly: declare
`[tool.mutmut] source_paths = [...]` and run `uvx --from mutmut mutmut run`. Nothing in
rhiza-task reads or writes that table.

This is a **minor** and not a major, which is a deliberate call rather than an oversight:
the generated list below marks the removal `[**breaking**]`, and it is one — a task name
that resolved now errors. It landed as a minor because the gate had not worked since mutmut
3 shipped, so no consumer had a working invocation to lose. If you pin `rhiza-task` and
invoke `mutation`, this release is breaking for you regardless of its number.

### 🚀 Features

- Let an empty pytest_rhiza run the checks from the project itself
- [**breaking**] Remove the mutation task, and every trace of it

### 🐛 Bug Fixes

- Leave no partial report when mutation's relocation fails

### 🚜 Refactor

- Decompose Config.__post_init__ into per-setting validators

### 📚 Documentation

- Diff layers.md's shadowing example, and correct the radon claim
- Correct three stale figures in CLAUDE.md's House style section
- Quote only figures that cannot drift, and say which those are
- Stop giving a total for uv.py's entry points, and name capture

### ⚙️ Miscellaneous Tasks

- Run the testing-extras vectors against a real toolchain

## [1.1.0] - 2026-08-21

### ⚠️ Upgrade note

`docs-examples` checks more than it did at v1.0.0, so a repository whose documentation passed
that gate can see it go red on upgrade without having changed anything. That is the gate
working, not a regression. Three changes compound:

- **`toml` and `yaml` fences are parsed**, where they were previously counted as uncheckable.
  A malformed configuration example under the docs folder now fails.
- **`README.md`'s `toml` and `yaml` fences are checked too.** Its `python` and `bash` fences
  remain pytest-rhiza's, under `rhiza-test`, so no fence is checked twice.
- **A yaml checker that crashes now fails** instead of reporting "parser unavailable", which
  passed.

To see what a repository would report before upgrading, run `uvx rhiza-task@1.1.0 docs-examples`
against it.

This note is here because the three changes landed as `fix:` and `refactor:` commits, so the
generated sections below file them where a reader does not look for a behaviour change. The
convention that a gate getting stricter is a `feat:` was written down after the fact, in
`CLAUDE.md`.


### 🚀 Features

- *(book)* Add the mkdocs book, built by the existing book task
- Gate the docs tree's fenced examples, and correct four drifted figures

### 🐛 Bug Fixes

- *(bumpversion)* Relock, and carry uv.lock through the bump
- *(runner)* Propagate the failing task's own exit status
- Contain *_folder settings, record the unwired gates, fold a deferred import
- *(security)* Stop the security gate warning about this repo's own suppressions
- Reject a --root that is not an existing directory
- Stop publishing local build paths in the book's reports
- Close the seven findings from the quality run
- Decompose Guard to A (5), and retire the orphaned Scorecard comments
- Close the three findings from the quality run
- Close the three regressions the quality re-run found
- Check README's data fences, which nothing was checking
- Bump the version pins in docs/, not just README.md
- Let a release PR go green by deselecting the tag-version check

### 💼 Other

- Add a pre-commit config, so fmt measures something
- Raise the coverage floor to 100, and make the stronger claim it unlocks
- Automate the pins, test the parsing, and write down the invariants
- Bump the actions group with 3 updates
- Pin this repo's typechecker rather than inheriting the default
- Run both typecheckers, and fix the one finding mypy --strict adds

### 🚜 Refactor

- Promote the name normaliser, and record why Python's security gate differs
- Extract the fence checker into tasks/fences.py
- Promote task_modules, and say where the layering rules stop

### 📚 Documentation

- *(readme)* Add five badges and cut the prose back by a fifth
- Make the examples executable, and declare the layout opt-out
- Say why the four C-complexity blocks are shaped as they are
- Link the book and widen the package description
- *(ci)* Correct the comments the pre-commit config made stale
- *(design)* Introduce jointview, so the comments citing it have an antecedent
- *(build)* Fix the stale premise about how the opt-out reason spells its floor
- Give Config.__post_init__'s growth rule a ceiling
- Add a security policy
- Correct the --strict answer, which had gone stale twice
- Link upstream's decision records from the design page
- Add docs/paper/paper.tex describing the package
- Publish the compiled paper in the mkdocs book
- Correct drifted figures and split the three example gates
- Describe the toml and yaml fence checks in tasks.md
- Record why fences.py is the largest module in src/
- Make commit types release evidence, and flag the pending minor
- CLAUDE.md still said four entry points

### 🧪 Testing

- Assert exit-status and outcome contracts, not only argument vectors
- Assert the propagated exit code, not the collapsed one
- Cover the last 37 statements, without touching src
- Execute the 39 doctests, so the documented contracts are gated
- Cover the two defensive except branches, retiring their pragmas
- Assert the installed version matches the declared one
- Stub capture too, and assert the hermeticity guarantee

### ⚙️ Miscellaneous Tasks

- *(bumpversion)* Keep the README's `rhiza-task@` pin in step
- *(release)* Re-sync from rhiza v1.4.2, keeping the local trim
- Adopt rhiza's scorecard, codeql and quality-review stubs
- Test both ends of the declared dependency range
- Enforce ruff.toml in a gate CI runs
- Lift the book and paper workflows from rhiza
- Invoke the CLI end-to-end on every matrix leg, not just ubuntu
- SHA-pin the two reusable workflow calls
- Add CODEOWNERS and the branch and tag ruleset definitions
- Add an editorconfig
- Close the four gaps /quality found, and gate the complexity ceiling
- Drop the two upstream workflow delegations, and correct CLAUDE.md
- Install TeX Live on the ref the book deploys, fixing the paper 404
# Changelog

## [1.0.0] - 2026-08-20

### 🚀 Features

- *(shim)* Spell out the named tasks and declare them phony
- *(shim)* Name paper and presentation too
- *(shim)* Drop doctor from the named set
- *(shim)* List local.mk's own targets in `make help`
- *(shim)* Provision `uv` alongside `uvx`
- *(cli)* [**breaking**] Remove the Makefile shim

### 🚜 Refactor

- *(shim)* Name the goal the catch-all forwards
- *(shim)* Make FORCE the phony mechanism, drop the named list

### 📚 Documentation

- *(shim)* Cut the shim's comments back, and say what belongs in it

### ⚙️ Miscellaneous Tasks

- Untrack the root Makefile again

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
- `tests` extras: benchmark, hypothesis-test, stress
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
