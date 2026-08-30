"""The dialogue: who hears what, in what order, and what is recorded.

Every assertion here is against the RECORD the user agent kept -- which file was
played into which leg, and when the two were put together -- rather than against
the agent's own description of what it did. An agent asked to describe its own
behaviour will describe its intentions.

The clock is a `FakeClock` a test advances. Audio finishes on a duration read out
of the shipped file, so `advance(60)` is "let everything queued be said"; the
timers are the same clock, which is what makes `no_answer_seconds` and
`nothing_usable_seconds` testable without waiting for either.
"""

from __future__ import annotations

import pytest

from conftest import FakeClock, agent_config_for, agent_for
from fake_ua import FakeUa
from foreign_lane import ForeignLane
from foreign_lane import make_server as foreign_server
from gate_agent.agent import REPROMPTS
from gate_agent.contract import AgentCase, AgentEventKind, Authorisation
from gate_agent.lines import TEXT
from serving import serving

INTERCOM = "sip:door1@10.0.0.9"
UNDECLARED = "sip:stranger@10.9.9.9"


@pytest.fixture
def lane():
    served = ForeignLane()
    served.decision = {
        "outcome": "fallback",
        "reason": "engine_unreachable",
        "fallback": "engine_unreachable",
        "cause": "unreachable",
        "presence": None,
        "at": "2026-08-30T14:03:11.482913+00:00",
        "read_ref": None,
    }
    served.transit = {"state": "none", "since": None}
    with serving(foreign_server(served)) as url:
        yield served, url


def running(tmp_path, url, clock=None, **kwargs):
    clock = clock or FakeClock()
    ua = FakeUa()
    agent = agent_for(agent_config_for(tmp_path, lane_url=url, **kwargs), ua, clock=clock)
    return agent, ua, clock


def kinds(agent) -> list[str]:
    return [event["kind"] for event in agent.events(0).to_dict()["events"]]


def files(ua, leg: str) -> list[str]:
    return [path.rsplit("/", 2)[-2] + "/" + path.rsplit("/", 1)[-1]
            for played_leg, path in ua.played if played_leg == leg]


def pump(agent, clock, until, step: float = 2.0, limit: int = 200):
    """Poll and advance until `until()` holds, or say what it was waiting for.

    Speech takes real seconds -- the durations come out of the shipped files --
    so a dialogue only moves when the clock does. This is the whole of "let time
    pass", and it is bounded so a test that never gets there fails saying so
    rather than hanging.
    """
    for _ in range(limit):
        agent.poll()
        if until():
            return
        clock.advance(step)
    raise AssertionError("the dialogue never reached the state this test waits for")


def said(ua, fragment: str) -> bool:
    return any(fragment in arg for verb, arg in ua.commands if verb == "play")


def answer(agent, ua, clock, peer=INTERCOM):
    ua.incoming(peer)
    pump(agent, clock, lambda: agent.session is None or bool(ua.played))
    return agent.session


# ---------------------------------------------------------------------------
# The driver's side
# ---------------------------------------------------------------------------


def test_the_case_plays_in_every_declared_language_in_order(tmp_path, lane):
    """The driver has no keypad, so every sentence plays in every language.

    IN THE DECLARED ORDER. A site that lists English then Spanish gets English
    then Spanish, and a language skipped is somebody at a barrier who was told
    nothing.
    """
    _served, url = lane
    agent, ua, clock = running(tmp_path, url, driver_languages=("en", "es"))
    ua.incoming(INTERCOM)
    pump(agent, clock, lambda: len(files(ua, "driver")) >= 2)
    spoken = files(ua, "driver")
    assert spoken[:2] == [
        "en/case.identification_unavailable.wav",
        "es/case.identification_unavailable.wav",
    ], spoken


def test_the_order_is_the_sites_and_not_this_packages(tmp_path, lane):
    """The control on the test above: reverse the declaration, reverse the audio.

    Without it, "English then Spanish" would pass on a build that always played
    English first, whatever a site declared.
    """
    _served, url = lane
    agent, ua, clock = running(tmp_path, url, driver_languages=("es", "en"))
    ua.incoming(INTERCOM)
    pump(agent, clock, lambda: len(files(ua, "driver")) >= 2)
    assert files(ua, "driver")[:2] == [
        "es/case.identification_unavailable.wav",
        "en/case.identification_unavailable.wav",
    ]


