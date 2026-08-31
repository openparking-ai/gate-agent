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

from conftest import INTERCOM_ACCOUNT, FakeClock, agent_config_for, agent_for
from fake_ua import FakeUa
from foreign_lane import ForeignLane, decided_at
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
        # PRODUCED. The agent branches on the age of this moment, so a typed one
        # would drift to the stale side of the threshold and every case in this
        # file would become `stale_decision` on a date nobody chose.
        "at": decided_at(),
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
    ua.incoming(peer, account_user=INTERCOM_ACCOUNT)
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
    agent, ua, clock = running(tmp_path, url, driver_languages=("en", "es-ES"))
    ua.incoming(INTERCOM, account_user=INTERCOM_ACCOUNT)
    pump(agent, clock, lambda: len(files(ua, "driver")) >= 2)
    spoken = files(ua, "driver")
    assert spoken[:2] == [
        "en/case.identification_unavailable.wav",
        "es-ES/case.identification_unavailable.wav",
    ], spoken


def test_the_order_is_the_sites_and_not_this_packages(tmp_path, lane):
    """The control on the test above: reverse the declaration, reverse the audio.

    Without it, "English then Spanish" would pass on a build that always played
    English first, whatever a site declared.
    """
    _served, url = lane
    agent, ua, clock = running(tmp_path, url, driver_languages=("es-ES", "en"))
    ua.incoming(INTERCOM, account_user=INTERCOM_ACCOUNT)
    pump(agent, clock, lambda: len(files(ua, "driver")) >= 2)
    assert files(ua, "driver")[:2] == [
        "es-ES/case.identification_unavailable.wav",
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
    ua.incoming(INTERCOM, account_user=INTERCOM_ACCOUNT)
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
    ua.incoming(INTERCOM, account_user=INTERCOM_ACCOUNT)
    pump(agent, clock, lambda: agent.session is None)
    assert [verb for verb, _ in ua.commands if verb == "dial"] == []
    assert "hangup_all" in [verb for verb, _ in ua.commands]
    assert kinds(agent) == ["call_answered", "case_spoken", "call_ended"]


# ---------------------------------------------------------------------------
# The undeclared intercom
# ---------------------------------------------------------------------------


def test_a_call_at_an_undeclared_account_is_refused_without_being_answered(tmp_path, lane):
    """Not answered, nothing played, no lane read, and no lane guessed."""
    served, url = lane
    agent, ua, clock = running(tmp_path, url)
    ua.incoming(UNDECLARED, account_user="agent-nothing-this-site-declares")
    pump(agent, clock, lambda: agent.session is None)
    assert [verb for verb, _ in ua.commands if verb in ("answer", "play", "dial")] == []
    assert ("hangup", "call-1") in ua.commands
    assert files(ua, "driver") == []
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


def test_the_forged_from_reaches_nothing_and_the_declared_account_is_answered(tmp_path, lane):
    """THE WHOLE MECHANISM, both directions, in one test.

    A caller asserting the declared intercom's own address of record -- the
    forgery that used to be answered as that lane, ring a person at three in the
    morning and write a complete authorisation record -- arrives at an account
    nobody declared and is refused unanswered. The same `From` at the declared
    ACCOUNT is answered. The `From` is identical in both; only the number
    dialled differs, which is the claim this round makes.
    """
    _served, url = lane
    agent, ua, clock = running(tmp_path, url)
    ua.incoming(INTERCOM, call_id="forged-1", account_user="agent-guessed-wrong")
    pump(agent, clock, lambda: agent.session is None)
    assert kinds(agent) == [AgentEventKind.CALL_FROM_UNDECLARED_INTERCOM.value]
    assert [verb for verb, _ in ua.commands if verb == "answer"] == []
    forged = agent.events(since=0).to_dict()["events"][0]
    assert forged["caller_stated_identity"] == INTERCOM
    assert forged["intercom"] == agent.config.agent_id
    assert forged["lane"] is None

    ua.incoming(INTERCOM, call_id="real-1", account_user=INTERCOM_ACCOUNT)
    agent.poll()
    assert kinds(agent)[-1] == AgentEventKind.CALL_ANSWERED.value
    assert agent.session is not None


def test_an_invite_the_user_agent_refused_itself_is_recorded(tmp_path, lane):
    """No call ever existed: baresip answered `404 Not Found` at the door.

    It is the ordinary fate of a caller who does not know an intercom's dial
    address, and it is on the record because otherwise the commonest unwanted
    caller leaves no trace at all.
    """
    _served, url = lane
    agent, ua, _clock = running(tmp_path, url)
    ua.refused_unknown_account("sip:scanner@10.9.9.9")
    agent.poll()
    assert kinds(agent) == [AgentEventKind.CALL_FROM_UNDECLARED_INTERCOM.value]
    assert [verb for verb, _ in ua.commands if verb in ("answer", "hangup", "dial")] == []
    written = agent.events(since=0).to_dict()["events"][0]
    assert written["caller_stated_identity"] == "sip:scanner@10.9.9.9"


def test_the_claimed_identity_is_recorded_by_shape(tmp_path, lane):
    """A `From` is not stable to the character, and the record must not be either.

    A door station sends `"Door 1" <sip:door1@10.0.0.9:5060>;tag=…` on one call
    and the bare URI on the next. It decides nothing now -- the account does --
    but two spellings of one claim would read as two callers.
    """
    _served, url = lane
    agent, ua, _clock = running(tmp_path, url)
    ua.incoming('"Door 1" <sip:door1@10.0.0.9:5060>;tag=99',
                account_user=INTERCOM_ACCOUNT)
    agent.poll()
    assert kinds(agent)[0] == AgentEventKind.CALL_ANSWERED.value
    assert agent.events(since=0).to_dict()["events"][0][
        "caller_stated_identity"
    ] == INTERCOM


# ---------------------------------------------------------------------------
# The human, the briefing, and the bridge
# ---------------------------------------------------------------------------


def brief_and_bridge(tmp_path, url, clock=None, **kwargs):
    """Answer, speak the case, call the human, brief them, and bridge."""
    agent, ua, clock = running(tmp_path, url, clock=clock, **kwargs)
    ua.incoming(INTERCOM, account_user=INTERCOM_ACCOUNT)
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
    ua.incoming(INTERCOM, account_user=INTERCOM_ACCOUNT)
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

    A DECLARED second caller. The undeclared one -- which is every caller the
    site did not name, and therefore the default -- is the separate test below.
    """
    _served, url = lane
    agent, ua, clock = running(tmp_path, url)
    ua.incoming(INTERCOM, call_id="call-1", account_user=INTERCOM_ACCOUNT)
    agent.poll()
    answered = [arg for verb, arg in ua.commands if verb == "answer"]
    assert answered == ["call-1"]
    ua.incoming(INTERCOM, call_id="call-2", account_user=INTERCOM_ACCOUNT)
    agent.poll()
    assert [arg for verb, arg in ua.commands if verb == "answer"] == answered
    assert ("hangup", "call-2") in ua.commands
    assert AgentEventKind.CALL_REFUSED_BUSY.value in kinds(agent)


# ---------------------------------------------------------------------------
# The language, per call
# ---------------------------------------------------------------------------


def test_a_mid_call_switch_changes_the_next_sentence_and_every_one_after(tmp_path, lane):
    """Gokhan: *"if the customer starts speaking in Spanish, no English, it
    should start Spanish from there."*

    What NOTICES they did is a later step -- hearing a language is ASR. What is
    here is the state that step will set, and it is per call: after the switch,
    every sentence is in that language and only that language.
    """
    _served, url = lane
    agent, ua, clock = running(tmp_path, url, driver_languages=("en", "es-ES"))
    ua.incoming(INTERCOM, account_user=INTERCOM_ACCOUNT)
    pump(agent, clock, lambda: len(files(ua, "driver")) >= 2)
    assert {name.split("/")[0] for name in files(ua, "driver")} == {"en", "es-ES"}

    agent.set_language("call-1", "es-ES")
    before = len(files(ua, "driver"))
    pump(agent, clock, lambda: any(verb == "dial" for verb, _ in ua.commands))
    operator = [arg for verb, arg in ua.commands if verb == "dial"][0].split("-> ")[1]
    ua.established(operator)
    pump(agent, clock, lambda: ua.bridged_at is not None)
    ua.dtmf(operator, "1")
    agent.poll()
    pump(agent, clock, lambda: said(ua, "authorisation.open_now"))
    after = files(ua, "driver")[before:]
    assert after, "nothing more was said, so this asserts nothing"
    assert {name.split("/")[0] for name in after} == {"es-ES"}, after


def test_a_language_this_site_did_not_declare_is_refused(tmp_path, lane):
    """The control on the switch, and the reason it is a function and not a field.

    A switch that silently did nothing would leave a driver being spoken to in a
    language they have just said they do not have -- and this package would have
    no words for the one they asked for either.
    """
    _served, url = lane
    agent, ua, clock = running(tmp_path, url, driver_languages=("en",))
    ua.incoming(INTERCOM, account_user=INTERCOM_ACCOUNT)
    agent.poll()
    with pytest.raises(ValueError) as raised:
        agent.set_language("call-1", "es-ES")
    assert "not a language this site declared" in str(raised.value)
    with pytest.raises(ValueError):
        agent.set_language("call-1", "kl")
    # The control: the declared one IS accepted, so the refusal is about the
    # language and not about the function refusing everything.
    agent.set_language("call-1", "en")
    with pytest.raises(ValueError):
        agent.set_language("no-such-call", "en")


def test_without_a_switch_every_declared_language_keeps_playing(tmp_path, lane):
    """The other control: the switch is what narrows it, not the passage of time."""
    _served, url = lane
    agent, ua, clock = running(tmp_path, url, driver_languages=("en", "es-ES"))
    ua.incoming(INTERCOM, account_user=INTERCOM_ACCOUNT)
    pump(agent, clock, lambda: any(verb == "dial" for verb, _ in ua.commands))
    operator = [arg for verb, arg in ua.commands if verb == "dial"][0].split("-> ")[1]
    ua.established(operator)
    pump(agent, clock, lambda: ua.bridged_at is not None)
    ua.dtmf(operator, "3")
    agent.poll()
    pump(
        agent,
        clock,
        lambda: len([1 for name in files(ua, "driver") if "do_not_open" in name]) >= 2,
    )
    spoken = [name for name in files(ua, "driver") if "authorisation.do_not_open" in name]
    assert {name.split("/")[0] for name in spoken} == {"en", "es-ES"}, spoken


def test_a_driver_who_hangs_up_takes_the_operators_leg_with_them(tmp_path, lane):
    """The leg left live would be bridged into the NEXT case.

    This user agent's bridge is site-wide, so a person still holding for a
    driver who has gone would be conferenced into a stranger's call the moment
    the next one is answered. It is the same reason `concurrent_cases` is 1,
    seen from the other end.
    """
    _served, url = lane
    agent, ua, clock, operator = brief_and_bridge(tmp_path, url)
    ua.closed("call-1")
    agent.poll()
    assert ("hangup", operator) in ua.commands, ua.commands
    assert agent.session is None
    assert kinds(agent)[-1] == AgentEventKind.CALL_ENDED.value

    # THE CONTROL: the next call is answered on a user agent with nothing left
    # over -- and it is answered, so the session really did end.
    ua.incoming(INTERCOM, call_id="call-9", account_user=INTERCOM_ACCOUNT)
    agent.poll()
    assert ("answer", "call-9") in ua.commands


# ---------------------------------------------------------------------------
# The round-5 cut. One section per blocker.
# ---------------------------------------------------------------------------


def test_an_undeclared_caller_during_a_case_is_refused_unanswered(tmp_path, lane):
    """X1. The LIVE CASE is checked before the identity, so the limit holds.

    Being undeclared is the DEFAULT state of every caller on a network, not a
    rare one. With the identity checked first, `concurrent_cases: 1` applied
    only to callers the site had declared: a stranger dialling mid-case was
    ANSWERED, given a session, and conferenced into a live bridge, and the one
    fixed sentence it was told ended in `hangup_all`, which cut off the real
    driver and the real operator mid-case.

    Measured here on the record: what the agent asked its user agent to do, and
    what survived. The waveform is in `test_agent_sip.py`.
    """
    _served, url = lane
    agent, ua, clock, operator = brief_and_bridge(tmp_path, url)
    assert agent.session.bridged
    before = list(ua.commands)

    ua.incoming("sip:stranger@10.9.9.9", call_id="stranger-1",
                account_user="agent-nothing-this-site-declares")
    agent.poll()

    after = [entry for entry in ua.commands if entry not in before or ua.commands.count(entry) > 1]
    # NOT answered. NOT given a session. NOTHING played to it. NO conference.
    assert ("answer", "stranger-1") not in ua.commands
    assert ("hangup", "stranger-1") in ua.commands
    assert agent.session is not None, "the stranger took the real case's session"
    assert agent.session.driver_call == "call-1"
    assert agent.session.operator_call == operator
    assert ("hangup_all", "") not in ua.commands, "the stranger tore the real case down"
    assert not any(
        verb == "play" and "undeclared_intercom" in arg for verb, arg in after
    ), "the stranger was played a sentence, which means it was answered"

    # The refusal carries the identity it claimed, so a site can see who called.
    refusals = [
        event for event in agent.events(0).to_dict()["events"]
        if event["kind"] == AgentEventKind.CALL_REFUSED_BUSY.value
    ]
    assert len(refusals) == 1
    assert refusals[0]["caller_stated_identity"] == "sip:stranger@10.9.9.9"
    # And no code went active for it: it is not an undeclared-intercom fault,
    # it is a busy agent.
    assert not any(
        event["kind"] == AgentEventKind.CALL_FROM_UNDECLARED_INTERCOM.value
        for event in agent.events(0).to_dict()["events"]
    )

    # THE REAL CASE SURVIVES TO ITS AUTHORISATION.
    ua.dtmf(operator, "1")
    agent.poll()
    recorded = [
        event for event in agent.events(0).to_dict()["events"]
        if event["kind"] == AgentEventKind.AUTHORISATION_RECEIVED.value
    ]
    assert len(recorded) == 1
    assert recorded[0]["intercom"] == INTERCOM
    assert recorded[0]["authorisation"] == Authorisation.OPEN_NOW.value


def test_the_two_refusals_are_told_apart(tmp_path, lane):
    """X1's other side, and the control on the test above.

    The busy refusal and the undeclared refusal are both "not answered", so an
    agent that simply refused everybody would satisfy the test above. They must
    stay two facts: with NO case in progress, the same stranger earns
    `call_from_undeclared_intercom` and a code, not `call_refused_busy`; and the
    declared account is answered, which is what makes "refused" mean anything.
    """
    _served, url = lane
    agent, ua, clock = running(tmp_path, url)
    ua.incoming("sip:stranger@10.9.9.9", call_id="stranger-1",
                account_user="agent-nothing-this-site-declares")
    pump(agent, clock, lambda: agent.session is None)
    assert ("answer", "stranger-1") not in ua.commands
    assert kinds(agent) == [AgentEventKind.CALL_FROM_UNDECLARED_INTERCOM.value]
    assert [
        entry["state"] for entry in agent.health().to_dict()["codes"]
        if entry["code"] == "call_from_undeclared_intercom"
        and entry["subject"] == agent.config.agent_id
    ] == ["active"]

    # THE CONTROL: the declared account, same agent, is answered.
    ua.incoming(INTERCOM, call_id="real-1", account_user=INTERCOM_ACCOUNT)
    agent.poll()
    assert ("answer", "real-1") in ua.commands
    # AND THE CODE RECOVERS. A code that could only ever go one way is a latch
    # that reads like a state -- and this one is keyed on the agent now, so
    # without recovery one scan would leave a site red for the life of the
    # process. `human_unreachable` carries this same assertion for this reason.
    assert [
        entry["state"] for entry in agent.health().to_dict()["codes"]
        if entry["code"] == "call_from_undeclared_intercom"
        and entry["subject"] == agent.config.agent_id
    ] == ["ok"], "the undeclared code is a latch"


def test_a_line_the_user_agent_will_not_play_is_a_code_a_timer_and_a_person(tmp_path, lane):
    """X4. A user agent that ANSWERS and refuses every file.

    What a real one does for a file it cannot decode, an audio mode it will not
    play into, or a mixer stuck in a mode it cannot leave. Retried for ever that
    is a driver in an answered call hearing nothing, with no timer, no code, and
    an event log saying the case was spoken -- measured at thirty-three hours on
    this build.
    """
    _served, url = lane
    agent, ua, clock = running(tmp_path, url)
    ua.refuse_play = True
    ua.incoming(INTERCOM, account_user=INTERCOM_ACCOUNT)
    agent.poll()
    assert agent.session is not None

    # Before the deadline: nothing has gone active, and nothing has been said.
    clock.advance(agent.config.line_timeout_seconds - 1)
    agent.poll()
    assert not ua.played
    assert _active(agent, "audio_playback_failed") == []

    # Past it: the code names the LEG, and the case goes to a person.
    clock.advance(3)
    agent.poll()
    assert _active(agent, "audio_playback_failed") == ["driver"]
    assert AgentEventKind.HUMAN_CALLED.value in kinds(agent)
    # And `case_spoken` is NOT in the log, because it was not spoken.
    assert AgentEventKind.CASE_SPOKEN.value not in kinds(agent)


def test_a_case_that_cannot_be_spoken_to_anybody_ends_and_releases_the_call(tmp_path, lane):
    """X4. The person's leg cannot be spoken to either, so the case ENDS.

    A call held open in silence is a call the driver cannot get out of and the
    intercom cannot move past.
    """
    _served, url = lane
    agent, ua, clock = running(tmp_path, url)
    ua.refuse_play = True
    ua.incoming(INTERCOM, account_user=INTERCOM_ACCOUNT)
    agent.poll()
    pump(agent, clock, lambda: any(verb == "dial" for verb, _ in ua.commands), step=4.0)
    operator = [arg for verb, arg in ua.commands if verb == "dial"][0].split("-> ")[1]
    ua.established(operator)
    pump(agent, clock, lambda: agent.session is None, step=4.0)

    assert _active(agent, "audio_playback_failed") == ["driver", "operator"]
    assert AgentEventKind.CASE_NOT_SPOKEN.value in kinds(agent)
    assert ("hangup_all", "") in ua.commands, "the driver's call was left open in silence"
    assert AgentEventKind.CASE_SPOKEN.value not in kinds(agent)
    # AND IT WAS NEVER BRIDGED. `_advance` used to go on using a session
    # `_speak` had just ended: from `BRIEFING`, with the queue cleared by the
    # failure, the next branch sent `conference` on a call already hung up.
    assert ua.bridged_at is None, "a case that was torn down was bridged anyway"
    assert not agent.session, "the ended session is still the agent's"


def test_case_spoken_is_written_when_the_last_file_has_finished(tmp_path, lane):
    """X4. NOT when it is queued. That was a claim about a queue.

    Written at queue time it stayed true in the log through thirty-three hours
    of a user agent refusing every line of the case.
    """
    _served, url = lane
    agent, ua, clock = running(tmp_path, url, driver_languages=("en", "es-ES"))
    ua.incoming(INTERCOM, account_user=INTERCOM_ACCOUNT)
    agent.poll()   # answers; the media event arrives on the next poll
    agent.poll()   # the first file starts
    # The first file is playing. Both are queued. NOTHING has finished.
    assert ua.played, "nothing was played at all, so this measures the wrong thing"
    assert AgentEventKind.CASE_SPOKEN.value not in kinds(agent)
    # The second language has not even started.
    assert len(files(ua, "driver")) == 1
    pump(agent, clock, lambda: AgentEventKind.CASE_SPOKEN.value in kinds(agent))
    # By the time it IS written, every file of the case has been played.
    assert len(files(ua, "driver")) == 2
    assert kinds(agent).index(AgentEventKind.CASE_SPOKEN.value) < kinds(agent).index(
        AgentEventKind.HUMAN_CALLED.value
    )


def test_nothing_to_do_also_writes_case_spoken_when_it_has_finished(tmp_path, lane):
    """X4's other path: the one case that reaches nobody still has a record."""
    served, url = lane
    served.decision = {
        "outcome": "allow", "reason": "allow", "fallback": None, "cause": None,
        "presence": None, "at": decided_at(), "read_ref": None,
    }
    served.transit = {"state": "confirmed", "since": None}
    agent, ua, clock = running(tmp_path, url)
    ua.incoming(INTERCOM, account_user=INTERCOM_ACCOUNT)
    agent.poll()
    assert AgentEventKind.CASE_SPOKEN.value not in kinds(agent)
    pump(agent, clock, lambda: agent.session is None)
    assert AgentEventKind.CASE_SPOKEN.value in kinds(agent)
    assert AgentEventKind.HUMAN_CALLED.value not in kinds(agent)


def test_the_operator_hanging_up_mid_menu_says_what_happened(tmp_path, lane):
    """The driver used to be told "I could not take an instruction" for this.

    That is not what happened, and a driver told it goes on standing there. Two
    different facts, two lines. The control is the OTHER path -- a timeout with
    no digit -- which still says the original sentence.
    """
    _served, url = lane
    agent, ua, clock, operator = brief_and_bridge(tmp_path, url)
    ua.closed(operator)
    agent.poll()
    pump(agent, clock, lambda: said(ua, "driver.operator_hung_up"))
    assert not said(ua, "driver.nothing_usable")
    assert AgentEventKind.NOTHING_USABLE.value in kinds(agent)

    # THE CONTROL: nobody keys anything, and the original sentence is what plays.
    agent2, ua2, clock2, _operator2 = brief_and_bridge(tmp_path, url)
    clock2.advance(agent2.config.nothing_usable_seconds + 1)
    pump(agent2, clock2, lambda: said(ua2, "driver.nothing_usable"))
    assert not said(ua2, "driver.operator_hung_up")


def _active(agent, code: str) -> list[str]:
    return sorted(
        entry["subject"]
        for entry in agent.health().to_dict()["codes"]
        if entry["code"] == code and entry["state"] == "active"
    )
