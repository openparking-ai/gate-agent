"""The user agent's control socket, and four ways it used to go wrong quietly.

  * OP-PYTHON-058: nothing checked that baresip's control socket is reachable on
    loopback ONLY -- and that socket lists every intercom's dial secret, places
    calls, and takes the agent's own connection away. baresip's default listens
    on every interface.
  * OP-PYTHON-061: a user agent that RESTARTED during a case holds no calls, and
    a reopen that found none read as "nothing to do": the case stayed open,
    every new caller was refused `busy`, and a person was rung for a driver who
    had gone.
  * OP-PYTHON-062: one SIP account's re-registration cleared ANOTHER account's
    registration failure, so `sip_registration_lost` said ok while that door's
    calls never arrived.
  * OP-PYTHON-063: one call event whose handling raised threw away every event
    behind it in the batch -- measured, the person's keyed digit.

Driven here through the real adapter on a socket a test scripts, and through the
real agent on the fake user agent. The same four against a real baresip 4.11.0
are in `test_agent_sip.py`.
"""

from __future__ import annotations

import json
import socket
import sys

import pytest

from conftest import (
    INTERCOM_ACCOUNT,
    FakeClock,
    agent_config_for,
    agent_for,
    agent_raw_for,
)
from fake_ua import FakeUa
from gate_agent.config import AgentConfig, ConfigError
from gate_agent.contract import AgentEventKind, Authorisation
from gate_agent.ua import UaMisconfigured, UaRefused, UaUnreachable
from gate_agent.ua_baresip import TESTED_VERSIONS, BaresipUa, listening_off_loopback

INTERCOM = "sip:door1@10.0.0.9"

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


class Scripted:
    """A `ctrl_tcp` that answers each command as `world["answers"]` says, keeps
    every command it was sent, and can put an EVENT on the wire."""

    def __init__(self, world) -> None:
        self.world = world
        self.pending = b""
        self.dead = False
        self.sent: list[tuple[str, str]] = []

    def settimeout(self, _timeout) -> None:
        pass

    def sendall(self, payload: bytes) -> None:
        if self.dead:
            raise OSError("broken pipe")
        body = json.loads(payload.split(b":", 1)[1][:-1].decode())
        self.sent.append((body["command"], body["params"]))
        ok, data = self.world["answers"].get(body["command"], (True, ""))
        self._push({"response": True, "ok": ok, "data": data, "token": body["token"]})

    def event(self, **fields) -> None:
        self._push({"event": True, "class": "test", **fields})

    def _push(self, message: dict) -> None:
        raw = json.dumps(message).encode()
        self.pending += b"%d:%s," % (len(raw), raw)

    def recv(self, _size: int) -> bytes:
        if self.dead:
            return b""
        if not self.pending:
            raise BlockingIOError
        out, self.pending = self.pending, b""
        return out

    def close(self) -> None:
        self.dead = True


def an_adapter(**answers):
    """The real adapter on a scripted socket, with no question about exposure."""
    world = {
        "answers": {
            "about": (True, f"baresip {TESTED_VERSIONS[0]}"),
            "config": (True, CONFIG),
            "modules": (True, MODULES),
            **answers,
        },
        "sockets": [],
        "now": [0.0],
        "up": True,
    }

    def connect(_address, timeout=None):
        if not world["up"]:
            raise OSError("connection refused")
        sock = Scripted(world)
        world["sockets"].append(sock)
        return sock

    ua = BaresipUa(
        "127.0.0.1", 4444, operator_aor="sip:agent-operator@h", connect=connect,
        clock=lambda: world["now"][0], exposed=lambda _port: (),
    )
    return ua, world


# ---------------------------------------------------------------------------
# OP-PYTHON-058: the control socket answers on loopback, and nowhere else
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("host", ["10.0.0.20", "192.168.1.10", "0.0.0.0", "", "baresip.lan"])
def test_a_user_agent_host_off_loopback_is_refused(tmp_path, host):
    raw = agent_raw_for(tmp_path)
    raw["user_agent"]["host"] = host
    with pytest.raises(ConfigError) as raised:
        AgentConfig.from_dict(raw)
    assert "[user_agent].host" in str(raised.value)
    assert "loopback" in str(raised.value)


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1"])
def test_a_user_agent_host_on_loopback_is_accepted(tmp_path, host):
    raw = agent_raw_for(tmp_path)
    raw["user_agent"]["host"] = host
    assert AgentConfig.from_dict(raw).user_agent.host == host


def test_the_adapter_refuses_to_start_while_its_socket_answers_off_loopback():
    """And it asks BEFORE opening its own connection: `ctrl_tcp` takes one
    client, so a probe made after it would take the agent's away."""
    opened = []

    def connect(address, timeout=None):
        opened.append(address)
        raise AssertionError("the adapter opened its connection before refusing")

    exposed = BaresipUa(
        "127.0.0.1", 4444, operator_aor="sip:agent-operator@h", connect=connect,
        exposed=lambda port: ("0.0.0.0",) if port == 4444 else (),
    )
    with pytest.raises(UaMisconfigured) as raised:
        exposed.start()
    assert "ctrl_tcp_listen 127.0.0.1:4444" in str(raised.value)
    assert "0.0.0.0" in str(raised.value)
    assert opened == []

    # THE CONTROL: the same adapter, nothing found off loopback, starts.
    ua, world = an_adapter()
    ua.start()
    assert ua.version() == TESTED_VERSIONS[0]
    # And what a process is built with is the real question, not a stub.
    assert BaresipUa("127.0.0.1", 4444, operator_aor="x")._exposed is listening_off_loopback


