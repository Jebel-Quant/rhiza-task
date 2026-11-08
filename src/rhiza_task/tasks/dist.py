"""``dist-check``: build the distributions, and check them before anything publishes them.

The release workflows already compared the *filenames* ``uv build`` wrote against the tag.
Two failures pass that comparison, and both have shipped:

- **A declared licence file that is not in the package.** chebfun 0.11.0 declared
  ``license-files = ["LICENSE"]`` while the repository held ``LICENSE.rst``. The build
  succeeded, the release succeeded, and the published metadata did not carry the licence
  (rhiza#1743, chebpy#518). Nothing reads ``License-File`` until someone needs it.
- **A version that only exists inside the checkout.** A version derived from the VCS --
  hatch-vcs, setuptools-scm -- is read from git when the sdist is built, and has to be
  frozen into the sdist's ``PKG-INFO`` for anyone building *from* it: conda-forge, a distro,
  ``pip install`` of the sdist. When it is not, a rebuild outside the repository falls back
  to ``0.0.0`` or ``0.1.dev1`` with a green build and no error.

So this task builds into ``dist/``, then reads what it built rather than what the manifest
says: the version in each artifact's metadata, every ``License-File`` entry and whether the
file is really in the archive, and every file the project *declares* as a licence. It then
unpacks the sdist into a temporary directory -- outside any git repository, with version
overrides such as ``SETUPTOOLS_SCM_PRETEND_VERSION`` removed from the environment -- and
rebuilds a wheel from it, which has to come out at the same version with the same licences.

``dist/`` is left holding exactly the artifacts that passed. That is the point of building
here rather than checking a folder someone else filled: a release job can upload the
directory this task validated, and nothing can be rebuilt in between. Earlier sdists and
wheels in ``dist/`` are removed first, because a stale one would be validated -- or
published -- alongside the new.

What it does not do is compare the version with a release tag. Tag handling (``v`` prefixes,
semver-to-PEP 440 normalisation) needs ``packaging``, which is not a dependency of this
package, and the release workflows already make that comparison against the filenames this
task leaves in ``dist/``. The two halves meet there.
"""

from __future__ import annotations

import os
import tarfile
import tempfile
import tomllib
import zipfile
from dataclasses import dataclass, field
from email.message import Message
from email.parser import HeaderParser
from pathlib import Path

from ..config import Config
from ..spec import Failed, Skip, task
from ..uv import uv

DIST_FOLDER = "dist"
"""Where the validated artifacts are left: ``uv build``'s own default, and what ``clean`` removes."""

VERSION_OVERRIDES = ("SETUPTOOLS_SCM_PRETEND_VERSION", "PDM_BUILD_SCM_VERSION")
"""Prefixes of the variables that override a VCS-derived version, removed for the rebuild.

Prefixes, because setuptools-scm (and hatch-vcs, which drives it) also reads a per-distribution
``SETUPTOOLS_SCM_PRETEND_VERSION_FOR_<NAME>``. A rebuild that inherits one of these reports the
override rather than what the sdist carries, which is the exact thing the rebuild is there to
find out.
"""

SKIPPED_ROOTS = ("dist", "build")
"""Top-level folders a licence glob is not matched in, besides hidden ones such as ``.venv``.

A pattern like ``**/LICENSE*`` would otherwise count a dependency's licence in the virtualenv,
or a previous build's copy, as the project's own.
"""


@dataclass(frozen=True)
class Artifact:
    """What one distribution says about itself, read from the archive.

    Attributes:
        name: The archive's filename, for messages.
        version: ``Version`` from its metadata.
        license_files: Its ``License-File`` entries, in order.
        contents: The bytes of each licence file found in the archive, keyed by the path the
            metadata (or the project's declaration) uses. A path with no entry is missing.
    """

    name: str
    version: str
    license_files: tuple[str, ...]
    contents: dict[str, bytes] = field(default_factory=dict)


