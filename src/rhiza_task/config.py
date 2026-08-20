"""Configuration, and the resolution order that replaces make's ``?=`` and ``+=``.

The make layer builds its settings from three overlapping mechanisms: ``?=`` defaults in
the fragment that owns a setting, ``+=`` accumulation from other fragments
(``DEPTRY_FOLDERS``, ``LICENSE_IGNORE_PACKAGES``, ``RHIZA_CHECKS``), and a repo-owned
Makefile or ``local.mk`` assigning over the top. The precedence is a consequence of
include order, which is why rhiza.mk has to explain that ``-include .rhiza/make.d/*.mk``
comes last and ``-include local.mk`` last of all.

Here the order is explicit and testable, lowest precedence first:

1. The dataclass defaults below.
2. ``.rhiza/.env`` -- kept unchanged, because it is already the file consumers edit and
   the reusable workflows read it too. Now a developer-local channel rather than a
   committed one: rhiza no longer ships ``.rhiza/.gitignore``, whose entire content was
   the ``!.env`` negation that kept this file tracked, so it falls under the shipped
   ``.gitignore``'s ``.env`` rule and a CI checkout never contains it.
3. ``rhiza.toml`` -- the language-neutral settings file, and the only committed one a Go
   module can have: it has no manifest to hide a table in. Read for every project, so a
   polyglot repository has one place to look rather than one per layer.
4. ``[tool.rhiza-task]`` in the language manifest -- ``Cargo.toml``, then
   ``pyproject.toml``. This is the new home for what used to require editing a synced
   ``.mk`` file or shadowing a target. Cargo ignores unknown top-level tables, so the
   table is as harmless there as it is in pyproject.
5. ``RHIZA_*`` (or bare make-style) environment variables.
6. Command-line flags.

Layers 3 and 4 are two files rather than one because neither alone covers the three
language layers: pyproject is Python-only, and a repo that already moved its settings
there should not have to move them again. ``rhiza.toml`` ranks *below* the manifest so
that adding it to a Python repo cannot silently outrank the table already there.

The ``+=`` accumulators do not survive as a mechanism, and do not need to: every one of
them was a bundle contributing something it owned, which the task body can now *derive*
by asking whether the contributing task is registered. See ``tasks/python.py``'s ``deps``
and ``license``.
"""

from __future__ import annotations

import json
import os
import tomllib
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

from dotenv import dotenv_values

TYPECHECKERS = ("ty", "mypy", "both")

LAYERS = ("python", "rust", "go")
"""The language layers, in the order :func:`~rhiza_task.spec.lookup` tries them.

Python first because it is the layer a polyglot repository is most likely to have grown
*into* -- a crate or a module that acquires a pyproject has acquired a Python package, and
the gates that package needs are the ones that would otherwise stop running.
"""

LAYER_MANIFESTS = {"python": "pyproject.toml", "rust": "Cargo.toml", "go": "go.mod"}
"""What makes a repository a member of a layer.

The make layer answered this at sync time -- exactly one of python.mk, rust.mk and go.mk
was ever synced into a repo, and `rhiza.mk`'s ``-include`` did the rest. A pinned CLI
carries all three, so the question moves to runtime, and the manifest is the honest
answer: it is what the toolchain itself looks for.
"""

NEUTRAL_RHIZA_CHECKS = (
    "pytest_rhiza.checks.test_readme",
    "pytest_rhiza.checks.test_release_tags",
    "pytest_rhiza.checks.test_readme_validation",
)
"""The checks every repository gets, whatever it is written in."""

LAYER_RHIZA_CHECKS = {
    "python": ("pytest_rhiza.checks.test_pyproject", "pytest_rhiza.checks.test_docstrings"),
    "rust": ("pytest_rhiza.checks.test_cargo_toml",),
    "go": ("pytest_rhiza.checks.test_go_module",),
}
"""What each layer contributes, enumerated rather than globbed.

pytest-rhiza ships all three layers' modules in one distribution, so
``--pyargs pytest_rhiza.checks`` would collect checks that cannot pass -- ``test_go_module``
against a Python project asserts a ``go.mod`` that is not there. In the make layer each
language fragment appended its own with ``RHIZA_CHECKS +=``; here the accumulator is
replaced by the same derivation the ``+=`` was standing in for, from the layer set rather
than from include order.
"""

