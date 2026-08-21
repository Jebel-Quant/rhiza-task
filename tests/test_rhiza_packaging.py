"""The declared version and the installed one must agree.

Adapted from `jebel-quant/rhiza`'s `bundles/python-core/tests/test_rhiza_packaging.py` at
v1.4.2, and adapted rather than copied because upstream's file carries two rationales and
only one of them applies here.

The one that does: ``pyproject.toml`` declares a version and the environment installs one,
and those drift. An editable install left behind by a rename, a ``uv sync`` that never ran,
a build backend pointed at a tree it no longer owns -- each surfaces here as a version
mismatch rather than as a confusing ``ImportError`` several files later.

That matters more in this repository than in a consumer, because this repository *dogfoods
its own CLI*. Every gate runs as ``uv run rhiza-task <task>``, resolved from the working
tree, and ``rhiza-task version`` reads the installed distribution's metadata. So a stale
install does not merely sit there: it makes the CLI under test report a version that is not
the one being tested, and every gate still passes.

The rationale that does **not** carry over is upstream's second one -- that this is the only
test a freshly synced project has, standing in for an empty suite that would let ``test``
pass while measuring nothing. That vacuum cannot exist here: the suite is 300 tests behind a
100% coverage floor. This file is narrow release-plumbing invariants, not a floor.

The second such invariant is the release workflow's *filename*, which is load-bearing for
publishing in a way no other file here is -- see
:func:`test_the_trusted_publishing_workflow_keeps_its_filename`.

Deliberately self-contained, as upstream is: no fixtures, so it cannot depend on what
pytest-rhiza contributes to ``rhiza-task rhiza-test``.
"""

from __future__ import annotations

import tomllib
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as installed_version
from pathlib import Path

import pytest

# `tests/` sits at the project root, so this file's grandparent is the root.
#
# `.absolute()`, not `.resolve()`. Nothing here is a symlink today, so the two agree -- but
# upstream's copy *is* symlinked into `bundles/python-core/tests/`, where `.resolve()`
# follows the link and computes a root with no pyproject.toml in it. The suite then skips
# for a plausible-looking wrong reason instead of running, which is the worst outcome
# available to a test whose failure mode is already a skip.
_ROOT = Path(__file__).absolute().parent.parent


def _project_table() -> dict:
    """Return the ``[project]`` table from pyproject.toml, skipping when unusable.

    Returns:
        The parsed ``[project]`` table.
    """
    pyproject = _ROOT / "pyproject.toml"
    if not pyproject.is_file():
        pytest.skip("no pyproject.toml at the project root")
    with pyproject.open("rb") as handle:
        table = tomllib.load(handle).get("project")
    if not isinstance(table, dict):
        pytest.skip("pyproject.toml declares no [project] table")
    return table


def test_the_installed_version_matches_pyproject() -> None:
    """The distribution installed in the environment must match the declared version.

    Every guard here is a ``skip`` rather than a failure, because each names a project shape
    in which the question is not askable rather than one in which the answer is wrong: a
    dynamic version has no static value to compare, and a ``uv`` *virtual* project (no
    ``[build-system]``) installs no distribution metadata at all. This repository is neither
    -- it declares its version statically and builds a real wheel -- so here the assert is
    reached and the skips are the inherited generality, not slack. Deliberately not naming the
    version: the point is that it is static, and a figure quoted here is one more thing a
    release has to remember to move.
    """
    project = _project_table()

    name = project.get("name")
    if not isinstance(name, str) or not name.strip():
        pytest.skip("[project].name is missing or not a plain string")

    declared = project.get("version")
    if not isinstance(declared, str):
        pytest.skip("[project].version is dynamic -- there is no static value to compare")

    try:
        found = installed_version(name)
    except PackageNotFoundError:
        pytest.skip(f"{name!r} is not installed as a distribution (a virtual project has no metadata)")

    assert found == declared, (
        f"pyproject.toml declares version {declared!r} but the installed {name!r} reports {found!r}. "
        f"The environment is stale, or the build backend is picking up a different tree -- re-run "
        f"`uv run rhiza-task install`, and check [build-system] and the packages it is told to "
        f"include if that does not fix it."
    )


# The one path in this repository that cannot be renamed by editing this repository alone.
TRUSTED_PUBLISHING_WORKFLOW = ".github/workflows/rhiza_release.yml"
"""The workflow filename PyPI's trusted publisher for this project is registered against."""


def test_the_trusted_publishing_workflow_keeps_its_filename() -> None:
    """The release workflow must keep the exact path PyPI's trusted publisher names.

    PyPI Trusted Publishing validates the *workflow file path*, not the repository or the
    job, so renaming this file silently revokes this project's ability to publish. The
    failure is maximally delayed: every job stays green, `rhiza-task all` passes, the PR
    merges, and the break surfaces only when a tag runs the release for real -- by which
    point the release is the thing that is broken.

    That is why the invariant is asserted here rather than left to the note in the
    workflow's own header. A comment is read by whoever opens that file; a rename is done by
    whoever *doesn't*. Renaming it legitimately means editing PyPI's publisher entry in the
    same change, and then this test's constant -- which is the reminder, in the one place
    that cannot be skipped.

    Deliberately only existence, not content: what PyPI pins is the path. Asserting anything
    about the jobs inside would couple this test to release-workflow edits it has no opinion
    about, and every such coupling is a future red build that teaches nothing.
    """
    workflow = _ROOT / TRUSTED_PUBLISHING_WORKFLOW
    assert workflow.is_file(), (
        f"{TRUSTED_PUBLISHING_WORKFLOW} is missing. PyPI Trusted Publishing validates the exact "
        f"workflow file path, so this project cannot publish until the file is restored at that "
        f"path -- or, if the rename was deliberate, until PyPI's trusted publisher entry for this "
        f"project is updated to the new filename and this test's TRUSTED_PUBLISHING_WORKFLOW "
        f"follows it."
    )
