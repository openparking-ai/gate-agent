"""A person's `OPEN_NOW` / `OPEN_AND_FLAG`, from the side of the person who keyed it.

Against OUR REAL LANE, over a socket, as `test_ticket_flow.py` is. The questions:

  * **Does the person hear what the lane answered?** On their own call, and the
    case does not end under the sentence.
  * **Is anybody rung twice?** A person is on the line. A refusal is said to
    them, the menu is read again, and nobody is dialled.
  * **Does a dead screen stop a person's open?** It does not: the screen is the
    driver's photograph, and the person has authorised the barrier.
  * **Does the code come down?** A ticket used for a person's open is not
    pending, and nothing that clears pending tickets ever saw it.
  * **Does "do not open" hold?** A ticket at that lane cannot be vended by the
    driver's next press against what the person said.
  * **Is a call that is closing somebody on the phone?** No: the next car's
    code minted under its closing sentences is told, and one press vends it.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from lane_controller import VehicleIdentity

from conftest import INTERCOM_ACCOUNT, FakeClock, agent_config_for, agent_for
from fake_ua import FakeUa
from gate_agent.act import LaneUnreachable
from gate_agent.config import Target, TicketSettings
from gate_agent.contract import AgentEventKind, TargetKind
from gate_agent.tickets import CONFIRMED, VENDED, VOIDED, TicketStore
from ours import our_lane, our_server
from serving import serving
from test_ticket_flow import (
    ACT_TOKEN,
    INTERCOM,
    SIGNING_KEY,
    FakeScreen,
    a_lane,
    agent_on,
    arrive,
    events_of,
    press,
    run,
)


@dataclass
class TimedUa(FakeUa):
    """`FakeUa`, and WHEN each file was played and the calls were ended.

    On the agent's own clock. "The person heard it" is a file that was played
    AND a call that was still up for the length of it; the record of commands
    alone cannot tell a sentence heard from one started and cut off.
    """

    clock: object = None
    at: list = field(default_factory=list)

    def play(self, call_id: str, path: str) -> None:
        super().play(call_id, path)
        self.at.append((self.clock(), "play", self.leg_of(call_id), path))

    def hangup_all(self) -> None:
        super().hangup_all()
        self.at.append((self.clock(), "hangup_all", "", ""))


def an_agent(tmp_path, url, *, act_token=ACT_TOKEN, display=True, languages=("en",),
             line_timeout_seconds=10.0):
    """`agent_on`, on a `TimedUa` and with the knobs these tests turn."""
    screen = FakeScreen()
    base = agent_config_for(
        tmp_path, lane_url=url, line_timeout_seconds=line_timeout_seconds
    )
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
        tickets=TicketSettings(signing_key=SIGNING_KEY, directory=tmp_path / "tickets"),
        driver_languages=languages,
    )
    clock = FakeClock()
    ua = TimedUa(clock=clock)
    agent = agent_for(config, ua, clock=clock)
    return agent, ua, screen


def dials(ua) -> list[str]:
    return [arg for verb, arg in ua.commands if verb == "dial"]


def to_the_person(agent, ua, *, bridged=True) -> str:
    """Ring the person, have them answer, and wait for the bridge (or not)."""
    for _ in range(60):
        run(agent, 1)
        if dials(ua):
            break
    operator = dials(ua)[0].split("-> ")[1]
    ua.established(operator)
    if not bridged:
        # ONE poll: the person is being briefed and the menu is still queued.
        run(agent, 1)
        return operator
    for _ in range(60):
        run(agent, 1)
        if ua.bridged_at is not None:
            break
    return operator


def operator_heard(ua, after: int = 0) -> list[str]:
    """The basenames played to the person, from position `after` of `played`."""
    return [
        str(path).rsplit("/", 1)[-1]
        for leg, path in ua.played[after:]
        if leg == "operator"
    ]


def a_deny_lane():
    return a_lane("deny", None)


# ---------------------------------------------------------------------------
# The person hears the lane's answer, and is not rung again
# ---------------------------------------------------------------------------


def test_a_refused_open_is_said_to_the_person_and_nobody_is_rung_again(tmp_path):
    """FM-11. `OPEN_AND_FLAG` on a deny is refused `not_completable` -- the
    contract's own example of an ordinary refusal.

    It used to go back to "speaking the case": the person heard nothing, was
    DIALLED AGAIN while still on the line, and their first call was no longer
    the case's -- so when the driver hung up, only the second was ended."""
    server = our_server(lane := a_deny_lane(), act_token=ACT_TOKEN, arrive=False)
    with serving(server) as url:
        agent, ua, _screen = an_agent(tmp_path, url)
        arrive(agent, lane)
        press(agent, ua)
        operator = to_the_person(agent, ua)
        before = len(ua.played)
        ua.dtmf(operator, "2")
        for _ in range(30):
            run(agent, 1)
            if agent.session is None or agent.session.deadline is not None:
                break

        refused = events_of(agent, AgentEventKind.VEND_REFUSED)
        assert [one["code"] for one in refused] == ["not_completable"]
        # EXACTLY ONE CALL TO A PERSON, for the whole case.
        assert len(dials(ua)) == 1, ua.commands
        assert len(events_of(agent, AgentEventKind.HUMAN_CALLED)) == 1
        assert events_of(agent, AgentEventKind.HUMAN_UNREACHABLE) == []
        # THE PERSON HEARS IT, the code's own sentence, then the menu again.
        heard = operator_heard(ua, before)
        assert heard[:3] == [
            "operator.ticket_refused.wav",
            "operator.vend_refused.not_completable.wav",
            "menu.open_now.wav",
        ], heard
        assert agent.session is not None and agent.session.state.name == "WAITING_DIGIT"
        assert agent.session.operator_call == operator
        # AND THEIR TIME TO KEY STARTS WHEN THE MENU HAS BEEN READ, not under it.
        assert heard[-1] == "menu.call_back.wav", heard
        assert agent.session.deadline is not None
        assert agent.session.deadline - agent._clock() >= 15.0

        # THE SAME PERSON, THE SAME CALL: `OPEN_NOW` overrides the deny.
        ua.dtmf(operator, "1")
        run(agent, 20)
        commanded = events_of(agent, AgentEventKind.VEND_COMMANDED)
        assert [one["authorised_by"] for one in commanded] == ["human_open_now"]
        assert "operator.vend_commanded.wav" in operator_heard(ua, before)
        assert len(dials(ua)) == 1, ua.commands
        # AND EVERY CALL OF THE CASE WAS ENDED WITH IT.
        assert agent.session is None
        assert ("hangup_all", "") in ua.commands


