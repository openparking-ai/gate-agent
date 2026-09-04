"""The monitor: it watches what is declared, and it tells a human what CHANGED.

Gokhan's spec, his words: *"gate broken, camera broken, no connection: we
constantly monitor these items and send message to the human. Text, email etc."*
The lane already publishes every malfunction code with its state and its source.
Until this existed, nothing read it.

**It has no opening authority.** It reads GETs and it sends messages. Everything
it can do to a target is in `client.py`, and there is nothing in that file but a
GET.

**It is a CONSUMER of the lane contract, not a part of the lane.** Nothing here
imports `lane_controller`; it speaks HTTP to the published contract, which is the
seat a third party takes. That is checked rather than intended -- if this package
could reach into a lane, "our own software is an ordinary client" would be a
sentence rather than a property, and the first thing to rot would be the seat.

Three things decide everything below.

**Transitions, not states.** A monitor that sent the current state would send the
same message every poll for as long as a fault lasted, and a human told a
thousand times has been told nothing. So a code moving into a fault is a message,
coming out of one is a message, and going UNMEASURED is a message -- once. A
state that holds is silent, unless this site asked to be reminded.

**`unknown` is never `ok`.** The states this surface publishes for a target are
that target's own, passed through unchanged. Nothing here maps `unknown` onto
anything, and nothing here pages on it. What is unmeasured is said out loud, at
startup and on the health route, and never quietly.

**`never_alarm` is read from the WIRE.** Whether a code may wake a human travels
with that code, in the payload. This package holds no list of its own, because
two lists drift -- and the one that drifts is the one that pages a technician
because a car arrived.
"""

from __future__ import annotations

import logging
from collections import deque
from datetime import UTC, datetime
from time import monotonic

from .client import ReadOnlyClient, TargetRefusedUs, TargetUnreachable
from .config import MonitorConfig, Target
from .contract import (
    CONTRACT_VERSION,
    KNOWN_LANE_VERSIONS,
    REFUSED_CODE,
    UNREACHABLE_CODE,
    EventPage,
    HealthState,
    MonitorCode,
    MonitorDescription,
    MonitorEntry,
    MonitorHealth,
    Notification,
    SinkDescription,
    Source,
    TargetDescription,
    TargetHealth,
    TargetKind,
    Transition,
    published_url,
)
from .sinks import DeliveryFailed

log = logging.getLogger(__name__)

#: Vehicle ID record schema versions this monitor can read, from that contract's
#: `schema_version`. Same rule, same reason.
KNOWN_IDENTITY_VERSIONS: tuple[int, ...] = (1,)

#: The versions this monitor can read, PER KIND OF TARGET. One mapping, so a
#: target kind cannot be added without an answer to "which versions of it" --
#: `KNOWN_LANE_VERSIONS if ... else KNOWN_IDENTITY_VERSIONS` stood here, and a
#: third kind would silently have been read as an identity service.
#:
#: The platform's entry is EMPTY, and that is the honest value: its operator
#: surface publishes no version at all, so nothing about it is ever checked and
#: `target_contract_unsupported` for it stays `unknown` rather than `ok`.
KNOWN_VERSIONS: dict[TargetKind, tuple[int, ...]] = {
    TargetKind.LANE: KNOWN_LANE_VERSIONS,
    TargetKind.IDENTITY_SERVICE: KNOWN_IDENTITY_VERSIONS,
    TargetKind.CAPTURE: (CONTRACT_VERSION,),
    TargetKind.AGENT: (CONTRACT_VERSION,),
    TargetKind.PLATFORM: (),
}

#: The two scopes a state can belong to, and they are never mixed.
#:
#: `platform_unreachable` and `lane_gone_quiet` are each BOTH a code the lane
#: contract publishes and a code this monitor measures itself -- spelt the same
#: and meaning different things. A lane's `platform_unreachable` is what that
#: lane thinks of its own uplink; the monitor's is whether the MONITOR can reach
#: the platform, which is a different machine's opinion about a different link.
#: One namespace would file one under the other, which is a fault attributed to
#: the wrong equipment.
OWN = "monitor"
PASSED_THROUGH = "target"


class UnsupportedContract(Exception):
    """A target answered with a version this build cannot read.

    Raised from `start()`, before anything is polled or served, because a
    configuration pointing at something this monitor cannot interpret is a
    configuration error and not a fault at the target. A monitor that carried on
    would be reporting on a payload it did not understand.

    It is NOT raised for a target that is merely down at startup. A monitor that
    refused to start while a lane was rebooting would be absent at exactly the
    moment it is wanted; that case is `<kind>_unreachable`, active, immediately.
    """


