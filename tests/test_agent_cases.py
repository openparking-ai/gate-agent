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

import pytest

from conftest import agent_config_for, agent_for
from fake_ua import FakeUa
from foreign_lane import ForeignLane
from foreign_lane import make_server as foreign_server
from gate_agent.cases import (
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


def decision(outcome: str, reason: str, transit: str = "none") -> tuple[dict, dict]:
    return (
        {
            "outcome": outcome,
            "reason": reason,
            "fallback": reason if reason in REQUIRED_FALLBACK_REASONS else None,
            "cause": None,
            "presence": None,
            "at": "2026-08-30T14:03:11.482913+00:00",
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


@pytest.fixture
def lane():
    lane = ForeignLane()
    with serving(foreign_server(lane)) as url:
        yield lane, url


def case_of(tmp_path, lane, url, peer=INTERCOM):
    """Answer one call at `url` and return the case the driver was told about."""
    ua = FakeUa()
    agent = agent_for(agent_config_for(tmp_path, lane_url=url), ua)
    ua.incoming(peer)
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
    ua.incoming(INTERCOM)
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
    ua.incoming(INTERCOM)
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
    reached = {
        derive(LaneReading(lane=None)),
        derive(LaneReading(lane="entry", readable=False)),
        derive(LaneReading(lane="entry", readable=True, malfunctions=("x",))),
        *(
            derive(LaneReading(lane="entry", readable=True, outcome="fallback", reason=reason))
            for reason in REQUIRED_FALLBACK_REASONS
        ),
        derive(LaneReading(lane="entry", readable=True, outcome="deny", reason="deny")),
        derive(
            LaneReading(lane="entry", readable=True, outcome="no_vehicle", reason="no_vehicle")
        ),
        derive(
            LaneReading(
                lane="entry", readable=True, outcome="allow", reason="allow", transit="held"
            )
        ),
        derive(
            LaneReading(
                lane="entry", readable=True, outcome="allow", reason="allow", transit="confirmed"
            )
        ),
        derive(LaneReading(lane="entry", readable=True, outcome="fallback", reason="theirs")),
    }
    assert reached == set(AgentCase), sorted(
        one.value for one in set(AgentCase) - reached
    )
