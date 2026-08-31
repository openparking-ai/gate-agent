"""The agent's surface: what it publishes, what it refuses, and the document.

The same two comparisons the monitor's surface gets, because one of them is not
enough. **By SHAPE** -- every `<!--payload:NAME-->` example is parsed and its key
structure compared against what the code builds, which catches a field added,
renamed or dropped. **By VALUE** for the fields that are constants, because
`shape()` discards every leaf and a document could otherwise publish
`contract_version: 99` or `can_vend: true` with the shape check green.

`can_vend` gets its own test. It is the field this whole round is about.
"""

from __future__ import annotations

import json
import re
from dataclasses import replace
from pathlib import Path

import pytest

from conftest import INTERCOM_ACCOUNT, agent_config_for, agent_for
from fake_ua import FakeUa
from foreign_lane import ForeignLane
from foreign_lane import make_server as foreign_server
from gate_agent.agent_service import (
    ACT_ROUTES,
    READ_ROUTES,
    AgentService,
    InsecureBind,
    _Handler,
    make_server,
)
from gate_agent.contract import (
    CONTRACT_VERSION,
    AgentCode,
    HealthState,
    Source,
)
from serving import serving

CONTRACT_DOC = Path(__file__).resolve().parent.parent / "docs" / "CONTRACT.md"
INTERCOM = "sip:door1@10.0.0.9"

AGENT_PAYLOADS = {"agent", "agent_health", "agent_events"}


def doc_payloads() -> dict[str, dict]:
    text = CONTRACT_DOC.read_text(encoding="utf-8")
    found = re.findall(r"<!--payload:([a-z_]+)-->\s*```json\n(.*?)\n```", text, re.S)
    return {name: json.loads(body) for name, body in found}


def shape(value):
    if isinstance(value, dict):
        return {key: shape(item) for key, item in sorted(value.items())}
    if isinstance(value, list):
        return [shape(value[0])] if value else []
    return None


@pytest.fixture
def busy(tmp_path):
    """An agent that has taken one whole call, so no payload is empty.

    A payload with nothing in it reduces to an empty shape, and a document
    comparison against nothing would pass whatever the document said.
    """
    lane = ForeignLane()
    with serving(foreign_server(lane)) as url:
        ua = FakeUa()
        agent = agent_for(agent_config_for(tmp_path, lane_url=url), ua)
        ua.incoming(INTERCOM, account_user=INTERCOM_ACCOUNT)
        agent.poll()
        yield agent


def live(agent) -> dict[str, dict]:
    return {
        "agent": agent.describe().to_dict(),
        "agent_health": agent.health().to_dict(),
        "agent_events": agent.events(0).to_dict(),
    }


def test_the_document_shows_exactly_the_payloads_the_code_builds(busy):
    doc = doc_payloads()
    assert AGENT_PAYLOADS <= set(doc), sorted(AGENT_PAYLOADS - set(doc))
    served = live(busy)
    for name in sorted(AGENT_PAYLOADS):
        assert shape(doc[name]) == shape(served[name]), (
            f"docs/CONTRACT.md's `{name}` example does not have the shape the code builds.\n"
            f"  doc:  {shape(doc[name])}\n  code: {shape(served[name])}"
        )
    # The control: the examples really are populated, so the comparison above
    # was not against empty lists.
    assert served["agent_health"]["codes"]
    assert served["agent_events"]["events"]
    assert served["agent"]["intercoms"]


def test_the_documents_contract_version_is_the_codes(busy):
    doc = doc_payloads()
    for name in AGENT_PAYLOADS:
        assert doc[name]["contract_version"] == CONTRACT_VERSION, name


def test_can_vend_is_derived_per_lane_from_what_this_agent_actually_holds(busy):
    """The field whose MEANING this round inverts, and it is derived both times.

    For two rounds it was `bool(ACTS)` and the answer was `false` everywhere,
    because nothing in this package could act. That question is now true
    everywhere and answers nothing about a SITE: an agent with an act table and
    no act token opens nothing.

    So it is per lane, and it is `true` only where this agent holds BOTH an act
    token for that lane and a key to sign a ticket with -- a completion names an
    identity, and there has to be one to name.

    **It is about the AGENT and not about the lane.** The lane's own `can_vend`
    is on the lane's surface; republishing it here would be this agent making a
    claim about another machine out of a read it may not have made since that
    machine restarted.
    """
    # `busy` declares a lane and no act token, so there is a lane to answer
    # about and the answer is `false` -- which is the case that matters: an
    # empty list would make the summary `false` for a reason that has nothing to
    # do with what this agent holds.
    described = busy.describe()
    assert [one.name for one in described.lanes] == ["entry"]
    assert described.lanes[0].can_vend is False
    assert described.can_vend is False
    assert described.to_dict()["can_vend"] is False
    assert described.to_dict()["lanes"] == [{"name": "entry", "can_vend": False}]
    # And the DOCUMENT shows the other side of it, so a reader sees what a lane
    # with an act token looks like rather than only the empty case.
    published = doc_payloads()["agent"]
    assert published["can_vend"] is True
    assert published["lanes"] == [{"name": "entry", "can_vend": True}]


