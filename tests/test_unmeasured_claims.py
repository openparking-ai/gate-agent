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


# ---------------------------------------------------------------------------
# Z17 — THE CLAIM THAT THIS PACKAGE CANNOT ACT
# ---------------------------------------------------------------------------
#
# Round 7 gave this package its first route to a barrier and left twenty-one
# published clauses saying it had none -- the README's opening lines, five
# modules' introductory prose, two sentences in `docs/CONTRACT.md`, and the JSON
# body served to any non-GET caller. Every one was TRUE at the previous `main`
# and false from the commit that added `act.py`.
#
# Two of them were repaired the round before this one, and the reason the other
# nineteen survived is the reason this block exists: the two that were fixed were
# the two somebody had ENUMERATED BY PATH, and the check written to hold them was
# a phrase match against one file. A list of sites is a guess with the shape of a
# specification.
#
# THE EXPECTATION HERE IS DERIVED FROM THE MEASUREMENT, not from a second copy of
# the assertion: `ACTS` is what says whether any authorisation in this package
# reaches a vend, and it is read below rather than assumed.

#: The BLANKET phrasings -- each one claims it of the PACKAGE, the AGENT or "this
#: build", with no scope on it. Every one of them is a sentence that was in the
#: tree at `35e0be1`, and after Z17 none may appear anywhere.
#:
#: Deliberately NOT in this list: `opens nothing`, `no opening authority` and
#: `no vend route` on their own. Those survive in true, SCOPED sentences -- about
#: the monitor, about the capture process, about a site that declared no act
#: token, and inside the fail-control break that plants the old banner back --
#: and a query that fired on them would have to carry an allow-list of about
#: thirty strings, which is the checklist this block exists to avoid.
CANNOT_ACT = re.compile(
    r"no client in this package capable"
    r"|client in this package (?:still )?cannot build"
    r"|cannot build a request that is not a[^A-Za-z]*GET"
    r"|(?:neither|none of them) can open a barrier"
    r"|it is never an act"
    r"|has no route that could"
    r"|this (?:version|build) cannot (?:move|operate) (?:a|the) barrier"
    r"|^\W*IT OPENS NOTHING"
    # Four more, added after the first run of this sweep against `35e0be1`
    # showed it caught fifteen of the twenty-two clauses and not these. Each is
    # narrow on purpose and each was checked against the survivors below: the
    # true sentences say "the MONITOR holds no client", "THIS PROCESS holds no
    # client", "it opens nothing" and "Neither calls a vend", none of which
    # these reach.
    r"|None has opening authority"
    r"|Nothing here calls a vend"
    r"|the agent holds no client"
    r"|and open nothing\b",
    re.IGNORECASE | re.MULTILINE,
)

#: Every deleted sentence, verbatim from `35e0be1`, as the POSITIVE CONTROL for
#: the sweep above. An absence claim is a claim about a SEARCH; without these,
#: `test_nothing_claims_this_package_cannot_act` asserts a fact about a regex.
DELETED_AT_35E0BE1 = (
    # README.md:3
    "It ships **three processes**, and none of them can open a barrier.",
    # README.md:15
    "An authorisation is a record of what somebody said. It is never an act.",
    # README.md:46
    "There is no client in this package capable of a method other than `GET`",
    # README.md:50
    "one fixed sentence to that person saying this version cannot operate the barrier.",
    # src/gate_agent/__init__.py:3
    "It ships TWO processes and neither can open a barrier.",
    # src/gate_agent/__init__.py:20
    "There is no client in this package capable of another method",
    # src/gate_agent/agent.py:8
    "**IT OPENS NOTHING.** An authorisation is a RECORD of what a person said.",
    # src/gate_agent/agent.py:11
    "this package cannot build a request that is not a `GET`.",
    # src/gate_agent/cli.py:7
    "**Three processes, beside each other, and none of them can open a barrier.**",
    # src/gate_agent/contract.py:12 and docs/CONTRACT.md:19 / :720
    "there is no client in this package capable of a method other than `GET`",
    # src/gate_agent/contract.py:1281
    "package still cannot build a request that is not a GET.",
    # src/gate_agent/contract.py:1356
    "opens nothing, because this package has no route that could",
    # README.md:43
    "**None has opening authority.**",
    # README.md:44
    "Nothing here calls a vend, resolves a transit, or writes to a lane.",
    # src/gate_agent/agent.py:1733
    "Record it, tell both sides what it means, and open nothing.",
    # src/gate_agent/agent_service.py:46
    "the agent holds no client capable of a method other than `GET`",
)


