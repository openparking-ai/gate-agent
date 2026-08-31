"""The ticket, the press, and the vend -- against OUR REAL LANE, over a socket.

**The lane here is the real `lane_controller`, serving its real vend route, with
its real refusals.** That is the only fixture worth having for this round: the
whole design rests on the LANE deciding and the agent asserting, and a fake lane
that accepted whatever it was sent would make every test below a claim about two
fakes agreeing.

The questions, and they are the attacker's:

  * **What puts a code on a screen?** Only a decision the lane made, in one of
    four cases, with presence TRUE. Not `None`, which is "nobody measured".
  * **What turns a code into an open barrier?** A press at that barrier, inside
    the window, and then the LANE's own seven refusals.
  * **What stops a second one?** The help window, before the lane's
    `already_completed` -- which is the backstop, not the design.
  * **Where does the reference go?** Into the ticket record, and nowhere else:
    not an event, not a read route, not a log line.
"""

from __future__ import annotations

import io
import json
import logging

import pytest
from lane_controller import VehicleIdentity

from conftest import INTERCOM_ACCOUNT, FakeClock, agent_config_for, agent_for
from fake_ua import FakeUa
from gate_agent.cases import TICKET_CASES
from gate_agent.config import Target, TicketSettings
from gate_agent.contract import AgentCase, AgentEventKind, TargetKind
from gate_agent.display import Geometry
from gate_agent.tickets import ISSUED, VENDED, VOIDED, TicketStore
from ours import our_lane, our_server
from serving import serving

ACT_TOKEN = "an-act-token-for-a-test-0000"
SIGNING_KEY = b"a-signing-key-long-enough-for-the-floor"
INTERCOM = "sip:door1@10.0.0.9"


class FakeScreen:
    """A display that RECORDS the frames it was shown, and can refuse one.

    A real `Display` writes to a file and `test_display.py` proves that path end
    to end through an independent decoder. What these tests need is which
    payload was shown and when it went away, so this holds the frames -- and it
    can be made to refuse, which is the one display failure that changes what a
    driver gets.
    """

    def __init__(self) -> None:
        self.name = "front"
        self.geometry = Geometry(width=320, height=240, bits_per_pixel=32, stride=1280)
        self.frames: list = []
        self.blanked = 0
        self.refuse = False

    def show(self, bitmap) -> None:
        if self.refuse:
            from gate_agent.display import DisplayUnavailable

            raise DisplayUnavailable("this screen is not there")
        self.frames.append(bitmap)

    def blank(self) -> None:
        self.blanked += 1


#: How many arrivals each lane in this file can serve. Three, because the
#: longest test drives one decision to establish the agent's cursor, a second to
#: put a ticket up, and a third to void it.
ARRIVALS = 3


def a_lane(outcome="fallback", reason="no_plate_read", presence=True):
    """OUR lane, driven to a decision of the shape a test needs.

    The identity is fed through the real `StubVehicleIdentifier`, so the
    decision, its `at`, its `presence` and its `completed` are all the lane's
    own -- nothing here writes a payload.
    """
    identities = {
        ("fallback", "no_plate_read"): VehicleIdentity(plate=None, presence=presence),
        ("fallback", "low_confidence"): VehicleIdentity(
            plate="MARGINAL1", confidence=0.10, presence=presence
        ),
        ("deny", None): VehicleIdentity(plate="DENIEDME", confidence=0.99, presence=presence),
        ("no_vehicle", None): VehicleIdentity(plate=None, presence=False),
    }
    controller = our_lane(
        identities=[identities[(outcome, reason)]] * ARRIVALS, arrivals=ARRIVALS
    )
    if outcome == "deny":
        controller.cache.default_action = "deny"
    return controller


def arrive(agent, controller, times=4):
    """A vehicle arrives at this lane WHILE the agent is watching.

    The order is the whole point and it is the product's, not the fixture's: a
    first read of a lane's events adopts its cursor and acts on nothing already
    in the window, because those cars have gone. So the agent polls once to take
    up its position, the lane then decides, and the next poll is the one that
    can put a code on a screen.
    """
    agent.poll()
    controller.run_once()
    for _ in range(times):
        agent.poll()