def test_can_vend_needs_the_act_token_AND_the_key_and_neither_alone(tmp_path):
    """Both halves, each varied on its own. Three of the four combinations are
    `false`, and a check that only tried the two ends would pass on a field that
    read either one of them."""
    from conftest import agent_config_for, agent_for
    from fake_ua import FakeUa
    from gate_agent.config import Target, TicketSettings
    from gate_agent.contract import TargetKind

    def described(act_token, tickets):
        base = agent_config_for(tmp_path, lane_url="http://127.0.0.1:1")
        config = replace(
            base,
            lanes=(
                Target(
                    name="entry",
                    kind=TargetKind.LANE,
                    url="http://127.0.0.1:1",
                    poll_seconds=2.0,
                    act_token=act_token,
                ),
            ),
            tickets=tickets,
        )
        return agent_for(config, FakeUa()).describe()

    settings = TicketSettings(
        signing_key=b"a-key-long-enough-for-the-floor-0000",
        directory=tmp_path / "tickets",
    )
    assert described("a-token", settings).lanes[0].can_vend is True
    assert described(None, settings).lanes[0].can_vend is False
    assert described("a-token", None).lanes[0].can_vend is False
    assert described(None, None).lanes[0].can_vend is False


def test_every_agent_code_ships_on_every_response(busy):
    """A code that is absent reads to a consumer exactly like one that is fine."""
    codes = {entry["code"] for entry in busy.health().to_dict()["codes"]}
    assert codes == {code.value for code in AgentCode}


def test_a_declared_lane_is_measured_by_name(tmp_path):
    """`lane_unavailable` per DECLARED lane, whether or not a call happened.

    A lane nobody has asked about is a lane nobody has measured, and it ships
    `unknown` rather than being absent -- which would read as healthy.
    """
    ua = FakeUa()
    agent = agent_for(agent_config_for(tmp_path, lane_url="http://127.0.0.1:1"), ua)
    entries = [
        entry
        for entry in agent.health().to_dict()["codes"]
        if entry["code"] == "lane_unavailable"
    ]
    assert [entry["subject"] for entry in entries] == ["entry"]
    assert entries[0]["state"] == HealthState.UNKNOWN.value
    assert entries[0]["source"] == Source.MEASURED.value


def test_registration_is_unknown_until_the_user_agent_has_said_something(tmp_path):
    """`unknown`, never `false`. A registration nobody has heard about is not one
    known to be lost, and publishing the second pages somebody to a working site."""
    ua = FakeUa(is_registered=None)
    agent = agent_for(agent_config_for(tmp_path, standalone=True), ua)
    agent.poll()
    entry = [
        one
        for one in agent.health().to_dict()["codes"]
        if one["code"] == "sip_registration_lost"
    ][0]
    assert entry["state"] == HealthState.UNKNOWN.value
    ua.registration_lost()
    agent.poll()
    entry = [
        one
        for one in agent.health().to_dict()["codes"]
        if one["code"] == "sip_registration_lost"
    ][0]
    assert entry["state"] == HealthState.ACTIVE.value


# ---------------------------------------------------------------------------
# The service
# ---------------------------------------------------------------------------


def test_there_is_no_act_route_and_every_other_method_is_one_refusal(busy):
    """Swept out of the handler, exactly as the monitor's and the capture's are.

    A route that opened a barrier would have to stop being this function, and
    this goes red in the same commit.
    """
    assert ACT_ROUTES == ()
    refusals = {
        name: getattr(_Handler, name)
        for name in dir(_Handler)
        if name.startswith("do_") and name != "do_GET"
    }
    assert refusals, "the sweep found no other methods, so it is not looking at anything"
    assert {handler for handler in refusals.values()} == {_Handler._method_not_allowed}


def test_the_routes_answer_and_the_other_methods_are_405(busy):
    import urllib.error
    import urllib.request

    with serving(make_server(AgentService(busy))) as url:
        for route in READ_ROUTES:
            with urllib.request.urlopen(f"{url}{route}", timeout=5) as response:
                body = json.loads(response.read())
            assert body["contract_version"] == CONTRACT_VERSION
        for method in ("POST", "PUT", "PATCH", "DELETE"):
            request = urllib.request.Request(f"{url}/v1/agent", method=method, data=b"{}")
            try:
                urllib.request.urlopen(request, timeout=5)
                raise AssertionError(f"{method} was accepted")
            except urllib.error.HTTPError as exc:
                assert exc.code == 405
                assert exc.headers["Allow"] == "GET"


def test_off_loopback_it_refuses_to_start_without_a_token(busy):
    """One rule for all three surfaces on one device, imported and not restated."""
    with pytest.raises(InsecureBind):
        make_server(AgentService(busy), host="0.0.0.0", port=0)
    server = make_server(AgentService(busy), host="0.0.0.0", port=0, token="t")
    server.server_close()


def test_the_events_route_refuses_a_cursor_it_cannot_read(busy):
    import urllib.error
    import urllib.request

    with serving(make_server(AgentService(busy))) as url:
        for bad in ("abc", "-1"):
            try:
                urllib.request.urlopen(f"{url}/v1/agent/events?since={bad}", timeout=5)
                raise AssertionError(f"{bad} was accepted")
            except urllib.error.HTTPError as exc:
                assert exc.code == 400


def test_a_health_payload_missing_a_code_is_refused_when_it_is_built():
    """Asked of the payload directly, because a live agent always builds a full one.

    The refusal is in the dataclass and it is the reason the set can be relied
    on. A test that only ever looked at a complete payload would leave the
    refusal itself unmeasured -- which is exactly what happened until the
    fail-control planted an incomplete one and the suite stayed green.
    """
    from gate_agent.contract import AgentEntry, AgentHealth

    complete = tuple(
        AgentEntry(code=code.value, subject="x", state=HealthState.UNKNOWN.value)
        for code in AgentCode
    )
    AgentHealth(codes=complete)  # the control: a complete payload is accepted
    with pytest.raises(ValueError) as raised:
        AgentHealth(codes=complete[1:])
    assert "missing" in str(raised.value)
    with pytest.raises(ValueError):
        AgentHealth(codes=complete + complete[:1])


