"""``update``: the template sync wrapped as one command.

Asserted the way every other task is -- on the argument vectors it *would* run -- with one
addition that matters more here than elsewhere. This module drives another project's
scripts, so the thing most likely to break is not the code below but the contract between
them: the script filenames, the order, the ``--no-project`` and interpreter pin, and the
exit codes ``sync.py`` documents. Those are what these tests pin, because they are what a
change in either repository would silently invalidate.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from rhiza_task.config import Config
from rhiza_task.spec import Failed
from rhiza_task.tasks import template

from .conftest import Recorder


@pytest.fixture
def managed(cfg: Config) -> Config:
    """Give the throwaway repository a ``.rhiza/template.yml`` so ``update``'s guard passes.

    Args:
        cfg: The resolved config.

    Returns:
        The same config, with the pointer file now on disk.
    """
    pointer = cfg.root / template.TEMPLATE_YML
    pointer.parent.mkdir(parents=True, exist_ok=True)
    pointer.write_text('template-repository: "jebel-quant/rhiza"\nref: "v1.3.2"\ntemplates:\n  - github-marimo\n')
    return cfg


@pytest.fixture
def scripts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point ``RHIZA_CLAUDE_DIR`` at a directory that looks like a rhiza-claude clone.

    The override path is used for most tests because it is the one that runs no git at all,
    which keeps a test about the sync vectors from also being a test about cloning.

    Args:
        tmp_path: pytest's temporary directory.
        monkeypatch: pytest's patcher.

    Returns:
        The scripts directory the task should resolve to.
    """
    clone = tmp_path / "rhiza-claude"
    scripts_dir = clone / template.SCRIPTS_SUBDIR
    scripts_dir.mkdir(parents=True)
    monkeypatch.setenv("RHIZA_CLAUDE_DIR", str(clone))
    return scripts_dir


