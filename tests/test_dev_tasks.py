"""``doctor`` and ``clean``, plus the book and marimo bodies the recorder cannot reach."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from rhiza_task.config import Config
from rhiza_task.runner import Status, run
from rhiza_task.spec import REGISTRY, Failed, Skip
from rhiza_task.tasks import book as book_tasks
from rhiza_task.tasks import doctor as doctor_module
from rhiza_task.tasks import quality
from rhiza_task.tasks import setup as setup_tasks

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
        """Every tool present and recent enough.

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

    def test_probes_nothing_a_guard_already_owns(self) -> None:
        """The diagnostic and the guards partition the question; they must not overlap.

        ``doctor`` answers "can this machine run the CLI at all", so it names uv and git and
        fails on a miss. Every other binary the package reaches for -- docker, gh, git-lfs,
        tectonic, marp -- is a precondition on the one task that wraps it, and reports itself
        on that task's ``skipped`` line with an install URL in its ``reason``. Listing one of
        them here too would answer the same question one indirection further from where it
        matters, and would need updating whenever a bundle gained a tool.

        The assertion is disjointness rather than a transcribed list, so a new guard tool
        needs no edit here and a *duplicated* one fails.
        """
        named = {tool.name for tool in doctor_module.TOOLS}
        guarded = {guard.tool for spec in REGISTRY.values() for guard in spec.guards if guard.tool}
        assert guarded, "no task guards on a tool; this test would pass vacuously"
        assert named.isdisjoint(guarded)

    def test_probing_make_would_be_a_regression(self) -> None:
        """doctor.mk required GNU make, and this package has no make layer to require it for.

        Kept as its own case because it is the correction, not a corollary: make was carried
        over as an *optional* entry for the sake of a repo-owned Makefile forwarding to the
        CLI, warned about on every clean run, and no task in the registry needs it. A guard
        would not catch its return, since nothing guards on make either.
        """
        assert "make" not in {tool.name for tool in doctor_module.TOOLS}

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
    """The conditional pre-commit hook install every ``install`` recipe carries."""

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
        monkeypatch.setattr(
            quality.subprocess,
            "run",
            lambda *a, **k: subprocess.CompletedProcess([], 0, stdout=".husky\n", stderr=""),
        )
        (cfg.root / ".pre-commit-config.yaml").touch()
        quality.install_hooks(cfg)
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
        monkeypatch.setattr(
            quality.subprocess,
            "run",
            lambda *a, **k: subprocess.CompletedProcess([], 0, stdout="", stderr=""),
        )
        (cfg.root / ".pre-commit-config.yaml").touch()
        quality.install_hooks(cfg)
        assert recorder.find("prek").flags == ["install", "-c", ".pre-commit-config.yaml"]
        assert recorder.find("prek").kwargs["check"] is False


class TestSetupHook:
    """The repo-owned environment hook, and the reason it hangs off ``install``."""

    def _write(self, cfg: Config, *, executable: bool) -> Path:
        """Write a hook into the throwaway repository.

        Args:
            cfg: The resolved config.
            executable: Whether to set the execute bit.

        Returns:
            The hook's path.
        """
        hook = cfg.root / setup_tasks.HOOK
        hook.write_text("#!/usr/bin/env bash\napt-get install -y graphviz\n")
        hook.chmod(0o755 if executable else 0o644)
        return hook

    def test_no_hook_succeeds_rather_than_skipping(
        self, cfg: Config, recorder: Recorder, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """An absent hook is the absence of a request, so it is not a skip.

        Deliberately not ``Skip``: ``--strict`` promotes a skip to a failure so CI can assert
        a gate measured something, and most repositories need no native provisioning -- so
        skipping here would fail every ``--strict`` run on the common case. See
        ``test_strict_does_not_fail_a_repository_that_needs_no_provisioning``.

        Args:
            cfg: The resolved config.
            recorder: The uv recorder.
            capsys: pytest's output capture.
        """
        setup_tasks.setup(cfg)
        assert recorder.calls == []
        assert "nothing to provision" in capsys.readouterr().out

    def test_strict_does_not_fail_a_repository_that_needs_no_provisioning(
        self, cfg: Config, recorder: Recorder
    ) -> None:
        """The regression this asymmetry exists to prevent, asserted through the runner.

        ``--strict`` is what CI uses to demand that a gate did its work. If an absent hook
        skipped, every ``--strict`` invocation would fail on a repository that simply has no
        native dependencies -- and ``install`` would be reported ``blocked`` behind it.

        Args:
            cfg: The resolved config.
            recorder: The uv recorder.
        """
        state = run(["install"], Config.load(root=cfg.root, strict=True))
        assert [(r.name, r.status) for r in state.results] == [("setup", Status.OK), ("install", Status.OK)]
        assert not state.failed

    def test_a_hook_that_cannot_run_fails_rather_than_skipping(self, cfg: Config, recorder: Recorder) -> None:
        """The asymmetry that is the whole point: a forgotten ``chmod`` must not pass quietly.

        Skipping here would recreate the silent-green failure the hook exists to remove --
        the author wrote provisioning, believed it ran, and nothing said otherwise.

        Args:
            cfg: The resolved config.
            recorder: The uv recorder.
        """
        self._write(cfg, executable=False)
        with pytest.raises(Failed, match="chmod"):
            setup_tasks.setup(cfg)
        assert recorder.calls == []

    def test_runs_the_hook_from_the_repository_root(self, cfg: Config, recorder: Recorder) -> None:
        """An absolute path, so it runs whatever the caller's working directory was.

        Args:
            cfg: The resolved config.
            recorder: The uv recorder.
        """
        hook = self._write(cfg, executable=True)
        setup_tasks.setup(cfg)
        call = recorder.find(str(hook))
        assert call.kind == "tool"
        assert call.kwargs["cwd"] == cfg.root

    @pytest.mark.parametrize("layer", ["python", "rust", "go"])
    def test_every_layers_install_names_it(self, layer: str) -> None:
        """The wiring, per layer -- one insertion point is only enough if all three have it.

        Asserted on the registry rather than on a run, because the failure this guards
        against is a layer added later without the prerequisite: an unresolvable one is
        skipped rather than an error, so it would go unnoticed at runtime.

        Args:
            layer: The language layer.
        """
        assert "setup" in REGISTRY[f"{layer}:install"].needs

    def test_the_hook_precedes_the_dependency_sync(self, cfg: Config, recorder: Recorder) -> None:
        """A native library needed to *build* a wheel has to be there before ``uv sync``.

        Args:
            cfg: The resolved config.
            recorder: The uv recorder.
        """
        self._write(cfg, executable=True)
        run(["install"], cfg)
        tools = recorder.tools()
        assert tools.index(str(cfg.root / setup_tasks.HOOK)) < tools.index("sync")
