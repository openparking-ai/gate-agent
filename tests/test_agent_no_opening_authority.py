"""THE INVARIANT for the third process: the agent opens nothing.

The same three questions the monitor's sweep asks, asked of the agent, because
this is the process that has a person on a phone saying "open the gate" and a
record of them saying it.

  1. **Could it?** The seam it drives has seven verbs and none of them opens
     anything; the source of `agent.py` is walked for every call it makes on the
     user agent and the set must be exactly those six. A seventh, added quietly,
     goes red here.
  2. **Did it?** A whole `OPEN_NOW` dialogue is run against a lane that RECORDS
     what reached it, and the set of methods must be exactly `{"GET"}` and the
     set of paths exactly the two an agent has business on.
  3. **Would it show?** A user agent that CAN open a barrier is run through the
     same dialogue. The barrier must not move -- and the control is that the same
     object moves it when something asks, so "nothing opened" is a statement
     about the world rather than about a search that found nothing.

And one more, because this round added the first module in the package that
opens a socket of its own: only that module may, and it may not be able to see a
lane's address or its credential.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

import gate_agent
from conftest import INTERCOM_ACCOUNT, agent_config_for, agent_for
from fake_ua import ActingUa, FakeUa
from foreign_lane import ForeignLane
from foreign_lane import make_server as foreign_server
from ours import our_server
from serving import serving

PACKAGE = Path(gate_agent.__file__).resolve().parent
INTERCOM = "sip:door1@10.0.0.9"

#: The whole seam. Seven verbs and two accessors, and NONE of them opens
#: anything. `accounts` is the one this round added: it asks which local
#: accounts the user agent holds, which is what says which intercom a call is
#: from, and it can no more open a barrier than `version` can.
#: Written here rather than derived from the class, because the point is to
#: notice a verb being ADDED -- an expectation derived from the thing under test
#: cannot.
SEAM = {
    "start", "version", "registered", "poll", "accounts",
    "answer", "dial", "play", "stop_playing", "bridge", "hangup", "hangup_all",
}

#: The one module in this package that opens a socket that is not urllib's. It
#: talks to the USER AGENT, which is a local process, and it is named here once
#: so a second is a change to this list and therefore a change somebody argues
#: for.
MAY_OPEN_A_SOCKET = {"ua_baresip.py"}


def test_the_agent_asks_the_user_agent_for_nothing_but_the_seam():
    """Every call `agent.py` makes on the user agent, out of its own source."""
    tree = ast.parse((PACKAGE / "agent.py").read_text(encoding="utf-8"))
    asked = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Attribute)
        and node.func.value.attr == "ua"
    }
    assert asked, "the sweep found no calls on the user agent, so it is looking at nothing"
    assert asked <= SEAM, f"the agent asks the user agent for {sorted(asked - SEAM)}"
    # THE CONTROL: the same walk over source that DOES ask for something else
    # finds it, so the assertion above is about the verbs and not about a walk
    # that sees nothing.
    planted = ast.parse("self.ua.vend()\nself.ua.answer(x)\n")
    found = {
        node.func.attr
        for node in ast.walk(planted)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Attribute)
        and node.func.value.attr == "ua"
    }
    assert found == {"vend", "answer"} and not found <= SEAM


def test_only_one_module_opens_a_socket_and_it_cannot_see_a_lane():
    """The user agent's control channel is not urllib, so the existing sweeps
    cannot see it. This is the half that keeps that exemption honest.

    The module allowed to open a socket must not be able to obtain a lane's URL
    or its credential -- the same shape as the webhook sink's exemption, and for
    the same reason: an exemption that can reach a target is a hole shaped
    exactly like the thing it was carved around.
    """
    offenders = []
    swept = 0
    for path in sorted(PACKAGE.glob("*.py")):
        if path.name in MAY_OPEN_A_SOCKET:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = (
                node.func.attr
                if isinstance(node.func, ast.Attribute)
                else getattr(node.func, "id", None)
            )
            if name in ("create_connection", "socket"):
                swept += 1
                offenders.append(f"{path.name}: {name}(...)")
    assert not offenders, f"a socket is opened outside {sorted(MAY_OPEN_A_SOCKET)}: {offenders}"

    tree = ast.parse((PACKAGE / "ua_baresip.py").read_text(encoding="utf-8"))
    modules = {
        ("." * node.level) + (node.module or "")
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    }
    names = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }
    assert not (modules & {".client", "gate_agent.client"}), sorted(modules)
    assert "Target" not in names and "ReadOnlyClient" not in names, sorted(names)
    # The control on BOTH halves: the sweep can see a socket call when there is
    # one, and it does see the imports this module really has.
    planted = ast.parse("socket.create_connection(a)\n")
    assert [
        node.func.attr
        for node in ast.walk(planted)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    ] == ["create_connection"]
    assert any(name.startswith("Ua") for name in names), sorted(names)


@pytest.fixture
def recording():
    lane = ForeignLane()
    lane.decision = {
        "outcome": "fallback",
        "reason": "engine_unreachable",
        "fallback": "engine_unreachable",
        "cause": "unreachable",
        "presence": None,
        "at": "2026-08-30T14:03:11.482913+00:00",
        "read_ref": None,
    }
    lane.transit = {"state": "none", "since": None}
    with serving(foreign_server(lane)) as url:
        yield lane, url


def open_now(tmp_path, url, user_agent):
    """A whole case, from the call arriving to `OPEN_NOW` being keyed."""
    from conftest import FakeClock

    clock = FakeClock()
    agent = agent_for(agent_config_for(tmp_path, lane_url=url), user_agent, clock=clock)
    user_agent.incoming(INTERCOM, account_user=INTERCOM_ACCOUNT)
    for _ in range(200):
        agent.poll()
        if any(verb == "dial" for verb, _ in user_agent.commands):
            break
        clock.advance(2.0)
    operator = [arg for verb, arg in user_agent.commands if verb == "dial"][0].split("-> ")[1]
    user_agent.established(operator)
    for _ in range(200):
        agent.poll()
        if user_agent.bridged_at is not None:
            break
        clock.advance(2.0)
    user_agent.dtmf(operator, "1")
    agent.poll()
    return agent


def test_a_whole_open_now_touches_the_lane_with_nothing_but_two_gets(tmp_path, recording):
    """What the LANE saw, asked from its side.

    A source sweep cannot see a client it does not recognise. This can: a real
    case is run against a server that records every request, including the ones
    it would refuse.
    """
    lane, url = recording
    agent = open_now(tmp_path, url, FakeUa())
    assert lane.requests, "the lane was not touched at all, so this asserts nothing"
    assert {method for method, _ in lane.requests} == {"GET"}
    assert {path for _, path in lane.requests} == {"/v1/lane/state", "/v1/lane/health"}
    # And the authorisation really was recorded, so the run above was the whole
    # dialogue and not a case that stopped early.
    assert any(
        event["authorisation"] == "open_now"
        for event in agent.events(0).to_dict()["events"]
    )


def test_the_recorder_would_see_a_planted_vend(tmp_path, recording):
    """The control for the test above, and it is not optional.

    A recorder that only ever recorded GETs would satisfy that assertion however
    the agent behaved. So a vend is POSTed directly, to the same lane, through
    the same handler, and it must appear.
    """
    lane, url = recording
    import urllib.error
    import urllib.request

    try:
        urllib.request.urlopen(
            urllib.request.Request(f"{url}/v1/lane/vend", data=b"{}", method="POST"), timeout=5
        )
    except urllib.error.HTTPError as exc:
        assert exc.code in (404, 405)
    assert ("POST", "/v1/lane/vend") in lane.requests


def test_our_own_lane_sees_nothing_but_gets_either(tmp_path):
    """The same question of OUR lane, which is the one the agent could cheat on.

    A foreign lane could not be reached in-process even if this package wanted
    to. Ours could -- `lane_controller` is installed in this environment -- so the
    only reading of "no opening authority" that means anything is one asserted
    against our own lane, served over a socket, recording what arrives.
    """
    server = our_server()
    with serving(server) as url:
        open_now(tmp_path, url, FakeUa())
    seen = server.requests.seen
    assert seen, "our lane was not touched at all"
    assert {method for method, _ in seen} == {"GET"}
    assert {path for _, path in seen} == {"/v1/lane/state", "/v1/lane/health"}


def test_a_user_agent_that_could_open_a_barrier_is_never_asked_to(tmp_path, recording):
    """THE PLANTED ACT. The barrier is capable of moving and does not move.

    Without this, "nothing opened" would be a claim about a search. Here it is a
    claim about an object that counts, and the control is the same object being
    told to move.
    """
    _lane, url = recording
    acting = ActingUa()
    open_now(tmp_path, url, acting)
    assert acting.barrier_moved == 0
    assert "vend" not in [verb for verb, _ in acting.commands]
    # THE CONTROL: it really can move, so the zero above is a measurement.
    acting.vend()
    assert acting.barrier_moved == 1


def test_the_agent_never_reads_a_route_that_could_carry_a_plate(tmp_path, recording):
    """`GET /v1/lane/events` carries a lane's own event detail. The agent does
    not read it, so there is no plate in this process to leak -- which is what
    makes the claim about the event surface a property rather than a filter."""
    lane, url = recording
    open_now(tmp_path, url, FakeUa())
    assert "/v1/lane/events" not in {path for _, path in lane.requests}
    # The control: that route exists on this lane and answers, so its absence
    # above is a statement about the agent.
    import urllib.request

    with urllib.request.urlopen(f"{url}/v1/lane/events?since=0", timeout=5) as response:
        assert response.status == 200
    assert "/v1/lane/events" in {path for _, path in lane.requests}
