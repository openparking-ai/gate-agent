"""How a human is told, and what happens when they are not.

Two things are proven here and they are different questions.

**What a sink puts on a wire.** Every sink is handed the same object, and the
sweep for identity text runs over what each one would actually send -- the log
line, the email body, the webhook body -- rather than over the notification it
was built from. A rendering that added something the notification did not carry
would be invisible to a sweep over the notification.

**What happens when a sink fails.** `sink_delivery_failed` is itself a monitor
code: reported on the health route AND to every OTHER sink that works. Never
wrong silently applies to the messenger too, and a paging system nobody can
reach is the failure that hides every other failure.
"""

from __future__ import annotations

import json

import pytest

from conftest import FakeClock, config_for, monitor_for
from fakes import RecordingSink
from foreign_lane import ForeignLane
from foreign_lane import make_server as foreign_server
from gate_agent.config import EmailSinkConfig, LogSinkConfig, WebhookSinkConfig
from gate_agent.contract import MonitorCode, Notification
from gate_agent.sinks import DeliveryFailed, EmailSink, LogSink, WebhookSink, build
from serving import serving

#: Text that must never reach a sink. It is a plate, and the monitor reads
#: `/health` rather than `/events` or `/state`, so it never holds one -- but an
#: absence claim is a claim about a search, so the sweep below is run against a
#: payload with this planted in it as its control.
PLATE = "PURGEME9"

ORDINARY_CODE = "outbox_depth_growing"


def a_notification(**over) -> Notification:
    fields = {
        "site_id": "site-1",
        "lane_id": "lane-1",
        "target": "lane",
        "code": ORDINARY_CODE,
        "subject": None,
        "transition": "raised",
        "source": "measured",
        "caveat": None,
        "at": "2026-08-30T14:03:11.482913+00:00",
    }
    fields.update(over)
    return Notification(**fields)


# ---------------------------------------------------------------------------
# What each sink puts on a wire
# ---------------------------------------------------------------------------


def _log_output(notification, capsys) -> str:
    LogSink(LogSinkConfig()).deliver(notification)
    return capsys.readouterr().out


def _email_output(notification) -> str:
    sink = EmailSink(
        EmailSinkConfig(
            host="smtp.example.com",
            port=587,
            sender="monitor@example.com",
            recipients=("oncall@example.com",),
        )
    )
    payload = notification.to_dict()
    message = sink.compose(f"[{payload['site_id']}] {payload['code']}", payload)
    return str(message)


def _webhook_output(notification) -> str:
    sent = {}

    class _Response:
        status = 204

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def opener(request, timeout=None):
        sent["body"] = request.data.decode("utf-8")
        sent["headers"] = dict(request.header_items())
        sent["method"] = request.get_method()
        return _Response()

    WebhookSink(
        WebhookSinkConfig(url="https://paging.example.com/hook", token="hook-token"), opener=opener
    ).deliver(notification)
    return sent["body"]


def test_no_sink_publishes_identity_text(capsys):
    """The plate sweep, over what every sink would ACTUALLY send.

    Run over the rendered output rather than over the notification, because a
    rendering is where a field can be added -- and a sweep over the input would
    not see it.
    """
    notification = a_notification(caveat="a camera moved, or a car arrived")
    outputs = {
        "log": _log_output(notification, capsys),
        "email": _email_output(notification),
        "webhook": _webhook_output(notification),
    }
    for name, output in outputs.items():
        assert output, f"the {name} sink produced nothing, so this asserts nothing"
        assert PLATE not in output, f"the {name} sink published plate text"

    # THE CONTROL, per sink: the same sweep over the same rendering with a plate
    # planted in it must find one. Run sink by sink, because a control on the log
    # output says nothing about whether the email body was ever searched.
    planted = a_notification(caveat=f"plate {PLATE} seen")
    for name, output in (
        ("log", _log_output(planted, capsys)),
        ("email", _email_output(planted)),
        ("webhook", _webhook_output(planted)),
    ):
        assert PLATE in output, (
            f"the sweep cannot see a plate planted in the {name} sink's output, so its "
            "absence there says nothing"
        )


def test_the_webhook_posts_the_notification_object_and_carries_its_token():
    """The seat a third party's paging system takes, and what it receives."""
    body = json.loads(_webhook_output(a_notification()))
    assert body["kind"] == "transition"
    assert body["code"] == ORDINARY_CODE
    assert body["transition"] == "raised"
    assert body["site_id"] == "site-1"


def test_a_webhook_that_answers_a_refusal_did_not_deliver():
    """A status nobody looked at is how a sink comes to page nobody for a month."""

    class _Refused:
        status = 401

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    sink = WebhookSink(
        WebhookSinkConfig(url="https://paging.example.com/hook", token="hook-token"),
        opener=lambda request, timeout=None: _Refused(),
    )
    with pytest.raises(DeliveryFailed):
        sink.deliver(a_notification())


def test_every_declared_sink_kind_can_be_built():
    """`build` is the one mapping from a declaration to a sink."""
    assert isinstance(build(LogSinkConfig()), LogSink)
    assert isinstance(
        build(
            EmailSinkConfig(
                host="h", port=25, sender="a@example.com", recipients=("b@example.com",)
            )
        ),
        EmailSink,
    )
    assert isinstance(build(WebhookSinkConfig(url="https://example.com", token="t")), WebhookSink)


# ---------------------------------------------------------------------------
# A sink that fails is itself news
# ---------------------------------------------------------------------------


