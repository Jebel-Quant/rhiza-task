"""``dist-check``: the archives are real, the build is not.

Like every task test here, nothing runs uv. The stand-in for ``uv build`` writes real
``.tar.gz`` and ``.whl`` archives, so what is under test is the part this task actually owns:
reading metadata and licence files back out of an archive, and judging them. The rebuild
stand-in reads ``PKG-INFO`` out of the directory the task unpacked, which is what a backend
deriving its version from an sdist does -- so a test that breaks the sdist's version breaks
the rebuilt wheel's the same way it would for real.
"""

from __future__ import annotations

import io
import re
import tarfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from rhiza_task.config import Config
from rhiza_task.spec import Failed, Skip
from rhiza_task.tasks import dist as dist_module

MIT = b"MIT License\n"

PYPROJECT = """\
[project]
name = "demo"
version = "0.2.0"
license = "MIT"
license-files = ["LICENSE"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
"""


@dataclass
class Build:
    """What the fake ``uv build`` writes.

    Attributes:
        version: The version both artifacts carry.
        license_files: ``License-File`` entries in both artifacts' metadata.
        sdist_files: Licence files placed in the sdist, by path.
        wheel_files: Licence files placed in the wheel, by path.
        wheel_layout: ``licenses`` for PEP 639's ``.dist-info/licenses/``, ``legacy`` for
            setuptools' older ``.dist-info/<path>``.
        rebuilt_version: Forces the rebuilt wheel's version; ``None`` reads it from the
            unpacked ``PKG-INFO``, as a backend would.
        sdists: How many sdists the main build writes.
        stray: Whether the sdist also holds a second top-level folder.
        calls: Every recorded invocation, as ``(args, kwargs)``.
    """

    version: str = "0.2.0"
    license_files: tuple[str, ...] = ("LICENSE",)
    sdist_files: dict[str, bytes] = field(default_factory=lambda: {"LICENSE": MIT})
    wheel_files: dict[str, bytes] = field(default_factory=lambda: {"LICENSE": MIT})
    wheel_layout: str = "licenses"
    rebuilt_version: str | None = None
    sdists: int = 1
    stray: bool = False
    calls: list[tuple[tuple[str, ...], dict[str, object]]] = field(default_factory=list)

    def __call__(self, *args: str, **kwargs: object) -> int:
        """Record the call and write the archives it asks for.

        Args:
            *args: The uv arguments.
            **kwargs: ``cwd``, ``env`` and ``unset``.

        Returns:
            0, as a successful build would.
        """
        self.calls.append((args, kwargs))
        out = Path(args[args.index("--out-dir") + 1])
        out.mkdir(parents=True, exist_ok=True)
        if "--wheel" in args:
            version = self.rebuilt_version or _pkg_info_version(Path(args[-1]))
            write_wheel(out, version, self.license_files, self.wheel_files, self.wheel_layout)
        else:
            for n in range(self.sdists):
                write_sdist(out, self.version, self.license_files, self.sdist_files, str(n or ""), self.stray)
            write_wheel(out, self.version, self.license_files, self.wheel_files, self.wheel_layout)
        return 0


def _metadata(version: str, license_files: tuple[str, ...]) -> bytes:
    """Render core metadata headers.

    Args:
        version: The ``Version`` header.
        license_files: One ``License-File`` header each.

    Returns:
        The encoded headers, followed by a body as real metadata has.
    """
    lines = ["Metadata-Version: 2.4", "Name: demo", f"Version: {version}"]
    lines += [f"License-File: {p}" for p in license_files]
    return ("\n".join(lines) + "\n\nThe long description.\n").encode()


def _pkg_info_version(source: Path) -> str:
    """Read ``Version`` from an unpacked sdist, as a backend building from it would.

    Args:
        source: The unpacked sdist's top-level folder.

    Returns:
        The version it carries.
    """
    for line in (source / "PKG-INFO").read_text().splitlines():
        if line.startswith("Version: "):
            return line.removeprefix("Version: ")
    return "0.0.0"


