"""OP-4-14. A ticket store that refuses a write is a code, not a crash mid-call.

`TicketStore.write` let a bare `OSError` out -- a full disk, a read-only
remount -- and it surfaced in the middle of the agent's loop with a driver at
the barrier. It is now `TicketStoreUnwritable`; the agent reports
`ticket_store_unwritable`, and a ticket whose issue could not be recorded is
not shown, not offered, and cannot move a barrier.
"""

from __future__ import annotations

import pytest

from gate_agent import tickets as tickets_module
from gate_agent.contract import AgentEventKind
from gate_agent.tickets import TicketStore
from serving import serving
from test_ticket_flow import ACT_TOKEN, a_lane, agent_on, arrive, events_of, our_server
from test_tickets import a_record


def _refusing(*_args, **_kwargs):
    raise OSError(28, "No space left on device")


def test_the_store_names_a_write_it_could_not_make(tmp_path, monkeypatch):
    from gate_agent.tickets import TicketStoreUnwritable

    store = TicketStore(tmp_path / "tickets")
    store.open()
    monkeypatch.setattr(tickets_module, "write_private", _refusing)
    with pytest.raises(TicketStoreUnwritable):
        store.write(a_record())


def test_an_arrival_with_an_unwritable_store_issues_nothing_and_says_so(tmp_path, monkeypatch):
    server = our_server(lane := a_lane(), act_token=ACT_TOKEN, arrive=False)
    with serving(server) as url:
        agent, _ua, screen = agent_on(tmp_path, url)
        monkeypatch.setattr(tickets_module, "write_private", _refusing)
        arrive(agent, lane)  # must not raise out of the loop
        assert events_of(agent, AgentEventKind.TICKET_ISSUED) == []
        assert "entry" not in agent._pending, "a ticket with no record was offered"
        states = {
            (entry["code"], entry["state"])
            for entry in agent.health().to_dict()["codes"]
        }
        assert ("ticket_store_unwritable", "active") in states, states
