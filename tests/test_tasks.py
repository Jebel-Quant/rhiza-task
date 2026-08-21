"""The task bodies, and specifically the behaviours the make recipes expressed in shell.

Every test asserts on the argument vector that *would* have been executed, which is what
the make recipes said in ``$$``-escaped shell.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from rhiza_task.config import Config
from rhiza_task.spec import Failed, Skip, lookup
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

    def test_reuses_an_existing_virtualenv(
        self, cfg: Config, recorder: Recorder, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A second run does not recreate the environment, and names the one it reused.

        Args:
            cfg: The resolved config.
            recorder: The uv recorder.
            capsys: pytest's output capture.
        """
        (cfg.root / ".venv").mkdir()
        python.install(cfg)
        assert "venv" not in recorder.tools()
        assert str(cfg.root / ".venv") in capsys.readouterr().out


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

    def test_clears_stale_coverage_data_first(self, cfg: Config, recorder: Recorder) -> None:
        """Leftover data from an interrupted run must not be merged into this one.

        Both shapes go: the plain ``.coverage`` file and the per-process
        ``.coverage.<host>.<pid>`` shards that xdist leaves behind.

        Args:
            cfg: The resolved config.
            recorder: The uv recorder.
        """
        (cfg.root / ".coverage").write_text("stale")
        (cfg.root / ".coverage.host.4711").write_text("stale")
        python.coverage(cfg)
        assert not (cfg.root / ".coverage").exists()
        assert not (cfg.root / ".coverage.host.4711").exists()
        assert (cfg.root / "_tests" / "html-coverage").is_dir()


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

    def test_fmt_skips_without_a_config(self, cfg: Config, recorder: Recorder) -> None:
        """No ``.pre-commit-config.yaml`` is a skip with a reason, not a failure.

        This is the outcome that lets one aggregate serve repositories with different
        bundles installed -- and the one ``--strict`` promotes when a consumer wants a
        missing config to be a red build.

        Args:
            cfg: The resolved config.
            recorder: The uv recorder.
        """
        with pytest.raises(Skip, match=r"\.pre-commit-config\.yaml"):
            quality.fmt(cfg)
        assert recorder.calls == []

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

    def test_todos_skips_an_unreadable_file_without_losing_the_rest(
        self, cfg: Config, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """One unopenable path costs its own hits, not every hit after it.

        Patched rather than provoked with a permission bit: ``chmod`` does not deny the
        owner a read on Windows, and Windows is in the matrix deliberately.

        Args:
            cfg: The resolved config.
            monkeypatch: pytest's patcher.
            capsys: pytest's output capture.
        """
        (cfg.root / "src" / "locked.py").write_text("# TODO: never seen\n")
        (cfg.root / "src" / "open.py").write_text("# TODO: still reported\n")
        real = Path.read_text

        def read_text(self: Path, *args: object, **kwargs: object) -> str:
            """Raise for the locked file, and read everything else normally.

            Args:
                self: The path being read.
                *args: Passed through.
                **kwargs: Passed through.

            Returns:
                The file's text.

            Raises:
                PermissionError: When the path is the locked file.
            """
            if self.name == "locked.py":
                raise PermissionError(self.name)
            return real(self, *args, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(Path, "read_text", read_text)
        quality.todos(cfg)
        out = capsys.readouterr().out
        assert "src/open.py:1" in out
        assert "locked.py" not in out
        assert "1 item(s) found" in out


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

    def test_relocates_the_generated_html_report(self, cfg: Config, recorder: Recorder) -> None:
        """The report lands in ``html/`` at the root, and belongs under ``_tests``.

        The previous run's report is removed rather than merged, so the folder describes
        one run.

        Args:
            cfg: The resolved config.
            recorder: The uv recorder.
        """
        generated = cfg.root / "html"
        generated.mkdir()
        (generated / "index.html").write_text("<html></html>")
        previous = cfg.root / "_tests" / "mutation" / "html"
        previous.mkdir(parents=True)
        (previous / "stale.html").write_text("stale")

        extras.mutation(cfg)

        moved = cfg.root / "_tests" / "mutation" / "html"
        assert (moved / "index.html").is_file()
        assert not (moved / "stale.html").exists()
        assert not generated.exists()


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

    def test_names_the_paper_as_a_prerequisite(self) -> None:
        """The book publishes the paper's PDF, so building the book must compile it.

        Asserted on the registry rather than on a vector, because there is no vector: the
        PDF needs no copy step. latexmk writes it beside its source and ``paper_folder`` is
        inside ``docs_dir``, so the only two moving parts are this prerequisite and the
        ``nav`` entry in ``mkdocs.yml`` -- and this is the half a test can hold.
        """
        spec = lookup("book")
        assert spec is not None
        assert "paper" in spec.needs

    def test_prunes_latex_artifacts_but_keeps_the_pdf(self, cfg: Config, monkeypatch: pytest.MonkeyPatch) -> None:
        """The published site carries the paper's PDF and source, not latexmk's scratch files.

        ``paper.log`` is the one that matters: it records absolute paths from the machine
        that built it, and publishing it to Pages is a leak of build environment rather than
        a cosmetic wart. zensical cannot exclude it, so the prune happens after the build.

        Args:
            cfg: The resolved config.
            monkeypatch: pytest's patcher.
        """
        (cfg.root / "mkdocs.yml").write_text("site_name: demo\n")
        published = cfg.path("book_output") / "paper"

        # The fake site has to be written by the stand-in for zensical, not before the call:
        # `book` rmtree's the output first, so anything staged earlier is gone by then.
        def fake_uvx(tool: str, *args: str, **kwargs: object) -> int:
            """Write the paper folder the real build would have copied out of docs_dir.

            Args:
                tool: The tool name, unused.
                *args: Its arguments, unused.
                **kwargs: Ignored.

            Returns:
                0, as a successful build does.
            """
            published.mkdir(parents=True, exist_ok=True)
            for name in ("paper.pdf", "paper.tex", "paper.aux", "paper.log", "paper.out", "paper.synctex.gz"):
                (published / name).write_text("x")
            return 0

        monkeypatch.setattr(book_tasks, "uvx", fake_uvx)
        book_tasks.book(cfg)

        assert sorted(p.name for p in published.iterdir()) == ["paper.pdf", "paper.tex"]

    def test_leaves_a_paper_folder_outside_the_docs_tree_alone(self, cfg: Config, recorder: Recorder) -> None:
        """A paper outside ``docs/`` was never copied into the site, so there is nothing to prune.

        The guard matters because the prune is scoped by a relative path: without the check,
        ``relative_to`` would raise on a ``paper_folder`` the build never published.

        Args:
            cfg: The resolved config.
            recorder: The uv recorder.
        """
        (cfg.root / "mkdocs.yml").write_text("site_name: demo\n")
        outside = replace(cfg, paper_folder="writing/paper")
        (cfg.root / "writing" / "paper").mkdir(parents=True)

        book_tasks.book(outside)

        assert (cfg.path("book_output") / ".nojekyll").is_file()

    def test_tolerates_a_site_with_no_published_paper_folder(self, cfg: Config, recorder: Recorder) -> None:
        """``paper`` skipped, so the build copied no paper folder and the prune is a no-op.

        The common case in a repository with no ``.tex`` at all, and the one that must not
        raise on a missing directory.

        Args:
            cfg: The resolved config.
            recorder: The uv recorder.
        """
        (cfg.root / "mkdocs.yml").write_text("site_name: demo\n")
        book_tasks.book(cfg)
        assert not (cfg.path("book_output") / "paper").exists()

    def test_masks_build_paths_in_the_published_reports(self, cfg: Config, recorder: Recorder) -> None:
        """The published copy carries no absolute paths; ``_tests/`` keeps them.

        A report is written for the machine that produced it and then published to the web.
        pytest records the repository root as its ``rootdir``, and pytest-xdist stamps every
        test with a worker banner naming the interpreter -- which under ``uv run --with``
        sits in the user's home. Both are masked in the copy, and deliberately left alone in
        ``_tests/``, which is what a developer reads locally.

        Args:
            cfg: The resolved config.
            recorder: The uv recorder.
        """
        (cfg.root / "mkdocs.yml").write_text("site_name: demo\n")
        reports = cfg.root / "_tests"
        reports.mkdir()
        banner = f"[gw0] linux -- Python 3.11.15 {Path.home()}/.cache/uv/bin/python"
        original = f"rootdir: {cfg.root}\n{banner}\n"
        (reports / "report.html").write_text(original)
        (reports / "coverage.xml").write_text(f"<source>{cfg.root}/src</source>")

        book_tasks.book(cfg)

        published = cfg.root / "docs" / "reports"
        scrubbed = (published / "report.html").read_text()
        assert str(cfg.root) not in scrubbed
        assert str(Path.home()) not in scrubbed
        assert "rootdir: ." in scrubbed
        assert "~/.cache/uv/bin/python" in scrubbed
        assert (published / "coverage.xml").read_text() == "<source>./src</source>"

        # The source of truth for a local reader is untouched.
        assert (reports / "report.html").read_text() == original

    def test_leaves_binary_and_unreadable_report_files_alone(self, cfg: Config, recorder: Recorder) -> None:
        """A report file that is not decodable UTF-8 is skipped, not fatal.

        The report tree carries PNGs and icons beside its HTML. Those are excluded by suffix
        already, but a ``.json`` or ``.html`` that turns out to be undecodable must not cost
        the book its build -- publishing is not the place to fail on somebody else's artefact.

        Args:
            cfg: The resolved config.
            recorder: The uv recorder.
        """
        (cfg.root / "mkdocs.yml").write_text("site_name: demo\n")
        reports = cfg.root / "_tests"
        reports.mkdir()
        (reports / "broken.json").write_bytes(b"\xff\xfe not utf-8 \x00")
        (reports / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n")

        book_tasks.book(cfg)

        published = cfg.root / "docs" / "reports"
        assert (published / "broken.json").read_bytes() == b"\xff\xfe not utf-8 \x00"
        assert (published / "logo.png").is_file()

    def test_rewrites_only_the_files_that_carry_a_path(self, cfg: Config, recorder: Recorder) -> None:
        """A report with nothing to mask is left byte-identical rather than rewritten.

        Args:
            cfg: The resolved config.
            recorder: The uv recorder.
        """
        (cfg.root / "mkdocs.yml").write_text("site_name: demo\n")
        reports = cfg.root / "_tests"
        reports.mkdir()
        (reports / "clean.html").write_text("<p>nothing absolute here</p>")

        book_tasks.book(cfg)

        assert (cfg.root / "docs" / "reports" / "clean.html").read_text() == "<p>nothing absolute here</p>"

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


class TestBookNav:
    """``book-nav``: the gate on what the built site actually contains."""

    NAV = """\
site_name: demo
nav:
  - Home: index.md
  # a comment inside the block
  - Guides:
      - Deep: guides/deep.md
  - Paper: paper/paper.pdf
  - bare.md
  - Upstream: https://example.com/x.pdf

extra:
  social: []
"""

    def test_parses_titles_sections_bare_paths_and_skips_urls(self) -> None:
        """The parser collects file targets and ignores what names no file.

        Four shapes in one fixture, because they are the whole grammar mkdocs documents and
        the parser is hand-written: a titled page, a section header with no value, an asset,
        a bare path, and an external URL. The URL is the one that must *not* appear -- a
        docs gate that reaches the network fails on somebody else's outage.
        """
        assert book_tasks._nav_targets(self.NAV) == [
            "index.md",
            "guides/deep.md",
            "paper/paper.pdf",
            "bare.md",
        ]

    def test_returns_nothing_without_a_nav_block(self) -> None:
        """A config with no ``nav:`` declares no targets, which is not an error."""
        assert book_tasks._nav_targets("site_name: demo\nextra: {}\n") == []

    def test_ignores_a_nav_written_as_a_mapping(self) -> None:
        """An entry with no ``- `` yields no target, rather than a mis-sliced one.

        Writing ``nav:`` as a mapping instead of a list is a real mkdocs mistake, and the
        branch that skips it is load-bearing rather than defensive: without it the line falls
        through to ``stripped[2:]``, which would strip two characters off the *title* and
        report ``index.md`` as a target it never actually found. Failing to parse is fine
        here; inventing a target is not, because this gate's whole output is a list of names.
        """
        assert book_tasks._nav_targets("nav:\n  Home: index.md\n") == []

    def test_markdown_resolves_to_either_built_form(self) -> None:
        """A page may be published as ``x.html`` or ``x/index.html``; an asset only as itself.

        Which of the two applies is mkdocs's ``use_directory_urls``, and this gate has no
        opinion on it -- accepting either answers "was the page built", which is the question.
        """
        assert book_tasks._built_candidates("faq.md") == ("faq.html", "faq/index.html")
        assert book_tasks._built_candidates("paper/paper.pdf") == ("paper/paper.pdf",)

    def test_passes_when_every_target_was_built(self, cfg: Config) -> None:
        """All four targets present, in both markdown forms, so the gate is silent.

        Args:
            cfg: The resolved config.
        """
        (cfg.root / "mkdocs.yml").write_text(self.NAV)
        output = cfg.path("book_output")
        for built in ("index.html", "guides/deep/index.html", "paper/paper.pdf", "bare.html"):
            target = output / built
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("x")

        book_tasks.book_nav(cfg)

    def test_fails_naming_the_missing_asset(self, cfg: Config, capsys: pytest.CaptureFixture[str]) -> None:
        """The published 404 this gate exists for: a nav entry whose asset was never written.

        This is the paper's case exactly. ``rhiza-task paper`` skips without latexmk, the
        build succeeds, zensical reports ``No issues found``, and the site deploys with a
        dead entry in its own navigation -- so the assertion is that the *name* reaches the
        operator, not merely that the count is wrong.

        Args:
            cfg: The resolved config.
            capsys: pytest's output capture.
        """
        (cfg.root / "mkdocs.yml").write_text(self.NAV)
        output = cfg.path("book_output")
        for built in ("index.html", "guides/deep/index.html", "bare.html"):
            target = output / built
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("x")

        with pytest.raises(Failed, match="1 of 4"):
            book_tasks.book_nav(cfg)
        assert "paper/paper.pdf" in capsys.readouterr().out

    def test_skips_before_the_book_is_built(self, cfg: Config) -> None:
        """Nothing to inspect yet, which is not askable rather than wrong.

        Args:
            cfg: The resolved config.
        """
        (cfg.root / "mkdocs.yml").write_text(self.NAV)
        with pytest.raises(Skip, match="no built book"):
            book_tasks.book_nav(cfg)

    def test_skips_when_the_config_declares_no_nav(self, cfg: Config) -> None:
        """A built book and a config with no ``nav:`` measures nothing.

        Args:
            cfg: The resolved config.
        """
        (cfg.root / "mkdocs.yml").write_text("site_name: demo\n")
        cfg.path("book_output").mkdir(parents=True, exist_ok=True)
        with pytest.raises(Skip, match="no nav targets"):
            book_tasks.book_nav(cfg)

    def test_is_not_a_prerequisite_of_book(self) -> None:
        """``book`` must not need this gate.

        Half the nav entries here resolve only after the gates that produce them have run,
        and a repository with no latexmk must still build its book -- which is what a skipped
        prerequisite buys. Wiring this into ``book`` would turn that into a failure for every
        consumer documenting a paper it cannot compile, so the omission is the design and a
        test holds it.
        """
        spec = lookup("book")
        assert spec is not None
        assert "book-nav" not in spec.needs

    def test_guards_on_the_mkdocs_config(self) -> None:
        """No ``mkdocs.yml``, no nav to check -- declared as a guard, not re-checked in the body."""
        spec = lookup("book-nav")
        assert spec is not None
        assert any(guard.file == "mkdocs.yml" for guard in spec.guards)


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


class TestPythonDocsCoverage:
    """python.mk's ``docs-coverage``."""

    def test_measures_both_folders_the_gate_claims(self, cfg: Config, recorder: Recorder) -> None:
        """Both the source and the test tree are measured, at a 100% floor.

        Args:
            cfg: The resolved config.
            recorder: The uv recorder.
        """
        python.docs_coverage(cfg)
        call = recorder.find("interrogate")
        assert call.flags[:5] == ["-vv", "--fail-under", "100", "--ignore-init-method", "--ignore-magic"]
        assert call.flags[5:] == ["src", "tests"]

    def test_omits_a_folder_that_is_not_there(self, cfg: Config, recorder: Recorder) -> None:
        """A repository with no test folder is measured on what it has.

        Args:
            cfg: The resolved config.
            recorder: The uv recorder.
        """
        shutil.rmtree(cfg.root / "tests")
        python.docs_coverage(cfg)
        assert recorder.find("interrogate").flags[5:] == ["src"]


class TestPyprojectStructure:
    """quality.mk's ``test-pyproject``: one check, reported loudly."""

    def test_runs_only_the_pyproject_check_verbosely(self, cfg: Config, recorder: Recorder) -> None:
        """A narrower, louder view of the one module ``rhiza-test`` also runs.

        Args:
            cfg: The resolved config.
            recorder: The uv recorder.
        """
        quality.test_pyproject(cfg)
        call = recorder.find("pytest")
        assert call.flags[:3] == ["--pyargs", "pytest_rhiza.checks.test_pyproject", "-v"]
        for loud in ("--tb=long", "--showlocals", "-rA", "--durations=0", "--no-header"):
            assert loud in call.flags
        assert call.kwargs["withs"] == (cfg.pytest_rhiza,)


class TestComplexity:
    """The one Python-layer gate with no make ancestor.

    Every other test here asserts the vector and stops, because the vector is the contract.
    This gate reads a *value* back -- radon's per-block numbers -- so ``uvx`` is patched to
    behave like radon and write a report, and the assertions are about the verdict the task
    reaches from it. Same reason ``capture`` is patched for the Go coverage gate.
    """

    @staticmethod
    def _radon(monkeypatch: pytest.MonkeyPatch, payload: object | None) -> list[tuple[str, ...]]:
        """Patch ``uvx`` to write ``payload`` to the file the task's vector names.

        Args:
            monkeypatch: pytest's patcher.
            payload: The object to serialise as radon's report, or None to write nothing --
                which is how a radon that produced no output is simulated.

        Returns:
            The argument vectors the task passed, tool name first, in invocation order.
        """
        seen: list[tuple[str, ...]] = []

        def fake(tool: str, *args: str, cwd: Path, **kwargs: object) -> int:
            """Record the vector and stand in for radon's ``--output-file``.

            Args:
                tool: The tool name.
                *args: Its arguments.
                cwd: Working directory, unused.
                **kwargs: Ignored.

            Returns:
                0, as a successful radon does.
            """
            seen.append((tool, *args))
            if payload is not None:
                Path(args[args.index("--output-file") + 1]).write_text(json.dumps(payload))
            return 0

        monkeypatch.setattr(python, "uvx", fake)
        return seen

    @staticmethod
    def _block(name: str, score: int, line: int = 10, classname: str | None = None) -> dict[str, object]:
        """Build one entry shaped like radon's JSON blocks.

        Args:
            name: The block's own name.
            score: Its cyclomatic complexity.
            line: The line it starts on.
            classname: The owning class, for a method; omitted entirely for a function,
                because radon omits the key rather than setting it to None.

        Returns:
            A block dict.
        """
        block: dict[str, object] = {"type": "function", "complexity": score, "lineno": line, "name": name}
        if classname is not None:
            block["classname"] = classname
        return block

    def test_asks_radon_for_json_in_a_file(self, cfg: Config, monkeypatch: pytest.MonkeyPatch) -> None:
        """The vector names ``--json`` and an ``--output-file``, so nothing needs a pipe.

        This is the assertion that keeps the gate free of a capturing ``uvx``: a change that
        reached for a pipe instead would have to change this vector.

        Args:
            cfg: The resolved config.
            monkeypatch: pytest's patcher.
        """
        seen = self._radon(monkeypatch, {})
        python.complexity(cfg)
        assert seen[0][:5] == ("radon", "cc", "src", "--json", "--output-file")
        assert Path(seen[0][5]) == cfg.root / "_tests" / "complexity.json"

    def test_passes_when_every_block_is_at_or_below_the_ceiling(
        self, cfg: Config, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """The ceiling is inclusive: a block *at* the maximum is not a finding.

        Args:
            cfg: The resolved config.
            monkeypatch: pytest's patcher.
            capsys: pytest's output capture.
        """
        self._radon(monkeypatch, {"src/a.py": [self._block("wide", 15), self._block("narrow", 1)]})
        python.complexity(replace(cfg, complexity_max=15))
        assert "no block above the complexity ceiling of 15" in capsys.readouterr().out

    def test_fails_on_a_block_above_the_ceiling(
        self, cfg: Config, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """One block over the line fails the gate and is named with its file and line.

        Args:
            cfg: The resolved config.
            monkeypatch: pytest's patcher.
            capsys: pytest's output capture.
        """
        self._radon(monkeypatch, {"src/a.py": [self._block("sprawling", 16, line=42)]})
        with pytest.raises(Failed, match=r"1 block\(s\) above the complexity ceiling of 15"):
            python.complexity(replace(cfg, complexity_max=15))
        assert "src/a.py:42 sprawling: 16" in capsys.readouterr().out

    def test_honours_a_raised_ceiling(self, cfg: Config, monkeypatch: pytest.MonkeyPatch) -> None:
        """The ceiling is a setting, so a consumer that wants 20 gets 20.

        The gate would be unusable outside this repository otherwise: 15 is a ceiling for a
        codebase that already argues its C blocks in comments.

        Args:
            cfg: The resolved config.
            monkeypatch: pytest's patcher.
        """
        self._radon(monkeypatch, {"src/a.py": [self._block("sprawling", 18)]})
        python.complexity(replace(cfg, complexity_max=20))

    def test_reports_a_method_by_its_qualified_name(
        self, cfg: Config, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A method reads as ``Class.method``, which is how radon's own text output reads.

        Args:
            cfg: The resolved config.
            monkeypatch: pytest's patcher.
            capsys: pytest's output capture.
        """
        self._radon(monkeypatch, {"src/a.py": [self._block("check", 20, line=7, classname="Guard")]})
        with pytest.raises(Failed):
            python.complexity(cfg)
        assert "src/a.py:7 Guard.check: 20" in capsys.readouterr().out

    def test_orders_the_worst_block_first(
        self, cfg: Config, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Findings are ordered by complexity, then by label, so the output is diffable.

        The tie-break matters: dict order follows radon's file walk, so without it the same
        two findings could swap places between runs and read as a change.

        Args:
            cfg: The resolved config.
            monkeypatch: pytest's patcher.
            capsys: pytest's output capture.
        """
        self._radon(
            monkeypatch,
            {
                "src/b.py": [self._block("tie", 17)],
                "src/a.py": [self._block("worst", 30), self._block("tie", 17)],
            },
        )
        with pytest.raises(Failed, match="3 block"):
            python.complexity(cfg)
        lines = [line for line in capsys.readouterr().out.splitlines() if line.startswith("src/")]
        assert [line.rsplit(": ", 1)[1] for line in lines] == ["30", "17", "17"]
        assert lines[1].startswith("src/a.py")

    def test_skips_a_file_radon_could_not_parse(
        self, cfg: Config, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """An unparseable file is an error dict, not a list, and is not this gate's finding.

        Args:
            cfg: The resolved config.
            monkeypatch: pytest's patcher.
            capsys: pytest's output capture.
        """
        self._radon(monkeypatch, {"src/broken.py": {"error": "invalid syntax"}})
        python.complexity(cfg)
        assert "no block above the complexity ceiling" in capsys.readouterr().out

    def test_skips_when_radon_wrote_no_report(self, cfg: Config, monkeypatch: pytest.MonkeyPatch) -> None:
        """Nothing measured is a skip, not a pass -- ``--strict`` is what promotes it.

        Args:
            cfg: The resolved config.
            monkeypatch: pytest's patcher.
        """
        self._radon(monkeypatch, None)
        with pytest.raises(Skip, match="radon wrote no report"):
            python.complexity(cfg)

    def test_discards_a_report_left_by_an_earlier_run(self, cfg: Config, monkeypatch: pytest.MonkeyPatch) -> None:
        """A stale report is removed before radon runs, so it cannot be read as this verdict.

        Reaching Skip rather than Failed is the assertion: the pre-written report says a
        block scores 99, and a gate that re-read it would fail instead.

        Args:
            cfg: The resolved config.
            monkeypatch: pytest's patcher.
        """
        stale = cfg.root / "_tests" / "complexity.json"
        stale.parent.mkdir(parents=True, exist_ok=True)
        stale.write_text(json.dumps({"src/a.py": [self._block("sprawling", 99)]}))

        self._radon(monkeypatch, None)
        with pytest.raises(Skip):
            python.complexity(cfg)


class TestDocsExamples:
    """``docs-examples``: the fence parser, the five checks, and the inventory.

    Hermetic like the rest of the suite. ``bash`` and ``uv_run`` are both patched, so the
    tests assert the argument vector each check *would* run -- ``bash -n`` over a throwaway
    script, ``uv run python`` over a generated one -- rather than provisioning either. That
    matters more here than elsewhere: this gate's whole job is to run other people's code,
    and a test that really ran it would execute the repository's own documentation.

    The toml check is the one exception, and it is asserted directly: :mod:`tomllib` is stdlib
    at this package's Python floor, so there is no subprocess to stand in for and no vector to
    assert -- the parse either happens in-process or the interpreter could not have run the
    test.
    """

    @staticmethod
    def _docs(cfg: Config, name: str, text: str) -> None:
        """Write one markdown file into the docs folder.

        Args:
            cfg: The resolved config.
            name: File name, e.g. ``guide.md``.
            text: The file's content.
        """
        folder = cfg.path("docs_folder")
        folder.mkdir(parents=True, exist_ok=True)
        (folder / name).write_text(text)

    @staticmethod
    def _bash(monkeypatch: pytest.MonkeyPatch, code: int, stderr: str = "") -> list[list[str]]:
        """Patch out the ``bash -n`` probe and collect the vectors it would have run.

        Args:
            monkeypatch: pytest's patcher.
            code: The exit status every probe reports.
            stderr: The stderr every probe reports.

        Returns:
            The recorded argument vectors, appended to as the gate runs.
        """
        seen: list[list[str]] = []
        monkeypatch.setattr(quality.shutil, "which", lambda _name: "/bin/bash")

        def fake_run(argv: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
            """Record the vector and report the canned outcome.

            Args:
                argv: The argument vector.
                **_kwargs: Ignored.

            Returns:
                A completed process carrying the canned status and stderr.
            """
            seen.append(argv)
            return subprocess.CompletedProcess(argv, code, "", stderr)

        monkeypatch.setattr(quality.subprocess, "run", fake_run)
        return seen

    def test_dedents_a_fence_indented_inside_an_admonition(self) -> None:
        """A fence indented by an admonition compiles, because the body is dedented.

        Without the dedent every fence inside an mkdocs admonition or content tab is an
        ``IndentationError``, which would be a finding against this checker rather than the
        documentation -- and this repository's docs indent eight of them.
        """
        fences = quality._fences("docs/x.md", '!!! tip "t"\n\n    ```python\n    x = 1\n    ```\n')
        assert [(f.line, f.language, f.code) for f in fences] == [(3, "python", "x = 1")]
        assert quality._syntax_violations(fences) == []

    def test_keeps_only_the_language_from_an_attributed_fence(self) -> None:
        """``python title="x"`` is a python fence, and an unlabelled one carries no language.

        The opening pattern is permissive for a reason worth pinning: were it to miss an
        attributed fence, that fence's *closing* backticks would be read as the next opening
        one and every language after it would be wrong.
        """
        fences = quality._fences("d.md", '```python title="a.py"\nx = 1\n```\n\n```\nplain\n```\n')
        assert [f.language for f in fences] == ["python", ""]

    def test_reports_a_python_fence_that_does_not_parse(self) -> None:
        """A malformed python fence is a violation, and a mere fragment is not.

        ``guards = (Guard("source_folder"),)`` in ``adding_a_task.md`` never imports
        ``Guard``, so resolving names would report the documentation as broken when it is
        only partial.
        """
        fences = quality._fences("d.md", "```python\nx = (1,\n```\n\n```python\ny = Undefined(1)\n```\n")
        violations = quality._syntax_violations(fences)
        assert len(violations) == 1
        # Line 2, the offending code, not line 1 where the fence opens.
        assert violations[0].startswith("d.md:2: python fence does not parse:")

    def test_reports_a_shell_fence_that_does_not_parse(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """A non-zero ``bash -n`` is a violation, and the throwaway path is not in the message.

        bash reports against the scratch file and its own line numbers, neither of which the
        reader can act on; the fence's real location is the prefix instead.

        Args:
            monkeypatch: pytest's patcher.
            tmp_path: Scratch directory for the throwaway script.
        """
        seen = self._bash(monkeypatch, 2, f"{tmp_path / 'fence.sh'}: line 2: syntax error\n")
        fences = quality._fences("d.md", "```bash\nif true\n```\n")
        violations = quality._shell_violations(fences, "/bin/bash", tmp_path)
        assert seen[0][:2] == ["/bin/bash", "-n"]
        assert violations == ["d.md:1: shell fence does not parse: fence: line 2: syntax error"]

    def test_falls_back_to_the_exit_status_when_bash_says_nothing(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """A failure with empty stderr still reports, rather than producing an empty message.

        Args:
            monkeypatch: pytest's patcher.
            tmp_path: Scratch directory for the throwaway script.
        """
        self._bash(monkeypatch, 2, "")
        fences = quality._fences("d.md", "```sh\nif true\n```\n")
        violations = quality._shell_violations(fences, "/bin/bash", tmp_path)
        assert violations == ["d.md:1: shell fence does not parse: exit 2"]

    def test_reports_a_toml_fence_that_does_not_parse(self) -> None:
        """A malformed toml fence is a violation, and a table-less fragment is not.

        The fragment half is the one that matters: most toml under ``docs/`` is a handful of
        ``key = value`` lines quoted out of a ``[tool.rhiza-task]`` table, and rejecting those
        would report the documentation as broken for being an excerpt.
        """
        fences = quality._fences(
            "d.md",
            "```toml\n[tool.rhiza-task\n```\n\n```toml\ncoverage_fail_under = 100\n```\n",
        )
        violations = quality._toml_violations(fences)
        assert len(violations) == 1
        assert violations[0].startswith("d.md:1: toml fence does not parse:")

    def test_treats_a_tree_with_no_yaml_fence_as_nothing_to_provision(self, cfg: Config, recorder: Recorder) -> None:
        """No yaml fence means no subprocess at all, rather than an empty one.

        Args:
            cfg: The resolved config.
            recorder: The uv recorder, which must stay empty.
        """
        fences = quality._fences("d.md", "```python\nx = 1\n```\n")
        assert quality._yaml_violations(fences, cfg, cfg.root) == []
        assert recorder.tools() == []

    def test_provisions_a_yaml_parser_rather_than_depending_on_one(self, cfg: Config, recorder: Recorder) -> None:
        """The yaml check runs ``uv run --no-project --with pyyaml python <script>``.

        ``rhiza-task`` is a published CLI, so a runtime dependency is an install cost every
        consumer pays on every invocation. Two fences in this repository's own docs do not
        justify one, which is why the parser is provisioned for the length of a single call.

        Args:
            cfg: The resolved config.
            recorder: The uv recorder.
        """
        scratch = cfg.root / "_tests" / "docs-examples"
        scratch.mkdir(parents=True, exist_ok=True)
        fences = quality._fences("d.md", "```yaml\na: 1\n```\n")
        # None, not []: uv_run is recorded rather than run, so the report file never appears --
        # which is exactly the "could not measure" case, and must not read as "all sound".
        assert quality._yaml_violations(fences, cfg, scratch) is None
        call = recorder.find("python")
        assert call.kind == "uv_run"
        assert call.flags == ["_tests/docs-examples/fence_yaml.py"]
        assert call.kwargs["withs"] == ("pyyaml",)
        assert call.kwargs["no_project"] is True
        # The fence reaches the parser as a file, so no escaping can corrupt indentation.
        assert (scratch / "yaml" / "0000.yaml").read_text() == "a: 1"
        assert (scratch / "yaml" / "index.txt").read_text() == "d.md:1"

    def test_returns_the_violations_the_yaml_parser_reported(
        self, cfg: Config, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A report file written by the subprocess becomes one violation per line.

        ``uv_run`` is patched with a stand-in producing the *effect* the real script has --
        the report file -- which covers the path the verdict depends on without a parser.

        Args:
            cfg: The resolved config.
            monkeypatch: pytest's patcher.
        """
        scratch = cfg.root / "_tests" / "docs-examples"
        scratch.mkdir(parents=True, exist_ok=True)

        def fake_uv_run(*_args: object, **_kwargs: object) -> int:
            """Write both markers the provisioned script would have written.

            Args:
                *_args: Ignored.
                **_kwargs: Ignored.

            Returns:
                Zero, as the script does whatever it found.
            """
            (scratch / "yaml" / "started.txt").write_text("ok")
            (scratch / "yaml" / "report.txt").write_text("d.md:1: yaml fence does not parse: bad\n")
            return 0

        monkeypatch.setattr(quality, "uv_run", fake_uv_run)
        fences = quality._fences("d.md", "```yaml\na: '\n```\n")
        assert quality._yaml_violations(fences, cfg, scratch) == ["d.md:1: yaml fence does not parse: bad"]

    def test_fails_when_the_yaml_checker_starts_and_does_not_finish(
        self, cfg: Config, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A crash after ``import yaml`` is this repo's bug, so it is a violation, not a skip.

        The case the two marker files exist for. ``uv`` exits non-zero both when it cannot
        resolve pyyaml and when the script it provisioned dies, so the status cannot tell a
        missing network from a broken checker -- and before ``started.txt`` this returned None
        and reported itself as "parser unavailable", which is a pass.

        Args:
            cfg: The resolved config.
            monkeypatch: pytest's patcher.
        """
        scratch = cfg.root / "_tests" / "docs-examples"
        scratch.mkdir(parents=True, exist_ok=True)

        def fake_uv_run(*_args: object, **_kwargs: object) -> int:
            """Write only the started marker, as a script dying mid-run would leave it.

            Args:
                *_args: Ignored.
                **_kwargs: Ignored.

            Returns:
                A non-zero status, as a crashing script does.
            """
            (scratch / "yaml" / "started.txt").write_text("ok")
            return 1

        monkeypatch.setattr(quality, "uv_run", fake_uv_run)
        fences = quality._fences("d.md", "```yaml\na: 1\n```\n")
        violations = quality._yaml_violations(fences, cfg, scratch)
        assert violations == ["fence_yaml.py: the yaml checker started and did not finish (exit 1)"]

    def test_discards_a_started_marker_left_by_an_earlier_run(self, cfg: Config, recorder: Recorder) -> None:
        """A stale start marker is removed, so an unprovisionable run cannot look like a crash.

        Without the unlink the previous run's marker would survive, `report.txt` would be
        absent, and a machine with no network would be reported as a broken checker -- the
        distinction the markers exist to draw, inverted.

        Args:
            cfg: The resolved config.
            recorder: The uv recorder, which records rather than running the script.
        """
        scratch = cfg.root / "_tests" / "docs-examples"
        (scratch / "yaml").mkdir(parents=True, exist_ok=True)
        (scratch / "yaml" / "started.txt").write_text("last time")
        fences = quality._fences("d.md", "```yaml\na: 1\n```\n")
        assert quality._yaml_violations(fences, cfg, scratch) is None
        assert recorder.tools() == ["python"]

    def test_discards_a_yaml_report_left_by_an_earlier_run(self, cfg: Config, recorder: Recorder) -> None:
        """A stale report is removed first, so its verdict cannot be read as this run's.

        Without the unlink a run whose parser could not be provisioned would return the
        *previous* run's violations, which is worse than either honest answer.

        Args:
            cfg: The resolved config.
            recorder: The uv recorder, which records rather than runs the script.
        """
        scratch = cfg.root / "_tests" / "docs-examples"
        (scratch / "yaml").mkdir(parents=True, exist_ok=True)
        (scratch / "yaml" / "report.txt").write_text("d.md:9: last time's verdict\n")
        fences = quality._fences("d.md", "```yaml\na: 1\n```\n")
        assert quality._yaml_violations(fences, cfg, scratch) is None
        assert recorder.tools() == ["python"]

    def test_counts_yaml_fences_as_unchecked_when_the_parser_is_unavailable(
        self, cfg: Config, recorder: Recorder, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """An unprovisionable parser leaves the yaml fences counted, not assumed sound.

        The same rule the missing-bash line follows: a fact about the machine must not fail
        the gate, and must not be reported as coverage either.

        Args:
            cfg: The resolved config.
            recorder: The uv recorder, which stands in for the unprovisionable parser.
            monkeypatch: pytest's patcher.
            capsys: pytest's output capture.
        """
        monkeypatch.setattr(quality.shutil, "which", lambda _name: None)
        self._docs(cfg, "g.md", "```yaml\na: 1\n```\n\n```python\nx = 1\n```\n")
        quality.docs_examples(cfg)
        out = capsys.readouterr().out
        assert "yaml parser unavailable: 1 yaml fence(s) went unchecked" in out
        assert "1 file(s), 2 fence(s): 1 checked" in out
        assert recorder.tools() == ["python"]

    def test_counts_the_toml_and_yaml_fences_in_the_inventory_line(
        self, cfg: Config, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """The summary names all five kinds, so a reader can see which half was measured.

        Args:
            cfg: The resolved config.
            monkeypatch: pytest's patcher.
            capsys: pytest's output capture.
        """
        monkeypatch.setattr(quality.shutil, "which", lambda _name: None)
        monkeypatch.setattr(quality, "_yaml_violations", lambda *_args: [])
        self._docs(cfg, "g.md", "```toml\nx = 1\n```\n\n```yaml\na: 1\n```\n")
        quality.docs_examples(cfg)
        out = capsys.readouterr().out
        assert "0 python, 0 shell, 1 toml, 1 yaml, 0 diffed" in out
        assert "1 file(s), 2 fence(s): 2 checked" in out

    def test_runs_the_generated_script_through_the_project_environment(self, cfg: Config, recorder: Recorder) -> None:
        """The fences run as ``uv run python <script>``, so imports see the project's deps.

        A file rather than ``-c``: the echoed invocation stays one readable line, and the
        script is what redirects its own stdout, keeping the vector shell-free.

        Args:
            cfg: The resolved config.
            recorder: The uv recorder.
        """
        scratch = cfg.root / "_tests" / "docs-examples"
        scratch.mkdir(parents=True, exist_ok=True)
        quality._run_fences(cfg, scratch, ["print('hi')"])
        call = recorder.find("python")
        assert call.kind == "uv_run"
        assert call.flags == ["_tests/docs-examples/fences.py"]
        assert "sys.stdout = open(" in (scratch / "fences.py").read_text()

    def test_treats_a_script_that_wrote_nothing_as_unrunnable(self, cfg: Config, recorder: Recorder) -> None:
        """No captured stdout means the fences did not run, which is not a passing diff.

        Args:
            cfg: The resolved config.
            recorder: The uv recorder, which records rather than runs the script.
        """
        scratch = cfg.root / "_tests" / "docs-examples"
        scratch.mkdir(parents=True, exist_ok=True)
        assert quality._run_fences(cfg, scratch, ["print('hi')"]) is None
        assert recorder.tools() == ["python"]

    def test_discards_stdout_left_by_an_earlier_run(self, cfg: Config, recorder: Recorder) -> None:
        """Stale stdout is removed first, so a diff cannot pass against the previous run.

        Args:
            cfg: The resolved config.
            recorder: The uv recorder.
        """
        scratch = cfg.root / "_tests" / "docs-examples"
        scratch.mkdir(parents=True, exist_ok=True)
        (scratch / "stdout.txt").write_text("last time's answer")
        assert quality._run_fences(cfg, scratch, ["print('hi')"]) is None
        assert recorder.tools() == ["python"]

    @pytest.mark.parametrize(
        ("printed", "expected"),
        [
            ("one", []),
            ("two", ["stale"]),
            (None, ["exited non-zero"]),
        ],
    )
    def test_diffs_a_result_block_against_what_the_python_prints(
        self, cfg: Config, monkeypatch: pytest.MonkeyPatch, printed: str | None, expected: list[str]
    ) -> None:
        """A ``result`` block matching its fence passes; a stale or unrunnable one does not.

        ``_run_fences`` is patched rather than run, so this asserts the diff and not the
        subprocess -- which the two tests above already pin as a vector.

        Args:
            cfg: The resolved config.
            monkeypatch: pytest's patcher.
            printed: What the python fences are made to print, or None for a failed run.
            expected: Substrings the violations must contain.
        """
        monkeypatch.setattr(quality, "_run_fences", lambda *_args: printed)
        fences = quality._fences("d.md", "```python\nprint('one')\n```\n\n```result\none\n```\n")
        violations = quality._result_violations([fences], cfg, cfg.root)
        assert len(violations) == len(expected)
        for violation, fragment in zip(violations, expected, strict=True):
            assert fragment in violation

    def test_reports_a_result_block_with_no_python_above_it(self, cfg: Config) -> None:
        """A ``result`` block that documents nothing runnable is a violation, not a pass.

        Args:
            cfg: The resolved config.
        """
        fences = quality._fences("d.md", "```result\nnothing produces this\n```\n")
        assert quality._result_violations([fences], cfg, cfg.root) == [
            "d.md:1: result block with no python fence above it"
        ]

    def test_a_prelude_fence_defines_names_the_later_one_uses(self, cfg: Config, recorder: Recorder) -> None:
        """Every python fence above the block is concatenated, because the pair needs it.

        ``README.md``'s first fence registers ``audit`` with ``@task`` and its second calls
        ``lookup("audit")``; running the second alone raises. Asserted on the generated
        script rather than on its output, which keeps the test hermetic.

        Args:
            cfg: The resolved config.
            recorder: The uv recorder.
        """
        scratch = cfg.root / "_tests" / "docs-examples"
        scratch.mkdir(parents=True, exist_ok=True)
        quality._run_fences(cfg, scratch, ["V = 7", "print(V)"])
        script = (scratch / "fences.py").read_text()
        assert "V = 7" in script
        assert script.index("V = 7") < script.index("print(V)")
        assert recorder.tools() == ["python"]

    def test_skips_when_the_docs_folder_holds_no_checkable_fence(
        self, cfg: Config, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A tree of ``mermaid`` and unlabelled fences measured nothing, so it skips.

        Passing would be the failure this gate exists to prevent: 100 percent docstring
        coverage over documentation carrying nothing runnable already reads as green.

        ``mermaid`` rather than the ``toml`` this used to use: toml is checked now, so a toml
        fence is no longer an example of something nothing looks at. Keeping it would have
        left a test asserting a skip that the gate is right not to take.

        Args:
            cfg: The resolved config.
            capsys: pytest's output capture.
        """
        self._docs(cfg, "conf.md", "```mermaid\ngraph TD;\n```\n\n```\nplain\n```\n")
        with pytest.raises(Skip, match="no checkable fence"):
            quality.docs_examples(cfg)
        assert "2 fence(s) not checkable: 1 (none), 1 mermaid" in capsys.readouterr().out

    def test_counts_shell_fences_as_unchecked_when_bash_is_absent(
        self, cfg: Config, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """No bash means the shell fences go uncounted as checked, and the gate says so.

        A stock Windows runner has no bash, and Windows is in the matrix deliberately. That
        is a fact about the machine, so it must not fail the gate -- the same reasoning as a
        tool guard raising Skip.

        Args:
            cfg: The resolved config.
            monkeypatch: pytest's patcher.
            capsys: pytest's output capture.
        """
        monkeypatch.setattr(quality.shutil, "which", lambda _name: None)
        self._docs(cfg, "g.md", "```bash\nls\n```\n\n```python\nx = 1\n```\n")
        quality.docs_examples(cfg)
        out = capsys.readouterr().out
        assert "bash not found: 1 shell fence(s) went unchecked" in out
        assert "1 file(s), 2 fence(s): 1 checked" in out

    def test_fails_on_a_broken_fence_and_names_it(
        self, cfg: Config, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """One unparseable fence fails the gate, with its file and line in the output.

        Args:
            cfg: The resolved config.
            monkeypatch: pytest's patcher.
            capsys: pytest's output capture.
        """
        monkeypatch.setattr(quality.shutil, "which", lambda _name: None)
        self._docs(cfg, "g.md", "```python\nx = (1,\n```\n")
        with pytest.raises(Failed, match="1 broken example"):
            quality.docs_examples(cfg)
        assert "docs/g.md:2: python fence does not parse" in capsys.readouterr().out

    def test_passes_a_clean_tree_and_checks_the_shell_fences(
        self, cfg: Config, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """With bash present every shell fence is probed, and a clean tree passes.

        Args:
            cfg: The resolved config.
            monkeypatch: pytest's patcher.
            capsys: pytest's output capture.
        """
        seen = self._bash(monkeypatch, 0)
        self._docs(cfg, "a.md", "```bash\nls\n```\n")
        self._docs(cfg, "b.md", "```python\nx = 1\n```\n")
        quality.docs_examples(cfg)
        assert len(seen) == 1
        assert "2 file(s), 2 fence(s): 2 checked" in capsys.readouterr().out

    def test_is_guarded_on_the_docs_folder(self, cfg: Config) -> None:
        """A repository with no docs tree skips through the guard, rather than failing.

        Args:
            cfg: The resolved config.
        """
        spec = lookup("docs-examples")
        assert [guard.folder for guard in spec.guards] == ["docs_folder"]
        with pytest.raises(Skip, match="docs_folder"):
            spec.guards[0].check(cfg.root, cfg.folders)

    def test_needs_install_because_the_examples_import_the_project(self) -> None:
        """The executed half imports the project's own packages, as ``rhiza-test`` does."""
        assert lookup("docs-examples").needs == ("install",)

    def test_returns_the_captured_stdout_when_the_script_ran(
        self, cfg: Config, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A script that wrote its stdout file has that text returned for diffing.

        ``uv_run`` is patched with a stand-in that produces the *effect* the real script has
        -- the redirected stdout file -- rather than running python, which keeps the test
        hermetic while still covering the path the diff depends on.

        Args:
            cfg: The resolved config.
            monkeypatch: pytest's patcher.
        """
        scratch = cfg.root / "_tests" / "docs-examples"
        scratch.mkdir(parents=True, exist_ok=True)

        def fake_uv_run(*_args: object, **_kwargs: object) -> int:
            """Write the stdout the redirected script would have written.

            Args:
                *_args: Ignored.
                **_kwargs: Ignored.

            Returns:
                Zero, as a successful run does.
            """
            (scratch / "stdout.txt").write_text("captured\n")
            return 0

        monkeypatch.setattr(quality, "uv_run", fake_uv_run)
        assert quality._run_fences(cfg, scratch, ["print('captured')"]) == "captured\n"
