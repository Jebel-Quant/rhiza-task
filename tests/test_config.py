"""The six-layer resolution order, and the three value shapes ``.rhiza/.env`` uses."""

from __future__ import annotations

from pathlib import Path

import pytest

from rhiza_task.config import Config, _coerce, _key


def test_defaults_need_no_files(tmp_path: Path) -> None:
    """A repository with no config at all still resolves.

    Args:
        tmp_path: An empty directory.
    """
    cfg = Config.load(root=tmp_path)
    assert cfg.source_folder == "src"
    assert cfg.typechecker == "ty"
    assert cfg.coverage_fail_under == 90


def test_env_file_beats_defaults(tmp_path: Path) -> None:
    """``.rhiza/.env`` overrides the dataclass defaults.

    Args:
        tmp_path: The repository root.
    """
    (tmp_path / ".rhiza").mkdir()
    (tmp_path / ".rhiza" / ".env").write_text(
        'SOURCE_FOLDER=lib\nTYPECHECKER=mypy\nRHIZA_CI_OS_MATRIX=["ubuntu-latest","macos-latest"]\n'
    )
    cfg = Config.load(root=tmp_path)
    assert cfg.source_folder == "lib"
    assert cfg.typechecker == "mypy"
    assert cfg.ci_os_matrix == ("ubuntu-latest", "macos-latest")


def test_pyproject_beats_env_file(tmp_path: Path) -> None:
    """``[tool.rhiza-task]`` outranks ``.rhiza/.env``.

    This is the layer that replaces shadowing a template-owned make target, so it has to
    win against the file the template ships.

    Args:
        tmp_path: The repository root.
    """
    (tmp_path / ".rhiza").mkdir()
    (tmp_path / ".rhiza" / ".env").write_text("SOURCE_FOLDER=lib\n")
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "d"\nversion = "0"\n\n[tool.rhiza-task]\nsource_folder = "app"\ncoverage_fail_under = 75\n'
    )
    cfg = Config.load(root=tmp_path)
    assert cfg.source_folder == "app"
    assert cfg.coverage_fail_under == 75


def test_rhiza_toml_beats_the_env_file(tmp_path: Path) -> None:
    """``rhiza.toml`` outranks ``.rhiza/.env``.

    The env file is now developer-local -- rhiza ships no ``.rhiza/.gitignore`` to keep it
    tracked -- so the committed neutral file has to win over a machine-local leftover.

    Args:
        tmp_path: The repository root.
    """
    (tmp_path / ".rhiza").mkdir()
    (tmp_path / ".rhiza" / ".env").write_text("SOURCE_FOLDER=lib\nTYPECHECKER=mypy\n")
    (tmp_path / "rhiza.toml").write_text('source_folder = "app"\ncoverage_fail_under = 75\n')
    cfg = Config.load(root=tmp_path)
    assert cfg.source_folder == "app"
    assert cfg.coverage_fail_under == 75
    assert cfg.typechecker == "mypy"


def test_rhiza_toml_serves_a_repo_with_no_manifest(tmp_path: Path) -> None:
    """A Go module has no manifest to hide a table in, and needs no special case.

    This is the layer's reason to exist: ``go.mod`` is not TOML and holds nothing a tool
    can namespace into, so before this the only committed settings surface a Go repo had
    was the root Makefile -- the very file the shim migration removes.

    Args:
        tmp_path: The repository root.
    """
    (tmp_path / "go.mod").write_text("module example.com/demo\n\ngo 1.23\n")
    (tmp_path / "rhiza.toml").write_text('source_folder = "cmd"\nrhiza_checks = ["a", "b"]\n')
    cfg = Config.load(root=tmp_path)
    assert cfg.source_folder == "cmd"
    assert cfg.rhiza_checks == ("a", "b")


def test_rhiza_toml_honours_the_pyproject_spelling(tmp_path: Path) -> None:
    """A ``[tool.rhiza-task]`` table in ``rhiza.toml`` is read, and wins over the top level.

    Copying the table across from pyproject is the obvious first thing to try, and silently
    ignoring it would be the worst of the three possible behaviours.

    Args:
        tmp_path: The repository root.
    """
    (tmp_path / "rhiza.toml").write_text('source_folder = "ignored"\n\n[tool.rhiza-task]\nsource_folder = "app"\n')
    assert Config.load(root=tmp_path).source_folder == "app"


