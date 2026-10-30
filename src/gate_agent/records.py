"""The durable record: what the monitor SENT, and what the agent's calls WERE.

Gokhan, 2026-10-30, on a car let out without paying: *"record it as exceptions
and add as much information as we can. what happened, who did what, gate agent
sent messages times, who received, etc"*, and *"gate agent records sending
messages for hardware, network failures"*. The platform builds the exception;
this is the half it reads from here -- every message this package sent and every
call it handled, for a lane, in a time window, after a restart as well as before
one.

The two event windows are not that. `GET /v1/monitor/events` and
`GET /v1/agent/events` are catch-up buffers, bounded, in memory, and gone at a
restart -- their own contract says so, and says the durable account is "whatever
the sinks delivered it to". A sink is an inbox somebody else owns. So this is a
store, on this box's disk, that a consumer reads through the contract.

**One JSON file per record, written atomically** -- a temporary name in the same
directory, `fsync`, then renamed into place, then the directory synced -- so a
crash leaves the previous version or the new one and never half a record. The
same shape as the ticket store and the capture store, plus the two syncs: a
record of a message that the power cut took back is a message nobody can show
was sent.

**The index is rebuilt by reading the directory** at every start. A memory of
what was on the disk is not a check of it.

**A record changes after it is written** -- a delivery is retried, a call is
answered, a person keys something -- and every write gives it a new REVISION
from one counter that never goes backwards, persisted beside the records. That
is the cursor a consumer holds: `since=N` serves every record whose latest
revision is after `N`, so a record that changed comes back with its change, and
the cursor means the same thing after a restart as before it.

**Bounded, and the bound says so.** Two rules, both per-site settings with a
published default. Age: a record older than `retention_days` is DELETED. Size:
past `max_bytes` the OLDEST records are dropped first, and every drop is counted
and dated on the health route, because a gap nobody knows about is worse than
one that is counted. Neither rule ever leaves a consumer believing it has seen
everything: a page whose `since` is behind a record that was removed before it
was served says `reset`.

**These files hold personal data** -- an email address a site configured, a
call-back number a person keyed -- so the directory is created `0700` and every
file `0600`, and the directory is DECLARED, never defaulted, for the reason the
capture store's is: a default would put it somewhere nobody chose.

**One process per directory**, stated rather than locked, as the capture and
ticket stores state it. Inside the process the store IS locked: the poller writes
and the read surface's threads read.
"""

from __future__ import annotations

import bisect
import json
import logging
import os
import re
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qsl

log = logging.getLogger(__name__)

#: How long a record is kept, in days. Gokhan's number, 2026-10-30: an exception
#: about a car let out unpaid is looked at when the money is missed, and a card
#: dispute can arrive months after the fact. A PER-SITE SETTING, `[records]
#: retention_days`, inside the same bounds every retention in this package uses.
DEFAULT_RECORD_RETENTION_DAYS = 400

#: The most this store may hold on disk, in bytes. A PER-SITE SETTING AND AN
#: ASSUMPTION. What IS known here is the size of a record, because this package
#: writes it: under a kilobyte with one sink and about two with three, read off
#: the records the test suite writes. What is NOT known is how many a site
#: produces -- a quiet lane sends a handful of messages a day and a flapping one
#: two every poll -- so this is drawn to hold a few hundred thousand records,
#: which is years of an ordinary site and weeks of a broken one, on a box whose
#: disk is measured in tens of gigabytes. Past it the oldest go first and the
#: health route counts them.
DEFAULT_RECORD_MAX_BYTES = 256 * 1024 * 1024

#: The bounds `[records] max_bytes` must fall inside. A mebibyte is a few hundred
#: records, below which the size rule would be the retention rule; sixteen
#: gibibytes is past anything a box in a gate housing should give to text.
RECORD_MAX_BYTES_BOUNDS = (1 << 20, 1 << 34)

#: How many records one page serves when the consumer does not say, and the
#: most it may ask for. Bounded because a page is read off the disk while the
#: store is locked, and the poller that writes the next record waits for it.
DEFAULT_PAGE_LIMIT = 100
MAX_PAGE_LIMIT = 500

