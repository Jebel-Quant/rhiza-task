---
icon: material/format-list-bulleted
---

# Tasks

Every task is `rhiza-task <name>`. The catalogue below is the registry the CLI generates
its own help from — `rhiza-task list` is the authoritative version for *your* repository,
and `rhiza-task list --all` adds the layers you do not have.

## The shape every task shares

Reading all ten make fragments back to back, every recipe turned out to have the same
three parts:

```mermaid
flowchart LR
    G["guard<br/><small>does the subject exist?</small>"] -->|absent| S([skipped])
    G -->|present| P["provision<br/><small>uv run --with / uvx</small>"]
    P --> A["argument vector<br/><small>long, mostly static</small>"]
    A --> R([ok / failed])
```

That is why a **guard** and a **skip** appear all over this page: they are the model, not
special cases.

## Python

| task | needs | does |
|---|---|---|
| `install` | — | create the venv and sync dependencies |
| `test` | `install` | run all tests |
| `coverage` | `install` | measure coverage and write `_tests/coverage.xml` |
| `typecheck` | `install` | run `ty` and/or `mypy` (`typechecker = ty \| mypy \| both`) |
| `security` | `install` | run the bandit security scan |
| `deps` | `install` | run deptry over the contributed folders |
| `license` | `install` | scan for copyleft licences |
| `docs-coverage` | `install` | check docstring coverage with interrogate |
| `all` | `fmt deps test docs-coverage security license typecheck rhiza-test` | run every gate, as CI does |

`test` and `coverage` share one argument builder, so the two cannot drift: both write
`_tests/coverage.xml`, `_tests/coverage.json`, `_tests/html-coverage/` and
`_tests/html-report/report.html`. That fixed path is a contract — it is what `book`'s
badge step reads and what CI uploads.

!!! info "`test` retries once, on exit 3 only"
    Exit 3 is the xdist teardown race. A retry on 1, 2 or 4 would be re-running a real
    failure and hoping, so it never happens.

## Rust

| task | needs | does |
|---|---|---|
| `install` | — | install the toolchain and fetch dependencies |
| `cargo-tools` | — | install the cargo subcommands the gates need |
| `test` | `install cargo-tools` | run the test suite with nextest, then the doctests |
| `coverage` | `install cargo-tools` | measure coverage and write `_tests/coverage.xml` |
| `typecheck` | `install` | lint with clippy, warnings as errors |
| `security` | `install cargo-tools` | scan dependencies for known advisories |
| `deps` | `install cargo-tools` | report unused dependencies |
| `license` | `install cargo-tools` | run the licence compliance scan |
| `docs-coverage` | `install` | fail on any undocumented public item |
| `all` | `fmt test docs-coverage security deps license typecheck rhiza-test` | run every gate, as CI does |

## Go

| task | needs | does |
|---|---|---|
| `install` | — | install the toolchain and download dependencies |
| `go-tools` | — | install the Go tools the gates need |
| `test` | `install` | run the test suite |
| `coverage` | `install go-tools` | measure coverage and write `_tests/coverage.xml` |
| `typecheck` | `install go-tools` | vet and lint (the compiler already type-checks) |
| `security` | `install go-tools` | scan dependencies for known vulnerabilities |
| `deps` | `install` | report dependency drift |
| `license` | `install go-tools` | run the licence compliance scan |
| `docs-coverage` | `install go-tools` | fail on any undocumented exported item |
| `all` | `fmt test docs-coverage security deps license typecheck rhiza-test` | run every gate, as CI does |

Go has no coverage-floor flag, so `coverage` pipes a profile through `gocover-cobertura`
and checks the floor itself.

## Quality

| task | needs | does |
|---|---|---|
| `fmt` | — | run the pre-commit hooks over all files |
| `semgrep` | — | run the semgrep static analysis rules |
| `rhiza-test` | `install` | run the rhiza repository checks |
| `test-pyproject` | `install` | run the `pyproject.toml` structure checks, verbosely |
| `todos` | — | list every TODO, FIXME and HACK comment |
| `complexity` | — | fail on a block above the cyclomatic-complexity ceiling |
| `docs-examples` | `install` | check the fenced examples in the docs tree |

