"""Which of a fail-control's breaks THIS run applies, how it runs them, and proof that all ran.

Both scripts in this directory run one staged suite per break. In one CI job they
no longer fit inside GitHub's six-hour limit, and cut into a few jobs BY COUNT
they still took up to two hours each, because the breaks do not cost the same:
the newest controls (fee, closing, board) cost far more each and all sat in the
last slice. So this module does three things.

**It cuts BY MEASURED TIME.** `fail_control_times.json`, beside this file, holds
the seconds each break took on a CI runner, by NAME. The breaks are dealt
longest first into the shard that is lightest so far, and the number of shards
is the smallest that keeps every shard's estimated time under `BUDGET_S`. There
is no shard count written anywhere: the workflow reads it from `--plan`, and
`select` refuses any other. A break with no measured time yet -- one added since
the file was last refreshed -- is dealt AFTER the measured ones, as the costliest
measured break, so adding one leaves every shard as it was except the one it
lands in. When that would cross the budget the count goes up by itself.

**It runs `JOBS` breaks at once on one runner** (`--shard` only). The suite is
mostly waiting -- 89 s of wall time for 52 s of CPU, measured -- so a runner can
carry several. Two suites at once on one machine are NOT independent: three
tests bind the services' default ports (8092-8094), and six intact suites run
side by side gave five red with `Address already in use`. So each suite runs in
its OWN network namespace with only its own loopback, and its own `TMPDIR`. Where
that cannot be had -- not Linux, no passwordless `sudo` -- it runs one break at a
time and says so. Control A runs IN THE POOL with the breaks, under the same
load, so a suite that only passes on an idle machine is red here too.

**It proves the cut.** A sharded control that silently skips breaks is worse
than no control -- it prints "all controls OK" about a set nobody ran. So
`select` refuses, with a non-zero exit and before anything is staged, a shard
count that is not the plan's, an index outside it, and anything on the command
line that is not `--plan` or exactly `--shard i/N`; `plan` refuses a measured
time for a break that is no longer in `BREAKS`, naming it, so a break deleted
from the script cannot leave quietly. Every shard PRINTS the name of every break
it ran, counted by the loop that ran it, and `verify`, run by CI once every shard
has finished, adds those names up per interpreter and fails, naming them, unless
every shard reported and passed and every break in the plan ran exactly once.

With no argument a script runs every break, one at a time, in one process,
exactly as it did before shards existed.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

#: Breaks one runner runs at once under `--shard`. GitHub's hosted Linux runner
#: for a public repository has 4 CPUs.
JOBS = 4
#: The estimated time, in seconds, one shard's pool may take -- control A and
#: its breaks -- before another shard is cut. Sized so the slowest job, with its
#: minute or two of setup and a slow runner's spread on top, stays under 15
#: minutes.
BUDGET_S = 480
#: Seconds assumed for a break, and for control A, before anything is measured.
UNMEASURED_S = 240

TIMES = Path(__file__).resolve().parent / "fail_control_times.json"

_ARG = re.compile(r"(\d+)/(\d+)")
_RAN = re.compile(r"^ran (\d+) of (\d+) breaks, shard (\d+)/(\d+)$", re.MULTILINE)
_BREAK = re.compile(r"^break ([a-z0-9_]+) (\d+(?:\.\d+)?)s$", re.MULTILINE)


# -- the cut ----------------------------------------------------------------


def measured(script: str) -> dict:
    """`{"control_a": s, "breaks": {name: s}}` for one script, empty if never measured."""
    if not TIMES.exists():
        return {"control_a": None, "breaks": {}}
    one = json.loads(TIMES.read_text(encoding="utf-8")).get(script, {})
    return {"control_a": one.get("control_a"), "breaks": dict(one.get("breaks", {}))}


def _pool(control_a: float, costs: list[float], jobs: int) -> float:
    """Wall time of control A then `costs` (longest first) on `jobs` workers."""
    free = [0.0] * jobs
    for cost in [control_a, *sorted(costs, reverse=True)]:
        slot = free.index(min(free))
        free[slot] += cost
    return max(free)


def cut(
    names: list[str], times: dict, jobs: int = JOBS, budget: float = BUDGET_S
) -> list[list[int]]:
    """Indices into `names`, one list per shard. Deterministic for the same input.

    Refuses (exit 2) a measured break that is not in `names`: that is a break
    deleted from the script, and it is named rather than forgotten.
    """
    breaks = times["breaks"]
    gone = sorted(set(breaks) - set(names))
    if gone:
        _refuse(
            f"{len(gone)} break(s) have a measured time and are not in BREAKS: {', '.join(gone)}. "
            "A break leaves the script AND fail_control_times.json together, on purpose"
        )
    default = max(breaks.values(), default=UNMEASURED_S)
    control_a = times["control_a"] or default
    known = sorted(
        (i for i, n in enumerate(names) if n in breaks), key=lambda i: (-breaks[names[i]], i)
    )
    new = [i for i, n in enumerate(names) if n not in breaks]
    cost = {i: breaks.get(n, default) for i, n in enumerate(names)}

    for count in range(1, len(names) + 1):
        shards: list[list[int]] = [[] for _ in range(count)]
        load = [0.0] * count
        for index in known + new:
            lightest = load.index(min(load))
            shards[lightest].append(index)
            load[lightest] += cost[index]
        worst = max(_pool(control_a, [cost[i] for i in one], jobs) for one in shards)
        if worst <= budget or count == len(names):
            return [sorted(one) for one in shards]
    raise AssertionError("unreachable")


def select(breaks: list, script: str, argv: list[str]) -> tuple[list[int], str]:
    """`(indices, label)` for this run, or exit 2 naming what was wrong."""
    names = [one["name"] for one in breaks]
    total = len(names)
    if not argv:
        return list(range(total)), "all"
    shards = cut(names, measured(script))
    if argv == ["--plan"]:
        print(json.dumps({"shards": len(shards), "total": total, "names": names}))
        sys.exit(0)
    if len(argv) != 2 or argv[0] != "--shard" or not _ARG.fullmatch(argv[1]):
        _refuse(f"expected no arguments, `--plan`, or exactly `--shard i/N`, got {argv!r}")
    index, count = (int(part) for part in argv[1].split("/"))
    if count != len(shards):
        _refuse(
            f"asked for shard {index}/{count} but the measured times cut this script into "
            f"{len(shards)} shards; whatever asked holds a different count from `--plan`"
        )
    if not 1 <= index <= count:
        _refuse(f"shard {index}/{count} is out of range 1..{count}")
    return shards[index - 1], f"{index}/{count}"


# -- the run ----------------------------------------------------------------


def _isolation() -> tuple[bool, str]:
    """Whether a suite can have a network namespace of its own here, and why not."""
    if not sys.platform.startswith("linux"):
        return False, f"no network namespaces on {sys.platform}"
    probe = subprocess.run(
        ["sudo", "-n", "unshare", "--net", "--", "true"], capture_output=True, text=True
    )
    if probe.returncode != 0:
        return False, f"`sudo -n unshare --net` refused: {probe.stderr.strip() or probe.returncode}"
    return True, ""


#: Run as root inside a fresh network namespace: bring its loopback up, then
#: drop back to the caller's own user and run the suite.
_ENTER = (
    'ip link set lo up && uid="$1" && gid="$2" && shift 2 && '
    'exec setpriv --reuid="$uid" --regid="$gid" --init-groups -- "$@"'
)


def suite(directory: Path, command: list[str], isolated: bool) -> subprocess.CompletedProcess:
    """`command` in `directory` with a `TMPDIR` of its own -- and a network of its own if asked."""
    scratch = tempfile.mkdtemp(prefix="ga-tmp-")
    try:
        env = {
            "TMPDIR": scratch,
            "PATH": os.environ.get("PATH", ""),
            "HOME": os.environ.get("HOME", ""),
        }
        pinned = ["env", *(f"{key}={value}" for key, value in env.items()), *command]
        if isolated:
            argv = [
                "sudo", "-n", "--preserve-env", "unshare", "--net", "--",
                "sh", "-c", _ENTER, "isolate", str(os.getuid()), str(os.getgid()), *pinned,
            ]
        else:
            argv = pinned
        return subprocess.run(argv, cwd=directory, capture_output=True, text=True)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def control(script, breaks, indices, label, stage, command, width, intact, judge, parallel) -> int:
    """Control A, then every break in `indices`. Returns how many controls failed.

    `parallel` is False with no shard argument: one break at a time, as before.
    """
    sys.stdout.reconfigure(line_buffering=True)
    jobs, isolated = 1, False
    if parallel:
        isolated, why = _isolation()
        if isolated:
            jobs = JOBS
        else:
            print(f"one break at a time: {why}")
    times = measured(script)["breaks"]
    order = sorted(indices, key=lambda i: (-times.get(breaks[i]["name"], 0.0), i))

    def staged_run(apply=None):
        directory = stage()
        try:
            if apply is not None and not apply(directory):
                return None, 0.0
            started = time.monotonic()
            result = suite(directory, command, isolated)
            return result, time.monotonic() - started
        finally:
            shutil.rmtree(directory, ignore_errors=True)

    def applier(brk):
        def apply(directory: Path) -> bool:
            path = directory / brk["file"]
            source = path.read_text(encoding="utf-8")
            if brk["from"] not in source:
                return False
            path.write_text(source.replace(brk["from"], brk["to"], 1), encoding="utf-8")
            return True

        return apply

    failures = 0
    lock = threading.Lock()
    with ThreadPoolExecutor(max_workers=jobs) as pool:
        # CONTROL A IS THE FIRST TASK IN THE POOL, so it runs on the same
        # machine, under the same load, as the breaks it is the baseline for.
        intact_future = pool.submit(staged_run)
        futures = [(number, pool.submit(staged_run, applier(breaks[number]))) for number in order]

        print(f"== control A: the suite must PASS intact ({jobs} at once) ==")
        result, control_a = intact_future.result()
        collected = intact(result)
        if collected < 0:
            failures += 1

        print(f"\n== control B: each break must make it FAIL — shard {label} ==")
        ran, took = [], {}
        for number, future in futures:
            brk = breaks[number]
            result, seconds = future.result()
            ran.append(brk["name"])
            with lock:
                if result is None:
                    # A break whose anchor has moved applies nothing, and the
                    # run then reports a passing suite as a failed control --
                    # for the wrong reason. Named here so the two cannot be
                    # confused. The judgement catches the OTHER shape of the
                    # same mistake: an anchor that is still there but whose
                    # replacement makes the suite ERROR.
                    print(f"  {brk['name']:{width}} *** ANCHOR NOT FOUND in {brk['file']} ***",
                          file=sys.stderr)
                    failures += 1
                    continue
                took[brk["name"]] = seconds
                if not judge(brk["name"], brk["why"], collected, result, width=width):
                    failures += 1

    report(ran, took, len(breaks), label, control_a)
    return failures


def report(ran: list[str], took: dict, total: int, label: str, control_a: float = 0.0) -> None:
    """What `verify` adds up: one `break` line per break the loop ran, by name.

    The seconds are what `refresh` reads back into the times file.
    """
    print(f"\nran {len(ran)} of {total} breaks, shard {label}")
    print(f"control_a {control_a:.1f}s")
    for name in ran:
        print(f"break {name} {took.get(name, 0.0):.1f}s")


# -- the proof --------------------------------------------------------------


def verify(directory: Path, plan: dict) -> list[str]:
    """Every problem with one interpreter's shard reports. Empty means whole.

    `directory` holds one `<script>-<i>.txt` per shard: the shard's `ran` line,
    its `break` lines and an `exit=<status>` line. `plan` is
    `{script: {shards, total, names}}` as each script's `--plan` printed it. A
    missing file is a shard that never reported -- killed, cancelled, or never
    scheduled -- and it is a failure.
    """
    problems = []
    for script, meta in sorted(plan.items()):
        shards, total, names = meta["shards"], meta["total"], meta["names"]
        if len(names) != total:
            problems.append(f"{script}: the plan names {len(names)} breaks and counts {total}")
        covered: list[str] = []
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
            ran, said_total, said_index, said_shards = map(int, lines[0])
            if (said_index, said_shards, said_total) != (index, shards, total):
                problems.append(
                    f"{script} shard {index}/{shards}: reported shard {said_index}/{said_shards} "
                    f"of {said_total} breaks"
                )
            these = [name for name, _ in _BREAK.findall(text)]
            if ran != len(these):
                problems.append(
                    f"{script} shard {index}/{shards}: said it ran {ran} and named {len(these)}"
                )
            covered += these
        missing = [name for name in names if name not in covered]
        doubled = sorted({name for name in covered if covered.count(name) > 1})
        unknown = sorted(set(covered) - set(names))
        if missing or doubled or unknown:
            problems.append(
                f"{script}: shards ran {len(covered)} of {total} breaks; "
                f"never ran {missing}, ran twice {doubled}, not in the plan {unknown}"
            )
        else:
            print(f"{script}: {shards} shards, {len(covered)} of {total} breaks, each exactly once")
    return problems


def refresh(directories: list[Path]) -> dict:
    """New `fail_control_times.json` content from shard reports: the slowest seen, by name."""
    current = json.loads(TIMES.read_text(encoding="utf-8")) if TIMES.exists() else {}
    seen: dict[str, dict] = {}
    for directory in directories:
        for where in sorted(directory.glob("*-*.txt")):
            script = where.name.split("-")[0]
            text = where.read_text(encoding="utf-8")
            one = seen.setdefault(script, {"control_a": 0.0, "breaks": {}})
            for name, seconds in _BREAK.findall(text):
                one["breaks"][name] = max(one["breaks"].get(name, 0.0), float(seconds))
            for seconds in re.findall(r"^control_a (\d+(?:\.\d+)?)s$", text, re.MULTILINE):
                one["control_a"] = max(one["control_a"], float(seconds))
    for script, one in seen.items():
        current[script] = {
            "control_a": round(one["control_a"]) or None,
            "breaks": {name: round(s) for name, s in sorted(one["breaks"].items())},
        }
    return current


def _refuse(why: str) -> None:
    print(f"*** SHARD REFUSED — {why}. Nothing was run. ***", file=sys.stderr)
    sys.exit(2)


if __name__ == "__main__":
    # `python scripts/_shard.py verify <directory> '<plan json>'`
    # `python scripts/_shard.py refresh <directory>...` rewrites the times file
    if len(sys.argv) >= 3 and sys.argv[1] == "refresh":
        fresh = refresh([Path(one) for one in sys.argv[2:]])
        TIMES.write_text(json.dumps(fresh, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        sys.exit(0)
    if len(sys.argv) != 4 or sys.argv[1] != "verify":
        _refuse(f"usage: _shard.py verify <directory> '<plan json>', got {sys.argv[1:]!r}")
    found = verify(Path(sys.argv[2]), json.loads(sys.argv[3]))
    for problem in found:
        print(f"*** {problem} ***", file=sys.stderr)
    sys.exit(1 if found else 0)


__all__ = ["control", "cut", "refresh", "report", "select", "suite", "verify"]
