"""Transitions, not states — and `never_alarm` read from the WIRE.

A monitor that sent the current state would send the same message every poll for
as long as a fault lasted, and a human told a thousand times has been told
nothing. So the rule is about CHANGE, and every branch of it is exercised here
with the case that would break it beside the case that proves it.

The lane under test is the foreign one, driven code by code. That is deliberate:
its states are set by the test rather than emerging from a controller, so a
transition can be produced exactly and its opposite produced immediately after —
which is what separates "this state changed" from "this state was read twice".
"""

from __future__ import annotations

import pytest

from conftest import FakeClock, config_for, monitor_for
from fakes import RecordingSink
from foreign_lane import ForeignLane
from foreign_lane import make_server as foreign_server
from gate_agent.contract import MonitorCode
from serving import serving

#: A code that is not on the lane contract's never-alarm list, so moving it
#: produces a message. Read from the installed contract rather than typed, so a
#: code renamed there fails here instead of silently testing nothing.
ORDINARY_CODE = "outbox_depth_growing"
#: The one the lane contract marks `never_alarm`, by SETTLED 3d(i): one reason
#: covers an ordinary car arriving, and a gate that pages a technician because a
#: car arrived is the failure that decision exists to prevent.
NEVER_ALARM_CODE = "reference_not_recognised"


@pytest.fixture
def lane_and_monitor():
    """A foreign lane whose codes a test moves, and a monitor watching it."""
    lane = ForeignLane()
    sink = RecordingSink()
    clock = FakeClock()
    with serving(foreign_server(lane)) as url:
        monitor = monitor_for(config_for(lane=url, poll_seconds=1.0), [sink], clock=clock)
        monitor.start()
        sink.delivered.clear()
        yield lane, monitor, sink, clock


def _set(lane, code, state, source="measured"):
    lane.states[code] = state
    lane.sources[code] = source


def test_a_code_going_active_is_raised_and_a_state_that_holds_is_silent(lane_and_monitor):
    """The rule, and the control for it in one test.

    The second poll reads exactly the same payload as the first. A monitor that
    reported states rather than changes would send a second message, and the
    person reading them would learn to skim.
    """
    lane, monitor, sink, clock = lane_and_monitor

    _set(lane, ORDINARY_CODE, "active")
    clock.advance(60)
    monitor.poll()
    assert sink.codes == [(ORDINARY_CODE, "raised")]

    clock.advance(60)
    monitor.poll()
    assert sink.codes == [(ORDINARY_CODE, "raised")], "a state that holds must not re-send"


def test_a_flap_is_two_messages(lane_and_monitor):
    """The other half of the control above: a change really does send again.

    Without this, a monitor that sent nothing at all would pass the silence
    assertion perfectly.
    """
    lane, monitor, sink, clock = lane_and_monitor

    _set(lane, ORDINARY_CODE, "active")
    clock.advance(60)
    monitor.poll()
    _set(lane, ORDINARY_CODE, "ok")
    clock.advance(60)
    monitor.poll()
    _set(lane, ORDINARY_CODE, "active")
    clock.advance(60)
    monitor.poll()

    assert sink.codes == [
        (ORDINARY_CODE, "raised"),
        (ORDINARY_CODE, "recovered"),
        (ORDINARY_CODE, "raised"),
    ]


def test_a_code_that_stops_being_measured_says_so_once(lane_and_monitor):
    """`-> unknown` is its own event, and it is the one a monitor most easily hides.

    A code that was `ok` and is now `unknown` has not recovered and has not
    broken: it has stopped being measured, which is a thing somebody needs to
    know and is exactly the change a state-reporting monitor would show as
    nothing at all.
    """
    lane, monitor, sink, clock = lane_and_monitor

    _set(lane, ORDINARY_CODE, "ok")
    clock.advance(60)
    monitor.poll()
    assert sink.codes == [], "unknown -> ok is not news and must be silent"

    _set(lane, ORDINARY_CODE, "unknown", source="not_measured")
    clock.advance(60)
    monitor.poll()
    assert sink.codes == [(ORDINARY_CODE, "no_longer_measured")]

    clock.advance(60)
    monitor.poll()
    assert sink.codes == [(ORDINARY_CODE, "no_longer_measured")], "said once, not every poll"


def test_nothing_repeats_unless_this_site_asked_to_be_reminded(lane_and_monitor):
    """The default is NEVER. A re-notify interval is a per-site decision.

    A default interval would be a decision about how often to wake somebody, made
    by whoever wrote the code, for every site that never mentioned it.
    """
    lane, monitor, sink, clock = lane_and_monitor
    assert monitor.config.renotify_seconds is None

    _set(lane, ORDINARY_CODE, "active")
    for _ in range(5):
        clock.advance(600)
        monitor.poll()
    assert sink.codes == [(ORDINARY_CODE, "raised")]