def write_sdist(
    out: Path,
    version: str,
    license_files: tuple[str, ...],
    files: dict[str, bytes],
    suffix: str = "",
    stray: bool = False,
) -> Path:
    """Write an sdist holding ``PKG-INFO`` and the given files.

    Args:
        out: The folder to write into.
        version: Its version.
        license_files: Its ``License-File`` entries.
        files: Extra files, by path below the top-level folder.
        suffix: Appended to the filename, to write more than one.
        stray: Add a second top-level folder, which no real sdist has.

    Returns:
        The archive's path.
    """
    path = out / f"demo-{version}{suffix}.tar.gz"
    top = f"demo-{version}"
    with tarfile.open(path, "w:gz") as archive:
        for name, data in {"PKG-INFO": _metadata(version, license_files), **files}.items():
            info = tarfile.TarInfo(f"{top}/{name}")
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
        if stray:
            archive.addfile(tarfile.TarInfo("stray/file"), io.BytesIO(b""))
    return path


def write_wheel(
    out: Path, version: str, license_files: tuple[str, ...], files: dict[str, bytes], layout: str = "licenses"
) -> Path:
    """Write a wheel holding ``METADATA`` and the given licence files.

    Args:
        out: The folder to write into.
        version: Its version.
        license_files: Its ``License-File`` entries.
        files: Licence files, by path.
        layout: ``licenses`` (PEP 639) or ``legacy``.

    Returns:
        The archive's path.
    """
    path = out / f"demo-{version}-py3-none-any.whl"
    info = f"demo-{version}.dist-info"
    prefix = f"{info}/licenses" if layout == "licenses" else info
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(f"{info}/METADATA", _metadata(version, license_files))
        for name, data in files.items():
            archive.writestr(f"{prefix}/{name}", data)
    return path


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """A buildable project declaring one licence file.

    Args:
        tmp_path: pytest's temporary directory.

    Returns:
        The project root.
    """
    (tmp_path / "pyproject.toml").write_text(PYPROJECT)
    (tmp_path / "LICENSE").write_bytes(MIT)
    return tmp_path


def run(project: Path, build: Build, monkeypatch: pytest.MonkeyPatch) -> None:
    """Run the task body against the project with the fake build patched in.

    Args:
        project: The project root.
        build: The fake ``uv build``.
        monkeypatch: pytest's patcher.
    """
    monkeypatch.setattr(dist_module, "uv", build)
    dist_module.dist_check(Config.load(root=project))


