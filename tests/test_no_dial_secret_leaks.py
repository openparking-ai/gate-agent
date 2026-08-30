"""A dial secret appears in nothing this agent publishes, logs or renders.

The secret is the user part of an account, and that account is the whole of what
identifies an intercom. It is worth exactly what a password is worth, so the
question this file asks is the same one asked of every other credential in this
package: **can it be found anywhere it can travel?**

Every sweep here is a claim about a SEARCH, so every one of them carries the
positive control the rule requires -- the same search, against a place the secret
IS, proving it fires.
"""

from __future__ import annotations

import io
import json
import logging

import pytest

from conftest import DIAL_SECRET, INTERCOM_ACCOUNT, agent_config_for, agent_for
from fake_ua import FakeUa
from gate_agent.contract import AgentEventKind


def _agent(tmp_path, **kwargs):
    return agent_for(agent_config_for(tmp_path, standalone=True, **kwargs), FakeUa())


def test_the_planted_secret_is_findable_at_all(tmp_path):
    """THE POSITIVE CONTROL for every sweep below, run first.

    Without this, "the secret is not on the read surface" would be satisfied by
    a test whose secret was never in the configuration to begin with.
    """
    agent = _agent(tmp_path)
    assert DIAL_SECRET in agent.config.intercoms[0].account_user
    assert INTERCOM_ACCOUNT in json.dumps(
        [one.account_user for one in agent.config.intercoms]
    )


def test_no_read_surface_carries_it(tmp_path):
    """The three routes, serialised, from an agent that has taken a whole call."""
    agent = _agent(tmp_path)
    ua = agent.ua
    ua.incoming("sip:door1@10.0.0.9", account_user=INTERCOM_ACCOUNT)
    agent.poll()
    ua.incoming("sip:someone@10.9.9.9", call_id="x-1", account_user="agent-not-declared")
    agent.poll()
    ua.refused_unknown_account("sip:scanner@10.9.9.9")
    agent.poll()

    served = {
        "/v1/agent": json.dumps(agent.describe().to_dict()),
        "/v1/agent/health": json.dumps(agent.health().to_dict()),
        "/v1/agent/events": json.dumps(agent.events(0).to_dict()),
    }
    # The run is not vacuous: a call really was answered at that account, and
    # two more really were refused -- one as busy, because the first is still
    # live, and one by the user agent itself.
    kinds = [one["kind"] for one in json.loads(served["/v1/agent/events"])["events"]]
    assert AgentEventKind.CALL_ANSWERED.value in kinds
    assert AgentEventKind.CALL_REFUSED_BUSY.value in kinds
    assert AgentEventKind.CALL_FROM_UNDECLARED_INTERCOM.value in kinds

    for route, body in served.items():
        assert DIAL_SECRET not in body, f"{route} published the dial secret"
        assert INTERCOM_ACCOUNT not in body, f"{route} published the account"


def test_nothing_it_logs_carries_it(tmp_path, caplog):
    """Every log line a whole call and both refusals produce."""
    with caplog.at_level(logging.DEBUG, logger="gate_agent"):
        agent = _agent(tmp_path)
        ua = agent.ua
        ua.incoming("sip:door1@10.0.0.9", account_user=INTERCOM_ACCOUNT)
        agent.poll()
        ua.incoming("sip:x@10.9.9.9", call_id="x-1", account_user="agent-not-declared")
        agent.poll()
        ua.refused_unknown_account("sip:scanner@10.9.9.9")
        agent.poll()
    text = "\n".join(record.getMessage() for record in caplog.records)
    assert DIAL_SECRET not in text

    # THE POSITIVE CONTROL: the same capture, with the secret deliberately
    # logged, is found -- so the sweep above is looking somewhere real.
    with caplog.at_level(logging.DEBUG, logger="gate_agent"):
        logging.getLogger("gate_agent").error("planted %s", DIAL_SECRET)
    assert DIAL_SECRET in "\n".join(r.getMessage() for r in caplog.records)


def test_no_traceback_or_repr_of_the_configuration_carries_it(tmp_path):
    """`Intercom` has a `__repr__` of its own, and this is why.

    The generated one puts the account -- which is the secret -- into every log
    line, every traceback and every test failure that touches a configuration,
    and none of those are places anybody chose to publish a credential.
    """
    config = agent_config_for(tmp_path, standalone=True)
    assert DIAL_SECRET not in repr(config.intercoms[0])
    assert DIAL_SECRET not in repr(config)
    assert DIAL_SECRET not in str(config.intercoms)

    # THE POSITIVE CONTROL: the field really does hold it, so the repr is
    # hiding something rather than there being nothing to hide.
    assert DIAL_SECRET in config.intercoms[0].account_user

    # And a raised error that carries the configuration does not carry it either.
    buffer = io.StringIO()
    try:
        raise ValueError(f"something went wrong: {config.intercoms}")
    except ValueError as exc:
        buffer.write(str(exc))
    assert DIAL_SECRET not in buffer.getvalue()


def test_the_startup_refusal_names_the_intercom_and_not_the_account(tmp_path):
    """The one message that is ABOUT the account must still not print it."""
    from gate_agent.agent import Agent
    from gate_agent.ua import UaMisconfigured

    config = agent_config_for(tmp_path, standalone=True)
    with pytest.raises(UaMisconfigured) as raised:
        Agent(config, FakeUa(held_accounts=("agent-operator",))).start()
    assert "sip:door1@10.0.0.9" in str(raised.value)
    assert DIAL_SECRET not in str(raised.value)
