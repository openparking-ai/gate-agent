"""OP-4-6. A vend that may have reached the lane is never `lane_unreachable`.

`lane_unreachable` says the lane was not asked. A timeout after the request
went out, a 5xx, or a 202 whose body could not be read are all a lane that MAY
have asked the barrier to open, and the record says `outcome_unknown` for them.
Only a request that never left -- a refused connection -- is `lane_unreachable`.
"""

from __future__ import annotations

import socket

import pytest

from gate_agent.act import LaneActClient, LaneOutcomeUnknown, LaneUnreachable
from test_http_client_errors import garbage_server
from test_whole_read_deadline import dripping_server


def _vend(url, timeout=1.0):
    return LaneActClient(url, "an-act-token", timeout=timeout).vend(
        authorised_by="display_code_confirmed",
        ticket_ref="t",
        decision_at="2026-11-01T00:00:00+00:00",
        idempotency_key="k",
    )


def test_a_202_still_arriving_at_the_deadline_is_outcome_unknown():
    with dripping_server(b"202 Accepted") as url:
        with pytest.raises(LaneOutcomeUnknown):
            _vend(url)


@pytest.mark.parametrize("reply", [
    b"NOT-HTTP garbage\r\n\r\n",
    b"HTTP/1.1 500 Internal Server Error\r\nContent-Length: 0\r\n\r\n",
    b"HTTP/1.1 202 Accepted\r\nContent-Length: 9\r\n\r\nnot json!",
])
def test_an_answer_after_the_request_was_sent_is_outcome_unknown(reply):
    with garbage_server(reply) as url:
        with pytest.raises(LaneOutcomeUnknown):
            _vend(url)


def test_the_control_a_refused_connection_is_lane_unreachable_and_not_unknown():
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()  # nothing listens there now
    with pytest.raises(LaneUnreachable) as raised:
        _vend(f"http://127.0.0.1:{port}")
    assert not isinstance(raised.value, LaneOutcomeUnknown)


def test_a_press_whose_vend_outcome_is_unknown_is_recorded_so(tmp_path, monkeypatch):
    from gate_agent import act as act_module
    from gate_agent.contract import AgentEventKind
    from gate_agent.tickets import VOIDED, TicketStore
    from serving import serving
    from test_ticket_flow import ACT_TOKEN, a_lane, agent_on, arrive, events_of, our_server, press

    server = our_server(lane := a_lane(), act_token=ACT_TOKEN, arrive=False)
    with serving(server) as url:
        agent, ua, _screen = agent_on(tmp_path, url)
        arrive(agent, lane)
        issued = events_of(agent, AgentEventKind.TICKET_ISSUED)[0]

        def timed_out_after_sending(self, **_kwargs):
            raise LaneOutcomeUnknown("the vend may have reached the lane: timed out")

        monkeypatch.setattr(act_module.LaneActClient, "vend", timed_out_after_sending)
        press(agent, ua)
        record = TicketStore(tmp_path / "tickets").read(issued["ticket_id"])
        assert (record.state, record.void_reason) == (VOIDED, "outcome_unknown"), record
