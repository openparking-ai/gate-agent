"""The monitor's own surface: what it publishes, and what it refuses to publish.

The document and the code are compared two ways, because one of them is not
enough and that was learned expensively on the round before this one.

**By SHAPE** — every `<!--payload:NAME-->` example is parsed and its key
structure compared against what the code builds. That catches a field added,
renamed or dropped.

**By VALUE** — for the fields that are CONSTANTS of the code. `shape()` discards
every leaf, so a document could publish `contract_version: 99`, a state outside
the enum, or a source the code would refuse, with the shape check green. The
expectation is derived from the LIVE payload and from the enums, never from a
second copy of the assertion.
"""

from __future__ import annotations

import json
import re
from dataclasses import replace
from pathlib import Path

import pytest

from conftest import config_for, monitor_for
from fakes import (
    FakeIdentityService,
    FakePlatform,
    RecordingSink,
    identity_server,
    platform_server,
)
from foreign_lane import ForeignLane
from foreign_lane import make_server as foreign_server
from gate_agent.config import DEFAULT_MAX_SNAPSHOT_BYTES_SETTING, RETENTION_DAYS_BOUNDS
from gate_agent.contract import (
    AUTHORISATION_DIGITS,
    CONTRACT_VERSION,
    AgentCase,
    AgentCode,
    AgentEventKind,
    Authorisation,
    CameraUnreachableCause,
    CaptureCode,
    CaptureReason,
    HealthState,
    MonitorCode,
    MonitorEntry,
    MonitorHealth,
    SinkKind,
    Source,
    TargetKind,
    Transition,
)
from gate_agent.service import (
    ACT_ROUTES,
    READ_ROUTES,
    InsecureBind,
    MonitorService,
    _Handler,
    assert_bind_allowed,
    make_server,
)
from serving import serving

CONTRACT_DOC = Path(__file__).resolve().parent.parent / "docs" / "CONTRACT.md"

ORDINARY_CODE = "outbox_depth_growing"


@pytest.fixture
def watched():
    """A monitor with all three kinds of target, one of them with a fault.

    A populated payload on purpose: an empty `codes` list reduces to an empty
    shape, and a doc comparison against nothing would pass whatever the document
    said.
    """
    lane = ForeignLane()
    lane.states[ORDINARY_CODE] = "active"
    lane.sources[ORDINARY_CODE] = "measured"
    identity = FakeIdentityService()
    platform = FakePlatform(
        devices=[
            {
                "id": "device-1",
                "lane_id": "lane-1",
                "name": "entry",
                "created_at": "2026-08-30T13:00:00+00:00",
                "last_seen_at": "2026-08-30T13:59:00+00:00",
                "revoked_at": None,
            }
        ]
    )
    with serving(foreign_server(lane)) as lane_url, serving(
        identity_server(identity)
    ) as identity_url, serving(platform_server(platform)) as platform_url:
        monitor = monitor_for(
            config_for(lane=lane_url, identity_service=identity_url, platform=platform_url),
            [RecordingSink()],
        )
        monitor.start()
        yield monitor


#: The blocks in the document that are a MONITOR ROUTE's payload. `sets` is in
#: the same marked-block mechanism and is deliberately not one of them: it
#: publishes the closed sets a consumer branches on, and each is compared
#: against the enum or the constant it comes from.
ROUTE_PAYLOADS = {"monitor", "health", "events"}

#: The capture process's route payloads, checked in
#: `tests/test_capture_contract.py`. Named here so THIS file's "every block
#: belongs to a route" assertion accounts for every block in the document rather
#: than being loosened to ignore whatever it does not recognise -- a block
#: nobody checks is exactly the copy that drifts.
CAPTURE_PAYLOADS = {"capture", "capture_health", "capture_records"}

#: The agent's route payloads, checked in `tests/test_agent_contract.py`. Named
#: here for the same reason the capture process's are: this file's "every block
#: belongs to a route" assertion accounts for every block in the document rather
#: than being loosened to ignore whatever it does not recognise.
AGENT_PAYLOADS = {"agent", "agent_health", "agent_events"}


def doc_payloads() -> dict[str, dict]:
    """Every `<!--payload:NAME-->` example in `docs/CONTRACT.md`, parsed."""
    text = CONTRACT_DOC.read_text(encoding="utf-8")
    found = re.findall(r"<!--payload:([a-z_]+)-->\s*```json\n(.*?)\n```", text, re.S)
    return {name: json.loads(body) for name, body in found}