DEFAULT_RHIZA_CHECKS = NEUTRAL_RHIZA_CHECKS + LAYER_RHIZA_CHECKS["python"]
"""The Python resolution, kept as a name because it is the set consumers know.

This is jointview's ``RHIZA_CHECKS`` list, promoted from a shadowed make variable to a
default: the 60-line override in its Makefile exists only because the make layer had
nowhere else to put it.
"""


def rhiza_checks_for(layers: Sequence[str]) -> tuple[str, ...]:
    """Return the check set for a repository's layers.

    Args:
        layers: The active layers.

    Returns:
        The neutral checks followed by each layer's own, in layer order, deduplicated.
    """
    checks = list(NEUTRAL_RHIZA_CHECKS)
    for layer in layers:
        checks += [c for c in LAYER_RHIZA_CHECKS.get(layer, ()) if c not in checks]
    return tuple(checks)


def detect_layers(root: Path) -> tuple[str, ...]:
    """Return the language layers a repository belongs to, by its manifests.

    Args:
        root: Repository root.

    Returns:
        The layers whose manifest is present, in :data:`LAYERS` order; ``("python",)``
        when a repository has none, because that is what every gate assumed before there
        was a choice, and a repo with no manifest at all has nothing for another layer's
        gates to measure either.
    """
    found = tuple(layer for layer in LAYERS if (root / LAYER_MANIFESTS[layer]).is_file())
    return found or ("python",)


DEFAULT_CI_OS_MATRIX = ("ubuntu-latest",)
"""The OS every consumer gets unless it asks for more.

Named rather than inlined because two callers need the same value: the field default
below, and the floor in ``rhiza-task ci-os-matrix`` that stops an explicitly empty
setting reaching GitHub as a zero-job matrix.
"""


