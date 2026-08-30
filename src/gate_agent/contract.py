"""The monitor contract. This module IS the monitor's public read surface.

Everything else in this package is an implementation detail that may be
rewritten; the payloads below are not. They are versioned from the first release
so a change to them is always visible as one, and they follow the same
compatibility policy the lane contract and the Vehicle ID contract state, in the
same words, so one consumer can hold one policy for all three.

**This surface is READ ONLY, and the module behind it has no opening authority
at all.** The monitor reads GETs and sends messages. It never calls a vend,
never resolves a transit, and never writes to a lane -- there is no client in
this package capable of a method other than `GET`, and that is swept rather than
promised. A monitor that could act would be a new route to a barrier, which is
the boundary every outside reviewer of this project has named.

Four properties are the whole point, and each is enforced below rather than
described and hoped for:

  * **A state nobody derived is `unknown`, never `ok`.** `MonitorEntry` refuses
    `ok` or `active` from a code this build does not derive, exactly as the lane
    contract's `HealthEntry` does. A monitor that reports a clean bill of health
    for something it never measured is the one lie this module exists to
    prevent.

  * **A target's codes are PASSED THROUGH.** The state and the source a target
    published are what this surface publishes. Not re-derived, not re-labelled,
    not translated into our vocabulary. A monitor that re-stated a lane's health
    in its own words would be a second copy of a claim, and the copy is the one
    that goes wrong.

  * **`never_alarm` is read from the PAYLOAD.** Whether a code may page a human
    travels on the wire with that code. This package holds no list of its own,
    because two lists drift and the one that drifts is the one that pages a
    technician because a car arrived.

  * **A notification is a TRANSITION, not a state.** It names what changed, and
    it carries no plate, no image reference and no event detail -- the monitor
    reads `/health`, not `/events`, so it never holds one.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import StrEnum
from urllib.parse import urlsplit

#: Bumped whenever a payload's shape changes in a way a consumer could notice.
#: Additive changes do not bump it and a consumer ignores fields it does not
#: know -- the same rule the lane and Vehicle ID contracts state, so one
#: consumer can hold one policy for all three.
CONTRACT_VERSION = 1


class TargetKind(StrEnum):
    """What kind of thing a target is, and therefore how it is read.

    CLOSED. Each member names a contract this monitor knows how to read, and a
    monitor that could be pointed at something it has no reader for would be
    reporting on a thing it cannot interpret.
    """

    #: A service implementing `lane-controller/docs/CONTRACT.md`. Ours or a
    #: third party's -- the monitor has no branch on which.
    LANE = "lane"
    #: A Vehicle ID service, read through its unauthenticated `GET /v1/health`.
    IDENTITY_SERVICE = "identity_service"
    #: An Open Parking AI platform, read through its operator surface.
    PLATFORM = "platform"
    #: A capture process implementing the capture half of THIS contract. It is
    #: the same shape as a lane's health surface on purpose -- one reader, one
    #: passthrough rule -- and it is how `camera_unreachable` gets from a camera
    #: nobody can reach to a human who can go and look at it.
    CAPTURE = "capture"


class SinkKind(StrEnum):
    """How a human is told. CLOSED this version, and stated as closed.

    SMS is a later round's provider and is deliberately not here. The set being
    closed and published is what makes adding one an additive change with a
    version bump behind it, rather than a surprise in somebody's configuration.
    """

    #: Structured JSON to stdout. Always on, and the one sink that needs no
    #: provider, no credential and no network.
    LOG = "log"
    EMAIL = "email"
    WEBHOOK = "webhook"


class MonitorCode(StrEnum):
    """The malfunctions the MONITOR ITSELF measures.

    Every one of them is about the monitor's own view of the world -- something
    it could not reach, something it could not tell anyone about, something a
    target said about itself that this build cannot read. A target's own codes
    are not in here: they are that target's vocabulary and they are published
    unchanged, under `targets`.

    The set is CLOSED and every member ships on every response, for the reason
    the lane's is: a code that is absent reads to a consumer exactly like a code
    that is fine.
    """

    #: The lane target did not answer. This is "no connection" for a lane, and
    #: it is measured by the monitor because nothing else can measure it.
    LANE_UNREACHABLE = "lane_unreachable"
    IDENTITY_SERVICE_UNREACHABLE = "identity_service_unreachable"
    PLATFORM_UNREACHABLE = "platform_unreachable"
    CAPTURE_UNREACHABLE = "capture_unreachable"
    #: The target ANSWERED, and what it answered was no -- a 3xx or a 4xx. It
    #: is not down: it is up, it received the request, and it declined it, and
    #: the `status` on the entry says which decline. A dead credential (401), a
    #: platform older than this build (404) and a target steering this monitor
    #: at another host (3xx) are three different repairs on three different
    #: machines, and every one of them used to be published as "that target is
    #: unreachable" -- which names the wrong machine and carries no status for a
    #: human to tell them apart with.
    LANE_REFUSED_US = "lane_refused_us"
    IDENTITY_SERVICE_REFUSED_US = "identity_service_refused_us"
    PLATFORM_REFUSED_US = "platform_refused_us"
    CAPTURE_REFUSED_US = "capture_refused_us"
    #: A target answered with a contract version this build does not know. Its
    #: codes STOP being passed through while this holds: half-understanding a
    #: payload is worse than admitting you cannot read it.
    TARGET_CONTRACT_UNSUPPORTED = "target_contract_unsupported"
    #: A sink could not deliver. Reported on this surface AND to every other
    #: sink that works -- never wrong silently applies to the messenger too.
    SINK_DELIVERY_FAILED = "sink_delivery_failed"
    #: A lane device the PLATFORM has not heard from for longer than this site
    #: allows. Derived from `lane_devices.last_seen_at`, which only the platform
    #: can see: a lane that has gone quiet is quiet, so the fault is invisible
    #: from the lane's own end.
    LANE_GONE_QUIET = "lane_gone_quiet"


class HealthState(StrEnum):
    """Copied from the lane contract, in its words, because it is its rule.

    A consumer holding one policy for both surfaces would otherwise have to hold
    two definitions of `unknown`, and the day they diverge is the day one of
    them starts meaning `ok`.
    """

    #: Somebody measured, and found nothing wrong.
    OK = "ok"
    #: The malfunction is happening.
    ACTIVE = "active"
    #: Nobody measured. NEVER read as `ok`.
    UNKNOWN = "unknown"


class Source(StrEnum):
    """Where a code's answer comes from, published beside every answer."""

    MEASURED = "measured"
    NOT_MEASURED = "not_measured"
    NO_SOURCE = "no_source"