class TestUpdateVectors:
    """What the task runs, in what order, against another project's scripts."""

    def test_runs_the_three_scripts_in_the_documented_order(
        self, managed: Config, scripts: Path, recorder: Recorder
    ) -> None:
        """A clean sync runs sync then stage, and never resolve.

        Exit 0 means "synced cleanly, or already up to date", so reaching for
        ``resolve_conflicts.py`` there would take the template's side of markers that are
        not present -- work the upstream document explicitly scopes to exit 1.

        Args:
            managed: A config whose repository carries a template pointer.
            scripts: The scripts directory.
            recorder: The uv recorder.
        """
        template.update(managed)
        ran = [Path(call.args[1]).name for call in recorder.calls if call.tool == "python"]
        assert ran == ["sync.py", "stage_synced.py"]

    def test_resolves_conflicts_only_on_exit_one(self, managed: Config, scripts: Path, recorder: Recorder) -> None:
        """Exit 1 is an expected outcome that adds a step, not a failure.

        Args:
            managed: A config whose repository carries a template pointer.
            scripts: The scripts directory.
            recorder: The uv recorder.
        """
        recorder.codes = [template.CONFLICTS]
        template.update(managed)
        ran = [Path(call.args[1]).name for call in recorder.calls if call.tool == "python"]
        assert ran == ["sync.py", "resolve_conflicts.py", "stage_synced.py"]

    def test_a_refusal_stops_before_staging(self, managed: Config, scripts: Path, recorder: Recorder) -> None:
        """Exit 2 applied nothing, so staging would stage a previous run's lock.

        Args:
            managed: A config whose repository carries a template pointer.
            scripts: The scripts directory.
            recorder: The uv recorder.
        """
        recorder.codes = [2]
        with pytest.raises(Failed, match="sync refused"):
            template.update(managed)
        ran = [Path(call.args[1]).name for call in recorder.calls if call.tool == "python"]
        assert ran == ["sync.py"]

    def test_a_refusal_after_a_bump_says_the_bump_is_committed(
        self, managed: Config, scripts: Path, recorder: Recorder
    ) -> None:
        """Say that the bump is committed, since "applied nothing" is true only of the sync.

        The bump has to be committed for sync to run at all, so a refusal leaves one commit
        behind. Saying "applied nothing" without qualifying it would send someone looking for
        a clean tree they no longer have.

        Args:
            managed: A config whose repository carries a template pointer.
            scripts: The scripts directory.
            recorder: The uv recorder.
        """
        recorder.codes = [0, 2]
        with pytest.raises(Failed, match=r"the bump to v1\.8\.0 is already committed"):
            template.update(replace(managed, template_ref="v1.8.0"))

    def test_pins_the_interpreter_and_skips_the_project(
        self, managed: Config, scripts: Path, recorder: Recorder
    ) -> None:
        """The scripts are another project's: 3.12, and no resolution of this repo's env.

        A bare ``python3`` on macOS is 3.9 and crashes ``sync.py``, and ``--no-project``
        stops uv resolving the target repository for a stdlib-only script. Both are pinned
        here because both are invisible until they fail on someone else's machine.

        Args:
            managed: A config whose repository carries a template pointer.
            scripts: The scripts directory.
            recorder: The uv recorder.
        """
        template.update(managed)
        call = recorder.find("python")
        assert call.kwargs["python"] == "3.12"
        assert call.kwargs["no_project"] is True

    def test_stage_is_asked_for_json(self, managed: Config, scripts: Path, recorder: Recorder) -> None:
        """``--json`` is the surface upstream pins; its human output may be reworded.

        Args:
            managed: A config whose repository carries a template pointer.
            scripts: The scripts directory.
            recorder: The uv recorder.
        """
        template.update(managed)
        stage = [c for c in recorder.calls if c.tool == "python" and c.args[1].endswith("stage_synced.py")]
        assert stage[0].args[3:] == ("--json",)

    def test_the_repository_is_passed_to_every_script(self, managed: Config, scripts: Path, recorder: Recorder) -> None:
        """Each script takes the target repo as its first argument, not the cwd by luck.

        Args:
            managed: A config whose repository carries a template pointer.
            scripts: The scripts directory.
            recorder: The uv recorder.
        """
        recorder.codes = [template.CONFLICTS]
        template.update(managed)
        assert all(c.args[2] == str(managed.root) for c in recorder.calls if c.tool == "python")


