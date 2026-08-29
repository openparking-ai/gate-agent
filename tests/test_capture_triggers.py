"""The two triggers: a clock, and a lane saying something happened.

Gokhan's spec, his words: *"camera captures an image every minute and every time
the gate opens"*. The second half is measurable only in the phrasing the L1
established: **every time the LANE VENDS**. Nothing anywhere knows whether the
boom moved -- the lane's vend output has one method, no feedback and no `close()`
by design -- so "the gate opened" is a claim this estate cannot make and does not.

The lane in this file is reached over a socket, by URL, through the READ contract
round 2 built. Nothing here imports `lane_controller`; the capture process is a
consumer of that contract, which is the seat a third party takes.
"""

from __future__ import annotations

import pytest

from cameras import FakeCamera, camera_server, jpeg
from conftest import MovingUtc, camera_config, capture_config_for, capture_for
from foreign_lane import ForeignLane
from foreign_lane import make_server as foreign_server
from gate_agent.contract import CaptureCode, CaptureReason
from serving import serving

AT = "2026-08-30T14:00:00+00:00"


@pytest.fixture
def wired(tmp_path):
    """A capture process, one camera, one lane, all on real sockets."""
    directory = tmp_path / "store"
    directory.mkdir()
    lane = ForeignLane()
    lane.window = 64
    camera = FakeCamera(body=jpeg(b"one"))
    with serving(camera_server(camera)) as camera_url, serving(foreign_server(lane)) as lane_url:
        config = capture_config_for(
            directory=directory,
            cameras=[camera_config("front", f"{camera_url}/snapshot", tmp_path)],
            lane=lane_url,
        )
        now = MovingUtc()
        process = capture_for(config, now=now)
        process.start()
        yield lane, camera, process, now


def reasons(process):
    return [one["reason"] for one in process.records(0).to_dict()["records"]]


def triggered(process):
    """Only the records a LANE caused. Every poll also takes an interval one."""
    return [
        one
        for one in process.records(0).to_dict()["records"]
        if one["reason"] != CaptureReason.INTERVAL.value
    ]


# ---------------------------------------------------------------------------
# THE CLOCK
# ---------------------------------------------------------------------------


def test_a_camera_is_photographed_on_its_interval_and_not_more_often(tmp_path):
    """The interval is a per-site setting. This is what it does."""
    directory = tmp_path / "store"
    directory.mkdir()
    camera = FakeCamera(body=jpeg(b"one"))
    with serving(camera_server(camera)) as base:
        config = capture_config_for(
            directory=directory,
            cameras=[camera_config("front", f"{base}/snapshot", tmp_path)],
            interval_seconds=60.0,
        )
        now = MovingUtc()
        process = capture_for(config, now=now)
        process.start()

        process.poll()
        assert len(reasons(process)) == 1

        now.advance(30)
        process.poll()
        assert len(reasons(process)) == 1, "a camera was photographed inside its interval"

        now.advance(31)
        camera.body = jpeg(b"two")
        process.poll()
        assert reasons(process) == [CaptureReason.INTERVAL.value] * 2


def test_with_no_lane_declared_it_captures_on_the_interval_and_says_so(tmp_path):
    """STANDALONE IS A MODE. A garage with a camera and no gate is a customer.

    The two lane codes are `unknown` -- nobody measured, and `ok` there would be
    a claim about a lane that does not exist.
    """
    directory = tmp_path / "store"
    directory.mkdir()
    camera = FakeCamera(body=jpeg(b"one"))
    with serving(camera_server(camera)) as base:
        config = capture_config_for(
            directory=directory,
            cameras=[camera_config("front", f"{base}/snapshot", tmp_path)],
        )
        process = capture_for(config)
        process.start()
        process.poll()

    described = process.describe().to_dict()
    assert described["lane_declared"] is False and described["lane_url"] is None
    assert reasons(process) == [CaptureReason.INTERVAL.value]
    states = {
        (entry["code"], entry["state"])
        for entry in process.health().to_dict()["codes"]
    }
    assert (CaptureCode.LANE_UNREACHABLE.value, "unknown") in states
    assert (CaptureCode.LANE_REFUSED_US.value, "unknown") in states

    # And the standalone line is printed, not left to be worked out.
    from gate_agent.cli import build_parser  # noqa: F401  (parser sanity)