def test_when_the_driver_leaves_after_a_refusal_the_persons_call_is_ended(tmp_path):
    """The leg that used to be left live: the driver hangs up after a refusal,
    and the ONE operator call there is goes with the case."""
    server = our_server(lane := a_deny_lane(), act_token=ACT_TOKEN, arrive=False)
    with serving(server) as url:
        agent, ua, _screen = an_agent(tmp_path, url)
        arrive(agent, lane)
        press(agent, ua)
        operator = to_the_person(agent, ua)
        ua.dtmf(operator, "2")
        run(agent, 6)
        ua.closed("driver-1")
        run(agent, 2)
        assert agent.session is None
        assert ("hangup", operator) in ua.commands, ua.commands
        assert len(dials(ua)) == 1, ua.commands


def test_an_act_the_lane_will_not_consider_is_said_to_the_person(tmp_path):
    """A 401 on the vend route, on a person's open: the person hears that the
    ticket was refused and that the reason has no words here -- and the menu."""
    server = our_server(lane := a_deny_lane(), act_token=ACT_TOKEN, arrive=False)
    with serving(server) as url:
        agent, ua, _screen = an_agent(tmp_path, url, act_token="a-different-act-token-0000")
        arrive(agent, lane)
        press(agent, ua)
        operator = to_the_person(agent, ua)
        before = len(ua.played)
        ua.dtmf(operator, "1")
        run(agent, 12)
        heard = operator_heard(ua, before)
        assert heard[:3] == [
            "operator.ticket_refused.wav",
            "operator.vend_refused.unknown.wav",
            "menu.open_now.wav",
        ], heard
        assert len(dials(ua)) == 1, ua.commands
        assert agent.session is not None and agent.session.state.name == "WAITING_DIGIT"


