"""A lane that stops answering holds up NOTHING but itself (OP-PYTHON-057).

Every lane used to be read inline, on the one thread that also plays the speech,
reads the keys and runs every dialogue timer. A lane process that accepts a
connection and never answers made each pass of that loop wait out its timeouts,
so a driver at ANOTHER door heard two sentences in ten seconds and every other
caller was refused `busy` for minutes. The relay had been moved off that thread
for exactly this reason; the lanes had not.

These are the tests of the PROCESS'S OWN way of reading -- `agent.on_a_thread`,
the default -- which the rest of the suite does not use: every other test drives
`poll()` by hand and has its reads made `inline` (`conftest.agent_for`). So the
lane here is real sockets and real threads, and the wedged one is a socket that
completes the handshake and then says nothing, which is the shape the review
measured.
"""

from __future__ import annotations

import contextlib
import socket
import time
from dataclasses import replace

from conftest import (
    INTERCOM_ACCOUNT,
    OTHER_ACCOUNT,
    FakeClock,
    FakeUtc,
    agent_config_for,
    agent_for,
    wav,
)
from fake_ua import FakeUa
from foreign_lane import ForeignLane, decided_at
from foreign_lane.lane import make_server
from gate_agent.agent import Agent, State
from gate_agent.config import Intercom, Target, TicketSettings
from gate_agent.contract import AgentCase, AgentEventKind, TargetKind
from serving import serving
from test_ticket_flow import SIGNING_KEY, FakeScreen, events_of

DOOR = "sip:door1@10.0.0.9"
OTHER_DOOR = "sip:door2@10.0.0.9"

#: The wedged lane's timeout. Short, so the suite does not wait out ten
#: seconds, and long enough that a pass held up by it cannot pass for one that
#: was not: every pass below is required to take a small fraction of it.
TIMEOUT = 3.0

#: The most one pass of the loop may take here. The loop passes five times a
#: second in the process (`cli.AGENT_PASS_SECONDS`); a pass that waited on the
#: wedged lane takes at least `TIMEOUT`.
A_PASS = 1.0


@contextlib.contextmanager
def a_lane_that_never_answers():
    """A socket that LISTENS and is never read: the kernel completes every
    connection, and no byte ever comes back. Closing it resets them all, which
    is how a test ends the wait instead of sitting out the timeout."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    sock.listen(64)
    try:
        yield f"http://127.0.0.1:{sock.getsockname()[1]}"
    finally:
        sock.close()


def two_doors(tmp_path, url, *, start_read=None, lane_timeout=TIMEOUT):
    """Door 1 at a lane with a screen and a ticket key; door 2 standalone."""
    screen = FakeScreen()
    base = agent_config_for(tmp_path, lane_url=url)
    other = Intercom(
        sip_uri=OTHER_DOOR,
        lane=None,
        name_audio=wav(tmp_path / "site" / "door2.wav"),
        account_user=OTHER_ACCOUNT,
    )
    config = replace(
        base,
        lanes=(
            Target(
                name="entry",
                kind=TargetKind.LANE,
                url=url,
                poll_seconds=0.0,
                timeout_seconds=lane_timeout,
            ),
        ),
        intercoms=(replace(base.intercoms[0], lane="entry", display="front"), other),
        displays={"front": screen},
        tickets=TicketSettings(signing_key=SIGNING_KEY, directory=tmp_path / "tickets"),
        driver_languages=("en",),
    )
    ua = FakeUa()
    clock = FakeClock()
    if start_read is not None:
        return agent_for(config, ua, clock=clock, start_read=start_read), ua, clock, screen
    # THE PROCESS'S OWN AGENT: built with nothing said about how a lane is
    # read, exactly as `cli` builds it, so what is measured is the default.
    ua.declared_accounts = tuple(intercom.account_user for intercom in config.intercoms)
    agent = Agent(config, ua, clock=clock, now=FakeUtc())
    agent.start()
    return agent, ua, clock, screen


def timed_pass(agent) -> float:
    started = time.monotonic()
    agent.poll()
    return time.monotonic() - started


def until(agent, condition, seconds: float = 10.0) -> None:
    """Pass the loop, for real time, until `condition()` holds."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        agent.poll()
        if condition():
            return
        time.sleep(0.02)
    raise AssertionError("the agent never got there")


def lane_unavailable(agent) -> list[str]:
    return [
        one["state"] for one in agent.health().to_dict()["codes"]
        if one["code"] == "lane_unavailable"
    ]


def driver_heard(ua) -> list[str]:
    return [str(path).rsplit("/", 1)[-1] for leg, path in ua.played if leg == "driver"]


def test_a_lane_that_never_answers_holds_up_no_other_door(tmp_path):
    """THE REPORT'S CASE: a standalone door's driver, and a wedged lane beside it.

    Before, the pass that answered door 2 then read the wedged lane -- its fee
    and its events, a timeout each -- before it played the driver anything.
    """
    with a_lane_that_never_answers() as url:
        agent, ua, _clock, _screen = two_doors(tmp_path, url)
        ua.incoming(OTHER_DOOR, call_id="driver-2", account_user=OTHER_ACCOUNT)
        took = timed_pass(agent)
        assert took < A_PASS, f"one pass took {took:.1f}s behind a lane that never answers"
        assert agent.session is not None and agent.session.case is AgentCase.STANDALONE
        # THE DRIVER IS SPOKEN TO ON THE NEXT PASS, the one that hears the
        # call's media come up -- which is the whole point.
        took = timed_pass(agent)
        assert took < A_PASS, f"the next pass took {took:.1f}s"
        assert driver_heard(ua) == ["case.standalone.wav"], ua.played
        # And the lane's round is still out, and is not started a second time.
        assert "entry" in agent._rounds
        for _ in range(5):
            assert timed_pass(agent) < A_PASS
        assert len(agent._rounds) == 1
    # The socket is gone, the round comes back refused, and it is applied.
    until(agent, lambda: lane_unavailable(agent) == ["active"])


