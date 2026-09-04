"""The capture configuration, and every refusal it makes at startup.

Two settings here have NO DEFAULT, and they are the two nobody may choose on a
site's behalf. `directory` is where that site's personal data will sit. `max_bytes`
is that site's disk, and **nothing in this package has ever seen a capture from
any camera it is written for** -- so there is no measurement to draw a default
from, and a plausible number would be a figure that looked measured.

Everything else is a per-site setting with a published default, and each default
lives in exactly one place: `config.py`. `tests/test_capture_contract.py` holds
`docs/CONTRACT.md` to those constants by value.
"""

from __future__ import annotations

import pytest

from gate_agent.config import (
    DEFAULT_CAPTURE_INTERVAL_SECONDS,
    DEFAULT_EVENT_WINDOW_DEPTH,
    DEFAULT_RETENTION_DAYS,
    RETENTION_DAYS_BOUNDS,
    CaptureConfig,
    ConfigError,
    MonitorConfig,
)


@pytest.fixture
def auth(tmp_path):
    """A camera credential with the permissions `config.py` insists on.

    `0600` because every credential this package reads is refused at anything
    wider. A fixture at the default `0644` would exercise a path the product
    refuses -- see `test_a_camera_auth_file_readable_by_anybody_is_refused`,
    which is the one place that writes the wider mode on purpose.
    """
    path = tmp_path / "camera.auth"
    path.write_text("operator:s3cret\n", encoding="utf-8")
    path.chmod(0o600)
    return path


def raw_for(tmp_path, auth, **capture):
    body = {
        "capture": {
            "id": "capture-1",
            "site_id": "site-1",
            "directory": str(tmp_path),
            "max_bytes": 1 << 20,
            # Declared here because the published default is 32 MiB and this
            # store's cap is 1 MiB: the ceiling on ONE read must be BELOW the
            # cap on the whole store, and a fixture that left them crossed
            # would be refused before any of these tests measured anything.
            "max_snapshot_bytes": 1 << 16,
        },
        "cameras": {
            "front": {"snapshot_url": "http://camera.example.com/snap", "auth_file": str(auth)}
        },
    }
    body["capture"].update(capture)
    return body


# ---------------------------------------------------------------------------
# THE TWO WITH NO DEFAULT
# ---------------------------------------------------------------------------


def test_a_capture_process_with_no_directory_is_refused(tmp_path, auth):
    raw = raw_for(tmp_path, auth)
    del raw["capture"]["directory"]
    with pytest.raises(ConfigError, match="does not declare directory"):
        CaptureConfig.from_dict(raw)
    # The control: the same document with it declared is accepted.
    assert CaptureConfig.from_dict(raw_for(tmp_path, auth)).directory == tmp_path


def test_a_capture_process_with_no_max_bytes_is_refused(tmp_path, auth):
    """There is no measurement here to draw a default from, and that is said."""
    raw = raw_for(tmp_path, auth)
    del raw["capture"]["max_bytes"]
    with pytest.raises(ConfigError, match="does not declare max_bytes"):
        CaptureConfig.from_dict(raw)
    for bad in (0, -1, 1.5, True, "lots"):
        raw["capture"]["max_bytes"] = bad
        with pytest.raises(ConfigError):
            CaptureConfig.from_dict(raw)


# ---------------------------------------------------------------------------
# THE DEFAULTS, AND THEIR BOUNDS
# ---------------------------------------------------------------------------


def test_the_published_defaults_are_what_an_undeclared_site_gets(tmp_path, auth):
    """Read from the constants, never typed here: a third copy lies too."""
    config = CaptureConfig.from_dict(raw_for(tmp_path, auth))
    assert config.retention_days == DEFAULT_RETENTION_DAYS
    assert config.interval_seconds == DEFAULT_CAPTURE_INTERVAL_SECONDS


def test_retention_days_outside_its_bounds_is_refused(tmp_path, auth):
    """Below the floor an overnight incident is gone before anybody looks."""
    low, high = RETENTION_DAYS_BOUNDS
    for good in (low, high, DEFAULT_RETENTION_DAYS):
        assert CaptureConfig.from_dict(
            raw_for(tmp_path, auth, retention_days=good)
        ).retention_days == good
    for bad in (low - 1, high + 1, 0, -30):
        with pytest.raises(ConfigError):
            CaptureConfig.from_dict(raw_for(tmp_path, auth, retention_days=bad))


