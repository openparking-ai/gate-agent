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
from gate_agent.tickets import CONFIRMED, ISSUED, VENDED, VOIDED, TicketStore
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
        #: What the driver would say if asked again. A test that means to change
        #: the mode under a running agent sets this; everything else leaves it
        #: alone and the screen answers what it has always answered.
        self.next_geometry = None
        #: Whether the geometry can be read at all -- a screen that has GONE, as
        #: opposed to one that refuses a write.
        self.readable = True
        self.rereads = 0

    def reread_geometry(self) -> Geometry:
        """The real `Display` asks sysfs again; this answers what a test set."""
        from gate_agent.display import DisplayUnavailable

        self.rereads += 1
        if not self.readable:
            raise DisplayUnavailable("this screen's geometry cannot be read")
        if self.next_geometry is not None:
            self.geometry = self.next_geometry
        return self.geometry

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

    **`engine_unreachable` is driven WITH `identity_service_down` ACTIVE**, which
    is the only way a real lane can emit that reason: `_identity_service_down()`
    reads `active` off the same `last_cause` the fallback came from. The row
    used to run against a lane with NO code active -- a state a real one cannot
    be in when it answers that -- so the one pairing that occurs in life was the
    one the suite never built, and the defect it hid was that the agent refused
    a ticket for fifteen of the sixteen codes the lane vends on.
    """
    from foreign_lane import make_server as foreign_server

    lane = a_foreign_lane(reason)
    if reason == "engine_unreachable":
        lane.states = {"identity_service_down": "active"}
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


def test_a_presence_that_is_not_a_BOOLEAN_is_unmeasured_and_gets_no_ticket(tmp_path):
    """The string `"false"` is not `False`, and truthiness would read it as a car.

    THE SAME FRAUD AS THE TEST ABOVE, arriving by a different route. `presence`
    is read with `_boolean`, which answers `None` for anything that is not a
    JSON boolean -- so a lane that publishes the STRING `"false"`, or the number
    `0`, or `"yes"`, is a lane that has measured nothing, and an unmeasured
    presence never puts a code on a screen (SETTLED 3f).

    Nothing in the suite drove a non-boolean `presence` before this test, which
    is why `presence_is_read_as_truthiness` -- a break that swaps the type check
    for `bool(value)` -- reddened nothing and was reported PASSED WHEN. A lane
    that is NOT ours is the right fixture: ours cannot publish a malformed
    field, and a third party's is exactly who will.
    """
    from foreign_lane import make_server as foreign_server

    for published in ("false", "true", 0, 1, "yes"):
        lane = a_vending_foreign_lane()
        with serving(foreign_server(lane)) as url:
            agent, _ua, screen = agent_on(tmp_path, url)
            foreign_decides(agent, lane, "no_plate_read", presence=published)
            reading = agent._read_lane(agent.config.intercoms[0])
            assert reading.presence is None, (
                f"a lane published presence={published!r} -- not a boolean, so nothing was "
                f"measured -- and the agent read it as {reading.presence!r}"
            )
            assert events_of(agent, AgentEventKind.TICKET_ISSUED) == [], published
            assert screen.frames == [], published

    # THE CONTROL, and it is the same fixture on the same route: a REAL boolean
    # `true` on the wire is measured, and a ticket appears. So the five refusals
    # above are about the TYPE of that field and not about the foreign lane.
    lane = a_vending_foreign_lane()
    with serving(foreign_server(lane)) as url:
        agent, _ua, screen = agent_on(tmp_path, url)
        foreign_decides(agent, lane, "no_plate_read", presence=True)
        assert agent._read_lane(agent.config.intercoms[0]).presence is True
        assert events_of(agent, AgentEventKind.TICKET_ISSUED)
        assert screen.frames



def test_a_new_decision_the_lane_offers_no_ticket_for_voids_the_old_one_IN_THAT_POLL(tmp_path):
    """The void that nothing was measuring (Z16.1, 2026-09-01), and WHEN it happens.

    A new decision voids the pending ticket, whatever the new decision says.
    Two things hid this from every test that touched it:

      * every one of them drove a new decision that ALSO offered a ticket, and a
        mint replaces the pending one on its way past -- so removing the void
        changed nothing they could see; and
      * `_check_pending` voids `lane_decided_again` too, on the NEXT poll, when
        it notices the decision moment has moved. So a test that polls more than
        once sees the ticket go down either way.

    **The guarantee is that it is down IN THE POLL THAT READ THE DECISION**, and
    that is not pedantry: between that poll and the next one, a press at the door
    finds driver one's ticket pending and confirms it for the car that is now at
    the barrier. So this drives exactly one poll and looks at the state inside
    that window.

    A foreign lane, because the payload has to be chosen: `deny` with presence
    still TRUE, so `presence_lost` is not what does the voiding. The reason is
    measured, not merely that something happened.
    """
    from foreign_lane import decided_at
    from foreign_lane import make_server as foreign_server

    lane = a_vending_foreign_lane()
    with serving(foreign_server(lane)) as url:
        agent, ua, screen = agent_on(tmp_path, url)
        foreign_decides(agent, lane, "no_plate_read", presence=True)
        issued = events_of(agent, AgentEventKind.TICKET_ISSUED)
        assert len(issued) == 1 and screen.frames, "no ticket to void"
        first = issued[0]["ticket_id"]
        blanked_before = screen.blanked

        # THE LANE DECIDES AGAIN, about somebody else, and offers no ticket.
        # Driven by hand rather than through `foreign_decides`, because that
        # helper polls four times and the second poll would void this through
        # `_check_pending` -- a different site, proving a different thing.
        lane.decision = {
            "outcome": "deny",
            "reason": None,
            "fallback": None,
            "cause": None,
            "presence": True,
            "at": decided_at(),
            "read_ref": None,
            "completed": False,
        }
        lane.record("decision", "2026-09-01T09:00:00+00:00", {"outcome": "deny"})
        agent.poll()  # EXACTLY ONE, and the ticket must already be down

        voided_events = events_of(agent, AgentEventKind.TICKET_VOIDED)
        assert [one["reason"] for one in voided_events] == ["lane_decided_again"], (
            agent.events(0).to_dict()["events"]
        )
        assert voided_events[0]["ticket_id"] == first
        assert agent._pending.get("entry") is None, "the old code is still pending"
        assert screen.blanked > blanked_before, "the old code is still on the screen"

        # AND THE RECORD SAYS SO -- it is the site's only account of this stay.
        record = TicketStore(tmp_path / "tickets").read(first)
        assert record is not None
        assert record.state == VOIDED and record.void_reason == "lane_decided_again"

        # THE CONTROL, and it is the harm: a press in this window is round five
        # exactly. With the void gone the press confirms driver ONE's ticket for
        # the car now at the barrier, and commands a vend on it.
        press(agent, ua)
        assert events_of(agent, AgentEventKind.TICKET_CONFIRMED) == []
        assert events_of(agent, AgentEventKind.VEND_COMMANDED) == []

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
        # Patched where `offers_ticket` reads it -- the agent no longer holds a
        # reference of its own, because whether a ticket is offered is decided
        # by the DECISION and that question lives in `cases`.
        import gate_agent.cases as agent_module

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


def test_the_driver_is_not_told_it_cannot_open_at_a_door_that_is_about_to_ask(tmp_path):
    """THE SAME TWO COLUMNS, on the leg the driver is standing at.

    Round 7 made the person's sentence conditional and left the driver's
    unconditional, so at a door with an act token the driver heard "A person has
    authorised your entry. This system cannot open the barrier itself, so please
    wait for them" and then, a moment later, "The barrier has been asked to
    open" -- two sentences contradicting each other, the false one first, in
    both languages, as shipped audio.

    Where nothing can act the sentence is TRUE and it stays: this asserts both,
    because a fix that simply stopped saying it would leave a driver at a door
    that will never open told nothing at all.
    """
    server = our_server(lane := a_lane("deny", None), act_token=ACT_TOKEN, arrive=False)
    with serving(server) as url:
        # NO ACT TOKEN: nothing here will ask, and the driver is told so.
        agent, ua, _screen = agent_on(tmp_path, url, act_token=None)
        arrive(agent, lane)
        press(agent, ua)
        open_now(agent, ua, "1")
        heard = [path for leg, path in ua.played if leg == "driver"]
        assert any("authorisation.open_now.wav" in one for one in heard), heard
        assert not any("authorisation.open_now.acting" in one for one in heard), heard
        assert events_of(agent, AgentEventKind.VEND_COMMANDED) == []

        # WITH ONE: the barrier IS about to be asked, so the driver hears the
        # sentence that claims nothing about what this system cannot do.
        agent2, ua2, _screen2 = agent_on(tmp_path, url)
        arrive(agent2, lane)
        press(agent2, ua2)
        open_now(agent2, ua2, "1")
        heard2 = [path for leg, path in ua2.played if leg == "driver"]
        assert any("authorisation.open_now.acting" in one for one in heard2), heard2
        assert not any(one.endswith("authorisation.open_now.wav") for one in heard2), heard2
        assert len(events_of(agent2, AgentEventKind.VEND_COMMANDED)) == 1


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


# ---------------------------------------------------------------------------
# THE OFFER IS THE DECISION'S, AND NOT THE HEALTH'S
# ---------------------------------------------------------------------------


def an_engine_down_lane(presence=True):
    """OUR lane with its identification engine UNREACHABLE.

    The pairing that occurs in life and that the suite never built: a lane
    answering `engine_unreachable` has `identity_service_down` ACTIVE at the
    same moment, because `_identity_service_down()` reads `active` off the same
    `last_cause`. Nothing here writes a payload -- the identity goes through the
    real `StubVehicleIdentifier` and the lane derives both.
    """
    from lane_controller.interfaces import Unavailable

    return our_lane(
        identities=[
            VehicleIdentity(
                plate=None,
                confidence=0.0,
                unavailable=Unavailable.UNREACHABLE,
                presence=presence,
            )
        ]
        * ARRIVALS,
        arrivals=ARRIVALS,
    )


def test_a_ticket_is_offered_while_the_identification_engine_is_DOWN(tmp_path):
    """THE CASE THE MODULE EXISTS FOR, and it used to be the one with no ticket.

    Round 6 made the lane's assisted vend proceed on every malfunction but the
    five in `VEND_BLOCKING`, because the identification engine being down is the
    common reason a driver presses the button. The agent then refused at its own
    layer: `derive()` answers `malfunction_active` for any active code before it
    looks at the decision, and that case is not in `TICKET_CASES` -- so a garage
    whose engine was down put every arriving driver on the phone, while the lane
    beside it would have opened on the ticket.

    Everything here is the lane's own: its decision, its health, its 202.
    """
    from datetime import UTC, datetime

    from gate_agent.cases import derive

    server = our_server(lane := an_engine_down_lane(), act_token=ACT_TOKEN, arrive=False)
    with serving(server) as url:
        agent, ua, screen = agent_on(tmp_path, url)
        arrive(agent, lane)

        # THE STATE THE LANE IS REALLY IN, read back off the wire.
        reading = agent._read_lane(agent.config.intercoms[0])
        assert (reading.outcome, reading.reason) == ("fallback", "engine_unreachable")
        assert reading.presence is True
        assert "identity_service_down" in reading.malfunctions, reading.malfunctions
        # AND THE CASE A DRIVER HEARS IS STILL THE MALFUNCTION. `derive` did not
        # move; a second question was asked instead.
        assert derive(reading, now=datetime.now(UTC)) is AgentCase.MALFUNCTION_ACTIVE

        issued = events_of(agent, AgentEventKind.TICKET_ISSUED)
        assert len(issued) == 1, agent.events(0).to_dict()["events"]
        assert issued[0]["case"] == AgentCase.IDENTIFICATION_UNAVAILABLE.value
        assert screen.frames, "a ticket was issued and nothing was drawn"

        # AND THE PRESS VENDS, at the real route, with the real refusals.
        press(agent, ua)
        commanded = events_of(agent, AgentEventKind.VEND_COMMANDED)
        assert len(commanded) == 1, agent.events(0).to_dict()["events"]
        assert [one for one in server.requests.seen if one[0] == "POST"]


def test_the_offer_consults_the_decision_and_the_control_is_the_health(tmp_path):
    """THE CONTROL for the test above: make the offer consult `malfunctions`
    again and the same lane offers nothing.

    Without this, "the health does not decide" is a claim about a code path
    rather than about behaviour.
    """
    import gate_agent.agent as agent_module

    server = our_server(lane := an_engine_down_lane(), act_token=ACT_TOKEN, arrive=False)
    with serving(server) as url:
        # Patched where the AGENT reads it, which is where the reversion would
        # be: a patch of `cases.offers_ticket` would leave the name the agent
        # imported at module load untouched and measure nothing.
        original = agent_module.offers_ticket

        def consults_the_health(reading, now, max_age_seconds=120.0):
            return not reading.malfunctions and original(reading, now, max_age_seconds)

        agent_module.offers_ticket = consults_the_health
        try:
            agent, _ua, screen = agent_on(tmp_path, url)
            arrive(agent, lane)
            assert events_of(agent, AgentEventKind.TICKET_ISSUED) == []
            assert screen.frames == []
        finally:
            agent_module.offers_ticket = original


def test_an_unmeasured_presence_is_still_refused_with_the_engine_down(tmp_path):
    """The clause that did NOT move. `None` is not `True`, whatever the health
    says, and the control beside it is the same lane with presence measured."""
    server = our_server(
        lane := an_engine_down_lane(presence=None), act_token=ACT_TOKEN, arrive=False
    )
    with serving(server) as url:
        agent, _ua, screen = agent_on(tmp_path, url)
        arrive(agent, lane)
        assert agent._read_lane(agent.config.intercoms[0]).presence is None
        assert events_of(agent, AgentEventKind.TICKET_ISSUED) == []
        assert screen.frames == []


def a_vending_foreign_lane(reason="no_plate_read", presence=True):
    """A lane that is NOT ours and CAN vend, with its own vocabulary.

    The fixture the L3 found missing: `tests/foreign_lane` published
    `can_vend: false` and refused every POST, so nothing in the suite drove a
    third party's lane through a completion -- which is the seat SETTLED 1
    requires this module to sit in.
    """
    lane = a_foreign_lane(reason, presence=presence)
    lane.can_vend = True
    return lane


def sweep_lane_and_agent(tmp_path, lane, url, code, blocking):
    """One code forced active, and what the agent does about it."""
    lane.states = {code: "active"} if code else {}
    lane.vend_refusal = "malfunction_active" if blocking else None
    lane.vend_malfunction = code if blocking else None
    agent, ua, screen = agent_on(tmp_path, url)
    foreign_decides(agent, lane, "no_plate_read")
    return agent, ua, screen


def test_every_code_the_lane_vends_on_still_offers_a_ticket(tmp_path):
    """THE 21-CODE SWEEP. One code active at a time, against a lane that vends.

    Twenty of the twenty-one used to suppress the ticket, and the one that did
    not was the only code carrying `never_alarm: true` on the wire -- so the
    single case that worked did so for a reason that had nothing to do with
    vending, and the five right answers were right by accident.

    `VEND_BLOCKING` is READ from the installed lane package, not typed, and the
    fixture's refusal is derived from it: what is measured here is the AGENT --
    a ticket offered whatever the health says, and the lane's own refusal
    reaching the person with its code.
    """
    from lane_controller.contract import VEND_BLOCKING

    from foreign_lane import MALFUNCTION_CODES
    from foreign_lane import make_server as foreign_server

    blocking = {code.value for code in VEND_BLOCKING}
    assert blocking <= set(MALFUNCTION_CODES), blocking
    table = {}
    for code in (None, *MALFUNCTION_CODES):
        lane = a_vending_foreign_lane()
        with serving(foreign_server(lane)) as url:
            agent, ua, screen = sweep_lane_and_agent(
                tmp_path, lane, url, code, code in blocking
            )
            issued = events_of(agent, AgentEventKind.TICKET_ISSUED)
            table[code] = {
                "ticket": bool(issued),
                "frames": bool(screen.frames),
                "vended": None,
                "refused": None,
            }
            if not issued:
                continue
            press(agent, ua)
            commanded = events_of(agent, AgentEventKind.VEND_COMMANDED)
            refused = events_of(agent, AgentEventKind.VEND_REFUSED)
            table[code]["vended"] = bool(commanded)
            table[code]["refused"] = refused[0]["code"] if refused else None

    # EVERY ONE OF THE TWENTY-ONE, and the control with no code active.
    assert all(row["ticket"] and row["frames"] for row in table.values()), table
    for code, row in table.items():
        if code in blocking:
            # THE LANE REFUSES, by name, and the agent did not.
            assert row["vended"] is False and row["refused"] == "malfunction_active", (
                code, row
            )
        else:
            assert row["vended"] is True and row["refused"] is None, (code, row)


def test_the_five_blocking_codes_reach_the_person_with_the_code(tmp_path):
    """B1's second half: the ticket is offered, the LANE refuses it, and the
    person is told a ticket was refused and what the refusal was."""
    from foreign_lane import make_server as foreign_server

    lane = a_vending_foreign_lane()
    with serving(foreign_server(lane)) as url:
        agent, ua, _screen = sweep_lane_and_agent(
            tmp_path, lane, url, "boom_did_not_rise", True
        )
        press(agent, ua)
        assert [one["code"] for one in events_of(agent, AgentEventKind.VEND_REFUSED)] == [
            "malfunction_active"
        ]
        heard = operator_hears(agent, ua)
        assert any("operator.ticket_refused" in one for one in heard), heard
        assert any(
            "operator.vend_refused.malfunction_active" in one for one in heard
        ), heard


def operator_hears(agent, ua, call_id=None):
    """Get the person on the phone and return every file played to them."""
    for _ in range(60):
        run(agent, 1)
        if any(verb == "dial" for verb, _ in ua.commands):
            break
    dialled = [arg for verb, arg in ua.commands if verb == "dial"]
    assert dialled, "nobody was called"
    ua.established(dialled[-1].split("-> ")[1])
    run(agent, 30)
    return [path for leg, path in ua.played if leg == "operator"]


def test_a_refusal_this_build_has_no_words_for_still_reaches_the_operator(tmp_path):
    """B8. A third party's own refusal code, and the person used to hear NOTHING
    about it: no line saying a ticket was confirmed, none saying it was refused,
    and then the menu offering `OPEN_NOW`, which meets the same refusal."""
    from foreign_lane import make_server as foreign_server

    lane = a_vending_foreign_lane()
    with serving(foreign_server(lane)) as url:
        agent, ua, _screen = agent_on(tmp_path, url)
        lane.vend_refusal = "barrier_operator_intervened"
        foreign_decides(agent, lane, "no_plate_read")
        assert events_of(agent, AgentEventKind.TICKET_ISSUED)
        press(agent, ua)
        refused = events_of(agent, AgentEventKind.VEND_REFUSED)
        assert [one["code"] for one in refused] == ["barrier_operator_intervened"]
        # THE RECORD SAYS SO TOO, in the lane's own word.
        record = TicketStore(tmp_path / "tickets").read(refused[0]["ticket_id"])
        assert (record.state, record.void_reason) == (VOIDED, "lane_refused")
        assert record.lane_answer == "barrier_operator_intervened"

        heard = operator_hears(agent, ua)
        assert any("operator.ticket_refused" in one for one in heard), heard
        assert any("operator.vend_refused.unknown" in one for one in heard), heard
        # AND THE MENU STILL OFFERS IT, which the contract says will meet the
        # same refusal unless the cause has changed.
        assert any("menu.open_now" in one for one in heard), heard


def test_the_unknown_sentence_is_what_makes_that_briefing_true(tmp_path):
    """THE CONTROL for the test above: remove the fallback and the person is
    told a ticket was refused and nothing about why."""
    import gate_agent.agent as agent_module
    from foreign_lane import make_server as foreign_server

    lane = a_vending_foreign_lane()
    with serving(foreign_server(lane)) as url:
        original = agent_module.UNKNOWN_REFUSAL
        agent_module.UNKNOWN_REFUSAL = "operator.vend_refused.no_vehicle"
        try:
            agent, ua, _screen = agent_on(tmp_path, url)
            lane.vend_refusal = "barrier_operator_intervened"
            foreign_decides(agent, lane, "no_plate_read")
            press(agent, ua)
            heard = operator_hears(agent, ua)
            assert not any("operator.vend_refused.unknown" in one for one in heard), heard
        finally:
            agent_module.UNKNOWN_REFUSAL = original


# ---------------------------------------------------------------------------
# THE PRESS INSIDE THE POLL GAP
# ---------------------------------------------------------------------------


def test_a_press_before_the_poll_mints_shows_and_TELLS_and_rings_nobody(tmp_path):
    """B3. The lane decides at t=0 and the driver presses on the same poll.

    What used to happen: a person was dialled at `sip:duty@10.0.0.5` while a
    valid, pending, unexpired ticket for that lane was on the screen a metre
    from the driver, issued in the same poll, with neither of them told it was
    there. The window is `poll_seconds` wide and it is the window a driver
    actually presses in -- the lane decides when the loop arms, and the press
    comes after the car has stopped.
    """
    server = our_server(lane := a_lane(), act_token=ACT_TOKEN, arrive=False)
    with serving(server) as url:
        agent, ua, screen = agent_on(tmp_path, url)
        agent.poll()          # adopt the lane's cursor
        lane.run_once()       # THE LANE DECIDES ... t=0
        # ... and the driver presses before the next poll has minted anything.
        assert agent._pending == {}
        press(agent, ua)

        issued = events_of(agent, AgentEventKind.TICKET_ISSUED)
        assert len(issued) == 1, agent.events(0).to_dict()["events"]
        assert screen.frames, "nothing was drawn on the press"
        told = events_of(agent, AgentEventKind.TICKET_ON_SCREEN)
        assert [one["ticket_id"] for one in told] == [issued[0]["ticket_id"]]
        # NOBODY WAS RUNG.
        assert not any(verb == "dial" for verb, _ in ua.commands), ua.commands
        assert events_of(agent, AgentEventKind.HUMAN_CALLED) == []
        # AND THE DRIVER WAS TOLD WHERE TO LOOK.
        played = [path for leg, path in ua.played if leg == "driver"]
        assert any("ticket.on_screen" in one for one in played), played


def test_the_second_press_confirms_a_ticket_the_driver_was_TOLD_about(tmp_path):
    """B4. The press after the telling confirms and vends -- and the one BEFORE
    it does not.

    The whole of the defect: the second press used to vend a code the driver had
    never seen and never photographed. `told_at` is what separates the two, and
    it is written when the sentence saying so has FINISHED.
    """
    server = our_server(lane := a_lane(), act_token=ACT_TOKEN, arrive=False)
    with serving(server) as url:
        agent, ua, _screen = agent_on(tmp_path, url)
        agent.poll()
        lane.run_once()
        press(agent, ua, "driver-1")
        issued = events_of(agent, AgentEventKind.TICKET_ISSUED)[0]
        assert events_of(agent, AgentEventKind.TICKET_CONFIRMED) == []
        assert [one for one in server.requests.seen if one[0] == "POST"] == []
        # The record says they have been told, now that the line has finished.
        store = TicketStore(tmp_path / "tickets")
        assert store.read(issued["ticket_id"]).told_at

        ua.closed("driver-1")
        agent.poll()
        press(agent, ua, "driver-2")
        confirmed_events = events_of(agent, AgentEventKind.TICKET_CONFIRMED)
        assert [one["ticket_id"] for one in confirmed_events] == [issued["ticket_id"]]
        assert len(events_of(agent, AgentEventKind.VEND_COMMANDED)) == 1


def test_a_press_confirms_only_a_ticket_the_driver_was_TOLD_about(tmp_path):
    """B4's defect, measured on the one field that decides it, both ways.

    A ticket can be pending and unseen: minted by the poll while the driver was
    already on the phone, or minted on a press and not yet spoken. Confirming
    one vends a code the driver never photographed, and they drive in with a
    stay whose only identity is a reference nobody holds.
    """
    from dataclasses import replace as _replace

    server = our_server(lane := a_lane(), act_token=ACT_TOKEN, arrive=False)
    with serving(server) as url:
        agent, ua, _screen = agent_on(tmp_path, url)
        arrive(agent, lane)
        pending = agent._pending["entry"]
        # NOT TOLD -- which is what a ticket minted behind a driver is.
        agent._pending["entry"] = _replace(pending, told_at=None)
        press(agent, ua, "driver-1")
        assert events_of(agent, AgentEventKind.TICKET_CONFIRMED) == []
        assert [one for one in server.requests.seen if one[0] == "POST"] == []
        played = [path for leg, path in ua.played if leg == "driver"]
        assert any("ticket.on_screen" in one for one in played), played

    # THE CONTROL, and it is the same run with that ONE field set: the ticket is
    # confirmed and the barrier is asked to open.
    server = our_server(lane2 := a_lane(), act_token=ACT_TOKEN, arrive=False)
    with serving(server) as url:
        agent2, ua2, _screen2 = agent_on(tmp_path, url)
        arrive(agent2, lane2)
        pending = agent2._pending["entry"]
        agent2._pending["entry"] = _replace(pending, told_at="2026-08-31T00:00:00+00:00")
        press(agent2, ua2, "driver-1")
        assert events_of(agent2, AgentEventKind.TICKET_CONFIRMED)
        assert events_of(agent2, AgentEventKind.VEND_COMMANDED)


def test_a_ticket_minted_while_a_call_is_up_is_spoken_in_that_call(tmp_path):
    """The other half of B3: the poll mints WHILE the driver is on the phone.

    The driver pressed before the lane decided, so the case was being spoken;
    the decision then arrives and the code goes up behind them. They are told
    about it in the call they are already in, and nobody is rung.
    """
    server = our_server(lane := a_lane(), act_token=ACT_TOKEN, arrive=False)
    with serving(server) as url:
        agent, ua, screen = agent_on(tmp_path, url)
        agent.poll()
        # THE CALL FIRST, with no decision at the lane yet.
        ua.incoming(INTERCOM, call_id="driver-1", account_user=INTERCOM_ACCOUNT)
        agent.poll()
        assert agent.session is not None
        # ... and NOW the lane decides.
        lane.run_once()
        run(agent, 12)

        assert events_of(agent, AgentEventKind.TICKET_ISSUED), "no ticket was minted"
        assert screen.frames
        assert events_of(agent, AgentEventKind.TICKET_ON_SCREEN)
        assert not any(verb == "dial" for verb, _ in ua.commands), ua.commands
        played = [path for leg, path in ua.played if leg == "driver"]
        assert any("ticket.on_screen" in one for one in played), played


# ---------------------------------------------------------------------------
# THE HELP WINDOW IS THE TICKET'S
# ---------------------------------------------------------------------------


def test_the_next_driver_is_never_briefed_with_the_last_ones_two_sentences(tmp_path):
    """B9. Driver one confirms and vends; a second car arrives and presses.

    Both help sentences used to be false about the person on the line -- driver
    two was given nothing and no barrier was asked to open for them -- and their
    own ticket then expired unvended while they were on the phone. The operator
    decides whether to open a barrier on that briefing.
    """
    server = our_server(lane := a_lane(), act_token=ACT_TOKEN, arrive=False)
    with serving(server) as url:
        agent, ua, screen = agent_on(tmp_path, url)
        arrive(agent, lane)
        press(agent, ua, "driver-1")
        assert len(events_of(agent, AgentEventKind.VEND_COMMANDED)) == 1
        ua.closed("driver-1")
        agent.poll()

        # THE SECOND CAR, inside the help window, with a ticket of its own.
        lane.run_once()
        for _ in range(4):
            agent.poll()
        issued = events_of(agent, AgentEventKind.TICKET_ISSUED)
        assert len(issued) == 2, agent.events(0).to_dict()["events"]
        assert len(screen.frames) >= 2

        press(agent, ua, "driver-2")
        confirmed_events = events_of(agent, AgentEventKind.TICKET_CONFIRMED)
        assert [one["ticket_id"] for one in confirmed_events] == [
            issued[0]["ticket_id"], issued[1]["ticket_id"]
        ], "driver two's own ticket was not the one confirmed"
        assert len(events_of(agent, AgentEventKind.VEND_COMMANDED)) == 2
        # AND THE OPERATOR WAS NEVER BRIEFED ABOUT DRIVER ONE.
        operator_lines = [path for leg, path in ua.played if leg == "operator"]
        assert not any("operator.help_after_ticket" in one for one in operator_lines), (
            operator_lines
        )


def test_a_new_ticket_at_the_lane_is_what_ends_the_window(tmp_path):
    """THE CONTROL: leave the window keyed on the door and the second driver
    gets the first driver's briefing back."""
    server = our_server(lane := a_lane(), act_token=ACT_TOKEN, arrive=False)
    with serving(server) as url:
        agent, ua, _screen = agent_on(tmp_path, url)
        arrive(agent, lane)
        press(agent, ua, "driver-1")
        ua.closed("driver-1")
        agent.poll()
        # THE BREAK: nothing ends the window.
        agent._end_help_at = lambda lane: None
        lane.run_once()
        for _ in range(4):
            agent.poll()
        press(agent, ua, "driver-2")
        for _ in range(60):
            run(agent, 1)
            if any(verb == "dial" for verb, _ in ua.commands):
                break
        operator = [arg for verb, arg in ua.commands if verb == "dial"][0].split("-> ")[1]
        ua.established(operator)
        run(agent, 20)
        operator_lines = [path for leg, path in ua.played if leg == "operator"]
        assert any("operator.help_after_ticket" in one for one in operator_lines), (
            "the control did not reproduce the defect"
        )