def failure(project: Path, build: Build, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> str:
    """Run the task, require it to fail, and return what it printed.

    Args:
        project: The project root.
        build: The fake ``uv build``.
        monkeypatch: pytest's patcher.
        capsys: pytest's output capture.

    Returns:
        Captured stdout.
    """
    with pytest.raises(Failed):
        run(project, build, monkeypatch)
    return capsys.readouterr().out


def test_a_consistent_build_passes_and_stays_in_dist(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The happy path: both artifacts and the rebuild agree, and dist/ keeps what passed.

    Args:
        project: The project root.
        monkeypatch: pytest's patcher.
    """
    run(project, Build(), monkeypatch)
    assert sorted(p.name for p in (project / "dist").iterdir()) == [
        "demo-0.2.0-py3-none-any.whl",
        "demo-0.2.0.tar.gz",
    ]


def test_the_vectors(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Build into dist/, then rebuild the wheel outside git with every override removed.

    Args:
        project: The project root.
        monkeypatch: pytest's patcher.
    """
    monkeypatch.setenv("SETUPTOOLS_SCM_PRETEND_VERSION", "9.9.9")
    monkeypatch.setenv("SETUPTOOLS_SCM_PRETEND_VERSION_FOR_DEMO", "9.9.9")
    build = Build()
    run(project, build, monkeypatch)

    (first, first_kwargs), (second, second_kwargs) = build.calls
    assert first == ("build", "--out-dir", str(project / "dist"))
    assert first_kwargs["cwd"] == project

    tmp = second_kwargs["cwd"]
    assert isinstance(tmp, Path)
    assert second[:4] == ("build", "--wheel", "--out-dir", str(tmp / "out"))
    assert Path(second[4]) == tmp / "source" / "demo-0.2.0"
    assert second_kwargs["env"] == {"GIT_CEILING_DIRECTORIES": str(tmp)}
    unset = second_kwargs["unset"]
    assert isinstance(unset, list)
    assert {"SETUPTOOLS_SCM_PRETEND_VERSION", "SETUPTOOLS_SCM_PRETEND_VERSION_FOR_DEMO"} <= set(unset)
    assert not tmp.exists(), "the rebuild's temporary directory must be removed"


def test_a_declared_file_the_project_does_not_have_fails(
    project: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The chebfun 0.11.0 case: ``LICENSE`` declared, ``LICENSE.rst`` on disk, metadata empty.

    Args:
        project: The project root.
        monkeypatch: pytest's patcher.
        capsys: pytest's output capture.
    """
    (project / "LICENSE").rename(project / "LICENSE.rst")
    out = failure(project, Build(license_files=(), sdist_files={}, wheel_files={}), monkeypatch, capsys)
    assert "declares licence file 'LICENSE', which matches no file in the project" in out


def test_a_declared_file_missing_from_the_metadata_fails(
    project: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The file exists and is declared, but no artifact records it.

    Args:
        project: The project root.
        monkeypatch: pytest's patcher.
        capsys: pytest's output capture.
    """
    out = failure(project, Build(license_files=()), monkeypatch, capsys)
    assert "demo-0.2.0.tar.gz does not record declared licence file 'LICENSE'" in out
    assert "demo-0.2.0-py3-none-any.whl does not record declared licence file 'LICENSE'" in out


def test_a_recorded_file_missing_from_the_archive_fails(
    project: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Metadata naming a file the wheel does not hold.

    Args:
        project: The project root.
        monkeypatch: pytest's patcher.
        capsys: pytest's output capture.
    """
    out = failure(project, Build(wheel_files={}), monkeypatch, capsys)
    assert "demo-0.2.0-py3-none-any.whl records License-File 'LICENSE' but does not contain it" in out


def test_a_packaged_copy_that_differs_fails(
    project: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The archive holds a licence file whose contents are not the project's.

    Args:
        project: The project root.
        monkeypatch: pytest's patcher.
        capsys: pytest's output capture.
    """
    out = failure(project, Build(sdist_files={"LICENSE": b"Proprietary\n"}), monkeypatch, capsys)
    assert "demo-0.2.0.tar.gz contains a copy of 'LICENSE' that differs from the project's" in out


def test_a_rebuild_at_another_version_fails(
    project: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A version that only existed in the checkout: the rebuild from the sdist falls back.

    Args:
        project: The project root.
        monkeypatch: pytest's patcher.
        capsys: pytest's output capture.
    """
    out = failure(project, Build(rebuilt_version="0.1.dev1"), monkeypatch, capsys)
    assert "(rebuilt from the sdist) has version '0.1.dev1', the sdist '0.2.0'" in out


def test_artifacts_recording_different_licences_fail(
    project: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The wheel and the sdist disagree about which licence files exist.

    Args:
        project: The project root.
        monkeypatch: pytest's patcher.
        capsys: pytest's output capture.
    """
    build = Build()
    original = write_wheel

    def wheel_with_notice(out: Path, version: str, license_files: tuple[str, ...], *args: object) -> Path:
        """Write the wheel with an extra ``NOTICE`` entry.

        Args:
            out: The folder to write into.
            version: Its version.
            license_files: Its entries, before the extra one.
            *args: The remaining arguments, passed through.

        Returns:
            The archive's path.
        """
        return original(out, version, (*license_files, "NOTICE"), {"LICENSE": MIT, "NOTICE": b"n\n"})

    monkeypatch.setattr(f"{__name__}.write_wheel", wheel_with_notice)
    out = failure(project, build, monkeypatch, capsys)
    assert "records License-File ['LICENSE', 'NOTICE'], the sdist ['LICENSE']" in out


def test_the_legacy_license_table_and_layout_are_accepted(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``license = {file = ...}`` and setuptools' pre-PEP 639 ``.dist-info/<path>``.

    Args:
        project: The project root.
        monkeypatch: pytest's patcher.
    """
    (project / "pyproject.toml").write_text(
        PYPROJECT.replace('license = "MIT"\nlicense-files = ["LICENSE"]\n', 'license = {file = "LICENSE"}\n')
    )
    run(project, Build(wheel_layout="legacy"), monkeypatch)


def test_a_glob_ignores_the_virtualenv_and_earlier_builds(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A recursive pattern counts the project's own files, not a dependency's in ``.venv``.

    Args:
        project: The project root.
        monkeypatch: pytest's patcher.
    """
    (project / "pyproject.toml").write_text(PYPROJECT.replace('["LICENSE"]', '["**/LICENSE"]'))
    for folder in (".venv/lib/dep", "build/lib"):
        (project / folder).mkdir(parents=True)
        (project / folder / "LICENSE").write_bytes(b"someone else's\n")
    assert dist_module.declared_license_files(project) == (["LICENSE"], [])
    run(project, Build(), monkeypatch)


def test_earlier_builds_are_removed_first(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A stale artifact would be validated, and then published, alongside the new one.

    Args:
        project: The project root.
        monkeypatch: pytest's patcher.
    """
    dist = project / "dist"
    dist.mkdir()
    write_sdist(dist, "0.1.0", ("LICENSE",), {"LICENSE": MIT})
    write_wheel(dist, "0.1.0", ("LICENSE",), {"LICENSE": MIT})
    (dist / "keep.txt").write_text("not a distribution")
    run(project, Build(), monkeypatch)
    assert sorted(p.name for p in dist.iterdir()) == ["demo-0.2.0-py3-none-any.whl", "demo-0.2.0.tar.gz", "keep.txt"]


def test_an_unexpected_set_of_artifacts_fails(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Two sdists cannot both be the one that is published.

    Args:
        project: The project root.
        monkeypatch: pytest's patcher.
    """
    with pytest.raises(Failed, match="expected one sdist and at least one wheel in dist/, found 2 and 1"):
        run(project, Build(sdists=2), monkeypatch)


def test_a_project_without_a_build_system_is_skipped(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A uv virtual project builds nothing, so there is nothing to check -- and no build runs.

    Args:
        project: The project root.
        monkeypatch: pytest's patcher.
    """
    (project / "pyproject.toml").write_text('[project]\nname = "demo"\nversion = "0.2.0"\n')
    build = Build()
    with pytest.raises(Skip, match="no \\[build-system\\]"):
        run(project, build, monkeypatch)
    assert build.calls == []


def test_an_sdist_without_pkg_info_fails(tmp_path: Path) -> None:
    """An archive that is not an sdist at all.

    Args:
        tmp_path: pytest's temporary directory.
    """
    path = tmp_path / "demo-0.2.0.tar.gz"
    with tarfile.open(path, "w:gz") as archive:
        info = tarfile.TarInfo("demo-0.2.0/setup.py")
        archive.addfile(info, io.BytesIO(b""))
    with pytest.raises(Failed, match=re.escape("has no demo-0.2.0/PKG-INFO")):
        dist_module.read_sdist(path, [])


def test_a_wheel_without_one_metadata_file_fails(tmp_path: Path) -> None:
    """A wheel must hold exactly one ``.dist-info/METADATA``.

    Args:
        tmp_path: pytest's temporary directory.
    """
    path = tmp_path / "demo-0.2.0-py3-none-any.whl"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("demo/__init__.py", "")
    with pytest.raises(Failed, match=re.escape("holds 0 .dist-info/METADATA files")):
        dist_module.read_wheel(path, [])


def test_a_rebuild_that_yields_no_wheel_fails(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The rebuild reporting success while writing nothing is a failure, not a pass.

    Args:
        project: The project root.
        monkeypatch: pytest's patcher.
    """
    build = Build()

    def silent_rebuild(*args: str, **kwargs: object) -> int:
        """Write the main build's archives, and nothing for the rebuild.

        Args:
            *args: The uv arguments.
            **kwargs: The keyword arguments.

        Returns:
            0.
        """
        return 0 if "--wheel" in args else build(*args, **kwargs)

    monkeypatch.setattr(dist_module, "uv", silent_rebuild)
    with pytest.raises(Failed, match="produced 0 wheels, not one"):
        dist_module.dist_check(Config.load(root=project))


def test_an_sdist_with_two_top_level_folders_fails(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The rebuild needs exactly one source folder to build from.

    Args:
        project: The project root.
        monkeypatch: pytest's patcher.
    """
    with pytest.raises(Failed, match="unpacks to 2 top-level folders, not one"):
        run(project, Build(stray=True), monkeypatch)