@dataclass
class Config:
    """Resolved settings for one repository.

    Field names are the lowercased make variables, so the mapping to what a consumer
    already knows stays one-to-one and greppable.
    """

    source_folder: str = "src"
    tests_folder: str = "tests"
    marimo_folder: str = "docs/notebooks"
    book_output: str = "_book"
    python_version: str = "3.13"

    coverage_fail_under: int = 90

    # ty | mypy | both. python.mk documents that ``both`` masks ty's exit status behind
    # mypy's, and jointview sets ``ty`` in .rhiza/.env for that reason. The shell ``case``
    # whose fourth branch validated this is replaced by __post_init__, so a typo now fails
    # before any tool is provisioned rather than after.
    typechecker: str = "ty"

    # Matched as substrings -- see ``--partial-match`` in the ``license`` task.
    license_fail_on: tuple[str, ...] = ("GPL", "LGPL", "AGPL")
    license_ignore_packages: tuple[str, ...] = ()

    deptry_ignore: tuple[str, ...] = ()

    # rust.mk's CARGO_FLAGS and go.mk's GO_FLAGS / GO_TEST_FLAGS. `-race` and `-shuffle=on`
    # are go.mk's default: the Go idiom for a CI run, and the flag that catches tests
    # depending on declaration order.
    cargo_flags: tuple[str, ...] = ()
    go_flags: tuple[str, ...] = ()
    go_test_flags: tuple[str, ...] = ("-race", "-shuffle=on")
    # Non-empty on purpose, and it pairs with ``zensical_version`` below: this setting is
    # the other half of "what the book gate provisions", and only one half was ever set.
    #
    # rhiza's ``book`` bundle ships ``docs/mkdocs-base.yml`` with ``mkdocstrings`` enabled
    # unconditionally, and every consumer inherits it via ``INHERIT``. With this empty,
    # ``book`` invoked ``uvx zensical build`` with no ``--with``, and zensical refused:
    #
    #     Error: mkdocstrings plugin is enabled, but mkdocstrings is not installed.
    #
    # So the bundle shipped a config that could not build with its own default. The default
    # belongs here rather than in the bundle because a bundle has nowhere to put it: core
    # ships no ``.rhiza/.env`` (jebel-quant/rhiza#1545 deleted it), ``pyproject.toml`` is
    # repo-owned, and ``_from_manifest`` reads only pyproject/Cargo -- so a ``book`` +
    # ``go-core`` repo has no TOML surface at all. This is the one layer every consumer has.
    #
    # A repo that wants no plugins sets ``mkdocs-extra-packages = []`` in its manifest.
    # That is TOML-only, deliberately -- see :func:`_from_env_file`.
    mkdocs_extra_packages: tuple[str, ...] = ("mkdocstrings[python]",)

    # The five bundle-owned fragments' settings. docker.mk's DOCKER_FOLDER was a `:=`
    # rather than a `?=` -- not configurable at all -- and paper.mk hard-coded the
    # PRESENTATION.md equivalent; both are ordinary settings here, because there is no
    # longer a cost to making one.
    docker_folder: str = "docker"
    # Empty rather than a computed default: docker.mk's `?= $(shell basename $(CURDIR))`
    # cannot be spelled as a dataclass default, and resolving it in the task body keeps
    # `rhiza-task print docker_image` honest about the setting being unset.
    docker_image: str = ""
    paper_folder: str = "docs/paper"
    presentation_file: str = "PRESENTATION.md"
    # Unpinned, because `npm install -g @marp-team/marp-cli` was too: presentation.mk
    # installed whatever latest resolved to. Set it to `@marp-team/marp-cli@4.2.3` to pin.
    marp_package: str = "@marp-team/marp-cli"
    zensical_version: str = ">=0.0.36"
    uv_sync_args: tuple[str, ...] = ("--all-extras", "--all-groups")
    ci_os_matrix: tuple[str, ...] = DEFAULT_CI_OS_MATRIX

    # Pinned to a tag rather than a branch: a gate that moves under you is not a gate.
    pytest_rhiza: str = "pytest-rhiza @ git+https://github.com/Jebel-Quant/pytest-rhiza@v0.2.0"

    # Both are empty by default and filled in __post_init__, because both depend on the
    # repository rather than on a constant: the layers come from the manifests present, and
    # the check set follows from the layers. Setting either explicitly -- in
    # pyproject.toml, or RHIZA_LAYERS=rust -- switches detection off for that field, which
    # is what a repository carrying two manifests and wanting one gate set needs.
    layers: tuple[str, ...] = ()
    rhiza_checks: tuple[str, ...] = ()

    # Turns Skip into failure. The answer to jointview's own complaint about "a green gate
    # measuring nothing": set it in CI and a missing folder is a red build rather than a
    # yellow line nobody reads.
    strict: bool = False

    root: Path = field(default_factory=Path.cwd)

    # radon scores this method C (12): one branch per field that needs normalising or
    # checking -- str and list coercion, layer defaulting and membership, rhiza_checks
    # defaulting, typechecker, coverage_fail_under. It is a flat sequence, one field per
    # step, with no interaction between the steps; the count grows with the number of
    # validated settings and not with the depth of anything. Deliberate.
    #
    # Unlike the other three C blocks here, that growth rule needs a stated ceiling, and
    # the ceiling is **C (15)**. `_run_one` is bounded by the size of `Status` and
    # `Guard.check` by the number of guard kinds -- closed sets, so their figures cannot
    # drift far. "One branch per validated setting" is not a closed set: every future
    # setting adds one, and a justification with no limit is an open licence rather than a
    # decision. At 15 -- roughly three more settings -- the flat form stops paying for
    # itself, and the answer is per-group helpers (`_validate_layers`,
    # `_validate_typechecker`, `_validate_coverage`) called in sequence from here, which
    # keeps the readable one-field-per-step order while bounding each block.
    #
    # `uvx radon cc src -a -s` is the check; nothing gates it, so this is discipline.
    def __post_init__(self) -> None:
        """Normalise the list fields, then validate the enumerated and numeric ones.

        :func:`_coerce` sees a string without knowing which field it is destined for, so
        it can only recognise the two shapes that announce themselves -- a JSON array and
        a ``;``-separated list. Everything else it leaves as a ``str``, and a ``str``
        reaching a ``tuple[str, ...]`` field is splatted one *character* per argument at
        the call site: ``UV_SYNC_ARGS="--group test"`` became ``uv sync - - g r o u p``.

        The field's type is known here and nowhere lower, so this is where a string
        becomes a tuple. Splitting on whitespace is the make layer's own format --
        python.mk documented ``LICENSE_IGNORE_PACKAGES`` as space-separated and
        ``RHIZA_CHECKS`` accumulated space-separated module names -- so a ``.rhiza/.env``
        written for make keeps working, as does the ``UV_SYNC_ARGS`` that rhiza's synced
        ``.devcontainer/bootstrap.sh`` exports.

        Raises:
            ValueError: When ``typechecker`` is not one of ty, mypy, both,
                ``coverage_fail_under`` is outside 0-100, ``layers`` names a layer that
                does not exist, or a ``*_folder`` escapes :attr:`root`.
        """
        for f in fields(self):
            if str(f.type).replace(" ", "") != "tuple[str,...]":
                continue
            value = getattr(self, f.name)
            if isinstance(value, str):
                object.__setattr__(self, f.name, tuple(value.split()))
            elif isinstance(value, list):
                object.__setattr__(self, f.name, tuple(value))

        if not self.layers:
            object.__setattr__(self, "layers", detect_layers(self.root))
        unknown = [layer for layer in self.layers if layer not in LAYERS]
        if unknown:
            msg = f"unknown layer(s) {', '.join(unknown)}; known: {', '.join(LAYERS)}"
            raise ValueError(msg)
        if not self.rhiza_checks:
            object.__setattr__(self, "rhiza_checks", rhiza_checks_for(self.layers))

        if self.typechecker not in TYPECHECKERS:
            msg = f"typechecker must be one of {', '.join(TYPECHECKERS)} (got {self.typechecker!r})"
            raise ValueError(msg)
        if not 0 <= int(self.coverage_fail_under) <= 100:
            msg = f"coverage_fail_under must be a percentage (got {self.coverage_fail_under!r})"
            raise ValueError(msg)

        self._validate_folders()

    def _validate_folders(self) -> None:
        """Reject a ``*_folder`` setting that resolves outside :attr:`root`.

        Not a sandbox: the folder settings arrive from ``.rhiza/.env``, ``rhiza.toml``, the
        manifest and the environment, which sit at the same trust level as the code they
        configure. It is the containment the enumerated fields above already get.
        ``SOURCE_FOLDER=../../elsewhere`` silently points a gate at a different checkout,
        and that is a typo far more often than an intention -- so it should say the field's
        name rather than run and report on somebody else's tree.

        Both sides are resolved because ``root`` itself is routinely a symlink -- macOS
        ``/tmp``, and every :func:`tempfile.mkdtemp` under it -- and comparing a resolved
        child against an unresolved parent would reject every folder in such a checkout.

        Raises:
            ValueError: When a folder setting escapes ``root``.
        """
        root = self.root.resolve()
        for name, value in self.folders.items():
            if not (root / value).resolve().is_relative_to(root):
                msg = f"{name} must stay inside the repository root (got {value!r})"
                raise ValueError(msg)

    @property
    def folders(self) -> dict[str, str]:
        """Return the folder settings, for :meth:`~rhiza_task.spec.Guard.check`.

        Returns:
            Mapping of field name to configured relative path.
        """
        return {f.name: getattr(self, f.name) for f in fields(self) if f.name.endswith("_folder")}

    def path(self, folder_field: str) -> Path:
        """Resolve a folder field to an absolute path.

        No containment check here: :meth:`_validate_folders` did it once at construction,
        so every field this resolves is already known to stay under ``root``.

        Args:
            folder_field: A field name such as ``source_folder``.

        Returns:
            The absolute path.
        """
        # The annotation is load-bearing under `mypy --strict`: `getattr` is typed to return
        # `Any`, `Path / Any` is `Any` too, and returning that from a `-> Path` function is
        # what `no-any-return` reports. Naming the type here is also the honest spelling --
        # every field this reaches ends in `_folder` and holds a `str`, which is the same
        # assumption `folders` above encodes in its `dict[str, str]`.
        value: str = getattr(self, folder_field)
        return self.root / value

    @staticmethod
    def field_for(name: str) -> str:
        """Normalise a make-style variable name to a field name.

        Public because the spelling rule is not private to the layer readers below: the
        ``print`` command has to answer for ``SOURCE_FOLDER`` exactly as ``.rhiza/.env``
        does, and a second normaliser written against the same rule is a second thing to
        keep in step. A caller outside this module asking "which field is this?" is asking
        :class:`Config`, so it is spelled as a question :class:`Config` can be asked.

        The ``RHIZA_`` prefix is optional, so it is stripped -- but only when what remains
        is actually a field. Stripping unconditionally made ``RHIZA_CHECKS`` resolve to
        the unknown field ``checks``, so the setting was silently dropped and
        ``rhiza_checks`` was reachable from the environment only as ``RHIZA_RHIZA_CHECKS``.
        Trying the whole name as a fallback fixes that without disturbing the fields whose
        prefix *is* redundant: ``RHIZA_CI_OS_MATRIX`` still resolves to ``ci_os_matrix``,
        and the doubled spelling keeps working for anyone who found it.

        Args:
            name: e.g. ``RHIZA_CI_OS_MATRIX``, ``SOURCE_FOLDER`` or ``rhiza-checks``.

        Returns:
            e.g. ``ci_os_matrix``, ``source_folder``, ``rhiza_checks``.

        Examples:
            >>> Config.field_for("SOURCE_FOLDER"), Config.field_for("rhiza-checks")
            ('source_folder', 'rhiza_checks')
        """
        lowered = name.lower().replace("-", "_")
        stripped = lowered.removeprefix("rhiza_")
        if stripped in _FIELD_NAMES or lowered not in _FIELD_NAMES:
            return stripped
        return lowered

    @classmethod
    def load(cls, root: Path | None = None, **overrides: Any) -> Config:
        """Build a config by walking the six layers in order.

        Args:
            root: Repository root; defaults to the current directory.
            **overrides: Layer 5, the command-line flags. ``None`` values are ignored so
                an unset flag does not shadow a configured value.

        Returns:
            The resolved config.

        Examples:
            Layer 4 -- ``[tool.rhiza-task]`` in the manifest -- over the dataclass
            defaults, with an unset flag passed as ``None`` and correctly *not* shadowing
            what the manifest said:

            >>> import tempfile
            >>> from pathlib import Path
            >>> manifest = '''
            ... [tool.rhiza-task]
            ... source_folder = "lib"
            ... coverage_fail_under = 100
            ... uv_sync_args = "--group test"
            ... '''
            >>> with tempfile.TemporaryDirectory() as tmp:
            ...     root = Path(tmp)
            ...     _ = (root / "pyproject.toml").write_text(manifest)
            ...     cfg = Config.load(root, source_folder=None, typechecker="mypy")
            >>> cfg.source_folder, cfg.coverage_fail_under, cfg.typechecker
            ('lib', 100, 'mypy')

            A ``tuple[str, ...]`` field given as a string is split on whitespace rather
            than one character per argument, which is the make layer's own format and the
            bug ``__post_init__`` exists to prevent:

            >>> cfg.uv_sync_args
            ('--group', 'test')

            The manifest that carried the table is also what put the repository in a
            layer, and the check set follows from the layers rather than from a list
            anyone maintains:

            >>> cfg.layers
            ('python',)
            >>> cfg.rhiza_checks[-1]
            'pytest_rhiza.checks.test_docstrings'

            An unreadable setting fails here, before any tool is provisioned -- the shell
            ``case`` that used to validate it ran after:

            >>> with tempfile.TemporaryDirectory() as tmp:
            ...     Config.load(Path(tmp), typechecker="pyright")
            Traceback (most recent call last):
                ...
            ValueError: typechecker must be one of ty, mypy, both (got 'pyright')
        """
        root = (root or Path.cwd()).absolute()
        raw: dict[str, Any] = {}
        raw.update(_from_env_file(root / ".rhiza" / ".env"))
        raw.update(_from_rhiza_toml(root / "rhiza.toml"))
        # Cargo before pyproject, so a repo carrying both -- a Rust crate with a Python
        # binding package, say -- resolves to the same settings as the Python-only repo it
        # grew out of, rather than to whichever manifest happened to be read last.
        raw.update(_from_manifest(root / "Cargo.toml"))
        raw.update(_from_manifest(root / "pyproject.toml"))
        raw.update(_from_environ(os.environ))
        raw.update({k: v for k, v in overrides.items() if v is not None})

        # .python-version wins over a configured python_version for the reason python.mk
        # reads it: it is what uv itself honours, so a second source of truth could only
        # ever disagree.
        pv = root / ".python-version"
        if pv.is_file() and (text := pv.read_text().strip()):
            raw["python_version"] = text

        known = {f.name for f in fields(cls)} - {"root"}
        return cls(root=root, **{k: v for k, v in raw.items() if k in known})