def shape(value):
    """The KEY structure of a payload, with every leaf value discarded.

    Values move -- a cursor, a timestamp, a port. The shape is what a consumer
    writes code against. Lists reduce to the shape of their first element,
    because a payload's list is homogeneous by construction and a doc example
    shows one of them.
    """
    if isinstance(value, dict):
        return {key: shape(item) for key, item in sorted(value.items())}
    if isinstance(value, list):
        return [shape(value[0])] if value else []
    return None


def live(monitor) -> dict[str, dict]:
    return {
        "monitor": monitor.describe().to_dict(),
        "health": monitor.health().to_dict(),
        "events": monitor.events(0).to_dict(),
    }


# ---------------------------------------------------------------------------
# GUARANTEE 1 — the document and the code agree, by shape and by value
# ---------------------------------------------------------------------------


def test_the_document_shows_exactly_the_payloads_the_code_builds(watched):
    """A hand-written example is a second copy of a shape the code defines.

    So the expectation is derived from the CODE and the doc compared against it:
    a field added, renamed or dropped in either goes red.
    """
    doc = doc_payloads()
    assert set(doc) == ROUTE_PAYLOADS | CAPTURE_PAYLOADS | AGENT_PAYLOADS | {"sets"}, (
        "every route has a payload example, every example belongs to a route, and "
        f"`sets` is the closed-set block that is not a route; found {sorted(doc)}"
    )
    served = live(watched)
    for name in sorted(ROUTE_PAYLOADS):
        example = doc[name]
        assert shape(example) == shape(served[name]), (
            f"docs/CONTRACT.md's `{name}` example does not have the shape the code builds.\n"
            f"  doc:  {shape(example)}\n  code: {shape(served[name])}"
        )
    # The control: the live health payload really does carry a target with codes
    # in it, so the nested comparison above was not against an empty list.
    assert served["health"]["targets"][0]["codes"]
    assert served["events"]["events"]


def test_the_documents_contract_version_is_the_codes(watched):
    """`shape()` discards leaves, so the doc could publish any version at all.

    The closed-set block is not a route's payload and carries no version: it
    publishes the set the routes are made of, and stamping one on it would be a
    second copy of a version number.
    """
    doc = doc_payloads()
    served = live(watched)
    checked = 0
    for name in sorted(ROUTE_PAYLOADS):
        example = doc[name]
        assert example["contract_version"] == CONTRACT_VERSION
        assert example["contract_version"] == served[name]["contract_version"]
        checked += 1
    assert checked == len(ROUTE_PAYLOADS), "a payload example carries no contract_version"
    assert checked, "no payloads were checked"


def test_every_closed_set_value_in_the_document_is_a_member_of_that_set():
    """A doc example may not quote a value the code would refuse.

    These sets are what a consumer branches on; a document showing a value
    outside one teaches an integrator a case that will never arrive, or worse,
    one the monitor would reject.
    """
    closed = {
        ("monitor", "targets", "kind"): tuple(kind.value for kind in TargetKind),
        ("monitor", "sinks", "kind"): tuple(kind.value for kind in SinkKind),
        ("health", "codes", "code"): tuple(code.value for code in MonitorCode),
        ("health", "codes", "state"): tuple(state.value for state in HealthState),
        ("health", "codes", "source"): tuple(source.value for source in Source),
        ("health", "targets", "kind"): tuple(kind.value for kind in TargetKind),
        ("health", "targets", "codes", "state"): tuple(state.value for state in HealthState),
        ("health", "targets", "codes", "source"): tuple(source.value for source in Source),
        ("events", "events", "transition"): tuple(one.value for one in Transition),
        ("events", "events", "source"): tuple(source.value for source in Source),
    }
    doc = doc_payloads()
    for (name, *path), allowed in closed.items():
        values = _at(doc[name], path)
        # The control for this path: if the field were renamed or dropped, the
        # loop would iterate over nothing and the check would evaporate.
        assert values, f"docs/CONTRACT.md's `{name}` example has no {'.'.join(path)}"
        for value in values:
            assert value is None or value in allowed, (
                f"docs/CONTRACT.md's `{name}` example publishes "
                f"{'.'.join(path)}={value!r}, which is not in {allowed}"
            )


def _at(payload, path):
    """Every value at `path`, descending into lists. `KeyError` if it is gone."""
    if not path:
        return [payload]
    key, rest = path[0], path[1:]
    if isinstance(payload, list):
        return [value for item in payload for value in _at(item, path)]
    return _at(payload[key], rest)