def test_cargo_toml_carries_the_table_too(tmp_path: Path) -> None:
    """``[tool.rhiza-task]`` in ``Cargo.toml`` is read, and outranks ``rhiza.toml``.

    Cargo ignores unknown top-level tables, so the table is as harmless there as the same
    table is in pyproject.

    Args:
        tmp_path: The repository root.
    """
    (tmp_path / "rhiza.toml").write_text('source_folder = "neutral"\ntests_folder = "t"\n')
    (tmp_path / "Cargo.toml").write_text(
        '[package]\nname = "demo"\nversion = "0.1.0"\n\n[tool.rhiza-task]\nsource_folder = "crate-src"\n'
    )
    cfg = Config.load(root=tmp_path)
    assert cfg.source_folder == "crate-src"
    assert cfg.tests_folder == "t"


def test_pyproject_beats_cargo_and_the_neutral_file(tmp_path: Path) -> None:
    """In a repo carrying both manifests, pyproject is the last word before the environment.

    Fixed rather than incidental: a Rust crate that grows a Python binding package should
    resolve to the settings the Python-only repo already had.

    Args:
        tmp_path: The repository root.
    """
    (tmp_path / "rhiza.toml").write_text('source_folder = "neutral"\n')
    (tmp_path / "Cargo.toml").write_text('[tool.rhiza-task]\nsource_folder = "crate-src"\n')
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "d"\nversion = "0"\n\n[tool.rhiza-task]\nsource_folder = "app"\n'
    )
    assert Config.load(root=tmp_path).source_folder == "app"


def test_unparseable_settings_file_names_itself(tmp_path: Path) -> None:
    """A settings file that is not valid TOML is reported, not skipped.

    An absent file means "nothing to say"; a broken one means a setting the author believes
    is in effect is not, which is exactly the failure the layered resolution exists to make
    impossible to have silently.

    Args:
        tmp_path: The repository root.
    """
    (tmp_path / "rhiza.toml").write_text("source_folder = \n")
    with pytest.raises(ValueError, match=r"rhiza\.toml is not valid TOML"):
        Config.load(root=tmp_path)


