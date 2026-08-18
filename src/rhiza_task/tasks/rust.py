"""The Rust language layer: rust.mk, as tasks.

The gate names are python.mk's, deliberately: ``install``, ``test``, ``typecheck``,
``docs-coverage``, ``security``, ``license``, ``deps``, ``all``. That is
the contract the reusable workflows and ``book`` depend on -- `rhiza_ci.yml` calls
``make typecheck`` without knowing what the repository is written in, and rust.mk's own
header says so. Only the engine differs.

Nothing here goes through uv. cargo is not a Python tool and rustup is not a uv-managed
toolchain, so the provisioning half of the make recipe has no analogue: what is left is
:func:`~rhiza_task.uv.tool`, an argument vector, and the guards.
"""

from __future__ import annotations

import os
from pathlib import Path

from ..config import Config
from ..spec import Failed, Guard, have, task
from ..uv import tool
from .quality import install_hooks

CARGO_TOOLS = ("cargo-nextest", "cargo-llvm-cov", "cargo-deny", "cargo-machete")
"""The cargo subcommands the gates need, in rust.mk's order.

A named tuple rather than a literal in the recipe, for the reason
:data:`~rhiza_task.tasks.python.PYTEST_WITHS` is one: what a gate provisions is part of
its contract, and this is the only place CI can assert on it.
"""

MANIFEST = Guard(file="Cargo.toml", reason="no Cargo.toml")
"""What every Rust gate is guarded on.

A file rather than a folder: cargo finds ``src/`` itself from the manifest, and a crate
that renames it is still a crate. This is the flat analogue of python.mk's
``if [ -d ${SOURCE_FOLDER} ]``.
"""


@task("install", "install the toolchain and fetch dependencies", section="Rust", layer="rust")
def install(cfg: Config) -> None:
    """Materialise the pinned toolchain, fetch dependencies, install the git hooks.

    ``rustup show`` is what materialises ``rust-toolchain.toml``'s channel and components,
    because rustup installs a pinned toolchain lazily -- so this is a provisioning step
    despite reading like a query.

    Args:
        cfg: The resolved config.

    Raises:
        Failed: When rustup is absent, or a step exits non-zero.
    """
    if not have("rustup"):
        raise Failed(1, "rustup not found -- install it from https://rustup.rs (or: brew install rustup)")

    if (cfg.root / "rust-toolchain.toml").is_file():
        print("[INFO] installing the toolchain pinned in rust-toolchain.toml")
        tool("rustup", "show", cwd=cfg.root)
    else:
        print("[WARN] no rust-toolchain.toml; using the active default toolchain")

    if (cfg.root / "Cargo.toml").is_file():
        # --locked first, unlocked as a fallback: rust.mk's `|| $(CARGO) fetch`, which
        # exists because a crate without a committed Cargo.lock is legitimate.
        if tool("cargo", "fetch", "--locked", cwd=cfg.root, check=False):
            tool("cargo", "fetch", cwd=cfg.root)
    else:
        print("[WARN] no Cargo.toml; skipping fetch")

    install_hooks(cfg)


@task("cargo-tools", "install the cargo subcommands the gates need", section="Rust", layer="rust")
def cargo_tools(cfg: Config) -> None:
    """Install the missing cargo subcommands, via cargo-binstall where it helps.

    binstall fetches a prebuilt binary where the project publishes one and falls back to a
    source build, which is the difference between seconds and minutes on CI.

    The one subtlety, carried over from rust.mk rather than rediscovered: ``cargo install``
    puts binaries in ``$CARGO_HOME/bin``, which is *not* necessarily on PATH --
    ``brew install rustup`` leaves the shims in Homebrew's bin and never links
    ``~/.cargo/bin``. cargo resolves ``cargo <sub>`` by searching that directory as well as
    PATH, so the gates work either way; what does not work is a bare ``command -v
    cargo-nextest``. So presence is probed in both places, and binstall is invoked as a
    cargo subcommand.

    Args:
        cfg: The resolved config.
    """
    if not have("cargo-binstall") and not (_cargo_bin() / "cargo-binstall").exists():
        print("[INFO] installing cargo-binstall")
        tool("cargo", "install", "cargo-binstall", "--locked", cwd=cfg.root)

    missing = [t for t in CARGO_TOOLS if not have(t) and not (_cargo_bin() / t).exists()]
    if not missing:
        print("[INFO] all cargo tools already installed")
        return
    print(f"[INFO] installing: {' '.join(missing)}")
    tool("cargo", "binstall", "--no-confirm", "--locked", *missing, cwd=cfg.root)


