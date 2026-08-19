"""The task bodies, and specifically the behaviours the make recipes expressed in shell.

Every test asserts on the argument vector that *would* have been executed, which is what
the make recipes said in ``$$``-escaped shell.
"""

from __future__ import annotations

import shutil
from dataclasses import replace
from pathlib import Path

import pytest

from rhiza_task.config import Config
from rhiza_task.spec import Failed, Skip
from rhiza_task.tasks import book as book_tasks
from rhiza_task.tasks import extras, python, quality
from rhiza_task.tasks.doctor import at_least, parse_version

from .conftest import Recorder


class TestInstall:
    """python.mk's ``install``."""

    def test_creates_the_venv_and_syncs_frozen(self, cfg: Config, recorder: Recorder) -> None:
        """With a lock file present, the sync is ``--frozen``.

        Args:
            cfg: The resolved config.
            recorder: The uv recorder.
        """
        (cfg.root / "uv.lock").write_text("")
        python.install(cfg)
        sync = next(c for c in recorder.calls if c.tool == "sync")
        assert "--frozen" in sync.flags
        assert "--inexact" in sync.flags

    def test_syncs_unfrozen_without_a_lock_file(self, cfg: Config, recorder: Recorder) -> None:
        """With no lock file, uv is allowed to resolve.

        Args:
            cfg: The resolved config.
            recorder: The uv recorder.
        """
        python.install(cfg)
        sync = next(c for c in recorder.calls if c.tool == "sync")
        assert "--frozen" not in sync.flags

    def test_stale_lock_file_fails_with_guidance(self, cfg: Config, recorder: Recorder) -> None:
        """An out-of-sync lock file fails, naming the fix.

        Args:
            cfg: The resolved config.
            recorder: The uv recorder.
        """
        (cfg.root / "uv.lock").write_text("")
        recorder.codes = [0, 1]  # venv ok, `lock --check` reports drift
        with pytest.raises(Failed, match="uv lock"):
            python.install(cfg)

    def test_skips_a_project_without_a_manifest(self, tmp_path: Path, recorder: Recorder) -> None:
        """No ``pyproject.toml`` is a skip, not a failure.

        Args:
            tmp_path: An empty repository.
            recorder: The uv recorder.
        """
        with pytest.raises(Skip, match="pyproject"):
            python.install(Config.load(root=tmp_path))


class TestTest:
    """python.mk's ``test`` -- the recipe that justified a real language."""

    def test_passes_coverage_flags_when_source_exists(self, cfg: Config, recorder: Recorder) -> None:
        """Coverage is measured against the configured source folder.

        Args:
            cfg: The resolved config.
            recorder: The uv recorder.
        """
        python.test(cfg)
        call = recorder.find("pytest")
        assert "--cov=src" in call.flags
        assert f"--cov-fail-under={cfg.coverage_fail_under}" in call.flags
        assert set(python.PYTEST_WITHS) <= set(call.kwargs["withs"])

    def test_runs_without_coverage_when_source_is_absent(self, cfg: Config, recorder: Recorder) -> None:
        """A missing source folder degrades to an uncovered run rather than skipping.

        The tests exist and must still run -- only coverage is unavailable. python.mk gets
        this right and it is easy to lose in translation.

        Args:
            cfg: The resolved config.
            recorder: The uv recorder.
        """
        import shutil

        shutil.rmtree(cfg.path("source_folder"))
        python.test(cfg)
        call = recorder.find("pytest")
        assert not [f for f in call.flags if f.startswith("--cov")]
        assert "--html=_tests/html-report/report.html" in call.flags

    def test_retries_once_on_a_teardown_error(self, cfg: Config, recorder: Recorder) -> None:
        """Exit 3 is retried; a passing retry is a pass.

        Args:
            cfg: The resolved config.
            recorder: The uv recorder.
        """
        recorder.codes = [python.PYTEST_INTERNAL_ERROR, 0]
        python.test(cfg)
        assert recorder.tools().count("pytest") == 2

    def test_gives_up_after_two_teardown_errors(self, cfg: Config, recorder: Recorder) -> None:
        """A persistent exit 3 fails rather than looping.

        Args:
            cfg: The resolved config.
            recorder: The uv recorder.
        """
        recorder.codes = [python.PYTEST_INTERNAL_ERROR, python.PYTEST_INTERNAL_ERROR]
        with pytest.raises(Failed, match="internal"):
            python.test(cfg)
        assert recorder.tools().count("pytest") == python.MAX_ATTEMPTS

    @pytest.mark.parametrize("code", [1, 2, 4])
    def test_does_not_retry_a_real_failure(self, cfg: Config, recorder: Recorder, code: int) -> None:
        """Test failures, interruptions and usage errors fail immediately.

        This is the distinction the whole retry rests on: retrying exit 1 would turn a red
        suite into a slow red suite, and could mask a flaky test as a pass.

        Args:
            cfg: The resolved config.
            recorder: The uv recorder.
            code: A non-teardown pytest exit status.
        """
        recorder.codes = [code, 0]
        with pytest.raises(Failed):
            python.test(cfg)
        assert recorder.tools().count("pytest") == 1

    def test_clears_stale_coverage_data_first(self, cfg: Config, recorder: Recorder) -> None:
        """A corrupt ``.coverage`` from a crashed run cannot report a false 0%.

        Args:
            cfg: The resolved config.
            recorder: The uv recorder.
        """
        (cfg.root / ".coverage").write_text("corrupt")
        (cfg.root / ".coverage.host.1").write_text("corrupt")
        python.test(cfg)
        assert not list(cfg.root.glob(".coverage*"))


