"""The SIP path, against the real user agent, on real sockets. The measurement.

Everything else in this suite asks whether the agent's LOGIC is right. This asks
whether the thing it drives does what the seam says it does, and there is no way
to ask that with a fake: SIP, RTP and RFC 4733 are the parts nobody writing a
gate agent has seen fail, and a fixture shaped like a SIP stack would make every
answer a claim about the fixture.

**What is measured here**

  * the user agent REGISTERS with a registrar, and the agent's health follows it
    -- including a registration being LOST, which is the control;
  * a call from a declared intercom is ANSWERED, and the case is spoken;
  * the person is called as a SECOND call, and the agent stays in both;
  * before the bridge the two legs are PRIVATE, and after it they are not --
    measured as a waveform, on the recording the person's user agent wrote;
  * a DTMF digit arrives tagged with the LEG it came in on, and an authorisation
    is recorded from it;
  * and after all of it, nothing opened anything.

**What is NOT measured here, and cannot be:** any of this against a real
intercom. Call setup time, audio quality, echo and DTMF detection through a door
station's microphone on a garage's network are stated as NOT MEASURED in
`docs/CONTRACT.md` and nothing in this file changes that.
"""

from __future__ import annotations

import json
import socket
import time

import pytest

import real_sip
from conftest import agent_config_for, wav
from gate_agent.agent import Agent
from gate_agent.config import AgentConfig, Intercom, UserAgentSettings
from gate_agent.contract import AgentEventKind, Authorisation, HealthState
from gate_agent.ua_baresip import TESTED_VERSIONS, BaresipUa
from real_sip import Instance, Registrar

pytestmark = pytest.mark.sip


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    """A registrar and three baresips: ours, the intercom, and the person."""
    if real_sip.baresip() is None or real_sip.module_path() is None:
        if real_sip.REQUIRE:
            raise AssertionError(
                "REQUIRE_SIP=1 and baresip is not installed. These are the only "
                "measurements in this suite that touch SIP; skipping them silently is how "
                "a guarantee stops being one."
            )
        pytest.skip("baresip is not installed")
    root = tmp_path_factory.mktemp("sip")
    registrar = Registrar().start()
    ours = Instance(
        root,
        "agent",
        accounts=[
            # The AOR's host is the REGISTRAR, which is where baresip sends the
            # REGISTER. Two accounts, one per leg, because the user agent
            # identifies the stream to play into by the local account.
            f"<sip:agent@127.0.0.1:{registrar.port}>;regint=60;audio_codecs=pcmu"
            ";answermode=manual",
            f"<sip:agent-op@127.0.0.1:{registrar.port}>;regint=0;audio_codecs=pcmu"
            ";answermode=manual",
        ],
    )
    intercom = Instance(
        root,
        "intercom",
        accounts=["<sip:door1@127.0.0.1>;regint=0;audio_codecs=pcmu;answermode=manual"],
        # The intercom SENDS A TONE. What the person hears, and when, is the
        # whole measurement of the bridge.
        audio_source=f"aufile,{real_sip.tone(root / 'tone.wav')}",
    )
    person = Instance(
        root,
        "person",
        accounts=["<sip:duty@127.0.0.1>;regint=0;audio_codecs=pcmu;answermode=manual"],
    )
    # THE STRANGER. A fourth user agent, its own process on its own port, whose
    # SIP identity this site does not declare -- which is the DEFAULT state of
    # every caller on a network. It RECORDS WHAT IT HEARS, because the harm the
    # round-5 cut is about is what an undeclared caller was conferenced into,
    # and that is a waveform rather than a command log.
    stranger = Instance(
        root,
        "stranger",
        accounts=["<sip:nobody@127.0.0.1>;regint=0;audio_codecs=pcmu;answermode=manual"],
    )
    for one in (ours, intercom, person, stranger):
        one.start()
    try:
        yield root, registrar, ours, intercom, person, stranger
    finally:
        for one in (stranger, person, intercom, ours):
            one.stop()
        registrar.stop()