# ---------------------------------------------------------------------------
# ONE DISPLAY PER LANE
# ---------------------------------------------------------------------------


def test_a_press_at_a_door_with_no_screen_is_round_five_and_never_a_confirm(tmp_path):
    """B15's other half. Two intercoms on one lane is ordinary; two SCREENS is
    refused at startup (`test_agent_config.py`).

    The ticket is bound to the one door whose screen shows it, so a press at the
    second door is the round-5 path: a case, a person, and no confirmation of
    somebody else's code.
    """
    from dataclasses import replace

    from conftest import OTHER_ACCOUNT

    server = our_server(lane := a_lane(), act_token=ACT_TOKEN, arrive=False)
    with serving(server) as url:
        agent, ua, screen = agent_on(tmp_path, url)
        second = replace(
            agent.config.intercoms[0],
            sip_uri="sip:door2@10.0.0.9",
            account_user=OTHER_ACCOUNT,
            display=None,
        )
        agent.config = replace(agent.config, intercoms=agent.config.intercoms + (second,))
        agent._by_account[OTHER_ACCOUNT] = second
        arrive(agent, lane)
        assert events_of(agent, AgentEventKind.TICKET_ISSUED)
        assert screen.frames, "the one declared screen was never drawn to"

        ua.incoming("sip:door2@10.0.0.9", call_id="driver-2", account_user=OTHER_ACCOUNT)
        run(agent, 8)
        assert events_of(agent, AgentEventKind.TICKET_CONFIRMED) == []
        assert [one for one in server.requests.seen if one[0] == "POST"] == []
        assert agent._pending.get("entry") is not None, "the ticket was taken down"


