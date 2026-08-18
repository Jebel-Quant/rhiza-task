"""The optional testing extras: test.mk, as tasks.

Four gates no aggregate depends on, each needing its own tool and folder convention. They
stay separate from the language layer for the reason test.mk gives: a project should be
able to take the Python gate set without also declaring an opinion on mutation testing.

``mutation`` is the second of the four recipes that resists the declarative form -- it runs
four tools in sequence, moves a directory, and must report the *first* status rather than
the last.
"""

from __future__ import annotations

import shutil

from ..config import Config
from ..spec import Failed, Guard, task
from ..uv import uv_run

PYTEST_NO_TESTS_COLLECTED = 5
"""pytest's "no tests collected".

For ``hypothesis-test`` this is a skip, not a failure: a project with no property-based
tests is a valid project, and the marker expression legitimately matches nothing.
"""


@task(
    "benchmark",
    "run the performance benchmarks",
    section="Testing extras",
    needs=("install",),
    guards=(Guard("tests_folder", glob="benchmarks/*.py", reason="no benchmarks folder"),),
)
def benchmark(cfg: Config) -> None:
    """Run pytest-benchmark over ``tests/benchmarks``, writing a histogram and JSON.

    The two pins are test.mk's, kept exact: benchmark results are only comparable across
    runs of the same tool version.

    Args:
        cfg: The resolved config.
    """
    (cfg.root / "_tests" / "benchmarks").mkdir(parents=True, exist_ok=True)
    uv_run(
        "pytest",
        f"{cfg.tests_folder}/benchmarks/",
        "--benchmark-only",
        "--benchmark-histogram=_tests/benchmarks/histogram",
        "--benchmark-json=_tests/benchmarks/results.json",
        cwd=cfg.root,
        withs=("pytest", "pytest-benchmark==5.2.3", "pygal==3.1.0"),
    )


@task(
    "hypothesis-test",
    "run the property-based tests",
    section="Testing extras",
    needs=("install",),
    guards=(Guard("tests_folder", glob="test_*.py", reason="no test files found"),),
)
def hypothesis_test(cfg: Config) -> None:
    """Run the Hypothesis-marked tests with statistics and a fixed seed.

    Args:
        cfg: The resolved config.

    Raises:
        Failed: When a property test fails.
    """
    (cfg.root / "_tests" / "hypothesis").mkdir(parents=True, exist_ok=True)
    code = uv_run(
        "pytest",
        f"--ignore={cfg.tests_folder}/benchmarks",
        "-v",
        "--hypothesis-show-statistics",
        "--hypothesis-seed=0",
        "-m",
        "hypothesis or property",
        "--tb=short",
        "--html=_tests/hypothesis/report.html",
        cwd=cfg.root,
        withs=("pytest", "hypothesis", "pytest-html"),
        env={"PYTEST_HTML_TITLE": "Hypothesis tests"},
        check=False,
    )
    if code == PYTEST_NO_TESTS_COLLECTED:
        print("[INFO] no hypothesis/property tests collected")
        return
    if code:
        raise Failed(code, "property tests failed")


@task(
    "stress",
    "run the stress and load tests",
    section="Testing extras",
    needs=("install",),
    guards=(Guard("tests_folder", glob="stress/*.py", reason="no stress folder"),),
)
def stress(cfg: Config) -> None:
    """Run the stress-marked tests.

    Args:
        cfg: The resolved config.
    """
    (cfg.root / "_tests" / "stress").mkdir(parents=True, exist_ok=True)
    uv_run(
        "pytest",
        "-v",
        "-m",
        "stress",
        "--tb=short",
        "--html=_tests/stress/report.html",
        cwd=cfg.root,
        withs=("pytest", "pytest-html"),
    )


@task(
    "mutation",
    "run mutation testing with mutmut",
    section="Testing extras",
    needs=("install",),
    guards=(Guard("source_folder"),),
)
def mutation(cfg: Config) -> None:
    """Run mutmut, then generate and relocate its HTML report.

    The exit-status handling is the whole subtlety, and test.mk gets it right in shell at
    the cost of four ``|| exit $$?`` clauses and a ``run_status`` variable: ``mutmut run``
    exits non-zero when mutants survive, but the report is worth having *precisely* then.
    So the run's status is remembered, the report is produced regardless, and the
    remembered status is what the task reports.

    Args:
        cfg: The resolved config.

    Raises:
        Failed: When mutants survived.
    """
    out = cfg.root / "_tests" / "mutation"
    out.mkdir(parents=True, exist_ok=True)

    run_status = uv_run(
        "mutmut",
        "run",
        f"--paths-to-mutate={cfg.source_folder}",
        f"--tests-dir={cfg.tests_folder}",
        cwd=cfg.root,
        withs=("mutmut",),
        check=False,
    )

    uv_run("mutmut", "html", cwd=cfg.root, withs=("mutmut",))
    generated = cfg.root / "html"
    if generated.is_dir():
        shutil.rmtree(out / "html", ignore_errors=True)
        shutil.move(str(generated), str(out / "html"))
    uv_run("mutmut", "results", cwd=cfg.root, withs=("mutmut",))

    if run_status:
        raise Failed(run_status, "mutants survived")