`fmt` skips without a `.pre-commit-config.yaml`, `semgrep` without a
`.rhiza/semgrep.yml`, and `docs-examples` without a docs folder — or with one holding no
fence it can check. None is a defect — see [Skip is an outcome](#skip-is-an-outcome).

`docs-examples` parses every `python` fence with `compile`, every `bash` fence with
`bash -n` — parsed, never executed — every `toml` fence with `tomllib`, every `yaml` fence
with a real parser, and runs the `python` fences that a ```result``` block follows,
diffing what they print against that block. Fences in any other language are reported as
unchecked with a count, because silence there would read as full coverage. It answers the
question no other gate does: not "is there a docstring?" but "is what the documentation
claims still true?"

Its subject is the docs folder, plus the **data fences** — `toml` and `yaml` — in
`README.md`. That split exists so no fence is checked twice: `README.md` belongs to
pytest-rhiza's `test_readme_validation` under `rhiza-test`, which parses its code fences and
does not look at data ones. The two gates therefore divide by language rather than by file,
and README's contribution is reported on its own line so a partial look at a file is never
mistaken for a full one.

Two of those five can go unavailable on a machine that runs the gate fine otherwise, and
both say so on their own line rather than passing quietly: `bash` may be absent (a stock
Windows runner), and the yaml parser is *provisioned* rather than depended on — `rhiza-task`
is a published CLI, so a runtime dependency is an install cost every consumer pays on every
invocation, which two fences do not justify. In either case those fences are counted out of
the checked total, never assumed sound. `toml` has no such caveat: `tomllib` is stdlib at
this package's `>=3.11` floor.

Parsing is not validation. A `toml` fence that parses may still name a setting that does not
exist, and a `yaml` fence that parses may still be an invalid workflow; checking either would
need the schema. What this closes is the narrower gap where a fence stopped being the language
it claims.

`complexity` is the one task in this section that is Python-only: it runs `radon`, so it is
registered in the `python` layer even though it reads like a neutral gate. It measures every
block in `source_folder` and fails when one scores above
[`complexity_max`](configuration.md#gates-and-thresholds) (default `15`).

It is deliberately **not** a prerequisite of `all`. `all` is the aggregate a consumer's CI
invokes, so adding a gate to it would fail builds in repositories that changed nothing —
name `complexity` in a workflow step to opt in, as this repository's `ci.yml` does. A repo
whose blocks are larger should raise the ceiling rather than skip the gate; 15 suits a
codebase that already argues its complex blocks in comments.

`rhiza-test` runs the checks that used to live in a synced `.rhiza/tests/` folder, now
provisioned as [`pytest-rhiza`](https://github.com/jebel-quant/pytest-rhiza). Which checks
run is derived from the language layer, not configured: the neutral ones, plus
`test_pyproject` and `test_docstrings` for Python, `test_cargo_toml` for Rust,
`test_go_module` for Go.

*Which* pytest-rhiza runs them is configured, by
[`pytest_rhiza`](configuration.md#environment-and-ci) — a pinned release by default, and the
project environment's own copy when the setting is empty.

## Testing extras

| task | needs | does |
|---|---|---|
| `benchmark` | `install` | run the performance benchmarks |
| `hypothesis-test` | `install` | run the property-based tests |
| `stress` | `install` | run the stress and load tests |

No `all` names the three, and each is guarded on the folder convention it needs —
`benchmarks/`, `stress/`, or any test file at all — so a project without one skips rather
than fails. `book` is the one aggregate that does name them, which is how their reports
reach the published book.

## Book

| task | needs | does |
|---|---|---|
| `book` | `test benchmark stress hypothesis-test paper` | build the companion book |
| `book-nav` | — | check that every mkdocs nav entry resolves in the built book |
| `serve` | `book` | build the book and serve it on port 8000 |
| `marimo` | `install` | start the Marimo editor |
| `marimo-validate` | `install` | check that every Marimo notebook runs |

`book` aggregates the report-producing gates, copies `_tests/` into `docs/reports/`,
exports every Marimo notebook to `docs/notebooks/*.html`, builds the site with
[zensical](https://github.com/zensical/zensical), and generates a coverage badge. It
skips entirely without a `mkdocs.yml`.

`paper` is a prerequisite but needs no copy step, unlike `_tests/`: tectonic writes the PDF
beside its source, and `paper_folder` (default `docs/paper`) is already inside `docs_dir`,
so the site build picks it up as an asset and `mkdocs.yml` only names it in `nav`. A
repository with no paper — or no tectonic — still builds its book, because a *skipped*
prerequisite does not block a dependent; only a failed one does. Under `--strict` that skip
becomes a failure and would block `book`, which is worth knowing but is not specific to
`paper`: `benchmark` and `stress` guard on folders most repositories do not have.

`book-nav` is the other side of that permissiveness, and deliberately **not** a prerequisite
of `book`. Skipping a producer is what lets a repository without tectonic still build its
book — and the cost is that `mkdocs.yml` can name a target the build never wrote. zensical
does not catch it: it reports `No issues found` for a nav entry whose page is missing *and*
for one whose asset is missing, so such a site builds green and publishes a 404 in its own
navigation. That is not hypothetical — it is what
`https://jebel-quant.github.io/rhiza-task/paper/paper.pdf` did.

So the check is a separate gate, for the ref that publishes rather than for every build.
Markdown targets are matched against both forms mkdocs may write (`faq.html` and
`faq/index.html`, per `use_directory_urls`); assets are matched verbatim; external targets
are left to a link checker. It skips before the book is built and when the config declares
no nav, because both are questions that are not askable rather than answers that are wrong.

Its prerequisite list is also where make's no-op stubs came from: `book.mk` had to declare
`test:: ; @:`, `benchmark:: ; @:`, `stress:: ; @:` and `hypothesis-test:: ; @:` so that
`book` could depend on gates the `tests` bundle may never have contributed. The runner
skips unregistered prerequisites, so all four stubs are gone.

`serve` uses Python's own HTTP server rather than an editor's built-in one, because the
JetBrains server refuses to serve gitignored directories and `_book` is one.

## Dev

| task | needs | does |
|---|---|---|
| `doctor` | — | check local prerequisites |
| `clean` | — | remove build artifacts and stale local branches |
| `setup` | — | run the repository's own environment setup hook |

`setup` runs a repo-owned `local-setup.sh` at the repository root, and **every layer's
`install` names it as a prerequisite** — so it reaches local `make test`, CI and a
devcontainer through one insertion point rather than a step in each workflow.

It is the seam for a native binary a project needs before its gates can run: graphviz for a
docs plugin, `libpq` for psycopg, pandoc. rhiza's template owns every configuration file in a
managed repository, so before this there was nowhere to put that step — and the workaround
that was documented, shadowing `install` in `local.mk`, never ran. The Makefile shim forwards
the *goal* to this CLI, so `install` resolves here rather than in make, and CI invokes the CLI
directly without going through make at all.

Deliberately **not** a `system-packages = [...]` setting. `graphviz` is spelled the same on
apt and brew; `libgl1-mesa-glx` is not, and a list cannot express "download this tarball" —
which is what rhiza itself does for tectonic. The hook is a script, so platform detection
stays with the people who know which platforms they build on, and this package acquires no
apt/brew/winget logic — the rule `lfs-install` states.

A hook that exists but is not executable **fails**, with the `chmod` hint — a provisioning
step someone wrote and believed was running is exactly what must not pass quietly.

**Windows runs the same hook**, handed to `sh` rather than started directly, because the OS
execs no `.sh` — it answers *%1 is not a valid Win32 application* before the shebang is read.
GitHub's `windows-latest` runners ship git-bash, so there is an `sh` on PATH to hand it to,
and one hook file still covers every platform. Two consequences: the shebang is **not**
consulted there, so a hook that means `bash` should keep to POSIX shell; and a machine with no
`sh` at all fails naming `sh`, rather than naming the hook that never started. The execute bit
is not asked about on Windows either — `os.access(X_OK)` calls every existing file executable
there, so the question would pass vacuously.

An absent hook, by contrast, **succeeds**; it does not skip. That is deliberate and is the
one place in this package where "nothing happened" is not a `skipped`. `Skip` means work was
asked for and did not happen, which is why `--strict` promotes it to a failure. Nothing is
asked for by a repository that declares no hook — and since most declare none, skipping would
fail every `--strict` invocation on the common case, `book --strict` included, five
prerequisites down. The `[INFO] no local-setup.sh` line is what keeps that outcome legible.

`doctor` is the second procedural escape — it compares semantic versions, which used to be
an `awk` function inside a make recipe.

It names **uv and git, and nothing else**, and a miss is a failure rather than a warning.
Every other binary the package reaches for — docker, gh, git-lfs, tectonic, marp — is a
`Guard` on the one task that wraps it, and reports itself on that task's `skipped` line with
an install URL. `doctor.mk` also probed GNU make, which was honest while the task layer *was*
make; nothing here needs it, so it is not probed at all.

## Bundle-owned sections

The last five sections are the bundle-owned fragments. **None is a gate**: no `all` names
them and no workflow invokes them. Each is guarded on the CLI it wraps and skips when that
tool is absent, with `--strict` for a caller who wants the hard failure instead.

### GitHub Helpers

| task | does |
|---|---|
| `view-prs` | list open pull requests |
| `view-issues` | list open issues |
| `failed-workflows` | list recent failing workflow runs |
| `workflow-status` | show recent runs for the release workflow |
| `latest-release` | show information about the latest GitHub release |
| `whoami` | check github auth status |

### Docker

| task | needs | does |
|---|---|---|
| `docker-build` | — | build the Docker image |
| `docker-run` | `docker-build` | run the Docker container |
| `docker-clean` | — | remove the Docker image |

### Git LFS

| task | does |
|---|---|
| `lfs-install` | configure git-lfs for this repository |
| `lfs-pull` | download the LFS files for the current branch |
| `lfs-track` | list the patterns tracked by git-lfs |
| `lfs-status` | show the status of LFS files |

!!! warning "`lfs-install` changed behaviour on purpose"
    It configures the repository and *reports* how to install the binary, rather than
    downloading one. The module docstring says so, which is the convention for every
    deliberate behaviour change.

### Paper

| task | does |
|---|---|
| `paper` | compile the LaTeX paper to PDF |
| `paper-clean` | remove the LaTeX build artifacts |

**The engine is [tectonic](https://tectonic-typesetting.github.io/), not a TeX
distribution's driver.** One binary to install instead of a distribution plus the list of
packages a document happens to cite, because tectonic resolves what the document cites from
its own bundle and caches it. So the vector is the document and one flag: convergence and
bibtex are the engine's own loop, and there is no interaction mode to pin because tectonic
never prompts. The cost is that a cold cache needs the network, where a provisioned
distribution did not.

`paper-clean` follows from that: tectonic has no clean subcommand, so the task is plain
Python and guards on no tool at all — the one task in this section that works on a machine
which cannot build the paper. It removes the PDF and the auxiliary files **belonging to the
folder's top-level `.tex` documents**, so a committed `diagram.pdf` with no `diagram.tex`
survives.

`paper.mk` preferred a file called `basanos.tex` — one downstream repository's paper, named
in a template every consumer synced. That is now two conventional names, `main.tex` and
`paper.tex`, then alphabetical order.

### Presentation

| task | does |
|---|---|
| `presentation` | generate the HTML slides with Marp |
| `presentation-pdf` | generate the PDF slides with Marp |
| `presentation-serve` | serve the slides with Marp's live preview |

`presentation` reaches Marp through `npx --yes` — also a deliberate change, also recorded
in the module docstring.

## Skip is an outcome

`skipped` is not a soft failure and not a pass. It is the third status, and it is what
makes one aggregate work across repositories with different bundles installed:

```text
      ok  install
      ok  typecheck
 skipped  fmt  no .pre-commit-config.yaml
```

Because it is first-class, `--strict` can promote every skip to a failure — which is how a
consumer's CI asserts that a gate actually measured something rather than quietly finding
nothing to do.

## `all` is not everything

`all` is the aggregate CI runs, and two registered tasks sit outside it on purpose, so a
reader can tell "skipped deliberately" from "forgotten":

- **`semgrep`** — no `all` names it. Upstream rhiza runs it on a weekly cadence, not per
  pull request.
- **`complexity`** and **`docs-examples`** — adding either to `all` would fail builds in
  repositories that changed nothing, so a repo opts in by naming it in its own CI, which
  is what this one does.
- **the bundle-owned sections** — none is a gate, as above.

Anything else registered *is* in `all` for its layer.
