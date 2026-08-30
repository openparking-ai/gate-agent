"""The gate agent: it answers the intercom, says what happened, and calls a human.

Gokhan's spec, his words: *the first line of communication is our automated gate
agent; a human only when the agent cannot handle, confirm or find the problem*,
and when the human is reached *"we RECEIVE THEIR AUTHORISATION -- open the gate,
don't open, I'll be there in a minute"*.

**IT OPENS NOTHING.** An authorisation is a RECORD of what a person said. It is
never an act, and this is not a promise: there is no vend route on the lane
contract this build reads, there is no act surface here, and the only client in
this package cannot build a request that is not a `GET`. `OPEN_NOW` ends in an
event, two audio messages, and nothing else -- and one of those messages is the
person being told, in the same breath, that this version cannot move a barrier.

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
case is REFUSED WITHOUT BEING ANSWERED, which is what makes the intercom's own
call list move on to the human's number -- the degradation the install
requirement in `docs/CONTRACT.md` exists for.
"""

from __future__ import annotations

import logging
import wave
from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum, auto
from pathlib import Path
from time import monotonic

from .cases import LaneReading, derive
from .client import ReadOnlyClient, TargetRefusedUs, TargetUnreachable
from .config import AgentConfig, Intercom
from .contract import (
    AUTHORISATION_DIGITS,
    CANNOT_ACT,
    CONTRACT_VERSION,
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
    UserAgentDescription,
)
from .lines import audio_name
from .ua import UaEvent, UaEventKind, UaLeg, UaRefused, UaUnreachable

log = logging.getLogger(__name__)

#: Lane contract versions this agent can read. Same rule and same reason as the
#: monitor's: a lane on another version is refused rather than half-read, and
#: half-understanding a decision about a vehicle is worse than admitting it
#: cannot be read.
KNOWN_LANE_VERSIONS: tuple[int, ...] = (1,)

#: How many times a digit outside the enabled set is re-prompted before the
#: attempt is `nothing usable`. Two, and it is fixed rather than per-site: it is
#: a property of how a person uses a keypad, not of a garage.
REPROMPTS = 2

