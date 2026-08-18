"""``doctor`` and ``clean``, plus the book and marimo bodies the recorder cannot reach."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from rhiza_task.config import Config
from rhiza_task.spec import Failed, Skip
from rhiza_task.tasks import book as book_tasks
from rhiza_task.tasks import doctor as doctor_module
from rhiza_task.tasks import quality

from .conftest import Recorder


class TestDoctor:
    """doctor.mk's 69 lines of shell, as a task."""

    @pytest.fixture
    def tools(self, monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
        """Pretend every tool is present, and let a test edit the versions.

        Args:
            monkeypatch: pytest's patcher.

        Returns:
            A mutable map of tool name to ``--version`` output.
        """
        versions = {
            "uv": "uv 0.9.2 (abc 2026-01-01)",
            "git": "git version 2.39.5",
            "make": "GNU Make 4.4.1",
        }
        monkeypatch.setattr(doctor_module.shutil, "which", lambda name: f"/fake/{name}" if name in versions else None)

        def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            """Return the canned ``--version`` output for the tool being probed.

            Args:
                argv: The argument vector, executable first.
                **kwargs: Ignored; subprocess accepts many and this fake needs none.

            Returns:
                A completed process carrying the faked output.
            """
            name = Path(argv[0]).name
            return subprocess.CompletedProcess(argv, 0, stdout=versions.get(name, ""), stderr="")

        monkeypatch.setattr(doctor_module.subprocess, "run", fake_run)
        return versions

    def test_passes_when_everything_is_current(
        self, cfg: Config, tools: dict[str, str], capsys: pytest.CaptureFixture[str]
    ) -> None:
        """All three tools present and recent enough.

        Args:
            cfg: The resolved config.
            tools: The faked tool versions.
            capsys: pytest's output capture.
        """
        doctor_module.doctor(cfg)
        out = capsys.readouterr().out
        assert out.count("[ OK ]") == len(doctor_module.TOOLS)

    def test_fails_on_an_outdated_required_tool(self, cfg: Config, tools: dict[str, str]) -> None:
        """An old uv is a hard failure.

        Args:
            cfg: The resolved config.
            tools: The faked tool versions.
        """
        tools["uv"] = "uv 0.3.9"
        with pytest.raises(Failed, match="uv"):
            doctor_module.doctor(cfg)

    def test_missing_make_only_warns(
        self, cfg: Config, tools: dict[str, str], capsys: pytest.CaptureFixture[str]
    ) -> None:
        """GNU make is optional now, because no task needs it.

        doctor.mk requires GNU make, which was honest while the task layer *was* make.
        Requiring a tool the layer no longer uses is how a diagnostic starts lying about
        its own prerequisites -- only the convenience shim needs it.

        Args:
            cfg: The resolved config.
            tools: The faked tool versions.
            capsys: pytest's output capture.
        """
        del tools["make"]
        doctor_module.doctor(cfg)
        out = capsys.readouterr().out
        assert "[WARN]" in out
        assert "(optional)" in out

    def test_fails_when_a_required_tool_is_absent(self, cfg: Config, tools: dict[str, str]) -> None:
        """A missing git is reported with its install URL and fails.

        Args:
            cfg: The resolved config.
            tools: The faked tool versions.
        """
        del tools["git"]
        with pytest.raises(Failed, match="git"):
            doctor_module.doctor(cfg)

    def test_unparseable_version_fails_rather_than_passing(self, cfg: Config, tools: dict[str, str]) -> None:
        """A tool that prints no version is not assumed to be fine.

        Args:
            cfg: The resolved config.
            tools: The faked tool versions.
        """
        tools["uv"] = "some other output entirely"
        with pytest.raises(Failed, match="uv"):
            doctor_module.doctor(cfg)


class TestClean:
    """bootstrap.mk's ``clean``."""

    @pytest.fixture
    def git_spy(self, monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
        """Record git invocations and fake a branch listing with one gone branch.

        Args:
            monkeypatch: pytest's patcher.

        Returns:
            The recorded argument vectors.
        """
        seen: list[list[str]] = []
        listing = (
            "* main          abc1234 [origin/main] head\n"
            "  merged-branch def5678 [origin/merged-branch: gone] old\n"
            "+ worktree-br   999aaaa [origin/worktree-br: gone] other\n"
        )

        def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            """Record the git invocation and answer a branch listing when asked for one.

            Args:
                argv: The argument vector, executable first.
                **kwargs: Ignored.

            Returns:
                A completed process, carrying the listing only for ``branch -vv``.
            """
            seen.append(argv[1:])
            out = listing if argv[1:3] == ["branch", "-vv"] else ""
            return subprocess.CompletedProcess(argv, 0, stdout=out, stderr="")

        monkeypatch.setattr(quality.subprocess, "run", fake_run)
        return seen

    def test_removes_artifacts_but_keeps_env_files(self, cfg: Config, git_spy: list[list[str]]) -> None:
        """Build output goes; ``.env`` stays.

        A ``.env`` holds local configuration that is expensive to reconstruct and is not an
        artifact, which is why the git-clean call excludes it.

        Args:
            cfg: The resolved config.
            git_spy: The git recorder.
        """
        (cfg.root / "dist").mkdir()
        (cfg.root / "demo.egg-info").mkdir()
        (cfg.root / ".coverage").write_text("data")
        quality.clean(cfg)
        assert not (cfg.root / "dist").exists()
        assert not (cfg.root / "demo.egg-info").exists()
        assert not (cfg.root / ".coverage").exists()
        clean_call = next(c for c in git_spy if c[0] == "clean")
        assert "!.env" in clean_call

    def test_deletes_only_deletable_gone_branches(self, cfg: Config, git_spy: list[list[str]]) -> None:
        """A branch whose remote is gone is deleted -- unless it is checked out.

        The leading ``*`` and ``+`` markers mean current and worktree-checked-out; git
        refuses to delete either, which is how the make recipe's ``xargs`` used to fail.

        Args:
            cfg: The resolved config.
            git_spy: The git recorder.
        """
        quality.clean(cfg)
        deleted = [c[2] for c in git_spy if c[:2] == ["branch", "-D"]]
        assert deleted == ["merged-branch"]


class TestSemgrep:
    """quality.mk's ``semgrep``."""

    def test_skips_without_a_rule_file(self, cfg: Config, recorder: Recorder) -> None:
        """No rules, nothing to run.

        Args:
            cfg: The resolved config.
            recorder: The uv recorder.
        """
        with pytest.raises(Skip, match=r"semgrep\.yml"):
            quality.semgrep(cfg)

    def test_runs_against_the_source_folder(self, cfg: Config, recorder: Recorder) -> None:
        """The rule file is passed by absolute path, the target by the configured name.

        Args:
            cfg: The resolved config.
            recorder: The uv recorder.
        """
        (cfg.root / ".rhiza").mkdir()
        (cfg.root / ".rhiza" / "semgrep.yml").write_text("rules: []\n")
        quality.semgrep(cfg)
        assert recorder.find("semgrep").flags[-1] == "src"


class TestMarimo:
    """marimo.mk's two targets."""

    def test_validate_skips_an_empty_folder(self, cfg: Config, recorder: Recorder) -> None:
        """A notebook folder with no notebooks is a skip.

        Args:
            cfg: The resolved config.
            recorder: The uv recorder.
        """
        cfg.path("marimo_folder").mkdir(parents=True)
        with pytest.raises(Skip, match="no notebooks"):
            book_tasks.marimo_validate(cfg)

    def test_validate_reports_every_failure_not_just_the_first(self, cfg: Config, recorder: Recorder) -> None:
        """Two broken notebooks are both named, and each gets its artefact folder.

        Args:
            cfg: The resolved config.
            recorder: The uv recorder.
        """
        folder = cfg.path("marimo_folder")
        folder.mkdir(parents=True)
        (folder / "one.py").write_text("")
        (folder / "two.py").write_text("")
        recorder.codes = [1, 1]
        with pytest.raises(Failed, match="2 notebook"):
            book_tasks.marimo_validate(cfg)
        assert (cfg.root / "results" / "one").is_dir()
        assert (cfg.root / "results" / "two").is_dir()

    def test_validate_passes_when_every_notebook_runs(self, cfg: Config, recorder: Recorder) -> None:
        """A clean run reports the count.

        Args:
            cfg: The resolved config.
            recorder: The uv recorder.
        """
        folder = cfg.path("marimo_folder")
        folder.mkdir(parents=True)
        (folder / "one.py").write_text("")
        book_tasks.marimo_validate(cfg)

    def test_editor_runs_in_the_notebook_folder(self, cfg: Config, recorder: Recorder) -> None:
        """The editor is started with the notebook folder as its working directory.

        Args:
            cfg: The resolved config.
            recorder: The uv recorder.
        """
        cfg.path("marimo_folder").mkdir(parents=True)
        book_tasks.marimo(cfg)
        call = recorder.find("marimo")
        assert call.kwargs["cwd"] == cfg.path("marimo_folder")
        assert call.kwargs["no_project"] is True


class TestBookHelpers:
    """book.mk's two internal steps."""

    def test_reports_are_copied_into_the_docs_tree(self, cfg: Config, recorder: Recorder) -> None:
        """``_tests`` output becomes ``docs/reports``.

        Args:
            cfg: The resolved config.
            recorder: The uv recorder.
        """
        (cfg.root / "_tests" / "html-report").mkdir(parents=True)
        (cfg.root / "_tests" / "html-report" / "report.html").write_text("<html/>")
        book_tasks._copy_reports(cfg)
        assert (cfg.root / "docs" / "reports" / "html-report" / "report.html").is_file()

    def test_empty_reports_are_tolerated(
        self, cfg: Config, recorder: Recorder, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """No reports is a warning, not a failure: the book is still worth building.

        Args:
            cfg: The resolved config.
            recorder: The uv recorder.
            capsys: pytest's output capture.
        """
        (cfg.root / "_tests").mkdir()
        book_tasks._copy_reports(cfg)
        assert "no _tests output" in capsys.readouterr().out

    def test_each_notebook_is_exported_to_html(self, cfg: Config, recorder: Recorder) -> None:
        """Export runs once per notebook, writing into ``docs/notebooks``.

        Args:
            cfg: The resolved config.
            recorder: The uv recorder.
        """
        folder = cfg.path("marimo_folder")
        folder.mkdir(parents=True)
        (folder / "alpha.py").write_text("")
        (folder / "beta.py").write_text("")
        book_tasks._export_notebooks(cfg)
        exports = [c for c in recorder.calls if c.tool == "marimo"]
        assert len(exports) == 2
        assert Path(exports[0].flags[-1]) == cfg.root / "docs" / "notebooks" / "alpha.html"

    def test_serve_serves_the_built_output(self, cfg: Config, recorder: Recorder) -> None:
        """The HTTP server runs inside the book output folder.

        Python's own server rather than an editor's, because the JetBrains one refuses to
        serve gitignored directories and ``_book`` is one.

        Args:
            cfg: The resolved config.
            recorder: The uv recorder.
        """
        book_tasks.serve(cfg)
        call = recorder.find("python")
        assert call.flags == ["-m", "http.server", "8000"]
        assert call.kwargs["cwd"] == cfg.path("book_output")


class TestExtrasGuards:
    """The guards on the optional gates, which is most of what they are."""

    def test_benchmark_runs_the_benchmarks_folder(self, cfg: Config, recorder: Recorder) -> None:
        """The pins are exact, because benchmark results are only comparable within a version.

        Args:
            cfg: The resolved config.
            recorder: The uv recorder.
        """
        from rhiza_task.tasks import extras

        (cfg.path("tests_folder") / "benchmarks").mkdir()
        extras.benchmark(cfg)
        call = recorder.find("pytest")
        assert "--benchmark-only" in call.flags
        assert "pytest-benchmark==5.2.3" in call.kwargs["withs"]

    def test_stress_runs_only_marked_tests(self, cfg: Config, recorder: Recorder) -> None:
        """The stress gate selects by marker, not by folder alone.

        Args:
            cfg: The resolved config.
            recorder: The uv recorder.
        """
        from rhiza_task.tasks import extras

        (cfg.path("tests_folder") / "stress").mkdir()
        extras.stress(cfg)
        assert recorder.find("pytest").flags[1:3] == ["-m", "stress"]


class TestHookInstall:
    """python.mk's conditional pre-commit hook install."""

    def test_skipped_when_an_external_manager_owns_hookspath(
        self, cfg: Config, recorder: Recorder, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """No install is attempted when ``core.hooksPath`` is set, because prek refuses.

        Args:
            cfg: The resolved config.
            recorder: The uv recorder.
            monkeypatch: pytest's patcher.
            capsys: pytest's output capture.
        """
        from rhiza_task.tasks import python as python_tasks

        monkeypatch.setattr(
            python_tasks.subprocess,
            "run",
            lambda *a, **k: subprocess.CompletedProcess([], 0, stdout=".husky\n", stderr=""),
        )
        python_tasks._install_hooks(cfg)
        assert "core.hooksPath" in capsys.readouterr().out
        assert "prek" not in recorder.tools()

    def test_installs_with_an_explicit_config(
        self, cfg: Config, recorder: Recorder, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``-c`` is repeated here as well as in ``fmt``, and for the same reason.

        prek bakes the flag into the generated shim, so omitting it here would let the
        commit-time gate rediscover nested projects and stop meaning what ``fmt`` means.

        Args:
            cfg: The resolved config.
            recorder: The uv recorder.
            monkeypatch: pytest's patcher.
        """
        from rhiza_task.tasks import python as python_tasks

        monkeypatch.setattr(
            python_tasks.subprocess,
            "run",
            lambda *a, **k: subprocess.CompletedProcess([], 0, stdout="", stderr=""),
        )
        python_tasks._install_hooks(cfg)
        assert recorder.find("prek").flags == ["install", "-c", ".pre-commit-config.yaml"]
        assert recorder.find("prek").kwargs["check"] is False
