"""The three language layers: detection, resolution, and each engine's argument vectors.

The make layer answered "which ``test`` is this?" at sync time, by copying exactly one of
python.mk, rust.mk and go.mk into a repository. A pinned CLI carries all three at once, so
the question moved to runtime and these tests are what pin the answer down: a crate gets
cargo, a module gets go, and a repository with both manifests gets one gate set rather
than an ambiguity.

As everywhere else in this suite, no toolchain is run -- ``cargo``, ``go`` and ``rustup``
are recorded through the same fixture that stands in for uv, and the argument vectors are
the contract rust.mk and go.mk expressed in shell.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from rhiza_task import runner
from rhiza_task.config import Config, detect_layers, rhiza_checks_for
from rhiza_task.runner import Status
from rhiza_task.spec import Failed, lookup
from rhiza_task.tasks import go as go_tasks
from rhiza_task.tasks import rust as rust_tasks

from .conftest import Recorder


@pytest.fixture
def crate(tmp_path: Path) -> Path:
    """Build a minimal Rust project: a manifest, a pinned toolchain, a source file.

    Args:
        tmp_path: pytest's temporary directory.

    Returns:
        The repository root.
    """
    # A subdirectory, so that a test taking both this and the Python ``repo`` fixture gets
    # two repositories rather than one repository with two manifests.
    root = tmp_path / "crate"
    (root / "src").mkdir(parents=True)
    (root / "Cargo.toml").write_text('[package]\nname = "demo"\nversion = "0.1.0"\n')
    (root / "rust-toolchain.toml").write_text('[toolchain]\nchannel = "stable"\n')
    (root / "src" / "lib.rs").write_text("//! A crate.\n")
    return root


@pytest.fixture
def module(tmp_path: Path) -> Path:
    """Build a minimal Go module.

    Args:
        tmp_path: pytest's temporary directory.

    Returns:
        The repository root.
    """
    root = tmp_path / "module"
    root.mkdir()
    (root / "go.mod").write_text("module example.com/demo\n\ngo 1.23\n")
    (root / "main.go").write_text("package main\n\nfunc main() {}\n")
    return root


class TestDetection:
    """Which layers a repository belongs to, and what follows from that."""

    def test_the_manifest_decides(self, tmp_path: Path) -> None:
        """Each layer is detected by the file its own toolchain looks for.

        Args:
            tmp_path: An empty directory, filled per case.
        """
        assert detect_layers(tmp_path) == ("python",)
        (tmp_path / "go.mod").touch()
        assert detect_layers(tmp_path) == ("go",)
        (tmp_path / "Cargo.toml").touch()
        assert detect_layers(tmp_path) == ("rust", "go")
        (tmp_path / "pyproject.toml").touch()
        assert detect_layers(tmp_path) == ("python", "rust", "go")

    def test_layers_can_be_pinned(self, crate: Path) -> None:
        """A repository carrying two manifests can name the layer it wants.

        This is the successor to "only one language layer is ever synced into a repo":
        detection is the default, not the mechanism.

        Args:
            crate: A Rust project root.
        """
        (crate / "pyproject.toml").write_text('[project]\nname = "d"\nversion = "0"\n')
        assert Config.load(root=crate).layers == ("python", "rust")

        (crate / "pyproject.toml").write_text(
            '[project]\nname = "d"\nversion = "0"\n\n[tool.rhiza-task]\nlayers = ["rust"]\n'
        )
        assert Config.load(root=crate).layers == ("rust",)

    def test_an_unknown_layer_is_a_configuration_error(self, tmp_path: Path) -> None:
        """A typo names itself rather than silently resolving to no gates.

        Args:
            tmp_path: The repository root.
        """
        with pytest.raises(ValueError, match="unknown layer"):
            Config.load(root=tmp_path, layers=("rustt",))

    def test_the_check_set_follows_the_layers(self, crate: Path, module: Path) -> None:
        """``RHIZA_CHECKS`` is derived per layer, replacing the make accumulator.

        Each language fragment appended its own check with ``RHIZA_CHECKS +=``, which
        worked only because of include order. Here the same set falls out of the layers.

        Args:
            crate: A Rust project root.
            module: A Go module root.
        """
        rust = Config.load(root=crate).rhiza_checks
        assert "pytest_rhiza.checks.test_cargo_toml" in rust
        assert "pytest_rhiza.checks.test_pyproject" not in rust
        assert "pytest_rhiza.checks.test_readme" in rust

        assert "pytest_rhiza.checks.test_go_module" in Config.load(root=module).rhiza_checks

        both = rhiza_checks_for(("python", "rust"))
        assert both.count("pytest_rhiza.checks.test_readme") == 1
        assert both[-1] == "pytest_rhiza.checks.test_cargo_toml"

    def test_an_explicit_check_set_still_wins(self, crate: Path) -> None:
        """Deriving the default does not take the setting away.

        Args:
            crate: A Rust project root.
        """
        cfg = Config.load(root=crate, rhiza_checks=("pytest_rhiza.checks.test_readme",))
        assert cfg.rhiza_checks == ("pytest_rhiza.checks.test_readme",)


class TestResolution:
    """Which task a bare name reaches, given a repository's layers."""

    def test_the_same_name_reaches_a_different_engine(self, crate: Path, repo: Path) -> None:
        """``test`` is pytest in a Python project and nextest in a crate.

        Args:
            crate: A Rust project root.
            repo: A Python project root.
        """
        assert lookup("test", Config.load(root=crate).layers).layer == "rust"
        assert lookup("test", Config.load(root=repo).layers).layer == "python"

    def test_a_qualified_name_reaches_the_layer_that_did_not_win(self, repo: Path) -> None:
        """``rust:test`` is how the other layer is addressed at all.

        Args:
            repo: A Python project root.
        """
        assert lookup("rust:test", Config.load(root=repo).layers).layer == "rust"
        assert lookup("nonsense:test", ()) is None

    def test_a_neutral_task_answers_everywhere(self, crate: Path) -> None:
        """``fmt`` and ``todos`` belong to no layer, which is what ``core`` was.

        Args:
            crate: A Rust project root.
        """
        layers = Config.load(root=crate).layers
        assert lookup("fmt", layers).layer is None
        assert lookup("todos", layers).layer is None

    def test_the_runner_runs_one_task_for_two_spellings(
        self, crate: Path, recorder: Recorder, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``test`` and ``rust:test`` in one invocation are one task, not two.

        Args:
            crate: A Rust project root.
            recorder: The command recorder.
            monkeypatch: pytest's patcher.
        """
        monkeypatch.setattr(rust_tasks, "have", lambda _: True)
        state = runner.run(["rust:test", "test"], Config.load(root=crate))
        assert [r.name for r in state.results] == ["install", "cargo-tools", "test"]
        # One `test`, not two: `install` and `cargo-tools` ran once each, and the two
        # spellings of the gate itself resolved to the same registry key.
        assert [c.flags[0] for c in recorder.calls if c.tool == "cargo"] == ["fetch", "nextest", "test"]

    def test_an_unknown_task_names_itself(self, module: Path) -> None:
        """A Python-only gate requested in a Go module is an unknown task, not a silent pass.

        Args:
            module: A Go module root.
        """
        with pytest.raises(KeyError, match="unknown task: mutation"):
            runner.run(["mutation"], Config.load(root=module))


class TestRustGates:
    """rust.mk's recipes, as argument vectors."""

    def test_install_materialises_the_pinned_toolchain(
        self, crate: Path, recorder: Recorder, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``rustup show`` is the provisioning step, despite reading like a query.

        Args:
            crate: A Rust project root.
            recorder: The command recorder.
            monkeypatch: pytest's patcher.
        """
        monkeypatch.setattr(rust_tasks, "have", lambda _: True)
        rust_tasks.install(Config.load(root=crate))
        assert recorder.find("rustup").flags == ["show"]
        assert recorder.find("cargo").flags == ["fetch", "--locked"]

    def test_install_without_rustup_says_where_to_get_it(self, crate: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """A missing rustup is a failure with an address, not a stack trace.

        Args:
            crate: A Rust project root.
            monkeypatch: pytest's patcher.
        """
        monkeypatch.setattr(rust_tasks, "have", lambda _: False)
        with pytest.raises(Failed, match=r"rustup\.rs"):
            rust_tasks.install(Config.load(root=crate))

    def test_install_falls_back_to_an_unlocked_fetch(
        self, crate: Path, recorder: Recorder, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A crate with no committed lock file is still a crate.

        Args:
            crate: A Rust project root.
            recorder: The command recorder.
            monkeypatch: pytest's patcher.
        """
        monkeypatch.setattr(rust_tasks, "have", lambda _: True)
        recorder.codes = [0, 1]  # rustup show, then --locked fetch
        rust_tasks.install(Config.load(root=crate))
        assert [c.flags for c in recorder.calls if c.tool == "cargo"] == [["fetch", "--locked"], ["fetch"]]

    def test_test_runs_nextest_and_the_doctests(self, crate: Path, recorder: Recorder) -> None:
        """Nextest does not run doctests, and a doctest is a real test.

        Args:
            crate: A Rust project root.
            recorder: The command recorder.
        """
        rust_tasks.test(Config.load(root=crate))
        assert [c.flags for c in recorder.calls] == [
            ["nextest", "run", "--all-targets"],
            ["test", "--doc"],
        ]

    def test_coverage_writes_the_path_the_badge_reads(self, crate: Path, recorder: Recorder) -> None:
        """Cobertura XML at ``_tests/coverage.xml``, and the floor is the shared setting.

        The path is the contract, not a detail: it is what book.mk's badge step reads, so a
        crate gets a measured badge for the same reason a Python project does.

        Args:
            crate: A Rust project root.
            recorder: The command recorder.
        """
        rust_tasks.coverage(Config.load(root=crate, coverage_fail_under=85))
        measure, report = (c.flags for c in recorder.calls)
        assert measure[:3] == ["llvm-cov", "nextest", "--all-targets"]
        assert measure[3:] == ["--fail-under-lines", "85", "--cobertura", "--output-path", "_tests/coverage.xml"]
        assert report == ["llvm-cov", "report", "--html", "--output-dir", "_tests/html-coverage"]

    def test_cargo_flags_reach_every_gate(self, crate: Path, recorder: Recorder) -> None:
        """``CARGO_FLAGS`` is a setting, not a per-recipe literal.

        Args:
            crate: A Rust project root.
            recorder: The command recorder.
        """
        cfg = Config.load(root=crate, cargo_flags=("--all-features",))
        rust_tasks.typecheck(cfg)
        assert recorder.find("cargo").flags == ["clippy", "--all-targets", "--all-features", "--", "-D", "warnings"]

    def test_docs_coverage_denies_missing_docs(self, crate: Path, recorder: Recorder) -> None:
        """Rustdoc has no percentage, so the floor is expressed as a denied lint.

        Args:
            crate: A Rust project root.
            recorder: The command recorder.
        """
        rust_tasks.docs_coverage(Config.load(root=crate))
        call = recorder.find("cargo")
        assert call.flags == ["doc", "--no-deps"]
        assert "-D missing_docs" in call.kwargs["env"]["RUSTDOCFLAGS"]

    def test_cargo_tools_installs_only_what_is_missing(
        self, crate: Path, recorder: Recorder, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Presence is probed on PATH *and* in cargo's bin directory, which is often not on it.

        Args:
            crate: A Rust project root.
            recorder: The command recorder.
            monkeypatch: pytest's patcher.
            tmp_path: pytest's temporary directory.
        """
        cargo_bin = tmp_path / "cargo-bin"
        cargo_bin.mkdir()
        (cargo_bin / "cargo-binstall").touch()
        (cargo_bin / "cargo-nextest").touch()
        monkeypatch.setattr(rust_tasks, "_cargo_bin", lambda: cargo_bin)
        monkeypatch.setattr(rust_tasks, "have", lambda _: False)

        rust_tasks.cargo_tools(Config.load(root=crate))
        assert recorder.find("cargo").flags == [
            "binstall",
            "--no-confirm",
            "--locked",
            "cargo-llvm-cov",
            "cargo-deny",
            "cargo-machete",
        ]

    def test_cargo_tools_bootstraps_binstall_from_source(
        self, crate: Path, recorder: Recorder, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Only binstall is built from source, because it is what installs the rest.

        Args:
            crate: A Rust project root.
            recorder: The command recorder.
            monkeypatch: pytest's patcher.
            tmp_path: pytest's temporary directory.
        """
        cargo_bin = tmp_path / "empty-cargo-bin"
        cargo_bin.mkdir()
        monkeypatch.setattr(rust_tasks, "_cargo_bin", lambda: cargo_bin)
        monkeypatch.setattr(rust_tasks, "have", lambda _: False)

        rust_tasks.cargo_tools(Config.load(root=crate))
        cargo = [c.flags for c in recorder.calls if c.tool == "cargo"]
        assert cargo[0] == ["install", "cargo-binstall", "--locked"]
        assert cargo[1][0] == "binstall"

    def test_security_checks_the_advisory_database(self, crate: Path, recorder: Recorder) -> None:
        """The Rust analogue of govulncheck: dependencies, not the crate's own source.

        Args:
            crate: A Rust project root.
            recorder: The command recorder.
        """
        rust_tasks.security(Config.load(root=crate))
        assert recorder.find("cargo").flags == ["deny", "check", "advisories"]

    def test_license_checks_the_allow_list(self, crate: Path, recorder: Recorder) -> None:
        """The allow-list lives in deny.toml, which is why no ``license_fail_on`` appears here.

        Args:
            crate: A Rust project root.
            recorder: The command recorder.
        """
        rust_tasks.license_(Config.load(root=crate))
        assert recorder.find("cargo").flags == ["deny", "check", "licenses"]

    def test_deps_reports_unused_dependencies(self, crate: Path, recorder: Recorder) -> None:
        """cargo-machete is the deptry of the Rust layer.

        Args:
            crate: A Rust project root.
            recorder: The command recorder.
        """
        rust_tasks.deps(Config.load(root=crate))
        assert recorder.find("cargo").flags == ["machete"]

    def test_cargo_bin_follows_cargo_s_own_variables(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """The two variables cargo reads, in cargo's order, then its default.

        Resolved rather than assumed, because the whole point of the probe is that this
        directory is often not on PATH.

        Args:
            monkeypatch: pytest's patcher.
            tmp_path: pytest's temporary directory.
        """
        monkeypatch.setenv("CARGO_INSTALL_ROOT", str(tmp_path / "install-root"))
        monkeypatch.setenv("CARGO_HOME", str(tmp_path / "home"))
        assert rust_tasks._cargo_bin() == tmp_path / "install-root" / "bin"

        monkeypatch.delenv("CARGO_INSTALL_ROOT")
        assert rust_tasks._cargo_bin() == tmp_path / "home" / "bin"

        monkeypatch.delenv("CARGO_HOME")
        assert rust_tasks._cargo_bin() == Path.home() / ".cargo" / "bin"

    def test_a_gate_without_a_manifest_skips(self, tmp_path: Path) -> None:
        """The guard is the manifest, because cargo finds the sources from it.

        Args:
            tmp_path: An empty directory pinned to the Rust layer.
        """
        cfg = Config.load(root=tmp_path, layers=("rust",))
        state = runner.run(["typecheck"], cfg)
        assert state.status_of("typecheck") is Status.SKIPPED


class TestGoGates:
    """go.mk's recipes, as argument vectors."""

    def test_install_downloads_the_module(
        self, module: Path, recorder: Recorder, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``go mod download``, and no rustup analogue: go.mod pins its own toolchain.

        Args:
            module: A Go module root.
            recorder: The command recorder.
            monkeypatch: pytest's patcher.
        """
        monkeypatch.setattr(go_tasks, "have", lambda _: True)
        go_tasks.install(Config.load(root=module))
        assert recorder.find("go").flags == ["mod", "download"]

    def test_test_runs_with_the_race_detector(self, module: Path, recorder: Recorder) -> None:
        """``-race -shuffle=on`` is go.mk's default, and a setting rather than a literal.

        Args:
            module: A Go module root.
            recorder: The command recorder.
        """
        go_tasks.test(Config.load(root=module))
        assert recorder.find("go").flags == ["test", "./...", "-race", "-shuffle=on"]

    def test_licence_ignores_the_module_itself(
        self, module: Path, recorder: Recorder, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Without ``--ignore $(go list -m)`` the gate fails a fresh project on itself.

        Found by rhiza's e2e suite rather than by a dry run: go-licenses walks the
        project's own packages alongside its dependencies, and a freshly synced project has
        no LICENSE of its own.

        Args:
            module: A Go module root.
            recorder: The command recorder.
            monkeypatch: pytest's patcher.
        """
        monkeypatch.setattr(go_tasks, "capture", lambda *a, **k: "example.com/demo")
        monkeypatch.setattr(go_tasks.shutil, "which", lambda _: None)
        go_tasks.license_(Config.load(root=module))
        call = recorder.calls[-1]
        assert call.tool.endswith("go-licenses")
        assert call.flags == ["check", "./...", "--ignore", "example.com/demo"]

    def test_licence_warns_rather_than_guessing(
        self, module: Path, recorder: Recorder, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """An unreadable module path is reported; the gate still runs.

        Args:
            module: A Go module root.
            recorder: The command recorder.
            monkeypatch: pytest's patcher.
            capsys: pytest's output capture.
        """
        monkeypatch.setattr(go_tasks, "capture", lambda *a, **k: "")
        go_tasks.license_(Config.load(root=module))
        assert "could not read the module path" in capsys.readouterr().out
        assert recorder.calls[-1].flags == ["check", "./..."]

    def test_coverage_converts_and_enforces_the_floor(
        self, module: Path, recorder: Recorder, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """The profile is measured, converted to Cobertura, and checked against the floor.

        ``go test`` has no ``--fail-under``; go.mk reads the total out of ``go tool cover
        -func`` in awk, and this is that awk one-liner.

        Args:
            module: A Go module root.
            recorder: The command recorder.
            monkeypatch: pytest's patcher.
            capsys: pytest's output capture.
        """
        monkeypatch.setattr(go_tasks, "_cobertura", lambda *a: None)
        monkeypatch.setattr(go_tasks, "capture", lambda *a, **k: "total:\t(statements)\t93.4%\n")
        go_tasks.coverage(Config.load(root=module, coverage_fail_under=90))
        measure = recorder.calls[0].flags
        assert measure[:4] == ["test", "./...", "-covermode=atomic", "-coverprofile=_tests/coverage.out"]
        assert recorder.calls[1].flags == [
            "tool",
            "cover",
            "-html=_tests/coverage.out",
            "-o",
            "_tests/html-coverage/index.html",
        ]
        assert "93.4%" in capsys.readouterr().out

    def test_coverage_below_the_floor_fails(
        self, module: Path, recorder: Recorder, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A measured number below the floor is a red gate, as it is in the other layers.

        Args:
            module: A Go module root.
            recorder: The command recorder.
            monkeypatch: pytest's patcher.
        """
        monkeypatch.setattr(go_tasks, "_cobertura", lambda *a: None)
        monkeypatch.setattr(go_tasks, "capture", lambda *a, **k: "total:\t(statements)\t41.0%\n")
        with pytest.raises(Failed, match="below the 90% floor"):
            go_tasks.coverage(Config.load(root=module, coverage_fail_under=90))

    def test_an_unreadable_total_warns_rather_than_passing_silently(
        self, module: Path, recorder: Recorder, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """No total means the floor was not enforced, and says so.

        Args:
            module: A Go module root.
            recorder: The command recorder.
            monkeypatch: pytest's patcher.
            capsys: pytest's output capture.
        """
        monkeypatch.setattr(go_tasks, "_cobertura", lambda *a: None)
        monkeypatch.setattr(go_tasks, "capture", lambda *a, **k: "")
        go_tasks.coverage(Config.load(root=module))
        assert "floor not enforced" in capsys.readouterr().out

    def test_a_malformed_total_reads_as_no_total(
        self, module: Path, recorder: Recorder, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A total line whose number will not parse is the same miss as no total line.

        ``go tool cover`` prints ``-`` for a profile covering no statements, so this is the
        empty-package case rather than a hypothetical. Guessing a number would be worse
        than reporting that the floor went unenforced.

        Args:
            module: A Go module root.
            recorder: The command recorder.
            monkeypatch: pytest's patcher.
            capsys: pytest's output capture.
        """
        monkeypatch.setattr(go_tasks, "_cobertura", lambda *a: None)
        monkeypatch.setattr(go_tasks, "capture", lambda *a, **k: "total:\t(statements)\t-\n")
        go_tasks.coverage(Config.load(root=module))
        assert "floor not enforced" in capsys.readouterr().out

    def test_the_cobertura_conversion_is_a_pipe(self, module: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """gocover-cobertura reads stdin and writes stdout, and still runs without a shell.

        Args:
            module: A Go module root.
            monkeypatch: pytest's patcher.
        """
        seen: dict[str, object] = {}

        def fake_call(argv: list[str], **kwargs: object) -> int:
            """Record the conversion invocation.

            Args:
                argv: The argument vector.
                **kwargs: The redirection handles.

            Returns:
                Zero.
            """
            seen["argv"] = argv
            seen["kwargs"] = kwargs
            return 0

        monkeypatch.setattr(go_tasks.subprocess, "call", fake_call)
        reports = module / "_tests"
        reports.mkdir()
        (reports / "coverage.out").write_text("mode: atomic\n")
        go_tasks._cobertura(Config.load(root=module), reports / "coverage.out", reports / "coverage.xml")
        assert seen["argv"][0].endswith("gocover-cobertura")
        assert seen["kwargs"]["stdin"].name.endswith("coverage.out")
        assert (reports / "coverage.xml").is_file()

    def test_deps_needs_no_tool_at_all(self, module: Path, recorder: Recorder) -> None:
        """``go mod tidy -diff`` is both halves of deptry's job in one command.

        Args:
            module: A Go module root.
            recorder: The command recorder.
        """
        go_tasks.deps(Config.load(root=module))
        assert recorder.find("go").flags == ["mod", "tidy", "-diff"]

    def test_go_tools_installs_into_the_repository(
        self, module: Path, recorder: Recorder, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """GOBIN is the repo's ``bin/``, so a gate never depends on a global GOPATH.

        Args:
            module: A Go module root.
            recorder: The command recorder.
            monkeypatch: pytest's patcher.
        """
        (module / "bin").mkdir()
        (module / "bin" / "revive").touch()
        go_tasks.go_tools(Config.load(root=module))
        installed = [c.flags[1] for c in recorder.calls if c.tool == "go"]
        assert all("revive" not in spec for spec in installed)
        assert len(installed) == len(go_tasks.GO_TOOLS) - 1
        assert recorder.calls[0].kwargs["env"] == {"GOBIN": str(module / "bin")}

    def test_a_tool_on_path_is_preferred(
        self, module: Path, recorder: Recorder, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``bin/`` fills gaps; it does not shadow a tool the developer already has.

        Args:
            module: A Go module root.
            recorder: The command recorder.
            monkeypatch: pytest's patcher.
        """
        monkeypatch.setattr(go_tasks.shutil, "which", lambda name: f"/usr/local/bin/{name}")
        go_tasks.security(Config.load(root=module))
        assert recorder.calls[-1].tool == "govulncheck"

    def test_install_without_go_says_where_to_get_it(self, module: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """A missing toolchain is a failure with an address, as the Rust layer does it.

        Args:
            module: A Go module root.
            monkeypatch: pytest's patcher.
        """
        monkeypatch.setattr(go_tasks, "have", lambda _: False)
        with pytest.raises(Failed, match=r"go\.dev"):
            go_tasks.install(Config.load(root=module))

    def test_install_warns_when_there_is_no_module_file(
        self,
        tmp_path: Path,
        recorder: Recorder,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """No go.mod means nothing to download -- a warning, not a download of nothing.

        Args:
            tmp_path: An empty directory pinned to the Go layer.
            recorder: The command recorder.
            monkeypatch: pytest's patcher.
            capsys: pytest's output capture.
        """
        monkeypatch.setattr(go_tasks, "have", lambda _: True)
        go_tasks.install(Config.load(root=tmp_path, layers=("go",)))
        assert "no go.mod" in capsys.readouterr().out
        assert recorder.calls == []

    def test_typecheck_vets_then_lints(self, module: Path, recorder: Recorder, monkeypatch: pytest.MonkeyPatch) -> None:
        """The compiler already type-checks, so this layer's ``typecheck`` is vet plus lint.

        Args:
            module: A Go module root.
            recorder: The command recorder.
            monkeypatch: pytest's patcher.
        """
        monkeypatch.setattr(go_tasks.shutil, "which", lambda name: f"/usr/local/bin/{name}")
        go_tasks.typecheck(Config.load(root=module))
        assert [(c.tool, c.flags) for c in recorder.calls] == [
            ("go", ["vet", "./..."]),
            ("golangci-lint", ["run"]),
        ]

    def test_typecheck_passes_the_configured_go_flags(
        self, module: Path, recorder: Recorder, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``go_flags`` reaches vet, as it reaches every other go invocation.

        Args:
            module: A Go module root.
            recorder: The command recorder.
            monkeypatch: pytest's patcher.
        """
        monkeypatch.setattr(go_tasks.shutil, "which", lambda name: f"/usr/local/bin/{name}")
        go_tasks.typecheck(Config.load(root=module, go_flags=("-tags=integration",)))
        assert recorder.find("go").flags == ["vet", "./...", "-tags=integration"]

    def test_docs_coverage_sets_the_exit_status(
        self, module: Path, recorder: Recorder, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Undocumented exports are revive's job, and ``-set_exit_status`` makes it a gate.

        Without that flag revive prints its findings and exits 0, which is a report rather
        than a gate.

        Args:
            module: A Go module root.
            recorder: The command recorder.
            monkeypatch: pytest's patcher.
        """
        monkeypatch.setattr(go_tasks.shutil, "which", lambda name: f"/usr/local/bin/{name}")
        go_tasks.docs_coverage(Config.load(root=module))
        assert recorder.find("revive").flags == ["-config", "revive.toml", "-set_exit_status", "./..."]

    def test_a_failed_cobertura_conversion_propagates_its_status(
        self, module: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The one recipe that needs a pipe still reports the converter's own status.

        Args:
            module: A Go module root.
            monkeypatch: pytest's patcher.
        """
        reports = module / "_tests"
        reports.mkdir()
        profile = reports / "coverage.out"
        profile.write_text("mode: atomic\n")

        monkeypatch.setattr(go_tasks.shutil, "which", lambda name: f"/usr/local/bin/{name}")
        monkeypatch.setattr(go_tasks.subprocess, "call", lambda *a, **k: 2)
        with pytest.raises(Failed, match="gocover-cobertura failed") as excinfo:
            go_tasks._cobertura(Config.load(root=module), profile, reports / "coverage.xml")
        assert excinfo.value.code == 2
