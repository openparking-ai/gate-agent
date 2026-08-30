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
from pathlib import Path

import pytest

from conftest import agent_config_for, agent_for
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
        ua.incoming(INTERCOM)
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


def test_the_document_says_can_vend_is_false_and_so_does_the_code(busy):
    """The one field this round exists to keep false, checked in both places.

    It is DERIVED from the act table rather than written down, so the day
    something in this package can act, this answer changes with it.
    """
    assert doc_payloads()["agent"]["can_vend"] is False
    assert busy.describe().to_dict()["can_vend"] is False
    assert busy.describe().can_vend is False
    # And the control: the property really does follow the table, so `False` is
    # a measurement rather than a constant.
    from gate_agent import contract

    contract.ACTS[contract.Authorisation.OPEN_NOW] = "planted"
    try:
        assert busy.describe().can_vend is True
    finally:
        contract.ACTS.clear()
    assert busy.describe().can_vend is False


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

    class Answering:
        """A control socket that answers `reginfo` and `about`, netstring-framed."""

        def __init__(self, version: str) -> None:
            self.version = version
            self.pending = b""

        def settimeout(self, _timeout):
            pass

        def sendall(self, payload: bytes) -> None:
            body = payload.split(b":", 1)[1][:-1].decode()
            token = json.loads(body)["token"]
            command = json.loads(body)["command"]
            data = f"baresip {self.version}" if command == "about" else ""
            answer = json.dumps(
                {"response": True, "ok": True, "data": data, "token": token}
            ).encode()
            self.pending += b"%d:%s," % (len(answer), answer)

        def recv(self, _size: int) -> bytes:
            out, self.pending = self.pending, b""
            return out

        def close(self):
            pass

    def connect(version):
        return lambda _address, timeout=None: Answering(version)

    # The control: the version this build WAS tested against starts.
    good = BaresipUa("127.0.0.1", 1, "sip:a@h", "sip:b@h", connect=connect(TESTED_VERSIONS[0]))
    good.start()
    assert good.version() == TESTED_VERSIONS[0]

    bad = BaresipUa("127.0.0.1", 1, "sip:a@h", "sip:b@h", connect=connect("9.9.9"))
    with pytest.raises(UaUnsupportedVersion) as raised:
        bad.start()
    assert "9.9.9" in str(raised.value)


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
    ua.incoming(INTERCOM)
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
    ua.incoming(INTERCOM, call_id="call-2")
    for _ in range(200):
        agent.poll()
        if len([1 for verb, _ in ua.commands if verb == "dial"]) >= 2:
            break
        clock.advance(2.0)
    operator = [arg for verb, arg in ua.commands if verb == "dial"][-1].split("-> ")[1]
    ua.established(operator)
    agent.poll()
    assert state_of(agent) == HealthState.OK.value