def test_a_keyed_value_that_is_not_digits_is_refused():
    """The one field a caller fills, and the field a plate would end up in.

    A call-back number is digits. Anything else on this surface is text a person
    at a barrier supplied, and this contract has no other field like it.
    """
    from gate_agent.contract import AgentEvent, AgentEventKind

    def event(keyed):
        return AgentEvent(
            kind=AgentEventKind.AUTHORISATION_RECEIVED.value,
            site_id="site-1",
            agent_id="agent-1",
            intercom="sip:door1@10.0.0.9",
            lane=None,
            case=None,
            authorisation="call_back",
            human="sip:duty@10.0.0.5",
            at="2026-08-30T14:00:00+00:00",
            keyed=keyed,
        )

    event("5551234")  # the control: digits are accepted, and so is nothing
    event(None)
    for planted in ("ABC123", "555 1234", "", "AB12CDE"):
        with pytest.raises(ValueError) as raised:
            event(planted)
        assert "digits" in str(raised.value), planted


def test_the_real_adapter_refuses_a_user_agent_version_nobody_tested():
    """The `schema_version` rule applied to a process, in the ADAPTER.

    The fake user agent has a check of its own, which is what most of this suite
    exercises -- so without this the only thing proven would be that the fake
    refuses. Driven through a socket that answers a control message, so the
    refusal is the adapter's.
    """
    from gate_agent.ua import UaUnsupportedVersion
    from gate_agent.ua_baresip import TESTED_VERSIONS, BaresipUa

    #: What a correctly set-up baresip answers `config` and `modules` with. The
    #: real shape, cut down: `config` is `key<TAB>value` lines and `modules` is
    #: `  <name> type=<kind> ref=N`. Both are measured in `test_agent_sip.py`
    #: against the real process; here they are the BACKGROUND to a question
    #: about the version, and the misconfiguration cases have their own test.
    GOOD_CONFIG = (
        "\n# Audio\naudio_player\taufile,/tmp/x.wav\naudio_source\taufile,/tmp/y.wav\n"
        "call_hold_other_calls\tno\n"
    )
    GOOD_MODULES = (
        "\n--- Modules (5) ---\n            g711 type=audio codec  ref=1\n"
        "        ctrl_tcp type=application  ref=1\n"
        "        mixausrc type=filter       ref=1\n"
        "        mixminus type=filter       ref=1\n"
        "       debug_cmd type=application  ref=1\n\n"
    )

    class Answering:
        """A control socket that answers `reginfo`, `about`, `config`, `modules`."""

        def __init__(self, version: str, config: str = GOOD_CONFIG,
                     modules: str = GOOD_MODULES) -> None:
            self.version = version
            self.config = config
            self.modules = modules
            self.pending = b""

        def settimeout(self, _timeout):
            pass

        def sendall(self, payload: bytes) -> None:
            body = payload.split(b":", 1)[1][:-1].decode()
            token = json.loads(body)["token"]
            command = json.loads(body)["command"]
            data = {
                "about": f"baresip {self.version}",
                "config": self.config,
                "modules": self.modules,
            }.get(command, "")
            answer = json.dumps(
                {"response": True, "ok": True, "data": data, "token": token}
            ).encode()
            self.pending += b"%d:%s," % (len(answer), answer)

        def recv(self, _size: int) -> bytes:
            out, self.pending = self.pending, b""
            return out

        def close(self):
            pass

    def connect(version, **kwargs):
        return lambda _address, timeout=None: Answering(version, **kwargs)

    # The control: the version this build WAS tested against starts.
    good = BaresipUa("127.0.0.1", 1, operator_aor="sip:agent-operator@h",
                     connect=connect(TESTED_VERSIONS[0]))
    good.start()
    assert good.version() == TESTED_VERSIONS[0]

    bad = BaresipUa("127.0.0.1", 1, operator_aor="sip:agent-operator@h",
                    connect=connect("9.9.9"))
    with pytest.raises(UaUnsupportedVersion) as raised:
        bad.start()
    assert "9.9.9" in str(raised.value)


