"""Properties over config parsing, where the input space is the difficulty.

Every other test in this suite is example-based, and for most of this package that is the
right shape: a task's contract *is* an argument vector, and a vector is best asserted by
writing it down. Config parsing is the exception. ``Config.field_for`` and
:func:`~rhiza_task.config._coerce` take arbitrary strings from four layers -- a dotenv
file, ``rhiza.toml``, a manifest, the environment -- and both have already shipped a bug
that a fixture list did not catch and could not have:

* ``RHIZA_CHECKS`` resolved to the unknown field ``checks`` and was silently dropped,
  because the prefix was stripped unconditionally. The field whose own name starts with
  ``rhiza_`` was the one case nobody enumerated.
* ``UV_SYNC_ARGS="--group test"`` reached a ``tuple[str, ...]`` field as a ``str`` and was
  splatted one *character* per argument: ``uv sync - - g r o u p``.

Both are statements about a whole class of input rather than about one value, so they are
written here as properties. The marker is Hypothesis's own -- its pytest plugin applies
``@pytest.mark.hypothesis`` to every ``@given`` test -- which is what ``rhiza-task
hypothesis-test`` selects with ``-m "hypothesis or property"``.
"""

from __future__ import annotations

import json
import string
from dataclasses import fields
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from rhiza_task.config import Config, _coerce

FIELD_NAMES = tuple(sorted(f.name for f in fields(Config)))
"""Every settable field, drawn from the dataclass rather than from a list kept in step."""

_UNSPACED = "".join(c for c in string.printable if not c.isspace())
UNSPACED_TOKEN = st.text(alphabet=_UNSPACED, min_size=1)
"""A token that survives ``str.split()`` whole: printable, non-empty, no whitespace."""

SETTING_TOKEN = st.text(alphabet=string.ascii_letters + string.digits + "-.+", min_size=1)
"""The alphabet the ``;``-separated shape actually carries -- ``GPL``, ``ubuntu-latest``.

Narrower than :data:`UNSPACED_TOKEN` on purpose, and narrow in the two ways that matter:
no ``;``, so the separator cannot appear inside a part, and no ``[``, so a value cannot
open with the character that selects the JSON branch instead.
"""


@pytest.fixture(scope="module")
def repo_root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Return one directory to build every ``Config`` against.

    Module-scoped rather than the usual ``tmp_path``: Hypothesis rejects a function-scoped
    fixture under ``@given``, since one directory would be shared across every example
    while appearing to be fresh for each. Nothing here writes to it -- it exists because
    ``__post_init__`` resolves the folder settings against a real path -- so sharing it is
    honest as well as necessary.

    Args:
        tmp_path_factory: pytest's session-scoped temporary directory factory.

    Returns:
        An empty directory standing in for a repository root.
    """
    return tmp_path_factory.mktemp("repo")


@given(name=st.sampled_from(FIELD_NAMES))
def test_every_field_is_reachable_by_every_documented_spelling(name: str) -> None:
    """Each of the four spellings the docstring promises resolves to the field itself.

    This is the ``RHIZA_CHECKS`` bug stated as a property. The old parametrize lists the
    eight cases somebody thought of; drawing from :data:`FIELD_NAMES` instead means a new
    field is covered the moment it is declared, and a *future* field named ``rhiza_*``
    cannot repeat the bug unnoticed.

    The last assertion is idempotence, which follows for free: normalising a name that
    already is a field must not move it, or the four spellings above would each resolve to
    something different on a second pass.

    Args:
        name: A real field name.
    """
    for spelling in (
        name,
        name.upper(),
        name.replace("_", "-"),
        f"RHIZA_{name.upper()}",
        f"rhiza-{name.replace('_', '-')}",
    ):
        assert Config.field_for(spelling) == name
        assert Config.field_for(Config.field_for(spelling)) == name


@given(name=st.text())
def test_field_for_never_invents_a_name(name: str) -> None:
    """The result is the normalised input, with at most one ``rhiza_`` prefix removed.

    The failure this rules out is worse than an unknown field, which merely gets dropped:
    a normaliser that returned some *other* field's name would apply a setting the caller
    never wrote. So the total statement -- over arbitrary text, not just plausible
    spellings -- is that the output is only ever one of two strings derived from the input.

    Args:
        name: Arbitrary text, as any of the four layers might carry.
    """
    lowered = name.lower().replace("-", "_")
    assert Config.field_for(name) in {lowered, lowered.removeprefix("rhiza_")}


@given(items=st.lists(st.text(), max_size=6))
def test_a_json_array_round_trips(items: list[str]) -> None:
    """``RHIZA_CI_OS_MATRIX=["ubuntu-latest","macos-latest"]`` survives the trip.

    The empty array is included deliberately: ``[]`` in a manifest is the one place TOML
    distinguishes an empty list from an absent key, which the layer readers rely on.

    Args:
        items: The list that was written out.
    """
    assert _coerce(json.dumps(items)) == tuple(items)


@given(items=st.lists(SETTING_TOKEN, min_size=2, max_size=6))
def test_a_semicolon_list_becomes_its_parts(items: list[str]) -> None:
    """``LICENSE_FAIL_ON=GPL;LGPL;AGPL`` splits on the separator and nothing else.

    At least two items, because a single one carries no ``;`` and is a scalar by the rule
    above it -- which is the branch, not an edge case being avoided.

    Args:
        items: The parts that were joined.
    """
    assert _coerce(";".join(items)) == tuple(items)


@given(value=st.integers())
def test_an_integer_setting_arrives_as_an_int(value: int) -> None:
    """``COVERAGE_FAIL_UNDER=100`` reaches an ``int`` field as an ``int``, sign and all.

    Args:
        value: The number that was written out.
    """
    assert _coerce(str(value)) == value


@given(tokens=st.lists(UNSPACED_TOKEN, max_size=6))
def test_a_space_joined_string_becomes_its_tokens(repo_root: Path, tokens: list[str]) -> None:
    """A ``tuple[str, ...]`` field given a string splits on whitespace, never per character.

    This is the ``UV_SYNC_ARGS`` bug stated as a property, and it is the one that needs
    ``__post_init__`` rather than ``_coerce`` alone: ``_coerce`` sees a string without
    knowing which field it is bound for, so the split can only happen where the field's
    type is known. The empty list is in range, and must give ``()`` rather than ``("",)``.

    Args:
        repo_root: A directory for the folder settings to resolve against.
        tokens: The arguments that were joined with spaces.
    """
    cfg = Config(root=repo_root, uv_sync_args=" ".join(tokens))
    assert cfg.uv_sync_args == tuple(tokens)
