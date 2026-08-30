"""The store: files on a disk, a retention rule, and a purge that deletes.

This is the first durable thing in this package, and every rule below exists
because of what is in it. SETTLED 3g, on the capture mode: *"somewhere to put
images and a RETENTION RULE, since stored plates and photographs are personal
data in most places this installs -- a decision, not a detail."*

**One record is two files.** The JPEG exactly as the camera sent it, never
re-encoded -- so the size measured is the camera's and not this package's -- and
a sidecar of seven fields saying when it was taken, by which camera, why, and
which lane event it answers. **No plate, no plate region, no vehicle attribute
and no event detail goes in either.** The join to who the car was is the lane
event cursor held in the sidecar and the platform's durable record, one place
each; putting a plate here would make this directory a second copy of an
identity, on a box in a gate housing, outside every retention mechanism that
already exists for one.

**Written atomically.** Both files are written to temporary names in the same
directory and then renamed. A crash before the first rename leaves no record at
all AND NO IMAGE: a live write removes its own temporary files in a `finally`,
and any that survive -- which only a crash can leave -- are removed and COUNTED
(`purged_by_crash`) by the next index rebuild. A crash between the two renames
leaves an image with no sidecar, which is REPORTED
(`store_record_incomplete`) and purged, never silently kept.

**A record is built THROUGH the contract before the disk is touched.** A record
this package could write but could not then publish is a store whose own read
route raises on it, for as long as the retention window keeps it. The class the
read route builds its page from is the class the write is validated by, so there
is no second opinion to drift.

**The index is rebuilt by reading the directory, every start.** A check, never a
memory: there is no manifest to go stale, no counter to be wrong, and a file
somebody deleted by hand is simply not in the index. It is also how the two
kinds of incomplete record are found.

**The purge DELETES, and that is different from what the platform does.** The
platform's retention nulls a vehicle's attributes and keeps the row, because the
row is a foreign key and a money record hangs off it. Here there is no foreign
key and no money record: **the image IS the datum**, and a retention rule that
kept it would not be one.

**One process per directory.** Two capture processes sharing one store would
each rebuild an index the other is writing into, and the size cap would be
enforced twice against one disk. Stated rather than locked: a lock is a second
mechanism and this package has no use for one yet.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .contract import RecordRef

log = logging.getLogger(__name__)

#: The temporary name a record is written under before it is renamed into
#: place. Prefixed with a dot and a fixed word so the index rebuild can tell a
#: half-written record from a real one without guessing, and so a crash leaves
#: something a human can recognise rather than a file that looks like a record.
TEMP_PREFIX = ".writing-"

IMAGE_SUFFIX = ".jpg"
SIDECAR_SUFFIX = ".json"

#: A record's name is a timestamp, a camera id and a sequence number, and it is
#: nothing else. Not a plate, not a lane, not a reason: a directory listing is
#: readable by anyone who can read the directory, and a filename is the one part
#: of a file that survives being copied somewhere with no context.
RECORD_ID = re.compile(r"^(\d{8}T\d{9})Z_([A-Za-z0-9_-]{1,64})_(\d{6})$")

#: How long a store must have been recording before a per-day projection from it
#: is a projection. Under this, `projected_bytes_per_day` is `null`: multiplying
#: four minutes by three hundred and sixty is a number that looks measured.
MIN_PROJECTION_SECONDS = 3600.0

#: The sidecar's fields, in the order they are written. Named here once so the
#: reader and the writer cannot come to disagree, and so a field added to a
#: record is added in one place.
SIDECAR_FIELDS = (
    "captured_at",
    "camera_id",
    "reason",
    "lane_event_cursor",
    "lane_event_at",
    "capture_minus_lane_event_ms",
    "bytes",
)


class StoreUnwritable(Exception):
    """The directory will not take a write, and this is said at startup.

    Raised by `open_store` before anything is captured. A capture process that
    started on a directory it cannot write to would photograph a lane for a
    fortnight and keep nothing, while every other signal it publishes said it
    was working.
    """


class StoreOverBudget(Exception):
    """A write was refused: one purge could not get under `max_bytes`.

    Raised rather than swallowed, so the caller reports it as a code. A store
    that quietly dropped what it could not fit would be a recording that is
    missing exactly the busiest hour, with nothing anywhere saying so.
    """


class StoreRecordRefused(Exception):
    """The contract will not publish this record, so it is not filed.

    Raised BEFORE anything touches the disk. A record this package could write
    but could not then serve is a store that answers its own read route with an
    exception -- for as long as the record is kept, which is the retention
    window. The check is the contract class itself: the record is BUILT through
    it, so there is no second opinion about what is publishable.
    """


@dataclass(frozen=True, slots=True)
class Record:
    """One stored capture: the sidecar's seven fields, and where the bytes are."""

    id: str
    captured_at: str
    camera_id: str
    reason: str
    lane_event_cursor: int | None
    lane_event_at: str | None
    capture_minus_lane_event_ms: int | None
    bytes: int
    image_path: Path

    def sidecar(self) -> dict:
        return {name: getattr(self, name) for name in SIDECAR_FIELDS}