# ---------------------------------------------------------------------------
# THE SCREEN IS RE-ASSERTED WHILE A TICKET IS UP
# ---------------------------------------------------------------------------


def test_a_screen_that_dies_between_frames_voids_the_ticket_within_one_poll(tmp_path):
    """B12. A screen is only touched at `_show` and `_blank`, so one that failed
    in between left a code the driver cannot see, a health surface saying `ok`,
    and a next press that confirmed and vended it."""
    server = our_server(lane := a_lane(), act_token=ACT_TOKEN, arrive=False)
    with serving(server) as url:
        agent, ua, screen = agent_on(tmp_path, url)
        arrive(agent, lane)
        assert agent._pending.get("entry") is not None

        screen.refuse = True          # every write from here on is refused
        agent.poll()
        assert agent._pending.get("entry") is None, "the dead screen was never noticed"
        assert [one["reason"] for one in events_of(agent, AgentEventKind.TICKET_VOIDED)] == [
            "display_unavailable"
        ]
        assert [
            entry["state"]
            for entry in agent.health().to_dict()["codes"]
            if entry["code"] == "display_unavailable" and entry["subject"] == "front"
        ] == ["active"]
        # AND THE NEXT PRESS IS A HUMAN CASE.
        press(agent, ua)
        assert events_of(agent, AgentEventKind.TICKET_CONFIRMED) == []
        assert [one for one in server.requests.seen if one[0] == "POST"] == []