def test_a_listener_on_every_interface_is_found_and_one_on_loopback_is_not():
    """Measured on THIS box, with real sockets: the kernel's own table where
    there is one (Linux), the box's own addresses otherwise."""
    for bind, exposed in (("0.0.0.0", True), ("127.0.0.1", False)):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind((bind, 0))
            sock.listen(8)
            found = listening_off_loopback(sock.getsockname()[1])
        assert bool(found) is exposed, (bind, found)


def test_the_kernels_table_is_read_by_address_and_state(tmp_path):
    """`/proc/net/tcp*`, the rows a Linux box writes. The literals are
    little-endian, which is every box this runs on."""
    assert sys.byteorder == "little"
    head = "  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt\n"
    (tmp_path / "tcp").write_text(
        head
        + "   0: 0100007F:115C 00000000:0000 0A 00000000:00000000 00:00000000 00000000\n"
        + "   1: 0A01A8C0:115C 00000000:0000 0A 00000000:00000000 00:00000000 00000000\n"
        # Connected, not listening: a client of the port, not a listener on it.
        + "   2: 0B01A8C0:115C 0C01A8C0:9C40 01 00000000:00000000 00:00000000 00000000\n"
        # Another port entirely.
        + "   3: 00000000:0016 00000000:0000 0A 00000000:00000000 00:00000000 00000000\n"
    )
    (tmp_path / "tcp6").write_text(
        head
        + "   0: 00000000000000000000000001000000:115C 00000000000000000000000000000000:0000"
        " 0A 00000000:00000000 00:00000000 00000000\n"
    )
    assert listening_off_loopback(4444, proc=tmp_path) == ("192.168.1.10",)
    # baresip's own default: every interface.
    (tmp_path / "tcp").write_text(
        head + "   0: 00000000:115C 00000000:0000 0A 00000000:00000000 00:00000000 00000000\n"
    )
    assert listening_off_loopback(4444, proc=tmp_path) == ("0.0.0.0",)
    (tmp_path / "tcp").write_text(head)
    (tmp_path / "tcp6").write_text(
        head
        + "   0: 00000000000000000000000000000000:115C 00000000000000000000000000000000:0000"
        " 0A 00000000:00000000 00:00000000 00000000\n"
    )
    assert listening_off_loopback(4444, proc=tmp_path) == ("::",)
    (tmp_path / "tcp6").write_text(head)
    assert listening_off_loopback(4444, proc=tmp_path) == ()


# ---------------------------------------------------------------------------
# OP-PYTHON-061: a reopen that finds no calls is still a reopen
# ---------------------------------------------------------------------------


def test_a_reopened_socket_says_so_even_when_it_holds_no_calls():
    ua, world = an_adapter(listcalls=(True, "\nUser-Agent: agent@h\n--- Active calls (0) ---\n"))
    ua.start()
    # Nothing was lost, so there is nothing to reopen.
    assert ua.reconnect() is None
    # The user agent restarts.
    world["sockets"][-1].dead = True
    with pytest.raises(UaUnreachable):
        ua.poll()
    world["now"][0] += 100.0
    found = ua.reconnect()
    assert found == (), f"a reopened socket holding no calls answered {found!r}"
    assert found is not None


class Restarting(FakeUa):
    """A user agent whose socket goes, and that comes back holding NOTHING --
    which is what a restarted baresip holds."""

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
        if not self.broken:
            return None
        if self.down:
            raise UaUnreachable("still down")
        self.broken = False
        return tuple(self.held)


def test_a_case_ends_when_the_user_agent_comes_back_holding_no_calls(tmp_path):
    clock = FakeClock()
    ua = Restarting()
    agent = agent_for(agent_config_for(tmp_path, standalone=True), ua, clock=clock)
    ua.incoming(INTERCOM, call_id="driver-1", account_user=INTERCOM_ACCOUNT)
    for _ in range(40):
        agent.poll()
        if any(verb == "dial" for verb, _ in ua.commands):
            break
        clock.advance(2.0)
    # The case is open and a person is being rung for this driver.
    assert agent.session is not None and agent.session.operator_call is not None

    ua.down = True
    agent.poll()
    ua.down = False                     # it is back, and it holds nothing
    agent.poll()
    assert agent.session is None, "the case outlived the user agent it was on"
    kinds = [event["kind"] for event in agent.events(0).to_dict()["events"]]
    assert AgentEventKind.CASE_NOT_SPOKEN.value in kinds

    # THE NEXT DRIVER IS ANSWERED, not refused for a case nobody is in.
    ua.incoming(INTERCOM, call_id="driver-2", account_user=INTERCOM_ACCOUNT)
    agent.poll()
    assert ("answer", "driver-2") in ua.commands
    assert AgentEventKind.CALL_REFUSED_BUSY.value not in [
        event["kind"] for event in agent.events(0).to_dict()["events"]
    ]