#: The largest cursor a consumer may send: the largest signed 64-bit integer,
#: the bound the event routes already apply.
MAX_CURSOR = 2**63 - 1

#: The query parameters the record routes accept, and nothing else. A parameter
#: spelt wrong is REFUSED rather than ignored: `?lane_id=entry` ignored would
#: serve every lane's records to a consumer who believes it filtered them.
QUERY_KEYS = ("since", "lane", "from", "to", "limit")

#: The longest a query string, a lane and a timestamp may be. A consumer has no
#: use for more, and a parser handed more is doing work for whoever sent it.
MAX_QUERY_LENGTH = 1024
MAX_LANE_LENGTH = 128
MAX_TIME_LENGTH = 64

TEMP_PREFIX = ".tmp-"
HEAD_FILE = "head.json"

#: What a record id is: a one-letter kind, a dash, and 32 hex digits from
#: `os.urandom`. It is a FILENAME, so it is checked against this before it is
#: ever joined onto the directory, and no id is ever taken from a request.
RECORD_ID = re.compile(r"^[mc]-[0-9a-f]{32}$")

#: An email address, by SHAPE, for the one job of keeping one out of a log line.
#: Deliberately broad: a false positive costs a word in a log, and a miss
#: publishes somebody's address to whatever collects this process's output.
ADDRESS = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)*\.[A-Za-z]{2,}")


def redact_addresses(text: str) -> str:
    """`text` with every email-shaped substring replaced by `<address>`.

    A sink's failure reason is built from whatever the SMTP library or the
    server said, and both quote recipients back: `{'oncall@example.com': (550,
    ...)}` is what `SMTPRecipientsRefused` prints. That reason is logged, so it
    is reduced here, once, before anything can log it.
    """
    return ADDRESS.sub("<address>", text)


def new_record_id(kind: str) -> str:
    if kind not in ("m", "c"):
        raise ValueError(f"no record kind {kind!r}")
    return f"{kind}-{os.urandom(16).hex()}"


class RecordUnwritable(Exception):
    """The record directory would not take a write.

    Raised from `open()` at startup, where the process refuses to start -- a
    monitor that paged people for a fortnight and kept no record of it would be
    the exact gap this exists to close -- and from `put()` while running, where
    the caller reports it as a code and carries on: the message still goes out,
    because a record store that could stop a page is worse than no record.
    """


class BadQuery(ValueError):
    """A query string the record routes refuse, with why. Answered `400`."""


@dataclass(frozen=True, slots=True)
class RecordQuery:
    since: int = 0
    lane: str | None = None
    start: datetime | None = None
    end: datetime | None = None
    limit: int = DEFAULT_PAGE_LIMIT

    def echo(self) -> dict:
        """The filter as the page publishes it, so a consumer can see what it got."""
        return {
            "since": self.since,
            "lane": self.lane,
            "from": self.start.isoformat() if self.start else None,
            "to": self.end.isoformat() if self.end else None,
            "limit": self.limit,
        }


