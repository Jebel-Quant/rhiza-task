r"""Prerequisite diagnostics: doctor.mk, as a task.

The fourth recipe that resists the declarative form. In doctor.mk it is 69 lines of shell
containing two functions defined inside a make recipe -- ``version_ge``, which is an awk
program comparing dotted versions component by component, and ``check_tool``, which takes
five positional arguments including a *quoted shell command to eval* for extracting the
version. The escaping is such that the awk field references appear as ``\\$$i``.

The change of substance is which tools it asks about at all. doctor.mk probes GNU make,
because the whole task layer was make. It is not probed here, and neither is anything else
beyond uv and git -- the two a process running ``uvx rhiza-task`` genuinely cannot do
without.

That is a design boundary rather than a short list. **Optionality is what
:class:`~rhiza_task.spec.Guard` is for**: docker, gh, git-lfs, tectonic and marp are each
declared as a precondition on the task that wraps them, and a missing one reports itself on
the ``skipped`` line of the gate that wanted it, with the install URL in its ``reason``. A
diagnostic that also enumerated them would answer the same question one indirection further
from where it matters, and would need updating every time a bundle gained a tool.

So this task has one tier, not two: everything it names is required, and a miss is a
failure. ``make`` was the last inhabitant of the optional tier -- reported as a warning for
the sake of a repo-owned Makefile forwarding to the CLI -- and the tier went with it. If a
genuinely optional *core* prerequisite ever appears, that is an edit here rather than a
mechanism to keep warm for it.
"""

from __future__ import annotations

import re
import shutil

# The version probe below is a fixed argument vector, with no shell -- which is what bandit's B404
# asks about. The reason sits here rather than on the suppression comment itself: bandit reads
# everything after that marker as a comma-separated list of test IDs, so a trailing explanation
# becomes one `Test in comment:` warning per word.
import subprocess  # nosec B404
from dataclasses import dataclass

from ..config import Config
from ..spec import Failed, task

GREEN = "\033[32m"
RED = "\033[31m"
RESET = "\033[0m"

VERSION_RE = re.compile(r"(\d+(?:\.\d+)+)")


@dataclass(frozen=True)
class Tool:
    """A prerequisite, its minimum version, and where to get it.

    Every entry is required; see the module docstring for why there is no optional tier.

    Attributes:
        name: Executable name.
        minimum: Lowest acceptable dotted version.
        url: Install instructions, printed when it is missing.
    """

    name: str
    minimum: str
    url: str


# uv because a process launched by `uvx rhiza-task` runs *because* uv exists, and git because
# `clean` and the release flow drive it directly. Nothing else belongs here: see the module
# docstring on why the bundle CLIs are guards rather than entries.
TOOLS = (
    Tool("uv", "0.4.0", "https://docs.astral.sh/uv/getting-started/installation/"),
    Tool("git", "2.0.0", "https://git-scm.com"),
)


def parse_version(text: str) -> tuple[int, ...]:
    """Extract the first dotted version from a tool's ``--version`` output.

    Replaces doctor.mk's per-tool awk extraction commands -- ``uv --version | awk 'NR==1
    {print $$2}'`` and the rest -- with one regex, because every tool in ``TOOLS`` prints
    its version as the first dotted number on the first line.

    Args:
        text: The raw ``--version`` output.

    Returns:
        The version as a tuple of ints, empty when none was found.
    """
    match = VERSION_RE.search(text.splitlines()[0] if text.strip() else "")
    return tuple(int(p) for p in match.group(1).split(".")) if match else ()


def at_least(found: tuple[int, ...], minimum: str) -> bool:
    """Compare dotted versions, padding the shorter one with zeros.

    Args:
        found: The installed version.
        minimum: The required version, dotted.

    Returns:
        True when ``found`` is at least ``minimum``.
    """
    want = tuple(int(p) for p in minimum.split("."))
    width = max(len(found), len(want))
    return found + (0,) * (width - len(found)) >= want + (0,) * (width - len(want))


@task("doctor", "check local prerequisites", section="Dev")
def doctor(cfg: Config) -> None:
    """Report on each prerequisite, failing when a required one is missing or too old.

    Args:
        cfg: The resolved config.

    Raises:
        Failed: When a required tool is missing or below its minimum version.
    """
    failed: list[str] = []
    for tool in TOOLS:
        path = shutil.which(tool.name)
        if path is None:
            _report(tool, "missing", ok=False, note=f"install: {tool.url}")
            failed.append(tool.name)
            continue

        output = subprocess.run(  # noqa: S603  # nosec B603
            [path, "--version"],
            capture_output=True,
            text=True,
            check=False,
        ).stdout
        version = parse_version(output)
        if not version:
            # Not assumed fine. A tool that prints no parseable version is a tool this
            # diagnostic cannot vouch for, and saying nothing would be the same as passing.
            _report(tool, "unknown", ok=False, note=f"required >= {tool.minimum}")
            failed.append(tool.name)
        elif at_least(version, tool.minimum):
            _report(tool, ".".join(map(str, version)), ok=True, note=f">= {tool.minimum}")
        else:
            _report(tool, ".".join(map(str, version)), ok=False, note=f"< {tool.minimum}")
            failed.append(tool.name)

    print(f"\n[INFO] python {cfg.python_version} (from .python-version or config)")
    if failed:
        raise Failed(1, f"missing or outdated: {', '.join(failed)}")


def _report(tool: Tool, version: str, ok: bool, note: str) -> None:
    """Print one aligned diagnostic line.

    Args:
        tool: The tool being reported.
        version: What was found.
        ok: Whether it satisfies the requirement.
        note: Trailing detail.
    """
    mark, colour = ("[ OK ]", GREEN) if ok else ("[FAIL]", RED)
    print(f"{colour}{mark}{RESET} {tool.name:<8} {version:<10} {note}")
