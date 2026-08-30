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
    for one in (ours, intercom, person):
        one.start()
    try:
        yield root, registrar, ours, intercom, person
    finally:
        for one in (person, intercom, ours):
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
    root, registrar, ours, _intercom, _person = world
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
    _root, registrar, ours, _intercom, _person = world
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
    _root, _registrar, ours, intercom, person = world
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
    _root, _registrar, ours, _intercom, person = world
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