def test_a_screen_whose_geometry_cannot_be_read_voids_the_ticket_too(tmp_path):
    """The other half of the same fact: a screen that has GONE, rather than one
    that refuses a write. One exception for both, because to a driver at a
    barrier they are one fact."""
    server = our_server(lane := a_lane(), act_token=ACT_TOKEN, arrive=False)
    with serving(server) as url:
        agent, _ua, screen = agent_on(tmp_path, url)
        arrive(agent, lane)
        screen.readable = False
        agent.poll()
        assert agent._pending.get("entry") is None
        assert [one["reason"] for one in events_of(agent, AgentEventKind.TICKET_VOIDED)] == [
            "display_unavailable"
        ]


def test_a_reassert_that_does_not_happen_is_the_control(tmp_path):
    """THE CONTROL: stop re-asserting and B12's transcript comes back -- the
    ticket stays pending, no code goes active, and the press vends it."""
    server = our_server(lane := a_lane(), act_token=ACT_TOKEN, arrive=False)
    with serving(server) as url:
        agent, ua, screen = agent_on(tmp_path, url)
        arrive(agent, lane)
        agent._reassert = lambda lane: None
        screen.refuse = True
        for _ in range(6):
            agent.poll()
        assert agent._pending.get("entry") is not None
        assert events_of(agent, AgentEventKind.TICKET_VOIDED) == []
        press(agent, ua)
        assert events_of(agent, AgentEventKind.VEND_COMMANDED), (
            "the control did not reproduce the defect"
        )


