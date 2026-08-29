"""The camera read: one GET, a credential from a file, and every ending named.

Gokhan's spec, his words: *"camera disconnected is a malfunction"*. This is the
half of that sentence which lives at the camera; `tests/test_monitor_reads_capture.py`
is the half that gets it to a human.

Every camera in this file is a real HTTP server on an ephemeral socket, issuing a
real authentication challenge and verifying the response. A fake that accepted
any credential, or that took one out of a query string, would let a client that
could do neither pass everything here.
"""

from __future__ import annotations

import pytest

from cameras import FakeCamera, camera_server, jpeg
from conftest import camera_config, capture_config_for, capture_for
from gate_agent.camera import CameraRefusedUs, CameraUnreachable, SnapshotCamera
from gate_agent.config import CaptureConfig, ConfigError
from gate_agent.contract import CaptureCode
from serving import serving


def states(process):
    return {
        (entry["code"], entry["subject"]): entry["state"]
        for entry in process.health().to_dict()["codes"]
    }


# ---------------------------------------------------------------------------
# The credential, and where it comes from
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("scheme", ["digest", "basic"])
def test_the_credential_is_presented_the_way_the_camera_asks_for_it(scheme, tmp_path):
    """The camera chooses the scheme. This client answers whichever it is asked.

    Both are what the vendor documentation for the supported camera names, and
    WHICH one a given unit demands is that unit's business -- a client that
    decided for itself would need a per-camera configuration key nobody can
    answer correctly.
    """
    camera = FakeCamera(scheme=scheme)
    with serving(camera_server(camera)) as base:
        reader = SnapshotCamera("front", f"{base}/snapshot", "operator", "s3cret", timeout=5)
        assert reader.snapshot() == camera.body

    # THE SHAPE OF IT: the first request carried NO credential, the camera
    # answered `401` with a challenge, and the retry answered that challenge.
    # This is what "standard HTTP auth" means, and it is why the password is
    # never in the URL, never in a query string and never in an access log.
    assert camera.authorizations[0] is None, "the credential was sent unchallenged"
    assert len(camera.authorizations) >= 2, "there was no challenge and no retry"
    assert camera.authorizations[-1].lower().startswith(scheme)


def test_a_wrong_credential_is_a_refusal_and_not_a_dead_camera(tmp_path):
    """401 is the file beside this process. The camera is working perfectly."""
    camera = FakeCamera()
    with serving(camera_server(camera)) as base:
        reader = SnapshotCamera("front", f"{base}/snapshot", "operator", "wrong", timeout=5)
        with pytest.raises(CameraRefusedUs) as refused:
            reader.snapshot()
    assert refused.value.status == 401


def test_a_camera_that_is_not_running_is_not_a_camera_that_is_fine():
    """Nothing came back. The alternative -- a client that returned empty bytes --
    would make every other assertion in this file pass against a dead camera."""
    reader = SnapshotCamera("front", "http://127.0.0.1:1/snapshot", None, None, timeout=2)
    with pytest.raises(CameraUnreachable):
        reader.snapshot()


def test_a_5xx_is_unreachable_and_a_4xx_is_a_refusal():
    """The split round 3 made for targets, applied to a camera.

    A `500` is the camera's own process saying it could not take the picture,
    and the repair is at the camera. A `404` is a snapshot route this camera does
    not have, which is a repair in a configuration file on this box. Folded into
    one code a human is sent to the wrong machine half the time.
    """
    for status, expected in ((500, CameraUnreachable), (404, CameraRefusedUs)):
        camera = FakeCamera(username=None, status=status)
        with serving(camera_server(camera)) as base:
            reader = SnapshotCamera("front", f"{base}/snapshot", None, None, timeout=5)
            with pytest.raises(expected):
                reader.snapshot()


def test_nothing_is_followed_and_a_redirect_is_a_refusal_with_its_status():
    """A `Location` from a camera would take the credential to another host.

    The retry is the request that carries `Authorization`, so this matters more
    at a camera than anywhere else in the package: a camera answering `302` to
    the challenged request would hand a site's camera password to whichever host
    it named. The opener refuses it, and the status reaches the caller.
    """
    camera = FakeCamera(username=None, status=302)
    with serving(camera_server(camera)) as base:
        reader = SnapshotCamera("front", f"{base}/snapshot", None, None, timeout=5)
        with pytest.raises(CameraRefusedUs) as refused:
            reader.snapshot()
    assert refused.value.status == 302


def test_something_that_is_not_a_jpeg_is_not_a_capture():
    """A login page served as `image/jpeg` is a camera that sent no picture.

    Stored, it would fill a store with documents that read as a working
    installation right up until somebody opens one.
    """
    camera = FakeCamera(body=b"<html>please log in</html>", username=None)
    with serving(camera_server(camera)) as base:
        reader = SnapshotCamera("front", f"{base}/snapshot", None, None, timeout=5)
        with pytest.raises(CameraUnreachable, match="do not begin as a JPEG"):
            reader.snapshot()
    # The control: the same server, answering a JPEG, is read.
    camera.body = jpeg(b"real")
    with serving(camera_server(camera)) as base:
        assert SnapshotCamera("front", f"{base}/snapshot", None, None, timeout=5).snapshot()


