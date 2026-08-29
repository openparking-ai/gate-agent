"""The monitor's configuration, read from TOML, and every refusal it makes.

Two rules run through the whole file and they are the reason it is this long.

**Every parameter is a per-site setting with a published default, and where no
default is safe it is DECLARED and refused at startup if absent.** A defaulted
recipient list, a defaulted garage, a defaulted credential: each of those is a
value nobody wrote, doing something at three in the morning that nobody chose.
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

import tomllib
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

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
    #: How many notifications the events route can serve behind the cursor.
    event_window_depth: int = 256

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


def _targets(raw: dict, relative_to: Path | None) -> tuple[Target, ...]:
    known = {kind.value for kind in TargetKind}
    unknown = sorted(set(raw) - known)
    if unknown:
        raise ConfigError(
            f"[targets] declares {', '.join(unknown)}, which this monitor has no reader for. "
            f"The kinds it can read are {', '.join(sorted(known))} -- a target it cannot "
            "interpret would be reported on as though it had been understood."
        )
    targets = []
    for kind in TargetKind:
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


def _renotify(raw: dict) -> float | None:
    """`None` unless this site asked to be reminded. There is no default.

    A default re-notify interval is a decision about how often to wake somebody,
    made by whoever wrote this file for every site that never mentioned it.
    """
    if "renotify_seconds" not in raw:
        return None
    return _positive(raw["renotify_seconds"], "[notify].renotify_seconds", 0.0)


__all__ = [
    "CREDENTIAL_VALUE_KEYS",
    "DEFAULT_EMAIL_TLS",
    "DEFAULT_TIMEOUT_SECONDS",
    "DEFAULT_LANE_QUIET_SECONDS",
    "DEFAULT_POLL_SECONDS",
    "ConfigError",
    "EmailSinkConfig",
    "LogSinkConfig",
    "MonitorConfig",
    "Target",
    "WebhookSinkConfig",
]
