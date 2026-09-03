"""The gate agent: it answers the intercom, says what happened, and calls a human.

Gokhan's spec, his words: *the first line of communication is our automated gate
agent; a human only when the agent cannot handle, confirm or find the problem*,
and when the human is reached *"we RECEIVE THEIR AUTHORISATION -- open the gate,
don't open, I'll be there in a minute"*.

**THE LANE DECIDES. THIS AGENT ASSERTS.** From round 7 an authorisation is an
act where a site declared one: `OPEN_NOW` and `OPEN_AND_FLAG` reach
`POST /v1/lane/vend` at a lane this agent holds an act token for, and the LANE
applies its own refusals -- presence off its arming loop at the moment of the
call, its own malfunction table, its own arming geometry, the age and identity
of its own last decision. Nothing here checks any of them first. Where a site
declared no act token and no relay, the same code answers, speaks the case,
calls a person and asks for nothing, which is round 5 exactly and is supported.
What this process can ask for is `AgentConfig.act_surface` and it is printed at
every start; `docs/CONTRACT.md`, "IT CAN NOW COMMAND A VEND", is the whole of it.

**WHICH INTERCOM A CALL IS FROM IS THE ADDRESS IT DIALLED, NEVER WHO IT SAYS IT
IS.** Each declared intercom has an account of its own on the user agent, whose
user part is a secret only that intercom's installer knows, and a call is that
intercom if and only if it arrived AT that account. The caller's `From` is
recorded as `caller_stated_identity` -- a claim, so labelled -- and nothing is
decided by it. A caller who can write any `From` it likes reaches nothing,
because the secret never travels in a header: it is the number dialled.

**The case is derived, never asked.** `cases.derive()` is a pure function of what
the lane published. The driver is not offered a menu of problems, because a
driver at a barrier does not know which of them happened.

**The dialogue is a clock and a state, not a thread.** `poll()` drains whatever
the user agent has said and advances whatever is due. Audio finishes on a
DURATION read out of the file at startup rather than on an event, because a
"playback finished" event that does not arrive would leave a driver in silence
with nothing timing out -- and a duration is a property of a file this package
ships and can measure.

**One case at a time, and it is published as a limit.** The user agent's bridge
is site-wide: a second case bridged while the first is open would put two
strangers and two operators into one conversation. So a call arriving during a
case is REFUSED WITHOUT BEING ANSWERED -- **and the live case is checked before
the caller's identity is**, because being undeclared is the default state of
every caller on a network, not a rare one. Measured from the caller's side, the
refusal is `486 Busy Here` after `180 Ringing`.
"""

from __future__ import annotations

import logging
import threading
import wave
from collections import deque
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import Enum, auto
from pathlib import Path
from time import monotonic

from .act import LaneActClient, LaneActRefusedUs, LaneUnreachable
from .cases import LaneReading, decision_case, derive, offers_ticket
from .client import ReadOnlyClient, TargetRefusedUs, TargetUnreachable
from .config import AgentConfig, Intercom
from .contract import (
    ACTS,
    AUTHORISATION_DIGITS,
    CONTRACT_VERSION,
    KNOWN_LANE_VERSIONS,
    OPENING_AUTHORISATIONS,
    AgentCase,
    AgentCode,
    AgentDescription,
    AgentEntry,
    AgentEvent,
    AgentEventKind,
    AgentEventPage,
    AgentHealth,
    Authorisation,
    HealthState,
    IntercomDescription,
    LaneCapability,
    UserAgentDescription,
)
from .display import DisplayUnavailable, frame_for
from .lines import DISPLAY_TEXT, UNKNOWN_REFUSAL, audio_name
from .relay import RelayRefusedUs, RelayUnreachable
from .relay import build as relay_build
from .tickets import CONFIRMED, ISSUED, TicketStore, confirmed, mint, vended, voided
from .tickets import TicketRecord as _TicketRecord
from .tickets import told as _told
from .ua import (
    UaEvent,
    UaEventKind,
    UaLeg,
    UaMisconfigured,
    UaRefused,
    UaUnreachable,
)

log = logging.getLogger(__name__)


#: How many times a digit outside the enabled set is re-prompted before the
#: attempt is `nothing usable`. Two, and it is fixed rather than per-site: it is
#: a property of how a person uses a keypad, not of a garage.
REPROMPTS = 2

#: The digit that ends a keyed number.
END_OF_NUMBER = "#"

#: The sample rate of every file this agent plays. What a narrowband SIP call
#: carries, and what `scripts/build_audio.py` writes.
NARROWBAND_RATE = 8000


class State(Enum):
    """Where one case has got to. One case at a time, so this is the agent's."""

    IDLE = auto()
    #: Speaking the case to the driver, in every declared language.
    SPEAKING_CASE = auto()
    #: The operator's phone is ringing. `no_answer_seconds` is running.
    CALLING_HUMAN = auto()
    #: Telling the operator where the call is from and what the case is, and
    #: reading them the menu. PRIVATE: the bridge has not been made yet.
    BRIEFING = auto()
    #: Bridged, and waiting for a digit this site accepts.
    WAITING_DIGIT = auto()
    #: `CALL_BACK` was keyed and a number is being collected.
    COLLECTING_NUMBER = auto()
    #: `HOLD` was keyed. The driver is re-prompted on an interval, because
    #: silence on a door station is indistinguishable from a dead intercom.
    HOLDING = auto()
    #: STANDALONE only: the record is written, the relay is being pulsed on its
    #: own thread, and the operator has not been told the outcome yet. The pulse
    #: is not run inside `poll()` any more -- it held the whole loop for the
    #: length of the request, so for a legal six-second barrier the agent played
    #: nothing, answered nothing and polled no lane while it ran.
    WAITING_RELAY = auto()
    #: Everything has been said; the call ends when the last message finishes.
    CLOSING = auto()


@dataclass(frozen=True, slots=True)
class Pending:
    """A ticket that is on a display and has not been confirmed or voided.

    **One per LANE, never more.** A second ticket at one barrier is a second
    stay for one car, and the lane would refuse the second vend
    `already_completed` -- which is the backstop and not the design.

    It is held in memory and NOWHERE ELSE, and that is deliberate: no pending
    ticket survives a restart. The record on disk says `voided` with reason
    `restarted`, so a ticket a display is still showing after a crash can never
    be vended, and the press that would have confirmed it goes to a person.
    """

    ticket: object
    payload: str
    lane: str
    #: WHICH DOOR'S SCREEN this code is on, and therefore the one door whose
    #: press confirms it. A lane may have two intercoms; the configuration
    #: refuses two DISPLAYS at one lane, so this is single-valued, and a press
    #: at the other door is the round-5 path.
    intercom: str | None
    case: AgentCase
    #: The lane's `decision.at` this ticket was minted against. **ECHOED to the
    #: vend, never invented**: it is what says WHICH decision is being
    #: completed, and a `now()` in its place is a caller telling a lane
    #: something it did not ask.
    decision_at: str
    expires: float
    displays: tuple = ()
    #: WHEN the driver was told this code is on the screen, and `None` while
    #: they have not been. Set at the issue where nobody is on the phone -- the
    #: screen is where a driver looks -- and when `ticket.on_screen` has
    #: FINISHED where somebody is. **A press confirms only a ticket with this
    #: set**: a code minted behind a driver who is already in a call is one they
    #: never saw, and vending it hands them a stay whose only identity is a
    #: reference nobody holds.
    told_at: str | None = None


@dataclass(frozen=True, slots=True)
class Help:
    """The help window open at one intercom, and THE TICKET it belongs to.

    A window keyed on a door and a clock says "somebody at this door was given
    a ticket recently", which is a fact about a door. What the operator is told
    is a fact about the person on the line, and the two came apart the moment a
    second car arrived.
    """

    ticket_id: str
    lane: str
    #: The agent's monotonic clock at the confirmation.
    at: float
    #: What the vend answered, as the operator lines that say it.
    lines: tuple = ()


@dataclass
class Pulse:
    """One relay pulse, running on its own thread.

    `poll()` used to make the request itself, so for the whole of it the agent
    played nothing, answered nothing and followed no lane -- up to the derived
    timeout, which on a legal six-second barrier is eleven seconds of an agent
    that has stopped working. The outcome is collected on a later poll and the
    operator is told then; `[escalation] nothing_usable_seconds` bounds how long
    they wait for it.
    """

    intercom: str
    pending: object
    authorisation: str
    port: int
    pulse_ms: int
    thread: object = None
    #: `None` while it is running, `""` for a pulse the unit answered as the
    #: document says, and the cause otherwise.
    outcome: str | None = None
    #: Which health code the failure is, and `None` on a success.
    code: object = None
    #: The case this call was, held here because the outcome may arrive after
    #: the call it belongs to has ended.
    case: str | None = None
    done: bool = False


@dataclass
class Session:
    """One case, from the moment a call is answered until the call ends."""

    intercom: Intercom
    driver_call: str
    started: float
    case: AgentCase | None = None
    operator_call: str | None = None
    state: State = State.SPEAKING_CASE
    bridged: bool = False
    authorisation: Authorisation | None = None
    keyed: str = ""
    prompts_left: int = REPROMPTS
    deadline: float | None = None
    #: WHICH LANGUAGES this call is being spoken in, and it is per call.
    #:
    #: It starts as the site's declared order -- the driver has no keypad and
    #: nothing has told us otherwise -- and one function narrows it to a single
    #: language for the rest of the call. Gokhan, 2026-08-30: *"if the customer
    #: starts speaking in Spanish, no English, it should start Spanish from
    #: there."* WHAT DETECTS THAT IS NOT HERE: hearing a language is ASR, which
    #: is a later step gated on a measurement of narrowband SIP audio that
    #: nobody has made. What is here is the state it will set, so that step adds
    #: a detector and nothing else.
    languages: tuple = ()
    #: The calls whose MEDIA is up. A file played into a call that has been
    #: answered but whose audio stream does not exist yet is refused by the user
    #: agent, and the driver hears the first sentence of their case not at all.
    #: Answered is not established, and the difference is a whole message.
    live: set = field(default_factory=set)
    #: Whether `case_spoken` has been written for this case. It is written when
    #: the last file of the case has FINISHED, so it is written once and there
    #: has to be somewhere to remember that.
    spoken: bool = False
    #: The ticket this call CONFIRMED, where the press was a confirmation.
    confirmed_ticket: object = None
    #: The ticket this call is TELLING the driver about, where a code went up
    #: while they were on the phone. `told_at` is written when the sentence has
    #: finished, which is the only moment anybody knows they heard it.
    telling: object = None
    #: The relay pulse this call is waiting on, STANDALONE only.
    pulse: object = None
    #: What the operator is told before the case, where this call is a driver
    #: calling back inside the help window: a line saying a ticket was just
    #: confirmed, and the line for whatever the vend answered.
    help_lines: tuple = ()
    #: What is queued to play on each leg, and when the leg is next free.
    speech: dict[UaLeg, deque] = field(default_factory=lambda: {
        UaLeg.DRIVER: deque(), UaLeg.OPERATOR: deque()
    })
    free_at: dict[UaLeg, float] = field(default_factory=lambda: {
        UaLeg.DRIVER: 0.0, UaLeg.OPERATOR: 0.0
    })
    #: WHEN the line at the head of each leg's queue became DUE, or `None` when
    #: nothing is waiting. `[speech] line_timeout_seconds` is measured from
    #: here, and it starts when the line is due rather than when it is first
    #: REFUSED: a leg whose media never comes up is never even attempted, and to
    #: the driver that is the same silence.
    line_due: dict[UaLeg, float | None] = field(default_factory=lambda: {
        UaLeg.DRIVER: None, UaLeg.OPERATOR: None
    })

    def call_of(self, leg: UaLeg) -> str | None:
        return self.driver_call if leg is UaLeg.DRIVER else self.operator_call


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


class AudioMissing(Exception):
    """A line's file is not there, or is not a file this build can play.

    Raised at startup, where it stops the agent registering at all, and again if
    a file goes missing under a running process -- at which point it is a code on
    the health surface rather than a driver hearing nothing.
    """