def _stamp(moment: datetime) -> str:
    """A UTC instant as a filename may carry it: no colons, no offset sign."""
    return moment.astimezone(UTC).strftime("%Y%m%dT%H%M%S%f")[:-3] + "Z"


class CaptureStore:
    """Files under one directory, an index rebuilt from them, and a purge.

    Everything this object answers is computed from the index, and the index is
    computed from the directory. There is no second copy of what is on the disk.
    """

    def __init__(
        self, directory: Path, retention_days: int, max_bytes: int, now=None
    ) -> None:
        self.directory = directory
        self.retention_days = retention_days
        self.max_bytes = max_bytes
        #: ONE clock. The retention window, the projection and the timestamp on
        #: a record are all read from it, so a record cannot be stamped by one
        #: clock and deleted by another -- which is a retention window that is
        #: not the one anybody configured, on exactly the box whose clock is
        #: wrong. The capture process passes its own.
        self._now = now or (lambda: datetime.now(UTC))
        #: Cursor order. Rebuilt from the directory on start, in capture order,
        #: numbered from one -- so the cursor is NOT durable across a restart and
        #: this contract says so, exactly as the lane's does.
        self._records: list[tuple[int, Record]] = []
        self._cursor = 0
        self._sequence = 0
        self.purged_by_age = 0
        self.purged_by_size = 0
        #: How many temporary files an index rebuild has removed, since start.
        #: A temp file at a rebuild is BY DEFINITION a write that died: nothing
        #: else can leave one, because a live write removes its own in a
        #: `finally`. Counted rather than swept, because how often a site loses
        #: power mid-write is a fact about that site.
        self.purged_by_crash = 0
        #: Whether the newest record held is stamped AFTER the clock that reads
        #: it. Measured on every purge, and true for as long as it holds.
        self.clock_stepped_back = False
        #: What the last index rebuild found that was half a record. Held so the
        #: health route can name it after it has been purged: the fault is that
        #: it HAPPENED, and deleting the evidence must not delete the report.
        self.incomplete: tuple[str, ...] = ()

    def adopt_clock(self, now) -> None:
        """Read time from `now` instead of the wall clock, from here on.

        Called by the capture process so the whole module runs on ONE clock: the
        moment a record is stamped with and the moment its retention window is
        measured against are the same read.

        **ONE CLOCK IS NOT A MONOTONIC ONE, and this used to claim otherwise.**
        A single wall clock that steps -- NTP correcting a box with no RTC
        battery, which is the environment this package names for itself -- still
        stamps one record after another with an earlier moment, and still holds
        records stamped ahead of it that no age rule can reach. That is
        `clock_stepped_back`, measured on every purge and published, because it
        cannot be fixed here.
        """
        self._now = now

    # -- opening -----------------------------------------------------------

    def open(self) -> None:
        """Prove the directory takes a write, then rebuild the index from it.

        The directory is NOT created. A path that does not exist is a typo or a
        disk that did not mount, and creating it means writing captures into the
        root filesystem of a device whose store never came up -- which fills the
        box the lane is running on. Refused, named, at startup.
        """
        if not self.directory.is_dir():
            raise StoreUnwritable(
                f"{self.directory} is not a directory. It is not created here on purpose: a path "
                "that is not there is a typo or a disk that did not mount, and creating it would "
                "put a site's captures on the root filesystem of the box the lane runs on."
            )
        self.probe()
        self.rebuild()

    def probe(self) -> None:
        """Write a byte and delete it. The only honest test of `store_unwritable`.

        `os.access` asks the permission bits, which answer for the wrong thing on
        a read-only mount, a full disk and a directory owned by another user with
        an ACL. This asks the disk.
        """
        probe = self.directory / f"{TEMP_PREFIX}probe"
        try:
            probe.write_bytes(b"\0")
        except OSError as exc:
            raise StoreUnwritable(f"{self.directory} will not take a write: {exc}") from exc
        finally:
            try:
                probe.unlink()
            except OSError:
                pass

    def rebuild(self) -> None:
        """The index, read off the disk. A check, never a memory.

        Pairs every image with its sidecar. An image with no sidecar and a
        sidecar with no image are both INCOMPLETE: they are named, reported, and
        purged. A half record kept is a photograph nobody can say anything about
        -- not when it was taken, not by which camera -- sitting in a directory
        under a retention rule that cannot reach it, because the rule reads the
        sidecar.
        """
        images, sidecars, incomplete = {}, {}, []
        crashed = 0
        for path in sorted(self.directory.iterdir()):
            if not path.is_file():
                continue
            if path.name.startswith(TEMP_PREFIX):
                # A TEMPORARY FILE AT A REBUILD IS A WRITE THAT DIED. Nothing
                # else leaves one: a live write removes its own in a `finally`,
                # and this process is the only writer of this directory. It
                # holds image bytes, it is outside the index, outside
                # `bytes_used` and outside the retention rule -- which reads a
                # sidecar there is none of. It is removed and counted here, so
                # the rule that says a crash "leaves no record at all" is true
                # of the IMAGE and not only of the record.
                _remove(path)
                crashed += 1
                continue
            if path.suffix == IMAGE_SUFFIX and RECORD_ID.match(path.stem):
                images[path.stem] = path
            elif path.suffix == SIDECAR_SUFFIX and RECORD_ID.match(path.stem):
                sidecars[path.stem] = path

        records = []
        for record_id in sorted(set(images) | set(sidecars)):
            image = images.get(record_id)
            sidecar = sidecars.get(record_id)
            record = None
            if image is not None and sidecar is not None:
                record = _read_record(record_id, image, sidecar)
            if record is None:
                incomplete.append(record_id)
                for path in (image, sidecar):
                    if path is not None:
                        _remove(path)
                continue
            records.append(record)

        records.sort(key=lambda one: (one.captured_at, one.id))
        self._cursor = 0
        self._records = []
        for record in records:
            self._cursor += 1
            self._records.append((self._cursor, record))
        self._sequence = max(
            (int(RECORD_ID.match(record.id).group(3)) for record in records), default=0
        )
        self.incomplete = tuple(incomplete)
        self.purged_by_crash += crashed
        if crashed:
            log.error(
                "%d temporary file(s) from a write that did not finish found in %s and removed",
                crashed,
                self.directory,
            )
        if incomplete:
            log.error(
                "%d incomplete record(s) found in %s and purged: %s",
                len(incomplete),
                self.directory,
                ", ".join(incomplete),
            )

    # -- writing -----------------------------------------------------------

    def write(
        self,
        image: bytes,
        camera_id: str,
        reason: str,
        captured_at: datetime,
        lane_event_cursor: int | None = None,
        lane_event_at: str | None = None,
    ) -> Record:
        """One record, BUILT THROUGH THE CONTRACT, then written atomically.

        The order here is the whole rule, and it is three steps:

        **1. CAN THIS CAPTURE EVER FIT?** `len(image)` against `max_bytes`,
        BEFORE any purge. A capture larger than the whole cap can never be
        stored however much is deleted for it, so deleting anything for it
        destroys a site's store to make room for a write that is then refused --
        and how long a camera's answer is, is the camera's to choose. Refused
        first, and nothing is purged.

        **2. IS THIS A RECORD THIS PROCESS CAN PUBLISH?** The record is built
        through `contract.RecordRef` before anything touches the disk. A record
        the contract would refuse is a record that is not filed, and the poll
        that produced it is refused -- rather than a file on a disk that makes
        `GET /v1/capture/records` raise for as long as the retention window.

        **3. THEN the purge makes room**, told how much this record needs, so a
        store at its cap rolls forward by deleting its oldest rather than by
        refusing its newest.

        `store_over_budget` is what step 1 raises. It is a misconfiguration and
        not a full disk, and the write is REFUSED and named rather than dropped
        -- a store that quietly discarded what it could not fit would be a
        recording missing exactly the busiest hour.
        """
        if len(image) > self.max_bytes:
            raise StoreOverBudget(
                f"{camera_id}: this capture is {len(image)} bytes and {self.directory}'s whole "
                f"cap is {self.max_bytes} bytes, so no purge can make room for it. NOTHING WAS "
                "PURGED and the capture was not written. Raise `[capture] max_bytes`, or lower "
                "`[capture] max_snapshot_bytes` so this camera cannot answer with more than "
                "this store can hold."
            )

        self._sequence += 1
        record_id = f"{_stamp(captured_at)}_{camera_id}_{self._sequence:06d}"
        while (self.directory / f"{record_id}{IMAGE_SUFFIX}").exists():
            self._sequence += 1
            record_id = f"{_stamp(captured_at)}_{camera_id}_{self._sequence:06d}"

        difference_ms = None
        if lane_event_at is not None:
            try:
                lane_moment = datetime.fromisoformat(lane_event_at)
            except ValueError as exc:
                raise StoreRecordRefused(
                    f"{camera_id}: lane_event_at={lane_event_at!r} is not a timestamp this "
                    "process can subtract from its own"
                ) from exc
            if lane_moment.tzinfo is None:
                raise StoreRecordRefused(
                    f"{camera_id}: lane_event_at={lane_event_at!r} carries no UTC offset, so "
                    "it is not a moment this process can subtract from its own"
                )
            difference_ms = int((captured_at - lane_moment).total_seconds() * 1000)
        record = Record(
            id=record_id,
            captured_at=captured_at.astimezone(UTC).isoformat(),
            camera_id=camera_id,
            reason=reason,
            lane_event_cursor=lane_event_cursor,
            lane_event_at=lane_event_at,
            capture_minus_lane_event_ms=difference_ms,
            bytes=len(image),
            image_path=self.directory / f"{record_id}{IMAGE_SUFFIX}",
        )
        # THROUGH THE CONTRACT, before the disk. `RecordRef` is the class the
        # records route builds its page from, so this is the same judgement that
        # route will make later -- not a second one that could come to differ.
        _refuse_unpublishable(record)

        self.purge(headroom=len(image))
        if self.bytes_used() + len(image) > self.max_bytes:
            raise StoreOverBudget(
                f"{camera_id}: this capture is {len(image)} bytes and one purge could not get "
                f"{self.directory} under its {self.max_bytes}-byte cap. The capture was not "
                "written."
            )

        image_temp = self.directory / f"{TEMP_PREFIX}{record_id}{IMAGE_SUFFIX}"
        sidecar_temp = self.directory / f"{TEMP_PREFIX}{record_id}{SIDECAR_SUFFIX}"
        try:
            # BOTH temporary files are complete on the disk before either is
            # renamed. A crash anywhere before the first rename leaves two files
            # the index removes and COUNTS at the next start; a crash between
            # the two renames leaves an image with no sidecar, which is the case
            # the rebuild reports and purges. That window is one `rename` wide
            # and it cannot be closed without a filesystem that renames two
            # names at once, so it is named here rather than claimed away.
            _write_atomic_body(image_temp, image)
            _write_atomic_body(
                sidecar_temp,
                json.dumps(record.sidecar(), sort_keys=True).encode("utf-8"),
            )
            os.replace(image_temp, record.image_path)
            os.replace(sidecar_temp, self.directory / f"{record_id}{SIDECAR_SUFFIX}")
        except OSError as exc:
            raise StoreUnwritable(f"{self.directory}: {exc}") from exc
        finally:
            # ALWAYS, and not only on an `OSError`. A live write cleans up after
            # itself whatever ended it -- an exception from anywhere inside this
            # block, an interrupt, a `SystemExit` -- because a temporary file
            # this process leaves behind while it is still running is one the
            # index will report as a crash at the next start, and one holding
            # image bytes nothing counts in the meantime. After the two renames
            # these two names no longer exist, so this removes nothing.
            for path in (image_temp, sidecar_temp):
                _remove(path)

        self._cursor += 1
        self._records.append((self._cursor, record))
        # And again, plainly, after the write. The retention window is a rule
        # about age and this is where it is applied to a store that has just
        # grown; the size half has nothing left to do, because the purge above
        # already made room for exactly these bytes.
        self.purge()
        return record

    # -- the retention rule ------------------------------------------------

    def purge(self, now: datetime | None = None, headroom: int = 0) -> tuple[int, int]:
        """Age first, then size. Returns what each half removed, this call.

        The order is the rule: a record older than the retention window goes
        because it is old, whatever the disk has room for, and the cap is then
        applied to what is left. Reversing them would let a large recent day
        evict a record the retention rule was still keeping deliberately, which
        is a retention window nobody can state.

        `headroom` is how many bytes are about to be written. The size half
        makes room for them, so a store sitting exactly at its cap keeps
        recording by deleting its oldest -- which is what a retention rule with
        a size cap in it is FOR.

        **THE SIZE HALF IS BOUNDED BY WHAT THIS CAPTURE NEEDS, and it can never
        be `while self._records`.** Headroom that does not fit under the cap
        makes that condition unsatisfiable, so the loop empties the store and
        the write is refused anyway: one impossible capture, and a site's whole
        recording is gone. Asked for, it does nothing and says so.

        **OLDEST IS BY VALUE, not by position.** The index is in insertion
        order and a clock that steps back inserts an earlier record after a
        later one, so "oldest first" read off the front of the list is whatever
        happened to be written first.
        """
        moment = now or self._now()
        cutoff = moment - timedelta(days=self.retention_days)
        by_age = 0
        kept = []
        for cursor, record in self._records:
            if _at(record.captured_at) < cutoff:
                self._delete(record)
                by_age += 1
            else:
                kept.append((cursor, record))
        self._records = kept

        by_size = 0
        if headroom > self.max_bytes:
            log.error(
                "%s: asked to make room for %d bytes under a %d-byte cap; nothing was purged, "
                "because no amount of deleting makes room for a capture larger than the cap",
                self.directory,
                headroom,
                self.max_bytes,
            )
        else:
            while self._records and self.bytes_used() + headroom > self.max_bytes:
                oldest = min(self._records, key=lambda one: (_at(one[1].captured_at), one[0]))
                self._records.remove(oldest)
                self._delete(oldest[1])
                by_size += 1

        # MEASURED HERE, on every purge: the newest record this store holds
        # against the clock that is about to decide what is old. A record ahead
        # of the clock is not deleted early and it is not ignored -- the age
        # rule simply cannot reach it, and this is how that is said.
        self.clock_stepped_back = bool(
            self._records
            and max(_at(record.captured_at) for _cursor, record in self._records) > moment
        )

        self.purged_by_age += by_age
        self.purged_by_size += by_size
        if by_age or by_size:
            log.info(
                "purged %d record(s) older than %d day(s) and %d for size from %s",
                by_age,
                self.retention_days,
                by_size,
                self.directory,
            )
        return by_age, by_size

    def purge_records(self, record_ids) -> int:
        """Delete named records and drop them from the index. Returns how many.

        The read route's answer to a record it cannot publish. It is the same
        deletion the incomplete-record path makes at a rebuild, for the same
        reason: a record nothing can say anything about, sitting under a
        retention rule, is what this store exists not to have. It counts as
        `store_record_incomplete`, which is what it is.
        """
        wanted = set(record_ids)
        gone = [pair for pair in self._records if pair[1].id in wanted]
        for pair in gone:
            self._records.remove(pair)
            self._delete(pair[1])
        if gone:
            self.incomplete = tuple(
                sorted({*self.incomplete, *(record.id for _cursor, record in gone)})
            )
            log.error(
                "%d record(s) in %s could not be published and were purged: %s",
                len(gone),
                self.directory,
                ", ".join(record.id for _cursor, record in gone),
            )
        return len(gone)

    def _delete(self, record: Record) -> None:
        _remove(record.image_path)
        _remove(self.directory / f"{record.id}{SIDECAR_SUFFIX}")

    # -- the reads ---------------------------------------------------------

    def bytes_used(self) -> int:
        return sum(record.bytes for _cursor, record in self._records)

    def records(self) -> tuple[tuple[int, Record], ...]:
        return tuple(self._records)

    def cursor(self) -> int:
        return self._cursor

    def oldest_cursor(self) -> int | None:
        """The LOWEST cursor still held, by value.

        `self._records[0]` is the front of a list in insertion order, and the
        size purge now deletes by the value of `captured_at` -- so the front of
        the list is not necessarily the lowest cursor any more.
        """
        return min((cursor for cursor, _record in self._records), default=None)

    def dropped(self) -> int:
        return self.purged_by_age + self.purged_by_size

    def get(self, record_id: str) -> Record | None:
        """One record BY ID, out of the index -- never by joining a path.

        The id from a request is looked up here and the path comes from the
        record. A route that built `directory / f"{id}.jpg"` would serve
        `../../etc/anything` to whoever asked for it, and this is why there is
        no such line anywhere in this package.
        """
        for _cursor, record in self._records:
            if record.id == record_id:
                return record
        return None

    def reads(self, now: datetime | None = None) -> dict:
        """What is on this disk, measured from the index, when asked.

        Every figure here is a read of one site's directory. Nothing in this
        package has seen a capture from any camera it is written for, so nothing
        here is compared against an expected size, a rate or a capacity: there
        is no such number to compare against and inventing one is what this
        round exists not to do.
        """
        moment = now or self._now()
        records = [record for _cursor, record in self._records]
        total = sum(record.bytes for record in records)
        day_ago = moment - timedelta(hours=24)
        recent = [record for record in records if _at(record.captured_at) >= day_ago]
        recent_bytes = sum(record.bytes for record in recent)

        # BY VALUE. `records[0]` and `records[-1]` are the ends of a list in
        # insertion order: after a clock steps back, the last record written is
        # the earliest one held, and the two fields published here read as
        # `newest_at` before `oldest_at`. That is not a store that is broken,
        # it is a read that was taken by position.
        oldest = min((record.captured_at for record in records), key=_at, default=None)
        newest = max((record.captured_at for record in records), key=_at, default=None)
        projected = None
        if oldest is not None:
            span = min((moment - _at(oldest)).total_seconds(), 24 * 3600.0)
            if span >= MIN_PROJECTION_SECONDS:
                projected = int(recent_bytes * (24 * 3600.0) / span)
        return {
            "bytes_used": total,
            "record_count": len(records),
            "oldest_at": oldest,
            "newest_at": newest,
            "mean_bytes_per_record": total // len(records) if records else None,
            "records_last_24h": len(recent),
            "bytes_last_24h": recent_bytes,
            "projected_bytes_per_day": projected,
            "purged_by_age": self.purged_by_age,
            "purged_by_size": self.purged_by_size,
            "purged_by_crash": self.purged_by_crash,
        }