def test_the_real_adapter_refuses_a_baresip_configuration_it_cannot_work_on(tmp_path):
    """X5. The three settings are READ BACK OUT OF THE PROCESS, and named.

    The source used to say they were "checked at startup" while
    `config/agent.example.toml` said, correctly, that they were checked nowhere
    but in baresip's own configuration file. Two copies of a claim, and the
    hand-written one was the one that lied.

    Every case here names the setting in the refusal, because a site reading
    "the user agent is misconfigured" has been told nothing.
    """
    import json as _json

    from gate_agent.ua import UaMisconfigured
    from gate_agent.ua_baresip import TESTED_VERSIONS, BaresipUa

    GOOD_CONFIG = (
        "\naudio_player\taufile,/tmp/x.wav\naudio_source\taufile,/tmp/y.wav\n"
        "call_hold_other_calls\tno\n"
    )
    GOOD_MODULES = (
        "\n--- Modules (5) ---\n            g711 type=audio codec  ref=1\n"
        "        ctrl_tcp type=application  ref=1\n"
        "        mixausrc type=filter       ref=1\n"
        "        mixminus type=filter       ref=1\n"
        "       debug_cmd type=application  ref=1\n\n"
    )

    class Answering:
        def __init__(self, config, modules, refuse=()):
            self.config, self.modules, self.refuse = config, modules, refuse
            self.pending = b""

        def settimeout(self, _t):
            pass

        def sendall(self, payload):
            body = _json.loads(payload.split(b":", 1)[1][:-1].decode())
            command, token = body["command"], body["token"]
            ok = command not in self.refuse
            data = {
                "about": f"baresip {TESTED_VERSIONS[0]}",
                "config": self.config,
                "modules": self.modules,
            }.get(command, "")
            answer = _json.dumps(
                {"response": True, "ok": ok, "data": data if ok else "unknown command",
                 "token": token}
            ).encode()
            self.pending += b"%d:%s," % (len(answer), answer)

        def recv(self, _size):
            out, self.pending = self.pending, b""
            return out

        def close(self):
            pass

    def ua(config=GOOD_CONFIG, modules=GOOD_MODULES, refuse=()):
        return BaresipUa(
            "127.0.0.1", 1, operator_aor="sip:agent-operator@h",
            connect=lambda _a, timeout=None: Answering(config, modules, refuse),
        )

    # THE CONTROL, first: a baresip set up the way the contract says starts.
    ua().start()

    # `aubridge` on either device, named.
    for key in ("audio_source", "audio_player"):
        broken = GOOD_CONFIG.replace(f"{key}\taufile", f"{key}\taubridge")
        assert "aubridge" in broken
        with pytest.raises(UaMisconfigured) as raised:
            ua(config=broken).start()
        assert "aubridge" in str(raised.value) and key in str(raised.value)

    # `call_hold_other_calls yes`, named. It is what would put the driver at the
    # barrier on hold the moment the agent calls the operator.
    with pytest.raises(UaMisconfigured) as raised:
        ua(config=GOOD_CONFIG.replace("call_hold_other_calls\tno",
                                      "call_hold_other_calls\tyes")).start()
    assert "call_hold_other_calls" in str(raised.value)

    # Each required module, missing, named. One at a time, so the message is
    # about the one that is gone rather than about all of them.
    for module in ("ctrl_tcp", "mixausrc", "mixminus"):
        without = "\n".join(
            line for line in GOOD_MODULES.splitlines() if not line.strip().startswith(module)
        )
        with pytest.raises(UaMisconfigured) as raised:
            ua(modules=without).start()
        assert module in str(raised.value)

    # And a baresip with no `debug_cmd`, which is what refuses the two commands
    # this check is made of. A check that quietly did not run is the thing this
    # replaced, so it is a refusal and it names the module.
    for command in ("config", "modules"):
        with pytest.raises(UaMisconfigured) as raised:
            ua(refuse=(command,)).start()
        assert "debug_cmd" in str(raised.value)


def test_human_unreachable_recovers_when_the_person_answers(tmp_path):
    """A code that could only ever go one way is a latch that reads like a state.

    `active` for the life of the process however long ago the rota was fixed,
    with no recovery for a monitor to report — which is the shape the lane
    contract already names and refuses.
    """
    from conftest import FakeClock
    from fake_ua import FakeUa

    def state_of(agent):
        return [
            one
            for one in agent.health().to_dict()["codes"]
            if one["code"] == "human_unreachable"
        ][0]["state"]

    clock = FakeClock()
    ua = FakeUa()
    agent = agent_for(
        agent_config_for(tmp_path, standalone=True, no_answer_seconds=30.0), ua, clock=clock
    )
    ua.incoming(INTERCOM, account_user=INTERCOM_ACCOUNT)
    for _ in range(200):
        agent.poll()
        if any(verb == "dial" for verb, _ in ua.commands):
            break
        clock.advance(2.0)
    clock.advance(31)
    agent.poll()
    assert state_of(agent) == HealthState.ACTIVE.value

    # And the next call they DO answer clears it.
    for _ in range(200):
        agent.poll()
        if agent.session is None:
            break
        clock.advance(2.0)
    ua.incoming(INTERCOM, call_id="call-2", account_user=INTERCOM_ACCOUNT)
    for _ in range(200):
        agent.poll()
        if len([1 for verb, _ in ua.commands if verb == "dial"]) >= 2:
            break
        clock.advance(2.0)
    operator = [arg for verb, arg in ua.commands if verb == "dial"][-1].split("-> ")[1]
    ua.established(operator)
    agent.poll()
    assert state_of(agent) == HealthState.OK.value