# ---------------------------------------------------------------------------
# OP-PYTHON-062: registration, account by account
# ---------------------------------------------------------------------------


def test_one_accounts_success_does_not_clear_anothers_failure():
    reginfo = (
        "\n--- User Agents (2) ---\n"
        "0 - sip:agent-door-a@10.0.0.30:5060  OK  Expires 60s\n"
        "1 - sip:agent-door-b@10.0.0.30:5060  OK  Expires 60s\n"
    )
    ua, world = an_adapter(reginfo=(True, reginfo))
    ua.start()
    assert ua.registered() is True
    wire = world["sockets"][-1]

    wire.event(type="REGISTER_FAIL", accountaor="sip:agent-door-a@10.0.0.30:5060",
               param="403 Forbidden")
    ua.poll()
    assert ua.registered() is False

    # ANOTHER DOOR'S REFRESH, which is what used to put the code back to ok.
    wire.event(type="REGISTER_OK", accountaor="sip:agent-door-b@10.0.0.30:5060",
               param="200 OK")
    ua.poll()
    assert ua.registered() is False, "door b's refresh cleared door a's failure"

    # And door a coming back is what clears it.
    wire.event(type="REGISTER_OK", accountaor="sip:agent-door-a@10.0.0.30:5060",
               param="200 OK")
    ua.poll()
    assert ua.registered() is True


# ---------------------------------------------------------------------------
# OP-PYTHON-063: one event that raises does not take the rest of the batch
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "refusal",
    [
        "call not found (id=deadbeef00000001)\n",   # while another call is up
        "command not found (callfind)\n",           # while none is
    ],
)
def test_hanging_up_a_call_the_user_agent_no_longer_holds_is_not_an_error(refusal):
    """Both refusals measured on baresip 4.11.0. And `hangup` is NOT sent: with
    nothing selected it ends whichever call is current, somebody else's."""
    ua, world = an_adapter(callfind=(False, refusal))
    ua.start()
    ua.hangup("deadbeef00000001")
    wire = world["sockets"][-1]
    assert ("callfind", "deadbeef00000001") in wire.sent
    assert ("hangup", "") not in wire.sent, "a hangup was sent with no call selected"

    # THE CONTROL: a call it does hold is selected and hung up.
    world["answers"]["callfind"] = (True, "setting current call: c0ffee\n")
    ua.hangup("c0ffee")
    assert wire.sent[-2:] == [("callfind", "c0ffee"), ("hangup", "")]


class Refusing(FakeUa):
    """A user agent that refuses to hang up a call it no longer holds, the way a
    real one may for any reason this build has not met."""

    gone: frozenset = frozenset({"cancelled-at-once"})

    def hangup(self, call_id: str) -> None:
        if call_id in self.gone:
            self.commands.append(("hangup", call_id))
            raise UaRefused(f"the user agent refused `callfind`: 'call not found (id={call_id})'")
        super().hangup(call_id)


def a_pass(agent) -> None:
    """One pass of the process's loop, which logs whatever a poll raises and goes
    on (`cli._agent_forever`)."""
    try:
        agent.poll()
    except Exception:  # noqa: BLE001
        pass


def test_an_event_that_raises_does_not_throw_away_the_persons_digit(tmp_path):
    """THE REPORT'S BATCH: a caller who cancelled at once, then the person's 4."""
    clock = FakeClock()
    ua = Refusing()
    agent = agent_for(agent_config_for(tmp_path, standalone=True), ua, clock=clock)
    ua.incoming(INTERCOM, call_id="driver-1", account_user=INTERCOM_ACCOUNT)
    for _ in range(200):
        a_pass(agent)
        if any(verb == "dial" for verb, _ in ua.commands):
            break
        clock.advance(2.0)
    operator = [arg for verb, arg in ua.commands if verb == "dial"][0].split("-> ")[1]
    ua.established(operator)
    for _ in range(200):
        a_pass(agent)
        if ua.bridged_at is not None:
            break
        clock.advance(2.0)
    assert ua.bridged_at is not None, "the person was never put through"

    # ONE BATCH: a second caller, already gone, and the person's digit.
    ua.incoming("sip:door9@10.0.0.9", call_id="cancelled-at-once",
                account_user=INTERCOM_ACCOUNT)
    ua.dtmf(operator, "4")
    a_pass(agent)
    a_pass(agent)
    received = [
        event["authorisation"]
        for event in agent.events(0).to_dict()["events"]
        if event["kind"] == AgentEventKind.AUTHORISATION_RECEIVED.value
    ]
    assert received == [Authorisation.HOLD.value], "the person's digit was lost"
    assert ("hangup", "cancelled-at-once") in ua.commands