# ---------------------------------------------------------------------------
# GUARANTEE 2 — every monitor code ships, every time
# ---------------------------------------------------------------------------


def test_every_monitor_code_ships_with_a_subject_and_a_source(watched):
    codes = watched.health().to_dict()["codes"]
    assert {entry["code"] for entry in codes} == {code.value for code in MonitorCode}
    for entry in codes:
        assert entry["subject"]
        assert entry["source"] in {source.value for source in Source}
        assert entry["state"] in {state.value for state in HealthState}


def test_a_health_payload_missing_a_code_is_refused():
    """The control for the test above, and it fires at construction."""
    every = tuple(
        MonitorEntry(code=code.value, subject="x", state=HealthState.UNKNOWN.value)
        for code in MonitorCode
    )
    MonitorHealth(codes=every)  # intact, builds

    with pytest.raises(ValueError, match="missing"):
        MonitorHealth(codes=every[:-1])
    with pytest.raises(ValueError, match="twice"):
        MonitorHealth(codes=every + (every[0],))


def test_a_code_with_no_subject_yet_still_ships():
    """A monitor with only a lane declared still publishes the platform's codes.

    `unknown`, once, under its own id. An absent code reads to a consumer
    exactly like a code that is fine, and "we have no platform declared" is not
    the same fact as "the platform is reachable".
    """
    lane = ForeignLane()
    with serving(foreign_server(lane)) as url:
        monitor = monitor_for(config_for(lane=url), [RecordingSink()])
        monitor.start()
        codes = {
            (entry["code"], entry["subject"], entry["state"])
            for entry in monitor.health().to_dict()["codes"]
        }
    assert (MonitorCode.PLATFORM_UNREACHABLE.value, monitor.config.monitor_id, "unknown") in codes
    assert (MonitorCode.LANE_GONE_QUIET.value, monitor.config.monitor_id, "unknown") in codes


def test_a_state_that_is_not_unknown_needs_a_measured_source():
    """`ok` and `active` are claims about a measurement.

    Copied from the lane contract because it is the same invariant, and asserted
    here because a consumer holding one policy for two surfaces needs both to
    mean it.
    """
    code = MonitorCode.LANE_UNREACHABLE.value
    MonitorEntry(code=code, subject="lane", state=HealthState.OK.value)
    MonitorEntry(code=code, subject="lane", state=HealthState.UNKNOWN.value)

    with pytest.raises(ValueError, match="not a monitor code"):
        MonitorEntry(code="reference_not_recognised", subject="lane", state="unknown")


# ---------------------------------------------------------------------------
# GUARANTEE 3 — the surface is read-only
# ---------------------------------------------------------------------------


def test_no_route_mutates_anything():
    """Every `do_*` other than `do_GET` IS the one shared refusal.

    Swept off the handler rather than asserted route by route, so a route that
    changed something would have to stop being that function and this goes red in
    the same commit.
    """
    methods = {
        name: getattr(_Handler, name) for name in dir(_Handler) if name.startswith("do_")
    }
    assert "do_GET" in methods
    others = {name: fn for name, fn in methods.items() if name != "do_GET"}
    assert others, "the sweep found no other methods, so it proves nothing"
    for name, fn in others.items():
        assert fn is _Handler._method_not_allowed, f"{name} is not the shared refusal"
    assert ACT_ROUTES == ()
    assert set(READ_ROUTES) == {"/v1/monitor", "/v1/monitor/health", "/v1/monitor/events"}


def test_a_consumer_that_posts_to_this_surface_is_refused(watched):
    """The question a consumer would ask, asked for real over a socket."""
    import urllib.request

    with serving(make_server(MonitorService(watched), port=0)) as base:
        for path in READ_ROUTES:
            with urllib.request.urlopen(f"{base}{path}", timeout=5) as response:
                assert response.status == 200
        try:
            urllib.request.urlopen(
                urllib.request.Request(f"{base}/v1/monitor", data=b"{}", method="POST"), timeout=5
            )
            raise AssertionError("a POST was accepted")
        except urllib.error.HTTPError as exc:
            assert exc.code == 405
            assert exc.headers["Allow"] == "GET"


# ---------------------------------------------------------------------------
# GUARANTEE 4 — the bind rule, and the cursor
# ---------------------------------------------------------------------------


