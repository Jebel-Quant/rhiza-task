"""The five bundle-owned fragments, as tasks: github, docker, lfs, paper, presentation.

These were the fragments `.rhiza/make.d/` could not retire, because no task answered for
their targets. What is asserted here is what a make dry run could not: the argument vector
each one builds, and the outcome when the tool it wraps is not installed.

Every test patches ``shutil.which`` rather than the individual ``have`` bindings.
:meth:`~rhiza_task.spec.Guard.check`, :func:`~rhiza_task.spec.have` and the task bodies all
reach the same function, so one patch covers the guard and the body and they cannot
disagree about whether a tool is present.
"""

from __future__ import annotations

import re
import shutil
from collections.abc import Callable

import pytest

from rhiza_task import runner
from rhiza_task.config import Config
from rhiza_task.runner import Status
from rhiza_task.spec import REGISTRY, Failed, Guard, Skip
from rhiza_task.tasks import docker as docker_tasks
from rhiza_task.tasks import github as github_tasks
from rhiza_task.tasks import lfs as lfs_tasks
from rhiza_task.tasks import paper as paper_tasks
from rhiza_task.tasks import presentation as presentation_tasks

from .conftest import Recorder

FRAGMENT_TARGETS = {
    "github.mk": ("view-prs", "view-issues", "failed-workflows", "workflow-status", "latest-release", "whoami"),
    "docker.mk": ("docker-build", "docker-run", "docker-clean"),
    "lfs.mk": ("lfs-install", "lfs-pull", "lfs-track", "lfs-status"),
    "paper.mk": ("paper", "paper-clean"),
    "presentation.mk": ("presentation", "presentation-pdf", "presentation-serve"),
}
"""Every target the five surviving fragments defined, minus the internal guards.

``require-gh``, ``gh-install`` and ``require-marp`` are absent on purpose: two were the
same "is it installed?" question spelled twice, and the third installed a global npm
package as a side effect. See the modules' docstrings.
"""


@pytest.fixture
def present(monkeypatch: pytest.MonkeyPatch) -> set[str]:
    """Control which executables the tasks believe are installed.

    Args:
        monkeypatch: pytest's patcher.

    Returns:
        A mutable set of tool names; add to it to make a tool present.
    """
    installed: set[str] = set()

    def fake_which(name: str, *args: object, **kwargs: object) -> str | None:
        """Report a tool as found only when the test asked for it.

        Args:
            name: Executable name.
            *args: Ignored.
            **kwargs: Ignored.

        Returns:
            A fake absolute path, or None.
        """
        return f"/fake/{name}" if name in installed else None

    monkeypatch.setattr(shutil, "which", fake_which)
    return installed


def test_every_retired_target_has_a_task() -> None:
    """No fragment target loses its name in the move to the CLI.

    The point of the migration is that ``make view-prs`` keeps working through the shim's
    catch-all, which it only does if the task is spelled identically.
    """
    for fragment, targets in FRAGMENT_TARGETS.items():
        for target in targets:
            assert target in REGISTRY, f"{fragment}'s {target} has no task"
            assert REGISTRY[target].layer is None, f"{target} should be language-neutral"


class TestToolGuard:
    """``Guard(tool=...)``, which is what ``require-gh`` and ``require-marp`` were."""

    def test_missing_tool_skips_with_the_reason(self, present: set[str], tmp_path) -> None:
        """An absent tool is a skip carrying the install hint.

        Args:
            present: The installed-tool set, left empty.
            tmp_path: pytest's temporary directory.
        """
        with pytest.raises(Skip, match="install from"):
            Guard(tool="gh", reason="gh not found; install from https://example.invalid").check(tmp_path, {})

    def test_present_tool_passes(self, present: set[str], tmp_path) -> None:
        """A present tool satisfies the guard.

        Args:
            present: The installed-tool set.
            tmp_path: pytest's temporary directory.
        """
        present.add("gh")
        Guard(tool="gh").check(tmp_path, {})

    def test_strict_turns_a_missing_tool_into_a_failure(self, repo, present: set[str]) -> None:
        """``--strict`` is how a caller opts into the fragment's hard-fail behaviour.

        Args:
            repo: The throwaway repository.
            present: The installed-tool set, left empty.
        """
        state = runner.run(["view-prs"], Config.load(root=repo, strict=True))
        assert state.results[0].status is Status.FAILED