def test_a_framebuffer_that_changes_mode_is_redrawn_at_the_new_stride(tmp_path):
    """B17. Reading the geometry once at startup is the same defect the document
    argues against, one step later: from the mode change on, the frame was
    written at the old stride, which is diagonal noise on the panel while the
    agent believed a code was up."""
    from gate_agent.display import Geometry, to_bytes

    server = our_server(lane := a_lane(), act_token=ACT_TOKEN, arrive=False)
    with serving(server) as url:
        agent, _ua, screen = agent_on(tmp_path, url)
        arrive(agent, lane)
        drawn = len(screen.frames)
        wide = Geometry(width=1920, height=1080, bits_per_pixel=32, stride=7680)
        screen.next_geometry = wide
        agent.poll()

        assert screen.geometry == wide
        assert len(screen.frames) > drawn, "nothing was redrawn"
        # THE BYTES MATCH THE NEW STRIDE, which is what the panel expects.
        assert len(to_bytes(screen.frames[-1], wide)) == wide.stride * wide.height
        changed = [
            one for one in agent.events(0).to_dict()["events"]
            if one["kind"] == "display_geometry_changed"
        ]
        assert [one["geometry"] for one in changed] == ["1920x1080@32"], changed
        assert changed[0]["display"] == "front"
        # AND THE TICKET IS STILL UP: a mode change is not a failure.
        assert agent._pending.get("entry") is not None


