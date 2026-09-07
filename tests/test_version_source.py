"""The version is the git tag, and these are the four ways that could quietly stop being true.

Adopting hatch-vcs (#160) removed the version from every file that used to hold a copy of
it: ``[project].version``, ``__version__`` and ``uv.lock``. That is what makes a release
one step -- there is no number to write, so there is no release that can ship with two of
the three updated. v1.0.0 shipped exactly that way, with ``uv.lock`` left behind, and every
gate then failed because ``uv lock --check`` is the first thing ``install`` runs.

The failure mode a *derived* version has instead is quieter than the one it replaces, which
is why this module exists rather than trusting the config to stay put. setuptools-scm and
hatch-vcs both fall back rather than fail: a clone with no tags derives
``0.1.dev1+g<sha>``, so a distribution built from a shallow checkout is published at a
version nobody asked for -- green build, wrong artifact on PyPI, and no error anywhere.
That is the same silent-green shape rhiza keeps a record of (jebel-quant/rhiza#1505, #1511,
#1516, #1535), reached here through a checkout depth.

Security Notes:
- S101 (assert usage): Asserts are the mechanism pytest reports failures through.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent


def _pyproject() -> dict:
    """Return the parsed manifest.

    Returns:
        pyproject.toml as a dict.
    """
    with (_ROOT / "pyproject.toml").open("rb") as handle:
        return tomllib.load(handle)


def test_the_version_is_declared_dynamic_and_written_nowhere() -> None:
    """``[project]`` must derive its version rather than carry one.

    Both halves: PEP 621 forbids declaring it both ways, and a written version would be a
    second source of truth that hatch-vcs silently overrides at build time -- so the number
    a reader sees in the manifest would not be the number that ships.
    """
    project = _pyproject()["project"]
    assert "version" in (project.get("dynamic") or ()), (
        "[project] no longer lists `version` in `dynamic`, so hatch-vcs is not supplying it "
        "and the build has no version source."
    )
    assert "version" not in project, (
        '[project] writes a `version` alongside `dynamic = ["version"]`. PEP 621 forbids '
        "that, and the written one is decoration hatch-vcs overrides -- a number in the "
        "manifest that is not the number that ships."
    )


def test_hatch_takes_the_version_from_the_vcs_without_guessing_forward() -> None:
    """The source must be the VCS, and an untagged commit must not claim the next release.

    ``version_scheme`` defaults to ``guess-next-dev``, which reports an untagged commit as
    ``1.7.0.dev4`` while 1.7.0 is unreleased -- a development build claiming a version that
    ``uvx rhiza-task@1.7.0`` will later resolve to something else entirely.
    """
    hatch_version = _pyproject()["tool"]["hatch"]["version"]
    assert hatch_version.get("source") == "vcs", (
        "[tool.hatch.version].source is not `vcs`, so the version no longer comes from the tag"
    )
    raw = hatch_version.get("raw-options") or {}
    assert raw.get("version_scheme") == "no-guess-dev", (
        "the version scheme is not `no-guess-dev`, so an untagged commit reports itself as the "
        "next unreleased version rather than as a post-release of the last real one."
    )


def test_no_bumpversion_entry_writes_a_version_number_again() -> None:
    """The remaining entries must rewrite documentation pins, and nothing else.

    ``src/rhiza_task/__init__.py`` and ``uv.lock`` each had an entry, and each entry existed
    to keep a *copy* of the version in step. Re-adding either would reintroduce the copy --
    and would fail, since bump-my-version errors on a file that does not contain the version
    it searched for.
    """
    entries = _pyproject()["tool"]["bumpversion"].get("files") or []
    targets = [entry.get("filename") or entry.get("glob") or "" for entry in entries]
    for forbidden in ("src/rhiza_task/__init__.py", "uv.lock"):
        assert forbidden not in targets, (
            f"a [[tool.bumpversion.files]] entry targets {forbidden}, which holds no version "
            f"any more -- hatch-vcs derives it from the tag. The entries here are for the "
            f"`rhiza-task@X.Y.Z` documentation pins only."
        )
    assert targets, "every bumpversion entry is gone, so the docs pins are no longer rewritten on release"


def test_the_dunder_version_is_read_from_the_installed_metadata() -> None:
    """``__init__.py`` must not carry a literal version again.

    Asserted on the source rather than on the value: the value is correct either way in an
    installed tree, so a re-added literal would agree with the tag on the day it was written
    and drift silently afterwards -- which is the whole defect.
    """
    source = (_ROOT / "src" / "rhiza_task" / "__init__.py").read_text(encoding="utf-8")
    literal = re.search(r'^__version__\s*=\s*["\']', source, re.MULTILINE)
    assert literal is None, (
        "__init__.py assigns __version__ a string literal. It must read the installed "
        "distribution's metadata, which hatch-vcs stamped from the tag; a literal is a "
        "second copy that agrees with the tag only on the day it is written."
    )
    assert "importlib.metadata" in source, "__init__.py no longer reads its version from the package metadata"


def test_every_release_checkout_fetches_the_full_history() -> None:
    """A shallow checkout makes hatch-vcs derive a wrong version, and publish it.

    This is the one coupling adopting hatch-vcs introduced that nothing else here would
    notice. The workflow is template-owned, so a future sync is what would drop the option,
    and the symptom is on PyPI rather than in the run: with no tags reachable the version
    resolves to ``0.1.dev1+g<sha>`` and the build succeeds.

    Asserted for every checkout in the file rather than only the build job, because the
    version is derived wherever the package is built or installed -- and the next job added
    to this workflow needs it too.
    """
    text = (_ROOT / ".github" / "workflows" / "rhiza_release.yml").read_text(encoding="utf-8")
    blocks = text.split("uses: actions/checkout@")[1:]
    assert blocks, "rhiza_release.yml checks out nothing, so this assertion is measuring an empty list"
    for index, block in enumerate(blocks, start=1):
        assert "fetch-depth: 0" in block[:400], (
            f"checkout #{index} in rhiza_release.yml does not pass `fetch-depth: 0`. hatch-vcs "
            f"derives the version from the tags in the clone, so a shallow one publishes "
            f"`0.1.dev1+g<sha>` and reports success."
        )
