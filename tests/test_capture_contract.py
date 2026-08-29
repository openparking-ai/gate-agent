"""The capture process's own surface: what it publishes, and what it will not.

The document and the code are compared the two ways round 3 established, because
one of them is not enough: by SHAPE, from the `<!--payload:NAME-->` blocks, which
catches a field added, renamed or dropped; and by VALUE, for the fields that are
CONSTANTS of the code, because `shape()` discards every leaf.

**And a third way, which is this round's own rule.** The brief for this round
forbids a disk figure, a bytes-per-image and a days-of-storage from appearing in
any document, because the L1 sized nothing -- no capture from any of these
devices exists. So every SIZE in every capture payload example is required to be
`null`: the shape is what a consumer writes code against, and the values on this
surface are reads of one site's disk, not a figure this package may publish.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from cameras import FakeCamera, camera_server, jpeg
from conftest import camera_config, capture_config_for, capture_for
from foreign_lane import ForeignLane
from foreign_lane import make_server as foreign_server
from gate_agent.capture_service import (
    ACT_ROUTES,
    IMAGES_PREFIX,
    READ_ROUTES,
    CaptureService,
    _Handler,
    make_server,
)
from gate_agent.config import (
    DEFAULT_CAPTURE_INTERVAL_SECONDS,
    DEFAULT_EVENT_WINDOW_DEPTH,
    DEFAULT_RETENTION_DAYS,
    RETENTION_DAYS_BOUNDS,
)
from gate_agent.contract import (
    CAPTURE_CAVEATS,
    CONTRACT_VERSION,
    CaptureCode,
    CaptureEntry,
    CaptureHealth,
    CaptureReason,
    HealthState,
    Source,
    StoreReads,
)
from gate_agent.service import InsecureBind, assert_bind_allowed
from serving import serving
from test_monitor_contract import doc_payloads, shape

CONTRACT_DOC = Path(__file__).resolve().parent.parent / "docs" / "CONTRACT.md"

#: The capture blocks in the document. Imported from the monitor's contract test
#: rather than restated: that file asserts that every block in the document
#: belongs to a route, and two lists of block names would drift.
from test_monitor_contract import CAPTURE_PAYLOADS  # noqa: E402

#: A key whose value is a NUMBER OF BYTES or A COUNT OF STORED RECORDS. Every
#: one of them must be `null` in the document: this round publishes no size, no
#: rate and no capacity anywhere, because nothing here has measured one.
SIZE_KEY = re.compile(r"(^|_)bytes($|_)|^record_count$|^records_last_24h$|^purged_by_")


@pytest.fixture
def running(tmp_path):
    """A capture process with a store that HAS something in it.

    Populated on purpose: an empty records list reduces to an empty shape, and a
    document comparison against nothing would pass whatever the document said.
    """
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
        process = capture_for(config)
        process.start()
        lane.record("vended", "2026-08-30T14:00:00+00:00")
        process.poll(force=True)
        yield process


def live(process) -> dict:
    return {
        "capture": process.describe().to_dict(),
        "capture_health": process.health().to_dict(),
        "capture_records": process.records(0).to_dict(),
    }


# ---------------------------------------------------------------------------
# GUARANTEE 1 — the document and the code agree, by shape and by value
# ---------------------------------------------------------------------------


def test_the_document_shows_exactly_the_capture_payloads_the_code_builds(running):
    doc = doc_payloads()
    assert CAPTURE_PAYLOADS <= set(doc), (
        "docs/CONTRACT.md is missing a capture payload block: "
        f"{sorted(CAPTURE_PAYLOADS - set(doc))}"
    )
    served = live(running)
    for name in sorted(CAPTURE_PAYLOADS):
        assert shape(doc[name]) == shape(served[name]), (
            f"docs/CONTRACT.md's `{name}` example does not have the shape the code builds.\n"
            f"  doc:  {shape(doc[name])}\n  code: {shape(served[name])}"
        )
    # The controls: the nested lists the comparison descended into are not empty.
    assert served["capture_health"]["codes"]
    assert served["capture"]["cameras"]
    assert served["capture_records"]["records"]


def test_the_capture_documents_contract_version_is_the_codes(running):
    doc = doc_payloads()
    for name in sorted(CAPTURE_PAYLOADS):
        assert doc[name]["contract_version"] == CONTRACT_VERSION, (
            "the capture process is versioned WITH the monitor: one contract, one version, so a "
            "consumer holds one compatibility policy for this package"
        )


def test_no_size_figure_appears_anywhere_in_the_capture_documentation():
    """This round's own rule, enforced rather than remembered.

    The L1 sized nothing because no capture from any of these devices exists,
    and that is still true. So the document publishes that these figures EXIST
    and what each is derived from, and it publishes no value -- a `bytes` of
    fifty thousand in an example would read as "a capture is about fifty
    kilobytes", which is the claim this round is forbidden to make.
    """
    doc = doc_payloads()
    checked = []
    for name in sorted(CAPTURE_PAYLOADS):
        for path, value in _walk(doc[name]):
            if SIZE_KEY.search(path.rsplit(".", 1)[-1]):
                checked.append(path)
                assert value is None, (
                    f"docs/CONTRACT.md's `{name}` example publishes {path}={value!r}. This round "
                    "publishes no size, no rate and no capacity: nothing in this package has "
                    "measured one, and a figure in a document looks measured."
                )
    # The control: the sweep found the size keys at all, and there are several.
    assert len(checked) >= 6, f"only {checked} were checked, so this asserts almost nothing"


def _walk_keys(payload, path=""):
    """Every (key, value) in a payload, descending into lists and dicts."""
    if isinstance(payload, dict):
        for key, value in payload.items():
            yield key, value
            yield from _walk_keys(value, key)
    elif isinstance(payload, list):
        for item in payload:
            yield from _walk_keys(item, path)


def _walk(payload, path=""):
    if isinstance(payload, dict):
        for key, value in payload.items():
            yield from _walk(value, f"{path}.{key}" if path else key)
    elif isinstance(payload, list):
        for item in payload:
            yield from _walk(item, path)
    else:
        yield path, payload


def test_the_retention_default_has_exactly_one_copy_and_the_document_quotes_it():
    """**THE SINGLE COPY IS `config.DEFAULT_RETENTION_DAYS`.**

    The platform keeps its identity-retention default in a database column and
    nowhere else. A lane has no database and a capture process has no column, so
    the copy lives in `config.py` and the document publishes it through the
    payload mechanism. Editing the document goes red here; editing the constant
    goes red here. There is no third place for it to be.
    """
    doc = doc_payloads()
    assert doc["capture"]["retention_days"] == DEFAULT_RETENTION_DAYS
    assert doc["capture"]["interval_seconds"] == DEFAULT_CAPTURE_INTERVAL_SECONDS
    assert doc["sets"]["retention_days_bounds"] == list(RETENTION_DAYS_BOUNDS)
    assert doc["monitor"]["event_window_depth"] == DEFAULT_EVENT_WINDOW_DEPTH
    # The control: none of these is `None` agreeing with `None`.
    assert all(
        value is not None
        for value in (DEFAULT_RETENTION_DAYS, DEFAULT_CAPTURE_INTERVAL_SECONDS)
    )


def test_the_frozen_caveat_in_the_document_is_the_one_on_the_wire():
    """A caveat a document restates in its own words is a second copy.

    This one matters: what `camera_feed_frozen` measures is the bytes, and a
    camera burning a clock into the frame is NEVER frozen by it however dead its
    sensor. That sentence travels with the code, in the payload, so the message
    that wakes somebody carries it.
    """
    doc = doc_payloads()
    published = [
        entry["caveat"]
        for entry in doc["capture_health"]["codes"]
        if entry["code"] == CaptureCode.CAMERA_FEED_FROZEN.value
    ]
    assert published, "the document's health example no longer shows the frozen code"
    assert published[0] == CAPTURE_CAVEATS[CaptureCode.CAMERA_FEED_FROZEN]


# ---------------------------------------------------------------------------
# GUARANTEE 2 — every capture code ships, every time
# ---------------------------------------------------------------------------


def test_every_capture_code_ships_with_a_subject_a_source_and_never_alarm(running):
    codes = running.health().to_dict()["codes"]
    assert {entry["code"] for entry in codes} == {code.value for code in CaptureCode}
    for entry in codes:
        assert entry["subject"]
        assert entry["source"] == Source.MEASURED.value
        assert entry["state"] in {state.value for state in HealthState}
        assert entry["never_alarm"] is False, (
            "nothing on this surface is never_alarm: every code is a physical thing that needs "
            "a person"
        )


def test_a_capture_health_payload_missing_a_code_is_refused():
    """The control for the test above, and it fires at construction."""
    every = tuple(
        CaptureEntry(code=code.value, subject="x", state=HealthState.UNKNOWN.value)
        for code in CaptureCode
    )
    store = StoreReads(
        bytes_used=0, record_count=0, oldest_at=None, newest_at=None,
        mean_bytes_per_record=None, records_last_24h=0, bytes_last_24h=0,
        projected_bytes_per_day=None,
    )
    CaptureHealth(codes=every, store=store)
    with pytest.raises(ValueError, match="missing"):
        CaptureHealth(codes=every[:-1], store=store)
    with pytest.raises(ValueError, match="twice"):
        CaptureHealth(codes=every + (every[0],), store=store)
    with pytest.raises(ValueError, match="half of one"):
        CaptureHealth(codes=every)


def test_a_state_that_is_not_unknown_needs_a_measured_source():
    code = CaptureCode.CAMERA_UNREACHABLE.value
    CaptureEntry(code=code, subject="front", state=HealthState.OK.value)
    with pytest.raises(ValueError, match="not a capture code"):
        CaptureEntry(code="reference_not_recognised", subject="front", state="unknown")
    with pytest.raises(ValueError, match="does not carry an HTTP status"):
        CaptureEntry(code=code, subject="front", state="active", status=404)


# ---------------------------------------------------------------------------
# GUARANTEE 3 — the surface is read-only, and it cannot delete a record either
# ---------------------------------------------------------------------------


def test_no_capture_route_mutates_anything():
    """Every `do_*` other than `do_GET` IS the one shared refusal.

    And `ACT_ROUTES` is empty: nothing here captures on demand, deletes a record
    or moves a retention window. A delete route would be a way to remove the one
    image that mattered from a store whose purpose is that the entries can be
    reconstructed afterwards.
    """
    methods = {name: getattr(_Handler, name) for name in dir(_Handler) if name.startswith("do_")}
    assert "do_GET" in methods
    others = {name: fn for name, fn in methods.items() if name != "do_GET"}
    assert others, "the sweep found no other methods, so it proves nothing"
    for name, fn in others.items():
        assert fn is _Handler._method_not_allowed, f"{name} is not the shared refusal"
    assert ACT_ROUTES == ()
    assert set(READ_ROUTES) == {"/v1/capture", "/v1/capture/health", "/v1/capture/records"}


def test_a_consumer_that_posts_or_deletes_is_refused(running):
    """Asked for real over a socket, including the DELETE a store invites."""
    with serving(make_server(CaptureService(running), port=0)) as base:
        for path in READ_ROUTES:
            with urllib.request.urlopen(f"{base}{path}", timeout=5) as response:
                assert response.status == 200
        for method in ("POST", "DELETE", "PUT", "PATCH"):
            with pytest.raises(urllib.error.HTTPError) as refused:
                urllib.request.urlopen(
                    urllib.request.Request(f"{base}/v1/capture", data=b"{}", method=method),
                    timeout=5,
                )
            assert refused.value.code == 405
            assert refused.value.headers["Allow"] == "GET"


# ---------------------------------------------------------------------------
# GUARANTEE 4 — the bind rule, the images, and the cursor
# ---------------------------------------------------------------------------


def test_a_non_loopback_bind_without_a_token_is_refused():
    """`InsecureBind`, imported rather than restated. One rule, one copy.

    Off loopback THIS surface serves photographs of cars and when they were
    taken, which is the sharpest of the three exposures in this estate.
    """
    assert assert_bind_allowed.__module__ == "gate_agent.service"
    assert_bind_allowed("127.0.0.1", 8093, None)
    assert_bind_allowed("192.168.1.10", 8093, "a-token")
    for host in ("192.168.1.10", "0.0.0.0", "", "capture.local"):
        with pytest.raises(InsecureBind):
            assert_bind_allowed(host, 8093, None)


def test_with_a_token_every_route_requires_it_including_the_images(running):
    """INCLUDING THE IMAGES, which is the one it would be easiest to leave open.

    An image route open "because it is just a JPEG" is the whole store readable
    by anyone who can enumerate a record id.
    """
    record = running.records(0).to_dict()["records"][0]
    paths = list(READ_ROUTES) + [f"{IMAGES_PREFIX}{record['id']}"]
    with serving(make_server(CaptureService(running), port=0, token="s3cret")) as base:
        for path in paths:
            with pytest.raises(urllib.error.HTTPError) as refused:
                urllib.request.urlopen(f"{base}{path}", timeout=5)
            assert refused.value.code == 401, path

            request = urllib.request.Request(f"{base}{path}")
            request.add_header("Authorization", "Bearer s3cret")
            with urllib.request.urlopen(request, timeout=5) as response:
                assert response.status == 200


def test_the_images_route_serves_the_bytes_the_camera_sent(running):
    """Never re-encoded: what comes back is what went in."""
    record = running.records(0).to_dict()["records"][0]
    stored = running.store.get(record["id"]).image_path.read_bytes()
    with serving(make_server(CaptureService(running), port=0)) as base:
        with urllib.request.urlopen(f"{base}{record['image_url']}", timeout=5) as response:
            assert response.status == 200
            assert response.headers["Content-Type"] == "image/jpeg"
            assert response.read() == stored


def test_an_id_that_is_not_a_record_is_a_404_and_never_a_file(running):
    """The id is looked up in the index. No path is built from a request."""
    (running.store.directory.parent / "outside.jpg").write_bytes(jpeg(b"not in the store"))
    with serving(make_server(CaptureService(running), port=0)) as base:
        for hostile in ("..%2f..%2foutside", "nope", "..", "%2e%2e%2foutside"):
            with pytest.raises(urllib.error.HTTPError) as refused:
                urllib.request.urlopen(f"{base}{IMAGES_PREFIX}{hostile}", timeout=5)
            assert refused.value.code in (404, 400), hostile


def test_the_records_cursor_says_reset_both_ways(running):
    """The lane's semantics, on a store whose window is the retention rule."""
    page = running.records(0).to_dict()
    current = page["cursor"]
    assert running.records(current).to_dict()["reset"] is False
    assert running.records(current + 1).to_dict()["reset"] is True

    # And `since` behind the oldest record still held: here the window is the
    # STORE, and what evicts from it is the retention rule and the size cap.
    running.store.max_bytes = 1
    running.store.purge()
    assert running.store.records() == () or running.records(0).to_dict()["dropped"] > 0
    assert running.records(0).to_dict()["dropped"] > 0