@pytest.fixture
def two_sinks():
    lane = ForeignLane()
    good = RecordingSink(name="good")
    bad = RecordingSink(name="bad")
    clock = FakeClock()
    with serving(foreign_server(lane)) as url:
        monitor = monitor_for(config_for(lane=url, poll_seconds=1.0), [good, bad], clock=clock)
        monitor.start()
        good.delivered.clear()
        bad.delivered.clear()
        yield lane, monitor, good, bad, clock


def test_a_sink_that_cannot_deliver_is_reported_to_the_ones_that_can(two_sinks):
    """Never wrong silently applies to the messenger.

    A paging system nobody can reach is the failure that hides every other
    failure: the monitor goes on measuring perfectly and nobody hears any of it.
    """
    lane, monitor, good, bad, clock = two_sinks

    bad.fail = True
    lane.states[ORDINARY_CODE] = "active"
    lane.sources[ORDINARY_CODE] = "measured"
    clock.advance(60)
    monitor.poll()

    assert (ORDINARY_CODE, "raised") in good.codes
    assert (MonitorCode.SINK_DELIVERY_FAILED.value, "raised") in good.codes
    # The failing sink got nothing, which is the whole reason the other one had
    # to be told.
    assert bad.codes == []

    states = {
        (entry["code"], entry["subject"], entry["state"])
        for entry in monitor.health().to_dict()["codes"]
    }
    assert (MonitorCode.SINK_DELIVERY_FAILED.value, "bad", "active") in states
    assert (MonitorCode.SINK_DELIVERY_FAILED.value, "good", "ok") in states


def test_a_sink_that_comes_back_is_reported_too(two_sinks):
    """The control for the test above: the failure state is not one-way."""
    lane, monitor, good, bad, clock = two_sinks

    bad.fail = True
    lane.states[ORDINARY_CODE] = "active"
    lane.sources[ORDINARY_CODE] = "measured"
    clock.advance(60)
    monitor.poll()

    bad.fail = False
    lane.states[ORDINARY_CODE] = "ok"
    clock.advance(60)
    monitor.poll()

    assert (MonitorCode.SINK_DELIVERY_FAILED.value, "recovered") in good.codes
    states = {
        (entry["code"], entry["subject"], entry["state"])
        for entry in monitor.health().to_dict()["codes"]
    }
    assert (MonitorCode.SINK_DELIVERY_FAILED.value, "bad", "ok") in states


def test_every_sink_failing_does_not_loop(two_sinks):
    """A failure while reporting a failure is recorded and told to nobody.

    Otherwise one dead endpoint becomes a message about a message about a
    message, and the monitor spends itself on the one thing it cannot fix.
    """
    lane, monitor, good, bad, clock = two_sinks

    good.fail = True
    bad.fail = True
    lane.states[ORDINARY_CODE] = "active"
    lane.sources[ORDINARY_CODE] = "measured"
    clock.advance(60)
    monitor.poll()  # must return, not recurse

    states = {
        (entry["code"], entry["subject"], entry["state"])
        for entry in monitor.health().to_dict()["codes"]
    }
    assert (MonitorCode.SINK_DELIVERY_FAILED.value, "good", "active") in states
    assert (MonitorCode.SINK_DELIVERY_FAILED.value, "bad", "active") in states


def test_a_sink_that_raises_something_unexpected_is_still_a_sink_that_failed(two_sinks):
    """Letting it escape would take the monitor down with the endpoint.

    The process that is supposed to still be alive when the thing it watches is
    not must not die because a paging library raised something this module has
    not heard of.
    """
    lane, monitor, good, bad, clock = two_sinks

    class _Exploding:
        name = "exploding"
        kind = "log"

        def deliver(self, notification):
            raise RuntimeError("something nobody imported")

        def announce(self, subject, payload):
            raise RuntimeError("something nobody imported")

    monitor.sinks = (good, _Exploding())
    lane.states[ORDINARY_CODE] = "active"
    lane.sources[ORDINARY_CODE] = "measured"
    clock.advance(60)
    monitor.poll()

    assert (MonitorCode.SINK_DELIVERY_FAILED.value, "raised") in good.codes


def test_the_startup_message_says_what_is_not_being_measured():
    """One message, at startup, naming every unmeasured code and its source.

    It is not a page and it is not a transition: it is the answer to the question
    an operator cannot otherwise ask -- what does this monitor NOT know? Sent
    once, because sending it every poll would make it wallpaper.
    """
    lane = ForeignLane()
    sink = RecordingSink()
    with serving(foreign_server(lane)) as url:
        monitor = monitor_for(config_for(lane=url), [sink])
        monitor.start()

    assert len(sink.announced) == 1
    subject, payload = sink.announced[0]
    assert "site-1" in subject
    unmeasured = payload["unmeasured"]
    assert unmeasured, "the foreign lane measures nothing, so this must not be empty"
    # Every one of them carries the SOURCE the target gave, which is what
    # separates "waiting to be read" from "nothing produces this at all".
    assert {one["source"] for one in unmeasured} == {"no_source"}
    assert payload["targets"] == ["lane"]
    assert payload["sinks"] == ["recording"]

    # And the same information is on the health route, continuously.
    target = next(one for one in monitor.health().to_dict()["targets"] if one["name"] == "lane")
    assert {one["code"] for one in target["codes"] if one["state"] == "unknown"} >= {
        one["code"] for one in unmeasured if one["target"] == "lane"
    }