def agent_on(tmp_path, url, *, act_token=ACT_TOKEN, display=True, tickets=True, clock=None):
    """An agent with a lane, a screen and a key -- or without any of them."""
    screen = FakeScreen()
    base = agent_config_for(tmp_path, lane_url=url)
    from dataclasses import replace

    config = replace(
        base,
        lanes=(
            Target(
                name="entry",
                kind=TargetKind.LANE,
                url=url,
                poll_seconds=0.0,
                act_token=act_token,
                timeout_seconds=5.0,
            ),
        ),
        intercoms=(
            replace(base.intercoms[0], lane="entry", display="front" if display else None),
        ),
        displays={"front": screen} if display else {},
        tickets=(
            TicketSettings(signing_key=SIGNING_KEY, directory=tmp_path / "tickets")
            if tickets
            else None
        ),
        driver_languages=("en",),
    )
    ua = FakeUa()
    agent = agent_for(config, ua, clock=clock or FakeClock())
    return agent, ua, screen


def events_of(agent, kind) -> list[dict]:
    return [
        event
        for event in agent.events(0).to_dict()["events"]
        if event["kind"] == kind.value
    ]


def pump(agent, times=4):
    for _ in range(times):
        agent.poll()


# ---------------------------------------------------------------------------
# What puts a code on a screen
# ---------------------------------------------------------------------------


def test_a_decision_in_a_ticket_case_with_presence_puts_a_code_on_the_screen(tmp_path):
    """The whole of what offers a ticket, and every clause of it is load-bearing.

    Against OUR REAL LANE, so the decision, its moment and its presence are the
    lane's own -- nothing here writes a payload. What that lane produces for a
    plate it could not read is `low_confidence`, which is `plate_unclear`; the
    other three ticket cases are covered against the foreign lane below, where a
    test can choose the reason.
    """
    server = our_server(lane := a_lane(), act_token=ACT_TOKEN, arrive=False)
    with serving(server) as url:
        agent, _ua, screen = agent_on(tmp_path, url)
        arrive(agent, lane)
        issued = events_of(agent, AgentEventKind.TICKET_ISSUED)
        assert len(issued) == 1, agent.events(0).to_dict()["events"]
        assert issued[0]["case"] == AgentCase.PLATE_UNCLEAR.value
        assert issued[0]["lane"] == "entry"
        assert screen.frames, "a ticket was issued and nothing was drawn"
        # And it really is the LANE'S OWN decision this was minted against.
        assert agent._pending["entry"].decision_at == lane.last_decision_at
        # The RECORD exists and is the site's own, before anything else happens.
        store = TicketStore(tmp_path / "tickets")
        record = store.read(issued[0]["ticket_id"])
        assert record is not None and record.state == ISSUED
        assert record.lane == "entry" and record.site == "site-1"


def a_foreign_lane(reason: str, presence=True, outcome="fallback"):
    """A lane whose payload a test CHOOSES, written from the document.

    Our own lane cannot produce every fallback reason on demand -- `no_plate_read`
    needs an identification service that answered nothing, and
    `engine_unreachable` needs one that is off. This stub can, and it is the
    right fixture for the CASE TABLE: what is being measured there is the
    mapping from a lane's words to a driver's, and a lane that is not ours is
    the one that will send them.
    """
    from foreign_lane import ForeignLane, decided_at

    lane = ForeignLane()
    lane.window = 64
    lane.decision = {
        "outcome": outcome,
        "reason": reason,
        "fallback": reason if outcome == "fallback" else None,
        "cause": None,
        "presence": presence,
        "at": decided_at(),
        "read_ref": None,
        "completed": False,
    }
    return lane