def test_a_lane_that_does_not_answer_the_vend_is_said_to_the_person(tmp_path):
    """The vend route does not answer: the person hears the lane could not be
    reached, and the menu. Nobody is rung."""
    server = our_server(lane := a_deny_lane(), act_token=ACT_TOKEN, arrive=False)
    with serving(server) as url:
        agent, ua, _screen = an_agent(tmp_path, url)
        arrive(agent, lane)
        press(agent, ua)
        operator = to_the_person(agent, ua)

        def unreachable(**_kwargs):
            raise LaneUnreachable("the lane did not answer the vend")

        agent._acts["entry"].vend = unreachable
        before = len(ua.played)
        ua.dtmf(operator, "1")
        run(agent, 20)
        heard = operator_heard(ua, before)
        assert heard[:2] == ["operator_case.lane_unavailable.wav", "menu.open_now.wav"], heard
        assert len(dials(ua)) == 1, ua.commands


def test_the_person_hears_the_vend_was_commanded_before_anybody_is_hung_up(tmp_path):
    """FM-11's other half: on a `202` the person heard NOTHING -- the sentence
    went to the driver only, and then every call was ended.

    Keyed DURING THE BRIEFING, so the rest of the menu is still queued on the
    person's leg: the calls used to end when the DRIVER's leg went quiet, and
    the person's sentence was never even started."""
    server = our_server(lane := a_deny_lane(), act_token=ACT_TOKEN, arrive=False)
    with serving(server) as url:
        agent, ua, _screen = an_agent(tmp_path, url)
        arrive(agent, lane)
        press(agent, ua)
        operator = to_the_person(agent, ua, bridged=False)
        ua.dtmf(operator, "1")
        run(agent, 30)
        assert events_of(agent, AgentEventKind.VEND_COMMANDED)
        plays = [
            (at, path) for at, verb, leg, path in ua.at
            if verb == "play" and leg == "operator" and path.endswith("operator.vend_commanded.wav")
        ]
        assert plays, f"the person was never told: {operator_heard(ua)}"
        ended = [at for at, verb, _leg, _path in ua.at if verb == "hangup_all"]
        assert ended, "the case never ended"
        played_at, path = plays[0]
        from pathlib import Path

        assert ended[0] >= played_at + agent._durations[Path(path)], (played_at, ended)


def test_a_person_who_hangs_up_after_keying_open_is_not_a_failed_line(tmp_path):
    """The person keys `OPEN_NOW` and puts the phone down. Their sentence can
    no longer be said, and it used to wait out `line_timeout_seconds` on a call
    that was gone -- then raise `audio_playback_failed` and end the case under
    the driver's own sentences."""
    server = our_server(lane := a_deny_lane(), act_token=ACT_TOKEN, arrive=False)
    with serving(server) as url:
        agent, ua, _screen = an_agent(tmp_path, url, line_timeout_seconds=3.0)
        arrive(agent, lane)
        press(agent, ua)
        operator = to_the_person(agent, ua)
        ua.dtmf(operator, "1")
        ua.closed(operator)
        run(agent, 20)
        assert events_of(agent, AgentEventKind.VEND_COMMANDED)
        assert events_of(agent, AgentEventKind.CASE_NOT_SPOKEN) == []
        assert [
            entry["state"]
            for entry in agent.health().to_dict()["codes"]
            if entry["code"] == "audio_playback_failed" and entry["subject"] == "operator"
        ] != ["active"]
        driver = [str(path).rsplit("/", 1)[-1] for leg, path in ua.played if leg == "driver"]
        assert "ticket.vend_commanded.wav" in driver, driver