@task(
    "dist-check",
    "build the sdist and wheel, and check their version and licence metadata",
    section="Python",
    layer="python",
    needs=("setup",),
)
def dist_check(cfg: Config) -> None:
    """Build into ``dist/``, read the artifacts back, and rebuild a wheel from the sdist.

    Args:
        cfg: The resolved configuration.

    Raises:
        Skip: When the project declares no ``[build-system]``, so there is nothing to build.
        Failed: When the build produced an unexpected set of files, or any check failed.
    """
    root = cfg.root
    if not _buildable(root / "pyproject.toml"):
        raise Skip("no [build-system] in pyproject.toml: nothing is built")

    dist = root / DIST_FOLDER
    _clear(dist)
    uv("build", "--out-dir", str(dist), cwd=root)
    sdists, wheels = sorted(dist.glob("*.tar.gz")), sorted(dist.glob("*.whl"))
    if len(sdists) != 1 or not wheels:
        raise Failed(
            1, f"expected one sdist and at least one wheel in {DIST_FOLDER}/, found {len(sdists)} and {len(wheels)}"
        )

    declared, problems = declared_license_files(root)
    artifacts = [read_sdist(sdists[0], declared), *(read_wheel(w, declared) for w in wheels)]
    with tempfile.TemporaryDirectory(prefix="rhiza-dist-check-") as tmp:
        artifacts.append(read_wheel(_rebuild(sdists[0], Path(tmp)), declared, rebuilt=True))

    for artifact in artifacts:
        print(
            f"[INFO] {artifact.name}: {artifact.version}, License-File: {', '.join(artifact.license_files) or 'none'}"
        )
    problems += check(artifacts, declared, root)
    if problems:
        for problem in problems:
            print(f"[ERROR] {problem}")
        raise Failed(1, f"{len(problems)} problem(s) in the built distributions")
    print(
        f"[INFO] {DIST_FOLDER}/ holds {len(sdists) + len(wheels)} validated distribution(s) at {artifacts[0].version}"
    )


def _buildable(pyproject: Path) -> bool:
    """Return whether the project declares a build backend.

    Args:
        pyproject: The manifest's path.

    Returns:
        True when it exists and has a ``[build-system]`` table. A uv *virtual* project has
        none, and builds nothing -- rhiza itself is one.
    """
    return pyproject.is_file() and "build-system" in tomllib.loads(pyproject.read_text(encoding="utf-8"))


def _clear(dist: Path) -> None:
    """Remove earlier sdists and wheels, leaving anything else in the folder alone.

    Args:
        dist: The output folder, which may not exist yet.
    """
    for stale in [*dist.glob("*.tar.gz"), *dist.glob("*.whl")]:
        print(f"[INFO] removing earlier build {stale.name}")
        stale.unlink()


def declared_license_files(root: Path) -> tuple[list[str], list[str]]:
    """Resolve the licence files ``pyproject.toml`` declares against the project tree.

    Reads PEP 639's ``[project].license-files`` globs and the older ``license = {file = ...}``
    table. A project that declares neither gets no expectation here -- its backend's defaults
    are still checked for consistency by :func:`check`, through the ``License-File`` entries.

    Args:
        root: The project root.

    Returns:
        The matched paths, POSIX-style and relative to ``root``, and one problem for each
        pattern that matches nothing -- which is the chebfun case: a declaration naming a file
        the repository does not have, which the backend accepted without a word.
    """
    found: list[str] = []
    problems: list[str] = []
    for pattern in _declared_patterns(root):
        matches = _matches(root, pattern)
        if not matches:
            problems.append(f"pyproject.toml declares licence file {pattern!r}, which matches no file in the project")
        found += [m for m in matches if m not in found]
    return found, problems


def _declared_patterns(root: Path) -> list[str]:
    """Return the licence globs and paths ``[project]`` declares, in declaration order.

    Args:
        root: The project root.

    Returns:
        ``license-files``' patterns, then ``license.file`` when the table form is used.
    """
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8")).get("project") or {}
    patterns = list(project.get("license-files") or [])
    legacy = project.get("license")
    if isinstance(legacy, dict) and "file" in legacy:
        patterns.append(legacy["file"])
    return patterns