def foreign_decides(agent, lane, reason, presence=True, outcome="fallback"):
    """Take up the cursor, then let the lane decide, exactly as `arrive` does."""
    from foreign_lane import decided_at

    agent.poll()
    lane.decision = {
        "outcome": outcome,
        "reason": reason,
        "fallback": reason if outcome == "fallback" else None,
        "cause": None,
        "presence": presence,
        "at": decided_at(),
        "read_ref": None,
        "completed": False,
    }
    lane.record("decision", "2026-08-31T14:00:00+00:00", {"outcome": outcome})
    for _ in range(4):
        agent.poll()


@pytest.mark.parametrize(
    ("reason", "case", "offered"),
    [
        # THE FOUR that get a ticket: a lane that made a decision and could not
        # say who this vehicle is.
        ("engine_unreachable", AgentCase.IDENTIFICATION_UNAVAILABLE, True),
        ("no_plate_read", AgentCase.PLATE_NOT_READ, True),
        ("low_confidence", AgentCase.PLATE_UNCLEAR, True),
        ("unknown_vehicle", AgentCase.VEHICLE_NOT_RECOGNISED, True),
        # AND THE ONE THAT DOES NOT, from the same branch of the same table: the
        # lane could not check its rules, which is not a question about who this
        # vehicle is.
        ("stale_rules", AgentCase.RULES_UNAVAILABLE, False),
    ],
)
def test_the_case_decides_whether_a_ticket_is_offered(tmp_path, reason, case, offered):
    """Every ticket case, and a neighbour of them that is not one.

    A table with only the four in it would be satisfied by an agent that offered
    a ticket for every fallback there is.
    """
    from foreign_lane import make_server as foreign_server

    lane = a_foreign_lane(reason)
    with serving(foreign_server(lane)) as url:
        agent, _ua, screen = agent_on(tmp_path, url, act_token=None)
        foreign_decides(agent, lane, reason)
        issued = events_of(agent, AgentEventKind.TICKET_ISSUED)
        assert bool(issued) is offered, (reason, agent.events(0).to_dict()["events"])
        assert bool(screen.frames) is offered
        if offered:
            assert issued[0]["case"] == case.value


def test_a_deny_never_gets_a_ticket(tmp_path):
    """The lane KNOWS who this is and said no. A ticket would overturn a rule,
    and only a human may do that."""
    server = our_server(lane := a_lane("deny", None), act_token=ACT_TOKEN, arrive=False)
    with serving(server) as url:
        agent, _ua, screen = agent_on(tmp_path, url)
        arrive(agent, lane)
        assert events_of(agent, AgentEventKind.TICKET_ISSUED) == []
        assert screen.frames == []


def test_an_unmeasured_presence_never_gets_a_ticket(tmp_path):
    """`None` IS NOT `True`, and this is the field where that costs the most.

    An unmeasured presence putting a code on a screen is the fraud this project
    has spent its rounds on, arriving through a display instead of a loop -- and
    an unmeasured presence is EVERY LANE BY DEFAULT (SETTLED 3f), not an exotic
    case.

    The lane is given an identity with no presence measurement, so its own
    `decision.presence` is `null` on the wire. Nothing here edits a payload.
    """
    server = our_server(lane := a_lane(presence=None), act_token=ACT_TOKEN, arrive=False)
    with serving(server) as url:
        agent, _ua, screen = agent_on(tmp_path, url)
        arrive(agent, lane)
        assert agent._read_lane(agent.config.intercoms[0]).presence is None
        assert events_of(agent, AgentEventKind.TICKET_ISSUED) == []
        assert screen.frames == []

    # THE CONTROL: the same lane, the same decision, presence MEASURED -- and a
    # ticket appears. So the refusal above is about that field and nothing else.
    server = our_server(lane2 := a_lane(presence=True), act_token=ACT_TOKEN, arrive=False)
    with serving(server) as url:
        agent2, _ua2, screen2 = agent_on(tmp_path, url)
        arrive(agent2, lane2)
        assert events_of(agent2, AgentEventKind.TICKET_ISSUED)
        assert screen2.frames