class Agent:
    """Answers an intercom, speaks the case, and records what a human said."""

    def __init__(
        self,
        config: AgentConfig,
        user_agent,
        clock=monotonic,
        now=utc_now,
        client_factory=ReadOnlyClient,
    ) -> None:
        self.config = config
        self.ua = user_agent
        self._clock = clock
        self._now = now
        self._clients = {
            lane.name: client_factory(lane.url, lane.token, lane.timeout_seconds)
            for lane in config.lanes
        }
        #: THE ONLY CLIENTS IN THIS PACKAGE THAT CAN OPEN A BARRIER, one per
        #: lane a site declared an `act_token_file` for. A lane that is not in
        #: here is READ-ONLY to this agent: `can_vend` is false for it, no
        #: ticket is offered, and a human's `OPEN_NOW` hears
        #: `operator.cannot_open`. Built only where the token exists, so there
        #: is no client to be refused by a lane.
        self._acts = {
            lane.name: LaneActClient(lane.url, lane.act_token, lane.timeout_seconds)
            for lane in config.lanes
            if lane.can_act
        }
        #: The displays at each lane, by lane name. A lane whose intercoms
        #: declare none offers no ticket.
        self._displays_at: dict[str, tuple] = {}
        for intercom in config.intercoms:
            if not intercom.display:
                continue
            # Keyed on the LANE where there is one and on the DOOR where there
            # is not. A standalone site has no lane, and a ticket has to say
            # where it was issued: the door is the place.
            where = intercom.lane or intercom.sip_uri
            self._displays_at.setdefault(where, ())
            self._displays_at[where] += (config.displays[intercom.display],)
        #: One pending ticket per lane, IN MEMORY ONLY -- see `Pending`.
        self._pending: dict[str, Pending] = {}
        #: Where this agent follows each lane's events from, and when it is next
        #: due to. `None` is "not established yet": the first read adopts the
        #: lane's current cursor and mints nothing for what is already in the
        #: window, because those cars have gone.
        self._cursors: dict[str, int | None] = {
            lane.name: None for lane in config.lanes
        }
        self._lane_due: dict[str, float] = {lane.name: 0.0 for lane in config.lanes}
        #: THE HELP WINDOW, per intercom, and it belongs to ONE TICKET.
        #:
        #: It used to be a moment and a set of sentences keyed on the door, with
        #: nothing tying either to a ticket, an arrival or a driver -- so the
        #: NEXT driver at that door, thirty seconds later, with a ticket of
        #: their own on the screen, was briefed to the operator as somebody who
        #: "was given a ticket a moment ago" whose "barrier has been asked to
        #: open". Both sentences were false about the person on the line, their
        #: own ticket was never confirmed, and it expired while they were on the
        #: phone. The operator decides whether to open a barrier on that
        #: briefing.
        #:
        #: So it names the ticket, and a NEW DECISION or a NEW TICKET at that
        #: lane ends it.
        self._help: dict[str, Help] = {}
        #: The relays this agent can pulse, by INTERCOM. Standalone only: an
        #: intercom with a lane has none, and the configuration refuses one.
        self._relays = {
            intercom.sip_uri: relay_build(intercom.relay)
            for intercom in config.intercoms
            if intercom.relay is not None
        }
        #: The relay pulses running on their own threads. Keyed on a COUNTER
        #: and not on the intercom: a second `OPEN_NOW` at the same door while
        #: the first pulse is still in flight would replace the first one, and
        #: the record it was about would never be settled by anything.
        self._pulses: dict[int, Pulse] = {}
        self._pulse_seq = 0
        self._store = (
            TicketStore(config.tickets.directory, config.tickets.retention_days)
            if config.tickets is not None
            else None
        )
        #: Keyed on the account a call ARRIVES AT, which is what identifies the
        #: intercom. Never on the `From`: that is a string the caller writes.
        self._by_account = {
            intercom.account_user: intercom for intercom in config.intercoms
        }
        self.session: Session | None = None
        self._durations: dict[Path, float] = {}
        self._states: dict[tuple[str, str], str] = {}
        self._log: deque[tuple[int, AgentEvent]] = deque(maxlen=config.event_window_depth)
        self._cursor = 0
        self._dropped = 0
        #: What the user agent said it is, read once at startup. Published on
        #: `GET /v1/agent` because the UA is an INSTALL REQUIREMENT and a
        #: consumer is entitled to see which one is running.
        self._ua_version: str | None = None

    # -- startup -----------------------------------------------------------

    def start(self) -> None:
        """Measure every audio file, take the user agent, and CLEAR IT DOWN.

        The files first, because a missing one is a configuration this process
        must not run on and it costs nothing to find out; the UA second, because
        finding out means opening a socket to another process; and then every
        call that user agent is still holding, because they are not ours.
        """
        self._measure_audio()
        self._code(AgentCode.AUDIO_MISSING, self.config.agent_id, HealthState.OK)
        for leg in UaLeg:
            self._code(AgentCode.AUDIO_PLAYBACK_FAILED, leg.value, HealthState.OK)
        if self._store is not None:
            # BEFORE the user agent, because it is the cheaper thing to find out
            # about: a store that cannot be opened is a configuration this
            # process must not run on, and finding out costs a `mkdir`.
            self._store.open()
            self._reconcile()
        self.ua.start()
        self._release_leftover_calls()
        self._check_accounts()
        for code, subject in (
            (AgentCode.DISPLAY_UNAVAILABLE, tuple(self.config.displays)),
            (AgentCode.LANE_ACT_REFUSED, tuple(self._acts)),
        ):
            for one in subject:
                self._code(code, one, HealthState.OK)
        self._ua_version = self.ua.version()
        self._code(AgentCode.UA_UNREACHABLE, self.config.agent_id, HealthState.OK)
        self._code(AgentCode.UA_UNSUPPORTED_VERSION, self.config.agent_id, HealthState.OK)

    def _reconcile(self) -> None:
        """SETTLE every record a previous process left in a non-terminal state.

        **Nothing pending survives a restart** -- that is unchanged and it is
        the design -- but the STORE is not the pending map, and it used to
        survive with nothing ever settling it. Two shapes, both measured:

          * a record left `issued`. The published reason `restarted` existed in
            `VOID_REASONS` and in `docs/CONTRACT.md` and **no code path wrote
            it**, so the ticket a screen is still showing after a crash was a
            live-looking record for ever, and the crash paragraph rested on it.
          * a record left `confirmed`: the agent died between the lane's `202`
            and its own write. The stay exists at the lane; the record says a
            press happened and stops. Nothing before the retention purge said
            whether the barrier opened.

        The second is settled by REPLAYING the vend with the record's own
        `Idempotency-Key` and its own `decision_at` -- round 6's replay
        guarantee, and it is the lane's own store that answers: a key it holds
        gives back the same `202` without anything moving. A key it does not
        hold is a fresh request, and the lane then applies its own refusals to
        it -- its decision, its loop, its age rule -- which is the invariant
        working rather than being worked around. Anything that is not a `202`
        is `outcome_unknown`, which says what this build knows and no more.
        """
        for ticket_id in self._store.all_ids():
            record = self._store.read(ticket_id)
            if record is None or record.state not in (ISSUED, CONFIRMED):
                continue
            if record.state == ISSUED:
                self._store.write(voided(record, self._now(), "restarted"))
                self._record(
                    AgentEventKind.TICKET_VOIDED,
                    lane=record.lane,
                    ticket_id=record.ticket_id,
                    reason="restarted",
                )
                continue
            self._replay_vend(record)

    def _replay_vend(self, record) -> None:
        """One `confirmed` record, settled against the lane that holds the stay."""
        client = self._acts.get(record.lane)
        if client is None or not record.decision_at:
            # NOTHING TO ASK. No act token for that lane any more, or a record
            # from before this field existed. The honest answer is the one that
            # says so.
            return self._unknown_outcome(record, None)
        try:
            answer = client.vend(
                authorised_by="display_code_confirmed",
                ticket_ref=record.ticket_ref,
                decision_at=record.decision_at,
                idempotency_key=record.ticket_id,
            )
        except LaneActRefusedUs as exc:
            log.error("lane %s would not settle a confirmed ticket: HTTP %s",
                      record.lane, exc.status)
            return self._unknown_outcome(record, None)
        except LaneUnreachable as exc:
            log.error("lane %s could not settle a confirmed ticket: %s", record.lane, exc)
            return self._unknown_outcome(record, None)
        if not answer.commanded:
            return self._unknown_outcome(record, answer.code)
        cursor = None if answer.event_cursor is None else str(answer.event_cursor)
        self._store.write(vended(record, self._now(), cursor))
        log.warning("a confirmed ticket left by a previous process settled as vended")
        self._record(
            AgentEventKind.VEND_COMMANDED,
            lane=record.lane,
            ticket_id=record.ticket_id,
            authorised_by="display_code_confirmed",
            lane_event_cursor=answer.event_cursor,
        )

    def _unknown_outcome(self, record, answer: str | None) -> None:
        self._store.write(voided(record, self._now(), "outcome_unknown", answer))
        self._record(
            AgentEventKind.TICKET_VOIDED,
            lane=record.lane,
            ticket_id=record.ticket_id,
            reason="outcome_unknown",
        )

    def _release_leftover_calls(self) -> None:
        """Hang up every call the user agent is holding, before answering any.

        **They belong to a process that is gone.** An agent that dies with two
        legs up leaves baresip holding both, and the user agent outlives it: it
        is a separate program, it stays registered, and it keeps the calls. A
        restarted agent used to enumerate nothing, so it answered the next call
        with the orphans still live -- and this user agent's bridge is
        SITE-WIDE, so the previous driver and the previous operator were
        conferenced into a stranger's case. Measured at the round-5 merge gate:

            after the agent dies -> person's calls: ['93567246...']
                                    intercom's calls: ['16d885aa...']
            restarted agent's session: None    (nothing enumerated, hung up)
            NEW call answered by the restarted agent: True
                the leftover person leg is still up: ['93567246...']

        `_reconnect()` has always done this for a socket lost INSIDE a running
        process. That is a different trigger and it could never cover this one:
        a new process has no socket to lose.

        **Every one of them goes, including a ringing one.** `_reconnect()`
        answers a ringing call, because there the agent was up when it arrived
        and somebody may still be at the barrier holding on. Here the agent has
        just started: nothing knows how long that call has been ringing, no
        session exists behind it, and its lane read would be one this process
        never made. Answering it would be speaking to somebody about a case
        assembled out of nothing. It is released, and their intercom's own call
        list takes them to a person -- which is what a dead agent does anyway,
        and it is the install requirement this module already states.

        A user agent that cannot be enumerated does NOT stop the agent starting.
        An agent that refuses to start because it could not ask is an intercom
        nobody answers, which is worse than the leftovers; it is logged, and the
        next `poll()` reports the socket through `ua_unreachable` as it always
        has.
        """
        try:
            leftover = list(self.ua.calls())
        except UaUnreachable as exc:
            log.error("could not ask the user agent what it is holding: %s", exc)
            return
        released = 0
        for call in leftover:
            try:
                self.ua.hangup(call.call_id)
            except UaUnreachable as exc:
                log.error("could not release the leftover call %s: %s", call.call_id, exc)
                continue
            released += 1
        if released:
            log.warning(
                "released %d call(s) the user agent was still holding at startup", released
            )
            self._record(AgentEventKind.LEFTOVER_CALLS_RELEASED, released=released)

    def _check_accounts(self) -> None:
        """Every declared intercom's account must be one the user agent holds.

        Without this, a door whose account the site never added to the user
        agent's own configuration is answered `404 Not Found` by baresip, before
        this process sees anything -- so the agent would publish a working
        surface while one entrance's intercom did nothing at all, at whatever
        hour somebody first pressed it. Asked once, at startup, against the
        running user agent rather than against a file this package does not own.

        **The message names the INTERCOM and never the account.** The account's
        user part IS the secret.

        `accounts()` is asked unconditionally rather than through a `getattr`
        that would skip when a user agent has not got it. A check that can be
        absent is a check that will one day be absent on the day it mattered,
        and this one is the whole of who gets answered.
        """
        held = set(self.ua.accounts())
        missing = sorted(
            intercom.sip_uri
            for intercom in self.config.intercoms
            if intercom.account_user not in held
        )
        if missing:
            raise UaMisconfigured(
                "the user agent holds no account for "
                + ", ".join(repr(one) for one in missing)
                + ". Each declared intercom is identified by the account it dials, so an "
                "account the user agent has not been given is a door whose every call is "
                "answered `404 Not Found` and reported nowhere. Add the account named by "
                "that intercom's dial_secret_file to the user agent's own configuration. "
                "Refusing to start."
            )

    def _measure_audio(self) -> None:
        """Read every file this configuration can reach for, and time it.

        A duration is what the dialogue schedules on, so an unreadable file is
        found here rather than as a step that never completes. It doubles as the
        check that what shipped is a file this user agent can play: **8 kHz,
        mono, 16-bit**, which is what a narrowband call carries -- and it now
        checks the RATE it says it checks. It used to name 8 kHz and compare
        only channels and width, so a `name_audio` recorded at 44.1 kHz started,
        and the one file in this configuration that this package does not
        produce is exactly the one that would be.

        The site's `name_audio` is also BOUNDED: `[speech] name_audio_max_seconds`.
        The operator's briefing waits for it, so an unbounded one holds a driver
        in a call nobody is coming to.
        """
        site_files = {intercom.name_audio for intercom in self.config.intercoms}
        paths = list(site_files)
        for line, languages in self._every_line():
            paths.extend(
                self.config.audio_directory / audio_name(line, language)
                for language in languages
            )
        for path in paths:
            try:
                with wave.open(str(path), "rb") as handle:
                    frames, rate = handle.getnframes(), handle.getframerate()
                    channels, width = handle.getnchannels(), handle.getsampwidth()
            except (OSError, wave.Error) as exc:
                raise AudioMissing(f"{path}: {exc}") from exc
            if channels != 1 or width != 2 or rate != NARROWBAND_RATE:
                raise AudioMissing(
                    f"{path} is {channels} channel(s) at {width * 8} bits, {rate} Hz. Every "
                    f"file this agent plays is mono 16-bit PCM at {NARROWBAND_RATE} Hz, "
                    "which is what a narrowband call carries."
                )
            seconds = frames / rate if rate else 0.0
            if path in site_files and seconds > self.config.name_audio_max_seconds:
                raise AudioMissing(
                    f"{path} is {seconds:.1f}s and [speech].name_audio_max_seconds is "
                    f"{self.config.name_audio_max_seconds:.1f}. It is played to the person "
                    "on the phone before the two legs are put together, so a driver at the "
                    "barrier waits for the whole of it."
                )
            self._durations[path] = seconds

    def _every_line(self):
        from .lines import DRIVER_LINES, OPERATOR_LINES

        for line in DRIVER_LINES:
            yield line, self.config.driver_languages
        for line in OPERATOR_LINES:
            yield line, (self.config.operator_language,)

    # -- the loop ----------------------------------------------------------

    def poll(self) -> None:
        """Everything the user agent said, then everything that is due.

        **A lost control socket is REOPENED here.** It used to be a permanent
        outage: the socket was raised on and never replaced, so an ordinary
        `systemctl restart baresip`, a package upgrade or an OOM kill left the
        agent alive, its user agent registered, and every call ringing at a
        process that would never answer one -- `ua_unreachable` active for the
        life of the process, and the only repair a human restarting the agent.
        """
        try:
            events = self.ua.poll()
            self._code(AgentCode.UA_UNREACHABLE, self.config.agent_id, HealthState.OK)
        except UaUnreachable as exc:
            # The agent is up and cannot work the phone. It is the loudest thing
            # it has to say, and it is its own measurement: nothing else can
            # make it, because a UA that is down cannot report that it is down.
            log.error("the user agent is unreachable: %s", exc)
            self._code(AgentCode.UA_UNREACHABLE, self.config.agent_id, HealthState.ACTIVE)
            self._reconnect()
            return
        for event in events:
            self._handle(event)
        self._registration_state()
        # BEFORE the lanes and before the dialogue: a pulse that has finished is
        # a record to write and a sentence somebody is waiting for.
        self._collect_pulses()
        self._follow_lanes()
        self._advance()

    def _reconnect(self) -> None:
        """Try to get the control socket back, and deal with what was missed.

        Whatever case was in progress is GONE: its legs were torn down or are
        beyond reach, and there is no way to find out what was said while
        nobody was listening. So the session is dropped and every call the user
        agent is still holding is dealt with by the same rule the agent applies
        to any new call -- one that is still RINGING is answered, and anything
        else is released.
        """
        reconnect = getattr(self.ua, "reconnect", None)
        if reconnect is None:
            return
        try:
            calls = reconnect()
        except UaUnreachable as exc:
            log.debug("the control socket is still down: %s", exc)
            return
        if not calls:
            return
        log.warning("the control socket came back; %d call(s) were held", len(calls))
        self._code(AgentCode.UA_UNREACHABLE, self.config.agent_id, HealthState.OK)
        if self.session is not None:
            self._record(
                AgentEventKind.CASE_NOT_SPOKEN,
                intercom=self.session.intercom.sip_uri,
                lane=self.session.intercom.lane,
                case=self.session.case.value if self.session.case else None,
            )
            self._end(self.session)
        for call in calls:
            if call.ringing:
                self._incoming(
                    UaEvent(
                        kind=UaEventKind.CALL_INCOMING,
                        call_id=call.call_id,
                        peer_uri=call.peer_uri,
                        account_user=call.account_user,
                    )
                )
                continue
            # Answered before the socket went, or placed by this process and
            # since orphaned. There is no dialogue behind it any more, and a leg
            # left live would be conferenced into the NEXT case.
            try:
                self.ua.hangup(call.call_id)
            except UaUnreachable as exc:
                log.error("could not release the orphaned call %s: %s", call.call_id, exc)
                return

    # -- the ticket ---------------------------------------------------------

    def _offers_a_ticket_at(self, lane: str) -> bool:
        """Whether a ticket could be OFFERED at this lane, unprompted.

        Both halves are needed and neither is enough. A display, because a
        ticket nobody can see is a stay nobody can prove; and a key to sign one
        with. **An act token is deliberately NOT required**: a site may want the
        driver to have a ticket while a person still works the barrier by hand,
        and a ticket is the identity either way.
        """
        return bool(self._displays_at.get(lane)) and self.config.tickets is not None

    def _follow_lanes(self) -> None:
        """Each declared lane's events, by cursor, then whatever has expired.

        The capture process's pattern, and the same reasons: the cursor is the
        join, a page this build cannot read is refused WHOLE, and a first read
        adopts the lane's position without acting on what is already in the
        window -- those cars have gone.

        Only lanes that could produce a ticket are followed. A lane with no
        display and no act token is one nothing here would do anything about,
        and polling it would be a request per two seconds for nobody.
        """
        for lane in self.config.lanes:
            if not (self._offers_a_ticket_at(lane.name) or lane.can_act):
                continue
            now = self._clock()
            if now < self._lane_due[lane.name]:
                continue
            self._lane_due[lane.name] = now + lane.poll_seconds
            self._poll_lane(lane)
            # THE SAME CADENCE, and after whatever that read did: a ticket the
            # poll has just voided is not redrawn, and a lane that could not be
            # read does not stop the screen in front of the driver being
            # checked.
            self._reassert(lane.name)
        self._expire_tickets()

    def _poll_lane(self, lane) -> None:
        """One read of a lane's events, and one of its state where a ticket is up."""
        try:
            page = self._clients[lane.name].get(
                f"/v1/lane/events?since={self._cursors[lane.name] or 0}"
            )
        except (TargetUnreachable, TargetRefusedUs) as exc:
            log.warning("lane %s could not be followed: %s", lane.name, exc)
            self._code(AgentCode.LANE_UNAVAILABLE, lane.name, HealthState.ACTIVE)
            return
        cursor = page.get("cursor")
        events = page.get("events")
        if not isinstance(cursor, int) or isinstance(cursor, bool) or not isinstance(
            events, list
        ):
            # REFUSED WHOLE, and the cursor is NOT adopted, so the next poll asks
            # for the same events again and this recovers by itself.
            log.error("lane %s served a page this build cannot read", lane.name)
            self._code(AgentCode.LANE_UNAVAILABLE, lane.name, HealthState.ACTIVE)
            return
        self._code(AgentCode.LANE_UNAVAILABLE, lane.name, HealthState.OK)

        held = self._cursors[lane.name]
        if held is None:
            # FIRST READ. Take the lane's place at its current cursor and mint
            # nothing for what is already in the window: a ticket offered now
            # against a decision from before this process started is a code on a
            # screen for a car that has gone.
            self._cursors[lane.name] = cursor
            log.info("following lane %s from cursor %d", lane.name, cursor)
            return
        if page.get("reset") or cursor < held:
            # The saved position no longer refers to anything, or the lane broke
            # its own contract. Either way nothing in the gap is acted on.
            log.warning("lane %s reset the cursor (%s -> %s)", lane.name, held, cursor)
            self._cursors[lane.name] = cursor
            self._void_at(lane.name, "lane_decided_again")
            return

        decided = any(
            isinstance(event, dict) and event.get("kind") == "decision" for event in events
        )
        self._cursors[lane.name] = cursor
        if decided:
            self._consider_ticket(lane.name)
        elif lane.name in self._pending:
            # NOTHING NEW, and a ticket is up. The lane is asked whether the car
            # is still there: presence can go false with no decision behind it --
            # the driver reversed away -- and a code on a screen for an empty
            # lane is a ticket the vend route would refuse `no_vehicle`.
            self._check_pending(lane.name)

    def _consider_ticket(self, lane: str) -> None:
        """A new decision at this lane. Void what was up, and maybe mint."""
        intercom = next(
            (one for one in self.config.intercoms if one.lane == lane), None
        )
        if intercom is None:
            return
        reading = self._read_lane(intercom)
        pending = self._pending.get(lane)
        if (
            pending is not None
            and reading.readable
            and reading.decision_at == pending.decision_at
        ):
            # **THE SAME DECISION, read again.** A press that minted before the
            # poll got there leaves the decision event still in the page, and
            # treating it as new voided the ticket the driver had just been told
            # about and minted a second one for the same car -- so they held a
            # photograph of a voided code while a different one stood on the
            # screen. A new decision is one with a new moment on it.
            return
        # A NEW DECISION VOIDS THE OLD TICKET, whatever the new one says. The
        # ticket named the previous decision's moment, and the lane refuses a
        # completion that names anything but its last one.
        self._void_at(lane, "lane_decided_again")
        # AND IT ENDS THE HELP WINDOW. What that window says to a person is
        # about the driver who was given the previous ticket, and the lane has
        # just decided about somebody else.
        self._end_help_at(lane)
        if not self._offers_a_ticket_at(lane):
            return
        # **THE DECISION DECIDES, NOT THE HEALTH.** `offers_ticket` is a
        # different question from `derive`, for the reason `cases.py` states at
        # length: this build holds no copy of the lane's `vend_blocking` subset,
        # so a malfunction that blocks nothing must not suppress a ticket, and
        # one that blocks the vend is the LANE'S refusal to give -- at the vend,
        # by name, where the human hears the code.
        now = datetime.now(UTC)
        if not offers_ticket(
            reading, now=now, max_age_seconds=self.config.decision_max_age_seconds
        ):
            # `None` IS NOT `True` is the clause of that function that matters
            # most here: an unmeasured presence must never put a code on a
            # screen -- that is the fraud this project has spent its rounds on,
            # arriving through a display instead of through a loop.
            log.info(
                "lane %s: no ticket (presence %r, outcome %r/%r)",
                lane, reading.presence, reading.outcome, reading.reason,
            )
            return
        if reading.decision_at is None:
            return
        case = decision_case(
            reading, now=now, max_age_seconds=self.config.decision_max_age_seconds
        )
        # IS SOMEBODY ALREADY ON THE PHONE AT THIS DOOR? Then the screen is not
        # where they are looking, and the ticket is theirs to be told about
        # rather than to discover.
        speaking = self._call_being_spoken_at(self._screen_door_at(lane))
        self._issue(lane, case, reading.decision_at, told=speaking is None)
        pending = self._pending.get(lane)
        if speaking is None or pending is None:
            return
        # **AND NOBODY IS RUNG.** The case sentence that was playing ends with
        # "I am connecting you to a person", which is no longer what is about to
        # happen -- so it is dropped rather than finished, and what replaces it
        # is the sentence about the code that has just appeared in front of them.
        speaking.speech[UaLeg.DRIVER].clear()
        speaking.line_due[UaLeg.DRIVER] = None
        self._tell_on_screen(speaking, speaking.intercom, pending)

    def _issue(
        self,
        lane: str,
        case: AgentCase,
        decision_at: str,
        show: bool = True,
        told: bool = True,
    ) -> None:
        """Mint, record, show. In that order, and the order is the guarantee.

        The RECORD IS WRITTEN AND FLUSHED BEFORE THE SCREEN, so a crash between
        them leaves a ticket nobody has seen rather than a ticket on a screen
        that this site has no record of. The second is the one that costs a
        customer: they photograph a code and the exit has never heard of it.

        **`show=False` mints WITHOUT a screen**, and it is not a convenience.
        Standalone with a relay and no display still needs the record, because
        the record is the SITE'S and the frame is the DRIVER'S -- and it is the
        record, not the frame, that has to exist before a barrier moves. A
        version of this that minted only where a screen existed pulsed a relay
        with nothing written down, which is the invariant broken; a test that
        records every call in order is what found it.

        **`told=False` mints a ticket NOBODY HAS BEEN TOLD ABOUT**, which is
        what a mint during a live call is until the sentence saying so has
        finished playing. `told_at` is what a press is checked against.
        """
        tickets = self.config.tickets
        told_at = self._now() if told else None
        ticket, payload = mint(
            self.config.site_id, lane, self._now(), tickets.signing_key
        )
        record = _TicketRecord(
            ticket_id=ticket.ticket_id,
            ticket_ref=ticket.ticket_ref,
            site=self.config.site_id,
            lane=lane,
            issued_at=ticket.issued_at,
            told_at=told_at,
            # KEPT because the vend echoes it -- and because a restart has to be
            # able to replay a confirmed ticket's vend with the same decision
            # as well as the same idempotency key.
            decision_at=decision_at,
        )
        self._store.write(record)
        pending = Pending(
            ticket=ticket,
            payload=payload,
            lane=lane,
            intercom=self._screen_door_at(lane),
            case=case,
            decision_at=decision_at,
            expires=self._clock() + tickets.confirm_window_s,
            displays=self._displays_at.get(lane, ()),
            told_at=told_at,
        )
        if show and not self._show(pending):
            self._store.write(
                voided(record, self._now(), "display_unavailable")
            )
            self._record(
                AgentEventKind.TICKET_VOIDED,
                lane=lane,
                ticket_id=ticket.ticket_id,
                reason="display_unavailable",
            )
            return
        self._pending[lane] = pending
        # A NEW TICKET AT THIS LANE ENDS THE PREVIOUS ONE'S HELP WINDOW, so the
        # driver standing here now cannot be described to a person with two
        # sentences about the driver before them.
        self._end_help_at(lane)
        self._record(
            AgentEventKind.TICKET_ISSUED,
            lane=lane,
            case=case.value,
            ticket_id=ticket.ticket_id,
        )

    def _call_being_spoken_at(self, door: str | None) -> Session | None:
        """A live call at this door that has not reached a person yet.

        Before the dial and no further: a person who has already picked up is
        in the case, and hanging up on them to say a code appeared would be a
        second failure rather than a repair.
        """
        session = self.session
        if session is None or door is None or session.intercom.sip_uri != door:
            return None
        if session.state is not State.SPEAKING_CASE:
            return None
        if session.telling is not None or session.confirmed_ticket is not None:
            return None
        return session

    def _screen_door_at(self, lane: str) -> str | None:
        """The one intercom whose display shows this lane's code.

        One, because the configuration refuses two intercoms with a display at
        one lane -- a single code on two door stations is a press at either
        confirming it, and whoever photographs the second screen holds the first
        driver's ticket. A door with no display of its own is not this: its
        press is the round-5 path.
        """
        for intercom in self.config.intercoms:
            where = intercom.lane or intercom.sip_uri
            if where == lane and intercom.display:
                return intercom.sip_uri
        return None

    def _end_help_at(self, lane: str) -> None:
        """End the help window at every door on this lane."""
        for uri, help_window in list(self._help.items()):
            if help_window.lane == lane:
                del self._help[uri]

    def _help_at(self, intercom: Intercom) -> tuple | None:
        """The operator lines a call at this door is HELP for, or `None`.

        `None` and an empty tuple are different answers and the caller branches
        on which: `None` is "this is not help", and `()` is "it is help and
        there is nothing extra to say".
        """
        help_window = self._help.get(intercom.sip_uri)
        if help_window is None:
            return None
        window = self.config.tickets.help_window_s if self.config.tickets else 0.0
        if self._clock() - help_window.at > window:
            return None
        return help_window.lines

    def _show(self, pending: Pending) -> bool:
        """Draw the ticket on every display at its lane. False if any refused."""
        instructions = tuple(
            DISPLAY_TEXT["display.instruction"][language]
            for language in self.config.driver_languages
            if DISPLAY_TEXT["display.instruction"].get(language)
        )
        for screen in pending.displays:
            try:
                screen.show(
                    frame_for(
                        pending.payload,
                        pending.ticket.ticket_ref,
                        instructions,
                        screen.geometry,
                    )
                )
            except DisplayUnavailable as exc:
                log.error("display %s could not be written: %s", screen.name, exc)
                self._code(
                    AgentCode.DISPLAY_UNAVAILABLE, screen.name, HealthState.ACTIVE
                )
                return False
            self._code(AgentCode.DISPLAY_UNAVAILABLE, screen.name, HealthState.OK)
        return True

    def _blank(self, pending: Pending) -> None:
        for screen in pending.displays:
            try:
                screen.blank()
            except DisplayUnavailable as exc:
                # NOT a void: the ticket is already gone. A screen that cannot
                # be cleared is a code left up, which is why it is a code on the
                # health surface rather than a log line.
                log.error("display %s could not be cleared: %s", screen.name, exc)
                self._code(
                    AgentCode.DISPLAY_UNAVAILABLE, screen.name, HealthState.ACTIVE
                )

    def _check_pending(self, lane: str) -> None:
        """Is the ticket on this lane's screen still about the car in front of it?"""
        pending = self._pending.get(lane)
        intercom = next((one for one in self.config.intercoms if one.lane == lane), None)
        if pending is None or intercom is None:
            return
        reading = self._read_lane(intercom)
        if not reading.readable:
            # NOT a void. A lane that cannot be read has not said the car left,
            # and voiding on silence would take a ticket off a screen somebody
            # is walking towards.
            return
        if reading.decision_at != pending.decision_at:
            self._void_at(lane, "lane_decided_again")
        elif reading.presence is not True:
            self._void_at(lane, "presence_lost")

    def _expire_tickets(self) -> None:
        now = self._clock()
        for lane, pending in list(self._pending.items()):
            if now >= pending.expires:
                self._void_at(lane, "window_elapsed")

    def _reassert(self, lane: str) -> None:
        """RE-READ the geometry and RE-WRITE the frame, on every poll a code is up.

        A screen used to be touched exactly twice -- once to show a code and
        once to take it away -- so a display that died in between was never
        noticed: the ticket stayed pending, `display_unavailable` stayed `ok`,
        and the next press confirmed and vended a code the driver could not see.
        A screen that changed MODE in between was worse than that: the frame
        went on being written at the old stride, which is diagonal noise on the
        panel and a code on the health surface, and the agent believed a ticket
        was up.

        So both are measured, and the answer to either is the one the mint
        already had: no ticket, and the press goes to a person.

        **On the LANE's own `poll_seconds`**, which is the cadence everything
        else about a ticket runs at, and not on the dialogue's 0.2 s tick: a
        full-HD frame is eight megabytes, and re-encoding a QR and writing that
        five times a second would be a process that spends its life redrawing a
        code nobody is looking at yet.
        """
        pending = self._pending.get(lane)
        if pending is not None and not self._redraw(pending):
            self._void_at(lane, "display_unavailable")

    def _redraw(self, pending: Pending) -> bool:
        """The geometry, then the frame. False if the screen cannot be asked."""
        for screen in pending.displays:
            was = screen.geometry
            try:
                # ASKED UNCONDITIONALLY, never through a `getattr` that would
                # skip on a screen that has not got it: a check that can be
                # absent is one that will be absent on the day it mattered.
                now = screen.reread_geometry()
            except DisplayUnavailable as exc:
                log.error("display %s could not be asked what it is: %s", screen.name, exc)
                self._code(AgentCode.DISPLAY_UNAVAILABLE, screen.name, HealthState.ACTIVE)
                return False
            if now != was:
                log.warning(
                    "display %s is now %dx%d at %d bits (stride %d)",
                    screen.name, now.width, now.height, now.bits_per_pixel, now.stride,
                )
                self._record(
                    AgentEventKind.DISPLAY_GEOMETRY_CHANGED,
                    display=screen.name,
                    geometry=f"{now.width}x{now.height}@{now.bits_per_pixel}",
                )
        return self._show(pending)

    def _void_at(self, lane: str, reason: str, answer: str | None = None) -> None:
        """Take the ticket down, record why, and blank the screen."""
        pending = self._pending.pop(lane, None)
        if pending is None:
            return
        record = self._store.read(pending.ticket.ticket_id)
        if record is not None:
            self._store.write(voided(record, self._now(), reason, answer))
        self._record(
            AgentEventKind.TICKET_VOIDED,
            lane=lane,
            ticket_id=pending.ticket.ticket_id,
            reason=reason,
        )
        self._blank(pending)

    def _registration_state(self) -> None:
        registered = self.ua.registered()
        state = (
            HealthState.UNKNOWN
            if registered is None
            else (HealthState.OK if registered else HealthState.ACTIVE)
        )
        # The subject is the AGENT rather than one account. It holds one per
        # declared intercom now, and their user parts are the dial secrets --
        # publishing them as health subjects would put every one of them on
        # `GET /v1/agent/health`. What the code says is what the user agent
        # answers: any account it reports in error makes this `active`.
        self._code(AgentCode.SIP_REGISTRATION_LOST, self.config.agent_id, state)

    def _handle(self, event: UaEvent) -> None:
        if event.kind is UaEventKind.CALL_INCOMING:
            return self._incoming(event)
        if event.kind is UaEventKind.CALL_TO_UNKNOWN_ACCOUNT:
            # The USER AGENT refused it -- an INVITE to an address no account of
            # its holds, which is what every caller who does not know an
            # intercom's dial address gets. There is no call to hang up and
            # never was one. It is recorded because the alternative is that the
            # commonest unwanted caller leaves no trace at all, and a site that
            # is being scanned should be able to see it.
            return self._undeclared(_bare_uri(event.peer_uri))
        session = self.session
        if session is None or event.call_id is None:
            return
        if event.kind is UaEventKind.CALL_MEDIA:
            session.live.add(event.call_id)
            return
        if event.kind is UaEventKind.CALL_ESTABLISHED:
            if event.call_id == session.operator_call and session.state is State.CALLING_HUMAN:
                self._brief(session)
            return
        if event.kind is UaEventKind.CALL_CLOSED:
            if event.call_id == session.driver_call:
                # The driver hung up. **The operator's leg goes with it**, and
                # not because it is tidy: this user agent's bridge is site-wide,
                # so a leg left live after its case ended would be conferenced
                # into the NEXT case -- a person still holding for a driver who
                # has gone, put into a stranger's call.
                if session.operator_call:
                    self.ua.hangup(session.operator_call)
                    session.operator_call = None
                self._end(session)
            elif event.call_id == session.operator_call:
                session.operator_call = None
                if session.state in (State.BRIEFING, State.WAITING_DIGIT,
                                     State.COLLECTING_NUMBER):
                    self._nothing_usable(session, hung_up=True)
            return
        if event.kind is UaEventKind.DTMF and event.call_id == session.operator_call:
            # ONLY the operator's leg. A digit from the driver is not an
            # authorisation, and the call it arrived on is the only thing that
            # separates them -- which is why the seam carries it.
            self._digit(session, event.digit or "")

    # -- answering ---------------------------------------------------------

    def _incoming(self, event: UaEvent) -> None:
        """A new inbound call. **The live case is checked BEFORE the identity.**

        The order is the first half of this method. `concurrent_cases` is 1
        because the user agent's bridge is site-wide, so the limit has to hold
        against EVERY caller -- and being undeclared is the DEFAULT state of
        every caller on the network, not a rare one. Checking the identity first
        made the limit apply only to callers the site had declared: a stranger
        dialling mid-case was answered, given a session, and conferenced into a
        live bridge, and the fixed sentence it was told ended with `hangup_all`,
        which cut off the real driver and the real operator.

        **The identity is the account the call ARRIVED AT**, which is the second
        half. Each intercom dials an address only its installer knows, so a call
        at that account is that intercom. The `From` is recorded as a CLAIM and
        decides nothing: it used to be the whole of the mapping, and a fourth
        user agent asserting a declared door's address of record was answered as
        that lane, had a person rung at three in the morning, and wrote a
        complete `authorisation_received` naming a barrier nobody was standing
        at.

        Neither refusal assigns `session`, plays anything, or sends `conference`.
        """
        claimed = _bare_uri(event.peer_uri)
        if self.session is not None:
            # NOT answered, whoever it is. The refusal carries the identity the
            # caller CLAIMED so a site can see who was turned away, and reading
            # it is the only thing done with it.
            self._record(
                AgentEventKind.CALL_REFUSED_BUSY,
                caller_stated_identity=claimed or None,
            )
            if event.call_id:
                self.ua.hangup(event.call_id)
            return
        intercom = self._by_account.get(event.account_user or "")
        if intercom is None:
            # NOT answered either. A call at an account no intercom declares is
            # a caller this site has no way to place, and answering it would
            # mean speaking to somebody about a barrier the agent would have to
            # guess at.
            self._undeclared(claimed)
            if event.call_id:
                self.ua.hangup(event.call_id)
            return
        if event.call_id is None:
            return
        self.ua.answer(event.call_id)
        session = Session(
            intercom=intercom,
            driver_call=event.call_id,
            started=self._clock(),
            languages=self.config.driver_languages,
        )
        self.session = session
        self._code(
            AgentCode.CALL_FROM_UNDECLARED_INTERCOM, self.config.agent_id, HealthState.OK
        )
        self._record(AgentEventKind.CALL_ANSWERED, intercom=intercom.sip_uri,
                     lane=intercom.lane, caller_stated_identity=claimed or None)

        # ONE LANE READ for the whole of what follows. The case, whether a
        # ticket is offered and what the vend will echo all come off the same
        # answer, so a driver cannot be told about one decision and vended
        # against another.
        reading = self._read_lane(intercom)
        now = datetime.now(UTC)

        # **HELP IS THE NEXT PRESS, and it is checked BEFORE the ticket.** A
        # driver whose ticket was confirmed a moment ago and who is pressing
        # again is asking for a person, not asking for a second vend -- and the
        # lane's `already_completed` is the BACKSTOP for that, not the design.
        # Checked first because a pending ticket and a recent confirmation can
        # both be true: the lane may have decided again in between.
        #
        # **It is THAT TICKET'S window, not the door's** -- see `Help`.
        help_lines = self._help_at(intercom)
        pending = self._pending.get(intercom.lane or "")
        if help_lines is not None:
            session.help_lines = help_lines
        elif pending is not None and pending.intercom == intercom.sip_uri:
            if pending.told_at is None:
                # MINTED BEHIND THEM, in the poll that ran while this call was
                # being set up. They have not seen it, so the press is not a
                # confirmation: they are told where to look, and the NEXT press
                # confirms it.
                return self._tell_on_screen(session, intercom, pending)
            return self._confirm_ticket(session, intercom)
        elif pending is None and intercom.display and self._offers_a_ticket_at(
            intercom.lane or ""
        ) and offers_ticket(
            reading, now=now, max_age_seconds=self.config.decision_max_age_seconds
        ) and reading.decision_at is not None:
            # **THE PRESS MINTS WHEN THE POLL HAS NOT.** The ticket path used to
            # run only from `_follow_lanes`, at `[lanes.*] poll_seconds`, so a
            # driver who pressed inside that window rang a person -- and the
            # code appeared on the screen a metre from them, in the same poll,
            # with neither of them told it was there. That window is the one a
            # driver actually presses in: the lane decides when the loop arms,
            # and the press comes after the car has stopped.
            self._issue(
                intercom.lane or "",
                decision_case(
                    reading, now=now,
                    max_age_seconds=self.config.decision_max_age_seconds,
                ),
                reading.decision_at,
                told=False,
            )
            minted = self._pending.get(intercom.lane or "")
            if minted is not None:
                return self._tell_on_screen(session, intercom, minted)

        session.case = derive(
            reading,
            now=now,
            max_age_seconds=self.config.decision_max_age_seconds,
        )
        self._say(session, UaLeg.DRIVER, f"case.{session.case.value}")
        # `case_spoken` is NOT written here. It is written when the last file of
        # the case has finished playing -- see `_advance`. Written at this point
        # it recorded that a driver had been told their case at the moment the
        # first file was QUEUED, which is a claim about a queue, and it stayed
        # true in the log through thirty-three hours of a user agent refusing
        # every one of them.
        if session.case is AgentCase.NOTHING_TO_DO:
            session.state = State.CLOSING
        else:
            session.state = State.SPEAKING_CASE
        session.deadline = None

    def _tell_on_screen(self, session: Session, intercom: Intercom, pending: Pending) -> None:
        """A code is on the screen and this driver has not seen it. Say so.

        **And ring nobody.** There is nothing for a person to do about a driver
        who has a ticket in front of them; the call that used to be placed here
        was placed while a valid, pending, unexpired ticket was on a screen a
        metre away, and neither the driver nor the operator was told it existed.

        `told_at` is written when this sentence has FINISHED, in `_advance`,
        because that is the only moment anything knows they heard it. Until
        then a press is this same sentence again rather than a confirmation.

        `session.case` is deliberately left unset: `case_spoken` means the
        driver was told what happened at the lane, and they were not -- they
        were told where to look. The event below is the record of what they
        were actually told.
        """
        self._say(session, UaLeg.DRIVER, "ticket.on_screen")
        self._record(
            AgentEventKind.TICKET_ON_SCREEN,
            intercom=intercom.sip_uri,
            lane=intercom.lane,
            case=pending.case.value,
            ticket_id=pending.ticket.ticket_id,
        )
        session.telling = pending
        session.state = State.CLOSING
        session.deadline = None

    def _mark_told(self, pending: Pending) -> None:
        """`told_at`, on the record and on the pending, once the driver heard it."""
        still = self._pending.get(pending.lane)
        if still is None or still.ticket is not pending.ticket:
            # Voided while the sentence was playing -- the lane decided again,
            # the car left, the window elapsed. There is nothing to mark and
            # nothing to confirm.
            return
        at = self._now()
        record = self._store.read(pending.ticket.ticket_id) if self._store else None
        if record is not None:
            self._store.write(_told(record, at))
        self._pending[pending.lane] = replace(still, told_at=at)

    def _confirm_ticket(self, session: Session, intercom: Intercom) -> None:
        """THE PRESS. Somebody at that barrier confirmed the ticket on its screen.

        **What a press proves, exactly:** that somebody at the barrier pressed.
        It does not prove a CAR is there -- that is the lane's question, read off
        its arming loop at the moment of the vend, and it is never this agent's
        (SETTLED 7, round 6 E3). The lane refuses `no_vehicle` if it is not, and
        that refusal reaches a person.

        And it does not prove WHO. Whoever photographed the code is holding the
        ticket, which is exactly as strong as holding the paper one it replaces.
        What the press adds is that the holder is standing at that barrier now.
        """
        lane = intercom.lane or ""
        pending = self._pending.pop(lane)
        session.case = pending.case
        session.confirmed_ticket = pending
        # THE WINDOW BELONGS TO THIS TICKET. It ends when the lane decides again
        # or a new ticket is issued here, so the next driver is never briefed
        # with two sentences about the last one.
        self._help[intercom.sip_uri] = Help(
            ticket_id=pending.ticket.ticket_id, lane=lane, at=self._clock()
        )
        record = self._store.read(pending.ticket.ticket_id)
        if record is not None:
            self._store.write(confirmed(record, self._now()))
        self._record(
            AgentEventKind.TICKET_CONFIRMED,
            intercom=intercom.sip_uri,
            lane=lane,
            case=pending.case.value,
            ticket_id=pending.ticket.ticket_id,
        )
        self._say(session, UaLeg.DRIVER, "ticket.confirmed")
        self._blank(pending)
        self._command_vend(session, pending, "display_code_confirmed")

    def _command_vend(self, session: Session, pending: Pending, authorised_by: str) -> None:
        """`POST /v1/lane/vend`, and whatever the lane says about it.

        **Every refusal is the LANE'S.** Nothing is checked here first: not
        presence, not the malfunction table, not the age of the decision. A
        second copy of those checks would be one that comes to disagree with the
        copy the barrier actually obeys, and the lane's own contract says why in
        its first paragraph.
        """
        client = self._acts.get(pending.lane)
        if client is None:
            # READ-ONLY at this lane. The driver has a ticket and a person opens
            # the barrier: the stay is identified either way, which is what the
            # ticket was for.
            return self._to_a_human(session, ("operator.cannot_open",))
        try:
            answer = client.vend(
                authorised_by=authorised_by,
                ticket_ref=pending.ticket.ticket_ref,
                # ECHOED. The moment the lane published for the decision this
                # ticket was minted against, not this process's clock.
                decision_at=pending.decision_at,
                idempotency_key=pending.ticket.ticket_id,
            )
        except LaneActRefusedUs as exc:
            log.error("lane %s refused this agent's act: HTTP %s", pending.lane, exc.status)
            self._code(AgentCode.LANE_ACT_REFUSED, pending.lane, HealthState.ACTIVE)
            # `act_refused`, and NOT `lane_decided_again`: the lane did not
            # decide anything, it would not consider the request at all.
            self._finish_ticket(pending, "act_refused", None)
            return self._to_a_human(session, ())
        except LaneUnreachable as exc:
            log.error("lane %s could not be asked to vend: %s", pending.lane, exc)
            self._code(AgentCode.LANE_UNAVAILABLE, pending.lane, HealthState.ACTIVE)
            self._finish_ticket(pending, "lane_unreachable", None)
            return self._to_a_human(session, ())
        self._code(AgentCode.LANE_ACT_REFUSED, pending.lane, HealthState.OK)

        if answer.commanded:
            record = self._store.read(pending.ticket.ticket_id)
            if record is not None:
                self._store.write(
                    vended(
                        record,
                        self._now(),
                        None if answer.event_cursor is None else str(answer.event_cursor),
                    )
                )
            self._record(
                AgentEventKind.VEND_COMMANDED,
                intercom=session.intercom.sip_uri,
                lane=pending.lane,
                case=pending.case.value,
                ticket_id=pending.ticket.ticket_id,
                authorised_by=authorised_by,
                lane_event_cursor=answer.event_cursor,
            )
            self._remember_help(session, ("operator.help_after_ticket",
                                          "operator.vend_commanded"))
            # **"ASKED TO OPEN", never "is open".** Nothing in this estate has
            # measured a barrier moving: `boom_did_not_rise` is `no_source` on
            # the lane's own health surface.
            self._say(session, UaLeg.DRIVER, "ticket.vend_commanded")
            session.state = State.CLOSING
            session.deadline = None
            return

        self._record(
            AgentEventKind.VEND_REFUSED,
            intercom=session.intercom.sip_uri,
            lane=pending.lane,
            case=pending.case.value,
            ticket_id=pending.ticket.ticket_id,
            authorised_by=authorised_by,
            code=answer.code,
        )
        # THE LANE REFUSED IT, with its own code on the record. Not
        # `lane_decided_again`: that is a new decision or a reset cursor, and
        # this is neither.
        self._finish_ticket(pending, "lane_refused", answer.code)
        # **THE PERSON ALWAYS HEARS BOTH SENTENCES.** One saying a ticket was
        # confirmed and refused, and one saying what the refusal was -- or, for
        # a code this build has no words for, saying exactly that.
        #
        # `VEND_REFUSALS` is set-equal to OUR lane's enum, which is the right
        # check for our lane and no check at all for the third-party seat this
        # module exists to sit in (SETTLED 1). A foreign lane answering its own
        # vocabulary used to reach `_to_a_human` with `()`: the person was
        # briefed as an ordinary case, was not told a ticket had been confirmed,
        # was not told it had been refused, and was then offered `OPEN_NOW`.
        named = f"operator.vend_refused.{answer.code}"
        refusal = named if named in self._durations_for_lines() else UNKNOWN_REFUSAL
        lines = ("operator.ticket_refused", refusal)
        self._remember_help(session, ("operator.help_after_ticket",) + lines)
        self._say(session, UaLeg.DRIVER, "ticket.vend_refused")
        self._to_a_human(session, lines)

    def _remember_help(self, session: Session, lines: tuple) -> None:
        """What the vend answered, onto the help window this call opened.

        Only where the window is about THIS call's ticket: a vend commanded for
        a ticket a human minted is not a confirmation, and there is no window to
        write to.
        """
        help_window = self._help.get(session.intercom.sip_uri)
        if help_window is not None:
            self._help[session.intercom.sip_uri] = replace(help_window, lines=lines)

    def _finish_ticket(self, pending: Pending, reason: str, answer: str | None) -> None:
        """A confirmed ticket that did not vend. The record says so."""
        record = self._store.read(pending.ticket.ticket_id) if self._store else None
        if record is not None:
            self._store.write(voided(record, self._now(), reason, answer))

    def _durations_for_lines(self) -> frozenset[str]:
        """Which operator lines this build has words for, for the ONE language
        the operator hears. A refusal code with no sentence is one this build
        will not claim to explain."""
        from .lines import TEXT

        return frozenset(
            line
            for line, languages in TEXT.items()
            if languages.get(self.config.operator_language)
        )

    def _to_a_human(self, session: Session, extra: tuple) -> None:
        """The case goes to a person, with anything extra they need told first."""
        session.help_lines = tuple(one for one in (session.help_lines + extra) if one)
        session.state = State.SPEAKING_CASE
        session.deadline = None

    def _undeclared(self, claimed: str) -> None:
        """A caller this site cannot place: one code, one event, and no lane.

        The subject of the code is the AGENT and not the caller. A caller-keyed
        subject let anybody who could dial the agent add rows to its health
        surface for ever, one per identity they invented -- and the identity
        they invented is a claim, so the surface would have been filling with
        the attacker's own strings.
        """
        self._code(
            AgentCode.CALL_FROM_UNDECLARED_INTERCOM,
            self.config.agent_id,
            HealthState.ACTIVE,
        )
        self._record(
            AgentEventKind.CALL_FROM_UNDECLARED_INTERCOM,
            caller_stated_identity=claimed or None,
        )

    def _read_lane(self, intercom: Intercom) -> LaneReading:
        """The lane's last decision and health, through the contract, GET only.

        Everything that can go wrong with the read is ONE fact to a driver at a
        barrier -- the lane cannot be asked -- so it is one field, `readable`,
        and the case it produces says exactly that rather than naming a fault
        the driver can do nothing about.
        """
        if intercom.lane is None:
            return LaneReading(lane=None)
        client = self._clients[intercom.lane]
        try:
            state = client.get("/v1/lane/state")
            health = client.get("/v1/lane/health")
        except (TargetUnreachable, TargetRefusedUs) as exc:
            log.warning("lane %s could not be read: %s", intercom.lane, exc)
            self._code(AgentCode.LANE_UNAVAILABLE, intercom.lane, HealthState.ACTIVE)
            return LaneReading(lane=intercom.lane, readable=False)
        version = state.get("contract_version")
        if version not in KNOWN_LANE_VERSIONS or health.get("contract_version") not in (
            KNOWN_LANE_VERSIONS
        ):
            log.error("lane %s speaks contract version %r", intercom.lane, version)
            self._code(AgentCode.LANE_UNAVAILABLE, intercom.lane, HealthState.ACTIVE)
            return LaneReading(lane=intercom.lane, readable=False)
        codes = health.get("codes")
        if not isinstance(codes, list):
            self._code(AgentCode.LANE_UNAVAILABLE, intercom.lane, HealthState.ACTIVE)
            return LaneReading(lane=intercom.lane, readable=False)
        malfunctions = []
        for entry in codes:
            if not isinstance(entry, dict):
                continue
            # `never_alarm` is read from the WIRE, as a boolean, and this
            # package holds no list of its own. A code marked never_alarm is one
            # whose causes include an ordinary car arriving; treating one as a
            # fault would tell a driver their entrance is broken because they
            # turned up.
            if entry.get("state") == HealthState.ACTIVE.value and (
                entry.get("never_alarm") is False
            ):
                malfunctions.append(str(entry.get("code")))
        self._code(AgentCode.LANE_UNAVAILABLE, intercom.lane, HealthState.OK)
        decision = state.get("decision")
        transit = state.get("transit")
        return LaneReading(
            lane=intercom.lane,
            readable=True,
            outcome=_string(decision, "outcome"),
            reason=_string(decision, "reason"),
            # WHEN the lane decided, exactly as it published it. Read as a
            # string and interpreted in `cases`, so an unreadable one is a case
            # rather than an exception in the middle of answering a call.
            decision_at=_string(decision, "at"),
            transit=_string(transit, "state"),
            # READ AS A BOOLEAN, never as truthiness. `None` is what a lane with
            # no presence measurement publishes and it is not `False`: a ticket
            # is offered on `True` and on nothing else, so an unmeasured
            # presence must not become a code on a screen.
            presence=_boolean(decision, "presence"),
            malfunctions=tuple(malfunctions),
        )

    # -- the human ---------------------------------------------------------

    def _call_human(self, session: Session) -> None:
        session.state = State.CALLING_HUMAN
        session.deadline = self._clock() + self.config.no_answer_seconds
        try:
            session.operator_call = self.ua.dial(self.config.human_sip_uri)
        except UaUnreachable as exc:
            log.error("could not call the human: %s", exc)
            self._human_unreachable(session)
            return
        self._record(
            AgentEventKind.HUMAN_CALLED,
            intercom=session.intercom.sip_uri,
            lane=session.intercom.lane,
            case=session.case.value if session.case else None,
            human=self.config.human_sip_uri,
        )

    def _brief(self, session: Session) -> None:
        """Where the call is from, what the case is, and what may be keyed.

        PRIVATE -- the bridge is made after this, not before -- and it carries
        the intercom's name and the case and NOTHING ELSE. No plate: this agent
        never reads one, from this surface or any other, so there is none here
        to leave out.
        """
        session.state = State.BRIEFING
        session.deadline = None
        # The person answered, so they are reachable. **It RECOVERS**: a code
        # that could only ever go one way would be a latch that reads like a
        # state -- `active` for the life of the process however long ago the
        # rota was fixed, with no recovery for a monitor to report.
        self._code(AgentCode.HUMAN_UNREACHABLE, self.config.human_sip_uri, HealthState.OK)
        self._play(session, UaLeg.OPERATOR, session.intercom.name_audio)
        case = session.case or AgentCase.UNRECOGNISED_REASON
        self._say(session, UaLeg.OPERATOR, f"operator_case.{case.value}", operator=True)
        # WHAT ALREADY HAPPENED, before the menu. A person asked to decide about
        # a driver who was given a ticket ninety seconds ago needs to know that
        # and what the barrier said about it -- otherwise their first act is to
        # ask the driver, over an intercom, at three in the morning.
        for line in session.help_lines:
            self._say(session, UaLeg.OPERATOR, line, operator=True)
        self._menu(session)

    def _menu(self, session: Session) -> None:
        for value in Authorisation:
            if value in self.config.authorisations:
                self._say(session, UaLeg.OPERATOR, f"menu.{value.value}", operator=True)

    def _digit(self, session: Session, digit: str) -> None:
        if session.state is State.COLLECTING_NUMBER:
            if digit == END_OF_NUMBER:
                self._finish_call_back(session)
            elif digit.isdigit():
                session.keyed += digit
            return
        if session.state not in (State.BRIEFING, State.WAITING_DIGIT, State.HOLDING):
            return
        value = AUTHORISATION_DIGITS.get(digit)
        if value is None or value not in self.config.authorisations:
            # A digit for an authorisation this site has not enabled is NOT
            # accepted quietly and is not treated as the nearest enabled one.
            session.prompts_left -= 1
            if session.prompts_left < 0:
                self._nothing_usable(session)
                return
            self._say(session, UaLeg.OPERATOR, "operator.menu_repeat", operator=True)
            self._menu(session)
            session.state = State.WAITING_DIGIT
            session.deadline = self._clock() + self.config.nothing_usable_seconds
            return
        self._authorised(session, value)

    def _authorised(self, session: Session, value: Authorisation) -> None:
        """Record what the person keyed, and tell both sides what it means.

        Whether it goes on to ask a barrier for anything is decided downstream by
        what this site declared, never here: `ACTS` names the two authorisations
        that can, and `AgentConfig.act_surface` says where.
        """
        session.authorisation = value
        if value is Authorisation.CALL_BACK:
            session.state = State.COLLECTING_NUMBER
            session.keyed = ""
            session.deadline = self._clock() + self.config.nothing_usable_seconds
            self._say(session, UaLeg.OPERATOR, "operator.menu_callback_number", operator=True)
            return
        self._record_authorisation(session, value, None)
        if value in OPENING_AUTHORISATIONS:
            # THE HUMAN'S WORD, ACTED ON. `OPEN_NOW` overrides a rule -- it is
            # the only authority the lane accepts on a `deny`, and the lane
            # records it as an override, by name, on the event it writes before
            # the barrier moves. `OPEN_AND_FLAG` does not override one: a
            # completion somebody is unsure about and a person overturning a
            # refusal are different acts, and one of them is not made safer by
            # being uncertain.
            return self._human_opens(session, value)
        self._say(session, UaLeg.DRIVER, f"authorisation.{value.value}")
        if value is Authorisation.HOLD:
            session.state = State.HOLDING
            session.deadline = self._clock() + self.config.hold_reprompt_seconds
            session.prompts_left = REPROMPTS
            return
        session.state = State.CLOSING

    def _human_opens(self, session: Session, value: Authorisation) -> None:
        """`OPEN_NOW` / `OPEN_AND_FLAG`, through the SAME route the press uses.

        One vend path, not two. The authority differs and nothing else does --
        which is what makes "a human opened it" and "a driver confirmed a
        ticket" the same record with a different name on it, and what stops one
        of them growing a refusal the other does not apply.

        **A ticket is minted here if there is not one**, because the lane's
        completion names an identity and there has to be one to name. Where a
        display is declared the driver sees it and leaves with a stay they can
        prove; where none is, the ticket exists in this site's record and the
        EXIT IS THEN THE HUMAN'S -- `docs/CONTRACT.md` says so rather than
        leaving an operator to discover it.
        """
        self._say(session, UaLeg.DRIVER, f"authorisation.{value.value}")
        if session.intercom.lane is None:
            return self._standalone_opens(session, value)
        lane = session.intercom.lane
        pending = self._pending.pop(lane, None) if lane else None
        if lane is not None and pending is None and self._can_mint_for(lane):
            pending = self._mint_for_human(lane)
        if lane is None or pending is None or lane not in self._acts:
            # NOTHING HERE CAN ACT: no act token for this lane, no key to sign a
            # ticket with, or a standalone intercom with no relay. One fixed
            # sentence, and it is not optional -- a person who believes a barrier
            # moved when it did not is worse off than one who was never called.
            self._say(session, UaLeg.OPERATOR, "operator.cannot_open",
                      operator=True)
            session.state = State.CLOSING
            return
        session.state = State.CLOSING
        session.deadline = None
        self._command_vend(session, pending, ACTS[value])

    def _standalone_opens(self, session: Session, value: Authorisation) -> None:
        """STANDALONE: the intercom's own relay, on the human's word only.

        **THE ORDER IS THE INVARIANT** (SETTLED 7, round 6 E4). Where there is a
        lane, the lane writes the identity and flushes it before its relay
        moves. There is no lane here, so this agent's OWN RECORD is written and
        flushed first, and only then does the relay pulse -- and if the record
        cannot be written, nothing moves.

        **AND NOTHING MEASURES PRESENCE.** There is no loop, no camera and no
        gate. The human, who can hear the driver, is the presence check; that is
        the whole of it, and `docs/CONTRACT.md` says so in a paragraph rather
        than leaving a reader to assume otherwise.
        """
        relay = self._relays.get(session.intercom.sip_uri)
        if relay is None:
            self._say(session, UaLeg.OPERATOR, "operator.cannot_open",
                      operator=True)
            session.state = State.CLOSING
            return
        # THE RECORD, ALWAYS, AND BEFORE THE RELAY. Not only where a screen
        # exists: the record is what says this site opened a barrier, and a
        # pulse with nothing written down is the invariant broken. Its `lane`
        # field is the INTERCOM's URI, because a standalone site has no lane and
        # a ticket has to say where it was issued.
        pending = self._mint_standalone(session.intercom)
        if pending is None:
            # NOTHING CAN BE RECORDED, so nothing moves. `[tickets]` is required
            # wherever a relay is declared, so this is reachable only if the
            # store itself refused a write -- and a barrier that opened anyway
            # would be one this site cannot prove it opened.
            log.error("no ticket record could be written; the relay is NOT pulsed")
            self._say(session, UaLeg.OPERATOR, "operator.cannot_open", operator=True)
            session.state = State.CLOSING
            return
        # `TicketStore.write` renames a temporary file into place, which is the
        # flush: past this line the record is on the disk. **NOTHING IS WRITTEN
        # ABOUT THE PULSE YET.** `relay_pulsed` used to be recorded here, before
        # the request, and it stood whether the unit refused, answered something
        # else, or was not there at all -- so `/v1/agent/events`, which at a
        # standalone site is the only machine-readable account of a barrier,
        # said the relay was pulsed when it was not.
        self._start_pulse(session, relay, pending, value)

    def _start_pulse(self, session: Session, relay, pending: Pending, value) -> None:
        """Pulse on a THREAD, and collect the outcome on a later poll.

        The request used to be made inside `poll()`, so for the whole of it this
        agent played nothing, answered nothing and followed no lane. With the
        timeout derived from the pulse -- which is the repair for a barrier that
        needs a six-second contact -- that is eleven seconds of an agent that
        has stopped working, on the one path that moves a barrier.

        `[escalation] nothing_usable_seconds` bounds how long the person waits:
        past it they are told this agent cannot say the barrier opened, which is
        true, and the outcome is still written to the record and the event
        stream when it arrives.
        """
        pulse = Pulse(
            intercom=session.intercom.sip_uri,
            pending=pending,
            authorisation=value.value,
            port=relay.relay.port,
            pulse_ms=relay.relay.pulse_ms,
        )
        pulse.case = session.case.value if session.case else None

        def run() -> None:
            try:
                relay.pulse()
            except RelayRefusedUs as exc:
                pulse.outcome, pulse.code = str(exc), AgentCode.RELAY_REFUSED_US
            except RelayUnreachable as exc:
                pulse.outcome, pulse.code = str(exc), AgentCode.RELAY_UNREACHABLE
            except BaseException as exc:  # noqa: BLE001
                # `relay.pulse()` maps everything it can name; this is the
                # thread's own last resort, because an exception that escaped it
                # would leave `done` false and an operator waiting on a bound.
                log.exception("the relay thread raised")
                pulse.outcome, pulse.code = (
                    f"the pulse raised {type(exc).__name__}: {exc}",
                    AgentCode.RELAY_REFUSED_US,
                )
            else:
                pulse.outcome = ""
            finally:
                pulse.done = True

        # NOT A DAEMON. A daemon thread mid-request when the interpreter starts
        # finalising is the abort `tests/serving.py` exists about, and this one
        # is bounded by the derived timeout anyway.
        pulse.thread = threading.Thread(
            target=run, name=f"relay-pulse-{session.intercom.sip_uri}", daemon=False
        )
        self._pulse_seq += 1
        self._pulses[self._pulse_seq] = pulse
        pulse.thread.start()
        session.pulse = pulse
        session.state = State.WAITING_RELAY
        session.deadline = self._clock() + self.config.nothing_usable_seconds

    def _collect_pulses(self) -> None:
        """Whatever a pulse thread has finished, written down and said."""
        for key, pulse in list(self._pulses.items()):
            if not pulse.done:
                continue
            del self._pulses[key]
            if pulse.thread is not None:
                pulse.thread.join(timeout=1.0)
            self._settle_pulse(pulse)

    def _settle_pulse(self, pulse: Pulse) -> None:
        """The record, the event, the health code and the operator's sentence.

        In that order, and the operator's sentence is last because it is the
        only one of the four that needs somebody to still be on the phone.
        """
        session = self.session
        speaking = (
            session
            if session is not None and session.pulse is pulse
            and session.state is State.WAITING_RELAY
            else None
        )
        record = (
            self._store.read(pulse.pending.ticket.ticket_id) if self._store else None
        )
        if pulse.outcome == "":
            if record is not None:
                # VENDED, and `lane_answer` is `None`: there is no lane here and
                # nothing answered but the relay. A record left `issued` would
                # be one a restart voids `restarted` over a barrier that moved.
                self._store.write(vended(record, self._now(), None))
            self._record(
                AgentEventKind.RELAY_PULSED,
                intercom=pulse.intercom,
                lane=None,
                case=pulse.case,
                authorisation=pulse.authorisation,
                relay_port=pulse.port,
                relay_ms=pulse.pulse_ms,
            )
            self._code(AgentCode.RELAY_REFUSED_US, pulse.intercom, HealthState.OK)
            self._code(AgentCode.RELAY_UNREACHABLE, pulse.intercom, HealthState.OK)
            self._blank(pulse.pending)
            if speaking is not None:
                self._say(speaking, UaLeg.OPERATOR, "operator.vend_commanded",
                          operator=True)
        else:
            log.error("the relay at %s did not pulse: %s", pulse.intercom, pulse.outcome)
            if record is not None:
                self._store.write(voided(record, self._now(), "relay_failed"))
            self._record(
                AgentEventKind.RELAY_PULSE_FAILED,
                intercom=pulse.intercom,
                lane=None,
                case=pulse.case,
                authorisation=pulse.authorisation,
                cause=pulse.outcome,
                relay_port=pulse.port,
                relay_ms=pulse.pulse_ms,
            )
            if pulse.code is not None:
                self._code(pulse.code, pulse.intercom, HealthState.ACTIVE)
            self._blank(pulse.pending)
            if speaking is not None:
                self._say(speaking, UaLeg.OPERATOR, "operator.cannot_open", operator=True)
        if speaking is not None:
            speaking.pulse = None
            speaking.state = State.CLOSING
            speaking.deadline = None

    def _mint_standalone(self, intercom: Intercom) -> Pending | None:
        """A ticket at a door with no lane. Its `lane` field is the door's URI.

        Shown only where a screen is declared; RECORDED either way.
        """
        if not self._can_mint_for(intercom.sip_uri):
            return None
        self._issue(
            intercom.sip_uri,
            AgentCase.STANDALONE,
            self._now(),
            show=bool(intercom.display),
        )
        return self._pending.pop(intercom.sip_uri, None)

    def _can_mint_for(self, lane: str) -> bool:
        return self.config.tickets is not None and self._store is not None

    def _mint_for_human(self, lane: str) -> Pending | None:
        """A ticket for a completion a person asked for, against THIS decision.

        The lane's `decision_at` is read now rather than remembered: a human
        keying `OPEN_NOW` is completing whatever the lane last decided, and a
        moment this agent held from an earlier poll would be refused
        `decision_mismatch`.
        """
        intercom = next((one for one in self.config.intercoms if one.lane == lane), None)
        if intercom is None:
            return None
        reading = self._read_lane(intercom)
        if not reading.readable or reading.decision_at is None:
            return None
        case = derive(
            reading,
            now=datetime.now(UTC),
            max_age_seconds=self.config.decision_max_age_seconds,
        )
        self._issue(lane, case, reading.decision_at)
        return self._pending.pop(lane, None)

    def _finish_call_back(self, session: Session) -> None:
        keyed = session.keyed
        if not keyed.isdigit() or not keyed:
            self._nothing_usable(session)
            return
        self._record_authorisation(session, Authorisation.CALL_BACK, keyed)
        self._say(session, UaLeg.DRIVER, f"authorisation.{Authorisation.CALL_BACK.value}")
        session.state = State.CLOSING

    def _record_authorisation(self, session, value: Authorisation, keyed: str | None) -> None:
        self._record(
            AgentEventKind.AUTHORISATION_RECEIVED,
            intercom=session.intercom.sip_uri,
            lane=session.intercom.lane,
            case=session.case.value if session.case else None,
            authorisation=value.value,
            human=self.config.human_sip_uri,
            keyed=keyed,
        )

    def _human_unreachable(self, session: Session) -> None:
        self._code(
            AgentCode.HUMAN_UNREACHABLE, self.config.human_sip_uri, HealthState.ACTIVE
        )
        self._record(
            AgentEventKind.HUMAN_UNREACHABLE,
            intercom=session.intercom.sip_uri,
            lane=session.intercom.lane,
            case=session.case.value if session.case else None,
            human=self.config.human_sip_uri,
        )
        if session.operator_call:
            self.ua.hangup(session.operator_call)
            session.operator_call = None
        self._say(session, UaLeg.DRIVER, "driver.human_unreachable")
        session.state = State.CLOSING
        session.deadline = None

    def _nothing_usable(self, session: Session, hung_up: bool = False) -> None:
        """No usable instruction. **The driver is told WHICH of the two it was.**

        A person who keyed nothing this site accepts and a person who put the
        phone down mid-menu are different things, and the driver used to hear "I
        could not take an instruction" for both -- which is not what happened in
        the second, and a driver told it goes on standing there.
        """
        self._record(
            AgentEventKind.NOTHING_USABLE,
            intercom=session.intercom.sip_uri,
            lane=session.intercom.lane,
            case=session.case.value if session.case else None,
            human=self.config.human_sip_uri,
        )
        self._say(
            session,
            UaLeg.DRIVER,
            "driver.operator_hung_up" if hung_up else "driver.nothing_usable",
        )
        session.state = State.CLOSING
        session.deadline = None

    # -- the clock ---------------------------------------------------------

    def _advance(self) -> None:
        session = self.session
        if session is None:
            return
        now = self._clock()
        self._speak(session, now)
        if self.session is not session:
            # `_speak` can END the case: a line nobody can be told is a case
            # nobody can be told. Everything below is about a session that is
            # still in progress, and from `BRIEFING` with a cleared queue the
            # very next branch would send `conference` on a call already hung
            # up.
            return
        if session.state is State.SPEAKING_CASE and self._silent(session, UaLeg.DRIVER, now):
            self._spoken(session)
            self._call_human(session)
            return
        if session.state is State.CALLING_HUMAN and session.deadline is not None:
            if now >= session.deadline:
                self._human_unreachable(session)
            return
        if session.state is State.BRIEFING and self._silent(session, UaLeg.OPERATOR, now):
            # The menu has finished. NOW the two are put together: everything
            # before this was private to a leg, which is what let the operator
            # be told the case without the driver hearing it.
            self.ua.bridge()
            session.bridged = True
            session.state = State.WAITING_DIGIT
            session.deadline = now + self.config.nothing_usable_seconds
            return
        if session.state in (State.WAITING_DIGIT, State.COLLECTING_NUMBER):
            if session.deadline is not None and now >= session.deadline:
                self._nothing_usable(session)
            return
        if session.state is State.HOLDING:
            if session.deadline is not None and now >= session.deadline:
                self._say(session, UaLeg.DRIVER, "driver.hold_reprompt")
                session.deadline = now + self.config.hold_reprompt_seconds
            return
        if session.state is State.WAITING_RELAY:
            # WAITING FOR THE UNIT, with a bound on how long the person waits.
            # Past it they are told this agent cannot say the barrier opened --
            # which is what it can support -- and the outcome is still written
            # to the record and the event stream when it arrives.
            if session.deadline is not None and now >= session.deadline:
                log.error(
                    "the relay at %s has not answered in %.0fs; the operator is told so",
                    session.intercom.sip_uri, self.config.nothing_usable_seconds,
                )
                self._say(session, UaLeg.OPERATOR, "operator.cannot_open", operator=True)
                session.pulse = None
                session.state = State.CLOSING
                session.deadline = None
            return
        if session.state is State.CLOSING and self._silent(session, UaLeg.DRIVER, now):
            if session.telling is not None:
                # THE SENTENCE HAS FINISHED, which is the only moment anything
                # knows the driver heard where to look. Until now a press was
                # that sentence again; from now it is a confirmation.
                self._mark_told(session.telling)
                session.telling = None
            # `nothing_to_do` reaches here without passing SPEAKING_CASE, and it
            # is a case that WAS spoken -- so this is its moment too.
            self._spoken(session)
            self._hangup_all()
            self._end(session)

    def _speak(self, session: Session, now: float) -> None:
        """Play what is due on each leg, and BOUND how long a line may be due.

        A refusal is usually benign and a fifth of a second from resolving --
        the call's audio stream has not come up yet -- so the line is kept and
        retried. What this used to have no answer for is the OTHER cause: a file
        the user agent will not decode, an audio mode it will not play into, a
        mixer stuck in a mode it cannot leave. Retried for ever, that is a driver
        in an answered call hearing nothing, with no timer, no code, and a log
        saying their case was spoken. Measured on this build: thirty-three hours
        of it, and a clean health surface throughout.

        So every line carries `[speech] line_timeout_seconds`, and a line still
        undelivered past it is `audio_playback_failed` on that LEG.
        """
        for leg in (UaLeg.DRIVER, UaLeg.OPERATOR):
            if not session.speech[leg]:
                session.line_due[leg] = None
                continue
            if session.free_at[leg] > now:
                continue
            # A line is DUE on this leg. The clock starts here.
            if session.line_due[leg] is None:
                session.line_due[leg] = now
            call_id = session.call_of(leg)
            if call_id is None or call_id not in session.live:
                # Answered, and no audio stream. Nothing to play into and
                # nothing to be refused by, which is why the clock is not
                # started by a refusal.
                self._line_overdue(session, leg, now)
                return
            path = session.speech[leg][0]
            try:
                self.ua.play(call_id, str(path))
            except UaRefused as exc:
                log.debug("the user agent refused %s for now: %s", path, exc)
                self._line_overdue(session, leg, now)
                return
            except UaUnreachable as exc:
                log.error("could not play %s: %s", path, exc)
                self._code(AgentCode.UA_UNREACHABLE, self.config.agent_id, HealthState.ACTIVE)
                self._line_overdue(session, leg, now)
                return
            session.speech[leg].popleft()
            session.line_due[leg] = None
            self._code(AgentCode.AUDIO_PLAYBACK_FAILED, leg.value, HealthState.OK)
            # WHEN IT IS OVER, on the file's own measured duration. The user
            # agent offers no completion signal for the verb this plays with --
            # measured against baresip 4.11.0, whose `mixausrc` reports the end
            # of a file to nobody: it logs at debug level and emits no event.
            # The duration is read out of the file at startup, which is a
            # property of something this package ships and can measure.
            session.free_at[leg] = now + self._durations.get(path, 0.0)

    def _line_overdue(self, session: Session, leg: UaLeg, now: float) -> None:
        """One line has been due too long. Say so, and stop waiting for it."""
        started = session.line_due[leg]
        if started is None or now - started <= self.config.line_timeout_seconds:
            return
        log.error(
            "the %s leg could not be spoken to for %.0fs; giving up on the line",
            leg.value, now - started,
        )
        self._code(AgentCode.AUDIO_PLAYBACK_FAILED, leg.value, HealthState.ACTIVE)
        session.speech[leg].clear()
        session.line_due[leg] = None
        session.free_at[leg] = now
        if leg is UaLeg.DRIVER and session.state is State.SPEAKING_CASE:
            # The driver cannot be told their case. It is still a case, so it
            # goes to a person -- briefed the same way, and timed the same way.
            self._call_human(session)
            return
        if leg is UaLeg.DRIVER and session.state is State.CALLING_HUMAN:
            # Already on its way to a person; nothing more to say to the driver.
            return
        # Nothing left that can tell anybody anything. The case ENDS and the
        # driver's call is RELEASED rather than held open in silence, which is
        # the failure this exists to stop.
        self._not_spoken(session)

    def _not_spoken(self, session: Session) -> None:
        self._record(
            AgentEventKind.CASE_NOT_SPOKEN,
            intercom=session.intercom.sip_uri,
            lane=session.intercom.lane,
            case=session.case.value if session.case else None,
        )
        self._hangup_all()
        self._end(session)

    def _hangup_all(self) -> None:
        """End every call, and do not let a dead user agent keep the session.

        A session that outlived its calls because the socket was down would
        refuse every later caller as busy for a case nobody is in.
        """
        try:
            self.ua.hangup_all()
        except UaUnreachable as exc:
            log.error("could not end the calls: %s", exc)

    def _spoken(self, session: Session) -> None:
        """`case_spoken`, ONCE, when the last file of the case has FINISHED.

        It used to be written when the first file was QUEUED, which is a claim
        about a queue: the record said the driver had been told their case
        while the user agent was refusing every line of it.
        """
        if session.case is None or session.spoken:
            return
        session.spoken = True
        self._record(
            AgentEventKind.CASE_SPOKEN,
            intercom=session.intercom.sip_uri,
            lane=session.intercom.lane,
            case=session.case.value,
        )

    def _silent(self, session: Session, leg: UaLeg, now: float) -> bool:
        return not session.speech[leg] and session.free_at[leg] <= now

    def _say(self, session: Session, leg: UaLeg, line: str, operator: bool = False) -> None:
        """Queue a line. On the driver's leg that is EVERY declared language.

        In the declared ORDER, one after the other. The driver has no keypad and
        no way to choose, so the site's order is the answer, and a language
        skipped would be somebody at a barrier who was told nothing.
        """
        languages = (
            (self.config.operator_language,) if operator else session.languages
        )
        for language in languages:
            path = self.config.audio_directory / audio_name(line, language)
            if path not in self._durations:
                self._code(AgentCode.AUDIO_MISSING, line, HealthState.ACTIVE)
                log.error("no audio for %s in %s", line, language)
                continue
            session.speech[leg].append(path)

    def set_language(self, call_id: str, language: str) -> None:
        """Speak the rest of THIS call in one language, from the next sentence on.

        The one function the language switch goes through, and the reason it
        exists now rather than with the thing that will call it: a driver who
        answers in Spanish should not go on hearing English first for the rest
        of the call, and the detector that notices is a later step. Everything
        that step has to add is the noticing.

        **A language this site did not declare is REFUSED**, not accepted and
        then found to have no audio: this package would have no words for it,
        and a switch that silently did nothing is a driver still being spoken to
        in a language they said they do not have.

        What is already queued is not re-cut. A sentence that has begun playing
        finishes; the switch applies from the next one, which is what "from
        there" means on a call somebody is listening to.
        """
        session = self.session
        if session is None or session.driver_call != call_id:
            raise ValueError(f"no call {call_id!r} is in progress")
        if language not in self.config.driver_languages:
            raise ValueError(
                f"{language!r} is not a language this site declared "
                f"({', '.join(self.config.driver_languages)}). This package has no words for "
                "it, and a switch that silently did nothing would leave a driver being "
                "spoken to in a language they have just said they do not have."
            )
        session.languages = (language,)

    def _play(self, session: Session, leg: UaLeg, path: Path) -> None:
        if path not in self._durations:
            self._code(AgentCode.AUDIO_MISSING, str(path), HealthState.ACTIVE)
            return
        session.speech[leg].append(path)

    def _end(self, session: Session) -> None:
        self._record(
            AgentEventKind.CALL_ENDED,
            intercom=session.intercom.sip_uri,
            lane=session.intercom.lane,
            case=session.case.value if session.case else None,
            authorisation=session.authorisation.value if session.authorisation else None,
        )
        self.session = None

    # -- the record --------------------------------------------------------

    def _record(self, kind: AgentEventKind, **fields) -> None:
        event = AgentEvent(
            kind=kind.value,
            site_id=self.config.site_id,
            agent_id=self.config.agent_id,
            intercom=fields.get("intercom") or self.config.agent_id,
            lane=fields.get("lane"),
            case=fields.get("case"),
            authorisation=fields.get("authorisation"),
            human=fields.get("human"),
            at=self._now(),
            keyed=fields.get("keyed"),
            caller_stated_identity=fields.get("caller_stated_identity"),
            released=fields.get("released"),
            ticket_id=fields.get("ticket_id"),
            authorised_by=fields.get("authorised_by"),
            code=fields.get("code"),
            reason=fields.get("reason"),
            lane_event_cursor=fields.get("lane_event_cursor"),
            relay_port=fields.get("relay_port"),
            relay_ms=fields.get("relay_ms"),
            cause=fields.get("cause"),
            display=fields.get("display"),
            geometry=fields.get("geometry"),
        )
        if len(self._log) == self._log.maxlen:
            self._dropped += 1
        self._cursor += 1
        self._log.append((self._cursor, event))

    def _code(self, code: AgentCode, subject: str, state: HealthState) -> None:
        self._states[(code.value, subject)] = state.value

    # -- the read surface --------------------------------------------------

    def describe(self) -> AgentDescription:
        return AgentDescription(
            agent_id=self.config.agent_id,
            site_id=self.config.site_id,
            intercoms=tuple(
                IntercomDescription(
                    sip_uri=intercom.sip_uri,
                    lane=intercom.lane,
                    has_display=intercom.display is not None,
                    has_relay=intercom.relay is not None,
                )
                for intercom in self.config.intercoms
            ),
            lanes=tuple(
                LaneCapability(
                    name=lane.name,
                    # BOTH, because a completion names an identity: an act token
                    # with no key to sign a ticket with opens nothing.
                    can_vend=lane.name in self._acts
                    and self.config.tickets is not None,
                )
                for lane in self.config.lanes
            ),
            user_agent=UserAgentDescription(
                kind=self.config.user_agent.kind,
                version=self._ua_version,
                tested_versions=_tested_versions(self.ua),
                registered=self.ua.registered(),
                reconnect_seconds=self.config.user_agent.reconnect_seconds,
            ),
            driver_languages=self.config.driver_languages,
            operator_language=self.config.operator_language,
            authorisations=tuple(
                value.value for value in Authorisation if value in self.config.authorisations
            ),
            event_window_depth=self.config.event_window_depth,
            concurrent_cases=1,
            no_answer_seconds=self.config.no_answer_seconds,
            nothing_usable_seconds=self.config.nothing_usable_seconds,
            hold_reprompt_seconds=self.config.hold_reprompt_seconds,
            transfer_declared=self.config.transfer_sip_uri is not None,
            decision_max_age_seconds=self.config.decision_max_age_seconds,
            line_timeout_seconds=self.config.line_timeout_seconds,
            name_audio_max_seconds=self.config.name_audio_max_seconds,
        )

    def health(self) -> AgentHealth:
        """Every code, every time. A code with no subject yet ships `unknown`.

        `lane_unavailable` ships once per DECLARED lane whether or not a call has
        been taken at it, because a lane nobody has asked about is a lane nobody
        has measured -- and an absent code reads exactly like a healthy one.
        """
        entries: list[AgentEntry] = []
        for code in AgentCode:
            subjects = {
                subject: state
                for (seen, subject), state in self._states.items()
                if seen == code.value
            }
            if code is AgentCode.LANE_UNAVAILABLE:
                for lane in self.config.lanes:
                    subjects.setdefault(lane.name, HealthState.UNKNOWN.value)
            if not subjects:
                subjects = {self.config.agent_id: HealthState.UNKNOWN.value}
            entries.extend(
                AgentEntry(code=code.value, subject=subject, state=state)
                for subject, state in sorted(subjects.items())
            )
        return AgentHealth(codes=tuple(entries))

    def events(self, since: int) -> AgentEventPage:
        current = self._cursor
        oldest = self._log[0][0] if self._log else None
        return AgentEventPage(
            cursor=current,
            reset=since > current or (oldest is not None and since + 1 < oldest),
            dropped=self._dropped,
            events=tuple(
                {"cursor": seq, **event.to_dict()}
                for seq, event in self._log
                if seq > since
            ),
        )