def test_at_a_standalone_door_the_person_hears_the_outcome_before_the_call_ends(tmp_path):
    """The relay answers after the driver's sentence is over, so the calls used
    to end in the same poll that STARTED the person's sentence."""
    from pathlib import Path

    from test_relay import FakeAxis, a_relay, axis_server, settle

    unit = FakeAxis()
    # The unit takes a moment, and the driver's one sentence is over by the
    # time its answer is collected: that is the order that cut the person off.
    unit.answer_after = 0.2
    with serving(axis_server(unit)) as url:
        base = agent_config_for(tmp_path, standalone=True)
        config = replace(
            base,
            intercoms=(replace(base.intercoms[0], relay=a_relay(url=url)),),
            tickets=TicketSettings(signing_key=SIGNING_KEY, directory=tmp_path / "tickets"),
            driver_languages=("en",),
        )
        clock = FakeClock()
        ua = TimedUa(clock=clock)
        agent = agent_for(config, ua, clock=clock)
        ua.incoming(INTERCOM, call_id="driver-1", account_user=INTERCOM_ACCOUNT)
        operator = to_the_person(agent, ua)
        ua.dtmf(operator, "1")
        agent.poll()
        clock.advance(4.0)
        settle(agent, clock)
        assert unit.authenticated, "the relay was never pulsed"
    plays = [
        (at, path) for at, verb, leg, path in ua.at
        if verb == "play" and leg == "operator" and path.endswith("operator.vend_commanded.wav")
    ]
    ended = [at for at, verb, _leg, _path in ua.at if verb == "hangup_all"]
    assert plays and ended, ua.at
    assert ended[0] >= plays[0][0] + agent._durations[Path(plays[0][1])], (plays, ended)


# ---------------------------------------------------------------------------
# A dead screen does not stop a person's open
# ---------------------------------------------------------------------------


def test_a_screen_that_refuses_does_not_stop_a_persons_open(tmp_path):
    """FM-12. The person's open was minted through the step that shows a code,
    and a screen that refused voided it -- so the person heard "this entrance
    is not set up for this system to open it" at a lane that could act, and the
    lane was never asked."""
    server = our_server(lane := a_deny_lane(), act_token=ACT_TOKEN, arrive=False)
    with serving(server) as url:
        agent, ua, screen = an_agent(tmp_path, url)
        screen.refuse = True
        arrive(agent, lane)
        press(agent, ua)
        operator = to_the_person(agent, ua)
        ua.dtmf(operator, "1")
        run(agent, 20)
        commanded = events_of(agent, AgentEventKind.VEND_COMMANDED)
        assert [one["authorised_by"] for one in commanded] == ["human_open_now"]
        assert ("POST", "/v1/lane/vend") in server.requests.seen
        heard = operator_heard(ua)
        assert "operator.vend_commanded.wav" in heard, heard
        assert "operator.cannot_open.wav" not in heard, heard
        record = TicketStore(tmp_path / "tickets").read(commanded[0]["ticket_id"])
        assert record.state == VENDED, record
        # AND THE SCREEN IS STILL REPORTED: it is broken, whatever opened.
        assert [
            entry["state"]
            for entry in agent.health().to_dict()["codes"]
            if entry["code"] == "display_unavailable" and entry["subject"] == "front"
        ] == ["active"]


def test_a_screen_that_refuses_does_not_stop_a_standalone_relay(tmp_path):
    """The same at a standalone door with a relay and a screen: the relay was
    never asked, and the log said no record could be written when one had."""
    from test_relay import FakeAxis, a_relay, axis_server, settle

    unit = FakeAxis()
    screen = FakeScreen()
    screen.refuse = True
    with serving(axis_server(unit)) as url:
        base = agent_config_for(tmp_path, standalone=True)
        config = replace(
            base,
            intercoms=(
                replace(base.intercoms[0], relay=a_relay(url=url), display="front"),
            ),
            displays={"front": screen},
            tickets=TicketSettings(signing_key=SIGNING_KEY, directory=tmp_path / "tickets"),
            driver_languages=("en",),
        )
        clock = FakeClock()
        ua = FakeUa()
        agent = agent_for(config, ua, clock=clock)
        ua.incoming(INTERCOM, call_id="driver-1", account_user=INTERCOM_ACCOUNT)
        operator = to_the_person(agent, ua)
        ua.dtmf(operator, "1")
        settle(agent, clock)
        assert unit.authenticated, "the relay was never pulsed"
    assert events_of(agent, AgentEventKind.RELAY_PULSED)
    store = TicketStore(tmp_path / "tickets")
    assert [store.read(one).state for one in store.all_ids()] == [VENDED]
    assert "operator.vend_commanded.wav" in operator_heard(ua)