def test_the_ticket_case_set_is_the_four_it_says_it_is(tmp_path):
    """The set, and the CONTROL that widening it changes the answer.

    Without the second half, "only these four" is a claim about a list rather
    than about behaviour.
    """
    assert {case.value for case in TICKET_CASES} == {
        "identification_unavailable",
        "plate_not_read",
        "plate_unclear",
        "vehicle_not_recognised",
    }
    server = our_server(lane := a_lane("deny", None), act_token=ACT_TOKEN, arrive=False)
    with serving(server) as url:
        agent, _ua, screen = agent_on(tmp_path, url)
        arrive(agent, lane)
        assert screen.frames == []
        # WIDENED: the same lane, the same decision, and now a ticket appears.
        import gate_agent.agent as agent_module

        widened = frozenset(TICKET_CASES | {AgentCase.ENTRY_REFUSED})
        original = agent_module.TICKET_CASES
        agent_module.TICKET_CASES = widened
        try:
            agent2, _ua2, screen2 = agent_on(tmp_path, url)
            arrive(agent2, lane)
            assert screen2.frames, "widening the set changed nothing, so it decides nothing"
        finally:
            agent_module.TICKET_CASES = original


def test_a_display_that_refuses_means_no_ticket_is_offered(tmp_path):
    """A code minted for a screen that cannot show it is a stay nobody can
    prove. The case goes to a person, exactly as it did in round 5."""
    server = our_server(lane := a_lane(), act_token=ACT_TOKEN, arrive=False)
    with serving(server) as url:
        agent, _ua, screen = agent_on(tmp_path, url)
        screen.refuse = True
        arrive(agent, lane)
        assert events_of(agent, AgentEventKind.TICKET_ISSUED) == []
        voided_events = events_of(agent, AgentEventKind.TICKET_VOIDED)
        assert [one["reason"] for one in voided_events] == ["display_unavailable"]
        assert [
            entry["state"]
            for entry in agent.health().to_dict()["codes"]
            if entry["code"] == "display_unavailable" and entry["subject"] == "front"
        ] == ["active"]


# ---------------------------------------------------------------------------
# The press, and the vend
# ---------------------------------------------------------------------------


def press(agent, ua, call_id="driver-1", settle=8):
    """The button, and then enough polls for what it queued to be SAID.

    A line is queued by the press and played by a later poll: `_speak` is what
    hands a file to the user agent, and a test that asserted on `ua.played`
    after one poll would be asserting about a QUEUE. That is the exact mistake
    `case_spoken` was written for.
    """
    ua.incoming(INTERCOM, call_id=call_id, account_user=INTERCOM_ACCOUNT)
    run(agent, settle)


def run(agent, times=8, step=2.0):
    """Poll, ADVANCING THE CLOCK between polls.

    A file finishes on its own measured duration, so a loop that polled without
    moving the clock plays exactly one line and then waits for ever -- which
    looks like an agent that queued something and never said it, and is a
    property of the fixture rather than of the agent.
    """
    for _ in range(times):
        agent.poll()
        if hasattr(agent._clock, "advance"):
            agent._clock.advance(step)


def test_a_press_confirms_the_ticket_and_the_lane_commands_the_vend(tmp_path):
    """END TO END, against the real vend route with its real refusals."""
    server = our_server(lane := a_lane(), act_token=ACT_TOKEN, arrive=False)
    with serving(server) as url:
        agent, ua, screen = agent_on(tmp_path, url)
        arrive(agent, lane)
        issued = events_of(agent, AgentEventKind.TICKET_ISSUED)[0]
        press(agent, ua)

        confirmed_events = events_of(agent, AgentEventKind.TICKET_CONFIRMED)
        assert len(confirmed_events) == 1
        assert confirmed_events[0]["ticket_id"] == issued["ticket_id"]

        commanded = events_of(agent, AgentEventKind.VEND_COMMANDED)
        assert len(commanded) == 1, agent.events(0).to_dict()["events"]
        assert commanded[0]["authorised_by"] == "display_code_confirmed"
        assert commanded[0]["ticket_id"] == issued["ticket_id"]
        # The lane's own answer, kept as the lane gave it: an EVENT CURSOR, which
        # is the join to the lane's `assisted_identity` record. Not a
        # `completion_id`: the 202 carries no such field.
        assert isinstance(commanded[0]["lane_event_cursor"], int)

        # The screen is cleared: the ticket has been used.
        assert screen.blanked >= 1
        # The record says vended, with the lane's answer on it.
        store = TicketStore(tmp_path / "tickets")
        record = store.read(issued["ticket_id"])
        assert record.state == VENDED and record.vended_at
        assert record.lane_answer == str(commanded[0]["lane_event_cursor"])
        # And the driver is told it was ASKED to open, never that it is open.
        played = [path for leg, path in ua.played if leg == "driver"]
        assert any("ticket.vend_commanded" in one for one in played)
        assert not any("open" == one for one in played)


