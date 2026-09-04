"""The ticket: what the agent mints, what the QR carries, and what it proves.

**A TICKET IS A CLAIM ON ONE ARRIVAL, AND IT IS NOT AN IDENTITY.** Whoever
photographed the code is holding it. That is exactly as strong as holding the
paper ticket it replaces, and no stronger -- a ticket machine hands a strip of
paper to whoever is standing there, and this hands a picture to whoever is
pointing a phone. Nothing here binds a PERSON to a ticket, and the contract says
so in those words rather than leaving a reader to assume otherwise.

What binds the ticket to an arrival is the PRESS: the ticket is offered on a
lane's own decision, and it is only vended when somebody at that barrier presses
the button within the window. That is the answer to "who holds it", and it is a
different answer from the one a signature gives.

**WHAT THE SIGNATURE IS FOR.** It says this site issued this ticket. The key is
per-site, the agent alone holds it, and the lane never sees it -- `lane-controller`
checks a ticket's SHAPE and says so in its own contract: *"this lane holds no key
and mints no ticket"*. So a forged ticket does not open a barrier because the
lane trusts it; it does not open one because the agent that would command the
vend is the only thing that mints one, and it will not command a vend for a
ticket it did not issue. The signature is what lets the EXIT -- a later round, in
another process, possibly with no connectivity at all -- decide the same thing.

**THE QR IS SELF-CONTAINED, and that is Gokhan's requirement (SETTLED 7).** A
phone photograph of the display holds everything the exit needs: the reference,
the site, the lane, when it was issued, and the signature over all of them. No
connectivity on either side, at either end.

**WHAT IS NOT IN IT.** No plate -- this package never reads one. No `ticket_id`:
that is the agent's own opaque handle, it is the vend's `Idempotency-Key` and it
is on every event, and putting it in the QR would publish the key that makes a
vend idempotent to whoever photographs a screen. And no personal data of any
kind, because there is none here to put in.

## The serialisation, exactly

The canonical form is UTF-8, five fields, separated by `\\n` (0x0a), in this
order and no other:

    OPT1
    <ticket_ref>
    <site>
    <lane>
    <issued_at>

A field holding a `\\n` is refused rather than escaped: an escape is a second
rule to get wrong, and no site's own name for itself or for a lane has a newline
in it. **The refusal is at STARTUP as well as at the mint** -- `check_field` is
one function and the configuration is put through it for the `site_id`, every
lane name and every intercom URI -- because a site that only found out at the
mint started, published a healthy surface, and refused its first ticket at three
in the morning.

The signature is `HMAC-SHA256(key, canonical)`, 32 bytes, appended to the
canonical bytes. The QR payload is `base32(canonical || signature)`, RFC 4648,
**unpadded**, uppercase.

**Why base32 and not the fields themselves.** QR's ALPHANUMERIC mode holds 45
characters -- `0-9 A-Z $ % * + - . / : ` and space -- and it is about half the
size of byte mode for the same content. A site's `site_id` and a lane's name are
the site's own strings, and `site-1` (the shipped example) is already outside
that set because of its lowercase letters. The alternative was to refuse a site
its own naming at startup, which is a product decision made to suit an encoder.
Base32's alphabet is `A-Z` and `2-7`, entirely inside QR's alphanumeric set, so
any site_id and any lane name encode, and the whole payload stays in the cheap
mode. **The human-readable half is the `ticket_ref` printed under the code**,
which is what a person reads out over an intercom.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

log = logging.getLogger(__name__)

#: The alphabet a `ticket_ref` is drawn from: `A-Z` without `I` and `O`, and
#: `2-9`. **Confusable-free**, because this is the one part of a ticket a person
#: reads aloud over an intercom or types at an exit: `I`/`1`/`l` and `O`/`0` are
#: the pairs that cost a customer a second call, and `Z`/`2` and `S`/`5` are
#: kept because dropping every arguable pair leaves an alphabet too small to be
#: worth the length.
#:
#: 32 characters, so each one is exactly five bits and the arithmetic below is
#: not a rounding of anything.
TICKET_REF_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"

#: How many characters a `ticket_ref` has. Eight from a 32-character alphabet is
#: 40 bits, which is the number to argue about rather than the length -- and
#: what it has to be enough for is stated rather than left implied: a ticket is
#: pending for `[tickets] confirm_window_s` (default 90 seconds) at ONE lane,
#: and the agent will not vend one it did not itself mint, so guessing has to
#: hit a live reference inside that window at that lane. It is NOT a secret that
#: has to survive being collected: it is on a screen, it is on a phone, and the
#: exit that will read it is a later round.
TICKET_REF_LENGTH = 8

#: The `ticket_id`: the agent's own opaque handle for one ticket. It is the
#: vend's `Idempotency-Key` and it is on every event, so it has to satisfy the
#: LANE's shape for one -- 6 to 64 of `A-Z0-9-` -- which uppercase hex does.
#: 16 bytes, because a repeated one is a vend the lane would answer from its
#: idempotency store instead of performing.
TICKET_ID_BYTES = 16

#: The format tag, first line of the canonical form. It is here so the EXIT --
#: another process, a later round -- can refuse a format it does not know rather
#: than parse one it has guessed at.
TICKET_FORMAT = "OPT1"

#: The separator, and the one character a field may not contain.
FIELD_SEPARATOR = "\n"

#: What a ticket field may hold: **any UTF-8 except the separator**. It is that
#: wide on purpose -- a `site_id` and a lane name are the site's own strings and
#: this package does not get to narrow somebody's name for their own garage --
#: and it is checked in ONE function so the mint and the startup refusal cannot
#: come to disagree about it.
FIELD_ALPHABET = "any UTF-8 character except a newline (0x0a)"


#: The shortest signing key this build will start on. **A FLOOR THIS REPOSITORY
#: CHOSE, NOT A MEASUREMENT**, and it is the same shape of claim as the dial
#: secret's: what makes a key unguessable is that it was generated at RANDOM,
#: which nothing here can see from a file's contents. What this refuses is the
#: case that needs no measurement -- a key short enough to have been typed.
#: 32 characters, which is HMAC-SHA256's own block-filling length.
MINIMUM_SIGNING_KEY = 32

#: The published default for `[tickets] retention_days`. **The platform's own
#: default for the stay this ticket identifies**, so a site that has both does
#: not hold one for a month here and a week there. It is a SETTING, per site,
#: because how long a garage may keep a record of an arrival is a decision
#: somebody makes about their jurisdiction and not a constant this package gets
#: to choose.
DEFAULT_RETENTION_DAYS = 30

#: The published default for `[tickets] confirm_window_s`: how long a ticket
#: stays pending before it is voided unvended.
#:
#: **AN ASSUMPTION, and stated as one: nothing has measured how long a driver
#: takes to photograph a code and press a button.** Ninety seconds is drawn from
#: reading a screen, finding a phone, taking a picture and reaching for a
#: button, which is a guess about people rather than a measurement of them.
#: What is not a guess is which way the error falls: past it there is no ticket
#: and the press goes to a person, which is what every case in round 5 already
#: got.
DEFAULT_CONFIRM_WINDOW_S = 90.0

#: The published default for `[tickets] help_window_s`: how long after a
#: confirmation a second press from the same intercom is HELP rather than
#: anything else.
#:
#: **AN ASSUMPTION TOO.** Sixty seconds is drawn from how long a driver waits
#: before deciding a barrier is not going to move. And there is a second
#: assumption underneath it that is NOT MEASURED and is in the January list:
#: this rests on the intercom placing a SECOND CALL, and whether a given door
#: station will place one while the first is still up is a property of that
#: device. The agent hangs up promptly after the `ticket.*` lines so the common
#: case is two separate calls.
DEFAULT_HELP_WINDOW_S = 60.0

#: What a `ticket_ref` must look like coming back IN. The same shape the lane
#: publishes for one (`is_ticket_ref`: 6-64 of `A-Z0-9-`) is WIDER than what
#: this mints, and this is deliberately the narrow one: what we mint is what we
#: verify, and accepting the lane's whole shape here would mean verifying a
#: reference this agent could never have issued.
_REF = re.compile(f"^[{TICKET_REF_ALPHABET}]{{{TICKET_REF_LENGTH}}}$")

_TICKET_ID = re.compile(f"^[0-9A-F]{{{TICKET_ID_BYTES * 2}}}$")


class BadTicket(Exception):
    """A ticket this build will not accept, and the reason is never the value.

    The reason a refusal does not quote what it refused: a `ticket_ref` is an
    OPAQUE IDENTIFIER of one stay and is personal data while that stay exists,
    and a refusal message is the one place a value reaches a log without
    anybody deciding to put it there. The lane's own contract makes the same
    choice in the same words.
    """
def check_field(value: str, what: str) -> None:
    """Refuse a value a ticket field cannot hold, NAMING the field.

    Called at the mint, where it has always been, and now also at STARTUP for
    every configured value that ends up in one -- the `site_id`, every lane
    name, every intercom URI. The mint-time check alone meant a site named with
    a newline started, published a healthy surface, and refused its first
    ticket at three in the morning with a traceback in a log.
    """
    if FIELD_SEPARATOR in value:
        raise BadTicket(
            f"{what} contains the separator. A ticket's canonical form is "
            "newline-separated and a field is never escaped: an escape is a second rule "
            f"to get wrong. A field holds {FIELD_ALPHABET}."
        )


@dataclass(frozen=True, slots=True)
class Ticket:
    """One minted ticket. Everything in it is on the QR except `ticket_id`."""

    #: What a person reads and says out loud. On no event and in no log line.
    ticket_ref: str
    #: The agent's own handle. The vend's `Idempotency-Key`, and on every event.
    ticket_id: str
    site: str
    #: The lane this ticket is for, or -- STANDALONE -- the intercom's URI. A
    #: ticket has to say WHERE it was issued and a standalone site has no lane,
    #: so the door is the place. The exit reads one field either way.
    lane: str
    #: ISO 8601 with an explicit UTC offset. The round-4 rule: a naive moment is
    #: a guess about which machine it came from.
    issued_at: str

    def canonical(self) -> bytes:
        """The exact bytes the signature covers. One definition, both ways."""
        fields = (TICKET_FORMAT, self.ticket_ref, self.site, self.lane, self.issued_at)
        for name, field in zip(
            ("the format tag", "ticket_ref", "site", "lane", "issued_at"),
            fields,
            strict=True,
        ):
            check_field(field, f"a ticket's {name}")
        return FIELD_SEPARATOR.join(fields).encode("utf-8")


def mint(site: str, lane: str, issued_at: str, key: bytes) -> tuple[Ticket, str]:
    """One ticket and its QR payload. `secrets`, never `random`.

    `issued_at` is passed in rather than read here for the reason `cases.derive`
    takes a clock: a function that reached for the wall clock itself is one
    whose output cannot be reproduced from its inputs, and a test vector needs
    exactly that.
    """
    ticket = Ticket(
        ticket_ref="".join(
            secrets.choice(TICKET_REF_ALPHABET) for _ in range(TICKET_REF_LENGTH)
        ),
        ticket_id=secrets.token_bytes(TICKET_ID_BYTES).hex().upper(),
        site=site,
        lane=lane,
        issued_at=issued_at,
    )
    return ticket, payload_for(ticket, key)


def payload_for(ticket: Ticket, key: bytes) -> str:
    """The QR string: `base32(canonical || HMAC-SHA256(key, canonical))`."""
    canonical = ticket.canonical()
    signature = hmac.new(key, canonical, hashlib.sha256).digest()
    return base64.b32encode(canonical + signature).decode("ascii").rstrip("=")


def verify(payload: str, key: bytes) -> Ticket:
    """The other direction, and it is HERE, beside `payload_for`.

    Beside it on purpose: a verifier written in another file, or in another
    round, is a second reading of one format, and the two come apart at the
    first field anybody adds. The exit that will call this is a later round; the
    function it will call is this one.

    Raises `BadTicket` on anything that is not a payload this site issued --
    a flipped byte, a truncated string, a format tag from another version, a
    field count that is not five. **Never with the value in the message.**
    """
    if not isinstance(payload, str) or not payload:
        raise BadTicket("the payload is empty")
    # EXACTLY AS MINTED. No `strip()`, no `upper()`: the comparison below makes
    # the textual form canonical, and normalising before it meant the check
    # measured only the spellings that survived the normalisation. Measured on
    # the shipped vector: lower case, mixed case and surrounding whitespace all
    # verified, so one ticket had three more spellings than the one it is
    # supposed to have -- and it is the exit keying on the string it decoded
    # that files a stay twice.
    text = payload
    try:
        raw = base64.b32decode(text + "=" * (-len(text) % 8))
    except (ValueError, TypeError) as exc:
        raise BadTicket("the payload is not base32") from exc
    if len(raw) <= hashlib.sha256().digest_size:
        raise BadTicket("the payload is too short to hold a signature and a ticket")
    # ONE SPELLING PER TICKET, and it is the whole of the check: base32 pads
    # the last group with bits that carry nothing, and `b32decode` does not care
    # what they are -- so several different strings decode to the same bytes and
    # every one of them would verify. Measured on the shipped vector: the last
    # character has three such bits, and flipping it left the ticket valid.
    # Case and whitespace are refused by the same comparison, now that nothing
    # normalises the input before it reaches this line.
    #
    # It is not a forgery -- the decoded bytes are identical, so the signature is
    # over the same ticket -- but it means a ticket has more than one textual
    # form, and the exit that will read one may key on the string it decoded.
    # Two spellings of one ticket is two stays, or one stay that cannot be
    # found. Re-encoding and comparing makes the form canonical.
    if base64.b32encode(raw).decode("ascii").rstrip("=") != text:
        raise BadTicket(
            "the payload is not the canonical encoding of the ticket it holds. It is "
            "compared EXACTLY as minted -- unpadded, upper case, nothing round it -- "
            "because base32 pads its last group with bits that carry nothing, and a "
            "ticket with more than one spelling is one the exit could file twice."
        )
    canonical, signature = raw[:-32], raw[-32:]
    expected = hmac.new(key, canonical, hashlib.sha256).digest()
    # CONSTANT TIME. A `==` here leaks how much of a forged signature was right,
    # one byte at a time, to anybody who can measure this call -- and the exit
    # that will call it is a route somebody drives up to.
    if not hmac.compare_digest(signature, expected):
        raise BadTicket("the signature is not this site's")
    try:
        fields = canonical.decode("utf-8").split(FIELD_SEPARATOR)
    except UnicodeDecodeError as exc:
        raise BadTicket("the ticket is not UTF-8") from exc
    if len(fields) != 5:
        raise BadTicket(f"a ticket has five fields and this has {len(fields)}")
    tag, ticket_ref, site, lane, issued_at = fields
    if tag != TICKET_FORMAT:
        raise BadTicket(
            f"this is ticket format {tag!r} and this build reads {TICKET_FORMAT!r}. "
            "A format tag is refused rather than parsed on a guess."
        )
    if not _REF.match(ticket_ref):
        raise BadTicket("the reference is not the shape this build mints")
    # THE SIGNATURE IS CHECKED BEFORE ANY OF THIS. Every refusal above this line
    # is about a payload that already proved it came from this site's key, so
    # nothing here is a parser exposed to an unauthenticated string.
    return Ticket(
        ticket_ref=ticket_ref,
        # NOT IN THE PAYLOAD, and it cannot be: it is the agent's own handle and
        # the vend's idempotency key. A verifier answers what the QR says.
        ticket_id="",
        site=site,
        lane=lane,
        issued_at=issued_at,
    )


# ---------------------------------------------------------------------------
# THE RECORD
# ---------------------------------------------------------------------------

#: What happened to a ticket, in the order it can happen. `issued` is every
#: ticket; the other three are terminal and a ticket reaches exactly one.
ISSUED = "issued"
CONFIRMED = "confirmed"
VENDED = "vended"
VOIDED = "voided"

#: Why a ticket was voided. CLOSED, and every one of them is a reason a site can
#: act on. `restarted` is here because it is a DESIGN DECISION rather than a
#: failure: no pending ticket survives a restart, so the ticket a display is
#: still showing after one can never be vended.
VOID_REASONS: tuple[str, ...] = (
    "window_elapsed",
    "presence_lost",
    #: A NEW DECISION, OR A RESET CURSOR, and nothing else. It used to be
    #: written for every outcome that was not a vend -- a lane that refused, a
    #: lane that could not be reached, an act token the lane would not accept --
    #: so the record asserted a cause that had not happened in six of the seven
    #: ways a press can end. Each of those has its own reason below.
    "lane_decided_again",
    "restarted",
    "display_unavailable",
    #: The LANE considered the completion and said no, with its own code in
    #: `lane_answer`. A 409 from the vend route.
    "lane_refused",
    #: The lane did not answer the vend at all, or answered a 5xx. There is no
    #: `lane_answer`, because there was no answer.
    "lane_unreachable",
    #: The lane would not consider the completion: a 401, a 403 or a 404 on the
    #: vend route. A different fact and a different machine from a refusal.
    "act_refused",
    #: A STANDALONE relay that did not pulse. The record is the only thing that
    #: says what happened at a site with no lane, so a pulse that failed has to
    #: be on it rather than left as an `issued` record nothing resolves.
    "relay_failed",
    #: A record found `confirmed` at startup whose vend could not be settled by
    #: the replay: the lane refused it, could not be reached, or answered
    #: something else. **It is not "the barrier did not open"** -- it is "this
    #: build cannot say either way", which is why it is its own reason and why
    #: `lane_answer` carries whatever the lane did say.
    "outcome_unknown",
)

#: A ticket id is uppercase hex, and a record is named by it. The same shape
#: as `_TICKET_ID` and read from the same constant: a file name that could
#: hold a `/` or a `..` would write a site's records somewhere nobody declared.
_RECORD_ID = _TICKET_ID

TEMP_PREFIX = ".writing-"


@dataclass(frozen=True, slots=True)
class TicketRecord:
    """What the site keeps about one ticket. **The record is the standalone
    site's platform**: a garage with no platform has this and nothing else, so
    it holds the whole life of a ticket rather than a pointer to somewhere.

    The `ticket_ref` IS in here and is in nothing else -- no event, no read
    route, no log line. That is the one place a stay's identifier lives on this
    box, which is what makes the retention rule below mean something.
    """

    ticket_id: str
    ticket_ref: str
    site: str
    lane: str
    issued_at: str
    state: str = ISSUED
    confirmed_at: str | None = None
    vended_at: str | None = None
    voided_at: str | None = None
    void_reason: str | None = None
    #: What the LANE answered, verbatim from its own contract: the refusal
    #: `code` on a 409, or the `completion_id` it minted on a 202. Not
    #: re-derived and not translated -- a lane that is not ours has its own
    #: vocabulary and this is where it survives.
    lane_answer: str | None = None
    #: WHEN THE DRIVER WAS TOLD this code is on the screen, and `None` while
    #: they have not been. A press confirms only a ticket with this set: a
    #: ticket minted behind a driver who is already on the phone is one they
    #: never saw and never photographed, and vending it hands them a stay whose
    #: only identity is a reference nobody holds.
    told_at: str | None = None
    #: The LANE'S OWN `decision.at` this ticket was minted against, kept because
    #: the vend echoes it -- and because a restart has to be able to replay a
    #: `confirmed` ticket's vend with the same idempotency key AND the same
    #: decision. `None` for a standalone ticket, which has no lane and no
    #: decision behind it.
    decision_at: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


class TicketStore:
    """One JSON file per ticket, in a declared directory, purged by DELETION.

    The same shape as the capture store and for the same reason (SETTLED 3g): a
    record of an arrival is personal data in most places this installs, so it
    has a retention rule and the rule DELETES. There is no foreign key here and
    no money record hanging off it -- the platform has those, where there is a
    platform -- so a retention that nulled fields and kept the row would not be
    a retention rule at all.

    **Written atomically**, to a temporary name in the same directory and then
    renamed, so a crash leaves either the previous version or the new one and
    never half a record.

    **One process per directory**, stated rather than locked, exactly as the
    capture store states it: a lock is a second mechanism and this package has
    no use for one yet.
    """

    def __init__(self, directory: Path, retention_days: int = DEFAULT_RETENTION_DAYS) -> None:
        self.directory = Path(directory)
        self.retention_days = retention_days

    def open(self) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        self._sweep_temporary()
        self.purge()

    def _sweep_temporary(self) -> None:
        """Whatever a crash left half-written. Removed, and counted in the log.

        Only a crash can leave one: a live write removes its own temporary file
        in a `finally`.
        """
        left = [one for one in self.directory.glob(f"{TEMP_PREFIX}*") if one.is_file()]
        for one in left:
            one.unlink(missing_ok=True)
        if left:
            log.warning("removed %d half-written ticket record(s) left by a crash", len(left))

    def write(self, record: TicketRecord) -> None:
        if not _RECORD_ID.match(record.ticket_id):
            raise ValueError("a ticket id is uppercase hex and is not a path")
        path = self.directory / f"{record.ticket_id}.json"
        temporary = self.directory / f"{TEMP_PREFIX}{record.ticket_id}.json"
        try:
            temporary.write_text(
                json.dumps(record.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    def read(self, ticket_id: str) -> TicketRecord | None:
        path = self.directory / f"{ticket_id}.json"
        try:
            body = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        try:
            return TicketRecord(**body)
        except TypeError:
            # A record from another version of this package. Not readable here,
            # and NOT deleted: the purge is what deletes, on the rule a site
            # declared, and a reader that swept up what it could not parse would
            # be a retention rule nobody configured.
            log.warning("a ticket record in %s is not one this build can read", self.directory)
            return None

    def purge(self, now: datetime | None = None) -> int:
        """Delete every record older than the retention rule. Returns how many.

        The AGE is the record's `issued_at`, because that is when the personal
        data was created; a ticket voided a month later is not a fresh one.
        """
        moment = now or datetime.now(UTC)
        cutoff = moment - timedelta(days=self.retention_days)
        removed = 0
        for path in sorted(self.directory.glob("*.json")):
            try:
                body = json.loads(path.read_text(encoding="utf-8"))
                issued = datetime.fromisoformat(body["issued_at"])
            except (OSError, ValueError, KeyError, TypeError):
                # A file this build cannot read the age of is LEFT. Deleting it
                # would be a purge acting on an age it did not measure, and the
                # sweep above already removes what a crash leaves.
                log.warning(
                    "%s: a ticket record with no readable issued_at", path.name
                )
                continue
            if issued.tzinfo is None:
                log.warning("%s: a ticket record whose issued_at has no offset", path.name)
                continue
            if issued < cutoff:
                path.unlink(missing_ok=True)
                removed += 1
        if removed:
            log.info("purged %d ticket record(s) past %d days", removed, self.retention_days)
        return removed

    def all_ids(self) -> tuple[str, ...]:
        return tuple(
            sorted(
                path.stem
                for path in self.directory.glob("*.json")
                if _RECORD_ID.match(path.stem)
            )
        )


def told(record: TicketRecord, at: str) -> TicketRecord:
    """The moment the driver was told the code is on the screen.

    Written at the issue where nobody is on the phone -- the screen is where a
    driver looks -- and when the sentence saying so has FINISHED where somebody
    is. It is not a state: a ticket is `issued` either way, and this is the
    field the press is checked against.
    """
    return replace(record, told_at=at)


def confirmed(record: TicketRecord, at: str) -> TicketRecord:
    return replace(record, state=CONFIRMED, confirmed_at=at)


def vended(record: TicketRecord, at: str, completion_id: str | None) -> TicketRecord:
    return replace(record, state=VENDED, vended_at=at, lane_answer=completion_id)


def voided(record: TicketRecord, at: str, reason: str, answer: str | None = None) -> TicketRecord:
    if reason not in VOID_REASONS:
        raise ValueError(f"{reason!r} is not a void reason in this contract")
    return replace(
        record,
        state=VOIDED,
        voided_at=at,
        void_reason=reason,
        lane_answer=answer if answer is not None else record.lane_answer,
    )


def is_ticket_id(value) -> bool:
    return isinstance(value, str) and bool(_TICKET_ID.match(value))


__all__ = [
    "CONFIRMED",
    "FIELD_ALPHABET",
    "DEFAULT_CONFIRM_WINDOW_S",
    "DEFAULT_HELP_WINDOW_S",
    "DEFAULT_RETENTION_DAYS",
    "FIELD_SEPARATOR",
    "ISSUED",
    "MINIMUM_SIGNING_KEY",
    "TICKET_FORMAT",
    "TICKET_ID_BYTES",
    "TICKET_REF_ALPHABET",
    "TICKET_REF_LENGTH",
    "VENDED",
    "VOIDED",
    "VOID_REASONS",
    "BadTicket",
    "Ticket",
    "TicketRecord",
    "TicketStore",
    "check_field",
    "confirmed",
    "told",
    "is_ticket_id",
    "mint",
    "payload_for",
    "verify",
    "vended",
    "voided",
]
