"""The store: what is on the disk, what the rule deletes, and what a restart sees.

This is the first durable thing in this package, and every test here is against a
real directory. Nothing is mocked: the records are written, the process is thrown
away, a new one is built on the same directory, and it is asked what it has.

The images are SYNTHETIC bytes built in the test. There is no fixture image in
this repository and `check-no-real-data.js` refuses one.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from cameras import jpeg
from gate_agent.store import (
    IMAGE_SUFFIX,
    SIDECAR_FIELDS,
    SIDECAR_SUFFIX,
    TEMP_PREFIX,
    CaptureStore,
    StoreOverBudget,
    StoreUnwritable,
)

START = datetime.fromisoformat("2026-08-30T14:00:00+00:00")


def store_at(tmp_path, retention_days=30, max_bytes=1 << 20, now=None):
    """A real store on a real directory, on a clock the test holds still.

    The clock is passed rather than left as the wall clock because the retention
    window is measured against it: a store on the real clock would delete a
    record dated relative to `START` or not, depending on what day the suite is
    run, which is a test that measures the calendar.
    """
    directory = tmp_path / "store"
    directory.mkdir(exist_ok=True)
    store = CaptureStore(directory, retention_days, max_bytes, now=now or (lambda: START))
    store.open()
    return store


def write(store, at, camera="front", body=b"x" * 64, **kwargs):
    return store.write(jpeg(body), camera_id=camera, reason="interval", captured_at=at, **kwargs)


# ---------------------------------------------------------------------------
# OPENING, AND WHAT IS REFUSED
# ---------------------------------------------------------------------------


def test_a_directory_that_is_not_there_is_refused_and_not_created(tmp_path):
    """A path that is not there is a typo or a disk that did not mount.

    Creating it puts a site's captures on the root filesystem of the box the
    lane runs on, which fills the disk the lane is running from -- and the store
    that was supposed to hold them is still empty and still not mounted.
    """
    missing = tmp_path / "not-mounted"
    with pytest.raises(StoreUnwritable, match="is not a directory"):
        CaptureStore(missing, 30, 1 << 20).open()
    assert not missing.exists(), "the store created the directory it was refusing"


def test_a_directory_that_will_not_take_a_write_is_refused_at_startup(tmp_path):
    """Asked of the DISK, not of the permission bits.

    `os.access` answers for the wrong thing on a read-only mount, a full disk
    and a directory with an ACL. This writes a byte and deletes it.
    """
    directory = tmp_path / "store"
    directory.mkdir()
    directory.chmod(0o500)
    try:
        with pytest.raises(StoreUnwritable, match="will not take a write"):
            CaptureStore(directory, 30, 1 << 20).open()
    finally:
        directory.chmod(0o700)
    # The control: the same directory, writable, opens.
    CaptureStore(directory, 30, 1 << 20).open()


# ---------------------------------------------------------------------------
# THE RECORD, AND WHAT IT DOES NOT CARRY
# ---------------------------------------------------------------------------


def test_a_record_is_a_jpeg_as_received_and_seven_fields(tmp_path):
    """Never re-encoded: the size measured is the camera's, not this package's."""
    store = store_at(tmp_path)
    image = jpeg(b"the camera's own bytes")
    record = store.write(image, camera_id="front", reason="interval", captured_at=START)

    assert record.image_path.read_bytes() == image
    assert record.bytes == len(image)
    sidecar = json.loads(
        (store.directory / f"{record.id}{SIDECAR_SUFFIX}").read_text(encoding="utf-8")
    )
    assert set(sidecar) == set(SIDECAR_FIELDS), (
        "the sidecar's fields are the contract's seven and nothing else -- a field added here "
        "is a field about a vehicle unless somebody argued for it"
    )


def test_a_records_name_is_a_timestamp_a_camera_and_a_sequence(tmp_path):
    """A directory listing is readable by anyone who can read the directory.

    A filename is also the one part of a file that survives being copied
    somewhere with no context, so a plate in one would be an identity leaving
    this store in the one field that cannot be stripped.
    """
    store = store_at(tmp_path)
    record = store.write(
        jpeg(), camera_id="front", reason="lane_vend", captured_at=START,
        lane_event_cursor=7, lane_event_at="2026-08-30T14:00:00+00:00",
    )
    assert record.id == "20260830T140000000Z_front_000001"
    assert "lane_vend" not in record.id and "7" not in record.id.split("_")[1]


def test_trigger_to_capture_ms_is_the_delay_and_is_absent_without_a_trigger(tmp_path):
    """THE COST OF THE SEAT, on every record rather than in a sentence.

    This process learns that a car arrived by POLLING the lane's read contract,
    so the picture is taken when the event was SEEN. That delay is a per-site
    number and it is published per record.
    """
    store = store_at(tmp_path)
    triggered = store.write(
        jpeg(), camera_id="front", reason="lane_arrival",
        captured_at=START + timedelta(milliseconds=1400),
        lane_event_cursor=7, lane_event_at=START.isoformat(),
    )
    assert triggered.trigger_to_capture_ms == 1400

    interval = store.write(jpeg(), camera_id="front", reason="interval", captured_at=START)
    assert interval.trigger_to_capture_ms is None
    assert interval.lane_event_cursor is None and interval.lane_event_at is None


# ---------------------------------------------------------------------------
# ATOMICITY, AND WHAT A CRASH LEAVES
# ---------------------------------------------------------------------------


def test_a_record_is_never_half_written_under_its_real_name(tmp_path, monkeypatch):
    """Killed between the temporary file and the rename: NO orphan at all.

    The window this design cannot close is one `rename` wide -- between the
    image's rename and the sidecar's -- and that case is the next test. Before
    the first rename there is nothing under a record's name to find.
    """
    store = store_at(tmp_path)

    import os as os_module

    def die(*_args, **_kwargs):
        raise OSError("killed between the temp file and the rename")

    monkeypatch.setattr(os_module, "replace", die)
    with pytest.raises(StoreUnwritable):
        store.write(jpeg(), camera_id="front", reason="interval", captured_at=START)
    monkeypatch.undo()

    left = sorted(path.name for path in store.directory.iterdir())
    assert left == [], f"a crash left something behind: {left}"
    # The control: the same write, uninterrupted, leaves exactly two files.
    store.write(jpeg(), camera_id="front", reason="interval", captured_at=START)
    assert len(list(store.directory.iterdir())) == 2


def test_an_orphan_is_reported_and_purged_never_silently_kept(tmp_path):
    """The one-rename window, produced on purpose, and what a rebuild does.

    A half record kept is a photograph nobody can say anything about -- not when
    it was taken, not by which camera -- under a retention rule that CANNOT
    REACH IT, because the rule reads the sidecar.
    """
    store = store_at(tmp_path)
    record = store.write(jpeg(), camera_id="front", reason="interval", captured_at=START)
    (store.directory / f"{record.id}{SIDECAR_SUFFIX}").unlink()

    reopened = CaptureStore(store.directory, 30, 1 << 20)
    reopened.open()
    assert reopened.incomplete == (record.id,), "the orphan was not reported"
    assert not record.image_path.exists(), "the orphan was kept"
    assert reopened.records() == ()

    # THE OTHER HALF: a sidecar whose image is gone is the same fault.
    second = store.write(jpeg(), camera_id="front", reason="interval", captured_at=START)
    second.image_path.unlink()
    again = CaptureStore(store.directory, 30, 1 << 20)
    again.open()
    assert again.incomplete == (second.id,)
    assert not (store.directory / f"{second.id}{SIDECAR_SUFFIX}").exists()


def test_a_sidecar_that_cannot_be_read_is_half_a_record(tmp_path):
    """A sidecar missing a field is not interpreted generously."""
    store = store_at(tmp_path)
    record = store.write(jpeg(), camera_id="front", reason="interval", captured_at=START)
    sidecar = store.directory / f"{record.id}{SIDECAR_SUFFIX}"
    body = json.loads(sidecar.read_text(encoding="utf-8"))
    del body["captured_at"]
    sidecar.write_text(json.dumps(body), encoding="utf-8")

    reopened = CaptureStore(store.directory, 30, 1 << 20)
    reopened.open()
    assert reopened.incomplete == (record.id,)


def test_a_half_written_temporary_file_is_not_a_record(tmp_path):
    """A crash before either rename leaves files the index ignores.

    They are also NOT reported as incomplete: nothing was ever a record, so
    there is nothing to report -- an orphan is a record that lost a half.
    """
    store = store_at(tmp_path)
    (store.directory / f"{TEMP_PREFIX}20260830T140000000Z_front_000009{IMAGE_SUFFIX}").write_bytes(
        jpeg()
    )
    reopened = CaptureStore(store.directory, 30, 1 << 20)
    reopened.open()
    assert reopened.records() == ()
    assert reopened.incomplete == ()


# ---------------------------------------------------------------------------
# A RESTART REBUILDS THE INDEX FROM THE DISK
# ---------------------------------------------------------------------------


def test_a_restart_rebuilds_the_index_by_reading_the_directory(tmp_path):
    """A CHECK, NEVER A MEMORY. There is no manifest to go stale.

    Written by one store, served by another built on the same directory with no
    state carried between them -- which is what a restart is.
    """
    store = store_at(tmp_path)
    written = [write(store, START + timedelta(minutes=n)) for n in range(3)]

    reopened = CaptureStore(store.directory, 30, 1 << 20)
    reopened.open()
    assert [record.id for _cursor, record in reopened.records()] == [one.id for one in written]
    assert reopened.get(written[1].id).bytes == written[1].bytes
    assert reopened.bytes_used() == sum(one.bytes for one in written)

    # And a file deleted by hand is simply not in the index -- nothing here
    # remembers it, so there is nothing to be wrong about.
    written[0].image_path.unlink()
    (store.directory / f"{written[0].id}{SIDECAR_SUFFIX}").unlink()
    third = CaptureStore(store.directory, 30, 1 << 20)
    third.open()
    assert [record.id for _cursor, record in third.records()] == [one.id for one in written[1:]]
    assert third.incomplete == ()


# ---------------------------------------------------------------------------
# THE RETENTION RULE, AND THE PURGE THAT DELETES
# ---------------------------------------------------------------------------


def test_the_purge_deletes_by_age_and_the_control_is_that_size_did_nothing(tmp_path):
    """Older than `retention_days` goes, whatever the disk has room for."""
    now = [START]
    store = store_at(tmp_path, retention_days=2, max_bytes=1 << 30, now=lambda: now[0])
    old = write(store, START)
    kept = write(store, START + timedelta(days=3))

    # Time passes. Nothing here shortens a window or moves a record: the
    # records stayed where they were and the clock moved past one of them,
    # which is the only way a retention rule ever fires in life.
    now[0] = START + timedelta(days=3)
    by_age, by_size = store.purge()
    assert (by_age, by_size) == (1, 0), "size purged something, so age is not what was measured"
    assert not old.image_path.exists()
    assert kept.image_path.exists()
    assert store.purged_by_age == 1 and store.purged_by_size == 0


def test_the_purge_deletes_by_size_oldest_first_and_age_did_nothing(tmp_path):
    """The cap, applied to what the retention window left, oldest first."""
    one = jpeg(b"x" * 200)
    store = store_at(tmp_path, retention_days=3650, max_bytes=len(one) * 2)
    first = write(store, START, body=b"x" * 200)
    second = write(store, START + timedelta(minutes=1), body=b"x" * 200)
    third = write(store, START + timedelta(minutes=2), body=b"x" * 200)

    assert not first.image_path.exists(), "the oldest was not the one evicted"
    assert second.image_path.exists() and third.image_path.exists()
    assert store.purged_by_size == 1 and store.purged_by_age == 0, (
        "age purged something, so size is not what was measured"
    )


def test_a_write_that_one_purge_cannot_make_room_for_is_refused_and_named(tmp_path):
    """`store_over_budget`: the write does not happen and it is not quiet.

    A store that wrote it anyway and let the next purge delete the oldest would
    be a recording missing exactly the busiest hour, with nothing saying so.
    """
    store = store_at(tmp_path, max_bytes=100)
    with pytest.raises(StoreOverBudget, match="one purge could not get"):
        store.write(jpeg(b"x" * 500), camera_id="front", reason="interval", captured_at=START)
    assert list(store.directory.iterdir()) == []
    # The control: a capture that DOES fit is written.
    assert store.write(jpeg(), camera_id="front", reason="interval", captured_at=START)


def test_the_purge_is_age_first_then_size(tmp_path):
    """Both halves in one call, each counted separately.

    Reversing them would let a large recent day evict a record the retention
    rule was still keeping deliberately, which is a window nobody can state.
    """
    now = [START]
    store = store_at(tmp_path, retention_days=2, max_bytes=1 << 30, now=lambda: now[0])
    write(store, START, body=b"x" * 200)
    write(store, START + timedelta(days=3), body=b"x" * 200)
    write(store, START + timedelta(days=3, minutes=1), body=b"x" * 200)

    now[0] = START + timedelta(days=3, minutes=2)
    store.max_bytes = 240
    by_age, by_size = store.purge()
    assert by_age == 1, "the old record was not taken by age"
    assert by_size == 1, "the cap was not then applied to what was left"
    assert store.bytes_used() <= store.max_bytes


# ---------------------------------------------------------------------------
# THE SIZING READS
# ---------------------------------------------------------------------------


def test_every_sizing_figure_is_read_off_this_directory(tmp_path):
    """These are READS. Nothing here is compared against an expected size.

    Nothing in this package has seen a capture from any camera it is written
    for, so there is no such number to compare against -- what a site's disk
    does is answered by pointing this route at that site's disk.
    """
    store = store_at(tmp_path)
    empty = store.reads(now=START)
    assert empty["record_count"] == 0
    assert empty["bytes_used"] == 0
    assert empty["oldest_at"] is None and empty["newest_at"] is None
    assert empty["mean_bytes_per_record"] is None
    assert empty["projected_bytes_per_day"] is None

    first = write(store, START - timedelta(hours=4), body=b"x" * 100)
    second = write(store, START - timedelta(hours=1), body=b"x" * 300)
    reads = store.reads(now=START)
    assert reads["record_count"] == 2
    assert reads["bytes_used"] == first.bytes + second.bytes
    assert reads["oldest_at"] == first.captured_at
    assert reads["newest_at"] == second.captured_at
    assert reads["mean_bytes_per_record"] == (first.bytes + second.bytes) // 2
    assert reads["records_last_24h"] == 2
    assert reads["bytes_last_24h"] == first.bytes + second.bytes
    # Four hours of data, so the projection is six times what is here.
    assert reads["projected_bytes_per_day"] == pytest.approx(
        (first.bytes + second.bytes) * 6, rel=0.01
    )


def test_a_projection_from_less_than_an_hour_is_unknown(tmp_path):
    """Multiplying four minutes by three hundred and sixty is not a projection.

    It is a number that looks measured, which is the one thing this project
    treats as worse than a missing number.
    """
    store = store_at(tmp_path)
    write(store, START - timedelta(minutes=4))
    assert store.reads(now=START)["projected_bytes_per_day"] is None
    # The control: the same store, an hour on, projects.
    assert store.reads(now=START + timedelta(hours=2))["projected_bytes_per_day"] is not None


def test_the_purge_counters_are_on_the_reads(tmp_path):
    """A store silently eating itself under a cap that is too small is visible."""
    store = store_at(tmp_path, retention_days=1, max_bytes=1 << 30)
    write(store, START - timedelta(days=4))
    store.purge(now=START)
    assert store.reads(now=START)["purged_by_age"] == 1
    assert store.reads(now=START)["purged_by_size"] == 0


def test_a_record_id_from_a_request_is_never_joined_onto_a_path(tmp_path):
    """`get` is a lookup in the index. There is no path built from an input."""
    store = store_at(tmp_path)
    record = write(store, START)
    assert store.get(record.id) is not None
    for hostile in ("../../etc/passwd", "..", "", record.id + "x", "/etc/passwd"):
        assert store.get(hostile) is None
    (tmp_path / "outside.jpg").write_bytes(jpeg(b"not in the store"))
    assert store.get("../outside") is None


def test_a_timestamp_nothing_can_read_is_purged_rather_than_kept(tmp_path):
    """A record no retention rule can honour is a photograph outside the rule."""
    store = store_at(tmp_path, retention_days=3650)
    record = write(store, START)
    sidecar = store.directory / f"{record.id}{SIDECAR_SUFFIX}"
    body = json.loads(sidecar.read_text(encoding="utf-8"))
    body["captured_at"] = "whenever"
    sidecar.write_text(json.dumps(body), encoding="utf-8")

    reopened = CaptureStore(store.directory, 3650, 1 << 20)
    reopened.open()
    assert reopened.records(), "the record did not survive the rebuild, so the purge is untested"
    reopened.purge(now=datetime.now(UTC))
    assert reopened.records() == ()
    assert reopened.purged_by_age == 1