def test_the_lane_and_not_this_agent_refuses_a_vend(tmp_path):
    """The lane's own `no_vehicle`, reaching a person with the code on it.

    Nothing here checks the loop first, and that is the design: a second copy of
    the lane's refusals is one that comes to disagree with the copy the barrier
    obeys.
    """
    controller = a_lane()
    server = our_server(controller, act_token=ACT_TOKEN, arrive=False)
    with serving(server) as url:
        agent, ua, _screen = agent_on(tmp_path, url)
        arrive(agent, controller)
        issued = events_of(agent, AgentEventKind.TICKET_ISSUED)[0]
        # THE CAR LEAVES. The loop reads clear at the moment of the press, which
        # is the lane's FIRST refusal and the one the whole design rests on.
        controller.loop.clear()
        press(agent, ua)

        refused = events_of(agent, AgentEventKind.VEND_REFUSED)
        assert len(refused) == 1, agent.events(0).to_dict()["events"]
        assert refused[0]["code"] == "no_vehicle"
        assert refused[0]["ticket_id"] == issued["ticket_id"]
        assert events_of(agent, AgentEventKind.VEND_COMMANDED) == []
        # The person is called, and told which refusal it was.
        for _ in range(40):
            run(agent, 1)
            if any(verb == "dial" for verb, _ in ua.commands):
                break
        assert any(verb == "dial" for verb, _ in ua.commands), "nobody was called"


def test_decision_at_is_ECHOED_and_not_invented(tmp_path):
    """The moment the LANE published, not this process's clock.

    The control is the lane itself: sending `now()` earns `decision_mismatch`
    from the real route, which is the right answer to the wrong question.
    """
    controller = a_lane()
    server = our_server(controller, act_token=ACT_TOKEN, arrive=False)
    with serving(server) as url:
        agent, ua, _screen = agent_on(tmp_path, url)
        arrive(agent, controller)
        pending = agent._pending["entry"]
        assert pending.decision_at == controller.last_decision_at

        # THE CONTROL: the same vend with this process's clock in that field.
        from gate_agent.act import LaneActClient
        from gate_agent.agent import utc_now

        client = LaneActClient(url, ACT_TOKEN, 5.0)
        answer = client.vend(
            authorised_by="display_code_confirmed",
            ticket_ref=pending.ticket.ticket_ref,
            decision_at=utc_now(),
            idempotency_key="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
        )
        assert answer.commanded is False
        assert answer.code == "decision_mismatch"

        # And the echoed one is accepted, so the refusal above is about the
        # FIELD and not about the request being malformed.
        press(agent, ua)
        assert events_of(agent, AgentEventKind.VEND_COMMANDED)


