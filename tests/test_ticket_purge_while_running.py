"""OP-4-12. The ticket retention purge runs while the agent is up.

It ran in `TicketStore.open` only, so an agent that ran for months kept every
ticket record for months whatever `[tickets] retention_days` said.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from conftest import FakeClock
from gate_agent.agent import TICKET_PURGE_SECONDS
from gate_agent.tickets import TicketStore
from serving import serving
from test_ticket_flow import ACT_TOKEN, a_lane, agent_on, our_server
from test_tickets import a_record


def test_a_record_past_retention_is_purged_by_a_running_agent(tmp_path):
    clock = FakeClock()
    server = our_server(a_lane(), act_token=ACT_TOKEN, arrive=False)
    with serving(server) as url:
        agent, _ua, _screen = agent_on(tmp_path, url, clock=clock)
        store = TicketStore(tmp_path / "tickets")
        now = datetime.fromisoformat(agent._now())
        old = a_record(ticket_id="A" * 32, issued_at=(now - timedelta(days=400)).isoformat())
        young = a_record(ticket_id="B" * 32, issued_at=now.isoformat())
        store.write(old)
        store.write(young)
        agent.poll()
        assert store.read(old.ticket_id) is not None, "the control: not due yet"
        clock.advance(TICKET_PURGE_SECONDS + 1)
        agent.poll()
        assert store.read(old.ticket_id) is None, "a record past retention outlived it"
        assert store.read(young.ticket_id) is not None