#: The digit that ends a keyed number.
END_OF_NUMBER = "#"


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
    #: Everything has been said; the call ends when the last message finishes.
    CLOSING = auto()


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
    #: What is queued to play on each leg, and when the leg is next free.
    speech: dict[UaLeg, deque] = field(default_factory=lambda: {
        UaLeg.DRIVER: deque(), UaLeg.OPERATOR: deque()
    })
    free_at: dict[UaLeg, float] = field(default_factory=lambda: {
        UaLeg.DRIVER: 0.0, UaLeg.OPERATOR: 0.0
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
        self._by_uri = {intercom.sip_uri: intercom for intercom in config.intercoms}
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
        """Measure every audio file, then check the UA. In that order.

        The files first, because a missing one is a configuration this process
        must not run on and it costs nothing to find out; the UA second, because
        finding out means opening a socket to another process.
        """
        self._measure_audio()
        self._code(AgentCode.AUDIO_MISSING, self.config.agent_id, HealthState.OK)
        self.ua.start()
        self._ua_version = self.ua.version()
        self._code(AgentCode.UA_UNREACHABLE, self.config.agent_id, HealthState.OK)
        self._code(AgentCode.UA_UNSUPPORTED_VERSION, self.config.agent_id, HealthState.OK)

    def _measure_audio(self) -> None:
        """Read every file this configuration can reach for, and time it.

        A duration is what the dialogue schedules on, so an unreadable file is
        found here rather than as a step that never completes. It doubles as the
        check that what shipped is a file this user agent can play: 8 kHz, mono,
        16-bit, which is what a narrowband call carries.
        """
        paths = [intercom.name_audio for intercom in self.config.intercoms]
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
            if channels != 1 or width != 2:
                raise AudioMissing(
                    f"{path} is {channels} channel(s) at {width * 8} bits. Every file this "
                    "agent plays is mono 16-bit PCM, which is what a narrowband call "
                    "carries."
                )
            self._durations[path] = frames / rate if rate else 0.0

    def _every_line(self):
        from .lines import DRIVER_LINES, OPERATOR_LINES

        for line in DRIVER_LINES:
            yield line, self.config.driver_languages
        for line in OPERATOR_LINES:
            yield line, (self.config.operator_language,)

    # -- the loop ----------------------------------------------------------

    def poll(self) -> None:
        """Everything the user agent said, then everything that is due."""
        try:
            events = self.ua.poll()
            self._code(AgentCode.UA_UNREACHABLE, self.config.agent_id, HealthState.OK)
        except UaUnreachable as exc:
            # The agent is up and cannot work the phone. It is the loudest thing
            # it has to say, and it is its own measurement: nothing else can
            # make it, because a UA that is down cannot report that it is down.
            log.error("the user agent is unreachable: %s", exc)
            self._code(AgentCode.UA_UNREACHABLE, self.config.agent_id, HealthState.ACTIVE)
            return
        for event in events:
            self._handle(event)
        self._registration_state()
        self._advance()

    def _registration_state(self) -> None:
        registered = self.ua.registered()
        state = (
            HealthState.UNKNOWN
            if registered is None
            else (HealthState.OK if registered else HealthState.ACTIVE)
        )
        self._code(
            AgentCode.SIP_REGISTRATION_LOST, self.config.user_agent.driver_aor, state
        )

    def _handle(self, event: UaEvent) -> None:
        if event.kind is UaEventKind.CALL_INCOMING:
            return self._incoming(event)
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
                    self._nothing_usable(session)
            return
        if event.kind is UaEventKind.DTMF and event.call_id == session.operator_call:
            # ONLY the operator's leg. A digit from the driver is not an
            # authorisation, and the call it arrived on is the only thing that
            # separates them -- which is why the seam carries it.
            self._digit(session, event.digit or "")

    # -- answering ---------------------------------------------------------

    def _incoming(self, event: UaEvent) -> None:
        uri = _bare_uri(event.peer_uri)
        intercom = self._by_uri.get(uri)
        if intercom is None:
            # ONE fixed message and the call ends. The agent never guesses a
            # lane: a guess here is a guess about which barrier somebody is
            # standing at.
            self._code(
                AgentCode.CALL_FROM_UNDECLARED_INTERCOM,
                uri or "unknown",
                HealthState.ACTIVE,
            )
            self._record(AgentEventKind.CALL_FROM_UNDECLARED_INTERCOM, intercom=uri or "unknown")
            if event.call_id:
                self.ua.answer(event.call_id)
                session = Session(
                    intercom=Intercom(sip_uri=uri or "unknown", lane=None, name_audio=Path()),
                    driver_call=event.call_id,
                    started=self._clock(),
                    state=State.CLOSING,
                    languages=self.config.driver_languages,
                )
                self.session = session
                self._say(session, UaLeg.DRIVER, "driver.undeclared_intercom")
            return
        if self.session is not None:
            # NOT answered. An unanswered call is what makes the intercom's own
            # call list move on to the human's number; answering it to say "busy"
            # would take that fall-through away.
            self._record(AgentEventKind.CALL_REFUSED_BUSY, intercom=uri or "unknown")
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
            AgentCode.CALL_FROM_UNDECLARED_INTERCOM, uri or "unknown", HealthState.OK
        )
        self._record(AgentEventKind.CALL_ANSWERED, intercom=intercom.sip_uri,
                     lane=intercom.lane)
        session.case = derive(self._read_lane(intercom))
        self._record(AgentEventKind.CASE_SPOKEN, intercom=intercom.sip_uri,
                     lane=intercom.lane, case=session.case.value)
        self._say(session, UaLeg.DRIVER, f"case.{session.case.value}")
        if session.case is AgentCase.NOTHING_TO_DO:
            session.state = State.CLOSING
        else:
            session.state = State.SPEAKING_CASE

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
            transit=_string(transit, "state"),
            malfunctions=tuple(malfunctions),
        )

    # -- the human ---------------------------------------------------------

    def _call_human(self, session: Session) -> None:
        session.state = State.CALLING_HUMAN
        session.deadline = self._clock() + self.config.no_answer_seconds
        try:
            session.operator_call = self.ua.dial(
                self.config.human_sip_uri, UaLeg.OPERATOR
            )
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
        """Record it, tell both sides what it means, and open nothing."""
        session.authorisation = value
        if value is Authorisation.CALL_BACK:
            session.state = State.COLLECTING_NUMBER
            session.keyed = ""
            session.deadline = self._clock() + self.config.nothing_usable_seconds
            self._say(session, UaLeg.OPERATOR, "operator.menu_callback_number", operator=True)
            return
        self._record_authorisation(session, value, None)
        if value in CANNOT_ACT:
            # The one fixed sentence the person keying it hears. A human who
            # believes a barrier moved when it did not is worse off than one who
            # was never called.
            self._say(session, UaLeg.OPERATOR, "operator.cannot_open", operator=True)
        self._say(session, UaLeg.DRIVER, f"authorisation.{value.value}")
        if value is Authorisation.HOLD:
            session.state = State.HOLDING
            session.deadline = self._clock() + self.config.hold_reprompt_seconds
            session.prompts_left = REPROMPTS
            return
        session.state = State.CLOSING

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

    def _nothing_usable(self, session: Session) -> None:
        self._record(
            AgentEventKind.NOTHING_USABLE,
            intercom=session.intercom.sip_uri,
            lane=session.intercom.lane,
            case=session.case.value if session.case else None,
            human=self.config.human_sip_uri,
        )
        self._say(session, UaLeg.DRIVER, "driver.nothing_usable")
        session.state = State.CLOSING
        session.deadline = None

    # -- the clock ---------------------------------------------------------

    def _advance(self) -> None:
        session = self.session
        if session is None:
            return
        now = self._clock()
        self._speak(session, now)
        if session.state is State.SPEAKING_CASE and self._silent(session, UaLeg.DRIVER, now):
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
        if session.state is State.CLOSING and self._silent(session, UaLeg.DRIVER, now):
            self.ua.hangup_all()
            self._end(session)

    def _speak(self, session: Session, now: float) -> None:
        for leg in (UaLeg.DRIVER, UaLeg.OPERATOR):
            if session.free_at[leg] > now or not session.speech[leg]:
                continue
            call_id = session.call_of(leg)
            if call_id is None or call_id not in session.live:
                continue
            path = session.speech[leg].popleft()
            try:
                self.ua.play(call_id, str(path))
            except UaRefused as exc:
                # It answered, and said no. Put the line BACK and try again next
                # poll: the usual reason is that the call's audio stream has not
                # come up yet, which is a fifth of a second away. A driver who is
                # never told their case has been told nothing, so the line is not
                # dropped -- and no code goes `active`, because the agent is
                # working the phone perfectly well.
                log.debug("the user agent refused %s for now: %s", path, exc)
                session.speech[leg].appendleft(path)
                return
            except UaUnreachable as exc:
                log.error("could not play %s: %s", path, exc)
                session.speech[leg].appendleft(path)
                self._code(AgentCode.UA_UNREACHABLE, self.config.agent_id, HealthState.ACTIVE)
                return
            session.free_at[leg] = now + self._durations.get(path, 0.0)

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
                IntercomDescription(sip_uri=intercom.sip_uri, lane=intercom.lane)
                for intercom in self.config.intercoms
            ),
            user_agent=UserAgentDescription(
                kind=self.config.user_agent.kind,
                version=self._ua_version,
                tested_versions=_tested_versions(self.ua),
                registered=self.ua.registered(),
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
    """A SIP identity, without a display name, parameters or a port.

    An intercom's `From` header is not stable to the character: a unit may send
    `"Door 1" <sip:door1@10.0.0.9:5060>;tag=…` on one call and
    `sip:door1@10.0.0.9` on the next, and a mapping keyed on the whole string
    would answer one of them `this intercom is not configured`. Reduced by
    SHAPE, once, here -- so a site declares `sip:door1@10.0.0.9` and the
    declaration means the same thing every time.
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
    "KNOWN_LANE_VERSIONS",
    "REPROMPTS",
    "Agent",
    "AudioMissing",
    "Session",
    "State",
    "utc_now",
]
