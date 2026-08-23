---
icon: material/help-circle
---

# FAQ & Troubleshooting

## Usage

### Why is my task not in `rhiza-task --help`?

It is not supposed to be. `rhiza-task <task>` is rewritten to `rhiza-task run <task>`, so
`--help` lists only the five real subcommands — `list`, `print`, `run`, `ci-os-matrix` and
`version`.

Use `rhiza-task list` to enumerate tasks, and `rhiza-task list --all` to include the
language layers this repository does not have.

### Do I have to pin the version?

You should. An unpinned task layer can change under a green pull request without a commit
touching your repository — which is the failure mode the whole package exists to remove.
`rhiza-task@1.3.1` makes a bump a deliberate commit that carries whatever fixes it wants.

### A gate says `skipped`. Is that bad?

Not by itself. A task whose subject is absent skips: `fmt` with no
`.pre-commit-config.yaml`, `marimo-validate` with no notebooks, `paper` with no LaTeX
folder. The reason is printed next to it.

It *is* bad when the subject was supposed to be there. That is what `--strict` is for:

```bash
uvx rhiza-task@1.3.1 all --strict
```

In a consumer repository `--strict` is usually the right setting, because a skip means a
gate lost its subject and nobody noticed.

### Why does this repository not use `--strict` on itself?

On `all`, it could: every gate `all` aggregates passes here, so `rhiza-task all --strict`
is green.

What skips is outside `all`, and for three different reasons worth separating:

- `semgrep` has no `.rhiza/semgrep.yml`. This is the genuine "not rhiza-managed" case.
- `presentation` has no `PRESENTATION.md`, and `marimo-validate` no `docs/notebooks`. These
  skip for want of a *subject*, which any consumer that has not adopted the bundle shares.
- `paper` skips on a machine without `tectonic` — a fact about the machine, not about this
  repository.

So a registry-wide `--strict` would assert that this is a consumer carrying every bundle,
which it is not. `all --strict` is a narrower claim, and a true one.

### `unknown task` — but `list` shows it

Two likely causes:

1. **It belongs to a layer you do not have.** `list --all` shows every layer's tasks; a
   name only that layer defines needs the explicit key, `rhiza-task rust:test`.
2. **Its module failed to import.** A plugin import error is reported and skipped rather
   than fatal, so look for this line earlier in the output:

   ```text
   could not load task module acme: ModuleNotFoundError: No module named 'acme_internal'
   ```

### How do I run the gates for another repository?

```bash
uvx rhiza-task@1.3.1 all --root ../other-repo
```

## Configuration

### My setting is being ignored

Check what actually resolved, rather than what you wrote:

```bash
uvx rhiza-task@1.3.1 print coverage_fail_under
```

The usual causes, in order of likelihood:

| cause | fix |
|---|---|
| set in `rhiza.toml`, but the manifest table also sets it | the manifest wins — layer 4 outranks layer 3 |
| set in `.rhiza/.env`, and you are looking at CI | that file is gitignored now; a CI checkout never has it |
| set as an empty string | an empty value is *unset*, and leaves the layer below alone |
| wrong spelling | both `source_folder` and `SOURCE_FOLDER` work; anything else does not |

### Why did `.rhiza/.env` stop working in CI?

rhiza no longer ships `.rhiza/.gitignore`, whose entire content was the `!.env` negation
that kept the file tracked. It now falls under the shipped `.gitignore`'s `.env` rule, so
it is developer-local and **a CI checkout never contains it**.

Move anything CI depends on into `rhiza.toml` or `[tool.rhiza-task]`.

### Why is an empty value not empty?

```bash
RHIZA_CI_OS_MATRIX= uvx rhiza-task@1.3.1 ci-os-matrix   # still ["ubuntu-latest"]
```

That is make's `$(or ...)` rule, kept because the reusable workflows depend on it:
`rhiza_ci.yml` exports one for every caller and deliberately leaves it empty for consumers,
whose own settings are meant to answer.

### My repo has two manifests and picks the wrong gates

Detection returns *both* layers, Python first. Pin it:

```toml
[tool.rhiza-task]
layers = ["rust"]
```

Setting `layers` switches detection off for that field. The other layer stays reachable as
`rhiza-task python:test`.

### `typechecker = "both"` hides errors

Known, documented, and unchanged from `python.mk`: `both` masks `ty`'s exit status behind
mypy's. Set `typechecker = "ty"` if you want `ty`'s status to matter.

A typo — `typechecker = "tpye"` — now fails *before* any tool is provisioned rather than
after, so a misconfiguration is immediate instead of a five-minute CI wait.

## Gates

### `test` failed, then passed on retry

By design, and only for one specific case: exit code **3**, the xdist teardown race. Exit
1, 2 and 4 are never retried, because that would be re-running a real failure and hoping.

### `book` did nothing

It skips without a `mkdocs.yml` at the repository root. That file's presence is what turns
`book` from a skip into a build.

If it built but the reports are missing, run the gates that produce them first — `_tests/`
is written by `test`, and `book` copies it into `docs/reports/`.

### `zensical: mkdocstrings plugin is enabled, but mkdocstrings is not installed`

The `mkdocs_extra_packages` setting is what provisions plugins into the isolated zensical
run, and its default is exactly `("mkdocstrings[python]",)` for this reason. If you
overrode it, add back what your `mkdocs.yml` enables:

```toml
[tool.rhiza-task]
mkdocs-extra-packages = ["mkdocstrings[python]", "mkdocs-jupyter"]
```

A repository that wants no plugins at all sets `mkdocs-extra-packages = []`.

### `deptry` reports a dependency as both unused and missing

The package's import name differs from its distribution name, and deptry runs isolated via
`uvx` so it cannot import the package to discover the mapping. Declare it:

```toml
[tool.deptry.package_module_name_map]
python-dotenv = "dotenv"
```

### `interrogate` or `bandit` disagrees with CI

Run the gate, not the tool. Thresholds, exclusions and arguments live in the task and in
the repository's config; a bare `uvx interrogate` measures something else and will report
failures CI does not have.

## Design

### Why not a Taskfile, `just`, or `nox`?

go-task is a genuinely better make, and its remote includes would attack the same root
problem — but that feature is experimental and env-var-gated, and it would be the single
load-bearing dependency of the whole multi-repo task layer, whereas `uvx pkg@version` is
boring and already used ~15 times per repository. The three procedural recipes would also
stay embedded shell in YAML.

`just` and `poe` do not apply: a Justfile or a noxfile still has to be copied into every
repository, which is the problem being deleted.

### Why is there no `install-uv` task?

It cannot exist. A process launched by `uvx rhiza-task` runs *because* uv exists, so
nothing in this package can be the thing that provisions uv — it would already be too late.
A runner shipping no uv adds an `astral-sh/setup-uv` step instead.

### Does a Rust crate really need Python now?

Yes, and it is the package's least comfortable trade. `rust.mk` and `go.mk` needed only
make; the Rust and Go layers here are Python calling `cargo` and `go`. It buys the version
pin and deletes ~1200 synced lines per consumer, and it costs a Python runtime in a
repository that may have had no other reason for one.

### Is Windows supported?

Yes, and it is in the CI matrix specifically to prove it. Commands are argument vectors,
never shell strings, so the 40-line probe `rhiza.mk` needed to detect make falling back to
`cmd.exe` has nothing left to detect.

### How expensive is the nested uv call?

`uvx rhiza-task test` internally runs `uv run --with pytest ...`. Cached, this should be
milliseconds — but it is an open question rather than a measured claim, and worth measuring
before a wide rollout.