_FIELD_NAMES = frozenset(f.name for f in fields(Config))
"""Every field name, for :meth:`Config.field_for`. Built once rather than per variable."""


def _from_env_file(path: Path) -> dict[str, Any]:
    """Read ``.rhiza/.env``.

    An empty assignment (``RHIZA_CI_OS_MATRIX=``) is dropped rather than carried as an
    empty string, for the reason given in :func:`_from_environ`: that rule mirrors make's
    ``?=``, which is what ``rhiza_ci.yml`` relies on when it exports the matrix empty for
    every consumer so their own ``.rhiza/.env`` can answer.

    **A consequence, now that one default is non-empty.** The rule used to be justified as
    "no field's type has a meaningful empty value", and that stopped being true when
    :attr:`Config.mkdocs_extra_packages` gained a default: for it, empty means "install no
    plugins", which is a real choice rather than an absent one. It is expressible as
    ``mkdocs-extra-packages = []`` in a manifest, where TOML distinguishes an empty array
    from an absent key -- and *not* here or in the environment, where both arrive as the
    same empty string. The behaviour is unchanged and deliberate: the matrix case above
    needs it, and a dotenv layer cannot tell the two apart. Only the reasoning is amended,
    so a reader does not conclude from the old wording that ``[]`` is meaningless anywhere.

    Args:
        path: Path to the dotenv file.

    Returns:
        Parsed settings; empty when the file is absent.
    """
    if not path.is_file():
        return {}
    return {Config.field_for(k): _coerce(v) for k, v in dotenv_values(path).items() if v and v.strip()}


