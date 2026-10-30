"""Every call the agent handles is a RECORD: who called, who was rung, who answered.

The intercom half of the account a platform builds when a car leaves unpaid.
`GET /v1/agent/events` is a window that turns over and is gone at a restart;
this is the same story kept on the disk, one record per call, read through
`GET /v1/agent/calls`. Proven here, each with its control:

  * an answered call's record names the lane the DIALLED ACCOUNT gives, when the
    person was rung, who answered and when, what they keyed, and how it ended;
  * a call refused unanswered -- busy, or at an account nobody declared -- is a
    record too, with no lane, because there was no door to name;
  * the record SURVIVES A RESTART, and a call the last run was on is closed as
    `interrupted_by_restart` rather than left looking live;
  * the route needs the credential when one is configured, and refuses a
    query it does not recognise.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

import pytest

from conftest import (
    INTERCOM_ACCOUNT,
    FakeClock,
    FakeUtc,
    agent_config_for,
    agent_for,
)
from fake_ua import FakeUa
from foreign_lane import ForeignLane
from foreign_lane import make_server as foreign_server
from gate_agent.agent_service import AgentService, make_server
from gate_agent.contract import AgentCode
from gate_agent.records import RecordQuery
from serving import serving
from test_agent_cases import decided_at

INTERCOM = "sip:door1@10.0.0.9"
PERSON = "sip:duty@10.0.0.5"


@pytest.fixture
def lane():
    served = ForeignLane()
    served.decision = {
        "outcome": "fallback",
        "reason": "engine_unreachable",
        "fallback": "engine_unreachable",
        "cause": "unreachable",
        "presence": None,
        "at": decided_at(),
        "read_ref": None,
    }
    served.transit = {"state": "none", "since": None}
    with serving(foreign_server(served)) as url:
        yield served, url


def pump(agent, clock, utc, until, step: float = 2.0, limit: int = 200):
    for _ in range(limit):
        agent.poll()
        if until():
            return
        clock.advance(step)
        utc.advance(step)
    raise AssertionError("the dialogue never reached the state this test waits for")


def calls(agent, **query) -> list[dict]:
    return agent.calls(RecordQuery(**query)).to_dict()["calls"]


def to_the_person(tmp_path, url, **kwargs):
    """Answer, speak the case, ring the person, have them pick up, and bridge."""
    clock, utc, ua = FakeClock(), FakeUtc(), FakeUa()
    agent = agent_for(agent_config_for(tmp_path, lane_url=url, **kwargs), ua,
                      clock=clock, now=utc)
    ua.incoming(INTERCOM, account_user=INTERCOM_ACCOUNT)
    pump(agent, clock, utc, lambda: any(verb == "dial" for verb, _ in ua.commands))
    operator = [arg for verb, arg in ua.commands if verb == "dial"][0].split("-> ")[1]
    return agent, ua, clock, utc, operator


def test_a_call_says_who_was_rung_who_answered_and_what_they_keyed(tmp_path, lane):
    _served, url = lane
    agent, ua, clock, utc, operator = to_the_person(tmp_path, url)
    rang_at = utc()
    clock.advance(5)
    utc.advance(5)
    ua.established(operator, peer_uri=f'"Duty desk" <{PERSON}:5060>;tag=a1')
    pump(agent, clock, utc, lambda: ua.bridged_at is not None)
    answered_at = calls(agent)[0]["person_answered_at"]
    ua.dtmf(operator, "6")
    agent.poll()
    for digit in "5550100#":
        ua.dtmf(operator, digit)
    agent.poll()
    pump(agent, clock, utc, lambda: agent.session is None)

    [record] = calls(agent, lane="entry")
    assert record["disposition"] == "answered"
    assert record["intercom"] == INTERCOM
    assert record["lane"] == "entry", "the lane the dialled account's intercom declares"
    assert record["caller_stated_identity"] == INTERCOM
    assert record["case"] == "identification_unavailable"
    assert record["case_spoken_at"] is not None
    assert record["person_called"] == PERSON
    assert record["rang_person_at"] == rang_at
    assert answered_at is not None and answered_at > rang_at
    assert record["person_answered_as"] == PERSON
    assert record["authorisation"] == "call_back"
    assert record["keyed"] == "5550100"
    assert record["authorisation_at"] >= answered_at
    assert record["outcome"] == "authorisation_received"
    assert record["ended_at"] is not None
    assert record["recording"] is None
    kinds = [event["kind"] for event in record["events"]]
    assert kinds[0] == "call_answered" and kinds[-1] == "call_ended"
    assert {"case_spoken", "human_called", "authorisation_received"} <= set(kinds)
    # The timeline IS the events route's: nothing on it is lost when that
    # window turns over.
    served = [event for event in agent.events(0).to_dict()["events"]
              if event["kind"] in kinds]
    assert [event["at"] for event in record["events"]] == [event["at"] for event in served]


def test_a_person_who_does_not_answer_is_the_outcome(tmp_path, lane):
    _served, url = lane
    agent, _ua, clock, utc, _operator = to_the_person(tmp_path, url, no_answer_seconds=30.0)
    pump(agent, clock, utc, lambda: agent.session is None)
    [record] = calls(agent)
    assert record["outcome"] == "human_unreachable"
    assert record["rang_person_at"] is not None
    assert record["person_answered_at"] is None and record["person_answered_as"] is None


def test_a_driver_who_hangs_up_first_is_the_outcome(tmp_path, lane):
    _served, url = lane
    agent, ua, clock, utc, _operator = to_the_person(tmp_path, url)
    ua.closed("call-1")
    agent.poll()
    [record] = calls(agent)
    assert record["outcome"] == "driver_hung_up"
    assert record["ended_at"] is not None


def test_a_call_turned_away_is_a_record_with_no_lane(tmp_path, lane):
    """Busy, and undeclared: two refusals, two records, and the live call untouched."""
    _served, url = lane
    agent, ua, clock, utc, _operator = to_the_person(tmp_path, url)
    ua.incoming("sip:second@10.0.0.33", call_id="call-2", account_user=INTERCOM_ACCOUNT)
    ua.refused_unknown_account("sip:stranger@10.9.9.9")
    agent.poll()

    refused = {one["disposition"]: one for one in calls(agent)
               if one["disposition"] != "answered"}
    assert set(refused) == {"refused_busy", "refused_undeclared"}
    for disposition, record in refused.items():
        assert record["lane"] is None
        assert record["outcome"] == disposition
        assert record["started_at"] == record["ended_at"]
    assert refused["refused_busy"]["caller_stated_identity"] == "sip:second@10.0.0.33"
    assert refused["refused_undeclared"]["caller_stated_identity"] == "sip:stranger@10.9.9.9"
    # Neither refusal landed on the live call's record.
    live = next(one for one in calls(agent) if one["disposition"] == "answered")
    assert not {"call_refused_busy", "call_from_undeclared_intercom"} & {
        event["kind"] for event in live["events"]
    }
    # And the lane filter carries them: an exception at `entry` sees that
    # somebody was turned away while the agent was busy.
    assert {one["disposition"] for one in calls(agent, lane="entry")} >= {
        "answered", "refused_busy",
    }


def test_call_records_survive_a_restart_and_a_live_call_is_closed_as_interrupted(
    tmp_path, lane
):
    _served, url = lane
    agent, _ua, _clock, utc, _operator = to_the_person(tmp_path, url)
    [live] = calls(agent)
    assert live["outcome"] is None and live["ended_at"] is None

    # A NEW PROCESS on the same directory: the old one never ended the call.
    config = agent_config_for(tmp_path, lane_url=url)
    restarted = agent_for(config, FakeUa(), clock=FakeClock(), now=utc)
    [closed] = calls(restarted)
    assert closed["call_id"] == live["call_id"]
    assert closed["outcome"] == "interrupted_by_restart"
    assert closed["ended_at"] is None, "nobody knows when it ended, so nothing says"
    assert closed["revision"] > live["revision"]
    assert closed["events"] == live["events"]
    # Found ONCE: a third start does not close it again.
    again = agent_for(config, FakeUa(), clock=FakeClock(), now=utc)
    assert calls(again)[0]["revision"] == closed["revision"]


def test_a_record_store_that_refuses_is_a_code_and_the_call_goes_on(tmp_path, lane):
    _served, url = lane
    clock, utc, ua = FakeClock(), FakeUtc(), FakeUa()
    config = agent_config_for(tmp_path, lane_url=url)
    agent = agent_for(config, ua, clock=clock, now=utc)
    config.records.directory.chmod(0o500)
    try:
        ua.incoming(INTERCOM, account_user=INTERCOM_ACCOUNT)
        pump(agent, clock, utc, lambda: any(verb == "dial" for verb, _ in ua.commands))
    finally:
        config.records.directory.chmod(0o700)
    assert ("answer", "call-1") in ua.commands, "a refused record refused a driver"
    states = {(one["code"], one["state"]) for one in agent.health().to_dict()["codes"]}
    assert (AgentCode.RECORD_STORE_UNWRITABLE.value, "active") in states


def _get(base, path, token=None):
    request = urllib.request.Request(f"{base}{path}")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"{}")


def test_the_calls_route_needs_the_credential_and_refuses_an_unknown_query(tmp_path, lane):
    _served, url = lane
    agent, _ua, _clock, _utc, _operator = to_the_person(tmp_path, url)
    with serving(make_server(AgentService(agent), port=0, token="s3cret")) as base:
        assert _get(base, "/v1/agent/calls")[0] == 401
        assert _get(base, "/v1/agent/calls", token="wrong")[0] == 401
        status, body = _get(base, "/v1/agent/calls?lane=entry", token="s3cret")
        assert status == 200 and body["calls"]
        assert body["calls"][0]["lane"] == "entry"
        for query in ("lane_id=entry", "since=-1", "since=../x", "limit=9999",
                      "from=2026-10-30T21:00:00"):
            status, body = _get(base, f"/v1/agent/calls?{query}", token="s3cret")
            assert status == 400, query


def test_off_loopback_the_agent_will_not_start_without_a_credential_file(tmp_path, capsys):
    from gate_agent.cli import main

    assert main(["agent", "--config", str(tmp_path / "absent.toml"),
                 "--host", "0.0.0.0"]) == 2
    assert "no token" in capsys.readouterr().err