def test_the_control_socket_is_reopened_with_a_bounded_backoff():
    """X7. A lost `ctrl_tcp` used to be a PERMANENT outage.

    `_open` was called once and from nowhere else, and every read raised on a
    socket that was never replaced -- so an ordinary `systemctl restart
    baresip`, a package upgrade, an OOM kill, or the second `ctrl_tcp` client
    the contract already names left the agent alive, its user agent registered,
    and every call ringing at a process that would never answer one. It failed
    LOUDLY, which is this project's standing acceptance, and it never recovered.

    Driven here against a socket a test can kill and revive, so the backoff and
    the recovery are measured on the adapter itself; the real one, with a real
    baresip and a real intruder on the port, is in `test_agent_sip.py`.
    """
    import json as _json

    from gate_agent.ua import UaUnreachable
    from gate_agent.ua_baresip import RECONNECT_FLOOR, TESTED_VERSIONS, BaresipUa

    CONFIG = (
        "\naudio_player\taufile,/tmp/x.wav\naudio_source\taufile,/tmp/y.wav\n"
        "call_hold_other_calls\tno\n"
    )
    MODULES = (
        "\n--- Modules (4) ---\n        ctrl_tcp type=application  ref=1\n"
        "        mixausrc type=filter       ref=1\n"
        "        mixminus type=filter       ref=1\n"
        "       debug_cmd type=application  ref=1\n\n"
    )

    class Answering:
        def __init__(self, world):
            self.world = world
            self.pending = b""
            self.dead = False

        def settimeout(self, _t):
            pass

        def sendall(self, payload):
            if self.dead:
                raise OSError("broken pipe")
            body = _json.loads(payload.split(b":", 1)[1][:-1].decode())
            data = {
                "about": f"baresip {TESTED_VERSIONS[0]}",
                "config": CONFIG,
                "modules": MODULES,
                "listcalls": self.world["listcalls"],
            }.get(body["command"], "")
            answer = _json.dumps(
                {"response": True, "ok": True, "data": data, "token": body["token"]}
            ).encode()
            self.pending += b"%d:%s," % (len(answer), answer)

        def recv(self, _size):
            if self.dead:
                return b""          # what a closed control socket reads as
            if not self.pending:
                # A live socket with nothing on it. `_drain` sets a zero
                # timeout, and this is what a real one does then -- NOT an
                # empty read, which is what "the far end closed" looks like.
                raise BlockingIOError
            out, self.pending = self.pending, b""
            return out

        def close(self):
            self.dead = True

    world = {"listcalls": "", "up": True, "sockets": [], "now": [0.0]}

    def connect(_address, timeout=None):
        if not world["up"]:
            raise OSError("connection refused")
        sock = Answering(world)
        world["sockets"].append(sock)
        return sock

    ua = BaresipUa(
        "127.0.0.1", 1, operator_aor="sip:agent-operator@h",
        connect=connect, reconnect_seconds=4.0, clock=lambda: world["now"][0],
    )
    ua.start()
    assert ua.poll() == ()

    # The user agent goes. The next poll raises, and the socket is DROPPED --
    # which is what used to be missing and is what makes a reopen possible.
    world["sockets"][-1].dead = True
    world["up"] = False
    with pytest.raises(UaUnreachable):
        ua.poll()
    assert ua._sock is None

    # Too soon: the backoff is real, and reconnect says so rather than
    # hammering a dead process once per poll for ever.
    with pytest.raises(UaUnreachable):
        ua.reconnect()

    # THE BACKOFF IS BOUNDED. It doubles from the floor and stops at the
    # setting; measured by asking when the next attempt is allowed.
    gaps = []
    for _ in range(6):
        world["now"][0] += 100.0
        with pytest.raises(UaUnreachable):
            ua.reconnect()          # still down; schedules the next gap
        gaps.append(ua._retry_gap)
    assert gaps[0] > RECONNECT_FLOOR, gaps
    assert max(gaps) <= 4.0, f"the backoff ran past the setting: {gaps}"
    assert gaps == sorted(gaps), f"the backoff did not grow: {gaps}"

    # It comes back, and it is holding one RINGING call and one established one.
    world["up"] = True
    world["listcalls"] = (
        "\nUser-Agent: agent@h\n--- Active calls (2) ---\n"
        "  [line 1, id aa11bb22]  00:00:03   INCOMING             sip:door1@10.0.0.9\n"
        "  [line 2, id cc33dd44]  00:01:20   ESTABLISHED          sip:duty@10.0.0.5\n\n"
    )
    world["now"][0] += 100.0
    found = ua.reconnect()
    assert ua._sock is not None
    assert [(one.call_id, one.ringing) for one in found] == [
        ("aa11bb22", True), ("cc33dd44", False)
    ]
    assert found[0].peer_uri == "sip:door1@10.0.0.9"
    # And the gap is back to the floor, so the NEXT outage is recovered from
    # just as fast as this one.
    assert ua._retry_gap == RECONNECT_FLOOR