def test_a_second_press_inside_the_help_window_is_a_person_and_never_a_second_vend(
    tmp_path,
):
    """HELP IS THE NEXT PRESS, and the lane's `already_completed` is the
    BACKSTOP rather than the design.

    Measured on the lane's side: exactly ONE POST reaches it across both presses.
    """
    server = our_server(lane := a_lane(), act_token=ACT_TOKEN, arrive=False)
    with serving(server) as url:
        agent, ua, _screen = agent_on(tmp_path, url)
        arrive(agent, lane)
        press(agent, ua, "driver-1")
        assert len(events_of(agent, AgentEventKind.VEND_COMMANDED)) == 1
        posts = [one for one in server.requests.seen if one[0] == "POST"]
        assert len(posts) == 1

        # The driver hangs up and presses again, inside the window.
        ua.closed("driver-1")
        agent.poll()
        press(agent, ua, "driver-2")
        for _ in range(40):
            run(agent, 1)
            if any(verb == "dial" for verb, _ in ua.commands):
                break

        assert len(events_of(agent, AgentEventKind.VEND_COMMANDED)) == 1
        assert [one for one in server.requests.seen if one[0] == "POST"] == posts, (
            "a second press reached the vend route"
        )
        assert any(verb == "dial" for verb, _ in ua.commands), "the second press found nobody"

        # THE PERSON PICKS UP, and is told WHAT ALREADY HAPPENED before the menu.
        operator = [arg for verb, arg in ua.commands if verb == "dial"][0].split("-> ")[1]
        ua.established(operator)
        run(agent, 20)
        operator_lines = [path for leg, path in ua.played if leg == "operator"]
        assert any("operator.help_after_ticket" in one for one in operator_lines), operator_lines
        assert any("operator.vend_commanded" in one for one in operator_lines), operator_lines
        # And still no second vend, after the whole dialogue rather than only
        # after the press.
        assert [one for one in server.requests.seen if one[0] == "POST"] == posts


def test_a_press_with_no_pending_ticket_is_round_five_exactly(tmp_path):
    """Nothing about this round reaches a door with no ticket up."""
    server = our_server(lane := a_lane("deny", None), act_token=ACT_TOKEN, arrive=False)
    with serving(server) as url:
        agent, ua, _screen = agent_on(tmp_path, url)
        arrive(agent, lane)
        press(agent, ua)
        assert agent.session.case is AgentCase.ENTRY_REFUSED
        assert events_of(agent, AgentEventKind.TICKET_CONFIRMED) == []
        assert [one for one in server.requests.seen if one[0] == "POST"] == []


# ---------------------------------------------------------------------------
# What voids a ticket
# ---------------------------------------------------------------------------


def test_a_ticket_past_its_window_is_voided_and_never_sent(tmp_path):
    clock = FakeClock()
    server = our_server(lane := a_lane(), act_token=ACT_TOKEN, arrive=False)
    with serving(server) as url:
        agent, ua, screen = agent_on(tmp_path, url, clock=clock)
        arrive(agent, lane)
        issued = events_of(agent, AgentEventKind.TICKET_ISSUED)[0]
        clock.advance(91.0)
        agent.poll()
        voided_events = events_of(agent, AgentEventKind.TICKET_VOIDED)
        assert [one["reason"] for one in voided_events] == ["window_elapsed"]
        assert screen.blanked >= 1
        assert TicketStore(tmp_path / "tickets").read(issued["ticket_id"]).state == VOIDED

        # AND IT IS NEVER SENT. A press now is round 5, not a confirmation.
        press(agent, ua)
        assert [one for one in server.requests.seen if one[0] == "POST"] == []
        assert events_of(agent, AgentEventKind.TICKET_CONFIRMED) == []


def test_a_ticket_is_voided_when_the_car_leaves(tmp_path):
    """Presence can go false with no decision behind it -- the driver reversed
    away -- and a code on a screen for an empty lane is one the vend route would
    refuse `no_vehicle`."""
    controller = a_lane()
    server = our_server(controller, act_token=ACT_TOKEN, arrive=False)
    with serving(server) as url:
        agent, _ua, screen = agent_on(tmp_path, url)
        arrive(agent, controller)
        assert agent._pending.get("entry") is not None
        controller.loop.clear()
        from dataclasses import replace as _replace

        # `decision.presence` is published from the decision's own IDENTITY --
        # the lane's `service.py` reads `decision.identity.presence` -- so that
        # is where a car that has left shows up. NO NEW DECISION: this is the
        # driver reversing away, which is the case a decision event cannot see.
        controller.last_decision = _replace(
            controller.last_decision,
            identity=_replace(controller.last_decision.identity, presence=False),
        )
        for _ in range(4):
            agent.poll()
        assert agent._pending.get("entry") is None
        assert [one["reason"] for one in events_of(agent, AgentEventKind.TICKET_VOIDED)] == [
            "presence_lost"
        ]
        assert screen.blanked >= 1