def test_a_non_loopback_bind_without_a_token_is_refused():
    assert_bind_allowed("127.0.0.1", 8092, None)
    assert_bind_allowed("::1", 8092, None)
    assert_bind_allowed("192.168.1.10", 8092, "a-token")
    for host in ("192.168.1.10", "0.0.0.0", "", "monitor.local"):
        with pytest.raises(InsecureBind):
            assert_bind_allowed(host, 8092, None)


def test_with_a_token_every_route_requires_it(watched):
    import urllib.request

    server = make_server(MonitorService(watched), port=0, token="s3cret")
    with serving(server) as base:
        for path in READ_ROUTES:
            with pytest.raises(urllib.error.HTTPError) as refused:
                urllib.request.urlopen(f"{base}{path}", timeout=5)
            assert refused.value.code == 401

            request = urllib.request.Request(f"{base}{path}")
            request.add_header("Authorization", "Bearer s3cret")
            with urllib.request.urlopen(request, timeout=5) as response:
                assert response.status == 200


def test_a_cursor_ahead_of_ours_says_reset(watched):
    current = watched.events(0).to_dict()["cursor"]
    assert watched.events(current).to_dict()["reset"] is False
    assert watched.events(current + 1).to_dict()["reset"] is True


def test_a_cursor_behind_the_window_says_reset():
    """The window is bounded and it reports its own eviction.

    A consumer further behind than the window would otherwise be served what
    survived, with the evicted notifications simply absent -- which looks exactly
    like a complete page.
    """
    lane = ForeignLane()
    sink = RecordingSink()
    from conftest import FakeClock

    clock = FakeClock()
    with serving(foreign_server(lane)) as url:
        monitor = monitor_for(
            config_for(lane=url, poll_seconds=1.0, event_window_depth=2), [sink], clock=clock
        )
        monitor.start()
        for state in ("active", "ok", "active", "ok", "active"):
            lane.states[ORDINARY_CODE] = state
            lane.sources[ORDINARY_CODE] = "measured"
            clock.advance(10)
            monitor.poll()

    page = monitor.events(0).to_dict()
    assert page["reset"] is True
    assert page["dropped"] > 0
    assert len(page["events"]) <= 2
    # And a cursor inside the window is served without a reset.
    inside = monitor.events(page["cursor"] - 1).to_dict()
    assert inside["reset"] is False


# ---------------------------------------------------------------------------
# GUARANTEE 5 — THIS ROUTE PUBLISHES AN ADDRESS, NEVER A WAY IN
# ---------------------------------------------------------------------------


def test_a_planted_credential_in_a_target_url_reaches_no_route():
    """The sweep, with the plant, over everything this monitor serves.

    `config.py` refuses userinfo at startup, so this plants it BELOW that --
    straight onto a `Target`, the way a caller building one by hand would -- and
    then sweeps all three routes for it. A refusal at one layer is a refusal at
    one layer; what this route publishes is a property of this route.

    Scheme, host, port and path, and nothing else: the URL is REBUILT from those
    four rather than echoed, so a query string or a fragment cannot carry
    anything out either.
    """
    from gate_agent.config import Target
    from gate_agent.contract import TargetKind

    planted = "S3CRET-PASSWORD"
    config = config_for(lane="http://127.0.0.1:8090")
    config = replace(
        config,
        targets=(
            Target(
                name="lane",
                kind=TargetKind.LANE,
                url=f"https://ops:{planted}@lane.example.com:8443/v1?token={planted}#{planted}",
                poll_seconds=30.0,
            ),
        ),
    )
    monitor = monitor_for(config, [RecordingSink()])

    served = json.dumps(
        {
            "monitor": monitor.describe().to_dict(),
            "health": monitor.health().to_dict(),
            "events": monitor.events(0).to_dict(),
        }
    )
    assert planted not in served, f"a planted credential is on this monitor's own routes: {served}"
    assert "ops" not in served

    published = monitor.describe().to_dict()["targets"][0]["url"]
    assert published == "https://lane.example.com:8443/v1"

    # THE CONTROL: the sweep can find that string in this payload when it is
    # there. Without it, `planted not in served` is a claim about the search.
    control = json.dumps({"monitor": {"targets": [{"url": f"https://x:{planted}@y/"}]}})
    assert planted in control