class Ctrl:
    """A `ctrl_tcp` client for the two baresips the AGENT does not drive."""

    def __init__(self, port: int) -> None:
        self.sock = socket.create_connection(("127.0.0.1", port), timeout=5)
        self.sock.settimeout(5)
        self.buffer = b""

    def command(self, command: str, params: str = "") -> dict:
        payload = json.dumps(
            {"command": command, "params": params, "token": "t"}
        ).encode("utf-8")
        self.sock.sendall(b"%d:%s," % (len(payload), payload))
        deadline = time.time() + 5
        while time.time() < deadline:
            for message in self._messages():
                if not message.get("event"):
                    return message
            self.buffer += self.sock.recv(65536)
        raise AssertionError(f"no answer to {command}")

    def _messages(self):
        out = []
        while b":" in self.buffer:
            head, _, rest = self.buffer.partition(b":")
            try:
                length = int(head)
            except ValueError:
                self.buffer = rest
                continue
            if len(rest) < length + 1:
                break
            body, self.buffer = rest[: length], rest[length + 1 :]
            try:
                out.append(json.loads(body))
            except ValueError:
                pass
        return out

    def close(self) -> None:
        self.sock.close()


def agent_on(world, tmp_path, **kwargs):
    """The real `Agent`, driving the real baresip over its control socket."""
    root, registrar, ours, _intercom, _person, _stranger = world
    ua = BaresipUa(
        host="127.0.0.1",
        port=ours.ctrl_port,
        driver_aor=f"sip:agent@127.0.0.1:{registrar.port}",
        operator_aor=f"sip:agent-op@127.0.0.1:{registrar.port}",
    )
    base = agent_config_for(tmp_path, standalone=True, **kwargs)
    config = AgentConfig(
        agent_id=base.agent_id,
        site_id=base.site_id,
        intercoms=(
            Intercom(
                sip_uri="sip:door1@127.0.0.1",
                lane=None,
                name_audio=wav(tmp_path / "site" / "door1.wav", seconds=0.4),
            ),
        ),
        lanes=(),
        user_agent=UserAgentSettings(
            kind="baresip",
            host="127.0.0.1",
            port=ours.ctrl_port,
            driver_aor=f"sip:agent@127.0.0.1:{registrar.port}",
            operator_aor=f"sip:agent-op@127.0.0.1:{registrar.port}",
        ),
        driver_languages=("en",),
        operator_language="en",
        authorisations=base.authorisations,
        human_sip_uri=f"sip:duty@127.0.0.1:{_person.sip_port}",
        audio_directory=base.audio_directory,
        no_answer_seconds=20.0,
        nothing_usable_seconds=30.0,
    )
    agent = Agent(config, ua)
    agent.start()
    return agent, ua


def pump(agent, until, seconds: float = 45.0) -> None:
    deadline = time.time() + seconds
    while time.time() < deadline:
        agent.poll()
        if until():
            return
        time.sleep(0.2)
    raise AssertionError("the real dialogue never reached the state this test waits for")


def settle(agent, seconds: float) -> None:
    """Keep the dialogue running for a while without waiting for anything.

    Used where what is being measured is a RECORDING that takes real time to
    accumulate: the person's user agent writes what it hears as it hears it.
    """
    deadline = time.time() + seconds
    while time.time() < deadline:
        agent.poll()
        time.sleep(0.2)


def test_the_user_agent_is_the_version_this_build_was_tested_against(world, tmp_path):
    agent, ua = agent_on(world, tmp_path)
    try:
        assert ua.version() in TESTED_VERSIONS
        assert agent.describe().to_dict()["user_agent"]["version"] == ua.version()
    finally:
        ua.close()


def test_it_registers_and_the_health_surface_follows_it(world, tmp_path):
    """`sip_registration_lost`, measured where it can be measured.

    The lane contract has a code of the same meaning and cannot answer it: a
    lane cannot see whether an agent is registered. The control is a registrar
    that starts REFUSING, and the code going `active` from it.
    """
    _root, registrar, ours, _intercom, _person, _stranger = world
    agent, ua = agent_on(world, tmp_path)
    try:
        pump(agent, lambda: ua.registered() is True, seconds=30)
        entry = [
            one
            for one in agent.health().to_dict()["codes"]
            if one["code"] == "sip_registration_lost"
        ][0]
        assert entry["state"] == HealthState.OK.value
        assert registrar.registrations, "nothing ever registered, so this asserts nothing"

        # THE CONTROL: the registrar starts saying no, and the agent says so.
        #
        # Driven through the agent's OWN connection, deliberately. baresip's
        # control socket accepts exactly one client, so a second connection
        # opened to force a re-registration takes the agent's away -- which is a
        # fact about this user agent worth knowing at an installation, and it is
        # in `docs/CONTRACT.md` beside the other three.
        registrar.refuse = True
        ua._command("uareg", "1 0")
        pump(agent, lambda: ua.registered() is False, seconds=30)
        entry = [
            one
            for one in agent.health().to_dict()["codes"]
            if one["code"] == "sip_registration_lost"
        ][0]
        assert entry["state"] == HealthState.ACTIVE.value

        # AND IT RECOVERS. This half was missing: the test asserted the raise
        # and stopped, so a code that could only ever go one way -- a latch that
        # reads like a state -- would have passed it. `human_unreachable` has
        # exactly this assertion for exactly this reason.
        registrar.refuse = False
        ua._command("uareg", "1 0")
        pump(agent, lambda: ua.registered() is True, seconds=30)
        entry = [
            one
            for one in agent.health().to_dict()["codes"]
            if one["code"] == "sip_registration_lost"
        ][0]
        assert entry["state"] == HealthState.OK.value, (
            "the registrar answers 200 again and the code stayed active: it is a latch"
        )
    finally:
        registrar.refuse = False
        ua.close()


