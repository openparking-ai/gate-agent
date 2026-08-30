"""Builders every test in this suite shares, and a clock a test can move.

Nothing here fakes the monitor's own behaviour. The targets are real servers on
real sockets and the monitor reads them the way it reads a lane in a gate
housing; what these helpers do is assemble a configuration and hand back a
`Monitor`, so that no test file has to build a `MonitorConfig` by hand and
quietly disagree with the next one about what a standard installation looks
like.
"""

from __future__ import annotations

from itertools import count

import pytest

from gate_agent.config import MonitorConfig, Target
from gate_agent.contract import TargetKind
from gate_agent.monitor import Monitor


class FakeClock:
    """A monotonic clock a test advances by hand.

    Poll intervals and the re-notify interval are both times, and a test that
    waited for them would be slow and flaky in the same breath. Advancing a
    clock is the same measurement without either.
    """

    def __init__(self, start: float = 1000.0) -> None:
        self.value = start

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


class FakeUtc:
    """An ISO 8601 UTC clock that moves only when a test moves it.

    A real `now()` would make `lane_gone_quiet` depend on how long the suite
    took to get here, which is a measurement of the test runner.
    """

    def __init__(self, start: str = "2026-08-30T14:00:00+00:00") -> None:
        from datetime import datetime

        self.moment = datetime.fromisoformat(start)

    def __call__(self) -> str:
        return self.moment.isoformat()

    def advance(self, seconds: float) -> None:
        from datetime import timedelta

        self.moment += timedelta(seconds=seconds)


_ids = count(1)


def config_for(
    *,
    lane: str | None = None,
    identity_service: str | None = None,
    platform: str | None = None,
    capture: str | None = None,
    garage_id: str = "garage-1",
    platform_token: str = "operator-token",
    lane_quiet_seconds: float = 900.0,
    poll_seconds: float = 30.0,
    renotify_seconds: float | None = None,
    event_window_depth: int = 256,
) -> MonitorConfig:
    """A configuration with whatever targets a test names. At least one, always."""
    targets = []
    if lane:
        targets.append(
            Target(name="lane", kind=TargetKind.LANE, url=lane, poll_seconds=poll_seconds)
        )
    if identity_service:
        targets.append(
            Target(
                name="identity_service",
                kind=TargetKind.IDENTITY_SERVICE,
                url=identity_service,
                poll_seconds=poll_seconds,
            )
        )
    if capture:
        targets.append(
            Target(name="capture", kind=TargetKind.CAPTURE, url=capture, poll_seconds=poll_seconds)
        )
    if platform:
        targets.append(
            Target(
                name="platform",
                kind=TargetKind.PLATFORM,
                url=platform,
                poll_seconds=poll_seconds,
                token=platform_token,
                garage_id=garage_id,
                lane_quiet_seconds=lane_quiet_seconds,
            )
        )
    return MonitorConfig(
        monitor_id=f"monitor-{next(_ids)}",
        site_id="site-1",
        targets=tuple(targets),
        sinks=(),
        renotify_seconds=renotify_seconds,
        event_window_depth=event_window_depth,
    )


def monitor_for(config: MonitorConfig, sinks, clock=None, now=None) -> Monitor:
    return Monitor(config, sinks, clock=clock or FakeClock(), now=now or FakeUtc())


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def utc():
    return FakeUtc()


def camera_config(
    camera_id: str,
    url: str,
    tmp_path,
    username="operator",
    password="s3cret",
    max_snapshot_bytes: int = 1 << 16,
):
    """One `CameraConfig`, with its credential written to a file first.

    A file, always, even here: there is no path in this package that takes a
    camera credential as a value, so a test builder that passed one would be
    exercising a shape the product does not have.
    """
    from gate_agent.config import CameraConfig

    auth = tmp_path / f"{camera_id}.auth"
    auth.write_text(f"{username}:{password}\n", encoding="utf-8")
    return CameraConfig(
        camera_id=camera_id,
        snapshot_url=url,
        username=username,
        password=password,
        timeout_seconds=5.0,
        # Below the 1 MiB cap `capture_config_for` gives a test store, because
        # the product refuses a configuration where they are the other way
        # round -- a builder that shipped the 32 MiB default here would build
        # configurations the product would not accept.
        max_snapshot_bytes=max_snapshot_bytes,
    )


def capture_config_for(
    *,
    directory,
    cameras,
    lane: str | None = None,
    max_bytes: int = 1 << 20,
    max_snapshot_bytes: int = 1 << 16,
    retention_days: int = 30,
    interval_seconds: float = 60.0,
    poll_seconds: float = 30.0,
):
    """A capture configuration with whatever a test names. At least one camera."""
    from gate_agent.config import CaptureConfig, Target
    from gate_agent.contract import TargetKind

    return CaptureConfig(
        capture_id=f"capture-{next(_ids)}",
        site_id="site-1",
        directory=directory,
        max_bytes=max_bytes,
        max_snapshot_bytes=max_snapshot_bytes,
        cameras=tuple(cameras),
        lane=(
            Target(name="lane", kind=TargetKind.LANE, url=lane, poll_seconds=poll_seconds)
            if lane
            else None
        ),
        interval_seconds=interval_seconds,
        retention_days=retention_days,
    )


class MovingUtc:
    """A wall clock a test advances, returning `datetime` rather than a string.

    The capture process stamps a record with the moment it took it and schedules
    the next one from the same read, so a test that could not move that clock
    could not exercise an interval, a retention window or a projection without
    waiting for one.
    """

    def __init__(self, start: str = "2026-08-30T14:00:00+00:00") -> None:
        from datetime import datetime

        self.moment = datetime.fromisoformat(start)

    def __call__(self):
        return self.moment

    def advance(self, seconds: float) -> None:
        from datetime import timedelta

        self.moment += timedelta(seconds=seconds)


def capture_for(config, store=None, now=None, camera_factory=None):
    """A `CaptureProcess` on a real store, with a clock a test can move."""
    from gate_agent.capture import CaptureProcess
    from gate_agent.store import CaptureStore

    now = now or MovingUtc()
    store = store or CaptureStore(
        config.directory, config.retention_days, config.max_bytes, now=now
    )
    kwargs = {}
    if camera_factory is not None:
        kwargs["camera_factory"] = camera_factory
    return CaptureProcess(config, store, now=now, **kwargs)
