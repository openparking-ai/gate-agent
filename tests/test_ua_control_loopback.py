"""OP-4-11. The user agent's control socket listens on loopback, or the agent
does not start.

That socket can place a call, bridge two of them and play audio at whoever is
on the line, and baresip's default for `ctrl_tcp_listen` is every interface.
The agent now refuses a control host that is not loopback, a reported
`ctrl_tcp_listen` that is not loopback, and a control port that ANSWERS on one
of this box's other addresses.
"""

from __future__ import annotations

import json
import socket
import sys
from contextlib import contextmanager

import pytest

from gate_agent.ua import UaMisconfigured
from gate_agent.ua_baresip import TESTED_VERSIONS, BaresipUa

CONFIG = (
    "\naudio_player\taufile,/tmp/x.wav\naudio_source\taufile,/tmp/y.wav\n"
    "call_hold_other_calls\tno\n"
)
MODULES = (
    "\n--- Modules (4) ---\n"
    "        ctrl_tcp type=application  ref=1\n"
    "        mixausrc type=filter       ref=1\n"
    "        mixminus type=filter       ref=1\n"
    "       debug_cmd type=application  ref=1\n\n"
)


class Answering:
    """A baresip that answers `about`, `config` and `modules` and nothing else."""

    def __init__(self, config):
        self.config = config
        self.pending = b""

    def settimeout(self, _t):
        pass

    def sendall(self, payload):
        body = json.loads(payload.split(b":", 1)[1][:-1].decode())
        data = {"about": f"baresip {TESTED_VERSIONS[0]}", "config": self.config,
                "modules": MODULES}.get(body["command"], "")
        answer = json.dumps({"response": True, "ok": True, "data": data,
                             "token": body["token"]}).encode()
        self.pending += b"%d:%s," % (len(answer), answer)

    def recv(self, _size):
        out, self.pending = self.pending, b""
        return out

    def close(self):
        pass


def _routable_address() -> str | None:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp:
            udp.connect(("192.0.2.1", 9))
            address = udp.getsockname()[0]
    except OSError:
        return None
    return None if address.startswith("127.") or address == "0.0.0.0" else address


def ua(host="127.0.0.1", port=1, config=CONFIG, **kwargs):
    return BaresipUa(host, port, operator_aor="sip:agent-operator@h",
                     connect=lambda _a, timeout=None: Answering(config), **kwargs)


@contextmanager
def listening(host):
    listener = socket.socket()
    listener.bind((host, 0))
    listener.listen(4)
    try:
        yield listener.getsockname()[1]
    finally:
        listener.close()


def test_the_control_a_loopback_control_socket_starts():
    ua().start()


def test_a_ctrl_tcp_listen_off_loopback_is_refused_by_name():
    with pytest.raises(UaMisconfigured, match="ctrl_tcp_listen"):
        ua(config=CONFIG + "ctrl_tcp_listen\t0.0.0.0:4444\n").start()
    # The control: the loopback setting starts.
    ua(config=CONFIG + "ctrl_tcp_listen\t127.0.0.1:4444\n").start()


def test_a_control_host_off_loopback_is_refused():
    with pytest.raises(UaMisconfigured, match="not loopback"):
        ua(host="10.0.0.5").start()


def test_a_control_port_that_answers_on_another_address_is_refused():
    """A real listener on every interface, found by trying the port."""
    if _routable_address() is None:
        pytest.skip("this box has no non-loopback address to try")
    with listening("0.0.0.0") as port:
        with pytest.raises(UaMisconfigured, match="answers on"):
            ua(port=port).start()
    # The control: the same, bound to loopback only, starts.
    with listening("127.0.0.1") as port:
        ua(port=port).start()


def test_a_box_whose_addresses_cannot_be_found_is_refused_not_assumed_loopback():
    """Review of #24: with nothing to try, the check used to pass silently.

    A gate box whose hostname maps to 127.0.1.1 and that has no default route
    finds no address by the ways it has, and still has the LAN address an
    intercom reaches it on. Nothing found must not read as "loopback only".
    """
    with listening("0.0.0.0") as port:
        with pytest.raises(UaMisconfigured, match="could not be found"):
            ua(port=port, local_addresses=lambda: ()).start()
    # The control: baresip REPORTING a loopback ctrl_tcp_listen is enough.
    ua(config=CONFIG + "ctrl_tcp_listen\t127.0.0.1:4444\n", local_addresses=lambda: ()).start()


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="SIOCGIFADDR is Linux's")
def test_interfaces_are_read_without_a_route_or_a_hostname():
    """The Linux path that needs neither: it sees `lo` on any box, by name."""
    from gate_agent.ua_baresip import _interface_addresses

    assert "127.0.0.1" in _interface_addresses()
