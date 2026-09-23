"""Which of a fail-control's breaks THIS run applies, and proof that all of them ran.

Both scripts in this directory run one staged suite per break, and all of them
in a row no longer fit inside one CI job: GitHub's hosted runners stop a job at
six hours, and at `ed9f859` the two scripts together took five and a half to six
on one interpreter. So CI runs each script as N SHARDS, each a contiguous slice
of `BREAKS` by index, on its own runner.

A sharded control that silently skips breaks is worse than no control -- it
prints "all controls OK" about a set nobody ran. So `select` refuses, with a
non-zero exit and before anything is staged, every way the slicing can come
apart from the script:

  * a shard count that is not the script's own `SHARDS` constant. The workflow
    reads that constant through `--plan` rather than holding a copy, and this
    is what fails if a copy ever appears and drifts;
  * a shard index outside `1..SHARDS`, which would select an empty slice and
    "pass" having measured nothing;
  * anything on the command line that is not `--plan` or exactly `--shard i/N`.

The slices are `[(i-1)*TOTAL//N, i*TOTAL//N)` for i = 1..N, which are disjoint
and cover every index by construction. Arithmetic is not a measurement, so every
shard also PRINTS what it ran -- `ran K of TOTAL breaks, shard i/N, indices
a..b` -- with K counted by the loop that applied the breaks, and `verify`, run by
CI once every shard has finished, adds those lines up per interpreter and fails
unless every shard reported, every shard passed, and the index sets are
disjoint and together are the whole of `BREAKS`.

With no argument a script runs every break, in one process, exactly as it did
before shards existed.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

_ARG = re.compile(r"(\d+)/(\d+)")
_RAN = re.compile(r"ran (\d+) of (\d+) breaks, shard (\d+)/(\d+), indices (\d+)\.\.(\d+)")


def select(breaks: list, shards: int, argv: list[str]) -> tuple[range, str]:
    """`(indices, label)` for this run, or exit 2 naming what was wrong."""
    total = len(breaks)
    if not argv:
        return range(total), "all"
    if argv == ["--plan"]:
        print(json.dumps({"shards": shards, "total": total}))
        sys.exit(0)
    if len(argv) != 2 or argv[0] != "--shard" or not _ARG.fullmatch(argv[1]):
        _refuse(f"expected no arguments, `--plan`, or exactly `--shard i/N`, got {argv!r}")
    index, count = (int(part) for part in argv[1].split("/"))
    if count != shards:
        _refuse(
            f"asked for shard {index}/{count} but this script is cut into {shards} shards; "
            "whatever asked holds a different count from the script's SHARDS constant"
        )
    if not 1 <= index <= shards:
        _refuse(f"shard {index}/{count} is out of range 1..{shards}")
    start = (index - 1) * total // shards
    end = index * total // shards
    return range(start, end), f"{index}/{shards}"


def report(ran: int, total: int, label: str, indices: range) -> None:
    """The line `verify` adds up. `ran` is counted by the loop, not derived."""
    span = f"{indices.start}..{indices.stop - 1}" if len(indices) else "none"
    print(f"\nran {ran} of {total} breaks, shard {label}, indices {span}")


def verify(directory: Path, plan: dict) -> list[str]:
    """Every problem with one interpreter's shard reports. Empty means whole.

    `directory` holds one `<script>-<i>.txt` per shard, each the shard's `ran`
    line and an `exit=<status>` line; `plan` is `{script: {shards, total}}` as
    each script's `--plan` printed it. A missing file is a shard that never
    reported -- killed, cancelled, or never scheduled -- and it is a failure.
    """
    problems = []
    for script, meta in sorted(plan.items()):
        shards, total = meta["shards"], meta["total"]
        covered: list[int] = []
        for index in range(1, shards + 1):
            where = directory / f"{script}-{index}.txt"
            if not where.exists():
                problems.append(f"{script} shard {index}/{shards}: no report — it never finished")
                continue
            text = where.read_text(encoding="utf-8")
            exits = re.findall(r"^exit=(\d+)$", text, re.MULTILINE)
            if exits != ["0"]:
                problems.append(f"{script} shard {index}/{shards}: exit {exits or 'missing'}")
            lines = _RAN.findall(text)
            if len(lines) != 1:
                problems.append(f"{script} shard {index}/{shards}: {len(lines)} `ran` lines")
                continue
            ran, said_total, said_index, said_shards, first, last = map(int, lines[0])
            if (said_index, said_shards, said_total) != (index, shards, total):
                problems.append(
                    f"{script} shard {index}/{shards}: reported shard {said_index}/{said_shards} "
                    f"of {said_total} breaks"
                )
            span = list(range(first, last + 1))
            if ran != len(span):
                problems.append(
                    f"{script} shard {index}/{shards}: ran {ran} but indices {first}..{last} "
                    f"are {len(span)}"
                )
            covered += span
        ran_total = len(covered)
        if sorted(covered) != list(range(total)) or ran_total != total:
            missing = sorted(set(range(total)) - set(covered))
            doubled = sorted({i for i in covered if covered.count(i) > 1})
            problems.append(
                f"{script}: shards cover {ran_total} of {total} breaks; "
                f"missing {missing[:20]}, run twice {doubled[:20]}"
            )
        else:
            print(f"{script}: {shards} shards, {ran_total} of {total} breaks, each exactly once")
    return problems


def _refuse(why: str) -> None:
    print(f"*** SHARD REFUSED — {why}. Nothing was run. ***", file=sys.stderr)
    sys.exit(2)


if __name__ == "__main__":
    # `python scripts/_shard.py verify <directory> '<plan json>'`
    if len(sys.argv) != 4 or sys.argv[1] != "verify":
        _refuse(f"usage: _shard.py verify <directory> '<plan json>', got {sys.argv[1:]!r}")
    found = verify(Path(sys.argv[2]), json.loads(sys.argv[3]))
    for problem in found:
        print(f"*** {problem} ***", file=sys.stderr)
    sys.exit(1 if found else 0)


__all__ = ["report", "select", "verify"]
