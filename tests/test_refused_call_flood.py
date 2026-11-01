"""OP-4-2. An INVITE flood must not erase the record of a call that was answered.

Anybody who can reach the SIP port can make a refused call: no account, no
answer, nothing to prove. Each one writes a call record and an event. Both used
to share one budget with answered calls, oldest first, so enough refusals
pushed the real calls -- and their audit trail -- out of the store and out of
the events window. Refusals now hold a bounded share of each, and a refusal
only ever evicts another refusal. The caller's own `From` is capped as well.
"""

from __future__ import annotations

from dataclasses import replace

from conftest import (
    INTERCOM_ACCOUNT,
    FakeClock,
    FakeUtc,
    agent_config_for,
    agent_for,
    record_settings,
)
from fake_ua import FakeUa
from gate_agent.agent import MAX_CALLER_IDENTITY_CHARS
from gate_agent.records import EXPENDABLE_SHARE_DIVISOR, RecordQuery, RecordStore
from test_call_records import INTERCOM, lane  # noqa: F401


def _calls(agent) -> list[dict]:
    found, after = [], 0
    while True:
        page = agent.calls(RecordQuery(since=after)).to_dict()
        found.extend(page["calls"])
        if not page["calls"] or not page["more"]:
            return found
        after = page["cursor"]


def _flooded_agent(tmp_path, url, depth=64):
    """An answered call, start to end, on a small store and a short window."""
    clock, utc, ua = FakeClock(), FakeUtc(), FakeUa()
    config = replace(
        agent_config_for(tmp_path, lane_url=url),
        records=record_settings(max_bytes=1 << 20),
        event_window_depth=depth,
    )
    agent = agent_for(config, ua, clock=clock, now=utc)
    ua.incoming(INTERCOM, account_user=INTERCOM_ACCOUNT)
    agent.poll()
    ua.closed("call-1")
    agent.poll()
    [answered] = _calls(agent)
    assert answered["disposition"] == "answered", "the control: one answered call"
    return agent, ua, answered


def test_a_flood_of_refused_calls_never_evicts_an_answered_one(tmp_path, lane):  # noqa: F811
    _served, url = lane
    agent, ua, answered = _flooded_agent(tmp_path, url)
    # 8000 characters of `From`, which the user agent hands over as it came.
    huge = "sip:" + "a" * 8000 + "@10.9.9.9"
    for _ in range(400):
        ua.refused_unknown_account(huge)
        agent.poll()
    records = _calls(agent)
    assert answered["call_id"] in {one["call_id"] for one in records}, (
        "the answered call's record was evicted by refusals"
    )
    refused = [one for one in records if one["disposition"] != "answered"]
    assert refused, "refusals are still recorded, within their share"
    assert all(len(one["caller_stated_identity"]) <= MAX_CALLER_IDENTITY_CHARS
               for one in refused)


def test_the_events_of_an_answered_call_stay_in_the_window_through_a_flood(
    tmp_path, lane  # noqa: F811
):
    _served, url = lane
    agent, ua, _answered = _flooded_agent(tmp_path, url)
    for _ in range(500):
        ua.refused_unknown_account("sip:stranger@10.9.9.9")
        agent.poll()
    window = agent.events(0).to_dict()["events"]
    kinds = [one["kind"] for one in window]
    assert "call_answered" in kinds, "a flood of refusals pushed out the answered call"
    assert kinds.count("call_from_undeclared_intercom") <= 64 // EXPENDABLE_SHARE_DIVISOR


def test_a_refusal_only_ever_evicts_another_refusal(tmp_path):
    """The store itself: answered records survive any number of expendable ones."""
    store = RecordStore(
        tmp_path / "records",
        kind="c",
        time_key="started_at",
        lane_key="lane",
        unfinished=lambda body: False,
        expendable=lambda body: body["disposition"] != "answered",
        max_bytes=1 << 20,
    )
    store.open()
    from gate_agent.records import new_record_id

    kept = []
    for second in range(40):
        record_id = new_record_id("c")
        store.put(record_id, {"disposition": "answered", "lane": "entry",
                              "started_at": f"2026-11-01T00:00:{second:02d}+00:00",
                              "padding": "x" * 4000})
        kept.append(record_id)
    for minute in range(3000):
        store.put(new_record_id("c"), {"disposition": "refused_undeclared", "lane": None,
                                       "started_at": f"2026-11-01T01:{minute % 60:02d}:00+00:00",
                                       "padding": "y" * 1000})
    assert all(store.get(one) is not None for one in kept)
    assert store._expendable_bytes <= (1 << 20) // EXPENDABLE_SHARE_DIVISOR
    # The control: an ANSWERED write past the budget still evicts oldest first.
    for second in range(400):
        store.put(new_record_id("c"), {"disposition": "answered", "lane": "entry",
                                       "started_at": f"2026-11-01T02:00:{second % 60:02d}+00:00",
                                       "padding": "z" * 4000})
    assert store.get(kept[0]) is None