class ContractViolation(Exception):
    """A target answered a payload this build cannot read, and it said why.

    NOT a version problem and not a connection problem: the target is up, it is
    on a version this monitor knows, and the payload it sent breaks the contract
    that version describes. `state` outside the three the contract defines,
    `never_alarm` absent or not a boolean.

    Both used to be read anyway, and both failed silently in the reassuring
    direction. `never_alarm` was `bool(...)` of whatever arrived: absent, it read
    as `false` and paged a technician because a car arrived; the string
    `"false"` is truthy, so a lane whose serialiser quoted it silenced that code
    for ever with nothing anywhere reporting it. A `state` this build does not
    know was passed through untouched AND poisoned the next transition -- one
    malformed poll and an `active` fault afterwards was published as active and
    told to nobody, because the state before it was neither `ok` nor `unknown`.

    So the payload is refused WHOLE and the target becomes
    `target_contract_unsupported`: named, active, paged once, and its codes stop
    being passed through. That is the same answer this monitor already gives a
    version it cannot read, for the same reason -- half-understanding a payload
    about a lane is worse than admitting it cannot be read.
    """

    def __init__(self, message: str, version: int | None = None) -> None:
        super().__init__(message)
        self.version = version


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


class Monitor:
    """Polls the declared targets, decides what changed, and tells the sinks."""

    def __init__(
        self,
        config: MonitorConfig,
        sinks,
        clock=monotonic,
        now=utc_now,
        client_factory=ReadOnlyClient,
    ) -> None:
        self.config = config
        self.sinks = tuple(sinks)
        self._clock = clock
        self._now = now
        self._clients = {
            target.name: client_factory(target.url, target.token, target.timeout_seconds)
            for target in config.targets
        }
        #: What KIND each declared target is. `lane_id` is stamped on a
        #: notification only when the target it is about is a lane, and this is
        #: how that question is answered without a second copy of the target
        #: list.
        self._kinds: dict[str, TargetKind] = {
            target.name: target.kind for target in config.targets
        }
        #: The last state observed for every (scope, target, code, subject).
        #:
        #: SCOPE is load-bearing and is not decoration. `platform_unreachable`
        #: and `lane_gone_quiet` are BOTH a malfunction code the lane contract
        #: publishes and a code this monitor measures itself, spelt identically
        #: -- and they are different facts about different things. The lane's
        #: `platform_unreachable` is what that lane thinks of its own uplink;
        #: the monitor's is whether the monitor can reach the platform. Keyed on
        #: the name alone, one would appear under the other on the health route,
        #: which is a fault attributed to the wrong machine.
        #:
        #: Everything starts UNKNOWN and is never absent, so a code seen for the
        #: first time as `active` is a transition into a fault and is reported --
        #: a monitor that started while a lane was already broken must say so.
        self._states: dict[tuple[str, str, str, str], str] = {}
        #: What the target said ABOUT each code, kept beside the state so a
        #: message can carry the source and the caveat the target gave.
        self._facts: dict[tuple[str, str, str, str], dict] = {}
        self._notified_at: dict[tuple[str, str, str, str], float] = {}
        self._targets: dict[str, TargetHealth] = {
            target.name: TargetHealth(
                name=target.name, kind=target.kind.value, polled_at=None, contract_version=None
            )
            for target in config.targets
        }
        self._due: dict[str, float] = {target.name: 0.0 for target in config.targets}
        self._lane_id: str | None = None
        self._log: deque[tuple[int, Notification]] = deque(maxlen=config.event_window_depth)
        self._cursor = 0
        self._dropped = 0
        #: True while a sink-failure message is being delivered. A failure while
        #: telling somebody about a failure is RECORDED and does not produce
        #: another message -- otherwise one dead endpoint becomes a loop.
        self._reporting_a_sink = False

    # -- startup -----------------------------------------------------------

    def start(self) -> None:
        """Read each target's identity, poll everything once, say what is unmeasured.

        The identity read is what refuses a contract version this build does not
        know. It happens once, before the first poll, so an unreadable target is
        a startup failure with a name rather than a monitor quietly watching
        something it cannot interpret.
        """
        for target in self.config.targets:
            self._read_identity(target)
        self.poll(force=True)
        self._announce_unmeasured()

    def _read_identity(self, target: Target) -> None:
        """`GET /v1/lane` once, for who this lane is and what version it speaks.

        A target that does not answer is not refused: it is down, which is a
        malfunction and not a misconfiguration, and the first poll reports it.
        Nor is one that answers NO -- a lane behind an expired credential is a
        credential to fix, reported by the first poll with its status, and a
        monitor that refused to start on it would be absent at exactly the
        moment it is wanted. A target that ANSWERS with a version this build
        does not know IS refused, loudly, here.
        """
        if target.kind is not TargetKind.LANE:
            return
        try:
            body = self._clients[target.name].get("/v1/lane")
        except TargetRefusedUs as exc:
            log.warning(
                "%s refused the identity route: HTTP %s", target.name, exc.status
            )
            return
        except TargetUnreachable as exc:
            log.warning("%s did not answer its identity route: %s", target.name, exc)
            return
        self._refuse_unknown_version(target, body.get("contract_version"), KNOWN_LANE_VERSIONS)
        lane_id = body.get("lane_id")
        if isinstance(lane_id, str) and lane_id:
            self._lane_id = lane_id

    def _refuse_unknown_version(self, target: Target, version, known: tuple[int, ...]) -> None:
        if version is None or version in known:
            return
        raise UnsupportedContract(
            f"{target.name} at {target.url} declares contract version {version!r}; this monitor "
            f"reads {known}. Refusing to start: half-understanding a payload about a lane is "
            "worse than admitting it cannot be read, and a monitor that carried on would report "
            "on codes whose meaning it had guessed."
        )

    # -- polling -----------------------------------------------------------

    def poll(self, force: bool = False) -> None:
        """Poll every target whose interval has elapsed, and act on what changed."""
        for target in self.config.targets:
            now = self._clock()
            if not force and now < self._due[target.name]:
                continue
            self._due[target.name] = now + target.poll_seconds
            self._poll_target(target)

    def _poll_target(self, target: Target) -> None:
        """One poll, and it has exactly four endings, all of them said out loud.

        The target did not answer; it answered NO; it answered something this
        build cannot read; or it answered. The first two used to be one, and
        folding them cost a human the difference between a dead platform, a dead
        credential and a platform older than this build.
        """
        unreachable = UNREACHABLE_CODE[target.kind]
        refused = REFUSED_CODE[target.kind]
        try:
            version, entries = self._read(target)
        except TargetRefusedUs as exc:
            # IT ANSWERED. So it is not unreachable -- that is a measurement,
            # and this poll made it -- and what it answered is its own code with
            # the status on it, because 401, 404 and 302 send a human to three
            # different machines.
            log.warning("%s refused us: HTTP %s", target.name, exc.status)
            self._monitor_code(unreachable, target.name, HealthState.OK, target.name)
            self._monitor_code(
                refused, target.name, HealthState.ACTIVE, target.name, status=exc.status
            )
            self._retire(target.name)
            self._forget_target_payload(target)
            return
        except TargetUnreachable as exc:
            # "No connection", in Gokhan's words, and it is the monitor's own
            # measurement: nothing else can make it, because a thing that is
            # down cannot report that it is down.
            log.warning("%s is unreachable: %s", target.name, exc)
            self._monitor_code(unreachable, target.name, HealthState.ACTIVE, target.name)
            # Whether it would refuse us is not a question this poll answered:
            # nothing came back to refuse anything. `unknown`, which is the
            # value that means nobody measured, and never `ok`.
            self._monitor_code(refused, target.name, HealthState.UNKNOWN, target.name)
            self._retire(target.name)
            self._forget_target_payload(target)
            return
        except ContractViolation as exc:
            # Up, reachable, on a version this build knows, and publishing a
            # payload that version does not describe. Named, active, paged once.
            log.error("%s published a payload this build cannot read: %s", target.name, exc)
            self._monitor_code(unreachable, target.name, HealthState.OK, target.name)
            self._monitor_code(refused, target.name, HealthState.OK, target.name)
            self._monitor_code(
                MonitorCode.TARGET_CONTRACT_UNSUPPORTED,
                target.name,
                HealthState.ACTIVE,
                target.name,
            )
            self._retire(target.name)
            self._targets[target.name] = TargetHealth(
                name=target.name,
                kind=target.kind.value,
                polled_at=self._now(),
                contract_version=exc.version,
                codes=(),
            )
            return

        self._monitor_code(unreachable, target.name, HealthState.OK, target.name)
        self._monitor_code(refused, target.name, HealthState.OK, target.name)

        known = KNOWN_VERSIONS[target.kind]
        if version is None:
            # This target's contract publishes no version at all -- the
            # platform's operator surface does not -- so nothing was checked and
            # `ok` would be a claim about a measurement nobody made. `unknown`,
            # named, and it stays that way until that surface carries one.
            self._monitor_code(
                MonitorCode.TARGET_CONTRACT_UNSUPPORTED,
                target.name,
                HealthState.UNKNOWN,
                target.name,
            )
        elif version not in known:
            # It came up on a version this build cannot read while the monitor
            # was running. Its codes STOP being passed through: a state whose
            # meaning may have changed is not a state, and publishing it anyway
            # is exactly the half-read the contract forbids.
            log.error(
                "%s speaks contract version %r, which this monitor cannot read",
                target.name,
                version,
            )
            self._monitor_code(
                MonitorCode.TARGET_CONTRACT_UNSUPPORTED,
                target.name,
                HealthState.ACTIVE,
                target.name,
            )
            self._retire(target.name)
            entries = ()
        else:
            self._monitor_code(
                MonitorCode.TARGET_CONTRACT_UNSUPPORTED, target.name, HealthState.OK, target.name
            )

        self._targets[target.name] = TargetHealth(
            name=target.name,
            kind=target.kind.value,
            polled_at=self._now(),
            contract_version=version,
            codes=tuple(entries),
        )
        for entry in entries:
            self._passed_through(target.name, entry)

    def _forget_target_payload(self, target: Target) -> None:
        """A target that did not give us a payload has no payload published.

        `polled_at` and `contract_version` keep what they had -- they say when
        this target was last read and as what -- and `codes` empties, because a
        lane's health as of whenever it was last reachable is indistinguishable
        from now.
        """
        self._targets[target.name] = TargetHealth(
            name=target.name,
            kind=target.kind.value,
            polled_at=self._targets[target.name].polled_at,
            contract_version=self._targets[target.name].contract_version,
            codes=(),
        )

    def _read(self, target: Target) -> tuple[int | None, tuple[dict, ...]]:
        """One target's poll. Every branch is a GET and nothing else."""
        client = self._clients[target.name]
        if target.kind is TargetKind.LANE:
            body = client.get("/v1/lane/health")
            codes = body.get("codes")
            if not isinstance(codes, list):
                raise TargetUnreachable(f"{target.name}: health payload carries no `codes` list")
            version = _version(body.get("contract_version"))
            entries = tuple(
                entry for entry in codes if isinstance(entry, dict) and entry.get("code")
            )
            _refuse_unreadable(target.name, entries, version)
            return version, entries
        if target.kind is TargetKind.IDENTITY_SERVICE:
            # That contract publishes no malfunction table, so this target
            # contributes reachability and its version and nothing else. Its
            # `status` is read by the LANE, which publishes it as
            # `identity_service_degraded`, and it reaches this surface through
            # that -- passed through, from the observer that already measures it.
            # A monitor with no lane declared therefore does not see degradation:
            # that is a gap, and it is named here rather than filled by a second
            # observer of one field.
            body = client.get("/v1/health")
            return _version(body.get("schema_version")), ()
        if target.kind in (TargetKind.CAPTURE, TargetKind.AGENT):
            # The capture process publishes a malfunction table in the LANE's
            # entry shape, on purpose, so it is read by the code that already
            # reads a lane: same states, same sources, same `never_alarm` on the
            # wire, same refusal when one of them is unreadable. This is how
            # `camera_unreachable` -- Gokhan's "camera disconnected is a
            # malfunction" -- gets from a camera nobody can reach to a human who
            # can go and look at it. The AGENT publishes the same shape for the
            # same reason, so this branch is one branch and not two: a third
            # dialect would be a third place for `never_alarm` to be read wrong.
            route = (
                "/v1/capture/health"
                if target.kind is TargetKind.CAPTURE
                else "/v1/agent/health"
            )
            body = client.get(route)
            codes = body.get("codes")
            if not isinstance(codes, list):
                raise TargetUnreachable(
                    f"{target.name}: health payload carries no `codes` list"
                )
            version = _version(body.get("contract_version"))
            entries = tuple(
                entry for entry in codes if isinstance(entry, dict) and entry.get("code")
            )
            _refuse_unreadable(target.name, entries, version)
            return version, entries
        return None, self._devices(target)

    def _devices(self, target: Target) -> tuple[dict, ...]:
        """`lane_gone_quiet`, per device, off the platform's devices route.

        The platform writes `lane_devices.last_seen_at` on every authenticated
        lane request and is the only thing that can see a lane fall silent. It
        publishes the timestamp and deliberately no verdict; the threshold is
        this site's, and it is an assumption -- see `lane_quiet_seconds`.

        **This comparison spans two clocks.** The timestamp is the platform's and
        `now` is this monitor's, so a monitor whose clock is wrong reports lanes
        as quiet that are not, or misses ones that are. Stated rather than
        corrected: correcting it would mean measuring the offset, which is a
        second measurement nobody has made.

        A REVOKED device is skipped. A credential that was deliberately ended and
        then stopped being seen is not a fault, and paging on it would train
        whoever reads these messages to ignore them.

        A device that has NEVER been seen is measured from when it was created,
        not treated as unmeasurable: a credential issued a week ago and never
        used is a lane that never came up, which is precisely the thing worth
        knowing at an installation.
        """
        body = self._clients[target.name].get(f"/garages/{target.garage_id}/devices")
        devices = body.get("devices")
        if not isinstance(devices, list):
            raise TargetUnreachable(f"{target.name}: devices payload carries no `devices` list")

        seen: set[str] = set()
        for device in devices:
            if not isinstance(device, dict) or not isinstance(device.get("id"), str):
                continue
            if device.get("revoked_at"):
                continue
            device_id = device["id"]
            seen.add(device_id)
            last = device.get("last_seen_at") or device.get("created_at")
            age = _age_seconds(last, self._now())
            if age is None:
                state = HealthState.UNKNOWN
            elif age > target.lane_quiet_seconds:
                state = HealthState.ACTIVE
            else:
                state = HealthState.OK
            self._monitor_code(MonitorCode.LANE_GONE_QUIET, device_id, state, target.name)

        # A device that has left the listing -- revoked, or deleted -- stops
        # being measured. That is `no_longer_measured`, once, and not a silent
        # disappearance from the health route.
        for (scope, name, code, subject) in list(self._states):
            if (
                scope == OWN
                and name == target.name
                and code == MonitorCode.LANE_GONE_QUIET.value
                and subject not in seen
            ):
                self._monitor_code(
                    MonitorCode.LANE_GONE_QUIET, subject, HealthState.UNKNOWN, target.name
                )
        return ()

    # -- the transition rule ------------------------------------------------

    def _monitor_code(
        self,
        code: MonitorCode,
        subject: str,
        state: HealthState,
        target: str,
        status: int | None = None,
    ) -> None:
        """One of the monitor's OWN codes. Always `measured`, never `never_alarm`."""
        self._observe(
            scope=OWN,
            target=target,
            code=code.value,
            subject=subject,
            state=state.value,
            source=Source.MEASURED.value,
            caveat=None,
            never_alarm=False,
            status=status,
        )

    def _passed_through(self, target: str, entry: dict) -> None:
        """One code a TARGET published, taken exactly as it published it.

        `state`, `source`, `never_alarm` and `caveat` all come off the wire.
        Nothing here re-derives any of them: a monitor that decided for itself
        whether a lane's code was measured would be a second copy of that lane's
        claim about its own instrumentation, and the copy is the one that lies.

        `never_alarm` in particular. If this package held its own set, the two
        would drift, and the drift shows up as a technician dispatched because a
        car arrived on low-texture ground -- the failure the lane's caveat exists
        to prevent, reintroduced by its reader.
        """
        # `subject` PASSED THROUGH when the target published one, and the
        # target's own name when it did not. A lane's entries have no subject:
        # its codes are about the lane. A capture process's are about a NAMED
        # CAMERA, and folding those under the target's name would send a message
        # saying a camera is dead without saying which -- at a site with four
        # cameras, that is a message somebody has to go and work out.
        subject = entry.get("subject")
        self._observe(
            scope=PASSED_THROUGH,
            target=target,
            code=str(entry.get("code")),
            subject=str(subject) if isinstance(subject, str) and subject else target,
            state=str(entry.get("state")),
            source=str(entry.get("source")),
            caveat=entry.get("caveat"),
            # Already known to be a boolean and already known to be one of the
            # three states: `_refuse_unreadable` refused the whole payload
            # otherwise, before anything here was observed. `bool(...)` used to
            # stand here and it is what made an ABSENT field page and the string
            # `"false"` silence.
            never_alarm=entry["never_alarm"],
        )

    def _observe(
        self,
        *,
        scope: str,
        target: str,
        code: str,
        subject: str,
        state: str,
        source: str,
        caveat,
        never_alarm: bool,
        status: int | None = None,
    ) -> None:
        key = (scope, target, code, subject)
        previous = self._states.get(key, HealthState.UNKNOWN.value)
        self._states[key] = state
        self._facts[key] = {
            "source": source,
            "caveat": caveat,
            "never_alarm": never_alarm,
            "status": status,
        }

        transition = self._transition(key, previous, state)
        if transition is None:
            return
        if never_alarm:
            # Recorded above, and that is all. The state is on the health route
            # and in this monitor's memory; what does not happen is a message.
            log.info(
                "%s/%s %s -> %s, never_alarm on the wire: not sent", target, code, previous, state
            )
            return
        self._notified_at[key] = self._clock()
        self._send(
            Notification(
                site_id=self.config.site_id,
                lane_id=self._lane_id_of(target),
                target=target,
                code=code,
                subject=subject if subject != target else None,
                transition=transition.value,
                source=source,
                caveat=caveat if isinstance(caveat, str) and caveat else None,
                at=self._now(),
                status=status,
            )
        )

    def _lane_id_of(self, target: str) -> str | None:
        """The lane a notification is about, or `null`, as the contract says.

        `null` for the platform's codes, for the identity service's, and for
        this monitor's own -- the sink that could not deliver and the target it
        could not reach are not facts about a lane. Stamped unconditionally, a
        lane's id turned `platform_unreachable` into "lane fl-lane-a cannot
        reach the platform": a different machine, a different fault, and a
        different repair from the true one. And it put a FALSE discriminator
        beside the only true one on the two codes that collide by name.
        """
        return self._lane_id if self._kinds.get(target) is TargetKind.LANE else None

    def _transition(self, key, previous: str, state: str) -> Transition | None:
        """The whole rule, in one place, so there is one copy of it.

        `unknown -> ok` is deliberately silent. Nothing was ever claimed about
        that code and now it is fine; there is no news in it, and a message would
        train the reader to skim.
        """
        ok = HealthState.OK.value
        active = HealthState.ACTIVE.value
        unknown = HealthState.UNKNOWN.value
        if state == active and previous in (ok, unknown):
            return Transition.RAISED
        if state == ok and previous == active:
            return Transition.RECOVERED
        if state == unknown and previous in (ok, active):
            return Transition.NO_LONGER_MEASURED
        if state == active and previous == active and self.config.renotify_seconds is not None:
            since = self._notified_at.get(key)
            if since is None or self._clock() - since >= self.config.renotify_seconds:
                return Transition.STILL_ACTIVE
        return None

    def _retire(self, target: str) -> None:
        """Everything this target used to say is now UNMEASURED, and says so.

        A target that has stopped answering has not become healthy, and leaving
        its last known states on the health route would publish a lane's health
        as of whenever it was last reachable, indistinguishable from now.
        """
        for key in list(self._states):
            scope, name, code, subject = key
            # The monitor's OWN codes are not retired here. They are its
            # measurements about the target, not the target's about itself, and
            # `<kind>_unreachable` in particular has just been set to `active` by
            # the caller -- retiring it would immediately unset it.
            if scope != PASSED_THROUGH or name != target:
                continue
            fact = self._facts.get(key, {})
            self._observe(
                scope=PASSED_THROUGH,
                target=name,
                code=code,
                subject=subject,
                state=HealthState.UNKNOWN.value,
                source=str(fact.get("source", Source.NOT_MEASURED.value)),
                caveat=fact.get("caveat"),
                never_alarm=bool(fact.get("never_alarm")),
            )

    # -- telling somebody ---------------------------------------------------

    def _send(self, notification: Notification) -> None:
        """Every sink gets it, and a sink that could not deliver is itself news."""
        self._record(notification)
        failures = []
        for sink in self.sinks:
            try:
                sink.deliver(notification)
            except DeliveryFailed as exc:
                failures.append((sink.name, str(exc)))
            except Exception as exc:  # noqa: BLE001
                # A sink that raises something else is still a sink that did not
                # deliver. Letting it escape would take the monitor down with the
                # endpoint it was trying to reach.
                failures.append((sink.name, f"{type(exc).__name__}: {exc}"))
        self._sink_states(failures)

    def _sink_states(self, failures: list[tuple[str, str]]) -> None:
        failed = {name for name, _ in failures}
        for name, reason in failures:
            log.error("sink %s could not deliver: %s", name, reason)
        if self._reporting_a_sink:
            # A failure while reporting a failure. Recorded on the health route
            # by the loop below and told to nobody: one dead endpoint must not
            # become a message about a message about a message.
            for sink in self.sinks:
                state = HealthState.ACTIVE if sink.name in failed else HealthState.OK
                key = (
                    OWN,
                    self.config.monitor_id,
                    MonitorCode.SINK_DELIVERY_FAILED.value,
                    sink.name,
                )
                self._states[key] = state.value
                self._facts[key] = {
                    "source": Source.MEASURED.value,
                    "caveat": None,
                    "never_alarm": False,
                }
            return
        self._reporting_a_sink = True
        try:
            for sink in self.sinks:
                self._monitor_code(
                    MonitorCode.SINK_DELIVERY_FAILED,
                    sink.name,
                    HealthState.ACTIVE if sink.name in failed else HealthState.OK,
                    self.config.monitor_id,
                )
        finally:
            self._reporting_a_sink = False

    def _record(self, notification: Notification) -> None:
        if len(self._log) == self._log.maxlen:
            self._dropped += 1
        self._cursor += 1
        self._log.append((self._cursor, notification))

    def _announce_unmeasured(self) -> None:
        """One message at startup: what nobody is measuring, and where it lives.

        It is not a page and it is not a transition. It is the answer to the
        question an operator cannot otherwise ask -- "what does this monitor NOT
        know?" -- and it is sent once, because sending it every poll would make
        it wallpaper.

        The same information is on `GET /v1/monitor/health` continuously, which
        is why it is not on the events route: that route serves transitions, and
        this is not one.
        """
        # ONE ENUMERATION, and it is `health()` -- the same answer the route
        # serves. A second walk over `self._states` stood here, and the two did
        # not agree: `health()` synthesises an `unknown` entry for every
        # `MonitorCode` that has no subject yet, and those never appeared in
        # this message. The omitted ones were exactly this monitor's own blind
        # spots -- with no platform declared, NOBODY is measuring whether a lane
        # has gone quiet, and the one message whose stated purpose is "what does
        # this monitor NOT know?" did not say so. A claim lives in one place.
        health = self.health()
        unmeasured = [
            {
                "target": self.config.monitor_id,
                "code": entry.code,
                "subject": entry.subject,
                "source": entry.source.value,
            }
            for entry in health.codes
            if entry.state == HealthState.UNKNOWN.value
        ]
        unmeasured += [
            {
                "target": target.name,
                "code": str(entry.get("code")),
                "subject": None,
                "source": str(entry.get("source", "")),
            }
            for target in health.targets
            for entry in target.codes
            if isinstance(entry, dict) and entry.get("state") == HealthState.UNKNOWN.value
        ]
        payload = {
            "site_id": self.config.site_id,
            "monitor_id": self.config.monitor_id,
            "at": self._now(),
            "targets": [target.name for target in self.config.targets],
            "sinks": [sink.name for sink in self.sinks],
            "unmeasured": unmeasured,
        }
        subject = f"[{self.config.site_id}] monitor started"
        for sink in self.sinks:
            try:
                sink.announce(subject, payload)
            except Exception as exc:  # noqa: BLE001
                # Startup is the one moment a failing sink cannot be reported
                # through the others, because nothing has been established yet.
                # Logged, and the first real notification reports it properly.
                log.error("sink %s could not deliver the startup message: %s", sink.name, exc)

    # -- the read surface ---------------------------------------------------

    def describe(self) -> MonitorDescription:
        return MonitorDescription(
            monitor_id=self.config.monitor_id,
            site_id=self.config.site_id,
            targets=tuple(
                TargetDescription(
                    name=target.name,
                    kind=target.kind.value,
                    # Scheme, host, port and path, REBUILT -- never the string as
                    # configured. `https://ops:S3CRET@example.com` was
                    # accepted and republished verbatim here, beside
                    # `authenticated: false`.
                    url=published_url(target.url),
                    poll_seconds=target.poll_seconds,
                    authenticated=target.authenticated,
                    timeout_seconds=target.timeout_seconds,
                )
                for target in self.config.targets
            ),
            sinks=tuple(
                SinkDescription(name=sink.name, kind=sink.kind) for sink in self.sinks
            ),
            event_window_depth=self.config.event_window_depth,
        )

    def health(self) -> MonitorHealth:
        """The monitor's own codes, and every target's, passed through.

        Every member of `MonitorCode` ships, every time. A code with no subject
        yet -- `lane_gone_quiet` before any device has been listed, or any code
        whose target is not declared at all -- ships once under this monitor's
        own id, `unknown`. An absent code reads exactly like a healthy one.
        """
        own: list[MonitorEntry] = []
        for code in MonitorCode:
            subjects: dict[str, tuple[str, int | None]] = {}
            for key, state in self._states.items():
                scope, _target, seen_code, subject = key
                if scope == OWN and seen_code == code.value:
                    subjects[subject] = (state, self._facts.get(key, {}).get("status"))
            if not subjects:
                subjects = {self.config.monitor_id: (HealthState.UNKNOWN.value, None)}
            own.extend(
                MonitorEntry(code=code.value, subject=subject, state=state, status=status)
                for subject, (state, status) in sorted(subjects.items())
            )
        return MonitorHealth(
            codes=tuple(own),
            targets=tuple(self._targets[target.name] for target in self.config.targets),
        )

    def events(self, since: int) -> EventPage:
        current = self._cursor
        oldest = self._log[0][0] if self._log else None
        return EventPage(
            cursor=current,
            # Two ways a saved position stops referring to anything, and both are
            # `reset` because to a consumer they are the same fact: what you
            # asked for is gone and you did not get it.
            reset=since > current or (oldest is not None and since + 1 < oldest),
            dropped=self._dropped,
            events=tuple(
                {"cursor": seq, **notification.to_dict()}
                for seq, notification in self._log
                if seq > since
            ),
        )