# ---------------------------------------------------------------------------
# A RESTART RECONCILES THE STORE
# ---------------------------------------------------------------------------


def test_a_ticket_left_issued_by_a_dead_process_is_voided_restarted(tmp_path):
    """B10. `restarted` was a PUBLISHED void reason no code path could write.

    The pending map is in memory and the STORE is not, and a restarted process
    never reconciled it -- so the crash paragraph in `docs/CONTRACT.md` rested
    on a reason nothing wrote, and the ticket a screen is still showing after a
    crash was a live-looking record for ever.
    """
    server = our_server(lane := a_lane(), act_token=ACT_TOKEN, arrive=False)
    with serving(server) as url:
        agent, _ua, _screen = agent_on(tmp_path, url)
        arrive(agent, lane)
        first = events_of(agent, AgentEventKind.TICKET_ISSUED)[0]["ticket_id"]
        store = TicketStore(tmp_path / "tickets")
        assert store.read(first).state == ISSUED

        # A SECOND PROCESS on the same store and the same screen.
        restarted, ua2, _screen2 = agent_on(tmp_path, url)
        settled = store.read(first)
        assert (settled.state, settled.void_reason) == (VOIDED, "restarted")
        assert restarted._pending == {}
        # THE PRESS CANNOT CONFIRM THE STRANDED ONE -- nothing is pending, so
        # the code the driver photographed is not a code this process holds. It
        # mints a NEW ticket and tells them about it, and the next press
        # confirms THAT one.
        press(restarted, ua2, "driver-1")
        second = events_of(restarted, AgentEventKind.TICKET_ISSUED)
        assert len(second) == 1 and second[0]["ticket_id"] != first
        assert events_of(restarted, AgentEventKind.TICKET_CONFIRMED) == []
        ua2.closed("driver-1")
        restarted.poll()
        press(restarted, ua2, "driver-2")

        # TWO RECORDS FOR ONE STAY, and the contract says which is which: the
        # stay is the vended one, the driver may be holding a photograph of the
        # other, and that other one is not an exit token.
        ids = store.all_ids()
        assert len(ids) == 2, ids
        states = sorted((store.read(one).state, store.read(one).void_reason) for one in ids)
        assert states == [(VENDED, None), (VOIDED, "restarted")], states
        assert store.read(first).state == VOIDED