def test_a_call_that_arrived_while_the_socket_was_down_is_answered_or_released(tmp_path, capsys):
    """X7, at the agent: what a reopened socket does with what it finds.

    Whatever case was in progress is gone -- its legs were torn down or are
    beyond reach, and nothing can say what was said while nobody was listening.
    So the session is dropped with `case_not_spoken`, and each call the user
    agent still holds gets the rule any new call gets: still RINGING is
    answered, anything else is released rather than left live to be conferenced
    into the next case.
    """
    from conftest import agent_config_for, agent_for
    from fake_ua import FakeUa
    from gate_agent.contract import AgentEventKind
    from gate_agent.ua import UaCall, UaUnreachable

    class Reconnecting(FakeUa):
        """A fake whose socket can be taken away, and stays away until reopened.

        The staying-away is the part that matters and the part a looser fake
        would get wrong: the real adapter drops the socket on any loss, so every
        later verb raises until `reconnect()` actually opens a new one. A fake
        that started working again on its own would let an agent that never
        reconnects pass this test.
        """

        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.down = False
            self.broken = False
            self.reconnects = 0

        def poll(self):
            if self.down or self.broken:
                self.broken = True
                raise UaUnreachable("the user agent closed its control socket")
            return super().poll()

        def reconnect(self):
            self.reconnects += 1
            if self.down:
                raise UaUnreachable("still down")
            self.broken = False
            return tuple(self.held)

    ua = Reconnecting()
    agent = agent_for(agent_config_for(tmp_path, standalone=True), ua)
    ua.incoming("sip:door1@10.0.0.9", call_id="driver-1",
                account_user=INTERCOM_ACCOUNT)
    agent.poll()
    assert agent.session is not None

    # The socket goes. The agent says so, and keeps trying.
    ua.down = True
    agent.poll()
    assert [
        entry["state"] for entry in agent.health().to_dict()["codes"]
        if entry["code"] == "ua_unreachable"
    ] == ["active"]
    assert ua.reconnects == 1
    agent.poll()
    assert ua.reconnects == 2, "the agent stopped trying to come back"

    # It comes back holding a ringing call and an orphaned one.
    ua.down = False
    ua.held = [
        # The ACCOUNT comes back with the call. `listcalls` groups its calls
        # under the account holding them, one heading per user agent -- measured
        # on baresip 4.11.0 -- and without it a recovered call could not be
        # placed at an intercom and would have to be released.
        UaCall(call_id="ringing-9", peer_uri="sip:door1@10.0.0.9", ringing=True,
               account_user=INTERCOM_ACCOUNT),
        UaCall(call_id="orphan-9", peer_uri="sip:duty@10.0.0.5", ringing=False,
               account_user="agent-operator"),
    ]
    agent.poll()

    assert [
        entry["state"] for entry in agent.health().to_dict()["codes"]
        if entry["code"] == "ua_unreachable"
    ] == ["ok"], "`ua_unreachable` did not recover on the reconnect"
    events = [event["kind"] for event in agent.events(0).to_dict()["events"]]
    assert AgentEventKind.CASE_NOT_SPOKEN.value in events
    # The still-ringing call is ANSWERED and becomes the new case.
    assert ("answer", "ringing-9") in ua.commands
    assert agent.session is not None and agent.session.driver_call == "ringing-9"
    # The orphan is RELEASED. A leg left live is a leg conferenced into the
    # next case, which is blocker 1's harm arriving by another road.
    assert ("hangup", "orphan-9") in ua.commands


def test_a_recovered_call_at_an_undeclared_account_is_released_not_answered(tmp_path):
    """The reconnect path is not a way round the account check.

    A call the user agent was holding when the socket came back goes through the
    SAME rule as any other, and a ringing one whose account nobody declared is
    refused rather than answered -- otherwise every guarantee about who gets
    answered would have a second door with no lock on it.
    """
    from conftest import INTERCOM_ACCOUNT, agent_config_for, agent_for
    from fake_ua import FakeUa
    from gate_agent.contract import AgentEventKind
    from gate_agent.ua import UaCall, UaUnreachable

    class Losable(FakeUa):
        """A socket that goes and comes back holding whatever a test put there."""

        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.down = False
            self.broken = False

        def poll(self):
            if self.down or self.broken:
                self.broken = True
                raise UaUnreachable("the user agent closed its control socket")
            return super().poll()

        def reconnect(self):
            if self.down:
                raise UaUnreachable("still down")
            self.broken = False
            return tuple(self.held)

    ua = Losable()
    agent = agent_for(agent_config_for(tmp_path, standalone=True), ua)
    ua.down = True
    agent.poll()
    ua.down = False
    ua.held = [
        UaCall(call_id="ringing-9", peer_uri="sip:door1@10.0.0.9", ringing=True,
               account_user="agent-nothing-this-site-declares"),
    ]
    agent.poll()
    assert ("answer", "ringing-9") not in ua.commands
    assert ("hangup", "ringing-9") in ua.commands
    assert agent.session is None
    assert [event["kind"] for event in agent.events(0).to_dict()["events"]] == [
        AgentEventKind.CALL_FROM_UNDECLARED_INTERCOM.value
    ]

    # THE CONTROL: the same path, the declared account, IS answered -- so the
    # refusal above is about the account and not about the reconnect.
    ua.broken = True
    ua.held = [
        UaCall(call_id="ringing-10", peer_uri="sip:door1@10.0.0.9", ringing=True,
               account_user=INTERCOM_ACCOUNT),
    ]
    agent.poll()
    agent.poll()
    assert ("answer", "ringing-10") in ua.commands


def test_the_agent_refuses_to_start_without_an_account_for_a_declared_intercom(tmp_path):
    """X2'. A door in this file and not in the user agent's is a silent door.

    baresip answers an INVITE naming an account it does not hold with
    `404 Not Found`, before this process sees anything at all -- so without this
    check the agent would publish a working surface while one entrance's
    intercom did nothing, at whatever hour somebody first pressed it.

    **The message names the intercom and never the account**, because the
    account's user part IS the secret.
    """
    from conftest import INTERCOM_ACCOUNT, agent_config_for
    from fake_ua import FakeUa
    from gate_agent.agent import Agent
    from gate_agent.ua import UaMisconfigured

    config = agent_config_for(tmp_path, standalone=True)
    ua = FakeUa(held_accounts=("agent-operator", "agent-some-other-door"))
    with pytest.raises(UaMisconfigured) as raised:
        Agent(config, ua).start()
    assert "sip:door1@10.0.0.9" in str(raised.value)
    assert INTERCOM_ACCOUNT not in str(raised.value), "the refusal published the secret"

    # THE CONTROL: the same agent, on a user agent that IS holding the account,
    # starts -- so the refusal is about the account and not about starting.
    Agent(config, FakeUa(held_accounts=(INTERCOM_ACCOUNT,))).start()