def _from_manifest(path: Path) -> dict[str, Any]:
    """Read ``[tool.rhiza-task]`` from a language manifest.

    ``pyproject.toml`` and ``Cargo.toml`` alike: the table is namespaced under ``tool``,
    which cargo ignores as readily as any Python build backend does, so one reader serves
    both and a Rust crate needs no file a Python project does not have.

    Args:
        path: Path to pyproject.toml or Cargo.toml.

    Returns:
        Parsed settings; empty when the file or the table is absent.
    """
    return _table(path, lambda data: data.get("tool", {}).get("rhiza-task", {}))


def _from_rhiza_toml(path: Path) -> dict[str, Any]:
    """Read ``rhiza.toml``, the manifest-free settings file.

    Settings sit at the top level, because a file named after this tool has nothing to
    namespace against. A ``[tool.rhiza-task]`` table is honoured too, and wins when both
    are present: the pyproject spelling is what a reader will have seen first, and
    silently ignoring it would be the worst of the three possible behaviours.

    Args:
        path: Path to rhiza.toml.

    Returns:
        Parsed settings; empty when the file is absent.
    """
    return _table(path, lambda data: data.get("tool", {}).get("rhiza-task") or data)


def _table(path: Path, select: Callable[[dict[str, Any]], Any]) -> dict[str, Any]:
    """Read a TOML file and normalise the selected table's keys and values.

    Values here are already typed by TOML, so they bypass :func:`_coerce` -- a TOML array
    arrives as a list and is tupled, nothing is parsed out of a string. Keys that are not
    field names are left in place and dropped by :meth:`Config.load`, which is what makes
    reading ``rhiza.toml``'s top level safe.

    Args:
        path: Path to the TOML file.
        select: Picks the settings table out of the parsed document.

    Returns:
        Parsed settings; empty when the file is absent or unreadable.

    Raises:
        ValueError: When the file is not valid TOML. A settings file that does not parse
            is a mistake worth reporting, not a file to skip -- unlike an absent one.
    """
    if not path.is_file():
        return {}
    try:
        data = tomllib.loads(path.read_text())
    except tomllib.TOMLDecodeError as exc:
        msg = f"{path.name} is not valid TOML: {exc}"
        raise ValueError(msg) from exc
    table = select(data)
    if not isinstance(table, dict):
        return {}
    return {
        k.replace("-", "_"): tuple(v) if isinstance(v, list) else v for k, v in table.items() if not isinstance(v, dict)
    }