def test_the_reconciliation_is_what_writes_restarted(tmp_path):
    """THE CONTROL: take it out and `state=issued` stands, which is B10."""
    server = our_server(lane := a_lane(), act_token=ACT_TOKEN, arrive=False)
    with serving(server) as url:
        agent, _ua, _screen = agent_on(tmp_path, url)
        arrive(agent, lane)
        first = events_of(agent, AgentEventKind.TICKET_ISSUED)[0]["ticket_id"]

        import gate_agent.agent as agent_module

        original = agent_module.Agent._reconcile
        agent_module.Agent._reconcile = lambda self: None
        try:
            agent_on(tmp_path, url)
        finally:
            agent_module.Agent._reconcile = original
        assert TicketStore(tmp_path / "tickets").read(first).state == ISSUED, (
            "the control did not reproduce the defect"
        )


def a_ticket_confirmed_but_never_recorded(tmp_path, url, lane, server):
    """The agent dies BETWEEN the lane's 202 and its own record.

    Driven through the real press: the `vended` write is dropped, which is
    exactly what a process that stopped existing between the two would leave.
    """
    from gate_agent import tickets as tickets_module

    agent, ua, _screen = agent_on(tmp_path, url)
    arrive(agent, lane)
    ticket_id = events_of(agent, AgentEventKind.TICKET_ISSUED)[0]["ticket_id"]
    original = tickets_module.TicketStore.write

    def dropping(self, record):
        if record.state == VENDED:
            raise SystemExit("the agent died between the 202 and its own record")
        original(self, record)

    tickets_module.TicketStore.write = dropping
    try:
        press(agent, ua)
    except SystemExit:
        pass
    finally:
        tickets_module.TicketStore.write = original
    return ticket_id