# ---------------------------------------------------------------------------
# No act route: the record is closed at the press
# ---------------------------------------------------------------------------


def test_a_press_at_a_lane_with_no_act_token_closes_the_record(tmp_path):
    """FL-45. A display and no act token: the ticket is offered, the press is
    taken, and nothing is sent -- so the record was left `confirmed` and the
    next start wrote `outcome_unknown` over an outcome that was known."""
    server = our_server(lane := a_lane(), act_token=ACT_TOKEN, arrive=False)
    with serving(server) as url:
        agent, ua, _screen = agent_on(tmp_path, url, act_token=None)
        arrive(agent, lane)
        issued = events_of(agent, AgentEventKind.TICKET_ISSUED)[0]
        press(agent, ua)
        assert events_of(agent, AgentEventKind.TICKET_CONFIRMED)
        assert [one for one in server.requests.seen if one[0] == "POST"] == []
        store = TicketStore(tmp_path / "tickets")
        record = store.read(issued["ticket_id"])
        assert (record.state, record.void_reason) == (VOIDED, "no_act_route"), record

        # A RESTART on the same store leaves it as it is.
        agent_on(tmp_path, url, act_token=None)
        settled = store.read(issued["ticket_id"])
        assert (settled.state, settled.void_reason) == (VOIDED, "no_act_route"), settled
        assert settled.state != CONFIRMED


# ---------------------------------------------------------------------------
# A ticket minted while a person is on the call, and "do not open"
# ---------------------------------------------------------------------------


def a_lane_that_denies_then_reads_nothing():
    """A deny, then -- the same driver, plate covered -- a read with no plate."""
    controller = our_lane(
        identities=[
            VehicleIdentity(plate="DENIEDME", confidence=0.99, presence=True),
            VehicleIdentity(plate=None, presence=True),
        ],
        arrivals=2,
    )
    controller.cache.default_action = "deny"
    return controller


def a_ticket_minted_while_the_person_is_on_the_line(tmp_path, url, lane):
    agent, ua, screen = an_agent(tmp_path, url)
    arrive(agent, lane)
    press(agent, ua)
    operator = to_the_person(agent, ua)
    assert not events_of(agent, AgentEventKind.TICKET_ISSUED)
    # THE LANE DECIDES AGAIN, mid-call: the driver re-armed with the plate covered.
    lane.run_once()
    run(agent, 3)
    issued = events_of(agent, AgentEventKind.TICKET_ISSUED)
    assert len(issued) == 1, agent.events(0).to_dict()["events"]
    return agent, ua, screen, operator, issued[0]["ticket_id"]


def test_a_ticket_minted_while_a_person_is_on_the_call_is_not_marked_told(tmp_path):
    """FL-47. Past the first step the agent thought nobody was on the phone, so
    the code was marked told -- and the driver's next press vended it."""
    server = our_server(lane := a_lane_that_denies_then_reads_nothing(),
                        act_token=ACT_TOKEN, arrive=False)
    with serving(server) as url:
        agent, ua, _screen, _operator, ticket_id = a_ticket_minted_while_the_person_is_on_the_line(
            tmp_path, url, lane
        )
        assert agent._pending["entry"].told_at is None
        assert TicketStore(tmp_path / "tickets").read(ticket_id).told_at is None

        # THE DRIVER HANGS UP AND PRESSES AGAIN: they are TOLD, not vended.
        ua.closed("driver-1")
        run(agent, 3)
        press(agent, ua, "driver-2")
        assert events_of(agent, AgentEventKind.VEND_COMMANDED) == []
        assert [one for one in server.requests.seen if one[0] == "POST"] == []
        assert events_of(agent, AgentEventKind.TICKET_ON_SCREEN)