class TestCoverage:
    """The ``coverage`` gate the Python layer was missing."""

    def test_writes_the_cobertura_path_the_other_layers_write(self, cfg: Config, recorder: Recorder) -> None:
        """One name, one output path, in all three layers.

        python.mk had no ``coverage`` target -- its ``test`` recipe carries the ``--cov``
        flags -- while rust.mk and go.mk both define one, and the gate-parity contract
        lists it for all three.

        Args:
            cfg: The resolved config.
            recorder: The uv recorder.
        """
        python.coverage(cfg)
        flags = recorder.find("pytest").flags
        assert "--cov-report=xml:_tests/coverage.xml" in flags
        assert f"--cov-fail-under={cfg.coverage_fail_under}" in flags
        assert "--html=_tests/html-report/report.html" not in flags

    def test_shares_its_flags_with_test(self, cfg: Config, recorder: Recorder) -> None:
        """``test`` and ``coverage`` cannot drift, because the flags are built once.

        Args:
            cfg: The resolved config.
            recorder: The uv recorder.
        """
        python.test(cfg)
        from_test = set(recorder.find("pytest").flags)
        assert set(python.coverage_args(cfg)) <= from_test

    def test_skips_without_a_source_folder(self, cfg: Config, recorder: Recorder) -> None:
        """Coverage of nothing is the skip ``--strict`` exists to catch.

        ``test`` warns and runs anyway, because the tests still have to run; ``coverage``
        has nothing left to do at all.

        Args:
            cfg: The resolved config.
            recorder: The uv recorder.
        """
        from rhiza_task import runner

        shutil.rmtree(cfg.path("source_folder"))
        state = runner.run(["coverage"], cfg)
        assert state.status_of("coverage") is runner.Status.SKIPPED
        assert "pytest" not in recorder.tools()


class TestTypecheck:
    """python.mk's ``typecheck``, including the ty/mypy asymmetry."""

    def test_ty_only_by_default(self, cfg: Config, recorder: Recorder) -> None:
        """One checker, no ``--strict``.

        Args:
            cfg: The resolved config.
            recorder: The uv recorder.
        """
        python.typecheck(cfg)
        assert recorder.tools() == ["ty"]
        assert recorder.find("ty").flags == ["check", "src"]

    def test_both_runs_ty_then_mypy_strict(self, repo: Path, recorder: Recorder) -> None:
        """``both`` runs them in order, and only mypy gets ``--strict``.

        Args:
            repo: The repository root.
            recorder: The uv recorder.
        """
        python.typecheck(Config.load(root=repo, typechecker="both"))
        assert recorder.tools() == ["ty", "mypy"]
        assert recorder.find("mypy").flags == ["--strict", "src"]


