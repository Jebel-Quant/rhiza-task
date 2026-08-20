r"""Prerequisite diagnostics: doctor.mk, as a task.

The fourth recipe that resists the declarative form. In doctor.mk it is 69 lines of shell
containing two functions defined inside a make recipe -- ``version_ge``, which is an awk
program comparing dotted versions component by component, and ``check_tool``, which takes
five positional arguments including a *quoted shell command to eval* for extracting the
version. The escaping is such that the awk field references appear as ``\\$$i``.

The change of substance is which tools are required. doctor.mk requires GNU make, because
the whole task layer was make; here make is optional -- a repo may keep a Makefile that
forwards to the CLI, but every task is reachable without one. Requiring a tool the layer no
longer needs is how a diagnostic starts lying about its own prerequisites.
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
YELLOW = "\033[33m"
RESET = "\033[0m"

VERSION_RE = re.compile(r"(\d+(?:\.\d+)+)")


@dataclass(frozen=True)
class Tool:
    """A prerequisite, its minimum version, and where to get it.

    Attributes:
        name: Executable name.
        minimum: Lowest acceptable dotted version.
        url: Install instructions, printed when it is missing.
        required: When False, a miss is reported but does not fail the task.
    """

    name: str
    minimum: str
    url: str
    required: bool = True


TOOLS = (
    Tool("uv", "0.4.0", "https://docs.astral.sh/uv/getting-started/installation/"),
    Tool("git", "2.0.0", "https://git-scm.com"),
    # Optional: only a repo-owned forwarding Makefile needs it, and that is a convenience.
    Tool("make", "3.8.0", "https://www.gnu.org/software/make/", required=False),
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
            if tool.required:
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
            _report(tool, "unknown", ok=False, note=f"required >= {tool.minimum}")
            if tool.required:
                failed.append(tool.name)
        elif at_least(version, tool.minimum):
            _report(tool, ".".join(map(str, version)), ok=True, note=f">= {tool.minimum}")
        else:
            _report(tool, ".".join(map(str, version)), ok=False, note=f"< {tool.minimum}")
            if tool.required:
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
    if ok:
        mark, colour = "[ OK ]", GREEN
    elif tool.required:
        mark, colour = "[FAIL]", RED
    else:
        mark, colour = "[WARN]", YELLOW
    optional = "" if tool.required else " (optional)"
    print(f"{colour}{mark}{RESET} {tool.name:<8} {version:<10} {note}{optional}")