def test_a_confirmed_record_is_settled_by_replaying_the_vend(tmp_path):
    """B10's second shape. The stay exists at the lane, the agent's own record
    says `confirmed`, and the driver holds the photo.

    Settled by REPLAYING the vend with the record's own `Idempotency-Key` --
    round 6's replay guarantee: the lane answers from its idempotency store, the
    same answer, and nothing moves.
    """
    server = our_server(lane := a_lane(), act_token=ACT_TOKEN, arrive=False)
    with serving(server) as url:
        ticket_id = a_ticket_confirmed_but_never_recorded(tmp_path, url, lane, server)
        store = TicketStore(tmp_path / "tickets")
        stranded = store.read(ticket_id)
        assert stranded.state == CONFIRMED and stranded.vended_at is None
        posts = len([one for one in server.requests.seen if one[0] == "POST"])
        assert posts == 1, "the lane never saw the first vend"

        agent_on(tmp_path, url)          # THE RESTART
        settled = store.read(ticket_id)
        assert settled.state == VENDED, settled
        assert settled.lane_answer, "the lane's own cursor is not on the record"
        # THE REPLAY REACHED THE LANE, and it was answered from its idempotency
        # store rather than performed again.
        assert len([one for one in server.requests.seen if one[0] == "POST"]) == posts + 1


def test_a_confirmed_record_the_lane_cannot_settle_is_outcome_unknown(tmp_path):
    """The same shape with the LANE restarted in between: its idempotency store
    is gone, so the replay is a fresh request and the lane applies its own
    refusals to it. This build cannot say whether the barrier opened, and the
    record says exactly that rather than guessing either way."""
    server = our_server(lane := a_lane(), act_token=ACT_TOKEN, arrive=False)
    with serving(server) as url:
        ticket_id = a_ticket_confirmed_but_never_recorded(tmp_path, url, lane, server)

    # A NEW LANE at a new socket: nothing it holds knows that key.
    server2 = our_server(a_lane(), act_token=ACT_TOKEN, arrive=False)
    with serving(server2) as url2:
        agent_on(tmp_path, url2)
        settled = TicketStore(tmp_path / "tickets").read(ticket_id)
        assert (settled.state, settled.void_reason) == (VOIDED, "outcome_unknown"), settled
        assert settled.lane_answer, "what the lane said is not on the record"


# ---------------------------------------------------------------------------
# THE VOID REASONS THE RECORD CARRIES, ONE PER WAY A PRESS CAN END
# ---------------------------------------------------------------------------


def test_a_lane_that_cannot_be_asked_to_vend_is_lane_unreachable_on_the_record(tmp_path):
    """B7's row with no `lane_answer` at all.

    `lane_decided_again` is documented as *a new decision, or a reset cursor*,
    and it was written here -- where the lane could not be reached and said
    nothing. A reader taking the documented field got a wrong answer; a reader
    taking `lane_answer` got `None`.
    """
    server = our_server(lane := a_lane(), act_token=ACT_TOKEN, arrive=False)
    with serving(server) as url:
        agent, ua, _screen = agent_on(tmp_path, url)
        arrive(agent, lane)
        issued = events_of(agent, AgentEventKind.TICKET_ISSUED)[0]
    # THE LANE IS GONE, and the ticket is still on the screen in front of the
    # driver. The press is a confirmation and the vend has nowhere to go.
    press(agent, ua)
    record = TicketStore(tmp_path / "tickets").read(issued["ticket_id"])
    assert (record.state, record.void_reason) == (VOIDED, "lane_unreachable"), record
    assert record.lane_answer is None
    assert [
        entry["state"]
        for entry in agent.health().to_dict()["codes"]
        if entry["code"] == "lane_unavailable" and entry["subject"] == "entry"
    ] == ["active"]


def test_a_lane_that_will_not_consider_the_act_is_act_refused_on_the_record(tmp_path):
    """The third row: a 401 on the vend route. The lane did not decide anything
    and it did not refuse a vend -- it would not consider the request, which is
    a different fact and a different machine."""
    server = our_server(lane := a_lane(), act_token=ACT_TOKEN, arrive=False)
    with serving(server) as url:
        # The act token this agent holds is NOT the one the lane was given.
        agent, ua, _screen = agent_on(
            tmp_path, url, act_token="a-different-act-token-0000"
        )
        arrive(agent, lane)
        issued = events_of(agent, AgentEventKind.TICKET_ISSUED)[0]
        press(agent, ua)
        record = TicketStore(tmp_path / "tickets").read(issued["ticket_id"])
        assert (record.state, record.void_reason) == (VOIDED, "act_refused"), record
        assert [
            entry["state"]
            for entry in agent.health().to_dict()["codes"]
            if entry["code"] == "lane_act_refused" and entry["subject"] == "entry"
        ] == ["active"]
        assert events_of(agent, AgentEventKind.VEND_COMMANDED) == []