def test_a_whole_case_over_real_sip(world, tmp_path):
    """The one that matters: a call, a case, a person, a bridge, and a digit.

    Every step is asserted against something outside the agent -- the intercom's
    own view that it is in a call, the recording the person's user agent wrote,
    and the agent's event log -- rather than against the agent's description of
    itself.
    """
    _root, _registrar, ours, intercom, person, _stranger = world
    agent, ua = agent_on(world, tmp_path)
    caller = Ctrl(intercom.ctrl_port)
    callee = Ctrl(person.ctrl_port)
    try:
        answer = caller.command("dial", f"sip:agent@127.0.0.1:{ours.sip_port}")
        assert answer["ok"], answer
        pump(agent, lambda: agent.session is not None, seconds=20)
        assert agent.session.intercom.sip_uri == "sip:door1@127.0.0.1"

        # The person's phone rings, and they answer it.
        def person_ringing() -> str | None:
            listed = callee.command("listcalls")["data"]
            for line in listed.splitlines():
                if "id " in line:
                    return line.split("id ")[1].split("]")[0]
            return None

        pump(agent, lambda: person_ringing() is not None, seconds=40)
        callee.command("accept", person_ringing())
        pump(agent, lambda: agent.session.bridged, seconds=60)

        # A DTMF digit, from the PERSON's leg, over RFC 4733.
        settle(agent, 3.0)
        callee.command("sndcode", "1")
        pump(
            agent,
            lambda: any(
                event["kind"] == AgentEventKind.AUTHORISATION_RECEIVED.value
                for event in agent.events(0).to_dict()["events"]
            ),
            seconds=20,
        )
        recorded = [
            event
            for event in agent.events(0).to_dict()["events"]
            if event["kind"] == AgentEventKind.AUTHORISATION_RECEIVED.value
        ]
        assert recorded[0]["authorisation"] == Authorisation.OPEN_NOW.value
        assert recorded[0]["intercom"] == "sip:door1@127.0.0.1"
        # And no plate anywhere near any of it.
        assert "plate" not in json.dumps(agent.events(0).to_dict()).lower()

        # THE MEASUREMENT. Read after the calls end, because the user agent
        # writing the recording finalises the file when it closes it.
        settle(agent, 4.0)
        ua.hangup_all()
        time.sleep(1.0)
        share = real_sip.tone_share(person.recording)
        assert share, f"the person's user agent recorded nothing:\n{person.tail()}"
        # Before the bridge the person is being briefed BY THE AGENT: their
        # recording is not silent, and it carries none of the driver's tone.
        # A real sinusoid puts half its energy in the positive-frequency bin, so
        # 0.5 is the ceiling and not 1.0. Measured across the bridge, the two
        # sides of it are three orders of magnitude apart.
        assert share[0] < 0.05, f"the person heard the driver before the bridge: {share[:6]}"
        # After it, the driver's tone is what they are hearing.
        assert max(share) > 0.4, f"the person never heard the driver at all: {share}"
        assert share[-1] > 0.4, f"the bridge did not hold: {share[-6:]}"
    finally:
        try:
            ua.hangup_all()
        finally:
            caller.close()
            callee.close()
            ua.close()