class Transition(StrEnum):
    """What a notification is ABOUT. States are not sent; changes are.

    A monitor that sent the current state would send the same message every poll
    for as long as a fault lasted, and a human who has been told a thousand
    times has been told nothing.
    """

    #: `ok` or `unknown` became `active`.
    RAISED = "raised"
    #: `active` became `ok`.
    RECOVERED = "recovered"
    #: `ok` or `active` became `unknown`. Sent ONCE. It is not a recovery and it
    #: is not a fault: it is the loss of a measurement, which is its own event
    #: and the one a monitor most easily hides.
    NO_LONGER_MEASURED = "no_longer_measured"
    #: The state has not changed and this site set a re-notify interval. Only an
    #: `active` state repeats; a held `ok` is not news and a repeated recovery
    #: is not a fact about anything.
    STILL_ACTIVE = "still_active"


#: WHERE each of the monitor's own codes gets its answer in this build. One
#: copy, and the payload is built from it, so a code cannot ship with a source
#: that disagrees with what the monitor actually does.
#:
#: Every one of them is `measured`: the monitor derives all of its own codes,
#: and a code it could not derive would not be one of its own. `not_measured`
#: and `no_source` exist in this enum because the states a TARGET publishes are
#: passed through carrying them.
MONITOR_SOURCES: dict[MonitorCode, Source] = {
    MonitorCode.LANE_UNREACHABLE: Source.MEASURED,
    MonitorCode.IDENTITY_SERVICE_UNREACHABLE: Source.MEASURED,
    MonitorCode.PLATFORM_UNREACHABLE: Source.MEASURED,
    MonitorCode.CAPTURE_UNREACHABLE: Source.MEASURED,
    MonitorCode.LANE_REFUSED_US: Source.MEASURED,
    MonitorCode.IDENTITY_SERVICE_REFUSED_US: Source.MEASURED,
    MonitorCode.PLATFORM_REFUSED_US: Source.MEASURED,
    MonitorCode.CAPTURE_REFUSED_US: Source.MEASURED,
    MonitorCode.TARGET_CONTRACT_UNSUPPORTED: Source.MEASURED,
    MonitorCode.SINK_DELIVERY_FAILED: Source.MEASURED,
    MonitorCode.LANE_GONE_QUIET: Source.MEASURED,
}

#: The unreachable code for each kind of target. Derived from nothing else, and
#: it is the one place the pairing is written: a target kind added without one
#: fails here rather than silently reporting nothing when it goes dark.
UNREACHABLE_CODE: dict[TargetKind, MonitorCode] = {
    TargetKind.LANE: MonitorCode.LANE_UNREACHABLE,
    TargetKind.IDENTITY_SERVICE: MonitorCode.IDENTITY_SERVICE_UNREACHABLE,
    TargetKind.PLATFORM: MonitorCode.PLATFORM_UNREACHABLE,
    TargetKind.CAPTURE: MonitorCode.CAPTURE_UNREACHABLE,
}

#: The same pairing for the OTHER half of a failed poll: the target answered,
#: and said no. Written beside the map above so a target kind cannot gain one
#: without the other -- a kind with an `unreachable` code and no `refused_us`
#: code would publish every refusal from it as silence again.
REFUSED_CODE: dict[TargetKind, MonitorCode] = {
    TargetKind.LANE: MonitorCode.LANE_REFUSED_US,
    TargetKind.IDENTITY_SERVICE: MonitorCode.IDENTITY_SERVICE_REFUSED_US,
    TargetKind.PLATFORM: MonitorCode.PLATFORM_REFUSED_US,
    TargetKind.CAPTURE: MonitorCode.CAPTURE_REFUSED_US,
}


def published_url(url: str) -> str:
    """A target's URL as this monitor publishes it: scheme, host, port, path.

    NOTHING ELSE. `GET /v1/monitor` exists so a consumer can see WHAT is being
    watched, and those four are what answers that. Userinfo is refused at
    startup, and this is the second half of the same rule: what is published is
    REBUILT from the parts that are an address, so a credential -- or a query
    string, or a fragment -- cannot ride out on this route because somebody
    found a way past the first check.
    """
    parts = urlsplit(url)
    host = parts.hostname or ""
    if ":" in host:
        host = f"[{host}]"
    port = f":{parts.port}" if parts.port is not None else ""
    return f"{parts.scheme}://{host}{port}{parts.path}"


def _text(value, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string, got {value!r}")
    return value


def _iso_utc(value, field_name: str) -> str:
    """An ISO 8601 timestamp that carries an offset.

    A naive timestamp is refused rather than assumed to be UTC -- the same rule
    the lane and Vehicle ID contracts apply, and for the same reason: assuming is
    how two machines in two timezones come to disagree about when something
    broke, months later, with the repair already made.
    """
    text = _text(value, field_name)
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"{field_name} is not ISO 8601: {text!r}") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{field_name} has no UTC offset: {text!r}")
    return text


