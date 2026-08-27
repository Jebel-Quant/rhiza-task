"""Fail when one module carries too many complex blocks, whatever their individual ranks.

``rhiza-task complexity`` gates the worst *block* at ``complexity_max``. Nothing gated
*accumulation* -- a module whose blocks are each defensible and collectively a lot -- and
issue #153 filled that gap with radon's maintainability index, which was the wrong
instrument.

MI counts length, comments count as length, and dense comments are this repository's stated
house style: writing the note that explained the MI ceiling cost 1.78 points for 19 lines of
prose, a quarter of the headroom, with no branch added or removed. A gate the convention
erodes is one people learn to raise rather than heed. See #156.

The measurement that decided the replacement is worth keeping, because it says the old gate
was not merely fragile but wrong: MI ranked ``config.py`` B and ``tasks/fences.py`` A, while
fences.py carries *more* blocks at rank B or worse and the same total complexity. Whatever MI
was reporting there, it was not complexity -- so counting the blocks directly loses nothing.

A file rather than an inline heredoc in ``ci.yml`` because it is the only step in that
workflow with real logic, and a step whose reasoning does not fit on one line is a step whose
reasoning ends up nowhere.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

HARD = 6
"""The rank-B boundary: radon calls CC 6-10 a B, and anything at or above this is a block
worth counting rather than a getter."""

CEILING = 6
"""How many such blocks one module may hold, one above the current worst.

A ceiling rather than a target, and the rule CLAUDE.md already states for a block's own
figure applies: a module that reaches it is the point to decompose, not the point to raise
the number.
"""


def over_ceiling(report: dict[str, object]) -> dict[str, int]:
    """Return each module holding more than :data:`CEILING` blocks at :data:`HARD` or above.

    Args:
        report: radon's ``cc --json`` output. A module radon could not parse maps to a dict
            carrying its error rather than to a list of blocks, and is skipped -- a syntax
            error is `ruff`'s finding to report, and swallowing it here would be a second
            gate answering for the first.

    Returns:
        Module path to count, for the modules over the ceiling.
    """
    counted = {
        path: sum(1 for block in blocks if block["complexity"] >= HARD)
        for path, blocks in report.items()
        if isinstance(blocks, list)
    }
    return {path: n for path, n in counted.items() if n > CEILING}


def main(argv: list[str]) -> int:
    """Report every module over the ceiling and set the exit status.

    Args:
        argv: One path, to radon's JSON report.

    Returns:
        1 when any module is over the ceiling, else 0.
    """
    over = over_ceiling(json.loads(Path(argv[1]).read_text()))
    for path, n in sorted(over.items()):
        print(f"::error::{path} holds {n} blocks at CC >= {HARD}; the ceiling is {CEILING}")
    if not over:
        print(f"[INFO] no module holds more than {CEILING} blocks at CC >= {HARD}")
    return 1 if over else 0


if __name__ == "__main__":  # pragma: no cover - the CI entry point, exercised by the job itself
    sys.exit(main(sys.argv))