class TestTemplateRef:
    """Bumping the pointer, which is step one and the only file this module writes."""

    def test_rewrites_only_the_ref_line(self, managed: Config, scripts: Path, recorder: Recorder) -> None:
        """``profiles:``/``templates:``/``exclude:`` are a separate decision a bump must not carry.

        Args:
            managed: A config whose repository carries a template pointer.
            scripts: The scripts directory.
            recorder: The uv recorder.
        """
        template.update(replace(managed, template_ref="v1.8.0"))
        text = (managed.root / template.TEMPLATE_YML).read_text()
        assert 'ref: "v1.8.0"' in text
        assert "v1.3.2" not in text
        assert "github-marimo" in text

    def test_an_indented_ref_is_not_the_one_bumped(self, managed: Config, scripts: Path) -> None:
        """Only column zero counts, so a nested ``ref:`` inside a list is left alone.

        Matching at any indentation would rewrite a template's own pinned ref, which is the
        bug the upstream ``sed`` anchor avoids and the reason it was copied rather than
        improved on.

        Args:
            managed: A config whose repository carries a template pointer.
            scripts: The scripts directory.
        """
        pointer = managed.root / template.TEMPLATE_YML
        pointer.write_text('ref: "v1.3.2"\ntemplates:\n  - name: book\n    ref: "v0.1.0"\n')
        template._bump_ref(pointer, "v1.8.0")
        assert pointer.read_text() == 'ref: "v1.8.0"\ntemplates:\n  - name: book\n    ref: "v0.1.0"\n'

    def test_a_file_with_no_ref_line_is_reported(self, managed: Config, scripts: Path) -> None:
        """Appending a ``ref:`` would be a guess about a file this module does not parse.

        Args:
            managed: A config whose repository carries a template pointer.
            scripts: The scripts directory.
        """
        pointer = managed.root / template.TEMPLATE_YML
        pointer.write_text('template-repository: "jebel-quant/rhiza"\n')
        with pytest.raises(Failed, match="no top-level template-branch or ref line"):
            template._bump_ref(pointer, "v1.8.0")

    def test_an_empty_ref_leaves_the_pointer_alone(self, managed: Config, scripts: Path, recorder: Recorder) -> None:
        """Empty means "re-sync at what it already names", which is a real case.

        Args:
            managed: A config whose repository carries a template pointer.
            scripts: The scripts directory.
            recorder: The uv recorder.
        """
        before = (managed.root / template.TEMPLATE_YML).read_text()
        template.update(managed)
        assert (managed.root / template.TEMPLATE_YML).read_text() == before

    def test_template_branch_wins_over_ref(self, managed: Config, scripts: Path) -> None:
        """The sync obeys ``template-branch`` when both keys are present, so the bump must too.

        Rewriting ``ref:`` in a file like this would leave it *claiming* the new version while
        the sync fetched the old one, and every exit code in the run would still be 0. This is
        the one bug in this module that no exit status would have reported.

        Args:
            managed: A config whose repository carries a template pointer.
            scripts: The scripts directory.
        """
        pointer = managed.root / template.TEMPLATE_YML
        pointer.write_text('template-branch: "v1.7.0"\nref: "v1.3.2"\n')
        template._bump_ref(pointer, "v1.8.0")
        assert pointer.read_text() == 'template-branch: "v1.8.0"\nref: "v1.3.2"\n'

    def test_ref_is_used_when_it_is_the_only_key(self, managed: Config, scripts: Path) -> None:
        """A file carrying only ``ref:`` is the common case and is bumped there.

        Args:
            managed: A config whose repository carries a template pointer.
            scripts: The scripts directory.
        """
        pointer = managed.root / template.TEMPLATE_YML
        pointer.write_text('ref: "v1.3.2"\n')
        template._bump_ref(pointer, "v1.8.0")
        assert pointer.read_text() == 'ref: "v1.8.0"\n'

    def test_the_bump_is_committed_before_the_sync(self, managed: Config, scripts: Path, recorder: Recorder) -> None:
        """``sync.py`` refuses a dirty tree, and the bump is what makes it dirty.

        Found by running the task for real against the actual scripts: sync reported
        ``Working tree is not clean`` naming the file the task had just written. A vector
        assertion could not have found it, because the refusal is a fact about ``sync.py``
        rather than about the arguments handed to it -- which is the reason CI runs this
        against the real scripts as well.

        The pathspec is asserted too: ``git commit -am`` would sweep every other modified
        tracked file into a commit whose message claims to be a version bump.

        Args:
            managed: A config whose repository carries a template pointer.
            scripts: The scripts directory.
            recorder: The uv recorder.
        """
        template.update(replace(managed, template_ref="v1.8.0"))
        order = [c.tool for c in recorder.calls]
        assert order.index("git") < order.index("python")
        commit = recorder.find("git")
        assert commit.args[1] == "commit"
        assert commit.args[-2:] == ("--", template.TEMPLATE_YML)

    def test_no_commit_is_made_without_a_ref(self, managed: Config, scripts: Path, recorder: Recorder) -> None:
        """A re-sync at the pinned ref writes no file, so it has nothing to commit.

        Args:
            managed: A config whose repository carries a template pointer.
            scripts: The scripts directory.
            recorder: The uv recorder.
        """
        template.update(managed)
        assert "git" not in recorder.tools()