# ---------------------------------------------------------------------------
# GET /v1/monitor
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TargetDescription:
    """One declared target, as a consumer may see it.

    `url` is here because knowing WHAT is being watched is the whole value of
    this route, and a URL is not a credential. `authenticated` says whether one
    is configured; the credential itself is not on this surface, is not on any
    surface, and is not in this process's argument vector either -- it is read
    from a file.
    """

    name: str
    kind: str
    url: str
    #: How often this target is polled. A per-site SETTING and an ASSUMPTION:
    #: nothing here measures how often a lane's health changes. It is published
    #: rather than documented because it is a property of THIS monitor's
    #: configuration, and a document could only describe one.
    poll_seconds: float
    #: Whether a credential is configured for this target. Not the credential.
    authenticated: bool
    #: How long this monitor waits for this target's answer. A per-site SETTING
    #: and an ASSUMPTION, published for the same reason `poll_seconds` is: it is
    #: a property of THIS monitor's configuration, and a document could only
    #: describe one. What it is drawn against is in `client.DEFAULT_TIMEOUT`.
    timeout_seconds: float = 0.0

    def __post_init__(self) -> None:
        _text(self.name, "target.name")
        if self.kind not in tuple(kind.value for kind in TargetKind):
            raise ValueError(f"target kind must be one of {tuple(TargetKind)}, got {self.kind!r}")
        _text(self.url, "target.url")
        parsed = urlsplit(self.url)
        if parsed.username or parsed.password:
            # The same refusal the configuration makes, held one layer lower so
            # it cannot be routed around by building the payload some other way.
            # This route publishes what a site watches; it may not publish how
            # to authenticate to it.
            raise ValueError(
                "target.url has userinfo in URL: credentials come from files, and this route "
                "publishes an address rather than a way in"
            )
        if not isinstance(self.poll_seconds, float) or self.poll_seconds <= 0:
            raise ValueError(
                f"poll_seconds must be a positive number of seconds, got {self.poll_seconds!r}"
            )
        if not isinstance(self.authenticated, bool):
            raise ValueError("target.authenticated must be a bool")
        if not isinstance(self.timeout_seconds, float) or self.timeout_seconds <= 0:
            raise ValueError(
                f"timeout_seconds must be a positive number of seconds, "
                f"got {self.timeout_seconds!r}"
            )

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class SinkDescription:
    """One declared sink: what it is, and nothing about how to reach it.

    No host, no recipient, no URL and no credential. A consumer of this route is
    entitled to know that somebody is being told; where they are is that site's
    business and putting it here would publish an address list on a read route.
    """

    name: str
    kind: str

    def __post_init__(self) -> None:
        _text(self.name, "sink.name")
        if self.kind not in tuple(kind.value for kind in SinkKind):
            raise ValueError(f"sink kind must be one of {tuple(SinkKind)}, got {self.kind!r}")

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class MonitorDescription:
    """`GET /v1/monitor` -- who this monitor is, and what it watches."""

    monitor_id: str
    site_id: str
    targets: tuple[TargetDescription, ...]
    sinks: tuple[SinkDescription, ...]
    #: How many notifications `GET /v1/monitor/events` can still serve behind
    #: the current cursor. A per-site SETTING with a published default, and it
    #: is PUBLISHED rather than described because a consumer's own catch-up
    #: policy depends on it: fall further behind than this and you are told
    #: `reset` rather than served a short page. The lane contract publishes its
    #: own on `GET /v1/lane` for the same reason and in the same field name.
    event_window_depth: int = 0
    contract_version: int = CONTRACT_VERSION

    def __post_init__(self) -> None:
        _text(self.monitor_id, "monitor_id")
        _text(self.site_id, "site_id")
        if isinstance(self.event_window_depth, bool) or not isinstance(
            self.event_window_depth, int
        ) or self.event_window_depth <= 0:
            raise ValueError(
                "event_window_depth must be a positive number of notifications, got "
                f"{self.event_window_depth!r}"
            )
        if not self.targets:
            # The same refusal the configuration makes, held one layer lower so
            # it cannot be routed around by building the payload some other way.
            # A monitor watching nothing that answers this route at all is the
            # lie this module exists to prevent.
            raise ValueError(
                "a monitor with no targets watches nothing and may not describe itself"
            )
        if not self.sinks:
            raise ValueError("a monitor with no sinks can tell nobody anything")

    def to_dict(self) -> dict:
        return {
            "monitor_id": self.monitor_id,
            "site_id": self.site_id,
            "contract_version": self.contract_version,
            "event_window_depth": self.event_window_depth,
            "targets": [target.to_dict() for target in self.targets],
            "sinks": [sink.to_dict() for sink in self.sinks],
        }


# ---------------------------------------------------------------------------
# GET /v1/monitor/health
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MonitorEntry:
    """One of the MONITOR'S OWN codes, its subject, its state and its source.

    `subject` is what the code is about: which target could not be reached,
    which sink could not deliver, which device has gone quiet. It is part of the
    identity of the entry, because "a sink failed" and "which sink failed" are
    different facts and only one of them can be acted on.
    """

    code: str
    subject: str
    state: str
    #: The HTTP status the target answered with, when the code is about an
    #: ANSWER. `null` everywhere else. It is on the entry rather than only in
    #: the message because a human arriving at this route after the message has
    #: scrolled away needs the same fact: 401 is a credential, 404 is an older
    #: target, and neither is a target that is down.
    status: int | None = None

    def __post_init__(self) -> None:
        if self.code not in tuple(code.value for code in MonitorCode):
            raise ValueError(f"{self.code!r} is not a monitor code in this contract")
        _text(self.subject, "subject")
        if self.state not in tuple(state.value for state in HealthState):
            raise ValueError(f"state must be one of {tuple(HealthState)}, got {self.state!r}")
        if self.status is not None and (
            isinstance(self.status, bool) or not isinstance(self.status, int)
            or not 100 <= self.status <= 599
        ):
            raise ValueError(f"status must be an HTTP status code or null, got {self.status!r}")
        if self.status is not None and self.code not in tuple(
            code.value for code in REFUSED_CODE.values()
        ):
            # A status on any other code would be a number with no answer behind
            # it. The codes that carry one are the ones that exist because a
            # target answered.
            raise ValueError(f"{self.code} does not carry an HTTP status; got {self.status!r}")
        # The invariant, copied from the lane contract because it is the same
        # invariant. `ok` and `active` are claims about a measurement, and a
        # code this build does not derive has no standing to make either.
        if self.state != HealthState.UNKNOWN and self.source != Source.MEASURED:
            raise ValueError(
                f"{self.code} is {self.source.value} but claims state {self.state!r}. "
                "Only a code this build derives may answer anything but 'unknown'."
            )

    @property
    def source(self) -> Source:
        return MONITOR_SOURCES[MonitorCode(self.code)]

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "subject": self.subject,
            "state": self.state,
            "source": self.source.value,
            "status": self.status,
        }