def parse_query(query: str) -> RecordQuery:
    """`?since=&lane=&from=&to=&limit=`, every one of them BOUNDED, or `BadQuery`.

    The cursor is an INTEGER and nothing else -- digits, at most nineteen of
    them, at most `MAX_CURSOR`. It is compared with a revision and never joined
    onto a path, so there is nothing in it to traverse with; and it is refused
    on shape anyway, because a cursor that accepted `../` today would be one
    somebody makes into a filename tomorrow.
    """
    if len(query) > MAX_QUERY_LENGTH:
        raise BadQuery(f"the query string is longer than {MAX_QUERY_LENGTH} characters")
    try:
        pairs = parse_qsl(
            query, keep_blank_values=True, strict_parsing=bool(query),
            max_num_fields=len(QUERY_KEYS),
        )
    except ValueError as exc:
        raise BadQuery(f"the query string could not be read: {exc}") from exc
    seen: dict[str, str] = {}
    for key, value in pairs:
        if key not in QUERY_KEYS:
            raise BadQuery(
                f"{key!r} is not a parameter of this route; it takes {', '.join(QUERY_KEYS)}. "
                "Ignored, it would serve records the consumer believes it filtered out."
            )
        if key in seen:
            raise BadQuery(f"{key!r} is given twice")
        seen[key] = value
    since = _whole(seen.get("since"), "since", 0, MAX_CURSOR, 0)
    limit = _whole(seen.get("limit"), "limit", 1, MAX_PAGE_LIMIT, DEFAULT_PAGE_LIMIT)
    lane = seen.get("lane")
    if lane is not None and (
        not lane or len(lane) > MAX_LANE_LENGTH or not lane.isprintable()
    ):
        raise BadQuery(
            f"lane must be 1-{MAX_LANE_LENGTH} printable characters"
        )
    start = _moment(seen.get("from"), "from")
    end = _moment(seen.get("to"), "to")
    if start is not None and end is not None and not start < end:
        raise BadQuery("from must be before to")
    return RecordQuery(since=since, lane=lane, start=start, end=end, limit=limit)


def _whole(raw: str | None, name: str, low: int, high: int, default: int) -> int:
    if raw is None:
        return default
    if not re.fullmatch(r"[0-9]{1,19}", raw):
        raise BadQuery(f"{name} must be a whole number, got {raw[:32]!r}")
    value = int(raw)
    if not low <= value <= high:
        raise BadQuery(f"{name} must be between {low} and {high}, got {value}")
    return value


def _moment(raw: str | None, name: str) -> datetime | None:
    """An ISO 8601 time WITH an offset. A naive one is refused, not assumed UTC.

    The rule every timestamp in this contract follows: assuming is how two
    machines in two timezones come to disagree about when something happened,
    and here the something is when a car left without paying.
    """
    if raw is None:
        return None
    if not raw or len(raw) > MAX_TIME_LENGTH:
        raise BadQuery(f"{name} must be an ISO 8601 time with a UTC offset")
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise BadQuery(f"{name} is not ISO 8601: {raw!r}") from exc
    if parsed.tzinfo is None:
        raise BadQuery(f"{name} has no UTC offset: {raw!r}")
    return parsed


@dataclass(slots=True)
class _Entry:
    revision: int
    moment: datetime
    lane: str | None
    size: int
    unfinished: bool


def _utc_now() -> datetime:
    return datetime.now(UTC)


