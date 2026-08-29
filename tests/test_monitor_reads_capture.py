"""Gokhan's sentence, end to end: *"camera disconnected is a malfunction"*.

A camera stops answering. The capture process beside it measures that and
publishes `camera_unreachable`, named for that camera, in the LANE's entry shape.
A monitor reads that surface with the same code that reads a lane, passes the
code through unchanged, and a sink puts it in an email.

**That path did not exist before this round.** The L1's malfunction table scored
`camera feed frozen` and every camera-side fault as NONE EXISTS: no capture loop
existed anywhere and nothing compared consecutive frames. This file is the proof
that it exists now, measured from both ends -- what arrived at the camera, and
what arrived in the message.
"""

from __future__ import annotations

import pytest

from cameras import FakeCamera, camera_server, jpeg
from conftest import camera_config, capture_config_for, capture_for, config_for, monitor_for
from fakes import RecordingSink
from gate_agent.capture_service import CaptureService
from gate_agent.capture_service import make_server as capture_server
from gate_agent.config import EmailSinkConfig
from gate_agent.contract import CaptureCode, MonitorCode, TargetKind
from gate_agent.monitor import KNOWN_VERSIONS
from gate_agent.sinks import EmailSink
from serving import serving


class Outbox:
    """An SMTP server that is not one: it keeps the messages it was handed."""

    def __init__(self) -> None:
        self.sent = []

    def __call__(self, _config):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def send_message(self, message) -> None:
        self.sent.append(message)


@pytest.fixture
def site(tmp_path):
    """A camera, a capture process beside it, and a monitor watching that."""
    directory = tmp_path / "store"
    directory.mkdir()
    camera = FakeCamera(body=jpeg(b"one"))
    with serving(camera_server(camera)) as camera_url:
        config = capture_config_for(
            directory=directory,
            cameras=[camera_config("front-camera", f"{camera_url}/snapshot", tmp_path)],
        )
        process = capture_for(config)
        process.start()
        process.poll(force=True)
        with serving(capture_server(CaptureService(process), port=0)) as capture_url:
            yield camera, process, capture_url, f"{camera_url}/snapshot"


def test_every_target_kind_has_an_answer_to_which_versions_of_it():
    """One mapping, so a kind cannot be added without one.

    `KNOWN_LANE_VERSIONS if lane else KNOWN_IDENTITY_VERSIONS` stood here, and a
    third kind would silently have been read as an identity service.
    """
    assert set(KNOWN_VERSIONS) == set(TargetKind)


def test_the_monitor_reads_a_capture_process_and_passes_its_codes_through(site):
    """Not re-derived, not re-labelled: that target's own vocabulary."""
    _camera, process, capture_url, _snapshot = site
    monitor = monitor_for(config_for(capture=capture_url), [RecordingSink()])
    monitor.start()

    health = monitor.health().to_dict()
    target = next(one for one in health["targets"] if one["name"] == "capture")
    assert target["kind"] == TargetKind.CAPTURE.value
    assert target["contract_version"] == 1
    assert {entry["code"] for entry in target["codes"]} == {
        code.value for code in CaptureCode
    }
    assert {(entry["code"], entry["state"]) for entry in target["codes"]} == {
        (entry["code"], entry["state"]) for entry in process.health().to_dict()["codes"]
    }


def test_a_capture_process_that_is_not_running_is_named_by_its_own_code(tmp_path):
    """`capture_unreachable`, the monitor's own measurement, with a transition."""
    sink = RecordingSink()
    monitor = monitor_for(config_for(capture="http://127.0.0.1:1"), [sink])
    monitor.start()
    states = {(one["code"], one["state"]) for one in monitor.health().to_dict()["codes"]}
    assert (MonitorCode.CAPTURE_UNREACHABLE.value, "active") in states
    assert (MonitorCode.CAPTURE_REFUSED_US.value, "unknown") in states
    assert (MonitorCode.CAPTURE_UNREACHABLE.value, "raised") in sink.codes