def test_a_dead_engine_never_tells_a_driver_to_clean_their_plate(tmp_path, lane):
    """The sentence the module was reordered around, asserted on the WORDS.

    `engine_unreachable` and `low_confidence` used to arrive as the same code.
    The first one must not produce an instruction about the plate, and the way
    to check that is to read what the file says, not which file it is.
    """
    served, url = lane
    agent, ua, clock = running(tmp_path, url)
    ua.incoming(INTERCOM)
    agent.poll()
    assert agent.session.case is AgentCase.IDENTIFICATION_UNAVAILABLE
    said = TEXT["case.identification_unavailable"]["en"].lower()
    assert "plate" not in said, said
    # The control: the case that IS about a plate says so, so the assertion
    # above is about this sentence rather than about a word nothing uses.
    assert "plate" in TEXT["case.plate_not_read"]["en"].lower()


def test_nothing_to_do_ends_the_call_without_calling_anybody(tmp_path, lane):
    """The button pressed after a normal entry. One message, and goodbye."""
    served, url = lane
    served.decision = {**served.decision, "outcome": "allow", "reason": "allow",
                       "fallback": None}
    served.transit = {"state": "confirmed", "since": None}
    agent, ua, clock = running(tmp_path, url)
    ua.incoming(INTERCOM)
    pump(agent, clock, lambda: agent.session is None)
    assert [verb for verb, _ in ua.commands if verb == "dial"] == []
    assert "hangup_all" in [verb for verb, _ in ua.commands]
    assert kinds(agent) == ["call_answered", "case_spoken", "call_ended"]


# ---------------------------------------------------------------------------
# The undeclared intercom
# ---------------------------------------------------------------------------


def test_a_call_from_an_undeclared_intercom_gets_one_message_and_ends(tmp_path, lane):
    """One fixed message, an event, a code, and NO lane is read or guessed."""
    served, url = lane
    agent, ua, clock = running(tmp_path, url)
    ua.incoming(UNDECLARED)
    pump(agent, clock, lambda: agent.session is None)
    assert files(ua, "driver") == [
        "en/driver.undeclared_intercom.wav",
        "es/driver.undeclared_intercom.wav",
    ]
    assert "dial" not in [verb for verb, _ in ua.commands]
    assert kinds(agent)[0] == AgentEventKind.CALL_FROM_UNDECLARED_INTERCOM.value
    entry = [
        one
        for one in agent.health().to_dict()["codes"]
        if one["code"] == "call_from_undeclared_intercom"
    ]
    assert [one for one in entry if one["state"] == "active"], entry
    # Nothing was asked of the lane at all: the agent never guesses which
    # barrier a stranger is standing at.
    assert served.requests == []


def test_a_declared_intercom_is_recognised_through_a_display_name(tmp_path, lane):
    """An intercom's `From` is not stable to the character, and must not have to be.

    A door station sends `"Door 1" <sip:door1@10.0.0.9:5060>;tag=…` on one call
    and the bare URI on the next. A mapping keyed on the whole string would
    answer one of them `this intercom is not configured`.
    """
    _served, url = lane
    agent, ua, clock = running(tmp_path, url)
    ua.incoming('"Door 1" <sip:door1@10.0.0.9:5060>;tag=99')
    agent.poll()
    assert kinds(agent)[0] == AgentEventKind.CALL_ANSWERED.value


# ---------------------------------------------------------------------------
# The human, the briefing, and the bridge
# ---------------------------------------------------------------------------


def brief_and_bridge(tmp_path, url, clock=None, **kwargs):
    """Answer, speak the case, call the human, brief them, and bridge."""
    agent, ua, clock = running(tmp_path, url, clock=clock, **kwargs)
    ua.incoming(INTERCOM)
    pump(agent, clock, lambda: any(verb == "dial" for verb, _ in ua.commands))
    operator = [arg for verb, arg in ua.commands if verb == "dial"][0].split("-> ")[1]
    ua.established(operator)
    pump(agent, clock, lambda: ua.bridged_at is not None)
    return agent, ua, clock, operator


