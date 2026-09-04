"""How a fail-control decides that a break BROKE something.

THE ONE PLACE, for both scripts in this directory, and it is one place because
the alternative was measured in `lane-controller` and failed. Both scripts here
used to judge on the subprocess's EXIT STATUS: zero meant the break did nothing,
non-zero meant "fails as required". That is true of a break that makes tests
FAIL and it is equally true of one that makes them ERROR -- and an anchor that
has moved does exactly that, as does an import this build cannot make and a
collection that comes back empty. The message a control prints in that case says
"fails as required" and is wrong, and nothing parses the line that would have
said so.

**This file is a COPY of `lane-controller/scripts/_control.py`**, taken at
`053808b` -- the commit this package pins -- and it differs from it in exactly
two places, both named here rather than left to be found by a diff:

  * the lane's scripts run a named suite with environment breaks, and these
    stage a whole copy of the tree and run pytest inside it, so `run` stays in
    each script and only the JUDGEMENT is shared;
  * `judge` has one branch the lane's does not: a run that printed NO SUMMARY
    LINE. Measured here -- renaming `Monitor` in a copy of the tree gives exit 4
    with nothing on stdout, because `tests/conftest.py` cannot be imported, and
    every count parses to zero. The lane's version would report "PASSED WHEN
    ..." about a suite that never ran, which is the wrong sentence for the right
    exit code. It is the same mistake as an ERROR and it now says so. **That
    branch belongs in `lane-controller` too and is not carried there by this
    round** -- this repository may not change that one.

The rule it encodes is that repository's round-6 Y7 and it is not re-derived
here, because two copies of a judgement is the thing this file exists to stop.

The judgement is on THE SUMMARY LINE, and it requires of a break four things,
each of which is a different way for a control to be worthless:

  * `failed >= 1`   -- something has to have gone red. A break that changes
                       nothing is not a control.
  * `errors == 0`   -- an error is a suite that could not RUN. It is the shape
                       an anchor that has moved takes, and it is not evidence
                       about the guarantee.
  * `passed + failed == control A's collected count` -- the same tests ran. A
                       break that changes what is COLLECTED is measuring a
                       different suite from the intact run it is compared with.
  * `passed >= 1`   -- and something has to have stayed green. A break that
                       fails everything is a broken tree, not a control: it
                       cannot distinguish the guarantee from the harness.

And of control A, that `passed >= 1` with no failures and no errors: a suite that
collected nothing passes, and every break under it would then "fail to fail" for
a reason that has nothing to do with the guarantees.

A break that fails any of these is reported as ANCHOR NOT FOUND / NOT A CONTROL
and the script exits non-zero. It is never reported as "fails as required".
"""

from __future__ import annotations

import re
import subprocess

#: pytest's own summary line: `3 failed, 35 passed in 0.03s`, `38 errors in
#: 0.15s`, `291 passed in 21.35s`. Counted per word rather than positionally,
#: because the words appear in different orders and some of them are absent.
_COUNT = r"(\d+) {word}s?\b"


def counts(result: subprocess.CompletedProcess) -> dict[str, int]:
    """`{failed, passed, errors}` from the LAST summary line pytest printed."""
    text = result.stdout
    found = {}
    for word in ("failed", "passed", "error"):
        hits = re.findall(_COUNT.format(word=word), text)
        found["errors" if word == "error" else word] = int(hits[-1]) if hits else 0
    return found


def tail(result: subprocess.CompletedProcess, lines: int = 1) -> str:
    body = [line for line in result.stdout.strip().splitlines() if line.strip()]
    return " | ".join(body[-lines:]) if body else "(no output)"


def intact(result: subprocess.CompletedProcess) -> int:
    """Control A. Returns the COLLECTED count every break is measured against.

    `-1` when the intact suite is not a suite that passed, and the caller counts
    that as a failure: there is nothing to measure a break against.
    """
    got = counts(result)
    if result.returncode != 0 or got["failed"] or got["errors"] or got["passed"] < 1:
        import sys

        print(
            f"  CONTROL A FAILED — the suite does not pass even intact: {tail(result)}",
            file=sys.stderr,
        )
        print(result.stdout, file=sys.stderr)
        return -1
    print(f"  control A OK — {got['passed']} passed")
    return got["passed"]


def judge(
    name: str,
    why: str,
    collected: int,
    result: subprocess.CompletedProcess,
    width: int = 38,
) -> bool:
    """Report one break. True when it is a real control that went red."""
    import sys

    got = counts(result)
    label = name.ljust(width)
    summary = f"{got['failed']} failed, {got['passed']} passed, {got['errors']} errors"

    if got["errors"]:
        print(
            f"  {label} *** ANCHOR NOT FOUND / NOT A CONTROL — {summary}: an ERROR is a suite "
            "that could not run, not a guarantee that went red ***",
            file=sys.stderr,
        )
        return False
    if not any(got.values()):
        # NO SUMMARY LINE AT ALL. pytest exits 4 with nothing on stdout when a
        # conftest cannot be imported, and prints `no tests ran` when a marker
        # or a path collects nothing -- and both parse to zeros. Measured on
        # this package: renaming `Monitor` in a copy of the tree gave exit 4, an
        # empty summary, and the old exit-status rule reported "fails as
        # required". It is the SAME mistake as an error and it needs its own
        # branch, because with every count at zero the `failed < 1` test below
        # would fire first and print "PASSED WHEN ..." about a suite that never
        # ran.
        print(
            f"  {label} *** ANCHOR NOT FOUND / NOT A CONTROL — pytest printed no summary line "
            f"(exit {result.returncode}): the suite did not run, so nothing was measured ***",
            file=sys.stderr,
        )
        return False
    if got["failed"] < 1:
        print(
            f"  {label} *** PASSED WHEN {why.upper()} — the suite is not measuring this ***",
            file=sys.stderr,
        )
        return False
    if got["passed"] < 1:
        print(
            f"  {label} *** NOT A CONTROL — {summary}: the break fails EVERYTHING, so it "
            "cannot separate this guarantee from the harness ***",
            file=sys.stderr,
        )
        return False
    if got["passed"] + got["failed"] != collected:
        print(
            f"  {label} *** ANCHOR NOT FOUND / NOT A CONTROL — {summary} against control A's "
            f"{collected} collected: the break changed what RUNS ***",
            file=sys.stderr,
        )
        return False
    print(f"  {label} fails as required when {why} — {summary}")
    return True


__all__ = ["counts", "intact", "judge", "tail"]