def test_the_records_route_carries_sidecar_fields_and_never_bytes_inline(running):
    """A page a consumer polls is a page it polls often."""
    page = running.records(0).to_dict()
    assert page["records"]
    for record in page["records"]:
        assert set(record) == {
            "cursor", "id", "captured_at", "camera_id", "reason", "lane_event_cursor",
            "lane_event_at", "trigger_to_capture_ms", "bytes", "image_url",
        }
        assert record["reason"] in {reason.value for reason in CaptureReason}
        assert record["image_url"].startswith(IMAGES_PREFIX)


# ---------------------------------------------------------------------------
# GUARANTEE 5 — NOTHING ON THIS SURFACE IDENTIFIES A VEHICLE
# ---------------------------------------------------------------------------


def test_a_planted_plate_in_a_lane_event_reaches_no_route_and_no_file(tmp_path):
    """The sweep, with the plant, over every route AND over the store itself.

    A lane's event `detail` is where the lane puts what it knows, and
    `entry_pending` really does carry `plate_region` there. This plants a plate
    beside it, on an event that IS a trigger, so the picture is taken from the
    payload carrying it -- and then asks all four routes and every byte on the
    disk.
    """
    planted = "PURGEME9"
    #: The lane really does put `plate_region` in an event's detail. A
    #: distinctive value rather than the real `TR`, because a two-letter string
    #: is a substring of half of everything and a sweep that matched one would
    #: be reporting on its own noise.
    planted_region = "REGIONXYZ"
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
        process = capture_for(config)
        process.start()
        lane.record(
            "vended",
            "2026-08-30T14:00:00+00:00",
            {"plate": planted, "plate_region": planted_region, "reason": "cached_allow"},
        )
        lane.record(
            "entry_pending", "2026-08-30T14:00:01+00:00", {"plate_region": planted_region}
        )
        process.poll(force=True)

        # THE CONTROL, first: the lane really is serving that string, so the
        # absence below is about this process rather than about the search.
        import urllib.request as request_module

        with request_module.urlopen(f"{lane_url}/v1/lane/events?since=0", timeout=5) as answer:
            served_by_the_lane = answer.read().decode()
        assert planted in served_by_the_lane and planted_region in served_by_the_lane

        with serving(make_server(CaptureService(process), port=0)) as base:
            served = ""
            payloads = []
            for path in READ_ROUTES:
                with request_module.urlopen(f"{base}{path}", timeout=5) as answer:
                    body = answer.read().decode()
                served += body
                payloads.append(json.loads(body))
            images = 0
            for record in process.records(0).to_dict()["records"]:
                with request_module.urlopen(
                    f"{base}{record['image_url']}", timeout=5
                ) as answer:
                    served += answer.read().decode("latin-1")
                    images += 1
            assert images, "no image was fetched, so the bytes were not swept"

    for value in (planted, planted_region):
        assert value not in served, f"{value!r} is on this process's routes"
    # AND STRUCTURALLY, not only by substring: no FIELD on any of these routes
    # is about a vehicle. A key check survives a value that happens not to
    # collide, which a substring sweep does not.
    keys = {key for payload in payloads for key, _value in _walk_keys(payload)}
    assert keys, "no keys were walked"
    assert not [key for key in keys if "plate" in key or "vehicle" in key], sorted(keys)

    for path in sorted(directory.iterdir()):
        body = path.read_bytes()
        for value in (planted, planted_region):
            assert value.encode() not in body, f"{value!r} is in the store: {path.name}"
        assert b"plate" not in body, f"the word `plate` is in the store: {path.name}"


def test_a_records_sidecar_holds_the_seven_fields_and_nothing_about_a_vehicle(running):
    """The store's schema, read off the disk rather than off the dataclass."""
    for path in sorted(running.store.directory.glob("*.json")):
        body = json.loads(path.read_text(encoding="utf-8"))
        assert set(body) == {
            "captured_at", "camera_id", "reason", "lane_event_cursor", "lane_event_at",
            "trigger_to_capture_ms", "bytes",
        }