def test_the_payload_layer_refuses_a_credential_bearing_url_outright():
    """The refusal one layer lower, so it cannot be routed around.

    The configuration refuses userinfo and the description rebuilds the address.
    This is the third: a `TargetDescription` built by hand with a credential in
    it does not exist.
    """
    from gate_agent.contract import TargetDescription

    with pytest.raises(ValueError, match="userinfo in URL"):
        TargetDescription(
            name="lane",
            kind="lane",
            url="https://ops:S3CRET-PASSWORD@example.com",
            poll_seconds=30.0,
            authenticated=False,
            timeout_seconds=10.0,
        )
    # The opposite beside it: the same target without one builds.
    assert TargetDescription(
        name="lane",
        kind="lane",
        url="https://lane.example.com",
        poll_seconds=30.0,
        authenticated=False,
        timeout_seconds=10.0,
    )


# ---------------------------------------------------------------------------
# GUARANTEE 6 — THE CLOSED SET IS PUBLISHED, AND THE COPY IS HELD TO THE ENUM
# ---------------------------------------------------------------------------


#: The closed sets this document publishes, keyed to where each comes from.
#: Derived from the enum or the constant, never typed -- a hand-written
#: expectation here would be a third copy, and the third copy lies too.
PUBLISHED_SETS = {
    "monitor_codes": lambda: [code.value for code in MonitorCode],
    "capture_codes": lambda: [code.value for code in CaptureCode],
    "camera_unreachable_causes": lambda: [cause.value for cause in CameraUnreachableCause],
    "capture_reasons": lambda: [reason.value for reason in CaptureReason],
    "retention_days_bounds": lambda: list(RETENTION_DAYS_BOUNDS),
    # NOT a set, and here for the reason the document gives beside it: it is a
    # published DEFAULT that no payload example may carry, because every size in
    # a capture example is `null`. This block is the one place a number in this
    # document is held to the constant it came from.
    "max_snapshot_bytes_default": lambda: DEFAULT_MAX_SNAPSHOT_BYTES_SETTING,
    # The agent's, added in the round that gave this package its third process.
    # Same rule and same reason: a paging system, an operator console or a
    # third-party intercom integration branches on these, and a document that
    # withholds them moves the copy into the implementer's guess.
    "agent_codes": lambda: [code.value for code in AgentCode],
    "agent_cases": lambda: [case.value for case in AgentCase],
    "authorisations": lambda: [value.value for value in Authorisation],
    # NOT a set. It is the DIGIT-to-authorisation mapping, published because it
    # is fixed across every site by decision -- the person keying it is often the
    # same person across several garages at three in the morning.
    "authorisation_digits": lambda: {
        digit: value.value for digit, value in AUTHORISATION_DIGITS.items()
    },
    "agent_event_kinds": lambda: [kind.value for kind in AgentEventKind],
    # THE VOID REASONS, published both ways from round 7. The set is what a
    # standalone site's own record says happened to a ticket -- it is the only
    # account of one where there is no platform -- and it grew from five to ten
    # in the round that stopped writing `lane_decided_again` for six outcomes
    # that were not a new decision.
    "void_reasons": lambda: list(_void_reasons()),
    "shipped_languages": lambda: list(_shipped_languages()),
}


def _void_reasons():
    from gate_agent.tickets import VOID_REASONS

    return VOID_REASONS


def _shipped_languages():
    from gate_agent.lines import SHIPPED_LANGUAGES

    return SHIPPED_LANGUAGES


def test_the_document_publishes_every_closed_set_this_package_defines(watched):
    """Both directions, against the enums, and the monitor's against the wire.

    This document withheld the sets on the reasoning that a hand-written copy of
    a set the code defines is the copy that goes wrong. The reasoning is right
    and the conclusion was not: a consumer cannot be written from a document
    that withholds what it must branch on, so the copy moves into the
    implementer's guess instead. They are published, and this is what stops them
    drifting.

    Keyed one-to-one by name in BOTH directions: a set dropped from the document
    goes red, and so does a set added to the code without adding it here.
    """
    published = doc_payloads()["sets"]
    assert set(published) == set(PUBLISHED_SETS), (
        f"docs/CONTRACT.md publishes {sorted(published)}; this package defines "
        f"{sorted(PUBLISHED_SETS)}"
    )
    for name, from_the_code in PUBLISHED_SETS.items():
        assert published[name] == from_the_code(), (
            f"docs/CONTRACT.md publishes {name}={published[name]}; the code holds "
            f"{from_the_code()}"
        )
        # The control: no comparison here is two empty lists agreeing.
        assert published[name], name
    # And the monitor's against the wire, so a document agreeing with an enum
    # nothing ships would still go red.
    served = [entry["code"] for entry in watched.health().to_dict()["codes"]]
    assert sorted(set(served)) == sorted(published["monitor_codes"])