def test_the_operator_is_briefed_privately_and_only_then_bridged(tmp_path, lane):
    """Everything the operator hears before the bridge is heard by them alone.

    This is the whole reason the seam has a `bridge` verb rather than a user
    agent that mixes whatever is up: the case and the menu are said to a person
    who has not been put through to the driver yet.
    """
    _served, url = lane
    agent, ua, _clock, _operator = brief_and_bridge(tmp_path, url)
    assert ua.bridged_at is not None, "the two legs were never put together"
    before = [
        entry for entry in ua.commands[: ua.bridged_at] if entry[0] == "play"
    ]
    operator_before = [arg for _verb, arg in before if arg.startswith("operator ")]
    assert any("operator_case." in one for one in operator_before)
    assert any("menu.open_now" in one for one in operator_before)
    # And nothing was played to the DRIVER between the human answering and the
    # bridge: the briefing is private.
    assert not any(
        "menu." in arg or "operator_case." in arg
        for _verb, arg in before
        if arg.startswith("driver ")
    )


def test_the_operators_context_carries_no_plate(tmp_path, lane):
    """The message a person hears is the intercom's name and the case. Nothing else.

    Planted: a plate is put on the lane's payload, in the one field a lane may
    carry text in. It must not reach any file this agent plays, and it must not
    reach the event log -- and the control is that the plant is genuinely on the
    wire, which is asserted against the lane's own served state.
    """
    served, url = lane
    served.decision = {**served.decision, "read_ref": "PLANTED9", "reason": "no_plate_read",
                       "fallback": "no_plate_read"}
    agent, ua, _clock, _operator = brief_and_bridge(tmp_path, url)
    assert "PLANTED9" in str(served.state()), "the plant is not on the wire"
    assert "PLANTED9" not in str(ua.played)
    assert "PLANTED9" not in str(agent.events(0).to_dict())
    assert "PLANTED9" not in str(agent.describe().to_dict())


def test_only_the_enabled_authorisations_are_offered(tmp_path, lane):
    """A site that has not enabled `transfer` never hears it in the menu."""
    _served, url = lane
    agent, ua, _clock, _operator = brief_and_bridge(
        tmp_path, url, authorisations=("open_now", "do_not_open")
    )
    menu = [arg for verb, arg in ua.commands if verb == "play" and "menu." in arg]
    assert any("menu.open_now" in one for one in menu)
    assert any("menu.do_not_open" in one for one in menu)
    assert not any("menu.transfer" in one for one in menu), menu
    assert not any("menu.hold" in one for one in menu), menu


def test_a_digit_for_a_disabled_authorisation_is_never_accepted(tmp_path, lane):
    """Keyed twice, re-prompted twice, and then it is `nothing usable`.

    Not mapped onto the nearest enabled thing, and not accepted quietly. `5` is
    `transfer` at every site; at a site that has not enabled it, keying it must
    record nothing.
    """
    _served, url = lane
    agent, ua, clock, operator = brief_and_bridge(
        tmp_path, url, authorisations=("open_now",)
    )
    for _ in range(REPROMPTS + 1):
        ua.dtmf(operator, "5")
        agent.poll()
        clock.advance(1)
    assert AgentEventKind.AUTHORISATION_RECEIVED.value not in kinds(agent)
    assert AgentEventKind.NOTHING_USABLE.value in kinds(agent)
    # The control: the ENABLED digit at the same site does record one.
    agent, ua, clock, operator = brief_and_bridge(
        tmp_path, url, authorisations=("open_now",)
    )
    ua.dtmf(operator, "1")
    agent.poll()
    assert AgentEventKind.AUTHORISATION_RECEIVED.value in kinds(agent)


def test_open_now_records_and_tells_the_person_it_cannot_open(tmp_path, lane):
    """`OPEN_NOW`: one event, two messages, and nothing else at all."""
    _served, url = lane
    agent, ua, clock, operator = brief_and_bridge(tmp_path, url)
    ua.dtmf(operator, "1")
    agent.poll()
    recorded = [
        event
        for event in agent.events(0).to_dict()["events"]
        if event["kind"] == AgentEventKind.AUTHORISATION_RECEIVED.value
    ]
    assert len(recorded) == 1
    assert recorded[0]["authorisation"] == Authorisation.OPEN_NOW.value
    assert recorded[0]["case"] == AgentCase.IDENTIFICATION_UNAVAILABLE.value
    assert recorded[0]["human"] == "sip:duty@10.0.0.5"
    assert recorded[0]["intercom"] == INTERCOM
    # The one fixed sentence the person who keyed it hears.
    assert any("operator.cannot_open" in arg for _verb, arg in ua.commands)
    assert any("authorisation.open_now" in arg for _verb, arg in ua.commands)
    # And the verbs the user agent was asked for: no verb that opens anything
    # exists on the seam, so the whole list is checked rather than one entry.
    assert {verb for verb, _ in ua.commands} <= {
        "start", "answer", "dial", "play", "bridge", "hangup", "hangup_all"
    }


