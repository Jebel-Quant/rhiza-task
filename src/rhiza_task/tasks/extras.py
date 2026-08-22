"""The optional testing extras: test.mk, as tasks.

Three gates no ``all`` depends on -- ``book`` is the one aggregate that names them, for
their reports -- each needing its own tool and folder convention. They stay separate from
the language layer for the reason test.mk gives: a project should be able to take the Python
gate set without also declaring an opinion on benchmarks, stress runs or property-based
testing.

Each body is one vector plus, for ``hypothesis-test``, a single exit code: pytest's "no
tests collected", which is a skip rather than a failure for a project that has none.
"""

from __future__ import annotations

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
    layer="python",
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
    layer="python",
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
    layer="python",
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
