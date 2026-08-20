"""The executable documentation: the 39 ``>>>`` examples in ``src/``, actually evaluated.

Five docstrings carry the load-bearing contracts as examples -- ``Run.exit_code``'s eleven
*are* the exit-code specification (signal collapse, BLOCKED standing alone, pytest's 2
surviving unflattened), ``Guard.check``'s ten enumerate every way a guard goes unsatisfied
and the exact line the runner prints, ``Config.load``'s nine document the layer-resolution
order. Nothing ran them. ``rhiza-test`` runs pytest-rhiza's ``test_docstrings`` and
``docs-coverage`` runs interrogate, and both ask whether a docstring *exists and is
well-formed*; neither evaluates an example. So the examples in the places a reader goes to
learn the contract -- and a refactor is most likely to change quietly -- could go stale at
100% on every gate.

This module is that gate. It is a test rather than ``--doctest-modules`` in ``pytest.ini``
or in the ``test`` task's argument vector, for two reasons:

* the task ships to consumer repositories, where whether to gate doctests is their call;
* ``addopts`` applies to every pytest this repo runs, including ``rhiza-test``'s
  ``pytest --pyargs pytest_rhiza.checks.*`` -- which would gate a *dependency's* doctests
  in this repo's name, and turn this repo red for an upstream release.

It is also the one place the suite's hermeticism bends: importing every module under
``src/`` is not patching anything. That is harmless here -- importing a task module only
registers its tasks -- but it is why the rule in CLAUDE.md is about what a test *runs*
rather than what it imports.
"""

from __future__ import annotations

import doctest
import importlib
import pkgutil

import pytest

import rhiza_task

MODULES = tuple(sorted(info.name for info in pkgutil.walk_packages(rhiza_task.__path__, "rhiza_task.")))
"""Every module under ``src/rhiza_task``, discovered rather than listed.

A list would be a second place to remember, and the failure it permits is silent: a new
module's examples simply never run. ``walk_packages`` recurses into ``tasks/``, so the
twelve task modules are covered by the same parametrisation.
"""

LOAD_BEARING = (
    ("rhiza_task.config", "Config.field_for"),
    ("rhiza_task.config", "Config.load"),
    ("rhiza_task.runner", "Run.exit_code"),
    ("rhiza_task.spec", "Guard.check"),
    ("rhiza_task.spec", "lookup"),
)
"""The five docstrings whose examples are the specification, as (module, qualname).

Named explicitly, because the test above is otherwise satisfied by a repository with no
examples at all: a module with nothing to run reports ``0 of 0`` and passes. That is the
same "green gate that measured nothing" failure the ``RHIZA_DOCTEST_FOLDERS`` tests in
``test_tasks.py`` exist to prevent.
"""


@pytest.mark.parametrize("name", MODULES)
def test_every_example_still_evaluates(name: str) -> None:
    """Run one module's doctests, and fail with what the examples reported.

    Parametrised per module rather than looped, so a stale example names the module it is in
    without a reader opening the log.

    Args:
        name: The module to run.
    """
    result = doctest.testmod(importlib.import_module(name), verbose=False)
    assert not result.failed, f"{result.failed} of {result.attempted} examples failed in {name}"


@pytest.mark.parametrize(("module_name", "qualname"), LOAD_BEARING)
def test_the_contracts_are_still_documented_by_example(module_name: str, qualname: str) -> None:
    """Assert one named docstring still carries at least one example.

    Args:
        module_name: The module the docstring lives in.
        qualname: The function or method, e.g. ``Run.exit_code``.
    """
    module = importlib.import_module(module_name)
    found = {test.name: test for test in doctest.DocTestFinder().find(module)}
    test = found.get(f"{module_name}.{qualname}")
    assert test is not None, f"{module_name}.{qualname} has no docstring for doctest to find"
    assert test.examples, f"{module_name}.{qualname} carries no doctest examples"