def test_a_call_at_a_lane_that_never_answers_holds_up_nothing_either(tmp_path):
    """The read a call is answered on is made off the loop too, and the case is
    derived from it when it is back -- so nothing is said meanwhile, exactly as
    while that read used to block the whole loop."""
    with a_lane_that_never_answers() as url:
        agent, ua, _clock, _screen = two_doors(tmp_path, url)
        ua.incoming(DOOR, call_id="driver-1", account_user=INTERCOM_ACCOUNT)
        took = timed_pass(agent)
        assert took < A_PASS, f"one pass took {took:.1f}s on a call at a lane that never answers"
        assert ("answer", "driver-1") in ua.commands
        assert agent.session.state is State.READING_LANE
        assert agent.session.case is None and driver_heard(ua) == []
        # A second caller meanwhile is refused at once, as during any case.
        ua.incoming(OTHER_DOOR, call_id="driver-2", account_user=OTHER_ACCOUNT)
        assert timed_pass(agent) < A_PASS
        assert ("hangup", "driver-2") in ua.commands
    # The lane is gone: the read fails, and the driver is told that.
    until(agent, lambda: agent.session is not None and agent.session.case is not None)
    assert agent.session.case is AgentCase.LANE_UNAVAILABLE
    until(agent, lambda: "case.lane_unavailable.wav" in driver_heard(ua))


def test_a_lane_read_on_its_own_thread_still_puts_the_ticket_up(tmp_path):
    """The production path, end to end: a decision, read off the loop, applied
    on a later pass -- a ticket on the screen, as before."""
    lane = ForeignLane()
    lane.window = 64
    with serving(make_server(lane)) as url:
        agent, _ua, _clock, screen = two_doors(tmp_path, url, lane_timeout=5.0)
        until(agent, lambda: agent._cursors["entry"] is not None)
        lane.decision = {**lane.decision, "outcome": "fallback", "reason": "no_plate_read",
                         "fallback": "no_plate_read", "presence": True, "at": decided_at()}
        lane.record("decision", decided_at(), {"outcome": "fallback"})
        until(agent, lambda: bool(events_of(agent, AgentEventKind.TICKET_ISSUED)))
        assert agent._pending["entry"].decision_at == lane.decision["at"]
        assert screen.frames


class Held:
    """A `start_read` that runs each read only when the test says, so the one
    order that matters can be produced on purpose rather than hoped for."""

    def __init__(self) -> None:
        self.waiting: dict[str, list] = {}

    def __call__(self, name, work):
        self.waiting.setdefault(name, []).append(work)

    def run(self, name: str) -> None:
        self.waiting[name].pop(0)()


def test_a_round_older_than_the_calls_own_read_is_not_applied(tmp_path):
    """The order the loop used to give for free, kept on purpose.

    A round reads decision D1. Before the loop applies it, the lane decides D2
    for the next car and its driver presses. The press's own read says D2 and
    puts up D2's code. Applied after that read had begun, the old round would
    have put up a code for D1 instead -- the code the driver is then told to
    photograph, for a decision the lane no longer holds.
    """
    lane = ForeignLane()
    lane.window = 64
    held = Held()
    with serving(make_server(lane)) as url:
        agent, ua, _clock, _screen = two_doors(tmp_path, url, start_read=held)
        agent.poll()
        held.run("lane-round-entry")
        agent.poll()                       # the first read: the cursor, and nothing else
        assert agent._cursors["entry"] is not None
        first = {"outcome": "fallback", "reason": "no_plate_read", "fallback": "no_plate_read",
                 "cause": None, "presence": True, "read_ref": None, "completed": False}
        lane.decision = {**first, "at": decided_at(5.0)}           # D1
        lane.record("decision", decided_at(5.0), {"outcome": "fallback"})
        held.run("lane-round-entry")                               # that round reads D1
        lane.decision = {**first, "at": decided_at(0.0)}           # D2, the next car
        lane.record("decision", decided_at(0.0), {"outcome": "fallback"})
        d2 = lane.decision["at"]

        ua.incoming(DOOR, call_id="driver-1", account_user=INTERCOM_ACCOUNT)
        agent.poll()                       # the call's read starts; the D1 round is back
        assert events_of(agent, AgentEventKind.TICKET_ISSUED) == [], (
            "a round from before the call's read put a ticket up"
        )
        held.run("lane-read-entry")                                # the call's read: D2
        agent.poll()
        issued = events_of(agent, AgentEventKind.TICKET_ISSUED)
        assert len(issued) == 1
        assert agent._pending["entry"].decision_at == d2
        assert events_of(agent, AgentEventKind.TICKET_ON_SCREEN)[0]["ticket_id"] == (
            issued[0]["ticket_id"]
        )
        # And the lane is simply read again, once, behind it.
        assert len(held.waiting["lane-round-entry"]) == 1