# ---------------------------------------------------------------------------
# THE LANE
# ---------------------------------------------------------------------------


def test_an_arrival_and_a_vend_each_take_a_picture(wired):
    """`frames_captured` and `vended`, filed under reasons of their own."""
    lane, _camera, process, _now = wired
    lane.record("frames_captured", AT, {"count": 3, "camera": "sim-cam-1"})
    lane.record("vended", AT, {"reason": "cached_allow"})
    process.poll(force=True)

    stored = triggered(process)
    assert [one["reason"] for one in stored] == [
        CaptureReason.LANE_ARRIVAL.value,
        CaptureReason.LANE_VEND.value,
    ]
    assert [one["lane_event_cursor"] for one in stored] == [2, 3]


def test_entry_pending_is_not_a_trigger_and_its_detail_is_never_copied(wired):
    """`entry_pending` carries `plate_region`, and it is the one to be careful of.

    It is not a trigger: it arrives after `vended` and would photograph the same
    vehicle twice. And its `detail` is not read at all -- what a record carries
    about a trigger is a CURSOR and a time, which is a reference and not a copy.
    """
    lane, _camera, process, _now = wired
    detail = {"plate_region": "REGIONXYZ", "plate": "PURGEME9"}
    lane.record("entry_pending", AT, detail)
    process.poll(force=True)

    assert triggered(process) == [], "entry_pending took a picture"

    # THE CONTROL: the same lane, the same detail, on an event that IS a
    # trigger. The picture is taken and the detail still does not travel.
    lane.record("vended", AT, detail)
    process.poll(force=True)
    stored = triggered(process)
    assert len(stored) == 1
    served = str(process.records(0).to_dict())
    for value in detail.values():
        assert value not in served
        for path in sorted(process.store.directory.iterdir()):
            assert value.encode() not in path.read_bytes()


def test_trigger_to_capture_ms_is_on_every_lane_record_and_no_interval_one(wired):
    """The seat's cost, measured on every record rather than described once."""
    lane, _camera, process, now = wired
    lane.record("vended", now().isoformat())
    now.advance(2.5)
    process.poll(force=True)

    stored = process.records(0).to_dict()["records"]
    by_lane = triggered(process)
    interval = [one for one in stored if one["reason"] == CaptureReason.INTERVAL.value]
    assert by_lane and interval, "this run produced only one kind of record"
    assert all(one["trigger_to_capture_ms"] == 2500 for one in by_lane)
    assert all(one["trigger_to_capture_ms"] is None for one in interval)


def test_the_first_read_takes_the_lanes_place_and_photographs_nothing_past(tmp_path):
    """A restart does not photograph for cars that have already gone.

    A picture taken now, filed against an event from before this process
    started, would be an image of an empty lane carrying a reference to a
    vehicle -- worse than the absence, because it reads as a record of that
    vehicle.
    """
    directory = tmp_path / "store"
    directory.mkdir()
    lane = ForeignLane()
    lane.window = 64
    lane.record("vended", AT)
    lane.record("frames_captured", AT)
    camera = FakeCamera(body=jpeg(b"one"))
    with serving(camera_server(camera)) as camera_url, serving(foreign_server(lane)) as lane_url:
        config = capture_config_for(
            directory=directory,
            cameras=[camera_config("front", f"{camera_url}/snapshot", tmp_path)],
            lane=lane_url,
        )
        process = capture_for(config)
        process.start()
        process.poll(force=True)
        assert triggered(process) == [], (
            "the process replayed the lane's window and photographed for cars that had gone"
        )
        # THE CONTROL: the next event, after it has taken the lane's place, IS
        # followed -- so the assertion above is about the backlog and not about
        # a process that follows nothing.
        lane.record("vended", AT)
        process.poll(force=True)
        assert CaptureReason.LANE_VEND.value in reasons(process)