def test_do_not_open_records_without_the_cannot_open_sentence(tmp_path, lane):
    """The control on the sentence above: it is played for the acts, not always."""
    _served, url = lane
    agent, ua, _clock, operator = brief_and_bridge(tmp_path, url)
    ua.dtmf(operator, "3")
    agent.poll()
    assert not any("operator.cannot_open" in arg for _verb, arg in ua.commands)
    assert any("authorisation.do_not_open" in arg for _verb, arg in ua.commands)


def test_hold_reprompts_the_driver_on_the_sites_interval(tmp_path, lane):
    """Silence on a door station is indistinguishable from a dead intercom."""
    _served, url = lane
    agent, ua, clock, operator = brief_and_bridge(
        tmp_path, url, hold_reprompt_seconds=45.0
    )
    ua.dtmf(operator, "4")
    agent.poll()
    pump(agent, clock, lambda: said(ua, "driver.hold_reprompt"), step=10.0)
    before = len([1 for _verb, arg in ua.commands if "driver.hold_reprompt" in arg])
    for _ in range(40):
        clock.advance(10.0)
        agent.poll()
    after = len([1 for _verb, arg in ua.commands if "driver.hold_reprompt" in arg])
    assert before >= 1 and after > before, (before, after)
    # And it opened nothing while holding.
    assert "vend" not in [verb for verb, _ in ua.commands]


def test_call_back_records_the_number_that_was_keyed(tmp_path, lane):
    _served, url = lane
    agent, ua, _clock, operator = brief_and_bridge(tmp_path, url)
    ua.dtmf(operator, "6")
    agent.poll()
    for digit in "5551234#":
        ua.dtmf(operator, digit)
    agent.poll()
    recorded = [
        event
        for event in agent.events(0).to_dict()["events"]
        if event["kind"] == AgentEventKind.AUTHORISATION_RECEIVED.value
    ]
    assert recorded and recorded[0]["keyed"] == "5551234"
    assert recorded[0]["authorisation"] == Authorisation.CALL_BACK.value


# ---------------------------------------------------------------------------
# The two timers, and neither opens anything
# ---------------------------------------------------------------------------


def test_the_human_not_answering_tells_the_driver_and_opens_nothing(tmp_path, lane):
    _served, url = lane
    agent, ua, clock = running(tmp_path, url, no_answer_seconds=30.0)
    ua.incoming(INTERCOM)
    pump(agent, clock, lambda: any(verb == "dial" for verb, _ in ua.commands))
    clock.advance(31)
    agent.poll()
    assert AgentEventKind.HUMAN_UNREACHABLE.value in kinds(agent)
    pump(agent, clock, lambda: said(ua, "driver.human_unreachable"))
    assert "vend" not in [verb for verb, _ in ua.commands]
    entry = [
        one for one in agent.health().to_dict()["codes"] if one["code"] == "human_unreachable"
    ]
    assert [one for one in entry if one["state"] == "active"], entry


def test_no_usable_digit_tells_the_driver_and_opens_nothing(tmp_path, lane):
    _served, url = lane
    agent, ua, clock, _operator = brief_and_bridge(
        tmp_path, url, clock=FakeClock(), nothing_usable_seconds=20.0
    )
    clock.advance(21)
    agent.poll()
    assert AgentEventKind.NOTHING_USABLE.value in kinds(agent)
    pump(agent, clock, lambda: said(ua, "driver.nothing_usable"))
    assert "vend" not in [verb for verb, _ in ua.commands]


def test_a_second_call_during_a_case_is_not_answered(tmp_path, lane):
    """One case at a time, and the second call is REFUSED WITHOUT BEING ANSWERED.

    That is what makes the intercom's own call list move on to the human's
    number, which is the degradation the install requirement exists for.
    Answering it to say "busy" would take that fall-through away.
    """
    _served, url = lane
    agent, ua, clock = running(tmp_path, url)
    ua.incoming(INTERCOM, call_id="call-1")
    agent.poll()
    answered = [arg for verb, arg in ua.commands if verb == "answer"]
    assert answered == ["call-1"]
    ua.incoming(INTERCOM, call_id="call-2")
    agent.poll()
    assert [arg for verb, arg in ua.commands if verb == "answer"] == answered
    assert ("hangup", "call-2") in ua.commands
    assert AgentEventKind.CALL_REFUSED_BUSY.value in kinds(agent)