def test_the_adapter_reads_the_accounts_and_the_registrations_off_reginfo(tmp_path):
    """What `reginfo` actually looks like, in the three states it has.

    Measured on baresip 4.11.0: every account is listed whether or not it
    registers; `OK` and `ERR` appear only where a registration was attempted;
    and an account that has not tried -- the whole of a standalone site, which
    has no registrar -- shows neither. **That third state is `None` and never
    `False`:** a registration nobody has heard about is not one known to be
    lost, and publishing the second pages somebody to a working site.
    """
    import json as _json

    from gate_agent.ua_baresip import BaresipUa

    ANSI_OK = "\x1b[32mOK \x1b[;m"
    ANSI_ERR = "\x1b[31mERR\x1b[;m"

    class Answering:
        def __init__(self, reginfo):
            self.reginfo, self.pending = reginfo, b""

        def settimeout(self, _t):
            pass

        def sendall(self, payload):
            body = _json.loads(payload.split(b":", 1)[1][:-1].decode())
            data = self.reginfo if body["command"] == "reginfo" else ""
            answer = _json.dumps(
                {"response": True, "ok": True, "data": data, "token": body["token"]}
            ).encode()
            self.pending += b"%d:%s," % (len(answer), answer)

        def recv(self, _size):
            out, self.pending = self.pending, b""
            return out

        def close(self):
            pass

    def ua(reginfo):
        made = BaresipUa(
            "127.0.0.1", 1, operator_aor="sip:agent-operator@h",
            connect=lambda _a, timeout=None: Answering(reginfo),
        )
        return made

    registered = (
        "\n--- User Agents (3) ---\n"
        f"0 - sip:agent-aaaa@127.0.0.1:5060 {ANSI_OK}  Expires 60s\n"
        f"1 - sip:agent-bbbb@127.0.0.1:5060 {ANSI_OK}  Expires 60s\n"
        f"2 - sip:agent-operator@127.0.0.1:5060 {ANSI_OK}  Expires 60s\n\n"
    )
    standalone = (
        "\n--- User Agents (2) ---\n"
        "0 - sip:agent-aaaa@127.0.0.1      \n"
        "1 - sip:agent-operator@127.0.0.1      \n\n"
    )
    refused_one = (
        "\n--- User Agents (2) ---\n"
        f"0 - sip:agent-aaaa@127.0.0.1:5060 {ANSI_ERR} \n"
        "1 - sip:agent-bbbb@127.0.0.1      \n\n"
    )

    one = ua(registered)
    one._open()
    assert one.accounts() == ("agent-aaaa", "agent-bbbb", "agent-operator")
    one._read_registration()
    assert one.registered() is True

    two = ua(standalone)
    two._open()
    assert two.accounts() == ("agent-aaaa", "agent-operator")
    two._read_registration()
    assert two.registered() is None, "a site with no registrar was reported as LOST"

    three = ua(refused_one)
    three._open()
    assert three.accounts() == ("agent-aaaa", "agent-bbbb")
    three._read_registration()
    assert three.registered() is False

    # AND THE STATE COMES FROM ITS OWN FIELD, not from a search of the line. A
    # dial secret is an arbitrary string: an account whose user part happens to
    # contain `ERR` is a healthy account, and reading it as a lost registration
    # would page somebody to a working site. It is this project's standing
    # error -- a check that matched a word instead of a position -- so it gets a
    # case of its own.
    trap = (
        "\n--- User Agents (2) ---\n"
        f"0 - sip:agent-zzERRzzOKzz0000@127.0.0.1:5060 {ANSI_OK}  Expires 60s\n"
        f"1 - sip:agent-operator@127.0.0.1:5060 {ANSI_OK}  Expires 60s\n\n"
    )
    four = ua(trap)
    four._open()
    assert four.accounts() == ("agent-zzERRzzOKzz0000", "agent-operator")
    four._read_registration()
    assert four.registered() is True, "a secret containing `ERR` read as a lost registration"


def test_listcalls_says_which_account_holds_each_call(tmp_path):
    """The account has to survive a lost control socket, or a recovered call
    cannot be placed at an intercom and has to be released.

    `listcalls` groups its calls under the account holding them, one heading per
    user agent -- measured on baresip 4.11.0 with two accounts registered and a
    call ringing at the second.
    """
    import json as _json

    from gate_agent.ua_baresip import BaresipUa

    LISTCALLS = (
        "\nUser-Agent: agent-aaaa@127.0.0.1\n"
        "--- Active calls (0) ---\n\n"
        "\nUser-Agent: agent-bbbb@127.0.0.1\n"
        "--- Active calls (1) ---\n"
        "> [line 1, id 86bd03e1b6b94f51]  0:00:00   INCOMING             sip:door1@10.0.0.9\n"
    )

    class Answering:
        def __init__(self):
            self.pending = b""

        def settimeout(self, _t):
            pass

        def sendall(self, payload):
            body = _json.loads(payload.split(b":", 1)[1][:-1].decode())
            data = LISTCALLS if body["command"] == "listcalls" else ""
            answer = _json.dumps(
                {"response": True, "ok": True, "data": data, "token": body["token"]}
            ).encode()
            self.pending += b"%d:%s," % (len(answer), answer)

        def recv(self, _size):
            out, self.pending = self.pending, b""
            return out

        def close(self):
            pass

    made = BaresipUa(
        "127.0.0.1", 1, operator_aor="sip:agent-operator@h",
        connect=lambda _a, timeout=None: Answering(),
    )
    made._open()
    held = made.calls()
    assert len(held) == 1
    assert held[0].call_id == "86bd03e1b6b94f51"
    assert held[0].ringing is True
    assert held[0].account_user == "agent-bbbb", "the call was placed at the wrong account"


