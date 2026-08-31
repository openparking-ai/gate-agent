"""The capture process: it photographs a lane, and it opens nothing.

Gokhan's spec, his words: *"camera captures an image every minute and every time
the gate opens"*, *"camera disconnected is a malfunction"*, *"we need to save the
picture of the cars"*. SETTLED 3g: when the barrier is broken the camera's job
changes from DECIDING to RECORDING -- capture every entry, timestamp and image so
the entries can be reconstructed. Until this existed nothing in this estate kept
an image anywhere: the lane grabs frames at an arrival, hands them to the
identifier and drops them.

**IT HAS NO OPENING AUTHORITY EITHER.** It reads a camera and it reads a lane's
READ contract, both `GET`, and it writes to its own directory. There is no client
in this package capable of another method, swept out of the source and observed
at the lane.

**IT IS A CONSUMER OF THE LANE CONTRACT.** It learns that a car arrived and that
the lane vended from `GET /v1/lane/events?since=` -- the seat round 2 built, and
the seat a third party takes. It imports nothing from `lane_controller`, and the
lane is not touched by this round at all: the lane's vend path is the boundary
every outside reviewer named, and a store the lane had to POST into would make
that path depend on a process that need not exist.

**THE COST OF THAT SEAT, AND IT IS MEASURED RATHER THAN DESCRIBED.** This process
learns about an arrival by POLLING, so the picture is taken when the event was
SEEN and not when the frames were grabbed. Every lane-triggered record carries
`capture_minus_lane_event_ms`, which is that subtraction, on that record, at that
site -- named for what it is, because it spans this process's clock and the
lane's. What it can and cannot be read as is stated once, in
`contract.CAPTURE_MINUS_LANE_EVENT_NOTE`.

**A LANE THIS PROCESS DID NOT WRITE IS THE DESIGNED CASE.** A page it cannot
read -- a timestamp with no offset, a cursor that went backwards without
`reset` -- is refused WHOLE: `lane_contract_unsupported`, the cursor not
adopted, nothing photographed under a lane reason. A `reset` is
`lane_backlog_lost` and a count, because what was in that gap can never be
photographed.

**Standalone is a MODE.** With no lane declared it takes minute captures and says
so on the line it prints when it starts. A garage with a camera and no gate is a
customer of this process, not a degraded installation.

**Nothing it stores identifies a vehicle.** Not a plate, not a plate region, not
a colour, not a make -- and not a lane event's `detail`, which is where the lane
puts what it knows. What a record carries about the trigger is the event's
CURSOR and the time the lane recorded, which is a reference and not a copy. The
join to who the car was lives at the lane's platform, under that cursor.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import UTC, datetime

from .camera import CameraRefusedUs, CameraUnreachable, SnapshotCamera
from .client import ReadOnlyClient, TargetRefusedUs, TargetUnreachable
from .config import CaptureConfig
from .contract import (
    CAMERA_CODES,
    KNOWN_LANE_VERSIONS,
    LANE_CODES,
    STORE_CODES,
    CameraDescription,
    CaptureCode,
    CaptureDescription,
    CaptureEntry,
    CaptureHealth,
    CaptureReason,
    HealthState,
    RecordPage,
    RecordRef,
    StoreReads,
    published_url,
)
from .store import CaptureStore, StoreOverBudget, StoreRecordRefused, StoreUnwritable

log = logging.getLogger(__name__)

#: The lane event kinds that make this process take a picture, and the reason it
#: files the capture under. ONE mapping, so a kind cannot become a trigger
#: without a reason to store it by.
#:
#: `entry_pending` is deliberately NOT here. It is the lane's record that a
#: ticket came out and a car has not yet been confirmed through, it carries
#: `plate_region` in its `detail`, and it arrives after `vended` -- so it would
#: photograph the same vehicle a second time and bring an attribute of that
#: vehicle to the edge of a store that must not hold one.
TRIGGERS: dict[str, CaptureReason] = {
    "frames_captured": CaptureReason.LANE_ARRIVAL,
    "vended": CaptureReason.LANE_VEND,
}

#: `subject` for the codes that are about the STORE rather than a camera. The
#: process's own id, the way the monitor files its subject-less codes under its.
#: Never the directory: a path on a box is on `GET /v1/capture`, where somebody
#: asking what this process is set to do will look for it, and not repeated onto
#: every health entry.
STORE = "store"

#: `subject` for the codes that are about the LANE. At a standalone site there
#: is no lane to name, so they ship under this process's own id -- which is the
#: contract's rule for a code with no subject, and is a different fact from
#: "the lane is answering".
LANE = "lane"


class UnsupportedLaneContract(Exception):
    """The lane answered with a version this build cannot read.

    Raised from `start()`, before anything is captured, because a configuration
    pointing at something this process cannot interpret is a configuration error
    and not a fault at the lane. A lane that is merely DOWN at startup is not
    this: that is `lane_unreachable`, active, immediately, and the process
    carries on taking minute captures -- which is exactly the job SETTLED 3g
    gives it for a lane that is not working.
    """


def utc_now() -> datetime:
    return datetime.now(UTC)


class CaptureProcess:
    """Photographs on a timer and on a lane's events, and answers for its store."""

    def __init__(
        self,
        config: CaptureConfig,
        store: CaptureStore,
        clock=None,
        now=utc_now,
        camera_factory=SnapshotCamera,
        client_factory=ReadOnlyClient,
    ) -> None:
        self.config = config
        self.store = store
        self._now = now
        # ONE CLOCK for the whole process. The store's retention window, the
        # projection it publishes and the timestamp on every record are all read
        # from the same callable the scheduler uses -- two clocks here would let
        # a record be stamped at one moment and deleted by another.
        store.adopt_clock(now)
        # The scheduler runs on the WALL CLOCK read, not a second monotonic one:
        # every capture is stamped with `now()` and the interval is the distance
        # between two of those stamps. Two clocks here would let a record be
        # stamped at one moment and scheduled by another.
        self._clock = clock or (lambda: self._now().timestamp())
        self._cameras = {
            camera.camera_id: camera_factory(
                camera.camera_id,
                camera.snapshot_url,
                camera.username,
                camera.password,
                camera.timeout_seconds,
                camera.max_snapshot_bytes,
            )
            for camera in config.cameras
        }
        self._lane = (
            client_factory(config.lane.url, config.lane.token, config.lane.timeout_seconds)
            if config.lane is not None
            else None
        )
        #: `subject` for the four lane codes: the lane, or -- with no lane
        #: declared -- this process's own id, because there is no lane to name.
        self._lane_subject = LANE if config.lane is not None else config.capture_id
        #: The last state observed for every (code, subject), COMPLETE FROM THE
        #: FIRST RESPONSE. Every declared camera is here under every camera code
        #: before anything has been attempted, `unknown`, because a camera that
        #: has never produced a state is exactly the camera worth asking about
        #: -- and under a per-CODE rule alone it vanishes from the payload the
        #: moment any OTHER camera reports. `CaptureHealth` refuses a payload
        #: that is not complete against the declared cameras, so this cannot
        #: come apart from what is published.
        self._states: dict[tuple[str, str], str] = {
            (code.value, subject): HealthState.UNKNOWN.value
            for code, subject in (
                *((code, camera.camera_id) for code in CAMERA_CODES for camera in config.cameras),
                *((code, STORE) for code in STORE_CODES),
                *((code, self._lane_subject) for code in LANE_CODES),
            )
        }
        self._status: dict[tuple[str, str], int | None] = {}
        self._cause: dict[tuple[str, str], str | None] = {}
        #: How many lane events this process is known not to have followed,
        #: since it started. Only a `reset` can raise it: everything else this
        #: process refuses, it refuses WHOLE and re-reads.
        self._lane_events_missed = 0
        #: The digest of each camera's last snapshot. A digest and not the bytes:
        #: `camera_feed_frozen` asks whether two snapshots were IDENTICAL, which
        #: a digest answers exactly, and holding the last megabyte of every
        #: camera in memory to answer it would be a process that grows with the
        #: number of cameras it watches.
        self._last_digest: dict[str, str] = {}
        self._due: dict[str, float] = {camera_id: 0.0 for camera_id in self._cameras}
        self._lane_due = 0.0
        #: `None` until the first successful read of the lane's events route.
        self._cursor: int | None = None

    # -- startup -----------------------------------------------------------

    def start(self) -> None:
        """Open the store, report what the disk held, and take the lane's place.

        The store is opened first because a directory that will not take a write
        is a configuration failure and not a malfunction, and a process that
        started on one would photograph a lane for a fortnight and keep nothing.
        """
        # `StoreUnwritable` is deliberately not caught: a directory that will
        # not take a write is a configuration failure, not a malfunction, and a
        # process that started on one would photograph a lane for a fortnight
        # and keep nothing while every other signal said it was working.
        self.store.open()
        self._code(
            CaptureCode.STORE_UNWRITABLE, STORE, HealthState.OK
        )
        self._code(
            CaptureCode.STORE_OVER_BUDGET, STORE, HealthState.OK
        )
        # What the rebuild found. `active` if this store held half a record --
        # the record is gone, purged, and the FAULT is that it happened.
        self._code(
            CaptureCode.STORE_RECORD_INCOMPLETE,
            STORE,
            HealthState.ACTIVE if self.store.incomplete else HealthState.OK,
        )
        self.store.purge(self._now())
        self._read_clock()
        if self._lane is not None:
            self._read_lane_identity()
            # TAKE THE LANE'S PLACE at the current cursor, before the first
            # capture. What is already in that lane's window happened before
            # this process existed and those cars have gone; a picture taken now
            # and filed against one of those events would be an image of an
            # empty lane carrying a reference to a vehicle, which reads as a
            # record OF that vehicle. A lane that is down at startup leaves the
            # cursor unestablished and the first poll that reaches it does this.
            self._poll_lane()

    def _read_clock(self) -> None:
        """`clock_stepped_back`, from the measurement the purge just made.

        Read rather than derived a second time here: the purge is where the
        newest record held is compared against the clock that decides what is
        old, and a second comparison in this file would be a second copy of the
        same claim.
        """
        self._code(
            CaptureCode.CLOCK_STEPPED_BACK,
            STORE,
            HealthState.ACTIVE if self.store.clock_stepped_back else HealthState.OK,
        )

    def _read_lane_identity(self) -> None:
        """`GET /v1/lane` once, for the version this build is about to read.

        A lane that does not answer is not refused: it is down, which is a
        malfunction reported by the first poll, and a capture process that
        refused to start on a broken lane would be absent at the one moment
        SETTLED 3g says it matters most.
        """
        try:
            body = self._lane.get("/v1/lane")
        except (TargetRefusedUs, TargetUnreachable) as exc:
            log.warning("the lane did not answer its identity route: %s", exc)
            return
        version = body.get("contract_version")
        if version is not None and version not in KNOWN_LANE_VERSIONS:
            raise UnsupportedLaneContract(
                f"the lane at {self.config.lane.url} declares contract version {version!r}; this "
                f"capture process reads {KNOWN_LANE_VERSIONS}. Refusing to start: it would be "
                "deciding which events mean a car arrived from a vocabulary it had guessed."
            )

    # -- the loop ----------------------------------------------------------

    def poll(self, force: bool = False) -> None:
        """Whatever is due: the lane's events first, then the minute captures.

        The lane first on purpose. An arrival that has just been recorded is
        worth a picture NOW, and taking the interval capture first would spend
        this camera's turn on the clock's picture and file the arrival's a beat
        later, with `capture_minus_lane_event_ms` carrying the difference.
        """
        moment = self._clock()
        if self._lane is not None and (force or moment >= self._lane_due):
            self._lane_due = moment + self.config.lane.poll_seconds
            self._poll_lane()
        for camera_id in self._cameras:
            moment = self._clock()
            if not force and moment < self._due[camera_id]:
                continue
            self._due[camera_id] = moment + self.config.interval_seconds
            self.capture(camera_id, CaptureReason.INTERVAL)

    def _refuse_page(self, why: str) -> None:
        """THE WHOLE PAGE, and nothing from it.

        The cursor is NOT adopted, so the next poll asks for the same events
        again and this recovers by itself the moment the lane serves a page this
        build can read. Nothing is photographed under a lane reason: a capture
        filed against a reference this process could not interpret is an image
        of a lane carrying a claim about an event, which reads as a record OF
        that event.

        The same answer, and the same code shape, as the monitor's
        `target_contract_unsupported`. A lane this process did not write is the
        DESIGNED case -- SETTLED 1: works standalone, integrates with a third
        party's, through one versioned contract -- so this is not the exotic
        path, and it may not be the silent one.
        """
        log.error("the lane's events page was refused whole: %s", why)
        self._code(CaptureCode.LANE_CONTRACT_UNSUPPORTED, self._lane_subject, HealthState.ACTIVE)

    def _poll_lane(self) -> None:
        """One read of the lane's events route, and every ending said out loud."""
        subject = self._lane_subject
        try:
            page = self._lane.get(f"/v1/lane/events?since={self._cursor or 0}")
        except TargetRefusedUs as exc:
            log.warning("the lane refused us: HTTP %s", exc.status)
            self._code(CaptureCode.LANE_UNREACHABLE, subject, HealthState.OK)
            self._code(CaptureCode.LANE_REFUSED_US, subject, HealthState.ACTIVE, exc.status)
            return
        except TargetUnreachable as exc:
            log.warning("the lane is unreachable: %s", exc)
            self._code(CaptureCode.LANE_UNREACHABLE, subject, HealthState.ACTIVE)
            # Whether it would refuse us is not a question this poll answered.
            self._code(CaptureCode.LANE_REFUSED_US, subject, HealthState.UNKNOWN)
            return
        self._code(CaptureCode.LANE_UNREACHABLE, subject, HealthState.OK)
        self._code(CaptureCode.LANE_REFUSED_US, subject, HealthState.OK)

        cursor = page.get("cursor")
        if not isinstance(cursor, int) or isinstance(cursor, bool):
            return self._refuse_page("it answered no cursor")
        events = page.get("events")
        if not isinstance(events, list):
            return self._refuse_page("it answered no `events` list")

        if self._cursor is None:
            # FIRST READ. This process takes the lane's place at the CURRENT
            # cursor and photographs nothing for what is already in the window.
            # Those cars have gone: a picture taken now, filed against an event
            # from before this process started, would be an image of an empty
            # lane carrying a reference to a vehicle -- which is worse than the
            # absence, because it looks like a record of that vehicle.
            self._cursor = cursor
            log.info("following the lane's events from cursor %d", cursor)
            self._code(CaptureCode.LANE_CONTRACT_UNSUPPORTED, subject, HealthState.OK)
            self._code(CaptureCode.LANE_BACKLOG_LOST, subject, HealthState.OK)
            return
        if page.get("reset"):
            # The saved position no longer refers to anything: the lane
            # restarted, or it has evicted further than this process fell
            # behind. What was in that gap was never photographed and cannot be
            # -- those cars have gone -- so it is a CODE and a COUNT and not
            # only a log line on a box nobody is reading. SETTLED 3g's capture
            # mode exists so the entries can be reconstructed, and the busiest
            # hour is exactly the hour that outruns a window.
            missed = max(cursor - self._cursor, 0)
            self._lane_events_missed += missed
            log.warning(
                "the lane reported reset at cursor %d; %d event(s) were not followed",
                cursor,
                missed,
            )
            self._cursor = cursor
            self._code(CaptureCode.LANE_CONTRACT_UNSUPPORTED, subject, HealthState.OK)
            self._code(CaptureCode.LANE_BACKLOG_LOST, subject, HealthState.ACTIVE)
            return
        if cursor < self._cursor:
            # A CURSOR THAT WENT BACKWARDS WITHOUT `reset`. The lane contract
            # says the cursor is monotonic within a run and that a restart sets
            # `reset`; this is a lane breaking its own contract. Adopting it
            # would re-serve the same events on the next poll and photograph
            # them again, for ever -- and every duplicate consumes `max_bytes`,
            # so the size purge then evicts real captures to make room for them.
            return self._refuse_page(
                f"cursor {cursor} is behind the {self._cursor} this process holds, with "
                "reset:false; the lane contract says the cursor is monotonic within a run"
            )

        # EVERY TRIGGER IS READ AND JUDGED BEFORE ANY CAMERA IS TOUCHED. A page
        # holding one event this build cannot interpret is refused whole rather
        # than half-followed: a partly-applied page leaves the cursor claiming
        # events that were never photographed.
        triggers = []
        for event in events:
            if not isinstance(event, dict):
                return self._refuse_page("an entry in `events` is not an event")
            reason = TRIGGERS.get(str(event.get("kind")))
            if reason is None:
                # A kind this build does not trigger on. NOT a contract break:
                # the lane contract says a consumer ignores what it does not
                # recognise, and a lane gaining an event kind is the ordinary
                # case.
                continue
            event_cursor = event.get("cursor")
            occurred_at = event.get("occurred_at")
            if not isinstance(event_cursor, int) or isinstance(event_cursor, bool):
                return self._refuse_page(f"a {event.get('kind')!r} event carries no cursor")
            if not isinstance(occurred_at, str) or not occurred_at:
                return self._refuse_page(f"a {event.get('kind')!r} event carries no occurred_at")
            try:
                naive = datetime.fromisoformat(occurred_at).tzinfo is None
            except ValueError:
                return self._refuse_page(
                    f"a {event.get('kind')!r} event's occurred_at {occurred_at!r} is not a "
                    "timestamp this build can read"
                )
            if naive:
                # NO OFFSET. It is not a moment this process can subtract from
                # its own -- two machines, two timezones -- and a capture filed
                # under a lane reason with the reference dropped is a record
                # this package's own contract refuses to publish.
                return self._refuse_page(
                    f"a {event.get('kind')!r} event's occurred_at {occurred_at!r} carries no "
                    "UTC offset; this contract requires an explicit one"
                )
            triggers.append((reason, event_cursor, occurred_at))

        self._code(CaptureCode.LANE_CONTRACT_UNSUPPORTED, subject, HealthState.OK)
        self._code(CaptureCode.LANE_BACKLOG_LOST, subject, HealthState.OK)
        for reason, event_cursor, occurred_at in triggers:
            for camera_id in self._cameras:
                # NOTHING from `event["detail"]`. Not read here, not passed, not
                # available to the writer: the cursor and the time are the whole
                # reference, and `entry_pending`'s `plate_region` is why.
                self.capture(
                    camera_id,
                    reason,
                    lane_event_cursor=event_cursor,
                    lane_event_at=occurred_at,
                )
        self._cursor = cursor

    # -- one picture -------------------------------------------------------

    def capture(
        self,
        camera_id: str,
        reason: CaptureReason,
        lane_event_cursor: int | None = None,
        lane_event_at: str | None = None,
    ):
        """One snapshot from one camera, stored, with every ending named."""
        camera = self._cameras[camera_id]
        try:
            image = camera.snapshot()
        except CameraRefusedUs as exc:
            log.warning("%s refused us: HTTP %s", camera_id, exc.status)
            self._code(CaptureCode.CAMERA_UNREACHABLE, camera_id, HealthState.OK)
            self._code(CaptureCode.CAMERA_REFUSED_US, camera_id, HealthState.ACTIVE, exc.status)
            self._retire_frozen(camera_id)
            return None
        except CameraUnreachable as exc:
            log.warning("%s is unreachable (%s): %s", camera_id, exc.cause.value, exc)
            self._code(
                CaptureCode.CAMERA_UNREACHABLE,
                camera_id,
                HealthState.ACTIVE,
                cause=exc.cause.value,
            )
            self._code(CaptureCode.CAMERA_REFUSED_US, camera_id, HealthState.UNKNOWN)
            self._retire_frozen(camera_id)
            return None
        self._code(CaptureCode.CAMERA_UNREACHABLE, camera_id, HealthState.OK)
        self._code(CaptureCode.CAMERA_REFUSED_US, camera_id, HealthState.OK)

        digest = hashlib.sha256(image).hexdigest()
        previous = self._last_digest.get(camera_id)
        self._last_digest[camera_id] = digest
        self._code(
            CaptureCode.CAMERA_FEED_FROZEN,
            camera_id,
            # `unknown` until there have been two: one snapshot is not two
            # snapshots, and `ok` after the first would be a claim about a
            # comparison nobody made.
            HealthState.UNKNOWN
            if previous is None
            else (HealthState.ACTIVE if previous == digest else HealthState.OK),
        )

        try:
            record = self.store.write(
                image,
                camera_id=camera_id,
                reason=reason.value,
                captured_at=self._now(),
                lane_event_cursor=lane_event_cursor,
                lane_event_at=lane_event_at,
            )
        except StoreOverBudget as exc:
            log.error("%s", exc)
            self._code(CaptureCode.STORE_OVER_BUDGET, STORE, HealthState.ACTIVE)
            return None
        except StoreRecordRefused as exc:
            # The contract refused it BEFORE the disk was touched, so there is
            # nothing to undo. It cannot happen from a page this build accepted
            # -- every reference is judged in `_poll_lane` before a camera is
            # touched -- and it is caught rather than allowed to end the poll,
            # because the two checks agreeing is the point and a crash is not
            # how this process reports one disagreeing.
            log.error("the record was refused by this package's own contract: %s", exc)
            self._code(
                CaptureCode.LANE_CONTRACT_UNSUPPORTED,
                self._lane_subject,
                HealthState.ACTIVE,
            )
            return None
        except StoreUnwritable as exc:
            log.error("%s", exc)
            self._code(CaptureCode.STORE_UNWRITABLE, STORE, HealthState.ACTIVE)
            return None
        self._code(CaptureCode.STORE_OVER_BUDGET, STORE, HealthState.OK)
        self._code(CaptureCode.STORE_UNWRITABLE, STORE, HealthState.OK)
        self._read_clock()
        return record

    def _retire_frozen(self, camera_id: str) -> None:
        """A camera that did not answer has not been compared with anything.

        `unknown`, and the remembered digest is dropped: comparing the next
        snapshot against one from before an outage would answer a question
        nobody asked -- whether the picture changed across the gap -- and publish
        it as whether the feed is frozen NOW.
        """
        self._last_digest.pop(camera_id, None)
        self._code(CaptureCode.CAMERA_FEED_FROZEN, camera_id, HealthState.UNKNOWN)

    def _code(
        self,
        code: CaptureCode,
        subject: str,
        state: HealthState,
        status: int | None = None,
        cause: str | None = None,
    ) -> None:
        self._states[(code.value, subject)] = state.value
        self._status[(code.value, subject)] = status
        self._cause[(code.value, subject)] = cause

    # -- the read surface ---------------------------------------------------

    def describe(self) -> CaptureDescription:
        return CaptureDescription(
            capture_id=self.config.capture_id,
            site_id=self.config.site_id,
            directory=str(self.config.directory),
            interval_seconds=self.config.interval_seconds,
            retention_days=self.config.retention_days,
            max_bytes=self.config.max_bytes,
            max_snapshot_bytes=self.config.max_snapshot_bytes,
            cameras=tuple(
                CameraDescription(
                    camera_id=camera.camera_id,
                    # Scheme, host, port and path, REBUILT -- never the string as
                    # configured, and never a query string. A credential in a
                    # snapshot URL is refused at startup, and this route rebuilds
                    # the address anyway, because one check is a check and two is
                    # a boundary.
                    snapshot_url=published_url(camera.snapshot_url),
                    authenticated=camera.authenticated,
                )
                for camera in self.config.cameras
            ),
            lane_declared=self.config.lane is not None,
            lane_url=(
                published_url(self.config.lane.url) if self.config.lane is not None else None
            ),
        )

    def health(self) -> CaptureHealth:
        """Every capture code for every subject, and what is on the disk.

        **COMPLETE PER (CODE, SUBJECT), not only per code.** Every declared
        camera is here under every camera code on every response, `unknown`
        until its first attempt -- because the camera that has never produced a
        state is the camera worth asking about, and a per-code rule alone drops
        it from this payload the moment any other camera reports. `CaptureHealth`
        refuses a payload that is not complete, so this cannot quietly stop
        being true.

        The four lane codes ship under this process's own id at a site with no
        lane: there is no lane to name, and "no lane is declared" is not the
        same fact as "the lane is answering".
        """
        entries = tuple(
            CaptureEntry(
                code=code,
                subject=subject,
                state=state,
                status=self._status.get((code, subject)),
                cause=self._cause.get((code, subject)),
            )
            for (code, subject), state in sorted(self._states.items())
        )
        return CaptureHealth(
            codes=entries,
            store=StoreReads(**self.store.reads(self._now())),
            camera_ids=tuple(camera.camera_id for camera in self.config.cameras),
            lane_events_missed=self._lane_events_missed,
        )

    def records(self, since: int) -> RecordPage:
        """The page, and THIS ROUTE DOES NOT DIE ON WHAT IT FINDS ON A DISK.

        Every record is built through the contract before it is written, so a
        record this cannot publish is one this process did not write: a sidecar
        edited by hand, or left by another build. It is reported
        (`store_record_incomplete`) and PURGED here rather than raising -- a
        route that raised would answer nothing for every consumer of this store
        until that one record aged out, which is up to `retention_days`.
        """
        published, refused = [], []
        for cursor, record in self.store.records():
            if cursor <= since:
                continue
            try:
                published.append(
                    RecordRef(
                        id=record.id,
                        captured_at=record.captured_at,
                        camera_id=record.camera_id,
                        reason=record.reason,
                        lane_event_cursor=record.lane_event_cursor,
                        lane_event_at=record.lane_event_at,
                        capture_minus_lane_event_ms=record.capture_minus_lane_event_ms,
                        bytes=record.bytes,
                        image_url=f"/v1/capture/images/{record.id}",
                    ).to_dict()
                    | {"cursor": cursor}
                )
            except ValueError as exc:
                log.error("%s cannot be published and was purged: %s", record.id, exc)
                refused.append(record.id)
        if refused:
            self.store.purge_records(refused)
            self._code(CaptureCode.STORE_RECORD_INCOMPLETE, STORE, HealthState.ACTIVE)
        current = self.store.cursor()
        oldest = self.store.oldest_cursor()
        return RecordPage(
            cursor=current,
            reset=since > current or (oldest is not None and since + 1 < oldest),
            dropped=self.store.dropped(),
            records=tuple(published),
        )

    def image(self, record_id: str) -> bytes | None:
        """The bytes of one record, LOOKED UP rather than joined onto a path.

        `None` when this store has no such record, which the route answers 404.
        There is no line anywhere in this package that builds a path out of
        something a request asked for.
        """
        record = self.store.get(record_id)
        if record is None:
            return None
        try:
            return record.image_path.read_bytes()
        except OSError as exc:
            log.error("%s: could not read %s: %s", record_id, record.image_path, exc)
            return None


__all__ = [
    "KNOWN_LANE_VERSIONS",
    "LANE",
    "STORE",
    "TRIGGERS",
    "CaptureProcess",
    "UnsupportedLaneContract",
    "utc_now",
]