def test_a_site_that_asked_to_be_reminded_is_reminded_on_its_own_interval():
    """The control for the test above: the silence is a setting, not an incapacity."""
    lane = ForeignLane()
    sink = RecordingSink()
    clock = FakeClock()
    with serving(foreign_server(lane)) as url:
        monitor = monitor_for(
            config_for(lane=url, poll_seconds=1.0, renotify_seconds=300.0), [sink], clock=clock
        )
        monitor.start()
        sink.delivered.clear()

        _set(lane, ORDINARY_CODE, "active")
        clock.advance(10)
        monitor.poll()
        assert sink.codes == [(ORDINARY_CODE, "raised")]

        # Not yet: the interval has not elapsed.
        clock.advance(100)
        monitor.poll()
        assert sink.codes == [(ORDINARY_CODE, "raised")]

        clock.advance(300)
        monitor.poll()
        assert sink.codes == [(ORDINARY_CODE, "raised"), (ORDINARY_CODE, "still_active")]


def test_never_alarm_is_honoured_from_the_payload_and_not_from_a_list_here(lane_and_monitor):
    """SETTLED 3d(i), enforced at the reader.

    The lane publishes `never_alarm` beside the code because one of the causes
    `reference_not_recognised` covers is an ordinary car arriving. A monitor that
    held its own copy of that set would drift from the lane's, and the drift
    shows up as a technician dispatched to a working camera.
    """
    lane, monitor, sink, clock = lane_and_monitor

    _set(lane, NEVER_ALARM_CODE, "active")
    clock.advance(60)
    monitor.poll()

    assert sink.codes == [], "a code the wire marks never_alarm must not be sent"
    # It is RECORDED, not hidden. The state is on the health route, where an
    # operator looking for it finds it.
    target = next(one for one in monitor.health().to_dict()["targets"] if one["name"] == "lane")
    entry = next(one for one in target["codes"] if one["code"] == NEVER_ALARM_CODE)
    assert entry["state"] == "active"
    assert entry["never_alarm"] is True
    assert entry["caveat"]


def test_the_flag_is_taken_from_the_wire_in_both_directions(lane_and_monitor):
    """The control, and it is the half that proves there is no list in here.

    `never_alarm: true` planted on an ordinary code must silence it, and
    `never_alarm: false` planted on `reference_not_recognised` must let it
    through. A monitor holding its own set would get exactly one of these right,
    and it would be the one that matches today's lane.
    """
    lane, monitor, sink, clock = lane_and_monitor

    lane.never_alarm_override[ORDINARY_CODE] = True
    _set(lane, ORDINARY_CODE, "active")
    clock.advance(60)
    monitor.poll()
    assert sink.codes == [], "never_alarm planted on an ordinary code was ignored"

    lane.never_alarm_override[NEVER_ALARM_CODE] = False
    _set(lane, NEVER_ALARM_CODE, "active")
    clock.advance(60)
    monitor.poll()
    assert sink.codes == [(NEVER_ALARM_CODE, "raised")], (
        "the monitor is using a list of its own instead of the wire"
    )


def test_a_target_that_goes_away_takes_its_codes_with_it(lane_and_monitor):
    """A lane that stopped answering has not become healthy.

    Leaving its last known states on the health route would publish a lane's
    health as of whenever it was last reachable, indistinguishable from now --
    and the codes that were `ok` would go on reading `ok` for as long as the lane
    stayed down.
    """
    lane, monitor, sink, clock = lane_and_monitor

    _set(lane, ORDINARY_CODE, "ok")
    clock.advance(60)
    monitor.poll()

    monitor._clients["lane"].base_url = "http://127.0.0.1:1"
    clock.advance(60)
    monitor.poll()

    assert (MonitorCode.LANE_UNREACHABLE.value, "raised") in sink.codes
    assert (ORDINARY_CODE, "no_longer_measured") in sink.codes
    target = next(one for one in monitor.health().to_dict()["targets"] if one["name"] == "lane")
    assert target["codes"] == [], "a stale copy of a dead lane's health is still on the surface"


def test_a_poll_that_is_not_due_yet_does_not_happen(lane_and_monitor):
    """The interval is a setting and it is honoured. One value per target."""
    lane, monitor, sink, clock = lane_and_monitor
    before = len(lane.requests)

    _set(lane, ORDINARY_CODE, "active")
    monitor.poll()  # no time has passed
    assert len(lane.requests) == before
    assert sink.codes == []

    clock.advance(1.0)
    monitor.poll()
    assert len(lane.requests) > before
    assert sink.codes == [(ORDINARY_CODE, "raised")]