def test_environ_beats_pyproject(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An environment variable outranks the pyproject table.

    Args:
        tmp_path: The repository root.
        monkeypatch: pytest's patcher.
    """
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "d"\nversion = "0"\n\n[tool.rhiza-task]\nsource_folder = "app"\n'
    )
    monkeypatch.setenv("SOURCE_FOLDER", "from-env")
    assert Config.load(root=tmp_path).source_folder == "from-env"


def test_empty_environ_value_is_not_an_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An exported empty string leaves the lower layer alone.

    rhiza_ci.yml's ``generate-matrix`` job exports ``RHIZA_CI_OS_MATRIX`` unconditionally
    and sets it to ``''`` for every repository that is not the template's own, expecting
    the consumer's ``.rhiza/.env`` to answer instead. Honouring that empty string would
    hand GitHub a zero-OS matrix and silently delete the test job.

    Args:
        tmp_path: The repository root.
        monkeypatch: pytest's patcher.
    """
    (tmp_path / ".rhiza").mkdir()
    (tmp_path / ".rhiza" / ".env").write_text('RHIZA_CI_OS_MATRIX=["ubuntu-latest","macos-latest"]\n')
    monkeypatch.setenv("RHIZA_CI_OS_MATRIX", "")
    assert Config.load(root=tmp_path).ci_os_matrix == ("ubuntu-latest", "macos-latest")

    monkeypatch.setenv("RHIZA_CI_OS_MATRIX", "   ")
    assert Config.load(root=tmp_path).ci_os_matrix == ("ubuntu-latest", "macos-latest")


def test_empty_env_file_value_is_not_an_override(tmp_path: Path) -> None:
    """An empty assignment in ``.rhiza/.env`` does not shadow the default either.

    Args:
        tmp_path: The repository root.
    """
    (tmp_path / ".rhiza").mkdir()
    (tmp_path / ".rhiza" / ".env").write_text("SOURCE_FOLDER=\nTYPECHECKER=mypy\n")
    cfg = Config.load(root=tmp_path)
    assert cfg.source_folder == "src"
    assert cfg.typechecker == "mypy"


def test_overrides_beat_everything(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A command-line override is the last word, and ``None`` is not an override.

    Args:
        tmp_path: The repository root.
        monkeypatch: pytest's patcher.
    """
    monkeypatch.setenv("SOURCE_FOLDER", "from-env")
    assert Config.load(root=tmp_path, source_folder="flag").source_folder == "flag"
    assert Config.load(root=tmp_path, source_folder=None).source_folder == "from-env"


def test_python_version_file_wins(tmp_path: Path) -> None:
    """``.python-version`` outranks a configured ``python_version``.

    It is what uv itself honours, so a second source of truth could only ever disagree.

    Args:
        tmp_path: The repository root.
    """
    (tmp_path / ".python-version").write_text("3.12\n")
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "d"\nversion = "0"\n\n[tool.rhiza-task]\npython_version = "3.9"\n'
    )
    assert Config.load(root=tmp_path).python_version == "3.12"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ('["ubuntu-latest","macos-latest"]', ("ubuntu-latest", "macos-latest")),
        ("GPL;LGPL;AGPL", ("GPL", "LGPL", "AGPL")),
        ("90", 90),
        ("true", True),
        ("src", "src"),
    ],
)
def test_coerce_handles_every_env_shape(raw: str, expected: object) -> None:
    """The three shapes that appear in real ``.rhiza/.env`` files, plus scalars.

    Args:
        raw: The string as it appears in the file.
        expected: The parsed value.
    """
    assert _coerce(raw) == expected


def test_invalid_typechecker_fails_at_load(tmp_path: Path) -> None:
    """A bad ``typechecker`` fails before any tool is provisioned.

    python.mk validates this in the fourth branch of a shell ``case``, i.e. after the venv
    is built and the gate has begun. Here it cannot get that far.

    Args:
        tmp_path: The repository root.
    """
    with pytest.raises(ValueError, match="typechecker must be"):
        Config.load(root=tmp_path, typechecker="tpye")


def test_invalid_coverage_threshold_fails_at_load(tmp_path: Path) -> None:
    """A coverage threshold outside 0-100 is rejected.

    Args:
        tmp_path: The repository root.
    """
    with pytest.raises(ValueError, match="percentage"):
        Config.load(root=tmp_path, coverage_fail_under=900)


def test_folders_and_path_resolve(tmp_path: Path) -> None:
    """``folders`` exposes exactly the folder fields, and ``path`` makes them absolute.

    Args:
        tmp_path: The repository root.
    """
    cfg = Config.load(root=tmp_path)
    assert set(cfg.folders) == {
        "source_folder",
        "tests_folder",
        "marimo_folder",
        "docker_folder",
        "paper_folder",
    }
    assert cfg.path("source_folder") == tmp_path / "src"


@pytest.mark.parametrize(
    ("var", "field", "raw", "expected"),
    [
        ("UV_SYNC_ARGS", "uv_sync_args", "--group test", ("--group", "test")),
        ("LICENSE_IGNORE_PACKAGES", "license_ignore_packages", "docutils chardet", ("docutils", "chardet")),
        ("DEPTRY_IGNORE", "deptry_ignore", "--ignore DEP004", ("--ignore", "DEP004")),
        ("MKDOCS_EXTRA_PACKAGES", "mkdocs_extra_packages", "mkdocstrings[python]", ("mkdocstrings[python]",)),
        ("RHIZA_CHECKS", "rhiza_checks", "pkg.a  pkg.b", ("pkg.a", "pkg.b")),
        ("LICENSE_FAIL_ON", "license_fail_on", "GPL;LGPL;AGPL", ("GPL", "LGPL", "AGPL")),
        ("RHIZA_CI_OS_MATRIX", "ci_os_matrix", '["ubuntu-latest","macos-latest"]', ("ubuntu-latest", "macos-latest")),
    ],
)
def test_space_separated_list_settings_do_not_splat(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    var: str,
    field: str,
    raw: str,
    expected: tuple[str, ...],
) -> None:
    """A ``tuple[str, ...]`` field never holds a ``str``, whatever shape the layer used.

    ``_coerce`` recognises only a JSON array and a ``;``-separated list, so every other
    string used to reach the field as a ``str`` and be splatted one character per argument
    at the call site -- ``uv sync - - g r o u p``. See issue #6.

    Args:
        tmp_path: The repository root.
        monkeypatch: To set the environment variable.
        var: The environment variable a consumer exports.
        field: The config field under test.
        raw: The value as a consumer writes it.
        expected: The tuple the field must hold.
    """
    monkeypatch.setenv(var, raw)
    assert getattr(Config.load(root=tmp_path), field) == expected


def test_env_file_and_pyproject_strings_are_tupled_too(tmp_path: Path) -> None:
    """Layer 2 and a TOML string in layer 3 get the same normalisation as the environment.

    A ``.rhiza/.env`` written for the retired make layer spelled
    ``LICENSE_IGNORE_PACKAGES`` space-separated, and ``[tool.rhiza-task]`` lets a list
    field be written as a plain string.

    Args:
        tmp_path: The repository root.
    """
    (tmp_path / ".rhiza").mkdir()
    (tmp_path / ".rhiza" / ".env").write_text("LICENSE_IGNORE_PACKAGES=docutils chardet\n")
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "d"\nversion = "0"\n\n[tool.rhiza-task]\nuv_sync_args = "--group test"\n'
    )
    cfg = Config.load(root=tmp_path)
    assert cfg.license_ignore_packages == ("docutils", "chardet")
    assert cfg.uv_sync_args == ("--group", "test")


@pytest.mark.parametrize(
    ("var", "expected"),
    [
        ("RHIZA_CHECKS", "rhiza_checks"),
        ("RHIZA_RHIZA_CHECKS", "rhiza_checks"),
        ("rhiza_checks", "rhiza_checks"),
        ("rhiza-checks", "rhiza_checks"),
        ("RHIZA_CI_OS_MATRIX", "ci_os_matrix"),
        ("CI_OS_MATRIX", "ci_os_matrix"),
        ("SOURCE_FOLDER", "source_folder"),
        ("NONSENSE", "nonsense"),
    ],
)
def test_key_strips_the_prefix_only_when_a_field_remains(var: str, expected: str) -> None:
    """``RHIZA_CHECKS`` names the ``rhiza_checks`` field, not the unknown ``checks``.

    The prefix is optional, and stripping it unconditionally meant the one field whose own
    name starts with ``rhiza_`` could not be set from the environment at all -- the value
    resolved to a name no field has and was dropped without a word.

    Args:
        var: The variable name as a consumer spells it.
        expected: The field it must resolve to.
    """
    assert _key(var) == expected


def test_rhiza_checks_is_settable_from_the_env_file(tmp_path: Path) -> None:
    """The short spelling reaches the field through layer 2 as well.

    Args:
        tmp_path: The repository root.
    """
    (tmp_path / ".rhiza").mkdir()
    (tmp_path / ".rhiza" / ".env").write_text("RHIZA_CHECKS=pkg.a pkg.b\n")
    assert Config.load(root=tmp_path).rhiza_checks == ("pkg.a", "pkg.b")


def test_mkdocs_extra_packages_defaults_to_mkdocstrings(tmp_path: Path) -> None:
    """The default must name mkdocstrings, with no repo configuration at all.

    rhiza's ``book`` bundle enables the plugin for every consumer and has no surface on which
    to install it -- core ships no ``.rhiza/.env``, and a ``book`` + ``go-core`` repo has no
    manifest this reader accepts. This default is the only layer that reaches all of them.

    Args:
        tmp_path: An empty repository root.
    """
    cfg = Config.load(root=tmp_path)
    assert any("mkdocstrings" in spec for spec in cfg.mkdocs_extra_packages), (
        f"mkdocs_extra_packages must default to mkdocstrings; got {cfg.mkdocs_extra_packages!r}"
    )


def test_an_empty_toml_array_overrides_the_default_away(tmp_path: Path) -> None:
    """``mkdocs-extra-packages = []`` must win over the non-empty default.

    TOML is the only layer that can express "none": :func:`_from_env_file` and
    :func:`_from_environ` drop empties to mirror make's ``?=``, which the CI matrix relies
    on. Without this, the default could not be opted out of anywhere.

    Args:
        tmp_path: The repository root.
    """
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "demo"\nversion = "0.0.0"\n\n[tool.rhiza-task]\nmkdocs-extra-packages = []\n'
    )
    assert Config.load(root=tmp_path).mkdocs_extra_packages == ()


def test_an_empty_env_value_leaves_the_default_alone(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An empty environment value must not be read as "no packages".

    The documented consequence of the empty-is-unset rule, pinned so the amended
    :func:`_from_env_file` docstring stays true: the manifest is the escape hatch, the
    environment is not.

    Args:
        tmp_path: The repository root.
        monkeypatch: To set the environment variable.
    """
    monkeypatch.setenv("RHIZA_MKDOCS_EXTRA_PACKAGES", "")
    assert Config.load(root=tmp_path).mkdocs_extra_packages != ()