def test_do_not_open_voids_the_ticket_pending_at_that_lane(tmp_path):
    """FL-47's second half: the person keys `do_not_open`, and the ticket at
    that lane stayed pending for the driver's next press to vend."""
    server = our_server(lane := a_lane_that_denies_then_reads_nothing(),
                        act_token=ACT_TOKEN, arrive=False)
    with serving(server) as url:
        agent, ua, screen, operator, ticket_id = a_ticket_minted_while_the_person_is_on_the_line(
            tmp_path, url, lane
        )
        blanked = screen.blanked
        ua.dtmf(operator, "3")
        run(agent, 3)
        voided_events = events_of(agent, AgentEventKind.TICKET_VOIDED)
        assert [(one["ticket_id"], one["reason"]) for one in voided_events] == [
            (ticket_id, "do_not_open")
        ]
        record = TicketStore(tmp_path / "tickets").read(ticket_id)
        assert (record.state, record.void_reason) == (VOIDED, "do_not_open"), record
        assert "entry" not in agent._pending
        assert screen.blanked > blanked

        # THE DRIVER'S NEXT PRESS opens nothing.
        run(agent, 10)
        press(agent, ua, "driver-2")
        run(agent, 10)
        assert events_of(agent, AgentEventKind.VEND_COMMANDED) == []
        assert [one for one in server.requests.seen if one[0] == "POST"] == []


def the_next_car_is_told_at_the_issue_and_one_press_vends(tmp_path, agent, ua, lane):
    """Car one's call is CLOSING; the lane decides for car two NOW.

    Car two's driver is on no call, so their code is told at the issue, as it
    always was, and ONE press vends it: no `ticket.on_screen`, no second press.
    """
    assert agent.session is not None and agent.session.state.name == "CLOSING"
    lane.run_once()
    for _ in range(3):
        agent.poll()
    # STILL CLOSING when the code was minted: that is the case under test.
    assert agent.session is not None and agent.session.state.name == "CLOSING"
    issued = events_of(agent, AgentEventKind.TICKET_ISSUED)
    assert issued, agent.events(0).to_dict()["events"]
    next_car = issued[-1]["ticket_id"]
    assert agent._pending["entry"].ticket.ticket_id == next_car
    assert agent._pending["entry"].told_at is not None
    assert TicketStore(tmp_path / "tickets").read(next_car).told_at is not None

    # Car one's call ends; car two presses ONCE.
    run(agent, 20)
    assert agent.session is None
    vends_before = len(events_of(agent, AgentEventKind.VEND_COMMANDED))
    press(agent, ua, "driver-2")
    assert events_of(agent, AgentEventKind.TICKET_ON_SCREEN) == []
    commanded = events_of(agent, AgentEventKind.VEND_COMMANDED)
    assert len(commanded) == vends_before + 1, agent.events(0).to_dict()["events"]
    assert commanded[-1]["ticket_id"] == next_car
    assert commanded[-1]["authorised_by"] == "display_code_confirmed"


def test_a_ticket_minted_while_a_self_service_call_is_closing_is_told(tmp_path):
    """Car one confirms its code and the vend is commanded; its call stays
    CLOSING while `ticket.confirmed` and `ticket.vend_commanded` play. A
    closing call is not somebody on the phone: the next car's code is told."""
    server = our_server(lane := a_lane(), act_token=ACT_TOKEN, arrive=False)
    with serving(server) as url:
        agent, ua, _screen = an_agent(tmp_path, url)
        arrive(agent, lane)
        ua.incoming(INTERCOM, call_id="driver-1", account_user=INTERCOM_ACCOUNT)
        agent.poll()
        assert len(events_of(agent, AgentEventKind.VEND_COMMANDED)) == 1
        the_next_car_is_told_at_the_issue_and_one_press_vends(tmp_path, agent, ua, lane)