@dataclass(frozen=True, slots=True)
class TargetHealth:
    """What one target said about itself, last time it was asked.

    `codes` is that target's payload, PASSED THROUGH. Not re-keyed, not
    re-ordered, not re-labelled and not filtered: whatever entries it published,
    exactly as it published them. A monitor that rendered a lane's health in its
    own vocabulary would be a second copy of that lane's claim about itself, and
    the copy is the one that comes to disagree.

    `polled_at` is `null` until this target has answered once, and `codes` is
    empty then -- which is a different fact from a target that answered with
    nothing to say, and the monitor's own `*_unreachable` code is what separates
    them.

    `contract_version` is the version the TARGET declared, so a consumer can see
    what it was read as. `null` for a target whose contract publishes no version
    -- the platform's operator surface does not, and that is a gap named here
    rather than papered over with a number this monitor invented.
    """

    name: str
    kind: str
    polled_at: str | None
    contract_version: int | None
    codes: tuple[dict, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        _text(self.name, "target.name")
        if self.kind not in tuple(kind.value for kind in TargetKind):
            raise ValueError(f"target kind must be one of {tuple(TargetKind)}, got {self.kind!r}")
        if self.polled_at is not None:
            _iso_utc(self.polled_at, "polled_at")
        if self.contract_version is not None and not isinstance(self.contract_version, int):
            raise ValueError(
                f"contract_version must be an int or null, got {self.contract_version!r}"
            )

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "kind": self.kind,
            "polled_at": self.polled_at,
            "contract_version": self.contract_version,
            "codes": list(self.codes),
        }