def test_a_dead_camera_becomes_an_email_naming_the_camera_and_nothing_else(site):
    """**THE WHOLE PATH, in one test.** Camera → capture → monitor → a person.

    The email body must name the code and the camera, because "a camera is dead"
    and "which camera is dead" are different facts and only one of them can be
    acted on. And it must carry NOTHING ELSE about the site: no plate, no image,
    no record id, no directory, no URL and no credential -- a message that woke
    somebody at 3am is a message that has been forwarded, screenshotted and
    pasted by morning.
    """
    camera, process, capture_url, snapshot_url = site
    outbox = Outbox()
    email = EmailSink(
        EmailSinkConfig(
            host="smtp.example.com", port=587, sender="monitor@example.com",
            recipients=("oncall@example.com",),
        ),
        opener=outbox,
    )
    monitor = monitor_for(config_for(capture=capture_url), [email])
    monitor.start()
    assert not [
        message for message in outbox.sent if "camera_unreachable" in message["Subject"]
    ], "the camera was reported dead while it was answering"

    # THE CAMERA STOPS ANSWERING. Nothing else changes.
    camera.status = 500
    process.poll(force=True)
    monitor.poll(force=True)

    raised = [
        message for message in outbox.sent
        if CaptureCode.CAMERA_UNREACHABLE.value in message["Subject"]
    ]
    assert raised, "a camera that stopped answering told nobody"
    message = raised[-1]
    body = message.get_content()

    assert CaptureCode.CAMERA_UNREACHABLE.value in body
    assert "front-camera" in body, "the message does not say WHICH camera"
    assert message["To"] == "oncall@example.com"
    assert "front-camera" in message["Subject"], (
        "the subject line is what a person sees on a phone before they open anything"
    )

    # AND NOTHING ELSE ABOUT THE SITE.
    for forbidden in (
        str(process.store.directory),   # where this site's photographs are kept
        capture_url,                    # how to reach the surface that serves them
        snapshot_url,                   # how to reach the camera itself
        "s3cret",                       # the camera's password
        "operator",                     # the camera's username
        "PURGEME9",                     # a plate, which nothing here ever holds
    ):
        assert forbidden not in body, f"the message carries {forbidden!r}"
    assert "image" not in body and "jpg" not in body


def test_the_recovery_is_a_message_too(site):
    """A fault that comes back is news. A held state is not."""
    camera, process, capture_url, snapshot_url = site
    sink = RecordingSink()
    monitor = monitor_for(config_for(capture=capture_url), [sink])
    monitor.start()

    camera.status = 500
    process.poll(force=True)
    monitor.poll(force=True)
    assert (CaptureCode.CAMERA_UNREACHABLE.value, "raised") in sink.codes

    before = len(sink.codes)
    monitor.poll(force=True)
    assert len(sink.codes) == before, "a held state was re-sent"

    camera.status = None
    process.poll(force=True)
    monitor.poll(force=True)
    assert (CaptureCode.CAMERA_UNREACHABLE.value, "recovered") in sink.codes


def test_a_capture_code_carries_no_lane_id(site):
    """`lane_id` is on a LANE's notifications and on nobody else's.

    A camera that stopped answering is not a fact about a lane, and stamping one
    would read as "lane-1's camera", which at a site with two lanes sends
    somebody to the wrong barrier.
    """
    camera, process, capture_url, snapshot_url = site
    sink = RecordingSink()
    monitor = monitor_for(config_for(capture=capture_url), [sink])
    monitor.start()
    camera.status = 500
    process.poll(force=True)
    monitor.poll(force=True)

    payloads = [
        one for one in sink.payloads if one["code"] == CaptureCode.CAMERA_UNREACHABLE.value
    ]
    assert payloads
    assert all(one["lane_id"] is None for one in payloads)
    assert all(one["subject"] == "front-camera" for one in payloads)