def test_the_sweep_for_a_package_that_cannot_act_sees_every_deleted_sentence():
    """THE POSITIVE CONTROL, and it comes first because the sweep needs one.

    Each sentence is quoted from the tree at `35e0be1`. If any of them stops
    matching, the sweep below has narrowed under somebody's edit and its silence
    means nothing.
    """
    blind = [one for one in DELETED_AT_35E0BE1 if not CANNOT_ACT.search(one)]
    assert blind == [], "the sweep cannot see what it swept for:\n  " + "\n  ".join(blind)


def test_the_sweep_does_not_fire_on_the_scoped_sentences_that_survive():
    """THE OTHER HALF OF THE CONTROL: a query that fires on everything is not one.

    These are true at this tip and must stay: the monitor's and the capture
    process's own claims, and the round-5 configuration the contract calls
    supported.
    """
    survivors = (
        "**This surface is READ ONLY, and the MONITOR behind it has no opening authority.**",
        "The capture process: it photographs a lane, and it opens nothing.",
        "an agent with an act table and no act token opens nothing.",
        "The monitor holds no client capable of a method other than `GET`.",
        "This process holds no client capable of another method.",
        "Neither calls a vend, resolves a transit, or writes to a lane.",
        "a site that declares neither still opens nothing",
        "  OPENS NOTHING: no lane here declares an act token and no intercom declares a relay",
    )
    fired = [one for one in survivors if CANNOT_ACT.search(one)]
    assert fired == [], "the sweep is too wide and would force an allow-list:\n  " + "\n  ".join(
        fired
    )


def test_nothing_claims_this_package_cannot_act():
    """Z17. The expectation is DERIVED: `ACTS` is non-empty, so no file may say it is.

    QUERY: `CANNOT_ACT` above, over every `.py`, `.md` and `.toml` this
    repository publishes, every hit accounted for one by one rather than counted.

    **What this cannot see**, stated beside the absence claim because a word
    query has exactly one blind spot and it is the important one: a NEW false
    sentence, written in words nobody has used yet. That is why
    `test_what_this_can_act_on_is_derived_in_one_place` below is structural and
    this one is not, and why the receipt says so too.
    """
    from gate_agent.contract import ACTS

    assert ACTS, (
        "THE PREMISE OF THIS TEST: this package holds authorisations that act. "
        "If `ACTS` is ever emptied, these sentences become true again and this "
        "test must be rewritten rather than deleted."
    )
    files = published()
    # THE TWO FAIL-CONTROL SCRIPTS ARE EXCLUDED, and the reason is the same one
    # that excludes this file from `published()` itself: their whole content is
    # sentences that MUST be false, because they are the broken versions each
    # guarantee is measured against. `the_readme_says_the_package_cannot_open_a_
    # barrier` carries the README's deleted sentence verbatim, on purpose.
    # Sweeping them for false sentences is a category error, not an allowance.
    harness = ("scripts/agent_fail_control.py", "scripts/monitor_fail_control.py")
    for name in harness:
        assert name in files, f"{name} is not in the swept set, so this exclusion excludes nothing"
        del files[name]

    hits = []
    for name, text in files.items():
        for line, sentence in sentences(text):
            if CANNOT_ACT.search(sentence):
                hits.append(f"{name}:~{line}: {sentence.strip()[:160]!r}")
    assert hits == [], (
        "this package can command a vend and these say it cannot:\n  " + "\n  ".join(hits)
    )


def test_the_readme_is_in_the_swept_set():
    """The sweep above is worthless on a tree that does not carry the README.

    `published()` globs `README.md` and a missing file is silently one fewer
    file, not a failure -- so the fail-control's staged copy of the tree has to
    carry it, and this is what goes red if somebody stops staging it. It is the
    same shape as every other rule here: a check that cannot fail is not one.
    """
    assert "README.md" in published(), (
        "README.md is not in the swept set on this tree, so the Z17 sweep above "
        "measured every file except the one a reader opens first"
    )