def test_no_pending_ticket_survives_a_restart(tmp_path):
    """BY DESIGN, and it is what makes a frame left up by a crash harmless: the
    code on that screen can never be vended, and the press goes to a person."""
    server = our_server(lane := a_lane(), act_token=ACT_TOKEN, arrive=False)
    with serving(server) as url:
        agent, _ua, _screen = agent_on(tmp_path, url)
        arrive(agent, lane)
        assert agent._pending.get("entry") is not None

        # A SECOND PROCESS against the same lane and the same store.
        restarted, ua2, _screen2 = agent_on(tmp_path, url)
        assert restarted._pending == {}
        press(restarted, ua2)
        assert events_of(restarted, AgentEventKind.TICKET_CONFIRMED) == []
        assert [one for one in server.requests.seen if one[0] == "POST"] == []


# ---------------------------------------------------------------------------
# The human's act
# ---------------------------------------------------------------------------


def open_now(agent, ua, digit="1"):
    """A whole case, from the call arriving to a digit being keyed."""
    for _ in range(60):
        run(agent, 1)
        if any(verb == "dial" for verb, _ in ua.commands):
            break
    operator = [arg for verb, arg in ua.commands if verb == "dial"][0].split("-> ")[1]
    ua.established(operator)
    for _ in range(60):
        run(agent, 1)
        if ua.bridged_at is not None:
            break
    ua.dtmf(operator, digit)
    run(agent, 8)
    return operator


def test_a_humans_open_now_vends_through_the_same_route(tmp_path):
    """One vend path, not two. The authority differs and nothing else does."""
    server = our_server(lane := a_lane("deny", None), act_token=ACT_TOKEN, arrive=False)
    with serving(server) as url:
        agent, ua, _screen = agent_on(tmp_path, url)
        arrive(agent, lane)
        press(agent, ua)
        open_now(agent, ua, "1")
        commanded = events_of(agent, AgentEventKind.VEND_COMMANDED)
        assert len(commanded) == 1, agent.events(0).to_dict()["events"]
        assert commanded[0]["authorised_by"] == "human_open_now"
        # A DENY IS OVERRIDDEN, and only by this authority. The lane records it
        # as an override on the event it writes before the barrier moves.
        assert commanded[0]["case"] == AgentCase.ENTRY_REFUSED.value


def test_open_and_flag_does_not_override_a_deny(tmp_path):
    """A completion somebody is unsure about and a person overturning a refusal
    are different acts, and one of them is not made safer by being uncertain.
    The LANE enforces that, and this is the agent meeting it."""
    server = our_server(lane := a_lane("deny", None), act_token=ACT_TOKEN, arrive=False)
    with serving(server) as url:
        agent, ua, _screen = agent_on(tmp_path, url)
        arrive(agent, lane)
        press(agent, ua)
        open_now(agent, ua, "2")
        assert events_of(agent, AgentEventKind.VEND_COMMANDED) == []
        refused = events_of(agent, AgentEventKind.VEND_REFUSED)
        assert len(refused) == 1
        assert refused[0]["code"] == "not_completable"
        assert refused[0]["authorised_by"] == "human_open_and_flag"


def test_cannot_open_is_spoken_where_nothing_can_act_and_not_where_something_can(tmp_path):
    """Both halves. The sentence is not optional where it is true, and it is a
    lie where it is not."""
    server = our_server(lane := a_lane("deny", None), act_token=ACT_TOKEN, arrive=False)
    with serving(server) as url:
        # NO ACT TOKEN: the sentence is spoken.
        agent, ua, _screen = agent_on(tmp_path, url, act_token=None)
        arrive(agent, lane)
        press(agent, ua)
        open_now(agent, ua, "1")
        spoken = [path for leg, path in ua.played if leg == "operator"]
        assert any("operator.cannot_open" in one for one in spoken)

        # WITH ONE: it is not.
        agent2, ua2, _screen2 = agent_on(tmp_path, url)
        arrive(agent2, lane)
        press(agent2, ua2)
        open_now(agent2, ua2, "1")
        spoken2 = [path for leg, path in ua2.played if leg == "operator"]
        assert not any("operator.cannot_open" in one for one in spoken2), spoken2
        assert events_of(agent2, AgentEventKind.VEND_COMMANDED)