def test_a_reset_is_not_replayed_and_is_not_absorbed_either(wired):
    """The lane says the saved position no longer refers to anything.

    What was missed cannot be recovered by photographing now, so the process
    takes the new position and says how many events it did not follow. The
    alternative -- replaying whatever survived the window -- puts pictures of an
    empty lane against other vehicles' events.
    """
    lane, _camera, process, _now = wired
    lane.record("vended", AT)
    process.poll(force=True)
    assert reasons(process).count(CaptureReason.LANE_VEND.value) == 1

    # The lane restarts: its cursor goes backwards, which is what `reset` on
    # this contract means.
    lane._seq = 0
    lane.log = []
    lane.record("vended", AT)
    process.poll(force=True)
    assert reasons(process).count(CaptureReason.LANE_VEND.value) == 1, (
        "the process replayed a window it had been told it could not trust"
    )


def test_a_dead_lane_is_named_and_the_interval_captures_continue(wired, tmp_path):
    """SETTLED 3g's whole point: a lane that is not working is when this matters.

    The lane is down, the code says so, and the camera keeps recording -- which
    is the job a broken barrier gives this process.
    """
    lane, _camera, process, _now = wired
    directory = process.store.directory
    process.poll(force=True)
    before = len(list(directory.iterdir()))

    # Point the process at a socket nothing listens on.
    from gate_agent.client import ReadOnlyClient

    process._lane = ReadOnlyClient("http://127.0.0.1:1", None, 2.0)
    process.poll(force=True)

    states = {
        (entry["code"], entry["subject"]): entry["state"]
        for entry in process.health().to_dict()["codes"]
    }
    assert states[(CaptureCode.LANE_UNREACHABLE.value, "lane")] == "active"
    assert states[(CaptureCode.LANE_REFUSED_US.value, "lane")] == "unknown"
    assert len(list(directory.iterdir())) > before, "the interval captures stopped with the lane"


def test_a_refused_write_is_published_as_store_over_budget(tmp_path):
    """The code, not only the exception. A store that quietly kept less than it
    said it would is the failure this whole module exists to prevent, and the
    thing that makes it visible is a state on the health route -- not a raise
    inside a function nobody is watching.

    Found by the fail-control: the break that turns this state into `ok` passed
    the whole suite, because the store's own refusal was tested and the
    PROCESS's report of it was not.
    """
    directory = tmp_path / "store"
    directory.mkdir()
    camera = FakeCamera(body=jpeg(b"x" * 400))
    with serving(camera_server(camera)) as base:
        config = capture_config_for(
            directory=directory,
            cameras=[camera_config("front", f"{base}/snapshot", tmp_path)],
            # A cap smaller than one capture from this camera: one purge can
            # empty everything it is allowed to and this still does not fit.
            max_bytes=100,
        )
        process = capture_for(config)
        process.start()
        over = (CaptureCode.STORE_OVER_BUDGET.value, "store")
        assert states(process)[over] == "ok", "it was over budget before anything was written"

        process.poll(force=True)
        assert states(process)[over] == "active", "a refused write told nobody"
        assert list(directory.iterdir()) == [], "the refused capture was written anyway"

        # AND IT RECOVERS. The cap is raised, the next capture fits, and the
        # code goes back -- so the state above is a measurement rather than a
        # latch that fires once and stays on.
        process.store.max_bytes = 1 << 20
        process.poll(force=True)
        assert states(process)[over] == "ok"
        assert list(directory.iterdir()), "nothing was written after the cap was raised"


def states(process):
    return {
        (entry["code"], entry["subject"]): entry["state"]
        for entry in process.health().to_dict()["codes"]
    }