def _matches(root: Path, pattern: str) -> list[str]:
    """Return the project's own files a pattern matches.

    Args:
        root: The project root.
        pattern: A glob relative to it.

    Returns:
        POSIX-style relative paths, sorted, outside hidden folders and :data:`SKIPPED_ROOTS`.
    """
    relative = (p.relative_to(root) for p in root.glob(pattern) if p.is_file())
    return sorted(r.as_posix() for r in relative if not _skipped(r.parts[0]))


def _skipped(top: str) -> bool:
    """Return whether a top-level path component is outside the project's own files.

    Args:
        top: The first component of a path relative to the root.

    Returns:
        True for hidden folders and :data:`SKIPPED_ROOTS`.
    """
    return top.startswith(".") or top in SKIPPED_ROOTS


def read_sdist(path: Path, declared: list[str]) -> Artifact:
    """Read an sdist's ``PKG-INFO`` and the licence files it holds.

    Args:
        path: The ``.tar.gz``.
        declared: The project's declared licence files, looked up as well as the metadata's.

    Returns:
        The artifact as the archive describes it.

    Raises:
        Failed: When the archive has no top-level ``PKG-INFO``.
    """
    with tarfile.open(path) as archive:
        members = {m.name: m for m in archive.getmembers() if m.isfile()}
        top = next(iter(members), "").split("/")[0]
        info = members.get(f"{top}/PKG-INFO")
        if info is None:
            raise Failed(1, f"{path.name} has no {top}/PKG-INFO")
        metadata = _parse(_read_member(archive, info))
        license_files = tuple(metadata.get_all("License-File") or ())
        contents = {
            p: _read_member(archive, members[f"{top}/{p}"])
            for p in {*license_files, *declared}
            if f"{top}/{p}" in members
        }
    return Artifact(path.name, metadata.get("Version", ""), license_files, contents)


def read_wheel(path: Path, declared: list[str], rebuilt: bool = False) -> Artifact:
    """Read a wheel's ``METADATA`` and the licence files it holds.

    PEP 639 puts a licence file under ``<dist-info>/licenses/<path>``; setuptools before it
    wrote ``<dist-info>/<path>``. Either counts as present.

    Args:
        path: The ``.whl``.
        declared: The project's declared licence files, looked up as well as the metadata's.
        rebuilt: Whether this wheel was rebuilt from the sdist, which the name then says.

    Returns:
        The artifact as the archive describes it.

    Raises:
        Failed: When the wheel does not hold exactly one ``.dist-info/METADATA``.
    """
    with zipfile.ZipFile(path) as archive:
        names = set(archive.namelist())
        found = [n for n in names if n.endswith(".dist-info/METADATA") and n.count("/") == 1]
        if len(found) != 1:
            raise Failed(1, f"{path.name} holds {len(found)} .dist-info/METADATA files, not one")
        info = found[0].rsplit("/", 1)[0]
        metadata = _parse(archive.read(found[0]))
        license_files = tuple(metadata.get_all("License-File") or ())
        members = {p: _wheel_member(names, info, p) for p in {*license_files, *declared}}
        contents = {p: archive.read(m) for p, m in members.items() if m is not None}
    name = f"{path.name} (rebuilt from the sdist)" if rebuilt else path.name
    return Artifact(name, metadata.get("Version", ""), license_files, contents)


def _wheel_member(names: set[str], info: str, path: str) -> str | None:
    """Locate a licence file in a wheel, in either layout.

    Args:
        names: Every member name in the wheel.
        info: Its ``.dist-info`` folder.
        path: The licence file's project-relative path.

    Returns:
        The member name, or ``None`` when the wheel does not hold it.
    """
    return next((m for m in (f"{info}/licenses/{path}", f"{info}/{path}") if m in names), None)


def _read_member(archive: tarfile.TarFile, member: tarfile.TarInfo) -> bytes:
    """Return a regular file's bytes from a tar archive.

    Args:
        archive: The open archive.
        member: A regular-file member of it.

    Returns:
        The file's contents.
    """
    handle = archive.extractfile(member)
    return handle.read() if handle is not None else b""