# ---------------------------------------------------------------------------
# The disk, and nothing above it
# ---------------------------------------------------------------------------


def _refuse_unpublishable(record: Record) -> None:
    """Build the record through the contract class, and let it judge.

    `contract.RecordRef` is what `GET /v1/capture/records` builds its page from.
    Constructing one here means the judgement made before the disk is written is
    THE SAME judgement, made by the same code, that the read route will make
    later -- not a second opinion that can come to differ from it.
    """
    try:
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
        )
    except ValueError as exc:
        raise StoreRecordRefused(f"{record.camera_id}: {exc}") from exc


def _write_atomic_body(path: Path, body: bytes) -> None:
    """Write and FLUSH TO THE DISK before the caller renames it into place.

    Without the `fsync`, the rename can reach the disk before the bytes do, and
    a machine that loses power leaves a record with a name, a sidecar and an
    empty or truncated image -- which is worse than the crash this design is
    built for, because it looks complete.
    """
    with open(path, "wb") as handle:
        handle.write(body)
        handle.flush()
        os.fsync(handle.fileno())


def _read_record(record_id: str, image: Path, sidecar: Path) -> Record | None:
    """One record off the disk, or `None` if the pair is not a record.

    A sidecar that will not parse, or that is missing a field, is not a record
    that can be interpreted generously: it is half of one, and it goes through
    the same path as an image with no sidecar at all.
    """
    try:
        body = json.loads(sidecar.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(body, dict) or any(name not in body for name in SIDECAR_FIELDS):
        return None
    try:
        size = image.stat().st_size
    except OSError:
        return None
    record = Record(
        id=record_id,
        captured_at=str(body["captured_at"]),
        camera_id=str(body["camera_id"]),
        reason=str(body["reason"]),
        lane_event_cursor=body["lane_event_cursor"],
        lane_event_at=body["lane_event_at"],
        capture_minus_lane_event_ms=body["capture_minus_lane_event_ms"],
        # The size on the DISK, not the number the sidecar remembers. They agree
        # unless something truncated the image, and where they disagree the disk
        # is the one the cap has to be applied against.
        bytes=size,
        image_path=image,
    )
    try:
        # AND THROUGH THE CONTRACT, on the way in. A sidecar this process would
        # not write -- edited by hand, written by an older build, corrupted in a
        # way that still parses as JSON -- is half a record: it goes down the
        # same path as an image with no sidecar at all, reported as
        # `store_record_incomplete` and purged, rather than being kept until the
        # read route trips over it.
        _refuse_unpublishable(record)
    except StoreRecordRefused:
        return None
    return record


def _remove(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        pass


def _at(stamp: str) -> datetime:
    """A record's timestamp. Unparseable reads as the beginning of time.

    Which means the purge takes it: a record whose sidecar carries a timestamp
    nothing can read is a record no retention rule can honour, and keeping it
    would put a photograph outside the only mechanism that deletes one.
    """
    try:
        moment = datetime.fromisoformat(stamp)
    except ValueError:
        return datetime.min.replace(tzinfo=UTC)
    if moment.tzinfo is None:
        return datetime.min.replace(tzinfo=UTC)
    return moment


__all__ = [
    "IMAGE_SUFFIX",
    "MIN_PROJECTION_SECONDS",
    "RECORD_ID",
    "SIDECAR_FIELDS",
    "SIDECAR_SUFFIX",
    "TEMP_PREFIX",
    "CaptureStore",
    "Record",
    "StoreOverBudget",
    "StoreRecordRefused",
    "StoreUnwritable",
]