def test_a_fractional_retention_window_is_refused(tmp_path, auth):
    """`retention_days = 0.5` truncating to nothing is a rule nobody wrote."""
    with pytest.raises(ConfigError, match="positive whole number"):
        CaptureConfig.from_dict(raw_for(tmp_path, auth, retention_days=0.5))


# ---------------------------------------------------------------------------
# CAMERAS
# ---------------------------------------------------------------------------


def test_a_capture_process_with_no_camera_is_refused(tmp_path, auth):
    """It would run, publish a working surface, and record nothing."""
    raw = raw_for(tmp_path, auth)
    raw["cameras"] = {}
    with pytest.raises(ConfigError, match="no camera is declared"):
        CaptureConfig.from_dict(raw)


def test_a_camera_id_that_would_escape_the_directory_is_refused(tmp_path, auth):
    """A camera id becomes part of every filename this camera's captures use."""
    for bad in ("../front", "front/one", "front cam", "a" * 65, ""):
        raw = raw_for(tmp_path, auth)
        raw["cameras"] = {
            bad: {"snapshot_url": "http://camera.example.com/snap", "auth_file": str(auth)}
        }
        with pytest.raises(ConfigError, match="not a usable camera id"):
            CaptureConfig.from_dict(raw)
    # The control: the ordinary ids a site would write are accepted.
    for good in ("front", "entry-1", "lane_a_2"):
        raw = raw_for(tmp_path, auth)
        raw["cameras"] = {
            good: {"snapshot_url": "http://camera.example.com/snap", "auth_file": str(auth)}
        }
        assert CaptureConfig.from_dict(raw).cameras[0].camera_id == good


def test_a_camera_with_no_auth_file_is_refused(tmp_path, auth):
    """A snapshot route with no credential is a camera anyone on that LAN reads."""
    raw = raw_for(tmp_path, auth)
    del raw["cameras"]["front"]["auth_file"]
    with pytest.raises(ConfigError, match="does not declare auth_file"):
        CaptureConfig.from_dict(raw)


def test_an_auth_file_that_is_not_user_colon_password_is_refused(tmp_path, auth):
    """Empty read as "no credential" turns authentication off where it mattered."""
    for body in ("", "   \n", "operator\n", ":s3cret\n", "operator:\n"):
        auth.write_text(body, encoding="utf-8")
        auth.chmod(0o600)
        with pytest.raises(ConfigError):
            CaptureConfig.from_dict(raw_for(tmp_path, auth))
    # A password with a colon in it is a password, and the split is on the FIRST.
    auth.write_text("operator:a:b:c\n", encoding="utf-8")
    auth.chmod(0o600)
    camera = CaptureConfig.from_dict(raw_for(tmp_path, auth)).cameras[0]
    assert (camera.username, camera.password) == ("operator", "a:b:c")


def test_a_camera_configuration_may_not_declare_a_platform_target(tmp_path, auth):
    """One target builder, restricted per process. A capture reads a LANE.

    `[targets.platform]` in a capture file is refused by name rather than parsed
    and ignored, which is how a site discovers that this process does not do
    what the key it copied from the monitor's example implies.
    """
    raw = raw_for(tmp_path, auth)
    raw["targets"] = {"platform": {"url": "https://platform.example.com"}}
    with pytest.raises(ConfigError, match="no reader for"):
        CaptureConfig.from_dict(raw)
    # The control: a lane IS read.
    raw["targets"] = {"lane": {"url": "http://127.0.0.1:8090"}}
    assert CaptureConfig.from_dict(raw).lane is not None


# ---------------------------------------------------------------------------
# THE MONITOR'S EVENT WINDOW, WHICH WAS A CONSTANT UNTIL THIS ROUND
# ---------------------------------------------------------------------------


def test_the_monitors_event_window_is_a_per_site_setting_now():
    """It was reachable only from Python, so exactly one parameter was not one."""
    raw = {
        "monitor": {"id": "monitor-1", "site_id": "site-1"},
        "targets": {"lane": {"url": "http://127.0.0.1:8090"}},
    }
    assert MonitorConfig.from_dict(raw).event_window_depth == DEFAULT_EVENT_WINDOW_DEPTH

    raw["monitor"]["event_window_depth"] = 32
    assert MonitorConfig.from_dict(raw).event_window_depth == 32

    for bad in (0, -1, 2.5, True, "deep"):
        raw["monitor"]["event_window_depth"] = bad
        with pytest.raises(ConfigError, match="positive whole number"):
            MonitorConfig.from_dict(raw)
