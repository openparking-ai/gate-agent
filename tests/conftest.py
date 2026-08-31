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
    auth.chmod(0o600)
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


# ---------------------------------------------------------------------------
# The agent
# ---------------------------------------------------------------------------


def wav(path, seconds: float = 0.5, rate: int = 8000):
    """A short mono 16-bit WAV at a real path.

    Used for the per-intercom `name_audio`, which is a SITE's file: the package
    ships no recording that can say the name of a door, so a test that wanted
    one had to make it. Silence is enough here -- what is measured is which file
    was played to which leg and in what order, and that is the path.
    """
    import wave

    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(b"\x00\x00" * int(rate * seconds))
    return path


#: The dial secret the tests' declared intercom uses, and the account it makes.
#: Long enough to pass the floor `config.py` refuses below, and obviously not a
#: secret anybody generated -- what a fixture must not do is look like the real
#: thing, and what it must do is exercise the same code path.
DIAL_SECRET = "test-only-dial-secret-0000"
INTERCOM_ACCOUNT = "agent-" + DIAL_SECRET
#: A second one, for a test that needs two doors.
OTHER_SECRET = "test-only-dial-secret-1111"
OTHER_ACCOUNT = "agent-" + OTHER_SECRET


def secret_file(path, secret: str = DIAL_SECRET):
    """A CREDENTIAL file with the permissions `config.py` insists on.

    Every credential this package reads is refused unless it is `0600` or
    `0400` -- a lane's token, the platform's operator token, a webhook's token,
    a camera's `user:password`, an intercom's dial secret, and the shared token
    on the read surfaces. A fixture that wrote one at the default `0644` would
    be exercising a path the product refuses, so every test that needs a real
    credential comes through here.

    The permission is what makes this a FIXTURE OF THE THING and not of
    something adjacent: the tests that assert the refusal write `0644`
    deliberately, by hand, and they are the only ones that do.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(secret + "\n", encoding="utf-8")
    path.chmod(0o600)
    return path


def agent_config_for(
    tmp_path,
    *,
    lane_url: str | None = None,
    intercom: str = "sip:door1@10.0.0.9",
    standalone: bool = False,
    driver_languages=("en", "es-ES"),
    operator_language: str = "en",
    authorisations=("open_now", "open_and_flag", "do_not_open", "hold", "call_back"),
    human_sip_uri: str = "sip:duty@10.0.0.5",
    transfer_sip_uri: str | None = None,
    no_answer_seconds: float = 30.0,
    nothing_usable_seconds: float = 20.0,
    hold_reprompt_seconds: float = 45.0,
    audio_directory=None,
    extra_intercoms=(),
    name_audio_seconds: float = 0.5,
    name_audio_rate: int = 8000,
    name_audio_max_seconds: float = 10.0,
    line_timeout_seconds: float = 10.0,
    decision_max_age_seconds: float = 120.0,
    account_user: str = INTERCOM_ACCOUNT,
):
    """An `AgentConfig` built the way `from_dict` would, without a TOML file.

    The audio directory defaults to the one that SHIPPED with the package, so a
    test exercises the files a site would get rather than files a test wrote --
    a fixture of its own would make "every line has audio" a claim about the
    fixture.
    """
    from gate_agent.config import AgentConfig, Intercom, UserAgentSettings
    from gate_agent.config import Target as _Target
    from gate_agent.contract import Authorisation, TargetKind

    name_audio = wav(
        tmp_path / "site" / "door1.wav",
        seconds=name_audio_seconds,
        rate=name_audio_rate,
    )
    lanes = ()
    lane_name = None
    if lane_url is not None and not standalone:
        lane_name = "entry"
        lanes = (
            _Target(
                name=lane_name,
                kind=TargetKind.LANE,
                url=lane_url.rstrip("/"),
                poll_seconds=30.0,
                timeout_seconds=5.0,
            ),
        )
    intercoms = [
        Intercom(
            sip_uri=intercom,
            lane=lane_name,
            name_audio=name_audio,
            account_user=account_user,
        )
    ]
    intercoms.extend(extra_intercoms)
    return AgentConfig(
        agent_id=f"agent-{next(_ids)}",
        site_id="site-1",
        intercoms=tuple(intercoms),
        lanes=lanes,
        user_agent=UserAgentSettings(
            kind="baresip",
            host="127.0.0.1",
            port=4444,
            operator_aor="sip:agent-operator@10.0.0.20",
        ),
        driver_languages=tuple(driver_languages),
        operator_language=operator_language,
        authorisations=frozenset(Authorisation(one) for one in authorisations),
        human_sip_uri=human_sip_uri,
        audio_directory=audio_directory or _shipped_audio(),
        transfer_sip_uri=transfer_sip_uri,
        no_answer_seconds=no_answer_seconds,
        nothing_usable_seconds=nothing_usable_seconds,
        hold_reprompt_seconds=hold_reprompt_seconds,
        decision_max_age_seconds=decision_max_age_seconds,
        line_timeout_seconds=line_timeout_seconds,
        name_audio_max_seconds=name_audio_max_seconds,
    )


def _shipped_audio():
    from pathlib import Path

    import gate_agent

    return Path(gate_agent.__file__).resolve().parent / "audio"


def agent_for(config, user_agent=None, clock=None, now=None):
    """An `Agent` on a fake user agent and a clock a test can move.

    The fake is told which accounts the configuration declares, so that
    `Agent.start()`'s check finds them -- a test that means to measure the
    refusal sets `held_accounts` on the fake instead.
    """
    from fake_ua import FakeUa
    from gate_agent.agent import Agent

    user_agent = user_agent or FakeUa()
    if getattr(user_agent, "declared_accounts", None) == ():
        user_agent.declared_accounts = tuple(
            intercom.account_user for intercom in config.intercoms
        )
    agent = Agent(
        config,
        user_agent,
        clock=clock or FakeClock(),
        now=now or FakeUtc(),
    )
    agent.start()
    return agent


def agent_raw_for(tmp_path, *, lane_extra=None, dial_secret_file=None, tickets=None) -> dict:
    """The smallest agent configuration `AgentConfig.from_dict` accepts, as a dict.

    Here rather than in one test file because the credential sweep needs to
    build one too, and two copies of a minimal configuration is two things to
    keep in step with the refusals.
    """
    return {
        "agent": {"id": "agent-1", "site_id": "site-1"},
        "user_agent": {"operator_aor": "sip:agent-operator@10.0.0.20"},
        "lanes": {"entry": {"url": "http://127.0.0.1:8090", **(lane_extra or {})}},
        "intercoms": {
            "sip:door1@10.0.0.9": {
                "lane": "entry",
                "name_audio": str(wav(tmp_path / "door1.wav")),
                "dial_secret_file": dial_secret_file
                or str(secret_file(tmp_path / "door1.dial-secret")),
            }
        },
        "languages": {"driver": ["en"], "operator": "en"},
        "authorisations": {"open_now": True, "do_not_open": True},
        "escalation": {"human_sip_uri": "sip:duty@10.0.0.5"},
        **({"tickets": tickets} if tickets else {}),
    }


def capture_raw_for(directory, auth) -> dict:
    """The smallest capture configuration `CaptureConfig.from_dict` accepts."""
    return {
        "capture": {
            "id": "capture-1",
            "site_id": "site-1",
            "directory": str(directory),
            # DECLARED, no default: nothing here has ever seen a capture from
            # any of the cameras this is written for.
            "max_bytes": 1 << 30,
        },
        "cameras": {
            "front": {
                "snapshot_url": "http://camera.example.com/snap",
                "auth_file": str(auth),
            }
        },
    }
