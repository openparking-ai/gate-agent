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
all; a crash between the two leaves an image with no sidecar, which is
REPORTED (`store_record_incomplete`) and purged, never silently kept.

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
    "trigger_to_capture_ms",
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


@dataclass(frozen=True, slots=True)
class Record:
    """One stored capture: the sidecar's seven fields, and where the bytes are."""

    id: str
    captured_at: str
    camera_id: str
    reason: str
    lane_event_cursor: int | None
    lane_event_at: str | None
    trigger_to_capture_ms: int | None
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
        #: What the last index rebuild found that was half a record. Held so the
        #: health route can name it after it has been purged: the fault is that
        #: it HAPPENED, and deleting the evidence must not delete the report.
        self.incomplete: tuple[str, ...] = ()

    def adopt_clock(self, now) -> None:
        """Read time from `now` instead of the wall clock, from here on.

        Called by the capture process so the whole module runs on ONE clock: the
        moment a record is stamped with and the moment its retention window is
        measured against must be the same read, or a box whose clock is wrong
        deletes by one rule and records by another.
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
        for path in sorted(self.directory.iterdir()):
            if not path.is_file() or path.name.startswith(TEMP_PREFIX):
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
        """One record, atomically, after a purge that must make room for it.

        The purge runs FIRST, and it is told how much room this record needs --
        so the rule that decides what is kept is applied before the thing that
        would break it is written, and a store at its cap rolls forward by
        deleting its oldest rather than by refusing its newest.

        `store_over_budget` is what is left when that cannot work: ONE purge has
        emptied everything it is allowed to and this capture still does not fit,
        which means a single capture is larger than the whole cap. That is a
        misconfiguration and not a full disk, and the write is REFUSED and named
        rather than dropped -- a store that quietly discarded what it could not
        fit would be a recording missing exactly the busiest hour.
        """
        self.purge(headroom=len(image))
        if self.bytes_used() + len(image) > self.max_bytes:
            raise StoreOverBudget(
                f"{camera_id}: this capture is {len(image)} bytes and one purge could not get "
                f"{self.directory} under its {self.max_bytes}-byte cap. The capture was not "
                "written. Raise `[capture] max_bytes`, lower `[capture] retention_days`, or "
                "give this store a bigger disk."
            )

        self._sequence += 1
        record_id = f"{_stamp(captured_at)}_{camera_id}_{self._sequence:06d}"
        while (self.directory / f"{record_id}{IMAGE_SUFFIX}").exists():
            self._sequence += 1
            record_id = f"{_stamp(captured_at)}_{camera_id}_{self._sequence:06d}"

        trigger_ms = None
        if lane_event_at is not None:
            trigger_ms = int(
                (captured_at - datetime.fromisoformat(lane_event_at)).total_seconds() * 1000
            )
        record = Record(
            id=record_id,
            captured_at=captured_at.astimezone(UTC).isoformat(),
            camera_id=camera_id,
            reason=reason,
            lane_event_cursor=lane_event_cursor,
            lane_event_at=lane_event_at,
            trigger_to_capture_ms=trigger_ms,
            bytes=len(image),
            image_path=self.directory / f"{record_id}{IMAGE_SUFFIX}",
        )

        image_temp = self.directory / f"{TEMP_PREFIX}{record_id}{IMAGE_SUFFIX}"
        sidecar_temp = self.directory / f"{TEMP_PREFIX}{record_id}{SIDECAR_SUFFIX}"
        try:
            # BOTH temporary files are complete on the disk before either is
            # renamed. A crash anywhere before the first rename leaves two files
            # nothing reads and the index ignores; a crash between the two
            # renames leaves an image with no sidecar, which is the case the
            # rebuild reports and purges. That window is one `rename` wide and
            # it cannot be closed without a filesystem that renames two names at
            # once, so it is named here rather than claimed away.
            _write_atomic_body(image_temp, image)
            _write_atomic_body(
                sidecar_temp,
                json.dumps(record.sidecar(), sort_keys=True).encode("utf-8"),
            )
            os.replace(image_temp, record.image_path)
            os.replace(sidecar_temp, self.directory / f"{record_id}{SIDECAR_SUFFIX}")
        except OSError as exc:
            for path in (image_temp, sidecar_temp):
                _remove(path)
            raise StoreUnwritable(f"{self.directory}: {exc}") from exc

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
        while self._records and self.bytes_used() + headroom > self.max_bytes:
            _cursor, record = self._records.pop(0)
            self._delete(record)
            by_size += 1

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
        return self._records[0][0] if self._records else None

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

        oldest = records[0].captured_at if records else None
        newest = records[-1].captured_at if records else None
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
        }


# ---------------------------------------------------------------------------
# The disk, and nothing above it
# ---------------------------------------------------------------------------


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
    return Record(
        id=record_id,
        captured_at=str(body["captured_at"]),
        camera_id=str(body["camera_id"]),
        reason=str(body["reason"]),
        lane_event_cursor=body["lane_event_cursor"],
        lane_event_at=body["lane_event_at"],
        trigger_to_capture_ms=body["trigger_to_capture_ms"],
        # The size on the DISK, not the number the sidecar remembers. They agree
        # unless something truncated the image, and where they disagree the disk
        # is the one the cap has to be applied against.
        bytes=size,
        image_path=image,
    )


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
    "StoreUnwritable",
]
