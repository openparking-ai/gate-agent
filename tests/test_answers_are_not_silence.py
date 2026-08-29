"""What a target can make this monitor do, and what it can make it say.

Every test here is asked from the ATTACKER's side of the wire: not "does the
monitor read a healthy lane correctly" but "what does a target that answers
badly get out of it?" Five things did, and each one failed in the reassuring
direction -- a credential handed over, a fault silenced, a machine misnamed.

  * a `302` steered the read-only client and the webhook sink at a host of the
    payload's choosing, with the bearer token attached, and the sink reported
    the undelivered POST as a SUCCESS;
  * `never_alarm` was `bool(...)` of whatever arrived, so an absent field paged
    a technician because a car arrived and the string `"false"` silenced a code
    for ever;
  * a `state` outside the contract's three was passed through and POISONED the
    next transition, so an `active` fault after one went to nobody;
  * every notification carried the lane's id, including the ones about the
    platform and about this monitor's own sinks;
  * every HTTP error was published as `<kind>_unreachable`, so a dead
    credential, a platform older than this build and a platform that is down
    were one message with no status on it.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from conftest import FakeClock, config_for, monitor_for
from fakes import FakeIdentityService, RecordingSink, identity_server
from foreign_lane import ForeignLane
from foreign_lane import make_server as foreign_server
from gate_agent.client import ReadOnlyClient, TargetRefusedUs, TargetUnreachable
from gate_agent.config import WebhookSinkConfig
from gate_agent.contract import MonitorCode
from gate_agent.sinks import DeliveryFailed, WebhookSink
from serving import serving

ORDINARY_CODE = "outbox_depth_growing"
NEVER_ALARM_CODE = "reference_not_recognised"

OPERATOR_TOKEN = "OPERATOR-TOKEN-FROM-FILE"
WEBHOOK_TOKEN = "WEBHOOK-TOKEN-FROM-FILE"


# ---------------------------------------------------------------------------
# V1 — NOTHING FOLLOWS A REDIRECT, AND THE SECOND HOST IS THE WITNESS
# ---------------------------------------------------------------------------


class Untouched:
    """A host that FAILS THE TEST IF IT IS TOUCHED. It records everything.

    The assertion that matters is not what the client returned: it is that this
    server, which the monitor was never configured with, received no request and
    therefore no credential. Asked of the third host rather than of the monitor,
    because a client the source sweep did not recognise would still show up
    here.
    """

    def __init__(self) -> None:
        self.received: list[tuple[str, str, str | None]] = []


def _redirector(second_url: str, obj: Untouched):
    """A target that answers `302 Location: <the second host>` to everything."""

    class _Handler(BaseHTTPRequestHandler):
        server_version = "hostile-target"
        sys_version = ""

        def log_message(self, *args) -> None:
            pass

        def _redirect(self) -> None:
            self.send_response(302)
            self.send_header("Location", f"{second_url}/stolen")
            self.send_header("Content-Length", "0")
            self.end_headers()

        do_GET = _redirect  # noqa: N815
        do_POST = _redirect  # noqa: N815

    return ThreadingHTTPServer(("127.0.0.1", 0), _Handler)


def _second_host(obj: Untouched):
    class _Handler(BaseHTTPRequestHandler):
        server_version = "third-party-host"
        sys_version = ""

        def log_message(self, *args) -> None:
            pass

        def _record(self) -> None:
            obj.received.append(
                (self.command, self.path, self.headers.get("Authorization"))
            )
            body = b'{"ok": true}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        do_GET = _record  # noqa: N815
        do_POST = _record  # noqa: N815

    return ThreadingHTTPServer(("127.0.0.1", 0), _Handler)


@pytest.fixture
def hostile():
    """A target that redirects, and the host it redirects to. Both real sockets."""
    witness = Untouched()
    with serving(_second_host(witness)) as second_url:
        with serving(_redirector(second_url, witness)) as first_url:
            yield first_url, second_url, witness


def test_the_read_only_client_refuses_a_redirect_and_hands_over_nothing(hostile):
    """The credential does not leave, and the third host's payload is not read.

    Three failures came out of one `Location` header: the operator token went to
    a host of the target's choosing, `{"ok": true}` from that host was published
    as the lane's own health, and neither was visible anywhere.
    """
    first_url, _second_url, witness = hostile
    client = ReadOnlyClient(first_url, token=OPERATOR_TOKEN)

    with pytest.raises(TargetRefusedUs) as refused:
        client.get("/v1/lane/health")

    # It is an ANSWER, and it is reported as one, with its status.
    assert refused.value.status == 302
    assert witness.received == [], (
        f"the second host was touched: {witness.received}. A redirect took this monitor's "
        "credential to a host nobody configured."
    )


def test_a_redirected_webhook_is_a_delivery_that_did_not_happen(hostile):
    """`deliver()` reported SUCCESS on a POST that went to a third host.

    So `sink_delivery_failed` -- the machinery this module has precisely so that
    "never wrong silently" applies to the messenger too -- was the one piece
    that walked past it.
    """
    first_url, _second_url, witness = hostile
    sink = WebhookSink(WebhookSinkConfig(url=f"{first_url}/page", token=WEBHOOK_TOKEN))

    with pytest.raises(DeliveryFailed, match="HTTP 302"):
        sink.deliver(_a_notification())

    assert witness.received == [], (
        f"the second host was touched: {witness.received}. A paging endpoint redirected this "
        "sink's bearer token to somebody else."
    )


def test_the_witness_would_have_noticed(hostile):
    """THE CONTROL, and without it the two tests above assert nothing.

    A recorder that never records would satisfy `received == []` however the
    monitor behaved. So the second host is asked for directly, with the same
    header, and it must appear.
    """
    _first_url, second_url, witness = hostile
    request = urllib.request.Request(f"{second_url}/stolen", method="GET")
    request.add_header("Authorization", f"Bearer {OPERATOR_TOKEN}")
    with urllib.request.urlopen(request, timeout=5) as response:
        assert response.status == 200
    assert witness.received == [("GET", "/stolen", f"Bearer {OPERATOR_TOKEN}")]


def _a_notification():
    from gate_agent.contract import Notification

    return Notification(
        site_id="site-1",
        lane_id=None,
        target="lane",
        code=ORDINARY_CODE,
        subject=None,
        transition="raised",
        source="measured",
        caveat=None,
        at="2026-08-30T14:00:00+00:00",
    )


# ---------------------------------------------------------------------------
# V2 and V3 — A PAYLOAD THIS BUILD CANNOT READ IS REFUSED WHOLE
# ---------------------------------------------------------------------------


@pytest.fixture
def lane_and_monitor():
    """A foreign lane on a socket, a monitor watching it, and a clock to move."""
    lane = ForeignLane()
    sink = RecordingSink()
    clock = FakeClock()
    with serving(foreign_server(lane)) as url:
        monitor = monitor_for(config_for(lane=url, poll_seconds=1.0), [sink], clock=clock)
        monitor.start()
        sink.delivered.clear()
        yield lane, monitor, sink, clock


def _states(monitor):
    return {
        (entry["code"], entry["subject"]): entry["state"]
        for entry in monitor.health().to_dict()["codes"]
    }


def _lane_codes(monitor):
    target = next(one for one in monitor.health().to_dict()["targets"] if one["name"] == "lane")
    return target["codes"]


def test_a_health_entry_without_never_alarm_is_a_payload_this_build_cannot_read(
    lane_and_monitor,
):
    """ABSENT is the named failure of this project, and it used to page.

    `bool(None)` is `False`, so a lane that omitted the field on
    `reference_not_recognised` dispatched a technician because a car arrived --
    which is the exact thing SETTLED 3d(i) and the lane contract's caveat exist
    to prevent.
    """
    lane, monitor, sink, clock = lane_and_monitor
    lane.never_alarm_override.pop(NEVER_ALARM_CODE, None)
    lane.states[NEVER_ALARM_CODE] = "active"
    lane.sources[NEVER_ALARM_CODE] = "measured"
    lane.drop_never_alarm = True

    clock.advance(60)
    monitor.poll()

    assert sink.codes == [(MonitorCode.TARGET_CONTRACT_UNSUPPORTED.value, "raised")], (
        "an absent never_alarm was read as a decision instead of as a payload this build "
        f"cannot read: {sink.codes}"
    )
    assert _states(monitor)[(MonitorCode.TARGET_CONTRACT_UNSUPPORTED.value, "lane")] == "active"
    assert _lane_codes(monitor) == []


@pytest.mark.parametrize("planted", ["false", "no", "", 0, 1, None, [], {}])
def test_never_alarm_that_is_not_a_boolean_is_refused_in_both_directions(
    lane_and_monitor, planted
):
    """`"false"` is truthy. It silenced a code for ever, with nothing reporting it.

    The fail-control's own preamble says every break must fail in the reassuring
    direction "because that is the direction a monitor fails in when nobody is
    looking". This input did it with no break applied, and the opposite value
    did the opposite. Neither is a decision this build is entitled to make.
    """
    lane, monitor, sink, clock = lane_and_monitor
    lane.states[ORDINARY_CODE] = "active"
    lane.sources[ORDINARY_CODE] = "measured"
    lane.never_alarm_override[ORDINARY_CODE] = planted

    clock.advance(60)
    monitor.poll()

    assert sink.codes == [(MonitorCode.TARGET_CONTRACT_UNSUPPORTED.value, "raised")]
    assert (ORDINARY_CODE, "raised") not in sink.codes
    assert _lane_codes(monitor) == []


def test_a_boolean_either_way_is_read_as_before(lane_and_monitor):
    """THE CONTROL: the refusal is about the TYPE, not about the field.

    A refusal that fired on every payload would satisfy every assertion above
    and would make this monitor useless. Both booleans still behave exactly as
    they did: `true` silences, `false` sends.
    """
    lane, monitor, sink, clock = lane_and_monitor

    lane.never_alarm_override[ORDINARY_CODE] = True
    lane.states[ORDINARY_CODE] = "active"
    lane.sources[ORDINARY_CODE] = "measured"
    clock.advance(60)
    monitor.poll()
    assert sink.codes == []

    lane.never_alarm_override[NEVER_ALARM_CODE] = False
    lane.states[NEVER_ALARM_CODE] = "active"
    lane.sources[NEVER_ALARM_CODE] = "measured"
    clock.advance(60)
    monitor.poll()
    assert sink.codes == [(NEVER_ALARM_CODE, "raised")]
    assert _states(monitor)[(MonitorCode.TARGET_CONTRACT_UNSUPPORTED.value, "lane")] == "ok"


def test_a_state_outside_the_contracts_three_is_refused_and_leaves_nothing_active(
    lane_and_monitor,
):
    """The blocker was not the unread state. It was the NEXT one.

    `_transition` matches three literals, so an unrecognised value could never
    produce a transition -- and it became the `previous` state, so the `active`
    that followed it was held, published as active, and told to NOBODY. One
    malformed poll suppressed the raise for as long as the fault lasted.
    """
    lane, monitor, sink, clock = lane_and_monitor

    # The baseline: this lane can raise a code at all.
    lane.states[ORDINARY_CODE] = "active"
    lane.sources[ORDINARY_CODE] = "measured"
    clock.advance(60)
    monitor.poll()
    assert sink.codes == [(ORDINARY_CODE, "raised")]
    sink.delivered.clear()

    # Now a state this build does not know. The payload is refused whole, and
    # nothing that was active before is left standing on the surface.
    lane.states[ORDINARY_CODE] = "broken"
    clock.advance(60)
    monitor.poll()
    assert sink.codes == [
        (MonitorCode.TARGET_CONTRACT_UNSUPPORTED.value, "raised"),
        (ORDINARY_CODE, "no_longer_measured"),
    ]
    assert _lane_codes(monitor) == []
    sink.delivered.clear()

    # And the fault that follows the malformed poll is REPORTED. This is the row
    # that was silent: with `"broken"` held as the previous state, `active` came
    # back and produced nothing.
    lane.states[ORDINARY_CODE] = "active"
    clock.advance(60)
    monitor.poll()
    assert (ORDINARY_CODE, "raised") in sink.codes


@pytest.mark.parametrize("planted", ["ACTIVE", "Ok", "probably_fine", "", None, 1])
def test_no_state_outside_the_three_is_ever_published(lane_and_monitor, planted):
    """Case included: `"ACTIVE"` is not `active`, and guessing is not available."""
    lane, monitor, sink, clock = lane_and_monitor
    lane.states[ORDINARY_CODE] = planted
    lane.sources[ORDINARY_CODE] = "measured"

    clock.advance(60)
    monitor.poll()

    assert _lane_codes(monitor) == []
    assert _states(monitor)[(MonitorCode.TARGET_CONTRACT_UNSUPPORTED.value, "lane")] == "active"


def test_the_refusal_is_paged_once_and_not_on_every_poll(lane_and_monitor):
    """It is a transition like any other: named, sent once, and recovered from."""
    lane, monitor, sink, clock = lane_and_monitor
    lane.states[ORDINARY_CODE] = "broken"

    for _ in range(3):
        clock.advance(60)
        monitor.poll()
    assert sink.codes.count((MonitorCode.TARGET_CONTRACT_UNSUPPORTED.value, "raised")) == 1

    lane.states[ORDINARY_CODE] = "ok"
    clock.advance(60)
    monitor.poll()
    assert (MonitorCode.TARGET_CONTRACT_UNSUPPORTED.value, "recovered") in sink.codes


# ---------------------------------------------------------------------------
# V5 — `lane_id` IS ON A LANE'S NOTIFICATIONS AND ON NOBODY ELSE'S
# ---------------------------------------------------------------------------


@pytest.fixture
def three_targets():
    """A lane that identifies itself, an identity service, and a dead platform.

    The platform is dead on purpose: it produces the monitor's own
    `platform_unreachable`, which is one of the two codes spelt the same as a
    LANE's and therefore the one a false `lane_id` misfiles.
    """
    lane = ForeignLane()
    # A code the lane really raises, so both sides of the branch below occur:
    # a notification about the lane, and notifications about everything else.
    lane.states[ORDINARY_CODE] = "active"
    lane.sources[ORDINARY_CODE] = "measured"
    identity = FakeIdentityService()
    sink = RecordingSink()
    with serving(foreign_server(lane)) as lane_url, serving(
        identity_server(identity)
    ) as identity_url:
        monitor = monitor_for(
            config_for(
                lane=lane_url,
                identity_service=identity_url,
                platform="http://127.0.0.1:1",
            ),
            [sink],
        )
        monitor.start()
        yield lane, monitor, sink


def test_only_a_lanes_notifications_carry_a_lane_id(three_targets):
    """A fault attributed to the wrong machine is a different repair.

    `code=platform_unreachable ... lane_id=fl-lane-a` reads as "lane fl-lane-a
    cannot reach the platform". It is a different machine, a different fault and
    a different repair from the true one -- and on the two codes that collide by
    name it puts a FALSE discriminator beside the only right one.
    """
    lane, monitor, sink = three_targets

    # The lane identified itself, so there is an id available to stamp wrongly.
    assert monitor._lane_id == lane.lane_id
    sent = sink.payloads
    assert sent, "nothing was sent, so this asserts nothing"

    for payload in sent:
        if payload["target"] == "lane":
            assert payload["lane_id"] == lane.lane_id
        else:
            assert payload["lane_id"] is None, (
                f"a notification about `{payload['target']}` carries lane_id="
                f"{payload['lane_id']!r}: {payload['code']}"
            )
    # The control: both sides of that branch really occurred.
    assert {payload["target"] for payload in sent} > {"lane"}


def test_the_monitors_own_sink_failure_is_not_about_a_lane():
    """The monitor's own codes are about the monitor. Every one of them.

    A sink that could not deliver is this process's fault, on this process's
    box, and it used to be published stamped with a lane's identity.
    """
    lane = ForeignLane()
    lane.states[ORDINARY_CODE] = "active"
    lane.sources[ORDINARY_CODE] = "measured"
    broken = RecordingSink(name="smtp", kind="email")
    broken.fail = True
    watching = RecordingSink(name="webhook", kind="webhook")

    with serving(foreign_server(lane)) as url:
        monitor = monitor_for(config_for(lane=url), [broken, watching])
        monitor.start()

    failures = [
        one for one in watching.payloads if one["code"] == MonitorCode.SINK_DELIVERY_FAILED.value
    ]
    assert failures, "the sink failure was not reported to the sink that works"
    for payload in failures:
        assert payload["lane_id"] is None
        assert payload["target"] == monitor.config.monitor_id
        assert payload["subject"] == "smtp"


def test_every_non_lane_code_is_swept_not_sampled(three_targets):
    """The enumeration, so this is not three cases that happened to be right."""
    _lane, monitor, _sink = three_targets
    health = monitor.health().to_dict()

    lane_target_names = {
        target["name"] for target in health["targets"] if target["kind"] == "lane"
    }
    assert lane_target_names == {"lane"}
    for target in health["targets"]:
        if target["kind"] != "lane":
            assert target["codes"] == [] or all(
                isinstance(entry, dict) for entry in target["codes"]
            )


# ---------------------------------------------------------------------------
# V6 — AN HTTP ANSWER IS NOT SILENCE
# ---------------------------------------------------------------------------


def _answering(status: int):
    """A platform that answers one status to everything, and nothing else."""

    class _Handler(BaseHTTPRequestHandler):
        server_version = "one-status"
        sys_version = ""

        def log_message(self, *args) -> None:
            pass

        def do_GET(self) -> None:  # noqa: N802
            body = json.dumps({"error": "no"}).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return ThreadingHTTPServer(("127.0.0.1", 0), _Handler)


@pytest.mark.parametrize(
    ("status", "code", "other"),
    [
        (401, MonitorCode.PLATFORM_REFUSED_US, MonitorCode.PLATFORM_UNREACHABLE),
        (403, MonitorCode.PLATFORM_REFUSED_US, MonitorCode.PLATFORM_UNREACHABLE),
        (404, MonitorCode.PLATFORM_REFUSED_US, MonitorCode.PLATFORM_UNREACHABLE),
        (500, MonitorCode.PLATFORM_UNREACHABLE, MonitorCode.PLATFORM_REFUSED_US),
        (503, MonitorCode.PLATFORM_UNREACHABLE, MonitorCode.PLATFORM_REFUSED_US),
    ],
)
def test_the_five_statuses_reach_the_right_code(status, code, other):
    """A dead credential, an older platform and a dead platform are three faults.

    401 and 403 are this monitor's credential. 404 is a platform that predates
    the route this build reads -- which is what a platform without #10 answers,
    and which used to make every monitor with a platform declared page
    `platform_unreachable: active` about a platform that is up. 5xx is the
    platform having a bad time, which IS the same situation as unreachable from
    here.
    """
    sink = RecordingSink()
    with serving(_answering(status)) as url:
        monitor = monitor_for(
            config_for(platform=url, garage_id="garage-1"), [sink]
        )
        monitor.start()

    states = _states(monitor)
    assert states[(code.value, "platform")] == "active"
    assert states[(other.value, "platform")] in ("ok", "unknown")
    assert (code.value, "raised") in sink.codes
    assert (other.value, "raised") not in sink.codes


@pytest.mark.parametrize("status", [401, 404])
def test_the_status_is_visible_in_the_email_the_webhook_and_the_log_line(status, caplog):
    """A message that cannot say WHICH refusal sends somebody to the wrong box.

    The same fact in the three places a human meets it: the health entry, the
    body every sink is handed, and the line in the log.
    """
    import logging

    from gate_agent.config import EmailSinkConfig
    from gate_agent.sinks import EmailSink

    sink = RecordingSink()
    with caplog.at_level(logging.WARNING, logger="gate_agent.monitor"):
        with serving(_answering(status)) as url:
            monitor = monitor_for(config_for(platform=url, garage_id="garage-1"), [sink])
            monitor.start()

    entry = next(
        one
        for one in monitor.health().to_dict()["codes"]
        if one["code"] == MonitorCode.PLATFORM_REFUSED_US.value
    )
    assert entry["status"] == status

    payload = next(
        one for one in sink.payloads if one["code"] == MonitorCode.PLATFORM_REFUSED_US.value
    )
    assert payload["status"] == status
    # The webhook posts this object; the email renders it. Both from one shape.
    email = EmailSink(
        EmailSinkConfig(
            host="smtp.example.com", port=25, sender="a@example.com", recipients=("b@example.com",)
        )
    ).compose(EmailSink.subject_for(payload), payload)
    assert f"status: {status}" in email.get_content()
    assert "platform" in email["Subject"]

    assert any(f"HTTP {status}" in record.getMessage() for record in caplog.records), (
        f"no log line carries the status: {[r.getMessage() for r in caplog.records]}"
    )


def test_a_target_that_answers_nothing_at_all_is_still_unreachable():
    """THE CONTROL for the split: silence is still silence.

    A refusal code that fired on everything would make `<kind>_unreachable`
    unreachable itself, which is the reassuring direction on Gokhan's "no
    connection".
    """
    sink = RecordingSink()
    monitor = monitor_for(config_for(lane="http://127.0.0.1:1"), [sink])
    monitor.start()

    states = _states(monitor)
    assert states[(MonitorCode.LANE_UNREACHABLE.value, "lane")] == "active"
    # And whether it would refuse us is not something this poll measured.
    assert states[(MonitorCode.LANE_REFUSED_US.value, "lane")] == "unknown"
    assert (MonitorCode.LANE_UNREACHABLE.value, "raised") in sink.codes


def test_a_refusal_recovers_when_the_target_answers_again():
    """It is a state and it follows the transition rule, like the other half."""
    lane = ForeignLane()
    sink = RecordingSink()
    clock = FakeClock()
    with serving(foreign_server(lane)) as good_url:
        with serving(_answering(401)) as bad_url:
            monitor = monitor_for(config_for(lane=bad_url, poll_seconds=1.0), [sink], clock=clock)
            monitor.start()
            assert (MonitorCode.LANE_REFUSED_US.value, "raised") in sink.codes

            monitor._clients["lane"].base_url = good_url
            clock.advance(60)
            monitor.poll()

    assert (MonitorCode.LANE_REFUSED_US.value, "recovered") in sink.codes
    assert _states(monitor)[(MonitorCode.LANE_REFUSED_US.value, "lane")] == "ok"


def test_the_client_itself_separates_the_two(hostile):
    """Read at the seam, so the split is not an accident of one caller."""
    first_url, _second, _witness = hostile
    with serving(_answering(500)) as five_hundred:
        with pytest.raises(TargetUnreachable):
            ReadOnlyClient(five_hundred).get("/anything")
    with serving(_answering(404)) as four_oh_four:
        with pytest.raises(TargetRefusedUs) as refused:
            ReadOnlyClient(four_oh_four).get("/anything")
        assert refused.value.status == 404


# ---------------------------------------------------------------------------
# V7 — THE STARTUP MESSAGE IS BUILT FROM THE MONITOR'S OWN CODE SET
# ---------------------------------------------------------------------------


def test_every_unknown_code_on_the_health_route_is_in_the_startup_message():
    """One enumeration. The message and the route cannot disagree.

    They did: `health()` synthesises an `unknown` entry for every `MonitorCode`
    with no subject yet, and the message walked a different structure that had
    none of them. The omitted ones were exactly this monitor's own blind spots
    -- with no platform declared, NOBODY is measuring whether a lane has gone
    quiet, and the one message whose stated purpose is "what does this monitor
    NOT know?" did not say so.

    Keyed on `(code, subject)`, because `platform_unreachable` and
    `lane_gone_quiet` are spelt the same on both surfaces and the first cut of
    this check read the LANE's copies as the monitor's own.
    """
    lane = ForeignLane()
    sink = RecordingSink()
    with serving(foreign_server(lane)) as url:
        monitor = monitor_for(config_for(lane=url), [sink])
        monitor.start()

    _subject, payload = sink.announced[0]
    announced = {(one["code"], one["subject"]) for one in payload["unmeasured"]}
    on_the_route = {
        (entry["code"], entry["subject"])
        for entry in monitor.health().to_dict()["codes"]
        if entry["state"] == "unknown"
    }

    assert on_the_route, "no monitor code is unknown, so this asserts nothing"
    assert on_the_route <= announced, (
        "unmeasured on the route and not in the startup message: "
        f"{sorted(on_the_route - announced)}"
    )


def test_the_startup_message_enumerates_the_monitors_own_code_set():
    """Derived from the ENUM, not from what happened to be observed.

    With only a lane declared, four of this monitor's own codes have no subject
    yet -- the platform it was not given, the identity service it was not given,
    the sink that has not failed, and the lane that nothing is watching for
    silence. Every one of them is a thing this monitor does not know.
    """
    lane = ForeignLane()
    sink = RecordingSink()
    with serving(foreign_server(lane)) as url:
        monitor = monitor_for(config_for(lane=url), [sink])
        monitor.start()

    _subject, payload = sink.announced[0]
    own = {
        one["code"]
        for one in payload["unmeasured"]
        if one["target"] == monitor.config.monitor_id
    }
    unknown_own = {
        entry["code"]
        for entry in monitor.health().to_dict()["codes"]
        if entry["state"] == "unknown"
    }
    assert own == unknown_own
    # And each one carries its source, which is what separates "waiting to be
    # read" from "nothing produces this at all".
    assert all(one["source"] for one in payload["unmeasured"])


def test_the_message_says_nothing_about_a_code_that_is_measured():
    """THE CONTROL: it is a list of what is NOT known, not a list of codes.

    A message that enumerated the whole enum regardless would satisfy the two
    tests above and would tell an operator nothing.
    """
    lane = ForeignLane()
    sink = RecordingSink()
    with serving(foreign_server(lane)) as url:
        monitor = monitor_for(config_for(lane=url), [sink])
        monitor.start()

    _subject, payload = sink.announced[0]
    announced = {(one["code"], one["subject"]) for one in payload["unmeasured"]}
    # `lane_unreachable` was MEASURED on the first poll: the lane answered.
    assert (MonitorCode.LANE_UNREACHABLE.value, "lane") not in announced
    assert (
        _states(monitor)[(MonitorCode.LANE_UNREACHABLE.value, "lane")] == "ok"
    )