def _parse(raw: bytes) -> Message:
    """Parse core metadata, which is RFC 822 headers followed by an optional body.

    Args:
        raw: The bytes of ``PKG-INFO`` or ``METADATA``.

    Returns:
        The parsed headers, read with ``get``/``get_all``.
    """
    return HeaderParser().parsestr(raw.decode("utf-8", errors="replace"))


def _rebuild(sdist: Path, tmp: Path) -> Path:
    """Unpack the sdist outside any repository and build a wheel from what it holds.

    Args:
        sdist: The ``.tar.gz`` to rebuild from.
        tmp: An empty temporary directory, which must outlive the read of the result.

    Returns:
        The rebuilt wheel.

    Raises:
        Failed: When the sdist does not unpack to one folder or the build yields no wheel.
    """
    source, out = tmp / "source", tmp / "out"
    with tarfile.open(sdist) as archive:
        # The "data" filter refuses absolute paths, links out of the tree and device files,
        # which is what makes unpacking an archive this task did not write safe.
        archive.extractall(source, filter="data")
    tops = [p for p in source.iterdir() if p.is_dir()]
    if len(tops) != 1:
        raise Failed(1, f"{sdist.name} unpacks to {len(tops)} top-level folders, not one")

    # GIT_CEILING_DIRECTORIES stops git's upward search at the temporary directory, so a
    # backend that asks git for the version finds no repository, as a downstream builder
    # would not -- and the version has to come from the sdist's own PKG-INFO.
    overrides = [name for name in os.environ if name.startswith(VERSION_OVERRIDES)]
    uv(
        "build",
        "--wheel",
        "--out-dir",
        str(out),
        str(tops[0]),
        cwd=tmp,
        env={"GIT_CEILING_DIRECTORIES": str(tmp)},
        unset=overrides,
    )
    wheels = sorted(out.glob("*.whl"))
    if len(wheels) != 1:
        raise Failed(1, f"rebuilding {sdist.name} produced {len(wheels)} wheels, not one")
    return wheels[0]


def check(artifacts: list[Artifact], declared: list[str], root: Path) -> list[str]:
    """Compare the artifacts with each other and with the project's declared licences.

    The first artifact is the sdist, and the reference: every other one must carry its
    version and its ``License-File`` set.

    Args:
        artifacts: The sdist first, then the wheels, the rebuilt one last.
        declared: The project's declared licence files.
        root: The project root, for the declared files' contents.

    Returns:
        One message per problem, empty when everything agrees.
    """
    reference = artifacts[0]
    problems: list[str] = []
    for artifact in artifacts:
        if artifact.version != reference.version:
            problems.append(f"{artifact.name} has version {artifact.version!r}, the sdist {reference.version!r}")
        if set(artifact.license_files) != set(reference.license_files):
            problems.append(
                f"{artifact.name} records License-File {sorted(artifact.license_files)}, "
                f"the sdist {sorted(reference.license_files)}"
            )
        problems += _license_problems(artifact, declared, root)
    return problems


def _license_problems(artifact: Artifact, declared: list[str], root: Path) -> list[str]:
    """Check one artifact's licence files against its metadata and the project.

    Args:
        artifact: The distribution.
        declared: The project's declared licence files.
        root: The project root.

    Returns:
        A message for each declared file the metadata does not record, each recorded file the
        archive does not hold, and each declared file whose packaged copy differs from the
        project's.
    """
    problems = [
        f"{artifact.name} does not record declared licence file {p!r} as a License-File"
        for p in declared
        if p not in artifact.license_files
    ]
    problems += [
        f"{artifact.name} records License-File {p!r} but does not contain it"
        for p in artifact.license_files
        if p not in artifact.contents
    ]
    problems += [
        f"{artifact.name} contains a copy of {p!r} that differs from the project's"
        for p in declared
        if p in artifact.contents and artifact.contents[p] != (root / p).read_bytes()
    ]
    return problems