# ---------------------------------------------------------------------------
# A CREDENTIAL IN A SNAPSHOT URL IS REFUSED AT STARTUP
# ---------------------------------------------------------------------------


def test_a_credential_in_a_snapshot_url_is_refused_at_startup(tmp_path):
    """The rule that decides which cameras this build supports at all.

    A camera whose only documented snapshot route takes the username and the
    password as query parameters is named unsupported in `docs/CONTRACT.md`
    rather than made to work by writing a password into a configuration file,
    every backup of it, and every access log between here and the camera. This
    is the code that enforces it -- the same `_refuse_userinfo` the monitor's
    targets and sinks go through.
    """
    auth = tmp_path / "camera.auth"
    auth.write_text("operator:s3cret\n", encoding="utf-8")
    raw = {
        "capture": {
            "id": "capture-1",
            "site_id": "site-1",
            "directory": str(tmp_path),
            "max_bytes": 1 << 20,
        },
        "cameras": {
            "front": {
                # `@example.com`, not `@camera.example.com`: the repository's
                # no-real-data gate reads `<anything>@<host>` as an address and
                # only exempts `example.com` at the END of it, so a subdomain
                # here turns that gate red. It found this the first time these
                # files were tracked, which is the gate working.
                "snapshot_url": "http://operator:s3cret@example.com/snap.jpg",
                "auth_file": str(auth),
            }
        },
    }
    with pytest.raises(ConfigError, match="userinfo in URL"):
        CaptureConfig.from_dict(raw)

    # The control: the same configuration without the credential in the URL is
    # accepted, so the refusal above is about the userinfo and not about the
    # document.
    raw["cameras"]["front"]["snapshot_url"] = "http://example.com/snap.jpg"
    assert CaptureConfig.from_dict(raw).cameras[0].camera_id == "front"


def test_a_credential_as_a_value_is_refused_by_name(tmp_path):
    """`password = "..."` under a camera is the same failure, one level down."""
    auth = tmp_path / "camera.auth"
    auth.write_text("operator:s3cret\n", encoding="utf-8")
    raw = {
        "capture": {
            "id": "capture-1",
            "site_id": "site-1",
            "directory": str(tmp_path),
            "max_bytes": 1 << 20,
        },
        "cameras": {
            "front": {
                "snapshot_url": "http://camera.example.com/snap.jpg",
                "auth_file": str(auth),
                "password": "s3cret",
            }
        },
    }
    with pytest.raises(ConfigError, match="would hold a credential as a value"):
        CaptureConfig.from_dict(raw)


# ---------------------------------------------------------------------------
# camera_feed_frozen
# ---------------------------------------------------------------------------


def _process(tmp_path, camera_body, base):
    directory = tmp_path / "store"
    directory.mkdir(exist_ok=True)
    config = capture_config_for(
        directory=directory,
        cameras=[camera_config("front", f"{base}/snapshot", tmp_path)],
    )
    process = capture_for(config)
    process.start()
    return process


def test_two_identical_snapshots_are_frozen_and_one_different_byte_is_not(tmp_path):
    """IDENTICAL means identical, and the control is one byte.

    The measure is a comparison of the bytes of two consecutive snapshots and
    nothing else. That is a cheap true negative and it is not a test of whether
    a camera is seeing -- which is why the caveat published beside the code says
    so, on the wire, where the message that wakes somebody can carry it.
    """
    camera = FakeCamera(body=jpeg(b"aaaa"))
    with serving(camera_server(camera)) as base:
        process = _process(tmp_path, camera.body, base)
        frozen = (CaptureCode.CAMERA_FEED_FROZEN.value, "front")

        process.poll(force=True)
        assert states(process)[frozen] == "unknown", "one snapshot is not two snapshots"

        process.poll(force=True)
        assert states(process)[frozen] == "active"

        # ONE BYTE. Not a new image, not a different size: the smallest change a
        # live sensor could produce.
        camera.body = jpeg(b"aaab")
        process.poll(force=True)
        assert states(process)[frozen] == "ok"


def test_a_camera_that_stopped_answering_is_not_frozen_it_is_unmeasured(tmp_path):
    """A camera that did not answer has not been compared with anything.

    Comparing the next snapshot against one from before an outage answers a
    different question -- whether the picture changed across the gap -- and
    publishing that as "the feed is frozen now" is a confident answer to
    something nobody measured.
    """
    camera = FakeCamera(body=jpeg(b"aaaa"))
    with serving(camera_server(camera)) as base:
        process = _process(tmp_path, camera.body, base)
        frozen = (CaptureCode.CAMERA_FEED_FROZEN.value, "front")
        process.poll(force=True)
        process.poll(force=True)
        assert states(process)[frozen] == "active"

        camera.status = 500
        process.poll(force=True)
        assert states(process)[(CaptureCode.CAMERA_UNREACHABLE.value, "front")] == "active"
        assert states(process)[frozen] == "unknown"

        # And the identical image after the outage is NOT reported as frozen,
        # because nothing was compared across it.
        camera.status = None
        process.poll(force=True)
        assert states(process)[frozen] == "unknown"
