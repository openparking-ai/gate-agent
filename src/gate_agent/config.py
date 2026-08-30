"""Both processes' configuration, read from TOML, and every refusal they make.

The monitor's and the capture process's, in one file, because the rules below
are the same rules and a second copy of them would be a second set of refusals
that agree by convention until they do not. `_refuse_credential_values`,
`_refuse_userinfo` and `_targets` are each written once and used by both.

Two rules run through the whole file and they are the reason it is this long.

**Every parameter is a per-site setting with a published default, and where no
default is safe it is DECLARED and refused at startup if absent.** A defaulted
recipient list, a defaulted garage, a defaulted credential: each of those is a
value nobody wrote, doing something at three in the morning that nobody chose.
The capture process adds the two sharpest cases of it: a defaulted DIRECTORY
would put a site's photographs of cars somewhere nobody named, and a defaulted
size cap would be a disk budget invented by a package that has never seen a
capture from any camera it is written for.
The lane's `[loops]` table established the shape -- a key spelt wrong is a key
that is missing, and both are found here rather than at 3am.

**A credential is read from a FILE, never taken as a value.** A value in a
configuration file is a credential in the file, in every backup of it and in
every paste of it into a chat window; a value on a command line is readable by
every user on the box for as long as the process runs. So a key that would carry
one is refused BY NAME, with the name of the file key that replaces it -- the
same rule the Vehicle ID service and the lane both apply, and refused rather
than merely undocumented because an undocumented key that works is a key that
gets used.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from .camera import DEFAULT_MAX_SNAPSHOT_BYTES, DEFAULT_SNAPSHOT_TIMEOUT
from .client import DEFAULT_TIMEOUT
from .contract import SinkKind, TargetKind

#: The published default for how often a target is polled.
#:
#: A PER-SITE SETTING AND AN ASSUMPTION. Nothing in this package measures how
#: often a lane's health changes, or how quickly a human needs to hear. It is
#: drawn short enough that a fault is reported while somebody is still on shift
#: and long enough that a monitor polling three targets is not a load on a
#: Jetson in a gate housing. A site that wants to hear sooner lowers it.
DEFAULT_POLL_SECONDS = 30.0

#: The published default for how long a lane device may go unheard-from before
#: `lane_gone_quiet` reads `active`.
#:
#: A PER-SITE SETTING AND AN ASSUMPTION, and it is the more consequential of the
#: two. Nothing here measures how often a healthy lane talks to its platform: a
#: busy entry touches it on every vehicle, and a quiet overnight exit may not
#: touch it for hours without anything being wrong. So this is drawn well above
#: any plausible gap between vehicles at a working lane and well below a shift,
#: and a site with a genuinely idle lane raises it rather than being paged.
DEFAULT_LANE_QUIET_SECONDS = 900.0

#: Whether an email sink negotiates TLS. Default ON, and it is a default rather
#: than a declaration because the safe value is knowable here: a monitor's
#: messages say which of a site's lanes are broken and when, which is a map for
#: whoever wants to arrive while nobody is watching.
DEFAULT_EMAIL_TLS = True

#: The published default for how many notifications `GET /v1/monitor/events`
#: can still serve behind the current cursor.
#:
#: A PER-SITE SETTING AND AN ASSUMPTION. Nothing here measures how far behind a
#: consumer of that route falls; it is drawn deep enough that a consumer polling
#: on any sane interval catches up, and shallow enough that the window is a
#: catch-up buffer rather than a log this process was never meant to be. Fall
#: further behind than this and the route says `reset` rather than serving a
#: short page. Published on `GET /v1/monitor`, the way the lane publishes its
#: own, because a consumer's catch-up policy depends on the number.
DEFAULT_EVENT_WINDOW_DEPTH = 256

#: The published default for how often each camera is photographed when nothing
#: has happened. Gokhan's spec, his words: *"camera captures an image every
#: minute"*.
#:
#: A PER-SITE SETTING AND AN ASSUMPTION. It is his operational judgement about
#: what a garage needs to be able to reconstruct, and nothing in this package
#: measures it -- a site that needs a finer record lowers it, and a site whose
#: disk cannot take it raises it and watches `projected_bytes_per_day`.
DEFAULT_CAPTURE_INTERVAL_SECONDS = 60.0

#: The published default for how long a capture is kept before it is deleted.
#:
#: **THIS CONSTANT IS THE ONLY COPY OF THAT DEFAULT.** The platform's identity
#: retention keeps its default in a database column and nowhere else; a lane has
#: no database and a capture process has no column, so the single copy lives
#: here and `docs/CONTRACT.md` publishes it out of this module through the
#: `<!--payload:-->` mechanism, with a test comparing the two by VALUE. Editing
#: the document goes red and editing this constant goes red.
#:
#: The window and the bounds are the platform's identity-retention window and
#: bounds, chosen once by Gokhan for personal data at rest in this estate. A
#: stored photograph of a car at a barrier is personal data in most places this
#: installs, so it gets the same answer rather than a second one.
DEFAULT_RETENTION_DAYS = 30

#: The bounds a site's `retention_days` must fall inside. Published beside the
#: default, from this one copy. A day is the shortest window that survives an
#: overnight incident being looked at the next morning; ten years is past any
#: retention any of this estate's jurisdictions asks for, and a site typing a
#: number outside them has made a mistake this refuses rather than honours.
RETENTION_DAYS_BOUNDS = (1, 3650)

#: The published default for how long a camera has to answer one snapshot. The
#: constant lives in `camera.py`, beside the code that waits, and is bound here
#: because this is where a site overrides it -- one copy, and the reason it is
#: the number it is travels with it.
DEFAULT_SNAPSHOT_TIMEOUT_SECONDS = DEFAULT_SNAPSHOT_TIMEOUT

#: The published default for the most this process reads from one camera. The
#: constant lives in `camera.py`, beside the code that stops reading, and is
#: bound here because this is where a site overrides it.
#:
#: **IT IS REFUSED AT STARTUP UNLESS IT IS BELOW `[capture] max_bytes`.** The
#: store evicts to make room for what arrives, so a ceiling at or above the
#: whole cap would let ONE camera's answer decide how much of a site's store
#: survives -- and the length of that answer is the camera's to choose.
DEFAULT_MAX_SNAPSHOT_BYTES_SETTING = DEFAULT_MAX_SNAPSHOT_BYTES

#: What a camera id may be made of. A camera id becomes part of every filename
#: in the store, so one carrying a `/` or a `..` would write a site's captures
#: somewhere nobody declared -- and one carrying a space or a colon makes a
#: directory nobody can work with over a shell. Refused at startup, by name.
CAMERA_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

#: The published default for how long a target has to answer, per target.
#: The constant lives in `client.py`, beside the code that waits, and is bound
#: here because this is where a site overrides it -- one copy, and the reason it
#: is the number it is travels with it.
DEFAULT_TIMEOUT_SECONDS = DEFAULT_TIMEOUT

#: Keys that would carry a credential BY VALUE. Refused by name, each with the
#: file key that replaces it. A key nobody documented but which happens to work
#: is a key that ends up in a configuration file, so these are refused loudly
#: instead of being ignored.
CREDENTIAL_VALUE_KEYS = {
    "token": "token_file",
    "auth_token": "token_file",
    "bearer_token": "token_file",
    "password": "password_file",
    "secret": "token_file",
    "api_key": "token_file",
}


class ConfigError(ValueError):
    """A configuration this monitor will not run on, named at startup.

    Every one of these is raised before a socket is opened or a target is
    polled. A monitor that started on a configuration it could not honour would
    be reporting on a subset of what somebody asked it to watch, and reporting
    it as if it were the whole.
    """


@dataclass(frozen=True, slots=True)
class Target:
    """One thing to watch."""

    name: str
    kind: TargetKind
    url: str
    poll_seconds: float
    #: The bearer token, already read out of the file that held it. `None` when
    #: the target needs none -- a loopback lane, and the Vehicle ID health route,
    #: which that contract keeps unauthenticated by its own decision because it
    #: carries no plate and no image.
    token: str | None = None
    #: PLATFORM ONLY, and declared: which garage's devices to read. There is no
    #: safe default -- a monitor pointed at the wrong garage reports no devices
    #: and therefore nothing wrong, which is worse than not running.
    garage_id: str | None = None
    #: PLATFORM ONLY: how long a device may go unheard-from. Setting, default
    #: published above, and an assumption.
    lane_quiet_seconds: float = DEFAULT_LANE_QUIET_SECONDS
    #: How long this target has to answer. Setting, default published above,
    #: and an assumption -- see `client.DEFAULT_TIMEOUT` for what it is drawn
    #: against, which is the lane's own bound on the machine BEHIND it.
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS

    @property
    def authenticated(self) -> bool:
        return self.token is not None


@dataclass(frozen=True, slots=True)
class LogSinkConfig:
    name: str = "log"


@dataclass(frozen=True, slots=True)
class EmailSinkConfig:
    """SMTP. Every field declared; there is no default recipient anywhere.

    A default recipient is the failure this whole module exists to prevent
    wearing a helpful costume: it would page somebody who never asked, or --
    far more likely -- page a placeholder nobody reads, while the site believes
    it is covered.

    There is deliberately NO SMTP credential in this version. Not an oversight:
    a credential is a file and a decision about which file, and nobody has made
    it. A site needing an authenticated relay finds out here, at startup, rather
    than discovering that its alerts have been rejected for a fortnight.
    """

    host: str
    port: int
    sender: str
    recipients: tuple[str, ...]
    tls: bool = DEFAULT_EMAIL_TLS
    name: str = "email"


@dataclass(frozen=True, slots=True)
class WebhookSinkConfig:
    """A POST to a per-site URL with a bearer token read from a file.

    This is the seat a third party's paging system takes: whatever already wakes
    that site's people receives the same notification object the log sink prints
    and the email sink renders.
    """

    url: str
    token: str
    name: str = "webhook"


@dataclass(frozen=True, slots=True)
class MonitorConfig:
    monitor_id: str
    site_id: str
    targets: tuple[Target, ...]
    sinks: tuple[object, ...]
    #: How long a state must hold before it is reported AGAIN. `None` -- the
    #: default -- means never: a fault is announced once and its recovery is
    #: announced once. A site that wants to be reminded while a lane is still
    #: down sets it, and gets one message per interval per code.
    renotify_seconds: float | None = None
    #: How many notifications `GET /v1/monitor/events` can serve behind the
    #: cursor. A per-site SETTING with a published default, `[monitor]
    #: event_window_depth` -- it was a constant reachable only from Python for
    #: one round, which made "every parameter is a per-site setting with a
    #: published default" untrue of exactly one parameter.
    event_window_depth: int = DEFAULT_EVENT_WINDOW_DEPTH

    @classmethod
    def from_file(cls, path: str | Path) -> MonitorConfig:
        try:
            with open(path, "rb") as handle:
                raw = tomllib.load(handle)
        except OSError as exc:
            raise ConfigError(f"could not read {path}: {exc}") from exc
        except tomllib.TOMLDecodeError as exc:
            raise ConfigError(f"{path} is not valid TOML: {exc}") from exc
        return cls.from_dict(raw, relative_to=Path(path).resolve().parent)

    @classmethod
    def from_dict(cls, raw: dict, relative_to: Path | None = None) -> MonitorConfig:
        _refuse_credential_values(raw, "")
        monitor = _table(raw, "monitor")
        for key in ("id", "site_id"):
            if key not in monitor:
                raise ConfigError(
                    f"[monitor] does not declare {key}. A monitor with no identity cannot be "
                    "told apart from another site's in a message or on its own surface."
                )
        targets = _targets(_table(raw, "targets", required=False), relative_to)
        if not targets:
            raise ConfigError(
                "no targets are declared. A monitor watching nothing has nothing to report and "
                "would report exactly that -- 'all fine' -- which is the lie this module exists "
                "to prevent. Declare at least one of "
                f"{', '.join('[targets.' + kind.value + ']' for kind in TargetKind)}."
            )
        return cls(
            monitor_id=str(monitor["id"]),
            site_id=str(monitor["site_id"]),
            targets=targets,
            sinks=_sinks(_table(raw, "sinks", required=False), relative_to),
            renotify_seconds=_renotify(_table(raw, "notify", required=False)),
            event_window_depth=_positive_int(
                monitor.get("event_window_depth"),
                "[monitor].event_window_depth",
                DEFAULT_EVENT_WINDOW_DEPTH,
            ),
        )


@dataclass(frozen=True, slots=True)
class CameraConfig:
    """One camera, and the credential already read out of the file that held it.

    There is no default camera anywhere in this module. A capture process that
    started with a camera nobody declared would photograph something nobody
    chose, and file it under an id nobody wrote, in a directory somebody else's
    retention rule applies to.
    """

    camera_id: str
    snapshot_url: str
    #: From `auth_file`. Both or neither; the file is refused if it holds one.
    username: str | None
    password: str | None
    #: How long this camera has to answer one snapshot. Setting, default
    #: published above, and an assumption. It is a DEADLINE on the whole read.
    timeout_seconds: float = DEFAULT_SNAPSHOT_TIMEOUT_SECONDS
    #: The most this process reads from this camera. `[capture]
    #: max_snapshot_bytes`, carried per camera because it is the camera the
    #: reader is holding when it applies.
    max_snapshot_bytes: int = DEFAULT_MAX_SNAPSHOT_BYTES_SETTING

    @property
    def authenticated(self) -> bool:
        return self.username is not None


@dataclass(frozen=True, slots=True)
class CaptureConfig:
    """What the capture process was told to do, after every refusal it makes.

    Two of these have NO DEFAULT and are refused when absent, and they are the
    two nobody may choose on a site's behalf. `directory` is where a site's
    personal data will sit, and a default would put photographs of cars
    somewhere the person who installed it never named. `max_bytes` is that
    site's disk, and **nothing in this package has ever seen a capture from any
    camera it is written for** -- so there is no measurement here to draw a
    default from, and a plausible number would be a figure that looked measured.
    """

    capture_id: str
    site_id: str
    directory: Path
    max_bytes: int
    cameras: tuple[CameraConfig, ...]
    #: The most this process reads from one camera. A SETTING with a published
    #: default, refused at startup unless it is BELOW `max_bytes` -- see
    #: `DEFAULT_MAX_SNAPSHOT_BYTES_SETTING` for why that bound is the one that
    #: matters.
    max_snapshot_bytes: int = DEFAULT_MAX_SNAPSHOT_BYTES_SETTING
    #: The lane whose events trigger captures, or `None`. **Standalone is a
    #: MODE**: a garage with a camera and no gate is a customer of this process,
    #: and it takes minute captures and says so on the line it prints at start.
    lane: Target | None = None
    interval_seconds: float = DEFAULT_CAPTURE_INTERVAL_SECONDS
    retention_days: int = DEFAULT_RETENTION_DAYS

    @classmethod
    def from_file(cls, path: str | Path) -> CaptureConfig:
        try:
            with open(path, "rb") as handle:
                raw = tomllib.load(handle)
        except OSError as exc:
            raise ConfigError(f"could not read {path}: {exc}") from exc
        except tomllib.TOMLDecodeError as exc:
            raise ConfigError(f"{path} is not valid TOML: {exc}") from exc
        return cls.from_dict(raw, relative_to=Path(path).resolve().parent)

    @classmethod
    def from_dict(cls, raw: dict, relative_to: Path | None = None) -> CaptureConfig:
        _refuse_credential_values(raw, "")
        capture = _table(raw, "capture")
        for key in ("id", "site_id"):
            if key not in capture:
                raise ConfigError(
                    f"[capture] does not declare {key}. A capture process with no identity "
                    "cannot be told apart from another site's on its own surface or in a "
                    "monitor's message."
                )
        directory = capture.get("directory")
        if not isinstance(directory, str) or not directory.strip():
            raise ConfigError(
                "[capture] does not declare directory. There is no default: this is where a "
                "site's photographs of cars are kept, and a default would put personal data "
                "somewhere nobody named. Whether it exists and takes a write is checked when "
                "the store opens."
            )
        if "max_bytes" not in capture:
            raise ConfigError(
                "[capture] does not declare max_bytes. There is no default and there is no "
                "measurement to draw one from: nothing in this package has ever seen a capture "
                "from any of the cameras it is written for, so a number here would be invented "
                "and would look measured. Write what this store's disk can spare."
            )
        max_bytes = _positive_int(capture["max_bytes"], "[capture].max_bytes", 0)
        retention_days = _positive_int(
            capture.get("retention_days"), "[capture].retention_days", DEFAULT_RETENTION_DAYS
        )
        low, high = RETENTION_DAYS_BOUNDS
        if not low <= retention_days <= high:
            raise ConfigError(
                f"[capture].retention_days is {retention_days}, outside {low}-{high}. Below the "
                "floor an overnight incident is deleted before anybody looks at it in the "
                "morning; above the ceiling this store keeps personal data longer than anything "
                "else in this estate is allowed to."
            )
        max_snapshot_bytes = _positive_int(
            capture.get("max_snapshot_bytes"),
            "[capture].max_snapshot_bytes",
            DEFAULT_MAX_SNAPSHOT_BYTES_SETTING,
        )
        if max_snapshot_bytes >= max_bytes:
            raise ConfigError(
                f"[capture].max_snapshot_bytes is {max_snapshot_bytes} and [capture].max_bytes "
                f"is {max_bytes}. The ceiling on ONE read must be below the cap on the WHOLE "
                "store: the store evicts to make room for what arrives, so a ceiling at or "
                "above the cap lets one camera's answer decide how much of this site's store "
                "survives -- and how long that answer is, is the camera's to choose."
            )
        cameras = _cameras(_table(raw, "cameras"), relative_to, max_snapshot_bytes)
        lanes = _targets(_table(raw, "targets", required=False), relative_to, (TargetKind.LANE,))
        return cls(
            capture_id=str(capture["id"]),
            site_id=str(capture["site_id"]),
            directory=_resolve(directory, relative_to),
            max_bytes=max_bytes,
            max_snapshot_bytes=max_snapshot_bytes,
            cameras=cameras,
            lane=lanes[0] if lanes else None,
            interval_seconds=_positive(
                capture.get("interval_seconds"),
                "[capture].interval_seconds",
                DEFAULT_CAPTURE_INTERVAL_SECONDS,
            ),
            retention_days=retention_days,
        )


# ---------------------------------------------------------------------------
# The refusals
# ---------------------------------------------------------------------------


def _table(raw: dict, key: str, required: bool = True) -> dict:
    value = raw.get(key)
    if value is None:
        if required:
            raise ConfigError(f"the configuration has no [{key}] table")
        return {}
    if not isinstance(value, dict):
        raise ConfigError(f"[{key}] must be a table, got {type(value).__name__}")
    return value


def _refuse_credential_values(raw: dict, path: str) -> None:
    """Refuse any key that would carry a credential as a VALUE, anywhere.

    Walked over the whole document rather than checked at the three places a
    token is expected, because the point is not to catch a typo -- it is that
    this file never learns to accept one. A key that works undocumented is a key
    somebody uses, and the credential is then in the configuration file, in
    every backup of it, and in every paste of it into a chat window.
    """
    for key, value in raw.items():
        here = f"{path}.{key}" if path else key
        if isinstance(value, dict):
            _refuse_credential_values(value, here)
            continue
        if isinstance(value, list):
            # A TOML array of tables is a list of dicts, and the walk used to
            # step over it. Nothing in today's schema is one, which is exactly
            # why this is worth closing now: the day a site declares two
            # webhooks, the sweep would not have been looking.
            for index, item in enumerate(value):
                if isinstance(item, dict):
                    _refuse_credential_values(item, f"{here}[{index}]")
            continue
        if key in CREDENTIAL_VALUE_KEYS:
            raise ConfigError(
                f"`{here}` would hold a credential as a value. Write "
                f"`{CREDENTIAL_VALUE_KEYS[key]}` with the path to a file holding it instead: a "
                "value here is a credential in this file, in every backup of it, and in "
                "everything anyone ever pastes it into."
            )


def _refuse_userinfo(url: str, where: str) -> None:
    """A URL may not carry a credential either, and one carries very well.

    `https://ops:S3CRET@example.com` is a working, documented way to put a
    password in this file -- the same thing the six key names above are refused
    for, in a key that is required rather than optional. It was accepted, and
    then republished verbatim on `GET /v1/monitor` beside `authenticated:
    false`, so the one field a consumer would use to notice read the wrong way.

    Refused by SHAPE, not by pattern: anything urllib parses as userinfo is
    userinfo, whatever it looks like.
    """
    parsed = urlsplit(url)
    if parsed.username or parsed.password:
        raise ConfigError(
            f"{where} has userinfo in URL: credentials come from files. Write the host on its "
            "own and give the credential's path in `token_file` -- a credential in a URL is a "
            "credential in this file, in every backup of it, and on the read surface that "
            "republishes what this monitor watches."
        )


def _read_token(value, where: str, relative_to: Path | None) -> str:
    """A credential, out of the file that holds it.

    An empty or whitespace-only file is refused rather than read as "no
    credential configured", which is a truncated file silently turning
    authentication off on exactly the target that needed it.
    """
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{where} must be a path to a file holding the token")
    path = Path(value)
    if not path.is_absolute() and relative_to is not None:
        path = relative_to / path
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"{where}: could not read {path}: {exc}") from exc
    token = raw.strip()
    if not token:
        raise ConfigError(f"{where}: {path} holds no token")
    return token


def _positive(value, where: str, default: float) -> float:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise ConfigError(f"{where} must be a positive number of seconds, got {value!r}")
    return float(value)


def _positive_int(value, where: str, default: int) -> int:
    """A count, not a duration. `True` is an `int` in Python and is not one here.

    Split from `_positive` rather than sharing it: a window depth of 2.5
    notifications is a configuration error, and a float that silently truncated
    would be a setting that does something nobody wrote.
    """
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ConfigError(f"{where} must be a positive whole number, got {value!r}")
    return value


def _targets(
    raw: dict, relative_to: Path | None, kinds: tuple[TargetKind, ...] | None = None
) -> tuple[Target, ...]:
    """The declared targets, restricted to the kinds THIS process can read.

    `kinds` is what makes one builder serve two processes: the monitor reads
    every kind, and the capture process reads a lane and nothing else -- so
    `[targets.platform]` in a capture configuration is refused by name here
    rather than parsed and ignored. One set of refusals, one set of credential
    rules, one place they are written.
    """
    kinds = kinds or tuple(TargetKind)
    known = {kind.value for kind in kinds}
    unknown = sorted(set(raw) - known)
    if unknown:
        raise ConfigError(
            f"[targets] declares {', '.join(unknown)}, which this process has no reader for. "
            f"The kinds it can read are {', '.join(sorted(known))} -- a target it cannot "
            "interpret would be reported on as though it had been understood."
        )
    targets = []
    for kind in kinds:
        table = raw.get(kind.value)
        if table is None:
            continue
        if not isinstance(table, dict):
            raise ConfigError(f"[targets.{kind.value}] must be a table")
        url = table.get("url")
        if not isinstance(url, str) or not url.strip():
            raise ConfigError(f"[targets.{kind.value}] does not declare a url")
        _refuse_userinfo(url, f"[targets.{kind.value}].url")
        token = None
        if "token_file" in table:
            token = _read_token(
                table["token_file"], f"[targets.{kind.value}].token_file", relative_to
            )
        garage_id = None
        quiet = DEFAULT_LANE_QUIET_SECONDS
        if kind is TargetKind.PLATFORM:
            # Both DECLARED. The platform's devices route is tenant-scoped from
            # the token and garage-scoped from the path, and neither has a safe
            # default: a monitor with no token reads nothing, and a monitor
            # pointed at a garage nobody chose reports no devices and therefore
            # nothing wrong.
            if token is None:
                raise ConfigError(
                    "[targets.platform] does not declare token_file. The platform's operator "
                    "surface is authenticated and the tenant comes from the token, so there is "
                    "nothing to read without one -- and a monitor that read nothing would "
                    "report nothing wrong."
                )
            garage_id = table.get("garage_id")
            if not isinstance(garage_id, str) or not garage_id.strip():
                raise ConfigError(
                    "[targets.platform] does not declare garage_id. There is no default: a "
                    "monitor pointed at a garage nobody chose lists no devices, and no devices "
                    "reads exactly like no faults."
                )
            quiet = _positive(
                table.get("lane_quiet_seconds"),
                "[targets.platform].lane_quiet_seconds",
                DEFAULT_LANE_QUIET_SECONDS,
            )
        targets.append(
            Target(
                name=kind.value,
                kind=kind,
                url=url.rstrip("/"),
                poll_seconds=_positive(
                    table.get("poll_seconds"),
                    f"[targets.{kind.value}].poll_seconds",
                    DEFAULT_POLL_SECONDS,
                ),
                token=token,
                garage_id=garage_id,
                lane_quiet_seconds=quiet,
                timeout_seconds=_positive(
                    table.get("timeout_seconds"),
                    f"[targets.{kind.value}].timeout_seconds",
                    DEFAULT_TIMEOUT_SECONDS,
                ),
            )
        )
    return tuple(targets)


def _sinks(raw: dict, relative_to: Path | None) -> tuple[object, ...]:
    """The declared sinks. `log` is always one of them, declared or not.

    A site that declares only `log` is VALID and it is a real configuration --
    the notifications go to this process's stdout and to whatever collects it.
    It is also a site where **nobody is paged**, and that is stated here, in the
    startup line, and in the receipt, rather than being a fact somebody
    discovers the first time a lane goes down at midnight.
    """
    known = {kind.value for kind in SinkKind}
    unknown = sorted(set(raw) - known)
    if unknown:
        raise ConfigError(
            f"[sinks] declares {', '.join(unknown)}, which is not a sink this version has. "
            f"The set is closed: {', '.join(sorted(known))}. SMS is a later round's provider "
            "and adding it is an additive change, not a configuration key that already works."
        )
    sinks: list[object] = [LogSinkConfig()]
    email = raw.get(SinkKind.EMAIL.value)
    if email is not None:
        if not isinstance(email, dict):
            raise ConfigError("[sinks.email] must be a table")
        for key in ("host", "port", "from", "to"):
            if key not in email:
                raise ConfigError(
                    f"[sinks.email] does not declare {key}. Every one of host, port, from and to "
                    "is required: there is no default recipient anywhere in this module, because "
                    "a default recipient pages somebody who never asked or -- far more likely -- "
                    "a placeholder nobody reads, while the site believes it is covered."
                )
        recipients = email["to"]
        if isinstance(recipients, str):
            recipients = [recipients]
        if not isinstance(recipients, list) or not recipients or not all(
            isinstance(one, str) and one.strip() for one in recipients
        ):
            raise ConfigError("[sinks.email].to must be one address or a list of them")
        port = email["port"]
        if isinstance(port, bool) or not isinstance(port, int) or not 0 < port < 65536:
            raise ConfigError(f"[sinks.email].port must be a port number, got {port!r}")
        sinks.append(
            EmailSinkConfig(
                host=str(email["host"]),
                port=port,
                sender=str(email["from"]),
                recipients=tuple(recipients),
                tls=bool(email.get("tls", DEFAULT_EMAIL_TLS)),
            )
        )
    webhook = raw.get(SinkKind.WEBHOOK.value)
    if webhook is not None:
        if not isinstance(webhook, dict):
            raise ConfigError("[sinks.webhook] must be a table")
        url = webhook.get("url")
        if not isinstance(url, str) or not url.strip():
            raise ConfigError("[sinks.webhook] does not declare a url")
        _refuse_userinfo(url, "[sinks.webhook].url")
        if "token_file" not in webhook:
            raise ConfigError(
                "[sinks.webhook] does not declare token_file. This sink POSTs a site's "
                "malfunctions to a URL, so it authenticates -- and the token is a file, never "
                "a value."
            )
        sinks.append(
            WebhookSinkConfig(
                url=url,
                token=_read_token(
                    webhook["token_file"], "[sinks.webhook].token_file", relative_to
                ),
            )
        )
    return tuple(sinks)


def _cameras(
    raw: dict, relative_to: Path | None, max_snapshot_bytes: int
) -> tuple[CameraConfig, ...]:
    """Every declared camera, and the four refusals a camera can earn.

    A capture process with NO camera is refused here, for the reason a monitor
    with no target is: it would run, publish a healthy surface, and record
    nothing, which is the shape of every quiet failure this module exists to
    prevent.
    """
    if not raw:
        raise ConfigError(
            "no camera is declared. A capture process with no camera photographs nothing and "
            "would publish a working surface while doing it. Declare at least one "
            "[cameras.<id>] with a snapshot_url and an auth_file."
        )
    cameras = []
    for camera_id in sorted(raw):
        if not CAMERA_ID.match(camera_id):
            raise ConfigError(
                f"[cameras.{camera_id}] is not a usable camera id. It becomes part of the name "
                "of every file this camera's captures are stored under, so it may hold letters, "
                "digits, `_` and `-` only: an id carrying a `/` or a `..` would write a site's "
                "captures somewhere nobody declared."
            )
        table = raw[camera_id]
        if not isinstance(table, dict):
            raise ConfigError(f"[cameras.{camera_id}] must be a table")
        url = table.get("snapshot_url")
        if not isinstance(url, str) or not url.strip():
            raise ConfigError(
                f"[cameras.{camera_id}] does not declare a snapshot_url. It is the address of a "
                "route that answers a JPEG to a GET, and there is no default camera anywhere in "
                "this module."
            )
        _refuse_userinfo(url, f"[cameras.{camera_id}].snapshot_url")
        if "auth_file" not in table:
            raise ConfigError(
                f"[cameras.{camera_id}] does not declare auth_file. A snapshot route with no "
                "credential is a camera anyone who can reach that network can photograph, and "
                "the credential is a FILE -- there is no key here that takes one as a value."
            )
        username, password = _read_camera_auth(
            table["auth_file"], f"[cameras.{camera_id}].auth_file", relative_to
        )
        cameras.append(
            CameraConfig(
                camera_id=camera_id,
                snapshot_url=url,
                username=username,
                password=password,
                timeout_seconds=_positive(
                    table.get("timeout_seconds"),
                    f"[cameras.{camera_id}].timeout_seconds",
                    DEFAULT_SNAPSHOT_TIMEOUT_SECONDS,
                ),
                max_snapshot_bytes=max_snapshot_bytes,
            )
        )
    return tuple(cameras)


def _read_camera_auth(value, where: str, relative_to: Path | None) -> tuple[str, str]:
    """A camera's credential, out of the file that holds it: `user:password`.

    One line, split on the FIRST colon -- a password may hold one and HTTP
    authentication forbids a username that does. A file with no colon is refused
    rather than read as a password with no user, because a camera answering a
    challenge this process cannot meet is a camera that reads as refusing us,
    which sends a human to the wrong machine.
    """
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{where} must be a path to a file holding `user:password`")
    path = Path(value)
    if not path.is_absolute() and relative_to is not None:
        path = relative_to / path
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"{where}: could not read {path}: {exc}") from exc
    line = raw.strip().splitlines()[0].strip() if raw.strip() else ""
    if ":" not in line:
        raise ConfigError(
            f"{where}: {path} does not hold `user:password` on its first line. An empty or "
            "truncated file read as `no credential configured` would turn authentication off "
            "on exactly the camera that needed it."
        )
    username, password = line.split(":", 1)
    if not username or not password:
        raise ConfigError(f"{where}: {path} holds an empty user or an empty password")
    return username, password


def _resolve(value: str, relative_to: Path | None) -> Path:
    """A path from the file, made absolute against the file it was written in."""
    path = Path(value)
    if not path.is_absolute() and relative_to is not None:
        path = relative_to / path
    return path


def _renotify(raw: dict) -> float | None:
    """`None` unless this site asked to be reminded. There is no default.

    A default re-notify interval is a decision about how often to wake somebody,
    made by whoever wrote this file for every site that never mentioned it.
    """
    if "renotify_seconds" not in raw:
        return None
    return _positive(raw["renotify_seconds"], "[notify].renotify_seconds", 0.0)


__all__ = [
    "CAMERA_ID",
    "CREDENTIAL_VALUE_KEYS",
    "DEFAULT_CAPTURE_INTERVAL_SECONDS",
    "DEFAULT_EMAIL_TLS",
    "DEFAULT_EVENT_WINDOW_DEPTH",
    "DEFAULT_RETENTION_DAYS",
    "DEFAULT_MAX_SNAPSHOT_BYTES_SETTING",
    "DEFAULT_SNAPSHOT_TIMEOUT_SECONDS",
    "DEFAULT_TIMEOUT_SECONDS",
    "DEFAULT_LANE_QUIET_SECONDS",
    "DEFAULT_POLL_SECONDS",
    "RETENTION_DAYS_BOUNDS",
    "CameraConfig",
    "CaptureConfig",
    "ConfigError",
    "EmailSinkConfig",
    "LogSinkConfig",
    "MonitorConfig",
    "Target",
    "WebhookSinkConfig",
]