def _bare_uri(peer: str | None) -> str:
    """The identity a caller CLAIMED, without a display name, parameters or port.

    Nothing is decided by it -- the account the call arrived at is what says
    which intercom it is -- but it is what goes on the record as
    `caller_stated_identity`, and a `From` is not stable to the character: a
    unit sends `"Door 1" <sip:door1@10.0.0.9:5060>;tag=…` on one call and
    `sip:door1@10.0.0.9` on the next, and two spellings of one claim would read
    as two callers. Reduced by SHAPE, once, here.
    """
    if not peer:
        return ""
    text = peer.strip()
    if "<" in text and ">" in text:
        text = text[text.index("<") + 1 : text.index(">")]
    text = text.split(";")[0].strip()
    scheme, _, rest = text.partition(":")
    if not rest:
        return text
    user, _, host = rest.partition("@")
    if not host:
        return text
    return f"{scheme}:{user}@{host.split(':')[0]}"


def _boolean(payload, key: str) -> bool | None:
    """A field that is `true`, `false` or absent. Anything else is `None`.

    Typed rather than truthy: a lane that published the string `"false"` or the
    number `0` would be read as a presence by `if value`, and the field it is
    reading decides whether a code goes on a screen for a vehicle nobody
    measured.
    """
    if not isinstance(payload, dict):
        return None
    value = payload.get(key)
    return value if isinstance(value, bool) else None


def _string(payload, key: str) -> str | None:
    if not isinstance(payload, dict):
        return None
    value = payload.get(key)
    return value if isinstance(value, str) and value else None


def _tested_versions(user_agent) -> tuple[str, ...]:
    from .ua_baresip import TESTED_VERSIONS

    return tuple(getattr(user_agent, "tested_versions", TESTED_VERSIONS))


__all__ = [
    "CONTRACT_VERSION",
    "END_OF_NUMBER",
    "NARROWBAND_RATE",
    "KNOWN_LANE_VERSIONS",
    "REPROMPTS",
    "Agent",
    "AudioMissing",
    "Session",
    "State",
    "utc_now",
]