def test_the_adapter_turns_a_404_into_an_event_and_leaves_the_rest_alone(tmp_path):
    """What baresip puts on the socket when it refuses an INVITE itself.

    Measured on 4.11.0, with a registrar and without one: an INVITE naming no
    account it holds is answered `404 Not Found` and reported as
    `SIPSESS_FAILED` carrying the caller's `From`. The OTHER reasons that event
    carries -- a call limit, a bad `Via` -- are DROPPED rather than renamed, by
    the same rule as every unrecognised event type.
    """
    from gate_agent.ua import UaEventKind
    from gate_agent.ua_baresip import BaresipUa

    made = BaresipUa("127.0.0.1", 1, operator_aor="sip:agent-operator@h")
    made._event({
        "event": True, "class": "sip", "type": "SIPSESS_FAILED",
        "param": "404 Not Found", "from": "sip:scanner@10.9.9.9",
        "contact": "<sip:caller@10.9.9.9:5060>",
    })
    events = tuple(made._events)
    made._events.clear()
    assert [one.kind for one in events] == [UaEventKind.CALL_TO_UNKNOWN_ACCOUNT]
    assert events[0].peer_uri == "sip:scanner@10.9.9.9"
    assert events[0].call_id is None

    # The rest are dropped. A call limit is not an unknown account, and giving
    # it that name would put a guess in the record of what happened.
    made._event({
        "event": True, "class": "sip", "type": "SIPSESS_FAILED",
        "param": "486 Max Calls", "from": "sip:door1@10.0.0.9",
    })
    assert tuple(made._events) == ()


def test_a_starting_agent_releases_the_calls_a_previous_process_left(tmp_path):
    """F0.7. `start()` enumerated NOTHING, and the leftovers were bridged.

    The round-5 merge gate measured it and left it open. baresip is a separate
    program: it outlives the agent, stays registered, and keeps the calls. A
    restarted agent answered the next call with the previous process's legs
    still live, and this user agent's bridge is SITE-WIDE -- so the previous
    driver and the previous operator were about to be conferenced into a
    stranger's case.

    `_reconnect()` covers a socket lost inside a RUNNING process. It could never
    cover this: a new process has no socket to lose.

    The SIP suite runs this against a real baresip holding two real calls
    (`test_a_restarted_agent_releases_the_calls_the_previous_one_left`). This is
    the same property where the fail-control can reach it -- that script runs
    `-m "not sip"`, so a guarantee measured only over there is one no control
    proves.
    """
    from conftest import agent_config_for, agent_for
    from fake_ua import FakeUa
    from gate_agent.contract import AgentEventKind
    from gate_agent.ua import UaCall

    ua = FakeUa()
    # WHAT THE PREVIOUS PROCESS LEFT: one leg at the door and one at the person,
    # and one of them still ringing -- a ringing call is the one `_reconnect()`
    # ANSWERS, so if `start()` shared that rule it would answer this one.
    ua.held = [
        UaCall(call_id="left-driver", peer_uri="sip:door1@10.0.0.9", ringing=False,
               account_user=INTERCOM_ACCOUNT),
        UaCall(call_id="left-operator", peer_uri="sip:duty@10.0.0.5", ringing=False,
               account_user="agent-operator"),
        UaCall(call_id="left-ringing", peer_uri="sip:door1@10.0.0.9", ringing=True,
               account_user=INTERCOM_ACCOUNT),
    ]
    agent = agent_for(agent_config_for(tmp_path, standalone=True), ua)

    # ALL THREE ARE GONE, the ringing one included: nothing here knows how long
    # it has been ringing, no session exists behind it, and its lane read would
    # be one this process never made.
    assert not ua.calls(), [one.call_id for one in ua.calls()]
    assert [command for command in ua.commands if command[0] == "answer"] == []
    assert agent.session is None

    # AND THE COUNT IS ON THE RECORD, once, with no call id in it.
    events = agent.events(0).to_dict()["events"]
    kind = AgentEventKind.LEFTOVER_CALLS_RELEASED.value
    released = [one for one in events if one["kind"] == kind]
    assert len(released) == 1, events
    assert released[0]["released"] == 3
    for call_id in ("left-driver", "left-operator", "left-ringing"):
        assert call_id not in json.dumps(released[0])

    # THE HARM: the next call is a clean case with nothing of the old one in it.
    ua.incoming("sip:door1@10.0.0.9", call_id="new-1", account_user=INTERCOM_ACCOUNT)
    agent.poll()
    assert agent.session is not None and agent.session.driver_call == "new-1"
    # Nothing the previous process left is still up. Asserted this way round
    # rather than as an equality with the new call: `FakeUa.incoming` does not
    # put the arriving call into `held`, so an equality here would be a claim
    # about the fake's bookkeeping instead of about the leftovers.
    assert not {one.call_id for one in ua.calls()} & {
        "left-driver", "left-operator", "left-ringing"
    }


def test_an_agent_that_starts_on_a_quiet_user_agent_records_nothing(tmp_path):
    """THE CONTROL for the test above: no leftovers, no event.

    A record of nothing having happened, on every start of every agent for ever,
    is noise in the window that holds what did -- and it would make the count
    above a field that is always there rather than a fact about a restart.
    """
    from conftest import agent_config_for, agent_for
    from fake_ua import FakeUa
    from gate_agent.contract import AgentEventKind

    ua = FakeUa()
    agent = agent_for(agent_config_for(tmp_path, standalone=True), ua)
    kinds = [event["kind"] for event in agent.events(0).to_dict()["events"]]
    assert AgentEventKind.LEFTOVER_CALLS_RELEASED.value not in kinds, kinds
    assert [command for command in ua.commands if command[0] == "hangup"] == []