def test_a_call_from_an_undeclared_intercom_is_answered_once_and_ended(world, tmp_path):
    """Over real SIP: one message, and the call is gone. No lane, no guess."""
    _root, _registrar, ours, _intercom, person, _stranger = world
    agent, ua = agent_on(world, tmp_path)
    # The PERSON's user agent stands in for a stranger here: it is a SIP
    # identity this site does not declare, which is exactly the case.
    stranger = Ctrl(person.ctrl_port)
    try:
        stranger.command("dial", f"sip:agent@127.0.0.1:{ours.sip_port}")
        pump(
            agent,
            lambda: any(
                event["kind"] == AgentEventKind.CALL_FROM_UNDECLARED_INTERCOM.value
                for event in agent.events(0).to_dict()["events"]
            ),
            seconds=20,
        )
        pump(agent, lambda: agent.session is None, seconds=40)
        assert not any(
            event["kind"] == AgentEventKind.HUMAN_CALLED.value
            for event in agent.events(0).to_dict()["events"]
        )
    finally:
        ua.hangup_all()
        stranger.close()
        ua.close()


def test_a_stranger_calling_mid_case_never_hears_the_driver(world, tmp_path):
    """X1, MEASURED AS AUDIO. The blocker was a waveform, so the cut is too.

    A whole case is run to the bridge; then a FOURTH baresip, whose identity
    this site does not declare, dials the agent. With the identity checked
    before the live case, that call was ANSWERED, given a session, and
    conferenced into the live bridge -- the stranger heard the person at the
    barrier at the ceiling of this measure, from the first bucket, identically
    to the operator -- and the one fixed sentence it was told ended in
    `hangup_all`, which cut the real driver and the real operator off mid-case.

    Three things are asserted, and each is outside the agent: the stranger's own
    recording, the real legs the intercom and the person still hold, and the
    authorisation the real case reaches afterwards.
    """
    _root, _registrar, ours, intercom, person, stranger = world
    agent, ua = agent_on(world, tmp_path)
    caller = Ctrl(intercom.ctrl_port)
    callee = Ctrl(person.ctrl_port)
    intruder = Ctrl(stranger.ctrl_port)
    try:
        caller.command("dial", f"sip:agent@127.0.0.1:{ours.sip_port}")
        pump(agent, lambda: agent.session is not None, seconds=20)

        def ringing(ctrl) -> str | None:
            for line in ctrl.command("listcalls")["data"].splitlines():
                if "id " in line:
                    return line.split("id ")[1].split("]")[0]
            return None

        pump(agent, lambda: ringing(callee) is not None, seconds=40)
        callee.command("accept", ringing(callee))
        pump(agent, lambda: agent.session.bridged, seconds=60)
        settle(agent, 2.0)

        # THE STRANGER DIALS, mid-case.
        intruder.command("dial", f"sip:agent@127.0.0.1:{ours.sip_port}")
        pump(
            agent,
            lambda: any(
                event["kind"] == AgentEventKind.CALL_REFUSED_BUSY.value
                for event in agent.events(0).to_dict()["events"]
            ),
            seconds=20,
        )
        settle(agent, 4.0)

        # The real case is UNTOUCHED: both legs still up, at both far ends.
        assert agent.session is not None, "the stranger took the real case's session"
        assert agent.session.bridged
        assert ringing(caller) is not None, "the real driver's call was cut off"
        assert ringing(callee) is not None, "the real operator's call was cut off"

        # The refusal names who called, and no `hangup_all` went out.
        refused = [
            event for event in agent.events(0).to_dict()["events"]
            if event["kind"] == AgentEventKind.CALL_REFUSED_BUSY.value
        ]
        assert len(refused) == 1
        assert refused[0]["intercom"] == "sip:nobody@127.0.0.1"

        # AND THE REAL CASE STILL REACHES ITS AUTHORISATION.
        callee.command("sndcode", "1")
        pump(
            agent,
            lambda: any(
                event["kind"] == AgentEventKind.AUTHORISATION_RECEIVED.value
                for event in agent.events(0).to_dict()["events"]
            ),
            seconds=20,
        )
        settle(agent, 3.0)
    finally:
        try:
            ua.hangup_all()
        finally:
            caller.close()
            callee.close()
            intruder.close()
            ua.close()
    time.sleep(1.0)

    # THE MEASUREMENT. The intercom sends a 440 Hz tone; this is the share of
    # the STRANGER's recording at exactly that frequency, half-second by
    # half-second. Speech spreads its energy across the band and a pure tone
    # does not, so this separates "heard the driver" from "heard the agent".
    share = real_sip.tone_share(stranger.recording)
    assert max(share, default=0.0) < 0.05, (
        f"the stranger heard the driver at the barrier: {share}"
    )
    # THE POSITIVE CONTROL, and it is what makes the line above mean anything:
    # the OPERATOR's recording, taken in the same run with the same measure,
    # carries the driver's tone. Without it "below 0.05" would be satisfied by a
    # microphone that recorded nothing.
    operator_share = real_sip.tone_share(person.recording)
    assert operator_share, f"the person's user agent recorded nothing:\n{person.tail()}"
    assert max(operator_share) > 0.4, (
        "the operator never heard the driver either, so the stranger's silence measures "
        f"nothing: {operator_share}"
    )