@task(
    "test",
    "run the test suite with nextest, then the doctests",
    section="Rust",
    layer="rust",
    needs=("install", "cargo-tools"),
    guards=(MANIFEST,),
)
def test(cfg: Config) -> None:
    """Run ``cargo nextest`` over all targets, then ``cargo test --doc``.

    Both, not either: nextest does not run doctests, and a doctest is a real test. This is
    the Rust analogue of the retry loop in python.mk being the interesting part of ``test``
    -- here the interesting part is that one command is not enough.

    Args:
        cfg: The resolved config.
    """
    reports = cfg.root / "_tests"
    reports.mkdir(parents=True, exist_ok=True)
    tool("cargo", "nextest", "run", "--all-targets", *cfg.cargo_flags, cwd=cfg.root)
    print("[INFO] running doctests")
    tool("cargo", "test", "--doc", *cfg.cargo_flags, cwd=cfg.root)


@task(
    "typecheck",
    "lint with clippy, warnings as errors",
    section="Rust",
    layer="rust",
    needs=("install",),
    guards=(MANIFEST,),
)
def typecheck(cfg: Config) -> None:
    """Run clippy over all targets with warnings denied.

    rustc already type-checks, so the parity entry for ``typecheck`` is the lint that
    catches what compiling does not -- the same relationship ``go vet`` has to the Go
    compiler.

    Args:
        cfg: The resolved config.
    """
    tool("cargo", "clippy", "--all-targets", *cfg.cargo_flags, "--", "-D", "warnings", cwd=cfg.root)


@task(
    "docs-coverage",
    "fail on any undocumented public item",
    section="Rust",
    layer="rust",
    needs=("install",),
    guards=(MANIFEST,),
)
def docs_coverage(cfg: Config) -> None:
    """Build the docs with ``missing_docs`` denied.

    interrogate's 100% floor expressed in rustdoc's own terms: pass/fail on an undocumented
    public item rather than a percentage, because rustdoc has no percentage to report.

    Args:
        cfg: The resolved config.
    """
    tool(
        "cargo",
        "doc",
        "--no-deps",
        *cfg.cargo_flags,
        cwd=cfg.root,
        env={"RUSTDOCFLAGS": "-D missing_docs -D rustdoc::broken_intra_doc_links"},
    )


@task(
    "security",
    "scan dependencies for known advisories",
    section="Rust",
    layer="rust",
    needs=("install", "cargo-tools"),
    guards=(MANIFEST,),
)
def security(cfg: Config) -> None:
    """Run ``cargo deny check advisories``.

    Args:
        cfg: The resolved config.
    """
    tool("cargo", "deny", "check", "advisories", cwd=cfg.root)


@task(
    "license",
    "run the licence compliance scan",
    section="Rust",
    layer="rust",
    needs=("install", "cargo-tools"),
    guards=(MANIFEST,),
)
def license_(cfg: Config) -> None:
    """Run ``cargo deny check licenses``.

    The allow-list lives in ``deny.toml`` rather than in this argument vector, which is why
    ``license_fail_on`` -- pip-licenses' flag, and Python-only -- does not appear here. No
    guard on that file: cargo-deny falls back to its own defaults and says so, and a gate
    that skipped instead would be the "green gate measuring nothing" this port exists to
    stop shipping.

    Args:
        cfg: The resolved config.
    """
    tool("cargo", "deny", "check", "licenses", cwd=cfg.root)


@task(
    "deps",
    "report unused dependencies",
    section="Rust",
    layer="rust",
    needs=("install", "cargo-tools"),
    guards=(MANIFEST,),
)
def deps(cfg: Config) -> None:
    """Run cargo-machete, the deptry analogue.

    Args:
        cfg: The resolved config.
    """
    tool("cargo", "machete", cwd=cfg.root)


@task(
    "all",
    "run every gate, as CI does",
    section="Rust",
    layer="rust",
    needs=("fmt", "test", "docs-coverage", "security", "deps", "license", "typecheck", "rhiza-test"),
)
def all_(cfg: Config) -> None:
    """Aggregate, with rust.mk's prerequisite list. The body is empty because ``needs`` is it.

    Args:
        cfg: Unused; the prerequisites do the work.
    """


def _cargo_bin() -> Path:
    """Return the directory ``cargo install`` writes to.

    The two variables cargo itself reads, in the order cargo reads them, then its default.
    Resolved here rather than assumed, because the whole point of the probe is that this
    directory is often not on PATH.

    Returns:
        The cargo binary directory.
    """
    root = os.environ.get("CARGO_INSTALL_ROOT") or os.environ.get("CARGO_HOME") or (Path.home() / ".cargo")
    return Path(root) / "bin"