def test_a_ticket_minted_while_a_persons_open_is_closing_is_told(tmp_path):
    """The same, after a person's `OPEN_NOW`: the vend is commanded and the
    call is CLOSING while both of them hear so."""
    server = our_server(lane := a_lane_that_denies_then_reads_nothing(),
                        act_token=ACT_TOKEN, arrive=False)
    with serving(server) as url:
        agent, ua, _screen = an_agent(tmp_path, url)
        arrive(agent, lane)
        press(agent, ua)
        operator = to_the_person(agent, ua)
        ua.dtmf(operator, "1")
        agent.poll()
        assert [one["authorised_by"] for one in events_of(
            agent, AgentEventKind.VEND_COMMANDED)] == ["human_open_now"]
        the_next_car_is_told_at_the_issue_and_one_press_vends(tmp_path, agent, ua, lane)


# ---------------------------------------------------------------------------
# The code a person's open put up comes down
# ---------------------------------------------------------------------------


def test_the_code_a_persons_open_put_up_comes_down_with_the_case(tmp_path):
    """FL-58. The ticket was shown and dropped from the pending list at once,
    so nothing that clears tickets ever saw it: an idle lane showed it to the
    next car. It stays up while the driver is told, and comes down with the
    case."""
    server = our_server(lane := a_deny_lane(), act_token=ACT_TOKEN, arrive=False)
    with serving(server) as url:
        agent, ua, screen = an_agent(tmp_path, url)
        arrive(agent, lane)
        press(agent, ua)
        operator = to_the_person(agent, ua)
        frames, blanked = len(screen.frames), screen.blanked
        ua.dtmf(operator, "1")
        run(agent, 1)
        assert events_of(agent, AgentEventKind.VEND_COMMANDED)
        assert len(screen.frames) == frames + 1, "the person's ticket was never shown"
        # UP while the driver is being told.
        assert agent.session is not None
        assert screen.blanked == blanked
        run(agent, 20)
        # DOWN with the case.
        assert agent.session is None
        assert screen.blanked > blanked
        # And it stays down: 400 seconds of an idle lane draw nothing over it.
        frames_after = len(screen.frames)
        run(agent, 200)
        assert len(screen.frames) == frames_after


def test_a_refused_persons_ticket_comes_down_at_once(tmp_path):
    """A ticket the lane refused is voided: a photograph of it is a code for a
    stay that does not exist, so it does not wait for the end of the call."""
    server = our_server(lane := a_deny_lane(), act_token=ACT_TOKEN, arrive=False)
    with serving(server) as url:
        agent, ua, screen = an_agent(tmp_path, url)
        arrive(agent, lane)
        press(agent, ua)
        operator = to_the_person(agent, ua)
        blanked = screen.blanked
        ua.dtmf(operator, "2")
        run(agent, 1)
        assert events_of(agent, AgentEventKind.VEND_REFUSED)
        assert agent.session is not None
        assert screen.blanked > blanked


def test_the_code_that_comes_down_is_never_the_next_cars(tmp_path):
    """The person's ticket comes down with the case -- but the lane may have
    decided for the next car in the seconds before the case ended, and THAT
    code is on the screen now. It stays."""
    server = our_server(lane := a_lane_that_denies_then_reads_nothing(),
                        act_token=ACT_TOKEN, arrive=False)
    with serving(server) as url:
        agent, ua, screen = an_agent(tmp_path, url)
        arrive(agent, lane)
        press(agent, ua)
        operator = to_the_person(agent, ua)
        ua.dtmf(operator, "1")
        run(agent, 1)
        assert events_of(agent, AgentEventKind.VEND_COMMANDED)
        assert agent.session is not None
        # THE NEXT CAR, while the driver is still being told.
        lane.run_once()
        run(agent, 1)
        assert "entry" in agent._pending, agent.events(0).to_dict()["events"]
        blanked = screen.blanked
        run(agent, 20)
        assert agent.session is None
        assert "entry" in agent._pending
        assert screen.blanked == blanked