@dataclass(frozen=True, slots=True)
class MonitorHealth:
    """`GET /v1/monitor/health` -- the monitor's own codes, and every target's.

    Refused at construction if one of the monitor's own codes is missing or
    duplicated for a subject. An absent code is indistinguishable from a healthy
    one to whoever reads this, which is the whole reason the set is closed.
    """

    codes: tuple[MonitorEntry, ...] = field(default_factory=tuple)
    targets: tuple[TargetHealth, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        seen = [(entry.code, entry.subject) for entry in self.codes]
        if len(seen) != len(set(seen)):
            raise ValueError("a monitor code appears twice for one subject in one payload")
        missing = {code.value for code in MonitorCode} - {code for code, _ in seen}
        if missing:
            raise ValueError(
                f"health payload is missing {sorted(missing)}. Every code ships every time: "
                "one that is absent reads exactly like one that is fine. A code with no "
                "subject yet ships once, `unknown`, under the monitor's own id."
            )

    def to_dict(self) -> dict:
        return {
            "contract_version": CONTRACT_VERSION,
            "codes": [entry.to_dict() for entry in self.codes],
            "targets": [target.to_dict() for target in self.targets],
        }


# ---------------------------------------------------------------------------
# The notification -- what a sink sends, and what GET /v1/monitor/events serves
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Notification:
    """One transition, told to a human, and recorded on the events route.

    THIS IS THE WHOLE MESSAGE. There is no second, richer rendering held back
    for one of the sinks: what a webhook receives and what an email says are
    built from this object, so a plate that is not on it cannot be on either.

    What it deliberately does NOT carry: a plate, an image reference, an event
    id, or any of a target's event detail. The monitor reads `/health`, never
    `/events` or `/state`, so it does not hold one to leak -- and this dataclass
    has nowhere to put one.
    """

    site_id: str
    #: The lane this is about, when the target that is about it IS a lane.
    #: `null` for the platform's codes, the identity service's, and this
    #: monitor's own -- stamping a lane's id on those reads as "that lane cannot
    #: reach the platform", which is a different machine, a different fault and
    #: a different repair from the true one.
    lane_id: str | None
    target: str
    code: str
    #: Which sink, which device, which target. `null` when the code is about the
    #: target as a whole.
    subject: str | None
    transition: str
    #: The source the TARGET gave for this code, passed through. `measured` for
    #: the monitor's own codes.
    source: str
    #: The target's own caveat for this code, when it published one. Carried
    #: because a caveat that stays behind at the monitor is a caveat the human
    #: reading the message does not get.
    caveat: str | None
    at: str
    #: The HTTP status a target answered with, for the codes that exist because
    #: it answered. `null` everywhere else. It is in the MESSAGE, not only on the
    #: health route, because the message is what wakes somebody: without it,
    #: "the platform is refusing us" sends them to the platform when the fault
    #: is a token in a file beside this process.
    status: int | None = None

    def __post_init__(self) -> None:
        _text(self.site_id, "site_id")
        _text(self.target, "target")
        _text(self.code, "code")
        if self.transition not in tuple(t.value for t in Transition):
            raise ValueError(
                f"transition must be one of {tuple(Transition)}, got {self.transition!r}"
            )
        _text(self.source, "source")
        _iso_utc(self.at, "at")

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class EventPage:
    """`GET /v1/monitor/events?since=N` -- the notifications this monitor sent.

    Deliberately the same shape and the same semantics as the lane contract's
    `GET /v1/lane/events` and the Vehicle ID service's `GET /v1/reads`, field for
    field, so one consumer can hold one cursor policy for all three surfaces.

      * the cursor is monotonic within one run and is NOT durable across a
        restart -- it is a catch-up window for a consumer that blinked, not a
        record of anything. The durable record of what a monitor said is
        whatever its sinks delivered it to;
      * `since` ahead of this monitor's own cursor sets `reset`. An empty list
        without that flag is indistinguishable from "nothing happened", which is
        how a consumer silently misses everything after a restart;
      * `since` behind the oldest notification still held also sets `reset`. The
        window is bounded and a consumer that has fallen further behind than
        that would otherwise receive a page with the evicted notifications
        simply absent from it, which looks exactly like a complete one.

    `dropped` is how many notifications the window has evicted. Published
    because a gap nobody knows about is worse than one that is counted -- and on
    this surface a gap is a fault nobody was told about.
    """

    cursor: int
    reset: bool
    dropped: int
    events: tuple[dict, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict:
        return {
            "contract_version": CONTRACT_VERSION,
            "cursor": self.cursor,
            "reset": self.reset,
            "dropped": self.dropped,
            "events": list(self.events),
        }


# ===========================================================================
# THE CAPTURE PROCESS
#
# The second process in this package, and it is the first thing here that keeps
# anything on a disk. SETTLED 3g: when the barrier is broken the camera's job
# changes from DECIDING to RECORDING -- capture every entry, timestamp and image
# so the entries can be reconstructed. Gokhan's words: "camera captures an image
# every minute and every time the gate opens", "we need to save the picture of
# the cars".
#
# **It has no opening authority either, and it holds no identity.** It reads a
# camera and it reads a lane's read contract, both `GET`, and it writes to its
# own directory. The store carries the JPEG the camera sent and seven fields
# about WHEN and WHY it was taken. No plate, no plate region, no vehicle
# attribute and no event detail -- the join to who the car was is the lane event
# reference held here and the platform's durable record, one place each.
# ===========================================================================


class CaptureReason(StrEnum):
    """Why a capture was taken. CLOSED, and published.

    A consumer branches on all three: one of them is a clock and two of them are
    a lane saying something happened, and the difference decides whether the
    record has a lane event behind it to join to.
    """

    #: The clock. `[capture] interval_seconds` elapsed and nothing had happened.
    INTERVAL = "interval"
    #: A lane recorded that it grabbed frames for an arriving vehicle.
    LANE_ARRIVAL = "lane_arrival"
    #: A lane recorded that it vended. Gokhan's "every time the gate opens", in
    #: the phrasing that is actually measurable: nothing anywhere knows whether
    #: the boom moved, and the lane's own vend output has no feedback by design.
    LANE_VEND = "lane_vend"


class CaptureCode(StrEnum):
    """The malfunctions the CAPTURE PROCESS measures. CLOSED, all `measured`.

    Deliberately the same shape as the lane contract's malfunction table, so a
    monitor reads this surface with the same code that reads a lane: one entry
    per member on every response, each with a `state`, a `source`, a boolean
    `never_alarm` and a `caveat`.

    **None of them is `never_alarm`.** Every one is a physical thing that needs
    somebody -- a camera that has stopped answering, a disk that will not take a
    write, a store eating itself under a cap that is too small. `camera_feed_frozen`
    is the one that comes closest to a false alarm, and what it measures is
    stated in its caveat rather than being softened into silence.
    """

    #: Nothing came back from the camera. A network failure, a timeout, or a
    #: 5xx: the camera's own process answered that it could not do the thing.
    #: This is Gokhan's "camera disconnected is a malfunction".
    CAMERA_UNREACHABLE = "camera_unreachable"
    #: The camera ANSWERED, and the answer was no -- a 3xx or a 4xx, with its
    #: status on the entry. 401 is the credential in the file beside this
    #: process, 404 is a snapshot route this camera does not have, and 3xx is a
    #: camera steering this process at another host. Three repairs, and folded
    #: into "unreachable" a human cannot tell them apart.
    CAMERA_REFUSED_US = "camera_refused_us"
    #: Two consecutive snapshots from this camera were BYTE-IDENTICAL.
    CAMERA_FEED_FROZEN = "camera_feed_frozen"
    #: The store's directory would not take a write.
    STORE_UNWRITABLE = "store_unwritable"
    #: A write was refused because one purge could not get the store under
    #: `[capture] max_bytes`. The store is not eating itself silently.
    STORE_OVER_BUDGET = "store_over_budget"
    #: An image with no sidecar, a sidecar with no image, or a sidecar the
    #: contract will not accept, was found when the index was rebuilt. Reported
    #: and then purged, never silently kept.
    STORE_RECORD_INCOMPLETE = "store_record_incomplete"
    #: The newest record this store holds is stamped AFTER the clock that reads
    #: it. Measured on every purge. While it is active the age rule cannot reach
    #: those records and the size cap is the only bound on them.
    CLOCK_STEPPED_BACK = "clock_stepped_back"
    #: The lane that triggers captures did not answer. `unknown` where no lane
    #: is declared: standalone is a MODE, and nobody measured.
    LANE_UNREACHABLE = "lane_unreachable"
    #: The lane ANSWERED, and the answer was no, with its status on the entry.
    LANE_REFUSED_US = "lane_refused_us"
    #: The lane served a page this build cannot read: a timestamp with no UTC
    #: offset, or a cursor that went backwards without `reset`. The page is
    #: refused WHOLE -- the cursor is not adopted and nothing is photographed
    #: under a lane reason. The same answer, for the same reason, as the
    #: monitor's `target_contract_unsupported`.
    LANE_CONTRACT_UNSUPPORTED = "lane_contract_unsupported"
    #: The lane reported `reset`: it restarted, or it evicted further than this
    #: process had fallen behind. Whatever was in that gap was never
    #: photographed and cannot be. Recovers on the next page that is not a
    #: reset; `lane_events_missed` counts what was lost, since start.
    LANE_BACKLOG_LOST = "lane_backlog_lost"


#: WHERE each capture code gets its answer in this build. One copy, and the
#: payload is built from it. Every one is `measured` -- this process derives all
#: of its own codes, and a code it could not derive would not be one of its own.
CAPTURE_SOURCES: dict[CaptureCode, Source] = {
    CaptureCode.CAMERA_UNREACHABLE: Source.MEASURED,
    CaptureCode.CAMERA_REFUSED_US: Source.MEASURED,
    CaptureCode.CAMERA_FEED_FROZEN: Source.MEASURED,
    CaptureCode.STORE_UNWRITABLE: Source.MEASURED,
    CaptureCode.STORE_OVER_BUDGET: Source.MEASURED,
    CaptureCode.STORE_RECORD_INCOMPLETE: Source.MEASURED,
    CaptureCode.CLOCK_STEPPED_BACK: Source.MEASURED,
    CaptureCode.LANE_UNREACHABLE: Source.MEASURED,
    CaptureCode.LANE_REFUSED_US: Source.MEASURED,
    CaptureCode.LANE_CONTRACT_UNSUPPORTED: Source.MEASURED,
    CaptureCode.LANE_BACKLOG_LOST: Source.MEASURED,
}

#: Whether a code may wake a human. Travels on the wire with the code, which is
#: what a monitor reads -- this package does not hold a second list for its own
#: consumption anywhere. Nothing here is `never_alarm`: every member is a
#: physical thing that needs a person.
CAPTURE_NEVER_ALARM: dict[CaptureCode, bool] = dict.fromkeys(CaptureCode, False)

#: The caveat published beside a code, when there is something a human acting on
#: it has to know. One copy, on the wire, so the caveat reaches the message
#: rather than staying behind in a document nobody opens at 3am.
CAPTURE_CAVEATS: dict[CaptureCode, str] = {
    CaptureCode.CAMERA_FEED_FROZEN: (
        "IDENTICAL means identical: this compares the bytes of two consecutive snapshots and "
        "nothing else. A camera that burns a clock, a date or a frame counter into the image "
        "is therefore NEVER frozen by this measure, however dead its sensor -- the overlay "
        "changes the bytes. A camera with no overlay pointed at an empty lane at night can be "
        "byte-identical while working perfectly. This measure is a cheap true negative, not a "
        "test of whether a camera is seeing."
    ),
    CaptureCode.CLOCK_STEPPED_BACK: (
        "WHILE THIS IS ACTIVE THE AGE RULE IS SUSPENDED for the records ahead of the clock: a "
        "record stamped later than now is not older than any window, so the retention rule "
        "cannot reach it and the size cap is the only bound on it. Nothing here corrects a "
        "clock and nothing here measures how far out it is -- this says that the store holds a "
        "record from the future, which is the fact a person has to act on."
    ),
}

#: WHICH KIND OF THING each code is about, and therefore what its `subject` is.
#: ONE copy: the process files its states under it and `CaptureHealth` refuses a
#: payload that is not complete against it. A code whose subject kind lived in
#: two places would be a code that is complete on one side and absent on the
#: other, and an absent code reads to a consumer exactly like a code that is
#: fine.
CAMERA_CODES: tuple[CaptureCode, ...] = (
    CaptureCode.CAMERA_UNREACHABLE,
    CaptureCode.CAMERA_REFUSED_US,
    CaptureCode.CAMERA_FEED_FROZEN,
)

#: The codes about the STORE. One subject: this process has one store.
STORE_CODES: tuple[CaptureCode, ...] = (
    CaptureCode.STORE_UNWRITABLE,
    CaptureCode.STORE_OVER_BUDGET,
    CaptureCode.STORE_RECORD_INCOMPLETE,
    CaptureCode.CLOCK_STEPPED_BACK,
)

#: The codes about the LANE that triggers captures. One subject, and at a
#: standalone site it is this process's own id: there is no lane to name.
LANE_CODES: tuple[CaptureCode, ...] = (
    CaptureCode.LANE_UNREACHABLE,
    CaptureCode.LANE_REFUSED_US,
    CaptureCode.LANE_CONTRACT_UNSUPPORTED,
    CaptureCode.LANE_BACKLOG_LOST,
)


class CameraUnreachableCause(StrEnum):
    """WHY nothing came back. CLOSED, and on the wire beside the code.

    `camera_unreachable` folds four different repairs together -- a camera that
    is off, a camera that is answering too slowly to be read inside its own
    timeout, a camera whose own process failed, and a camera answering something
    that is not a picture. They are one code because to this process they are
    one fact, "I asked and I do not have an image"; they are told apart here
    because they are not one repair.
    """

    #: The deadline passed. The body was still arriving, or was not arriving at
    #: all: this process stopped reading rather than being held by one camera.
    TIMEOUT = "timeout"
    #: The socket failed, or nothing answered on it.
    NETWORK = "network"
    #: The camera's own process answered that it could not take the picture -- a
    #: 5xx. The repair is at the camera either way, which is why it is here and
    #: not under `camera_refused_us`.
    SERVER_ERROR = "server_error"
    #: Something came back and it was not a JPEG, or it was longer than
    #: `[capture] max_snapshot_bytes`. A login page served as `image/jpeg` is
    #: this one.
    NOT_A_PICTURE = "not_a_picture"


def _capture_never_alarm(code: CaptureCode) -> bool:
    return CAPTURE_NEVER_ALARM[code]


@dataclass(frozen=True, slots=True)
class CameraDescription:
    """One declared camera, as a consumer may see it.

    `snapshot_url` is REBUILT from scheme, host, port and path -- the same rule
    and the same function `GET /v1/monitor` uses. A credential in a snapshot URL
    is refused at startup; this is the second half of that rule, so a credential
    cannot ride out on this route because something found a way past the first
    check. `authenticated` says a credential is configured. It is not the
    credential.
    """

    camera_id: str
    snapshot_url: str
    authenticated: bool

    def __post_init__(self) -> None:
        _text(self.camera_id, "camera.camera_id")
        _text(self.snapshot_url, "camera.snapshot_url")
        parsed = urlsplit(self.snapshot_url)
        if parsed.username or parsed.password:
            raise ValueError(
                "camera.snapshot_url has userinfo in URL: credentials come from files, and "
                "this route publishes an address rather than a way in"
            )
        if not isinstance(self.authenticated, bool):
            raise ValueError("camera.authenticated must be a bool")

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class CaptureDescription:
    """`GET /v1/capture` -- who this process is, and what it is set to do.

    The settings AS LOADED, so a site can see what the file it wrote actually
    produced rather than what it meant. `directory` is here because where the
    personal data is kept is the first question anybody asks of this process,
    and the answer is a path on a box, not a credential.
    """

    capture_id: str
    site_id: str
    directory: str
    interval_seconds: float
    retention_days: int
    max_bytes: int
    #: The most this process reads from one camera before it stops reading. On
    #: this route beside `max_bytes` because the relationship between the two is
    #: what a site has to get right, and startup refuses it unless it is BELOW
    #: `max_bytes`.
    max_snapshot_bytes: int
    cameras: tuple[CameraDescription, ...]
    #: Whether a lane is declared. `false` is STANDALONE, and standalone is a
    #: mode: a garage with a camera and no gate is a customer of this process,
    #: not a degraded installation. It decides whether any record can ever carry
    #: a lane event reference.
    lane_declared: bool
    #: The lane's address, rebuilt, or `null` when none is declared.
    lane_url: str | None
    contract_version: int = CONTRACT_VERSION

    def __post_init__(self) -> None:
        _text(self.capture_id, "capture_id")
        _text(self.site_id, "site_id")
        _text(self.directory, "directory")
        if not self.cameras:
            # The same refusal the configuration makes, held one layer lower so
            # it cannot be routed around by building the payload some other way.
            # A capture process with no camera photographs nothing and would
            # describe itself as running.
            raise ValueError("a capture process with no camera records nothing")
        if not isinstance(self.lane_declared, bool):
            raise ValueError("lane_declared must be a bool")
        if self.lane_declared != (self.lane_url is not None):
            raise ValueError("lane_declared and lane_url disagree about whether a lane exists")

    def to_dict(self) -> dict:
        return {
            "capture_id": self.capture_id,
            "site_id": self.site_id,
            "contract_version": self.contract_version,
            "directory": self.directory,
            "interval_seconds": self.interval_seconds,
            "retention_days": self.retention_days,
            "max_bytes": self.max_bytes,
            "max_snapshot_bytes": self.max_snapshot_bytes,
            "lane_declared": self.lane_declared,
            "lane_url": self.lane_url,
            "cameras": [camera.to_dict() for camera in self.cameras],
        }


@dataclass(frozen=True, slots=True)
class CaptureEntry:
    """One capture code, its subject, its state, its source and its caveat.

    The same entry shape a lane's health route publishes, field for field, so a
    monitor reads this surface with the code that already reads a lane. `state`
    is one of the three; `never_alarm` is a JSON boolean on every entry, never
    absent, because absent and `false` point in opposite directions at whoever
    reads it.
    """

    code: str
    subject: str
    state: str
    #: The HTTP status the camera or the lane answered with, when the code is
    #: about an ANSWER. `null` everywhere else.
    status: int | None = None
    #: WHY nothing came back, on `camera_unreachable` and nowhere else. `null`
    #: on every other code, and `null` on this one until it has been measured:
    #: one closed set, on the wire, so a monitor reading this surface can tell a
    #: camera that is off from one that cannot be read inside its own timeout
    #: without holding a second list of its own.
    cause: str | None = None

    def __post_init__(self) -> None:
        if self.code not in tuple(code.value for code in CaptureCode):
            raise ValueError(f"{self.code!r} is not a capture code in this contract")
        _text(self.subject, "subject")
        if self.state not in tuple(state.value for state in HealthState):
            raise ValueError(f"state must be one of {tuple(HealthState)}, got {self.state!r}")
        if self.cause is not None:
            if self.code != CaptureCode.CAMERA_UNREACHABLE.value:
                raise ValueError(f"{self.code} does not carry a cause; got {self.cause!r}")
            if self.cause not in tuple(cause.value for cause in CameraUnreachableCause):
                raise ValueError(
                    f"cause must be one of {tuple(CameraUnreachableCause)}, got {self.cause!r}"
                )
        if self.status is not None and (
            isinstance(self.status, bool) or not isinstance(self.status, int)
            or not 100 <= self.status <= 599
        ):
            raise ValueError(f"status must be an HTTP status code or null, got {self.status!r}")
        if self.status is not None and self.code not in (
            CaptureCode.CAMERA_REFUSED_US.value,
            CaptureCode.LANE_REFUSED_US.value,
        ):
            raise ValueError(f"{self.code} does not carry an HTTP status; got {self.status!r}")
        # The invariant, copied from the lane contract and from `MonitorEntry`,
        # because it is the same invariant. `ok` and `active` are claims about a
        # measurement, and a code this build does not derive may make neither.
        if self.state != HealthState.UNKNOWN and self.source != Source.MEASURED:
            raise ValueError(
                f"{self.code} is {self.source.value} but claims state {self.state!r}. "
                "Only a code this build derives may answer anything but 'unknown'."
            )

    @property
    def source(self) -> Source:
        return CAPTURE_SOURCES[CaptureCode(self.code)]

    def to_dict(self) -> dict:
        code = CaptureCode(self.code)
        return {
            "code": self.code,
            "subject": self.subject,
            "state": self.state,
            "source": self.source.value,
            "never_alarm": _capture_never_alarm(code),
            "caveat": CAPTURE_CAVEATS.get(code),
            "status": self.status,
            "cause": self.cause,
        }


@dataclass(frozen=True, slots=True)
class StoreReads:
    """What is on the disk, MEASURED from the directory when this is asked.

    Every field here is a read. Nothing in this package has ever seen a capture
    from any of the cameras it is written for, so this module states no size, no
    rate and no capacity anywhere: what a site's disk does is answered by
    pointing this route at that site's disk.

    `projected_bytes_per_day` is derived from the last 24 hours and is `null`
    under an hour of data -- a projection from four minutes is not a projection,
    and publishing one would be a number that looks measured.
    """

    bytes_used: int
    record_count: int
    oldest_at: str | None
    newest_at: str | None
    mean_bytes_per_record: int | None
    records_last_24h: int
    bytes_last_24h: int
    projected_bytes_per_day: int | None
    #: How many records the purge has removed since this process started, and
    #: why. Published because a store that is silently eating itself under a cap
    #: that is too small looks exactly like a store nothing is happening at.
    purged_by_age: int = 0
    purged_by_size: int = 0
    #: How many temporary files an index rebuild has removed since this process
    #: started. A temporary file found at a rebuild is BY DEFINITION a write
    #: that died -- a power cut in a gate housing -- and it is counted rather
    #: than swept, because the number is how often that is happening at a site.
    purged_by_crash: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class CaptureHealth:
    """`GET /v1/capture/health` -- every capture code, and what is on the disk.

    Refused at construction if one of the codes is missing or duplicated for a
    subject, exactly as `MonitorHealth` is. An absent code is indistinguishable
    from a healthy one to whoever reads this.
    """

    codes: tuple[CaptureEntry, ...] = field(default_factory=tuple)
    store: StoreReads | None = None
    #: Every camera this process was DECLARED with. Not published -- it is on
    #: `GET /v1/capture`, where a reader looks for what this process is set to
    #: do -- but held here because completeness is per `(code, subject)` and a
    #: payload cannot be checked complete against a list it does not have.
    camera_ids: tuple[str, ...] = field(default_factory=tuple)
    #: How many lane events this process is known not to have followed, since it
    #: started. `0` at a standalone site: there is no lane to miss events from.
    lane_events_missed: int = 0

    def __post_init__(self) -> None:
        seen = [(entry.code, entry.subject) for entry in self.codes]
        if len(seen) != len(set(seen)):
            raise ValueError("a capture code appears twice for one subject in one payload")
        missing = {code.value for code in CaptureCode} - {code for code, _ in seen}
        if missing:
            raise ValueError(
                f"health payload is missing {sorted(missing)}. Every code ships every time: "
                "one that is absent reads exactly like one that is fine. A code with no "
                "subject yet ships once, `unknown`, under this process's own id."
            )
        # AND COMPLETE PER (CODE, CAMERA). A camera that has never produced a
        # state is exactly the camera worth asking about -- the one that has not
        # answered since the process started -- and under a per-CODE rule alone
        # it disappears from this payload the moment any other camera reports.
        absent = sorted(
            (code.value, camera_id)
            for code in CAMERA_CODES
            for camera_id in self.camera_ids
            if (code.value, camera_id) not in set(seen)
        )
        if absent:
            raise ValueError(
                f"health payload is missing {absent}. Every declared camera ships under every "
                "camera code on every response, `unknown` until its first attempt: a camera "
                "that is absent reads to a consumer exactly like a camera that is fine, and "
                "the camera that has never answered is the one worth asking about."
            )
        if self.store is None:
            raise ValueError("a capture health payload without the store's reads is half of one")

    def to_dict(self) -> dict:
        return {
            "contract_version": CONTRACT_VERSION,
            "codes": [entry.to_dict() for entry in self.codes],
            "store": self.store.to_dict(),
            "lane_events_missed": self.lane_events_missed,
        }


#: WHAT `capture_minus_lane_event_ms` SPANS. **The one copy.** Published into
#: `docs/CONTRACT.md` from here and held to it by a value test, so the sentence
#: a reader acts on and the sentence this package believes cannot come apart.
#:
#: It is the monitor half's "`lane_gone_quiet`, and the two clocks it spans"
#: applied to this field, for the same reason and with the same honesty: naming
#: a subtraction across two machines as a delay is a number nobody measured
#: wearing the label of one that was.
CAPTURE_MINUS_LANE_EVENT_NOTE = (
    "This is a SUBTRACTION ACROSS TWO CLOCKS: `captured_at` is read from this process's clock "
    "and `lane_event_at` from the lane's. It is not a measured delay. A NEGATIVE VALUE IS "
    "REACHABLE and is served -- it means the two clocks disagree by at least that much, with "
    "the lane's ahead. Nothing here measures the offset between them, so nothing here can "
    "separate the offset from the time this process took to see the event, and correcting it "
    "would mean a second measurement nobody has made. Where the two clocks are the same box, "
    "or are disciplined to the same source, it is the cost of this process being a CONSUMER of "
    "the lane's contract rather than something the lane calls."
)


@dataclass(frozen=True, slots=True)
class RecordRef:
    """One stored capture, as the records route publishes it. SIDECAR ONLY.

    The seven fields are the sidecar's, and there is nothing else in the sidecar
    to publish. **No plate, no plate region, no vehicle attribute and no event
    detail** -- not withheld here, absent from the store, which is why this
    dataclass has nowhere to put one.

    `image_url` is the route that serves the bytes. The bytes are never inline:
    a records page a consumer polls is a page it polls often, and a JPEG on it
    would make the cheapest read the most expensive one.
    """

    id: str
    captured_at: str
    camera_id: str
    reason: str
    #: The lane event this capture answers, by CURSOR and by the time the LANE
    #: recorded, and nothing else from that event. This is the whole join: who
    #: the car was lives at the lane's platform, under this cursor.
    lane_event_cursor: int | None
    lane_event_at: str | None
    #: `captured_at` minus `lane_event_at`, in milliseconds. NAMED FOR THE
    #: SUBTRACTION IT IS, and what it spans is stated once, in
    #: `CAPTURE_MINUS_LANE_EVENT_NOTE`, published into `docs/CONTRACT.md` from
    #: that one copy. `null` on an interval capture, which has no lane event to
    #: subtract.
    capture_minus_lane_event_ms: int | None
    bytes: int
    image_url: str

    def __post_init__(self) -> None:
        _text(self.id, "record.id")
        _iso_utc(self.captured_at, "captured_at")
        _text(self.camera_id, "camera_id")
        if self.reason not in tuple(reason.value for reason in CaptureReason):
            raise ValueError(f"reason must be one of {tuple(CaptureReason)}, got {self.reason!r}")
        if self.lane_event_at is not None:
            _iso_utc(self.lane_event_at, "lane_event_at")
        if (self.lane_event_cursor is None) != (self.lane_event_at is None):
            raise ValueError(
                "a lane event reference is a cursor AND the time the lane recorded, or neither"
            )
        if (self.reason == CaptureReason.INTERVAL.value) != (self.lane_event_cursor is None):
            raise ValueError(
                f"reason={self.reason!r} and the lane event reference disagree about whether a "
                "lane triggered this capture"
            )
        if (self.capture_minus_lane_event_ms is None) != (self.lane_event_at is None):
            raise ValueError(
                "capture_minus_lane_event_ms is present exactly when a lane event triggered "
                "the capture: it is a subtraction, and there is nothing to subtract without one"
            )

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class RecordPage:
    """`GET /v1/capture/records?since=N` -- what has been stored.

    Deliberately the same shape and the same semantics as the lane contract's
    `GET /v1/lane/events`, field for field, so one consumer holds one cursor
    policy for every surface in this estate.

      * the cursor is **monotonic within one run** and is **NOT durable across a
        restart**. The store is durable; the cursor over it is not. On start the
        index is rebuilt by reading the directory and numbered from one in
        capture order, so a saved position no longer refers to the same record
        once anything has been purged;
      * `since` ahead of this process's own cursor sets `reset`;
      * `since` behind the oldest record still held also sets `reset`. Here the
        window is the STORE, and what evicts from it is the retention rule and
        the size cap -- so this flag is how a consumer learns that what it was
        going to fetch has been deleted rather than simply not served;
      * `dropped` is how many records the purge has removed since this process
        started. A gap nobody knows about is worse than one that is counted.
    """

    cursor: int
    reset: bool
    dropped: int
    records: tuple[dict, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict:
        return {
            "contract_version": CONTRACT_VERSION,
            "cursor": self.cursor,
            "reset": self.reset,
            "dropped": self.dropped,
            "records": list(self.records),
        }