def test_the_agent_refuses_to_start_against_an_aubridge_baresip(world, tmp_path):
    """X5. Against a REAL baresip set up the way the contract forbids.

    The source said these were "checked at startup" and they were not: `grep -rn
    aubridge` over the whole repository returned four hits and not one was code.
    A real baresip with `aubridge` on both devices STARTED, answered calls, and
    refused every playback for ever.
    """
    from gate_agent.ua import UaMisconfigured

    root = tmp_path / "aubridge-world"
    misconfigured = Instance(
        root,
        "aubridge",
        accounts=["<sip:agent@127.0.0.1>;regint=0;audio_codecs=pcmu;answermode=manual"],
        audio_source="aubridge,pseudo0",
        audio_player="aubridge,pseudo0",
    )
    misconfigured.start()
    try:
        ua = BaresipUa(
            host="127.0.0.1",
            port=misconfigured.ctrl_port,
            driver_aor="sip:agent@127.0.0.1",
            operator_aor="sip:agent-op@127.0.0.1",
        )
        try:
            with pytest.raises(UaMisconfigured) as raised:
                ua.start()
            assert "aubridge" in str(raised.value)
            assert "audio_source" in str(raised.value) or "audio_player" in str(raised.value)
        finally:
            ua.close()
    finally:
        misconfigured.stop()

    # THE CONTROL: the same real binary, the same code, set up the way the
    # contract says -- and it starts. Without this the refusal above could be an
    # agent that refuses every baresip there is.
    _root, _registrar, _ours, _intercom, _person, _stranger = world
    agent, ua = agent_on(world, tmp_path)
    try:
        assert ua.version() in TESTED_VERSIONS
    finally:
        ua.close()


def test_the_control_socket_comes_back_and_the_next_call_is_answered(world, tmp_path):
    """X7, against a real baresip and a real second client on the port.

    `ctrl_tcp` accepts exactly ONE client. Something else on the box opening it
    -- a console, a script, a monitoring tool, the case the contract already
    names -- took the agent's away, and the agent then reported `ua_unreachable`
    while baresip was running perfectly and NEVER CAME BACK. The same thing
    happened on any ordinary restart of that process, and the only repair was a
    human restarting the agent.
    """
    _root, _registrar, ours, intercom, _person, _stranger = world
    agent, ua = agent_on(world, tmp_path)
    caller = Ctrl(intercom.ctrl_port)
    try:
        pump(agent, lambda: ua.registered() is True, seconds=30)

        def code(name: str) -> str:
            return [
                one for one in agent.health().to_dict()["codes"] if one["code"] == name
            ][0]["state"]

        assert code("ua_unreachable") == HealthState.OK.value

        # THE INTRUDER takes the socket.
        intruder = socket.create_connection(("127.0.0.1", ours.ctrl_port), timeout=5)
        try:
            pump(agent, lambda: code("ua_unreachable") == HealthState.ACTIVE.value, seconds=30)
            assert ours.process.poll() is None, "baresip died; this measures the wrong thing"
        finally:
            intruder.close()

        # AND IT COMES BACK, inside the site's `reconnect_seconds`.
        pump(agent, lambda: code("ua_unreachable") == HealthState.OK.value, seconds=30)

        # A NEW CALL IS ANSWERED, which is the fact that matters: the agent used
        # to be alive, registered, and unable to answer anything ever again.
        caller.command("dial", f"sip:agent@127.0.0.1:{ours.sip_port}")
        pump(agent, lambda: agent.session is not None, seconds=30)
        assert agent.session.intercom.sip_uri == "sip:door1@127.0.0.1"
    finally:
        try:
            ua.hangup_all()
        finally:
            caller.close()
            ua.close()
