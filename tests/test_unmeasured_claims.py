"""Claims this package DELETED because nothing measured them, and they stay deleted.

A deletion that is not checked comes back. Every sentence swept out here was
true-sounding, load-bearing in an argument, and about somebody else's hardware or
about a measurement nobody could reproduce — which is the shape this project has
a named rule for and keeps rediscovering.

**Each sweep names its query and carries a positive control.** An absence claim
is a claim about a SEARCH, not about the world: before "it is not there" means
anything, the same search has to be shown firing on something that IS.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

#: Everything a reader of this repository can reach. Derived from the tree, not
#: listed: a hand-written list of files cannot notice a file somebody adds.
def published() -> dict[str, str]:
    files = {}
    for pattern in ("src/**/*.py", "tests/**/*.py", "scripts/*.py", "docs/*.md",
                    "config/*.toml", "README.md"):
        for path in sorted(ROOT.glob(pattern)):
            if ".venv" in path.parts or "egg-info" in str(path):
                continue
            # THIS FILE quotes every deleted sentence verbatim, because that is
            # what its positive controls are made of. Excluded by name, and the
            # exclusion is why `test_a_claim_nobody_could_reproduce_is_gone`
            # asserts against the same list rather than against a count.
            if path.name == "test_unmeasured_claims.py":
                continue
            files[str(path.relative_to(ROOT))] = path.read_text(encoding="utf-8")
    return files


#: The clause, by CONCEPT rather than by phrasing, and it is the CAUSAL claim
#: that is forbidden: that leaving a call unanswered is what makes a door
#: station's own call list advance to the human's number. That justified
#: `concurrent_cases: 1` with an unmeasured fact about an Axis or a 2N unit, in
#: the one document whose opening section is a list of what is NOT measured. It
#: is doubly wrong on this build: the refusal is not an unanswered call at all.
#: baresip sends `486 Busy Here` after `180 Ringing`, which in SIP terms IS
#: answering it to say busy.
#:
#: Matched SENTENCE BY SENTENCE rather than over a window, because the sentences
#: that legitimately survive sit in the same paragraph as this one did, and a
#: window wide enough to spare them is wide enough to hide it.
JUSTIFICATION = re.compile(
    r"(unanswered call|not answer\w*|refus\w+ without being answered|do(?:es)? not answer)"
    r"[^.]{0,160}?(call list|fall[- ]through)"
    r"|(call list|fall[- ]through)[^.]{0,160}?"
    r"(so the intercom|moves? on to the human|move on to the human|is taken away|take that)"
    r"|answering it to say",
    re.IGNORECASE,
)

#: The two sentences that legitimately contain these words, and they are
#: different claims:
#:   * the INSTALL REQUIREMENT — how an installer configures the intercom, which
#:     is about a DEAD agent and a no-answer, not about a 486;
#:   * the NOT MEASURED statement that replaced the deleted one.
#: Keyed on the sentence itself, not on a window around it.
ALLOWED = re.compile(r"NOT MEASURED|not measured|must name the AGENT FIRST", re.IGNORECASE)


def sentences(text: str):
    """Rough sentence split, with each one's line number, WHITESPACE COLLAPSED.

    Rough is enough: what matters is that the unit is SMALLER than a paragraph,
    so an allowance granted to one sentence cannot shelter the one beside it.

    Collapsing the whitespace is NOT cosmetic and it is why this yields the
    normalised form rather than the raw one: every one of these files is
    hard-wrapped, so `call list` is `call\nlist` about half the time and a
    pattern written with a space in it silently sees neither.
    """
    line = 1
    for chunk in re.split(r"(?<=[.!?])\s+|\n\n+", text):
        yield line, " ".join(chunk.split())
        line += chunk.count("\n") + 1


def test_the_busy_refusal_is_no_longer_justified_by_hardware_nobody_has():
    """X6. The clause is gone from every file a reader can reach.

    QUERY, stated beside the claim so the next reader can see what it could not
    have seen: `JUSTIFICATION` above, over every `.py`, `.md` and `.toml` this
    repository publishes, with every hit accounted for one by one rather than
    counted.
    """
    hits = []
    for name, text in published().items():
        for line, sentence in sentences(text):
            match = JUSTIFICATION.search(sentence)
            if match is None or ALLOWED.search(sentence):
                continue
            hits.append(f"{name}:~{line}: {sentence.strip()[:160]!r}")
    assert hits == [], "the deleted justification is back:\n  " + "\n  ".join(hits)


def test_the_sweep_above_can_find_the_clause_when_it_is_there():
    """THE POSITIVE CONTROL. Without it the test above is a claim about a regex.

    The exact sentence that was deleted, run through the same expression. If
    this does not fire, nothing the sweep says about its absence means anything.
    """
    deleted = (
        "A call arriving during a case is refused without being answered. That is "
        "deliberate: an unanswered call is what makes the intercom's own call list move "
        "on to the human's number, and answering it to say \"busy\" would take that "
        "fall-through away."
    )
    assert JUSTIFICATION.search(deleted), "the sweep cannot see the thing it swept for"
    # And it is not so wide that it fires on anything: the sentence that REPLACED
    # it, and the install requirement that legitimately survives, are both clear
    # of it once the allowance is applied.
    replacement = (
        "The agent hangs the unanswered call up, and baresip sends 486 Busy Here after "
        "180 Ringing. That is read out of a second caller's own user agent."
    )
    assert not JUSTIFICATION.search(replacement)


def test_what_replaced_it_is_in_the_document_and_says_it_is_not_measured():
    """A deletion that installs a new sentence has not finished until that one is checked.

    The replacement is two claims and they are different: what the refusal IS,
    measured from the caller's side, and what remains NOT MEASURED about it.
    """
    contract = (ROOT / "docs" / "CONTRACT.md").read_text(encoding="utf-8")
    assert "486 Busy Here" in contract and "180 Ringing" in contract
    assert re.search(
        r"(Axis|2N|door station).{0,200}call list.{0,200}(NOT MEASURED|not measured)",
        contract, re.IGNORECASE | re.DOTALL,
    ), "the unmeasured half is not stated where it was deleted"
    # And it is in the agent's own NOT MEASURED list, under its first heading,
    # rather than only beside the behaviour it qualifies.
    heading = contract.index("## What has been measured, and what has not")
    section = contract[heading : contract.index("\n## ", heading + 10)]
    assert "486" in section, "it is not in the agent's not-measured list"


@pytest.mark.parametrize(
    "claim",
    [
        # The measurement an independent session could not reproduce. It is gone
        # from the contract, and the refusal no longer rests on it.
        "the operator heard the driver before `conference` was ever sent",
        # The source's own false sentence: three settings said to be checked at
        # startup by code that checked none of them.
        "they are checked at startup and named in `docs/CONTRACT.md`",
    ],
)
def test_a_claim_nobody_could_reproduce_is_gone(claim):
    """Each one verbatim, so the assertion is about the sentence and not a paraphrase."""
    assert [name for name, text in published().items() if claim in text] == []