def _refuse_unreadable(target: str, entries: tuple[dict, ...], version: int | None) -> None:
    """Every entry, or none of them. The two fields a reader may not guess at.

    `state` and `never_alarm` are what this monitor DOES something with: one
    decides whether a fault is raised, the other whether a human is woken. A
    value outside what the contract defines for either is not a value to be
    interpreted generously -- it is a payload this build cannot read, and the
    contract's own answer to that is to refuse it whole rather than half-read
    it.

    Refusing WHOLE, and not entry by entry, on purpose: a lane publishing one
    unreadable entry is a lane whose serialiser this build does not understand,
    and passing through the rest of its payload would publish a partial view of
    a lane's health as though it were the whole. The same rule the version
    refusal follows.
    """
    states = tuple(state.value for state in HealthState)
    for entry in entries:
        code = entry.get("code")
        never_alarm = entry.get("never_alarm")
        if not isinstance(never_alarm, bool):
            raise ContractViolation(
                f"{target}: `{code}` publishes never_alarm={never_alarm!r}. The contract requires "
                "a JSON boolean on every entry. Absent could be a lane with nothing to say or a "
                "lane whose serialiser dropped it, and the two point opposite ways -- one pages a "
                "technician because a car arrived, the other silences a real fault for ever.",
                version,
            )
        state = entry.get("state")
        if state not in states:
            raise ContractViolation(
                f"{target}: `{code}` publishes state={state!r}, which is not one of {states}. "
                "Passed through, a state outside the set can never produce a transition -- and it "
                "poisons the next one, so an ACTIVE fault after it is held, published as active, "
                "and told to nobody.",
                version,
            )


def _version(value):
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _age_seconds(stamp, now: str) -> float | None:
    """How long ago `stamp` was, from this monitor's clock. `None` if unreadable.

    Unreadable is `None` and not zero: a timestamp this build cannot parse is a
    device it has not measured, and zero would read as "seen just now", which is
    the reassuring direction.
    """
    if not isinstance(stamp, str) or not stamp:
        return None
    try:
        then = datetime.fromisoformat(stamp)
        moment = datetime.fromisoformat(now)
    except ValueError:
        return None
    if then.tzinfo is None:
        return None
    return (moment - then).total_seconds()


__all__ = [
    "KNOWN_IDENTITY_VERSIONS",
    "KNOWN_LANE_VERSIONS",
    "KNOWN_VERSIONS",
    "ContractViolation",
    "Monitor",
    "UnsupportedContract",
    "utc_now",
]