# ---------------------------------------------------------------------------
# The reference goes in exactly one place
# ---------------------------------------------------------------------------


def test_the_ticket_ref_is_on_no_event_no_route_and_no_log_line(tmp_path, caplog):
    """A planted-value sweep, with the STORE as its positive control.

    A `ticket_ref` identifies one stay and is personal data while that stay
    exists. It is what a person reads out and what an exit will scan, and the
    one place it lives on this box is the ticket record -- which is what makes
    the retention rule mean anything.
    """
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    logging.getLogger("gate_agent").addHandler(handler)
    logging.getLogger("gate_agent").setLevel(logging.DEBUG)
    try:
        server = our_server(lane := a_lane(), act_token=ACT_TOKEN, arrive=False)
        with serving(server) as url:
            agent, ua, _screen = agent_on(tmp_path, url)
            arrive(agent, lane)
            press(agent, ua)
            for _ in range(10):
                agent.poll()
            issued = events_of(agent, AgentEventKind.TICKET_ISSUED)[0]
            record = TicketStore(tmp_path / "tickets").read(issued["ticket_id"])
            ref = record.ticket_ref
    finally:
        logging.getLogger("gate_agent").removeHandler(handler)

    assert ref and len(ref) == 8
    # THE POSITIVE CONTROL: the value really is in the store, through the same
    # search, so its absence below is about the surfaces and not about the sweep.
    store_text = "".join(
        path.read_text(encoding="utf-8")
        for path in (tmp_path / "tickets").glob("*.json")
    )
    assert ref in store_text

    assert ref not in json.dumps(agent.events(0).to_dict())
    assert ref not in json.dumps(agent.health().to_dict())
    assert ref not in json.dumps(agent.describe().to_dict())
    assert ref not in stream.getvalue()


def test_the_agent_reads_the_lanes_events_and_never_their_detail(tmp_path):
    """The route this round starts reading is the one that carries a lane's own
    event detail -- `entry_pending` carries `plate_region` -- so the guarantee
    that used to be "it never reads that route" has to be replaced.

    What replaces it: the agent reads `kind` and nothing else out of an event.
    Proven by planting a plate in a lane event's detail and sweeping every
    surface this agent has.
    """
    from foreign_lane import ForeignLane
    from foreign_lane import make_server as foreign_server

    lane = ForeignLane()
    lane.window = 64
    lane.decision = {
        "outcome": "fallback",
        "reason": "no_plate_read",
        "fallback": "no_plate_read",
        "cause": None,
        "presence": True,
        "at": __import__("foreign_lane").decided_at(),
        "read_ref": None,
        "completed": False,
    }
    with serving(foreign_server(lane)) as url:
        agent, _ua, _screen = agent_on(tmp_path, url, act_token=None)
        agent.poll()
        lane.record(
            "entry_pending",
            "2026-08-31T14:00:00+00:00",
            {"plate_region": "PLANTEDPLATE9", "identity_kind": "ticket"},
        )
        foreign_decides(agent, lane, "no_plate_read")
        assert events_of(agent, AgentEventKind.TICKET_ISSUED), "the poll did nothing"
        for surface in (agent.events(0), agent.health(), agent.describe()):
            assert "PLANTEDPLATE9" not in json.dumps(surface.to_dict())
    # THE CONTROL: the plate really was on the wire, so its absence is about the
    # agent rather than about a lane that never sent one.
    assert any(
        "PLANTEDPLATE9" in json.dumps(one.get("detail", {})) for one in lane.log
    )
