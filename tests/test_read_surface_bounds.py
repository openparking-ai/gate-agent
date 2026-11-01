"""OP-4-15. A read surface bounds each connection, and how many it serves.

`BaseHTTPRequestHandler` has no timeout by default, so a client that opened a
connection and sent nothing held a thread for ever, and `ThreadingHTTPServer`
gives every connection a thread. Each of the three surfaces now times an idle
connection out and serves at most `MAX_CONNECTIONS` at once.
"""

from __future__ import annotations

import socket
import time

import pytest

from gate_agent import agent_service, capture_service, service
from serving import serving

SURFACES = {
    "agent": lambda: agent_service.make_server(agent_service.AgentService(None), port=0),
    "monitor": lambda: service.make_server(service.MonitorService(None), port=0),
    "capture": lambda: capture_service.make_server(capture_service.CaptureService(None), port=0),
}

#: The cap as published. Read with a default so the test also runs, and fails,
#: against a build that has none.
CAP = getattr(service, "MAX_CONNECTIONS", 16)


def _closed_within(sock: socket.socket, seconds: float) -> bool:
    sock.settimeout(seconds)
    try:
        return sock.recv(1) == b""
    except TimeoutError:
        return False
    except OSError:
        return True


@pytest.fixture
def short_timeout(monkeypatch):
    for module in (agent_service, capture_service, service):
        monkeypatch.setattr(module, "HANDLER_TIMEOUT_SECONDS", 1, raising=False)


@pytest.mark.parametrize("surface", sorted(SURFACES))
def test_a_connection_that_sends_nothing_is_closed(surface, short_timeout):
    with serving(SURFACES[surface]()) as url:
        host, port = url.removeprefix("http://").split(":")
        with socket.create_connection((host, int(port))) as idle:
            started = time.monotonic()
            assert _closed_within(idle, 4.0), "an idle connection held its thread"
            assert time.monotonic() - started < 4.0


@pytest.mark.parametrize("surface", sorted(SURFACES))
def test_connections_past_the_cap_are_closed_at_once(surface, monkeypatch):
    for module in (agent_service, capture_service, service):
        monkeypatch.setattr(module, "HANDLER_TIMEOUT_SECONDS", 3, raising=False)
    with serving(SURFACES[surface]()) as url:
        host, port = url.removeprefix("http://").split(":")
        held = [socket.create_connection((host, int(port))) for _ in range(CAP)]
        time.sleep(0.3)  # every one of them has its thread now
        extra = [socket.create_connection((host, int(port))) for _ in range(4)]
        try:
            assert all(_closed_within(one, 1.0) for one in extra), (
                "a connection past the cap was given a thread"
            )
            assert not any(_closed_within(one, 0.05) for one in held[:2]), (
                "the control: connections within the cap are still open"
            )
        finally:
            for one in held + extra:
                one.close()