class TestScriptProvisioning:
    """Where the scripts come from, and whose clone gets written to."""

    def test_an_override_is_used_as_found(self, managed: Config, scripts: Path, recorder: Recorder) -> None:
        """Someone else's clone is read, never fetched into or reset.

        A ``git reset --hard`` into the ``~/.local/share/rhiza-claude`` that ``headless.md``
        tells people to make would discard whatever they had in it.

        Args:
            managed: A config whose repository carries a template pointer.
            scripts: The scripts directory.
            recorder: The uv recorder.
        """
        template.update(managed)
        assert "git" not in recorder.tools()

    def test_an_override_without_scripts_is_refused(
        self, managed: Config, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Falling back to the cache would run a different copy than the one asked for.

        Args:
            managed: A config whose repository carries a template pointer.
            tmp_path: pytest's temporary directory.
            monkeypatch: pytest's patcher.
        """
        empty = tmp_path / "not-a-clone"
        empty.mkdir()
        monkeypatch.setenv("RHIZA_CLAUDE_DIR", str(empty))
        with pytest.raises(Failed, match="has no plugin/scripts"):
            template.update(managed)

    def test_a_missing_cache_is_cloned_shallow(
        self, managed: Config, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, recorder: Recorder
    ) -> None:
        """With no override the module makes its own clone, and owns it.

        Args:
            managed: A config whose repository carries a template pointer.
            tmp_path: pytest's temporary directory.
            monkeypatch: pytest's patcher.
            recorder: The uv recorder.
        """
        monkeypatch.delenv("RHIZA_CLAUDE_DIR", raising=False)
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
        template.update(managed)
        assert recorder.find("git").args[:4] == ("git", "clone", "--depth", "1")
        assert template.SCRIPTS_REPO in recorder.find("git").args

    def test_a_cache_this_module_owns_is_refreshed(
        self, managed: Config, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, recorder: Recorder
    ) -> None:
        """A stale ``sync.py`` is a correctness risk, and our own copy has nothing to lose.

        Args:
            managed: A config whose repository carries a template pointer.
            tmp_path: pytest's temporary directory.
            monkeypatch: pytest's patcher.
            recorder: The uv recorder.
        """
        monkeypatch.delenv("RHIZA_CLAUDE_DIR", raising=False)
        cache = tmp_path / "cache"
        monkeypatch.setenv("XDG_CACHE_HOME", str(cache))
        (cache / "rhiza-task" / "rhiza-claude" / ".git").mkdir(parents=True)
        template.update(managed)
        git = [c.args for c in recorder.calls if c.tool == "git"]
        assert [a[3] for a in git] == ["fetch", "reset"]

    def test_the_cache_falls_back_to_the_home_directory(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """``XDG_CACHE_HOME`` is honoured where set; ``~/.cache`` is the standard default.

        Args:
            monkeypatch: pytest's patcher.
        """
        monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
        assert template._cache_root() == Path.home() / ".cache" / "rhiza-task" / "rhiza-claude"


class TestUpdateGuards:
    """The task is a no-op where there is no template to sync."""

    def test_a_repo_with_no_pointer_skips(self) -> None:
        """This repository is one of those, which is why the task cannot be dogfooded here."""
        from rhiza_task.spec import REGISTRY

        (guard,) = REGISTRY["update"].guards
        assert guard.file == template.TEMPLATE_YML
        assert "not rhiza-managed" in guard.reason

    def test_git_is_not_guarded_because_doctor_owns_it(self) -> None:
        """Guarding on git would demote a hard prerequisite to a per-task skip.

        ``doctor`` names uv and git as the two things a process running ``uvx rhiza-task``
        cannot do without, and fails on a miss;
        ``TestDoctor::test_probes_nothing_a_guard_already_owns`` asserts the two sets stay
        disjoint. It caught a ``Guard(tool="git")`` here on the first run, which is the whole
        reason this test exists rather than the guard.
        """
        from rhiza_task.spec import REGISTRY

        assert not any(guard.tool for guard in REGISTRY["update"].guards)
