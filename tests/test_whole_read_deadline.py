"""OP-4-5. A lane's timeout bounds the WHOLE answer, not each socket read.

The agent runs one loop. A lane that answers its headers and then drips a byte
at a time never trips a per-read timeout, and every intercom and every other
lane waited behind it. The body is now read against a deadline, the way the
camera's snapshot already was.
"""

from __future__ import annotations

import socket
import threading
import time
from contextlib import contextmanager

import pytest

from gate_agent.act import LaneActClient, LaneUnreachable
from gate_agent.client import ReadOnlyClient, TargetUnreachable

#: Long enough that a read without a deadline is plainly slower than one with.
DRIP_BYTES = 40
DRIP_SECONDS = 0.1
TIMEOUT = 1.0


@contextmanager
def dripping_server(status: bytes = b"200 OK"):
    """Answers its headers at once, then a JSON body one byte per DRIP_SECONDS."""
    body = b"{}" + b" " * (DRIP_BYTES - 2)
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(4)
    stop = threading.Event()

    def serve():
        listener.settimeout(0.2)
        while not stop.is_set():
            try:
                conn, _ = listener.accept()
            except OSError:
                continue
            with conn:
                try:
                    conn.recv(65536)
                    conn.sendall(
                        b"HTTP/1.1 " + status + b"\r\nContent-Type: application/json\r\n"
                        b"Content-Length: " + str(len(body)).encode() + b"\r\n\r\n"
                    )
                    for byte in body:
                        if stop.is_set():
                            return
                        conn.sendall(bytes([byte]))
                        time.sleep(DRIP_SECONDS)
                except OSError:
                    pass

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{listener.getsockname()[1]}"
    finally:
        stop.set()
        thread.join()
        listener.close()


def test_a_lane_dripping_its_read_answer_is_abandoned_at_its_timeout():
    with dripping_server() as url:
        started = time.monotonic()
        with pytest.raises(TargetUnreachable):
            ReadOnlyClient(url, timeout=TIMEOUT).get("/v1/lane/health")
        assert time.monotonic() - started < TIMEOUT + 1.0


def test_a_lane_dripping_its_vend_answer_is_abandoned_at_its_timeout():
    with dripping_server(b"202 Accepted") as url:
        started = time.monotonic()
        with pytest.raises(LaneUnreachable):
            LaneActClient(url, "an-act-token", timeout=TIMEOUT).vend(
                authorised_by="display_code_confirmed",
                ticket_ref="t",
                decision_at="2026-11-01T00:00:00+00:00",
                idempotency_key="k",
            )
        assert time.monotonic() - started < TIMEOUT + 1.0


def test_the_control_a_lane_that_answers_in_time_is_read():
    """The same server with a timeout longer than the drip is read whole."""
    with dripping_server() as url:
        assert ReadOnlyClient(url, timeout=DRIP_BYTES * DRIP_SECONDS + 3).get("/x") == {}