class RecordStore:
    """One directory of one kind of record, its index, and its two bounds.

    `time_key` and `lane_key` name the two fields of a record this store files
    it by -- WHEN it is about and WHICH LANE -- and `unfinished` says whether a
    record was still in progress when it was written, so a restart can find the
    ones the last run left open without reading every file twice.
    """

    def __init__(
        self,
        directory: Path,
        *,
        time_key: str,
        lane_key: str,
        unfinished: Callable[[dict], bool],
        retention_days: int = DEFAULT_RECORD_RETENTION_DAYS,
        max_bytes: int = DEFAULT_RECORD_MAX_BYTES,
        now: Callable[[], datetime] = _utc_now,
    ) -> None:
        self.directory = Path(directory)
        self.retention_days = retention_days
        self.max_bytes = max_bytes
        self._time_key = time_key
        self._lane_key = lane_key
        self._unfinished = unfinished
        self._now = now
        self._lock = threading.RLock()
        #: record id -> entry, IN REVISION ORDER. A write pops and re-inserts,
        #: and every write takes the highest revision, so iteration order is
        #: revision order without a sort.
        self._index: dict[str, _Entry] = {}
        #: (moment, id), sorted, for "oldest first" by the VALUE of the time and
        #: not by the order things were written in.
        self._by_age: list[tuple[datetime, str]] = []
        self._bytes = 0
        self._revision = 0
        #: The highest revision of any record REMOVED -- for age or for space.
        #: A consumer whose `since` is below it may have missed that record, and
        #: is told `reset` rather than served a page that looks complete.
        self._removed_through = 0
        self._purged_for_age = 0
        self._dropped_for_space = 0
        self._last_dropped_at: str | None = None
        #: The time of the NEWEST record dropped for space: before this moment
        #: the store is no longer complete, and that is what a consumer needs.
        self._dropped_through: str | None = None
        self._unreadable = 0
        self._writable: bool | None = None
        self._opened = False

    # -- opening ------------------------------------------------------------

    def open(self) -> None:
        """Make the directory, sweep a crash's leftovers, rebuild, and purge."""
        with self._lock:
            try:
                self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
                # PROBED, not assumed: a directory that exists and refuses a
                # write is the case `mkdir` cannot see.
                probe = self.directory / f"{TEMP_PREFIX}probe"
                self._write_bytes(probe, b"{}")
                probe.unlink(missing_ok=True)
            except OSError as exc:
                self._writable = False
                raise RecordUnwritable(
                    f"the record directory {self.directory} will not take a write: {exc}. "
                    "Refusing to start: every message this process sends and every call it "
                    "takes is meant to be on this disk, and a process that ran without it would "
                    "leave no account of either."
                ) from exc
            self._writable = True
            self._sweep_temporary()
            self._rebuild()
            self._opened = True
            self.purge()

    def _sweep_temporary(self) -> None:
        left = [one for one in self.directory.glob(f"{TEMP_PREFIX}*") if one.is_file()]
        for one in left:
            one.unlink(missing_ok=True)
        if left:
            log.warning("removed %d half-written record(s) left by a crash", len(left))

    def _rebuild(self) -> None:
        self._index.clear()
        self._by_age.clear()
        self._bytes = 0
        self._unreadable = 0
        head = self._read_head()
        found: list[tuple[int, str, _Entry]] = []
        for path in self.directory.glob("*.json"):
            if path.name == HEAD_FILE:
                continue
            record_id = path.stem
            body = self._read_path(path) if RECORD_ID.match(record_id) else None
            entry = self._entry_of(body, path) if body is not None else None
            if entry is None:
                # LEFT, and counted. A file this build cannot read the age of is
                # not deleted on a guess: the age rule is what deletes, and it
                # cannot measure this one.
                self._unreadable += 1
                log.warning("%s: a record this build cannot read; it is left", path.name)
                continue
            found.append((entry.revision, record_id, entry))
        for _revision, record_id, entry in sorted(found):
            self._index[record_id] = entry
            bisect.insort(self._by_age, (entry.moment, record_id))
            self._bytes += entry.size
        newest = max((entry.revision for entry in self._index.values()), default=0)
        self._revision = max(int(head.get("revision", 0)), newest)
        self._removed_through = int(head.get("removed_through", 0))
        self._purged_for_age = int(head.get("purged_for_age", 0))
        self._dropped_for_space = int(head.get("dropped_for_space", 0))
        self._last_dropped_at = head.get("last_dropped_at")
        self._dropped_through = head.get("dropped_through")

    def _entry_of(self, body: dict, path: Path) -> _Entry | None:
        revision = body.get("revision")
        stamp = body.get(self._time_key)
        lane = body.get(self._lane_key)
        if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
            return None
        if not isinstance(stamp, str):
            return None
        try:
            moment = datetime.fromisoformat(stamp)
        except ValueError:
            return None
        if moment.tzinfo is None:
            return None
        try:
            size = path.stat().st_size
        except OSError:
            return None
        return _Entry(
            revision=revision,
            moment=moment,
            lane=lane if isinstance(lane, str) else None,
            size=size,
            unfinished=bool(self._unfinished(body)),
        )

    def _read_head(self) -> dict:
        try:
            body = json.loads((self.directory / HEAD_FILE).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return body if isinstance(body, dict) else {}

    # -- writing ------------------------------------------------------------

    def put(self, record_id: str, body: dict) -> int:
        """Write `body` as the newest revision of `record_id`. Returns the revision.

        Raises `RecordUnwritable` if the disk refuses; the store's index is left
        exactly as it was, so a write that failed is not served as one that
        happened.
        """
        if not RECORD_ID.match(record_id):
            raise ValueError("a record id is a kind, a dash and 32 hex digits, and is not a path")
        with self._lock:
            if not self._opened:
                self.open()
            revision = self._revision + 1
            stored = {**body, "revision": revision}
            data = (json.dumps(stored, sort_keys=True, separators=(",", ":")) + "\n").encode()
            entry = self._entry_of_body(stored, len(data))
            try:
                self._write_bytes(self.directory / f"{record_id}.json", data)
                self._revision = revision
                self._write_head()
            except OSError as exc:
                self._writable = False
                raise RecordUnwritable(f"a record could not be written: {exc}") from exc
            self._writable = True
            previous = self._index.pop(record_id, None)
            if previous is not None:
                self._bytes -= previous.size
                self._by_age.remove((previous.moment, record_id))
            self._index[record_id] = entry
            bisect.insort(self._by_age, (entry.moment, record_id))
            self._bytes += entry.size
            self._make_room(keep=record_id)
            self.purge()
            return revision

    def _entry_of_body(self, body: dict, size: int) -> _Entry:
        stamp = body.get(self._time_key)
        moment = datetime.fromisoformat(stamp) if isinstance(stamp, str) else None
        if moment is None or moment.tzinfo is None:
            raise ValueError(f"a record's {self._time_key} must be ISO 8601 with an offset")
        lane = body.get(self._lane_key)
        return _Entry(
            revision=body["revision"],
            moment=moment,
            lane=lane if isinstance(lane, str) else None,
            size=size,
            unfinished=bool(self._unfinished(body)),
        )

    def _write_bytes(self, path: Path, data: bytes) -> None:
        """Atomically, `0600`, synced, and the directory synced after the rename."""
        temporary = path.with_name(f"{TEMP_PREFIX}{path.name}")
        try:
            descriptor = os.open(
                temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600
            )
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
            directory = os.open(self.directory, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            temporary.unlink(missing_ok=True)

    def _write_head(self) -> None:
        head = {
            "revision": self._revision,
            "removed_through": self._removed_through,
            "purged_for_age": self._purged_for_age,
            "dropped_for_space": self._dropped_for_space,
            "last_dropped_at": self._last_dropped_at,
            "dropped_through": self._dropped_through,
        }
        self._write_bytes(
            self.directory / HEAD_FILE,
            (json.dumps(head, sort_keys=True) + "\n").encode(),
        )

    # -- the two bounds -----------------------------------------------------

    def purge(self) -> int:
        """Delete every record older than the retention rule. Returns how many.

        Measured on the record's own time -- when the message was sent, when the
        call started -- because that is when the personal data in it was
        created; a delivery retried a minute later is not a fresh one.
        """
        with self._lock:
            cutoff = self._now() - timedelta(days=self.retention_days)
            removed = 0
            while self._by_age and self._by_age[0][0] < cutoff:
                _moment, record_id = self._by_age[0]
                self._remove(record_id)
                self._purged_for_age += 1
                removed += 1
            if removed:
                self._safe_head()
                log.info("purged %d record(s) past %d days", removed, self.retention_days)
            return removed

    def _make_room(self, keep: str) -> None:
        """Past `max_bytes`, the OLDEST go first -- by their time, not by position.

        Never the record just written: a store that dropped what it was asked to
        keep in order to keep it would be a write that succeeded and vanished.
        """
        dropped = 0
        while self._bytes > self.max_bytes and len(self._index) > 1:
            moment, record_id = self._by_age[0]
            if record_id == keep:
                moment, record_id = self._by_age[1]
            self._remove(record_id)
            self._dropped_for_space += 1
            self._last_dropped_at = self._now().isoformat()
            if self._dropped_through is None or moment > datetime.fromisoformat(
                self._dropped_through
            ):
                self._dropped_through = moment.isoformat()
            dropped += 1
        if dropped:
            self._safe_head()
            log.warning(
                "dropped %d record(s), oldest first, to stay under %d bytes",
                dropped, self.max_bytes,
            )

    def _remove(self, record_id: str) -> None:
        entry = self._index.pop(record_id)
        self._by_age.remove((entry.moment, record_id))
        self._bytes -= entry.size
        self._removed_through = max(self._removed_through, entry.revision)
        (self.directory / f"{record_id}.json").unlink(missing_ok=True)

    def _safe_head(self) -> None:
        try:
            self._write_head()
        except OSError as exc:
            self._writable = False
            log.error("the record head could not be written: %s", exc)

    # -- reading ------------------------------------------------------------

    def get(self, record_id: str) -> dict | None:
        if not RECORD_ID.match(record_id):
            return None
        with self._lock:
            if record_id not in self._index:
                return None
            return self._read_path(self.directory / f"{record_id}.json")

    def unfinished(self) -> tuple[str, ...]:
        """The records the LAST run left in progress, oldest revision first."""
        with self._lock:
            return tuple(
                record_id for record_id, entry in self._index.items() if entry.unfinished
            )

    def page(self, query: RecordQuery) -> tuple[list[dict], int, bool, bool, bool]:
        """`(records, cursor, more, reset, gap)` for one query.

        Filtered by revision first, then by lane, then by time, and cut at
        `limit`. `cursor` is what to send as `since` next: the last record's
        revision when there is MORE, and the store's own head when there is not
        -- so a consumer that polls from it is told about every later write.

        **`lane` matches that lane's records AND every record about no lane.**
        A platform that cannot be reached, a sink that cannot deliver, a call
        turned away before anybody knew which door it was: each is a fact about
        every lane at the site, and an exception about one lane is missing it
        otherwise. Every record says its lane, `null` included, so a consumer
        that wants one lane only drops the nulls.

        Two flags, because they are two different facts and a consumer does two
        different things about them. `reset`: `since` is AHEAD of this store's
        head, so it is not the store that cursor came from -- start again from
        0. `gap`: a record written after `since` was REMOVED, for age or for
        space, before it was served -- keep this page, carry on from `cursor`,
        and know the account is not whole; the health route says how much went.
        """
        with self._lock:
            reset = query.since > self._revision
            gap = query.since < self._removed_through
            chosen: list[tuple[str, int]] = []
            more = False
            for record_id, entry in self._index.items():
                if entry.revision <= query.since:
                    continue
                if query.lane is not None and entry.lane not in (query.lane, None):
                    continue
                if query.start is not None and entry.moment < query.start:
                    continue
                if query.end is not None and entry.moment >= query.end:
                    continue
                if len(chosen) == query.limit:
                    more = True
                    break
                chosen.append((record_id, entry.revision))
            records = []
            for record_id, _revision in chosen:
                body = self._read_path(self.directory / f"{record_id}.json")
                if body is not None:
                    records.append(body)
            cursor = chosen[-1][1] if more else self._revision
            return records, cursor, more, reset, gap

    def reads(self) -> dict:
        """What the health route says about this store. Every count, every time."""
        with self._lock:
            oldest = self._by_age[0][0].isoformat() if self._by_age else None
            newest = self._by_age[-1][0].isoformat() if self._by_age else None
            return {
                "held": len(self._index),
                "bytes_used": self._bytes,
                "max_bytes": self.max_bytes,
                "retention_days": self.retention_days,
                "oldest_at": oldest,
                "newest_at": newest,
                "cursor": self._revision,
                "purged_for_age": self._purged_for_age,
                "dropped_for_space": self._dropped_for_space,
                "last_dropped_at": self._last_dropped_at,
                "dropped_through": self._dropped_through,
                "unreadable": self._unreadable,
                "writable": self._writable,
            }

    def _read_path(self, path: Path) -> dict | None:
        try:
            body = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return body if isinstance(body, dict) else None


__all__ = [
    "DEFAULT_PAGE_LIMIT",
    "DEFAULT_RECORD_MAX_BYTES",
    "DEFAULT_RECORD_RETENTION_DAYS",
    "MAX_PAGE_LIMIT",
    "QUERY_KEYS",
    "RECORD_MAX_BYTES_BOUNDS",
    "BadQuery",
    "RecordQuery",
    "RecordStore",
    "RecordUnwritable",
    "new_record_id",
    "parse_query",
    "redact_addresses",
]