class TestGitHub:
    """github.mk's six helpers."""

    def test_view_prs_asks_gh_for_the_fields_the_template_renders(
        self, cfg: Config, recorder: Recorder, present: set[str]
    ) -> None:
        """Every column in the template is requested in ``--json``.

        A mismatch is silent in gh: an unrequested field renders as empty rather than as
        an error, so the table loses a column and nothing says why.

        Args:
            cfg: The resolved config.
            recorder: The uv recorder.
            present: The installed-tool set.
        """
        present.add("gh")
        github_tasks.view_prs(cfg)
        call = recorder.find("gh")
        fields = call.flags[call.flags.index("--json") + 1].split(",")
        assert fields == ["number", "title", "author", "headRefName", "updatedAt"]
        for field in ("number", "title", "author.login", "headRefName", "updatedAt"):
            assert field in github_tasks.PR_TEMPLATE

    def test_issue_template_renders_the_label_names(self, cfg: Config, recorder: Recorder, present: set[str]) -> None:
        """The labels column plucks names out of the label objects, as github.mk does.

        Args:
            cfg: The resolved config.
            recorder: The uv recorder.
            present: The installed-tool set.
        """
        present.add("gh")
        github_tasks.view_issues(cfg)
        assert recorder.find("gh").flags[:2] == ["issue", "list"]
        assert 'pluck "name" .labels' in github_tasks.ISSUE_TEMPLATE

    def test_failed_workflows_asks_only_for_failures(self, cfg: Config, recorder: Recorder, present: set[str]) -> None:
        """The status filter and the limit are the recipe's.

        Args:
            cfg: The resolved config.
            recorder: The uv recorder.
            present: The installed-tool set.
        """
        present.add("gh")
        github_tasks.failed_workflows(cfg)
        flags = recorder.find("gh").flags
        assert flags[:2] == ["run", "list"]
        assert "--status" in flags
        assert flags[flags.index("--status") + 1] == "failure"
        assert flags[flags.index("--limit") + 1] == "10"

    def test_workflow_status_uses_the_first_matching_workflow(
        self, cfg: Config, recorder: Recorder, present: set[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``head -1`` over the jq output, without a shell.

        Args:
            cfg: The resolved config.
            recorder: The uv recorder.
            present: The installed-tool set.
            monkeypatch: pytest's patcher.
        """
        present.add("gh")
        monkeypatch.setattr(github_tasks, "capture", _canned("Release\nRelease notes\n"))
        github_tasks.workflow_status(cfg)
        flags = recorder.find("gh").flags
        assert flags[flags.index("--workflow") + 1] == "Release"

    def test_workflow_status_skips_a_repo_with_no_release_workflow(
        self, cfg: Config, recorder: Recorder, present: set[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The WARN branch of the recipe, as a skip.

        Args:
            cfg: The resolved config.
            recorder: The uv recorder.
            present: The installed-tool set.
            monkeypatch: pytest's patcher.
        """
        present.add("gh")
        monkeypatch.setattr(github_tasks, "capture", _canned(""))
        with pytest.raises(Skip, match="no release workflow"):
            github_tasks.workflow_status(cfg)
        assert recorder.calls == []

    def test_latest_release_skips_when_there_are_none(
        self, cfg: Config, recorder: Recorder, present: set[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A repository with no release reports that rather than showing gh's error.

        Args:
            cfg: The resolved config.
            recorder: The uv recorder.
            present: The installed-tool set.
            monkeypatch: pytest's patcher.
        """
        present.add("gh")
        monkeypatch.setattr(github_tasks, "capture", _canned(""))
        with pytest.raises(Skip, match="no releases"):
            github_tasks.latest_release(cfg)
        assert recorder.calls == []

    def test_latest_release_renders_when_one_exists(
        self, cfg: Config, recorder: Recorder, present: set[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The probe's success leads to the full templated view.

        Args:
            cfg: The resolved config.
            recorder: The uv recorder.
            present: The installed-tool set.
            monkeypatch: pytest's patcher.
        """
        present.add("gh")
        monkeypatch.setattr(github_tasks, "capture", _canned("v1.2.3"))
        github_tasks.latest_release(cfg)
        assert recorder.find("gh").flags[:2] == ["release", "view"]

    def test_whoami_template_survived_makes_dollar_escaping(
        self, cfg: Config, recorder: Recorder, present: set[str]
    ) -> None:
        """``$$host`` in the recipe is ``$host`` in the template gh actually parses.

        make doubles a dollar to pass one through, so a literal transcription of the
        recipe would hand gh a template referring to the undefined variable ``$$host``.

        Args:
            cfg: The resolved config.
            recorder: The uv recorder.
            present: The installed-tool set.
        """
        present.add("gh")
        github_tasks.whoami(cfg)
        assert recorder.find("gh").flags[:2] == ["auth", "status"]
        assert "{{range $host, $accounts := .hosts}}" in github_tasks.WHOAMI_TEMPLATE
        assert "$$" not in github_tasks.WHOAMI_TEMPLATE

    def test_every_helper_skips_without_gh(self, repo, present: set[str]) -> None:
        """No task in the module runs anything on a machine without gh.

        Args:
            repo: The throwaway repository.
            present: The installed-tool set, left empty.
        """
        state = runner.run(list(FRAGMENT_TARGETS["github.mk"]), Config.load(root=repo))
        assert {r.status for r in state.results} == {Status.SKIPPED}
        assert all("gh not found" in r.detail for r in state.results)


class TestDocker:
    """docker.mk's three targets."""

    @pytest.fixture
    def dockerfile(self, repo) -> None:
        """Give the throwaway repository a Dockerfile.

        Args:
            repo: The repository root.
        """
        (repo / "docker").mkdir()
        (repo / "docker" / "Dockerfile").write_text("FROM scratch\n")

    def test_build_skips_without_a_dockerfile(self, cfg: Config, recorder: Recorder, present: set[str]) -> None:
        """A repo that adopted the bundle but wrote no Dockerfile is skipped, not failed.

        Args:
            cfg: The resolved config.
            recorder: The uv recorder.
            present: The installed-tool set.
        """
        present.add("docker")
        with pytest.raises(Skip, match=re.escape("docker/Dockerfile")):
            docker_tasks.docker_build(cfg)
        assert recorder.calls == []

    def test_build_tags_after_the_directory_and_passes_the_python_version(
        self, cfg: Config, recorder: Recorder, present: set[str], dockerfile: None
    ) -> None:
        """``$(shell basename $(CURDIR))`` as a default, and the PYTHON_VERSION build arg.

        Args:
            cfg: The resolved config.
            recorder: The uv recorder.
            present: The installed-tool set.
            dockerfile: Writes the Dockerfile.
        """
        present.add("docker")
        docker_tasks.docker_build(cfg)
        flags = recorder.find("docker").flags
        assert flags[:2] == ["buildx", "build"]
        assert flags[flags.index("--tag") + 1] == f"{cfg.root.name}:latest"
        assert flags[flags.index("--build-arg") + 1] == f"PYTHON_VERSION={cfg.python_version}"
        assert flags[flags.index("--file") + 1] == "docker/Dockerfile"
        assert flags[-1] == "."

    def test_image_name_is_overridable(self, repo, recorder: Recorder, present: set[str], dockerfile: None) -> None:
        """``docker_image`` in the manifest wins over the directory name.

        Args:
            repo: The throwaway repository.
            recorder: The uv recorder.
            present: The installed-tool set.
            dockerfile: Writes the Dockerfile.
        """
        present.add("docker")
        (repo / "pyproject.toml").write_text(
            '[project]\nname = "demo"\nversion = "0.1.0"\n\n[tool.rhiza-task]\ndocker-image = "demo-app"\n'
        )
        docker_tasks.docker_build(Config.load(root=repo))
        assert "demo-app:latest" in recorder.find("docker").flags

    def test_run_builds_first(self, repo, recorder: Recorder, present: set[str], dockerfile: None) -> None:
        """``docker-run: docker-build`` survives as a prerequisite.

        Args:
            repo: The throwaway repository.
            recorder: The uv recorder.
            present: The installed-tool set.
            dockerfile: Writes the Dockerfile.
        """
        present.add("docker")
        state = runner.run(["docker-run"], Config.load(root=repo))
        assert [r.name for r in state.results] == ["docker-build", "docker-run"]
        assert [c.args[1] for c in recorder.calls] == ["buildx", "run"]

    def test_clean_tolerates_a_missing_image(self, cfg: Config, recorder: Recorder, present: set[str]) -> None:
        """``|| true``: removing what was never built is not a failure.

        Args:
            cfg: The resolved config.
            recorder: The uv recorder.
            present: The installed-tool set.
        """
        present.add("docker")
        docker_tasks.docker_clean(cfg)
        assert recorder.find("docker").kwargs["check"] is False


class TestGitLfs:
    """lfs.mk's four targets, one of which changed behaviour on purpose."""

    def test_install_fails_with_a_platform_hint_when_the_binary_is_absent(
        self, cfg: Config, recorder: Recorder, present: set[str], capsys: pytest.CaptureFixture[str]
    ) -> None:
        """The 50 lines of download-and-extract shell became an actionable message.

        Args:
            cfg: The resolved config.
            recorder: The uv recorder.
            present: The installed-tool set, left empty.
            capsys: pytest's output capture.
        """
        with pytest.raises(Failed, match="git-lfs is not installed"):
            lfs_tasks.lfs_install(cfg)
        assert lfs_tasks.install_hint() in capsys.readouterr().out
        assert recorder.calls == []

    def test_install_configures_the_repository(self, cfg: Config, recorder: Recorder, present: set[str]) -> None:
        """What the macOS branch's last line did, and the only part of it that stuck.

        Args:
            cfg: The resolved config.
            recorder: The uv recorder.
            present: The installed-tool set.
        """
        present.add("git-lfs")
        lfs_tasks.lfs_install(cfg)
        assert recorder.find("git").flags == ["lfs", "install"]

    @pytest.mark.parametrize(
        ("name", "subcommand"),
        [("lfs-pull", "pull"), ("lfs-track", "track"), ("lfs-status", "status")],
    )
    def test_thin_wrappers(self, repo, recorder: Recorder, present: set[str], name: str, subcommand: str) -> None:
        """Each remaining target is one git subcommand.

        Args:
            repo: The throwaway repository.
            recorder: The uv recorder.
            present: The installed-tool set.
            name: The task name.
            subcommand: The git-lfs subcommand it must invoke.
        """
        present.add("git-lfs")
        state = runner.run([name], Config.load(root=repo))
        assert state.results[0].status is Status.OK
        assert recorder.find("git").flags == ["lfs", subcommand]

    def test_wrappers_skip_without_the_binary(self, repo, present: set[str]) -> None:
        """``git lfs pull`` without git-lfs is an unknown-command error; this is clearer.

        Args:
            repo: The throwaway repository.
            present: The installed-tool set, left empty.
        """
        state = runner.run(["lfs-pull"], Config.load(root=repo))
        assert state.results[0].status is Status.SKIPPED
        assert "git-lfs not found" in state.results[0].detail


class TestPaper:
    """paper.mk, minus the one downstream repository it named."""

    @pytest.fixture
    def papers(self, repo) -> Callable[..., None]:
        """Return a helper that writes ``.tex`` files into the paper folder.

        Args:
            repo: The repository root.

        Returns:
            A callable taking filenames.
        """

        def write(*names: str) -> None:
            """Create the named files under ``docs/paper``.

            Args:
                *names: File names to create.
            """
            folder = repo / "docs" / "paper"
            folder.mkdir(parents=True, exist_ok=True)
            for name in names:
                (folder / name).write_text("\\documentclass{article}\\begin{document}\\end{document}\n")

        return write

    def test_prefers_main_then_paper_then_alphabetical(self, repo, papers) -> None:
        """The replacement for ``if [ -f docs/paper/basanos.tex ]``.

        Args:
            repo: The repository root.
            papers: The paper-writing helper.
        """
        folder = repo / "docs" / "paper"
        papers("zeta.tex", "alpha.tex")
        assert paper_tasks.main_document(folder).name == "alpha.tex"
        papers("paper.tex")
        assert paper_tasks.main_document(folder).name == "paper.tex"
        papers("main.tex")
        assert paper_tasks.main_document(folder).name == "main.tex"

    def test_nested_tex_files_are_not_candidates(self, repo, papers) -> None:
        """Chapters under a subdirectory are inputs, not root documents.

        Args:
            repo: The repository root.
            papers: The paper-writing helper.
        """
        papers("main.tex")
        chapters = repo / "docs" / "paper" / "chapters"
        chapters.mkdir()
        (chapters / "aaa.tex").write_text("chapter\n")
        assert paper_tasks.main_document(repo / "docs" / "paper").name == "main.tex"

    def test_compiles_in_the_paper_folder(self, repo, recorder: Recorder, present: set[str], papers) -> None:
        """Run latexmk with the folder as cwd, so its aux files land beside the source.

        Args:
            repo: The repository root.
            recorder: The uv recorder.
            present: The installed-tool set.
            papers: The paper-writing helper.
        """
        present.add("latexmk")
        papers("main.tex")
        state = runner.run(["paper"], Config.load(root=repo))
        assert state.results[0].status is Status.OK
        call = recorder.find("latexmk")
        assert call.flags == ["-pdf", "-bibtex", "-interaction=nonstopmode", "main.tex"]
        assert call.kwargs["cwd"] == repo / "docs" / "paper"

    def test_skips_a_folder_with_no_tex(self, repo, recorder: Recorder, present: set[str]) -> None:
        """An adopted-but-unused bundle skips rather than failing.

        Args:
            repo: The repository root.
            recorder: The uv recorder.
            present: The installed-tool set.
        """
        present.add("latexmk")
        (repo / "docs" / "paper").mkdir(parents=True)
        state = runner.run(["paper"], Config.load(root=repo))
        assert state.results[0].status is Status.SKIPPED
        assert recorder.calls == []

    def test_clean_skips_without_a_paper_folder(self, cfg: Config, recorder: Recorder, present: set[str]) -> None:
        """Cleaning a folder that does not exist reports that rather than failing.

        Args:
            cfg: The resolved config.
            recorder: The uv recorder.
            present: The installed-tool set.
        """
        present.add("latexmk")
        with pytest.raises(Skip, match="paper_folder"):
            paper_tasks.paper_clean(cfg)
        assert recorder.calls == []

    def test_clean_tolerates_an_unbuilt_paper(self, repo, recorder: Recorder, present: set[str], papers) -> None:
        """``latexmk -C || true``.

        Args:
            repo: The repository root.
            recorder: The uv recorder.
            present: The installed-tool set.
            papers: The paper-writing helper.
        """
        present.add("latexmk")
        papers("main.tex")
        paper_tasks.paper_clean(Config.load(root=repo))
        assert recorder.find("latexmk").kwargs["check"] is False


class TestPresentation:
    """presentation.mk, minus the global npm install."""

    @pytest.fixture
    def deck(self, repo) -> None:
        """Write the default slide deck.

        Args:
            repo: The repository root.
        """
        (repo / "PRESENTATION.md").write_text("# Slide\n")

    def test_marp_on_path_wins(self, cfg: Config, present: set[str]) -> None:
        """A deliberately installed or pinned Marp is used as-is.

        Args:
            cfg: The resolved config.
            present: The installed-tool set.
        """
        present.update({"marp", "npx"})
        assert presentation_tasks.marp_argv(cfg) == ("marp", ())

    def test_npx_replaces_the_global_install(self, cfg: Config, present: set[str]) -> None:
        """``npx --yes`` keeps the fragment's convenience without the global install.

        Args:
            cfg: The resolved config.
            present: The installed-tool set.
        """
        present.add("npx")
        assert presentation_tasks.marp_argv(cfg) == ("npx", ("--yes", "@marp-team/marp-cli"))

    def test_marp_package_is_pinnable(self, repo, present: set[str]) -> None:
        """The setting is what npx is handed, so a consumer can pin the CLI.

        Args:
            repo: The throwaway repository.
            present: The installed-tool set.
        """
        present.add("npx")
        (repo / "pyproject.toml").write_text(
            '[project]\nname = "demo"\nversion = "0.1.0"\n\n'
            '[tool.rhiza-task]\nmarp-package = "@marp-team/marp-cli@4.2.3"\n'
        )
        _, prefix = presentation_tasks.marp_argv(Config.load(root=repo))
        assert prefix == ("--yes", "@marp-team/marp-cli@4.2.3")

    def test_no_node_at_all_skips(self, cfg: Config, present: set[str]) -> None:
        """Neither marp nor npx is a skip naming Node, which is the actual prerequisite.

        Args:
            cfg: The resolved config.
            present: The installed-tool set, left empty.
        """
        with pytest.raises(Skip, match=re.escape("nodejs.org")):
            presentation_tasks.marp_argv(cfg)

    def test_html_output_keeps_the_fragments_filename(
        self, repo, recorder: Recorder, present: set[str], deck: None
    ) -> None:
        """PRESENTATION.md still produces presentation.html, lower-cased.

        Args:
            repo: The repository root.
            recorder: The uv recorder.
            present: The installed-tool set.
            deck: Writes the deck.
        """
        present.add("marp")
        state = runner.run(["presentation"], Config.load(root=repo))
        assert state.results[0].status is Status.OK
        assert recorder.find("marp").flags == ["PRESENTATION.md", "-o", "presentation.html"]

    def test_pdf_allows_local_files(self, repo, recorder: Recorder, present: set[str], deck: None) -> None:
        """Without the flag, headless Chrome drops every local image from the PDF.

        Args:
            repo: The repository root.
            recorder: The uv recorder.
            present: The installed-tool set.
            deck: Writes the deck.
        """
        present.add("marp")
        presentation_tasks.presentation_pdf(Config.load(root=repo))
        flags = recorder.find("marp").flags
        assert flags == ["PRESENTATION.md", "-o", "presentation.pdf", "--allow-local-files"]

    def test_missing_deck_skips(self, cfg: Config, recorder: Recorder, present: set[str]) -> None:
        """No PRESENTATION.md is a skip, not a Marp error.

        Args:
            cfg: The resolved config.
            recorder: The uv recorder.
            present: The installed-tool set.
        """
        present.add("marp")
        with pytest.raises(Skip, match=re.escape("PRESENTATION.md")):
            presentation_tasks.presentation(cfg)
        assert recorder.calls == []

    def test_serve_watches_the_repository(self, repo, recorder: Recorder, present: set[str], deck: None) -> None:
        """``marp -s .``, unchanged.

        Args:
            repo: The repository root.
            recorder: The uv recorder.
            present: The installed-tool set.
            deck: Writes the deck.
        """
        present.add("marp")
        presentation_tasks.presentation_serve(Config.load(root=repo))
        assert recorder.find("marp").flags == ["-s", "."]


def _canned(text: str) -> Callable[..., str]:
    """Build a stand-in for :func:`~rhiza_task.uv.capture`.

    Args:
        text: What the faked command should have written to stdout.

    Returns:
        A callable accepting capture's signature and returning ``text``.
    """

    def fake(*args: object, **kwargs: object) -> str:
        """Return the canned output.

        Args:
            *args: Ignored.
            **kwargs: Ignored.

        Returns:
            The canned stdout.
        """
        return text

    return fake
