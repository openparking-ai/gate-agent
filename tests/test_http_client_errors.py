"""OP-4-4. What `http.client` raises is an unreachable target, not a crash.

urllib wraps socket errors in `URLError`, but a reply it cannot PARSE -- a
garbage status line, a body cut short -- raises `http.client.HTTPException`,
which is neither `URLError` nor `OSError`. It used to escape both clients: a
monitor left its last `ok` standing, the agent dropped a lane's events, and a
replay at startup stopped the agent from starting at all.
"""

from __future__ import annotations

import socket
import threading
from contextlib import contextmanager

import pytest

from gate_agent.act import LaneActClient, LaneUnreachable
from gate_agent.client import ReadOnlyClient, TargetUnreachable


@contextmanager
def garbage_server(reply: bytes = b"NOT-HTTP garbage\r\n\r\n"):
    """A TCP socket that answers every request with something that is not HTTP."""
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(16)
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
                    conn.sendall(reply)
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


#: A status line http.client cannot parse, and a body shorter than it declared.
REPLIES = {
    "bad_status_line": b"NOT-HTTP garbage\r\n\r\n",
    "incomplete_read": b"HTTP/1.1 200 OK\r\nContent-Length: 100\r\n\r\n{\"a\":",
}


@pytest.mark.parametrize("reply", sorted(REPLIES))
def test_the_read_client_reports_an_unparseable_reply_as_unreachable(reply):
    with garbage_server(REPLIES[reply]) as url:
        with pytest.raises(TargetUnreachable):
            ReadOnlyClient(url, timeout=2.0).get("/v1/lane/health")


@pytest.mark.parametrize("reply", sorted(REPLIES))
def test_the_act_client_reports_an_unparseable_reply_as_unreachable(reply):
    with garbage_server(REPLIES[reply]) as url:
        with pytest.raises(LaneUnreachable):
            LaneActClient(url, "an-act-token", timeout=2.0).vend(
                authorised_by="display_code_confirmed",
                ticket_ref="t",
                decision_at="2026-11-01T00:00:00+00:00",
                idempotency_key="k",
            )


def test_a_replay_that_raises_anything_is_outcome_unknown_and_the_agent_starts(
    tmp_path, monkeypatch
):
    """`_reconcile` settles each record on its own: one that cannot be settled
    is `outcome_unknown`, and the agent still starts."""
    from gate_agent import act as act_module
    from gate_agent.tickets import VOIDED, TicketStore
    from serving import serving
    from test_ticket_flow import (
        ACT_TOKEN,
        a_lane,
        a_ticket_confirmed_but_never_recorded,
        agent_on,
        our_server,
    )

    server = our_server(lane := a_lane(), act_token=ACT_TOKEN, arrive=False)
    with serving(server) as url:
        ticket_id = a_ticket_confirmed_but_never_recorded(tmp_path, url, lane, server)

        def exploding(self, **_kwargs):
            raise RuntimeError("something this build did not foresee")

        monkeypatch.setattr(act_module.LaneActClient, "vend", exploding)
        agent_on(tmp_path, url)  # THE RESTART: it must not raise
        settled = TicketStore(tmp_path / "tickets").read(ticket_id)
        assert (settled.state, settled.void_reason) == (VOIDED, "outcome_unknown"), settled