def _from_environ(environ: Mapping[str, str]) -> dict[str, Any]:
    """Read settings from the process environment.

    Both ``RHIZA_CI_OS_MATRIX`` and bare ``SOURCE_FOLDER`` are accepted, because the
    reusable workflows currently pass bare make-style names on the command line and those
    jobs must keep working through the transition.

    **An empty value counts as unset.** This is not a nicety, it is the make semantics
    this layer replaces. rhiza_ci.yml's ``generate-matrix`` job exports

        RHIZA_CI_OS_MATRIX: ${{ github.repository == 'jebel-quant/rhiza'
                               && '["ubuntu-latest","macos-latest"]' || '' }}

    -- one variable, set for the mother repo and *deliberately empty* for every consumer,
    whose own ``.rhiza/.env`` is then meant to answer. make's ``?=`` treats an exported
    empty string as set, which is why the retired ``ci-os-matrix`` recipe resolved through
    ``$(or ...)``; dropping empties here is the same rule, applied one layer earlier so
    every setting gets it rather than only the one whose recipe remembered to ask.

    Args:
        environ: The environment mapping.

    Returns:
        Parsed settings.

    """
    settings = ((Config.field_for(k), v) for k, v in environ.items())
    return {k: _coerce(v) for k, v in settings if k in _FIELD_NAMES and v.strip()}


def _coerce(value: str) -> Any:
    """Turn a string setting into the field's type.

    Three shapes exist in ``.rhiza/.env`` today and all must keep parsing: a JSON array
    (``RHIZA_CI_OS_MATRIX=["ubuntu-latest","macos-latest"]``), a semicolon-separated list
    (``LICENSE_FAIL_ON=GPL;LGPL;AGPL``), and a plain scalar.

    Args:
        value: The raw string.

    Returns:
        A ``str``, ``int``, ``bool`` or ``tuple[str, ...]``.
    """
    value = value.strip()
    if value.startswith("["):
        return tuple(json.loads(value))
    if ";" in value:
        return tuple(p for p in value.split(";") if p)
    if value.lower() in {"true", "false"}:
        return value.lower() == "true"
    if value.lstrip("-").isdigit():
        return int(value)
    return value
