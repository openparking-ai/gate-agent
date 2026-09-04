"""THE CASE TABLE: every row, derived from a payload a real lane SERVED.

Not from a `LaneReading` built by hand. The thing being measured is the whole
path -- a lane publishes a decision over HTTP, the agent reads it through the
contract, and a driver hears a sentence -- and a test that constructed the middle
of that by hand would leave the read untested while looking like coverage.

Three separate questions, and they are different:

  1. **Does each row of the table produce its case?** One test per row, against a
     served payload, with the case asserted through `agent.session.case`.
  2. **Is the reason subset this package holds the same one the lane emits?**
     `src/` may not import `lane_controller`, so the subset is a COPY here --
     and a copy is only safe while something compares it to the original. The
     comparison is done against the INSTALLED package, in both directions.
  3. **Does an unrecognised reason escalate rather than being mapped?** With its
     control: a lane's own vocabulary, which is not in our subset and must not
     be guessed at.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from conftest import INTERCOM_ACCOUNT, agent_config_for, agent_for
from fake_ua import FakeUa
from foreign_lane import ForeignLane, decided_at
from foreign_lane import make_server as foreign_server
from gate_agent.cases import (
    DEFAULT_DECISION_MAX_AGE_SECONDS,
    FALLBACK_CASES,
    OUTCOMES,
    REQUIRED_FALLBACK_REASONS,
    TRANSIT_STATES,
    LaneReading,
    derive,
)
from gate_agent.contract import AgentCase
from serving import serving

INTERCOM = "sip:door1@10.0.0.9"

#: The clock the direct `derive()` calls below are given, and the stamp that is
#: FRESH against it. Both produced from one moment, so "fresh" here is a
#: property of the pair rather than of when the suite happened to run.
NOW = datetime(2026, 8, 30, 14, 3, 11, tzinfo=UTC)
FRESH = NOW.isoformat()
STALE = (NOW - timedelta(seconds=DEFAULT_DECISION_MAX_AGE_SECONDS + 1)).isoformat()


def decision(
    outcome: str, reason: str, transit: str = "none", age_seconds: float = 0.0, at=...
) -> tuple[dict, dict]:
    """One served decision. `age_seconds` is how OLD the lane says it is.

    Zero by default, which is the fresh side of `[cases]
    decision_max_age_seconds`; `at=` replaces the moment outright, which is how
    an unreadable one is asked for.
    """
    return (
        {
            "outcome": outcome,
            "reason": reason,
            "fallback": reason if reason in REQUIRED_FALLBACK_REASONS else None,
            "cause": None,
            "presence": None,
            "at": decided_at(age_seconds) if at is ... else at,
            "read_ref": None,
        },
        {"state": transit, "since": None},
    )


#: One row of `docs/CONTRACT.md`'s case table each. The last column is what a
#: driver hears about, so a wrong row here is a driver told the wrong thing.
ROWS = [
    ("fallback", "engine_unreachable", "none", (), AgentCase.IDENTIFICATION_UNAVAILABLE),
    ("fallback", "no_plate_read", "none", (), AgentCase.PLATE_NOT_READ),
    ("fallback", "low_confidence", "none", (), AgentCase.PLATE_UNCLEAR),
    ("fallback", "unknown_vehicle", "none", (), AgentCase.VEHICLE_NOT_RECOGNISED),
    ("fallback", "stale_rules", "none", (), AgentCase.RULES_UNAVAILABLE),
    ("deny", "deny", "none", (), AgentCase.ENTRY_REFUSED),
    ("no_vehicle", "no_vehicle", "none", (), AgentCase.VEHICLE_NOT_DETECTED),
    ("allow", "allow", "held", (), AgentCase.ENTRY_NOT_CONFIRMED),
    ("allow", "allow", "unconfirmable", (), AgentCase.ENTRY_NOT_CONFIRMED),
    ("allow", "allow", "confirmed", (), AgentCase.NOTHING_TO_DO),
    ("allow", "allow", "pending", (), AgentCase.NOTHING_TO_DO),
    ("fallback", "barrier_operator_intervened", "none", (), AgentCase.UNRECOGNISED_REASON),
    # A malfunction that is not `never_alarm` comes FIRST, whatever the decision
    # says: a broken lane's last decision is not a fact about the vehicle
    # standing at it.
    ("allow", "allow", "confirmed", ("boom_did_not_rise",), AgentCase.MALFUNCTION_ACTIVE),
]

#: The rows the decision's AGE decides. Separate from `ROWS` because they need a
#: fixture input on the OTHER side of `[cases] decision_max_age_seconds`, and
#: every row above is on the fresh side by construction.
AGE_ROWS = [
    # The one that costs the customer everything: `nothing_to_do` is the only
    # case that reaches nobody, and this is the reading that used to produce it.
    (3600.0, "allow", "allow", "confirmed", AgentCase.STALE_DECISION),
    (3600.0, "allow", "allow", "pending", AgentCase.STALE_DECISION),
    # A stale decision is stale whatever it said. The lane is not describing the
    # person standing at the barrier in any of them.
    (3600.0, "deny", "deny", "none", AgentCase.STALE_DECISION),
    (3600.0, "fallback", "low_confidence", "none", AgentCase.STALE_DECISION),
    (3600.0, "no_vehicle", "no_vehicle", "none", AgentCase.STALE_DECISION),
    # And the other side of the same threshold, from the same fixture.
    (0.0, "allow", "allow", "confirmed", AgentCase.NOTHING_TO_DO),
    (DEFAULT_DECISION_MAX_AGE_SECONDS - 5, "allow", "allow", "confirmed",
     AgentCase.NOTHING_TO_DO),
]


@pytest.fixture
def lane():
    lane = ForeignLane()
    with serving(foreign_server(lane)) as url:
        yield lane, url


def case_of(tmp_path, lane, url, peer=INTERCOM):
    """Answer one call at `url` and return the case the driver was told about."""
    ua = FakeUa()
    agent = agent_for(agent_config_for(tmp_path, lane_url=url), ua)
    ua.incoming(peer, account_user=INTERCOM_ACCOUNT)
    agent.poll()
    assert agent.session is not None, "the call was not answered at all"
    return agent.session.case, agent, ua


@pytest.mark.parametrize(
    "outcome,reason,transit,active,expected",
    ROWS,
    ids=[f"{row[0]}-{row[1]}-{row[2]}-{'+'.join(row[3]) or 'clean'}" for row in ROWS],
)
def test_every_row_of_the_case_table(tmp_path, lane, outcome, reason, transit, active, expected):
    served, url = lane
    served.decision, served.transit = decision(outcome, reason, transit)
    for code in active:
        served.states[code] = "active"
        served.sources[code] = "measured"
    case, _agent, _ua = case_of(tmp_path, served, url)
    assert case is expected


@pytest.mark.parametrize(
    "age,outcome,reason,transit,expected",
    AGE_ROWS,
    ids=[f"{row[0]:.0f}s-{row[1]}-{row[3]}" for row in AGE_ROWS],
)
def test_the_age_of_the_decision_decides_the_case(
    tmp_path, lane, age, outcome, reason, transit, expected
):
    """A decision older than the site's bound is a person, whatever it said.

    Both sides of `[cases] decision_max_age_seconds` come out of the SAME
    fixture, which is the only way this threshold is exercised rather than
    described: the rows above are all on the fresh side by construction.
    """
    served, url = lane
    served.decision, served.transit = decision(outcome, reason, transit, age_seconds=age)
    case, _agent, _ua = case_of(tmp_path, served, url)
    assert case is expected


@pytest.mark.parametrize(
    "stamp",
    [None, "", "not a moment", "2026-08-30T14:03:11.482913", 17],
    ids=["missing", "empty", "unparseable", "naive", "not-a-string"],
)
def test_a_decision_whose_moment_cannot_be_read_escalates(tmp_path, lane, stamp):
    """Unreadable is NOT fresh. It is the catch-all, which is a person.

    The naive case is the round-4 rule again: a moment with no timezone compared
    against an aware one is a guess about which machine it came from.
    """
    served, url = lane
    served.decision, served.transit = decision("allow", "allow", "confirmed", at=stamp)
    case, _agent, _ua = case_of(tmp_path, served, url)
    assert case is AgentCase.UNRECOGNISED_REASON
    # The control: the SAME reading with a readable moment is not the catch-all,
    # so the assertion is about the stamp and not about the rest of the payload.
    served.decision, served.transit = decision("allow", "allow", "confirmed")
    case, _agent, _ua = case_of(tmp_path, served, url)
    assert case is AgentCase.NOTHING_TO_DO


def test_a_decision_stamped_in_the_future_is_fresh_and_not_stale(tmp_path, lane):
    """The two clocks. A NEGATIVE age is reachable, and it is not staleness.

    Sending a driver to a person because the lane's clock is ahead of this
    process's would be acting on an offset nobody has measured. The contract
    says so in one copy and this is that copy exercised.
    """
    served, url = lane
    served.decision, served.transit = decision(
        "allow", "allow", "confirmed", age_seconds=-3600.0
    )
    case, _agent, _ua = case_of(tmp_path, served, url)
    assert case is AgentCase.NOTHING_TO_DO


def test_the_two_clocks_note_has_one_copy(tmp_path):
    """The sentence in `docs/CONTRACT.md` IS `cases.DECISION_AGE_NOTE`.

    Two copies drift, and the hand-written one is always the one that lies. The
    document quotes it as a block quote and renders `--` as an em dash, so both
    are normalised away before the comparison and nothing else is.
    """
    from pathlib import Path as _Path

    from gate_agent.cases import DECISION_AGE_NOTE

    document = _Path(__file__).resolve().parent.parent / "docs" / "CONTRACT.md"
    quoted = " ".join(
        line.lstrip(">").strip()
        for line in document.read_text(encoding="utf-8").splitlines()
        if line.startswith(">")
    )

    def flat(text: str) -> str:
        return " ".join(text.replace("`", "").replace("\u2014", "--").split())

    assert flat(DECISION_AGE_NOTE) in flat(quoted), (
        "the contract's two-clocks note is not `cases.DECISION_AGE_NOTE`. One copy, or the "
        "hand-written one starts lying."
    )


def test_a_never_alarm_code_is_not_a_malfunction_case(tmp_path, lane):
    """The control on the row above, and it is the one that costs a customer.

    `reference_not_recognised` is `never_alarm` ON THE WIRE, and one of the
    things it covers is an ordinary car arriving. Read as a fault it would tell
    every driver at a low-texture entrance that their entrance is broken because
    they turned up.
    """
    served, url = lane
    served.decision, served.transit = decision("allow", "allow", "confirmed")
    served.states["reference_not_recognised"] = "active"
    served.sources["reference_not_recognised"] = "measured"
    case, _agent, _ua = case_of(tmp_path, served, url)
    assert case is AgentCase.NOTHING_TO_DO
    # And the control: the same code with `never_alarm` false IS a malfunction,
    # so the assertion above is about the flag and not about the code's name.
    served.never_alarm_override["reference_not_recognised"] = False
    case, _agent, _ua = case_of(tmp_path, served, url)
    assert case is AgentCase.MALFUNCTION_ACTIVE


def test_a_lane_that_cannot_be_reached_is_lane_unavailable(tmp_path):
    """No server at all. The lane cannot be asked, and the driver is told so."""
    ua = FakeUa()
    agent = agent_for(agent_config_for(tmp_path, lane_url="http://127.0.0.1:1"), ua)
    ua.incoming(INTERCOM, account_user=INTERCOM_ACCOUNT)
    agent.poll()
    assert agent.session.case is AgentCase.LANE_UNAVAILABLE


def test_a_lane_on_an_unreadable_version_is_lane_unavailable(tmp_path, lane, monkeypatch):
    """A version this build does not know is refused, not half-read."""
    served, url = lane
    monkeypatch.setenv("BREAK_FOREIGN_LANE", "future_version")
    case, _agent, _ua = case_of(tmp_path, served, url)
    assert case is AgentCase.LANE_UNAVAILABLE
    # The control: without the break, the same lane and the same call produce a
    # case derived from the decision, so the assertion is about the version.
    monkeypatch.delenv("BREAK_FOREIGN_LANE")
    case, _agent, _ua = case_of(tmp_path, served, url)
    assert case is not AgentCase.LANE_UNAVAILABLE


def test_a_standalone_intercom_is_a_human_case_from_the_first_second(tmp_path):
    ua = FakeUa()
    agent = agent_for(agent_config_for(tmp_path, standalone=True), ua)
    ua.incoming(INTERCOM, account_user=INTERCOM_ACCOUNT)
    agent.poll()
    assert agent.session.case is AgentCase.STANDALONE


def test_a_lane_that_has_decided_nothing_escalates(tmp_path, lane):
    """`decision: null` is what a lane serves after a restart, and it has no row.

    It is not `nothing_to_do` and it is not a fallback: the lane has nothing to
    say about this vehicle, so it is the catch-all, which is a person.
    """
    served, url = lane
    served.decision, served.transit = None, {"state": "none", "since": None}
    case, _agent, _ua = case_of(tmp_path, served, url)
    assert case is AgentCase.UNRECOGNISED_REASON


def test_the_reason_subset_is_the_lanes_own(tmp_path):
    """The copy in `src/` against the ORIGINAL, in both directions.

    `src/` may not import `lane_controller` -- that import is what would make
    this package something other than an ordinary client of the contract -- so
    the required reasons are a copy. A copy is safe exactly as long as something
    compares it to the original, and a test is the only place that can, because
    the suite has the real package installed.
    """
    from lane_controller.contract import REQUIRED_REASONS

    fallbacks = {reason for reason in REQUIRED_REASONS if reason not in OUTCOMES}
    assert set(REQUIRED_FALLBACK_REASONS) == fallbacks, (
        "the fallback reasons this package branches on are no longer the ones the lane "
        "emits. A reason added there without an answer here is a driver escalated with no "
        "case; a reason invented here is a branch nothing can reach."
    )
    assert set(FALLBACK_CASES) == set(REQUIRED_FALLBACK_REASONS)


def test_the_outcomes_and_transits_are_the_lanes_own(tmp_path):
    """Same question for the two sets the lane contract publishes as CLOSED."""
    from lane_controller.contract import OUTCOMES as LANE_OUTCOMES
    from lane_controller.contract import TransitState

    assert set(OUTCOMES) == set(LANE_OUTCOMES)
    assert set(TRANSIT_STATES) == {state.value for state in TransitState}


def test_every_case_is_reachable_from_some_reading():
    """No member of the closed set is unreachable, and none is reached twice.

    A case nobody can produce is a sentence in ninety files that no driver will
    ever hear, and it would sit there reading like coverage.
    """
    def case(**fields) -> AgentCase:
        fields.setdefault("decision_at", FRESH)
        return derive(LaneReading(**fields), now=NOW)

    reached = {
        derive(LaneReading(lane=None), now=NOW),
        derive(LaneReading(lane="entry", readable=False), now=NOW),
        derive(LaneReading(lane="entry", readable=True, malfunctions=("x",)), now=NOW),
        *(
            case(lane="entry", readable=True, outcome="fallback", reason=reason)
            for reason in REQUIRED_FALLBACK_REASONS
        ),
        case(lane="entry", readable=True, outcome="deny", reason="deny"),
        case(lane="entry", readable=True, outcome="no_vehicle", reason="no_vehicle"),
        case(lane="entry", readable=True, outcome="allow", reason="allow", transit="held"),
        case(lane="entry", readable=True, outcome="allow", reason="allow", transit="confirmed"),
        case(lane="entry", readable=True, outcome="fallback", reason="theirs"),
        # The two the decision's AGE decides, and they are the whole of X3: a
        # stale decision is a person, and one whose moment cannot be read is the
        # catch-all rather than being treated as fresh.
        derive(
            LaneReading(
                lane="entry", readable=True, outcome="allow", reason="allow",
                transit="confirmed", decision_at=STALE,
            ),
            now=NOW,
        ),
        derive(
            LaneReading(
                lane="entry", readable=True, outcome="allow", reason="allow",
                transit="confirmed", decision_at=None,
            ),
            now=NOW,
        ),
    }
    assert reached == set(AgentCase), sorted(
        one.value for one in set(AgentCase) - reached
    )


# ---------------------------------------------------------------------------
# The two sets this package COPIES from the lane contract
# ---------------------------------------------------------------------------


def test_the_vend_authorities_are_the_lanes_own_in_both_directions():
    """`ACTS` names the lane's `VendAuthority` values, and this holds the copy.

    The copy exists because it has to: this package is a CONSUMER of that
    contract and may not import it, so an authority is a string here. What that
    would cost if it drifted is a vend the lane answers `400` at the moment a
    driver is waiting, so the copy is compared against the enum out of the
    INSTALLED package, both ways.
    """
    from lane_controller.contract import VendAuthority

    from gate_agent.contract import ACTS

    assert set(ACTS.values()) == {
        VendAuthority.HUMAN_OPEN_NOW.value,
        VendAuthority.HUMAN_OPEN_AND_FLAG.value,
    }
    # `display_code_confirmed` is the third and it is NOT in `ACTS`: it is not
    # something a human keys, it is what a PRESS means. It is used by name in
    # `agent._confirm_ticket`, and this is where that name is held to the enum.
    assert VendAuthority.DISPLAY_CODE_CONFIRMED.value == "display_code_confirmed"
    import gate_agent.agent as agent_module

    source = Path(agent_module.__file__).read_text(encoding="utf-8")
    assert '"display_code_confirmed"' in source
    # And every authority the lane publishes is one this agent can produce, so a
    # fourth added there arrives here as a red test rather than as silence.
    assert set(ACTS.values()) | {VendAuthority.DISPLAY_CODE_CONFIRMED.value} == {
        one.value for one in VendAuthority
    }


def test_the_vend_refusal_codes_are_the_lanes_own_in_both_directions():
    """One operator sentence per refusal code, and the set is the lane's.

    A code the lane can answer and this build has no words for is a person told
    "it was refused" and not told what to do about it. A sentence for a code
    that does not exist is a file nobody will ever hear.
    """
    from lane_controller.contract import VendRefusal

    from gate_agent.contract import VEND_REFUSALS
    from gate_agent.lines import OPERATOR_LINES, TEXT

    assert set(VEND_REFUSALS) == {one.value for one in VendRefusal}, {
        "in the lane and not here": sorted(
            {one.value for one in VendRefusal} - set(VEND_REFUSALS)
        ),
        "here and not in the lane": sorted(
            set(VEND_REFUSALS) - {one.value for one in VendRefusal}
        ),
    }
    # AND ONE SENTENCE EACH, keyed one-to-one, generated from the same list on
    # both sides -- a missing key and an orphan key each fail.
    #
    # PLUS EXACTLY ONE MORE, and it is not a member of that set: the sentence
    # for a code this build has no words for. `VEND_REFUSALS` is our lane's
    # vocabulary, which is the right check for our lane and no check at all for
    # the third-party seat this module sits in -- so a foreign lane's own code
    # reaches a person as "the entrance gave a reason I have no words for"
    # rather than as silence.
    from gate_agent.lines import UNKNOWN_REFUSAL

    expected = {f"operator.vend_refused.{code}" for code in VEND_REFUSALS}
    assert UNKNOWN_REFUSAL not in expected, (
        "the lane has grown a refusal code called `unknown`, which now collides with the "
        "line for a code this build has no words for"
    )
    published = {line for line in OPERATOR_LINES if line.startswith("operator.vend_refused.")}
    assert published == expected | {UNKNOWN_REFUSAL}
    for line in expected | {UNKNOWN_REFUSAL}:
        assert TEXT[line]["en"].strip(), line
        assert TEXT[line]["es-ES"].strip(), line