class TestDerivedAccumulators:
    """The ``+=`` accumulators, replaced by derivation from the registry."""

    def test_deps_adds_the_marimo_folder_and_dep004(self, cfg: Config, recorder: Recorder) -> None:
        """marimo.mk's two ``+=`` lines, derived instead of accumulated.

        Args:
            cfg: The resolved config.
            recorder: The uv recorder.
        """
        cfg.path("marimo_folder").mkdir(parents=True)
        python.deps(cfg)
        flags = recorder.find("deptry").flags
        assert cfg.marimo_folder in [*recorder.find("deptry").args]
        assert "DEP004" in flags

    def test_deps_omits_dep004_without_notebooks(self, cfg: Config, recorder: Recorder) -> None:
        """No notebook folder, no notebook-specific ignore.

        Args:
            cfg: The resolved config.
            recorder: The uv recorder.
        """
        python.deps(cfg)
        assert "DEP004" not in recorder.find("deptry").flags

    def test_deps_skips_when_nothing_to_scan(self, tmp_path: Path, recorder: Recorder) -> None:
        """With no source and no notebooks there is nothing to check.

        Args:
            tmp_path: An empty repository.
            recorder: The uv recorder.
        """
        with pytest.raises(Skip, match="deptry"):
            python.deps(Config.load(root=tmp_path))

    def test_license_exempts_docutils_and_keeps_partial_match(self, cfg: Config, recorder: Recorder) -> None:
        """``--partial-match`` is present, and marimo's docutils exemption is derived.

        Without ``--partial-match`` the gate compares against the whole licence string and
        ``GPL`` never equals "GNU General Public License v2 or later (GPLv2+)", which is how
        it passed with a GPL package installed.

        Args:
            cfg: The resolved config.
            recorder: The uv recorder.
        """
        python.license_(cfg)
        flags = recorder.find("pip-licenses").flags
        assert "--partial-match" in flags
        assert "--fail-on=GPL;LGPL;AGPL" in flags
        assert "docutils" in flags

    def test_license_omits_the_ignore_flag_when_nothing_is_exempt(
        self, cfg: Config, recorder: Recorder, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``--ignore-packages`` errors on a bare flag, so it is omitted entirely.

        Args:
            cfg: The resolved config.
            recorder: The uv recorder.
            monkeypatch: pytest's patcher.
        """
        from rhiza_task.spec import REGISTRY

        monkeypatch.delitem(REGISTRY, "marimo")
        python.license_(cfg)
        assert "--ignore-packages" not in recorder.find("pip-licenses").flags


class TestQuality:
    """quality.mk's gates."""

    def test_fmt_names_the_config_explicitly(self, cfg: Config, recorder: Recorder) -> None:
        """Without ``--config``, prek treats nested configs as separate projects.

        Args:
            cfg: The resolved config.
            recorder: The uv recorder.
        """
        (cfg.root / ".pre-commit-config.yaml").write_text("repos: []\n")
        quality.fmt(cfg)
        assert recorder.find("prek").flags == ["run", "--all-files", "--config", ".pre-commit-config.yaml"]

    def test_rhiza_test_runs_the_pinned_plugin(self, cfg: Config, recorder: Recorder) -> None:
        """The checks arrive installed and enumerated, never globbed.

        Globbing would collect pytest-rhiza's Rust and Go modules, which cannot pass here.
        This is jointview's 60-line Makefile override, as a default.

        Args:
            cfg: The resolved config.
            recorder: The uv recorder.
        """
        quality.rhiza_test(cfg)
        call = recorder.find("pytest")
        assert "--pyargs" in call.flags
        assert "pytest_rhiza.checks.test_readme" in call.flags
        assert not [f for f in call.flags if "test_cargo_toml" in f or "test_go_module" in f]
        assert call.kwargs["withs"] == (cfg.pytest_rhiza,)

    def test_rhiza_test_tells_the_docstring_check_where_to_look(self, cfg: Config, recorder: Recorder) -> None:
        """``RHIZA_DOCTEST_FOLDERS`` carries ``source_folder`` into the checks.

        Without it ``test_docstrings`` falls back to a literal ``src`` and reports
        ``SKIPPED  No doctest folder found`` -- a green gate that measured nothing, which is
        exactly the regression rhiza's doctest gate exists to prevent.

        Args:
            cfg: The resolved config.
            recorder: The uv recorder.
        """
        quality.rhiza_test(cfg)
        assert recorder.find("pytest").kwargs["env"] == {"RHIZA_DOCTEST_FOLDERS": cfg.source_folder}

    def test_rhiza_test_honours_a_relocated_source_folder(self, cfg: Config, recorder: Recorder) -> None:
        """A repo declaring ``source-folder = "utils"`` gets its doctests checked.

        The default would pass the assertion above by coincidence, since ``source_folder``
        already defaults to ``src``. This is the case that actually failed: rhiza itself
        ships configuration, so its only non-test Python is ``utils/``.

        Args:
            cfg: The resolved config.
            recorder: The uv recorder.
        """
        relocated = replace(cfg, source_folder="utils")
        quality.rhiza_test(relocated)
        assert recorder.find("pytest").kwargs["env"] == {"RHIZA_DOCTEST_FOLDERS": "utils"}

    def test_todos_reports_file_and_line(self, cfg: Config, capsys: pytest.CaptureFixture[str]) -> None:
        """A TODO is reported with a repo-relative path and a line number.

        Args:
            cfg: The resolved config.
            capsys: pytest's output capture.
        """
        (cfg.root / "src" / "thing.py").write_text("x = 1\n# TODO: fix me\n")
        quality.todos(cfg)
        out = capsys.readouterr().out
        assert "src/thing.py:2" in out
        assert "1 item(s) found" in out

    def test_todos_ignores_the_virtualenv(self, cfg: Config, capsys: pytest.CaptureFixture[str]) -> None:
        """A dependency's TODO comments are not this project's business.

        Args:
            cfg: The resolved config.
            capsys: pytest's output capture.
        """
        venv = cfg.root / ".venv" / "lib"
        venv.mkdir(parents=True)
        (venv / "vendored.py").write_text("# FIXME: not ours\n")
        quality.todos(cfg)
        assert "0 item(s) found" in capsys.readouterr().out


class TestMutation:
    """test.mk's ``mutation``, and its exit-status subtlety."""

    def test_reports_the_run_status_but_still_builds_the_report(self, cfg: Config, recorder: Recorder) -> None:
        """Surviving mutants fail the gate *after* the HTML report is produced.

        The report is worth having precisely when the run fails, which is why the status is
        remembered rather than propagated immediately.

        Args:
            cfg: The resolved config.
            recorder: The uv recorder.
        """
        recorder.codes = [2, 0, 0]  # run reports survivors, html and results succeed
        with pytest.raises(Failed, match="survived"):
            extras.mutation(cfg)
        assert recorder.tools() == ["mutmut", "mutmut", "mutmut"]
        assert [c.flags[0] for c in recorder.calls] == ["run", "html", "results"]


class TestHypothesis:
    """test.mk's ``hypothesis-test``."""

    def test_no_tests_collected_is_not_a_failure(self, cfg: Config, recorder: Recorder) -> None:
        """A project with no property tests is a valid project.

        Args:
            cfg: The resolved config.
            recorder: The uv recorder.
        """
        recorder.codes = [extras.PYTEST_NO_TESTS_COLLECTED]
        extras.hypothesis_test(cfg)

    def test_a_failing_property_fails_the_gate(self, cfg: Config, recorder: Recorder) -> None:
        """Exit 1 is a real failure.

        Args:
            cfg: The resolved config.
            recorder: The uv recorder.
        """
        recorder.codes = [1]
        with pytest.raises(Failed):
            extras.hypothesis_test(cfg)


class TestBook:
    """book.mk's ``book``."""

    def test_skips_without_a_mkdocs_config(self, cfg: Config, recorder: Recorder) -> None:
        """No ``mkdocs.yml``, nothing to build.

        Args:
            cfg: The resolved config.
            recorder: The uv recorder.
        """
        with pytest.raises(Skip, match="mkdocs"):
            book_tasks.book(cfg)

    def test_builds_and_writes_nojekyll(self, cfg: Config, recorder: Recorder) -> None:
        """The output directory gets a ``.nojekyll`` marker for GitHub Pages.

        Args:
            cfg: The resolved config.
            recorder: The uv recorder.
        """
        (cfg.root / "mkdocs.yml").write_text("site_name: demo\n")
        book_tasks.book(cfg)
        assert (cfg.path("book_output") / ".nojekyll").is_file()
        assert recorder.find(f"zensical{cfg.zensical_version}").flags[0] == "build"

    def test_mkdocstrings_is_provisioned_by_default(self, cfg: Config, recorder: Recorder) -> None:
        """The zensical run must carry mkdocstrings without the repo configuring anything.

        rhiza's ``book`` bundle enables the plugin in ``docs/mkdocs-base.yml`` for every
        consumer, and no bundle can install it -- so an empty default made zensical fail with
        "mkdocstrings plugin is enabled, but mkdocstrings is not installed" on a freshly
        synced repo. Asserted on ``withs`` rather than on the config value, because passing
        the setting to :func:`uvx` is the half that actually reaches the build.

        Args:
            cfg: The resolved config.
            recorder: The uv recorder.
        """
        (cfg.root / "mkdocs.yml").write_text("site_name: demo\n")
        book_tasks.book(cfg)
        withs = recorder.find(f"zensical{cfg.zensical_version}").kwargs["withs"]
        assert any("mkdocstrings" in spec for spec in withs), (
            f"the book build must provision mkdocstrings; withs={withs!r}"
        )

    def test_extra_packages_can_be_turned_off_in_the_manifest(self, cfg: Config, recorder: Recorder) -> None:
        """``mkdocs-extra-packages = []`` must reach the build as no packages at all.

        The escape hatch for the non-empty default. TOML is the only layer that can express
        it -- ``.rhiza/.env`` and the environment drop empties on purpose -- so if this
        stopped working the default would be impossible to opt out of.

        Args:
            cfg: The resolved config.
            recorder: The uv recorder.
        """
        (cfg.root / "mkdocs.yml").write_text("site_name: demo\n")
        (cfg.root / "pyproject.toml").write_text(
            '[project]\nname = "demo"\nversion = "0.0.0"\n\n[tool.rhiza-task]\nmkdocs-extra-packages = []\n'
        )
        reloaded = Config.load(root=cfg.root)
        assert reloaded.mkdocs_extra_packages == ()
        book_tasks.book(reloaded)
        assert not recorder.find(f"zensical{reloaded.zensical_version}").kwargs["withs"]

    def test_generates_a_coverage_badge_when_coverage_exists(self, cfg: Config, recorder: Recorder) -> None:
        """The badge is produced from the XML report that ``test`` leaves behind.

        Args:
            cfg: The resolved config.
            recorder: The uv recorder.
        """
        (cfg.root / "mkdocs.yml").write_text("site_name: demo\n")
        (cfg.root / "_tests").mkdir()
        (cfg.root / "_tests" / "coverage.xml").write_text("<coverage/>")
        book_tasks.book(cfg)
        assert "genbadge[coverage]" in recorder.tools()


class TestDoctorVersions:
    """doctor.mk's awk version comparison, as two functions."""

    @pytest.mark.parametrize(
        ("output", "expected"),
        [
            ("uv 0.9.2 (abc123 2026-01-01)", (0, 9, 2)),
            ("git version 2.39.5 (Apple Git-154)", (2, 39, 5)),
            ("GNU Make 4.4.1\nBuilt for x86_64", (4, 4, 1)),
            ("no version here", ()),
            ("", ()),
        ],
    )
    def test_parses_the_first_dotted_version(self, output: str, expected: tuple[int, ...]) -> None:
        """One regex replaces five per-tool awk extraction commands.

        Args:
            output: Raw ``--version`` output.
            expected: The parsed version.
        """
        assert parse_version(output) == expected

    @pytest.mark.parametrize(
        ("found", "minimum", "ok"),
        [
            ((0, 9, 2), "0.4.0", True),
            ((0, 4, 0), "0.4.0", True),
            ((0, 3, 9), "0.4.0", False),
            ((2, 39), "2.0.0", True),
            ((1,), "1.0.0", True),
            ((4, 4, 1), "3.8.0", True),
        ],
    )
    def test_compares_versions_component_wise(self, found: tuple[int, ...], minimum: str, ok: bool) -> None:
        """Shorter versions are zero-padded, so ``2.39`` satisfies ``2.0.0``.

        Args:
            found: The installed version.
            minimum: The requirement.
            ok: Whether it should pass.
        """
        assert at_least(found, minimum) is ok


class TestSecurity:
    """python.mk's ``security``."""

    def test_passes_the_ini_when_it_exists(self, cfg: Config, recorder: Recorder) -> None:
        """The shared scan scope is honoured when the project ships one.

        Args:
            cfg: The resolved config.
            recorder: The uv recorder.
        """
        (cfg.root / ".bandit").write_text("[bandit]\n")
        python.security(cfg)
        assert recorder.find("bandit").flags[-2:] == ["--ini", ".bandit"]

    def test_omits_the_ini_when_absent(self, cfg: Config, recorder: Recorder) -> None:
        """A project with no ``.bandit`` still gets a real scan.

        bandit treats a missing ini as a usage error, so passing the flag unconditionally --
        as python.mk does -- turns a configuration gap into a red security gate.

        Args:
            cfg: The resolved config.
            recorder: The uv recorder.
        """
        python.security(cfg)
        assert "--ini" not in recorder.find("bandit").flags
