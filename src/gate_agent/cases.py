"""Why a driver is at the intercom, DERIVED from the lane's own payload.

One pure function, `derive()`, and a table in `docs/CONTRACT.md` with a test per
row. It is pure on purpose: the case decides what a driver hears, and a decision
that depended on the agent's mood, its clock or its previous call would be one
nobody could reproduce from a lane's payload afterwards.

**The case is never asked.** A driver at a barrier does not know whether the
identification service is down, their plate was marginal, or the loop read
nothing, and a menu offering them the choice would be a guess with a keypad.

**Two rules run through every branch.**

*Never wrong silently.* A lane answer this build will not interpret ends with a
human and says so -- it is never mapped onto the nearest thing we know. That is
the lane contract's own instruction to a consumer, and it is the seam where
guessing costs the most.

*A dead engine is not a marginal read.* `engine_unreachable` and
`low_confidence` arrive as different reasons and leave as different cases,
because the second one tells a driver to clean their number plate and the first
one must never do that with the service switched off.

*A decision has an AGE, and a stale one never ends a call.* The lane publishes
`decision.at` and this function is given a clock, so a decision older than the
site's `[cases] decision_max_age_seconds` becomes `stale_decision` -- a person --
whatever it said. `nothing_to_do` is the only case in the set that reaches
nobody, so it is the only one where being wrong costs the customer everything,
and it is reachable ONLY from a FRESH `allow`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .contract import AgentCase

#: The reasons a lane MUST be able to emit, and the subset an agent may branch
#: on. **This is a COPY, and it is a copy because it has to be.** The lane
#: contract publishes its outcomes, its transit states and its malfunction codes
#: in full and deliberately does NOT publish this set -- so a consumer written
#: from that document alone cannot know it, and this package may not import
#: `lane_controller` to find out.
#:
#: The copy is held to the real lane by `tests/test_agent_cases.py`, which reads
#: `lane_controller.contract.REQUIRED_REASONS` out of the INSTALLED package and
#: requires the two to be equal in both directions. A reason added there without
#: an answer here goes red, and so does a reason invented here.
#:
#: Anything outside it is `unrecognised_reason`, which is a human.
REQUIRED_FALLBACK_REASONS: tuple[str, ...] = (
    "low_confidence",
    "no_plate_read",
    "unknown_vehicle",
    "stale_rules",
    "engine_unreachable",
)

#: The outcomes, closed by the lane contract and published in it. A lane that
#: answered a fifth would leave this function with a case it has no behaviour
#: for, which is why that set is closed and this one is not derived from a guess.
OUTCOMES: tuple[str, ...] = ("allow", "deny", "fallback", "no_vehicle")

#: The transit states, closed by the lane contract and published in it.
TRANSIT_STATES: tuple[str, ...] = (
    "pending",
    "confirmed",
    "held",
    "backed_out",
    "unconfirmable",
    "none",
)

#: WHICH CASE each required fallback reason becomes. One copy, and the payload
#: is derived from it, so a reason cannot gain a case in one place and keep
#: another somewhere else.
FALLBACK_CASES: dict[str, AgentCase] = {
    "engine_unreachable": AgentCase.IDENTIFICATION_UNAVAILABLE,
    "no_plate_read": AgentCase.PLATE_NOT_READ,
    "low_confidence": AgentCase.PLATE_UNCLEAR,
    "unknown_vehicle": AgentCase.VEHICLE_NOT_RECOGNISED,
    "stale_rules": AgentCase.RULES_UNAVAILABLE,
}

#: The published default for `[cases] decision_max_age_seconds`, and it is a
#: SETTING with a default rather than a constant because it is an ASSUMPTION:
#: **nothing has measured how long a lane decision stays the same car's.** Two
#: minutes is drawn from a person walking from a stopped car to a door station
#: and pressing a button, which is a guess about people, not a measurement of
#: them. A site whose lane is thirty metres from its intercom raises it; a busy
#: one lowers it. What is NOT a guess is which way the error falls: past the
#: bound the driver gets a person, which is the answer every other case in the
#: set already gets.
DEFAULT_DECISION_MAX_AGE_SECONDS = 120.0

#: The transit states that mean the lane could not say whether the vehicle
#: actually went through. `held` is neither a confirmation nor a refutation and
#: `unconfirmable` is an ordinary lane with no closing loops -- a third party's
#: usually is one.
UNCONFIRMED_TRANSITS: tuple[str, ...] = ("held", "unconfirmable")


@dataclass(frozen=True, slots=True)
class LaneReading:
    """What the agent got from a lane, as the case function sees it.

    Everything here comes off the wire. `readable` is false when the lane did
    not answer, refused us, published a contract version this build cannot read,
    or published a payload that version does not describe -- to a driver at a
    barrier those are one fact, and it is that the lane cannot be asked.
    """

    #: `None` for a STANDALONE intercom: there is no lane to read.
    lane: str | None
    readable: bool = False
    #: The lane's `decision`, or `None` when it has not decided anything. A lane
    #: keeps no state store, so this is the honest answer after a restart and it
    #: is NOT the same thing as "nothing has ever happened here".
    outcome: str | None = None
    reason: str | None = None
    transit: str | None = None
    #: `decision.at` off the wire, exactly as the lane published it. A STRING,
    #: not a moment: parsing it is where it can turn out to be unreadable, and
    #: that is a fact about the answer this build was given rather than
    #: something to be lost in a conversion before anybody can branch on it.
    decision_at: str | None = None
    #: The codes the lane published as `active` with `never_alarm` FALSE, as it
    #: published them. Filtered where the payload is read, not here, because
    #: `never_alarm` travels on the wire with the code and this package holds no
    #: list of its own.
    malfunctions: tuple[str, ...] = ()


#: The one copy of what the comparison in `derive()` spans, published from here
#: into `docs/CONTRACT.md` so the two cannot come apart. Same shape and same
#: reason as the capture process's `capture_minus_lane_event_ms` note.
DECISION_AGE_NOTE = (
    "This is a COMPARISON ACROSS TWO CLOCKS: `decision.at` is read from the "
    "LANE's clock and `now` from this process's. It is not a measured age. A "
    "NEGATIVE AGE IS REACHABLE -- a decision stamped after the moment this "
    "process reads it -- and it is treated as FRESH, because the alternative is "
    "sending a driver to a person on the strength of a clock offset nobody has "
    "measured. Nothing here measures the offset between the two, so nothing "
    "here can separate it from the age it is trying to read. Where the two "
    "clocks are the same box, or are disciplined to the same source, this is "
    "the cost of being a CONSUMER of the lane's contract rather than something "
    "the lane calls."
)


def _age_seconds(stamp: str | None, now: datetime) -> float | None:
    """How old the lane says its decision is, or `None` if it cannot be read.

    `None` is not zero and it is not old: it is "this build cannot tell", which
    is the catch-all case and a person. A stamp with no timezone is UNREADABLE
    here rather than assumed to be UTC -- the round-4 rule, and the same reason:
    a naive moment compared against an aware one is a guess about which machine
    it came from.
    """
    if not isinstance(stamp, str) or not stamp:
        return None
    try:
        moment = datetime.fromisoformat(stamp)
    except ValueError:
        return None
    if moment.tzinfo is None:
        return None
    return (now - moment).total_seconds()


def derive(
    reading: LaneReading,
    now: datetime,
    max_age_seconds: float = DEFAULT_DECISION_MAX_AGE_SECONDS,
) -> AgentCase:
    """The case, from one lane reading AND A CLOCK. Pure, total, and ordered.

    `now` is passed in rather than read here, and it has no default: a function
    that reached for the clock itself would be one whose answer cannot be
    reproduced from a payload, and a DEFAULT clock is a function that silently
    calls every decision fresh on the day somebody forgets to pass one.

    The order is the table's and it is load-bearing:

      * **standalone first** -- there is no lane, so nothing below can be asked;
      * **then whether the lane could be read at all** -- an unreadable lane
        cannot have a decision interpreted out of it;
      * **then a malfunction** -- a broken lane's last decision is not a fact
        about the vehicle standing at it, whatever that decision was;
      * **then the AGE of the decision** -- a decision the lane made for
        somebody else is not a fact about this driver either, and this is the
        only guard in front of `nothing_to_do`, the one case that reaches
        nobody;
      * **then the outcome**, and only inside `allow` does the transit decide
        anything: under `deny` and `no_vehicle` there was no vend, so there is
        nothing for closing loops to have confirmed.
    """
    if reading.lane is None:
        return AgentCase.STANDALONE
    if not reading.readable:
        return AgentCase.LANE_UNAVAILABLE
    if reading.malfunctions:
        return AgentCase.MALFUNCTION_ACTIVE
    if reading.outcome is not None:
        # There IS a decision, so it has an age, and this build either knows it
        # or does not. Ahead of every outcome branch, because the age decides
        # whether any of them is about the person standing at the barrier.
        age = _age_seconds(reading.decision_at, now)
        if age is None:
            # A decision with no readable moment on it. The catch-all, for the
            # same reason as every other answer this build will not interpret.
            return AgentCase.UNRECOGNISED_REASON
        if age > max_age_seconds:
            return AgentCase.STALE_DECISION
    if reading.outcome == "fallback":
        case = FALLBACK_CASES.get(reading.reason or "")
        # A reason outside the required subset is NOT mapped onto the nearest
        # thing we know. A lane that is not ours has its own vocabulary and
        # will emit it, and guessing here would tell a driver to do something
        # about a fault this build invented for them.
        return case if case is not None else AgentCase.UNRECOGNISED_REASON
    if reading.outcome == "deny":
        return AgentCase.ENTRY_REFUSED
    if reading.outcome == "no_vehicle":
        # The wrongly-refused real car. There is a driver at the barrier and the
        # lane believes the lane is empty; the presence gate is unvalidated on
        # real vehicles and this is that customer's only recourse.
        return AgentCase.VEHICLE_NOT_DETECTED
    if reading.outcome == "allow":
        if reading.transit in UNCONFIRMED_TRANSITS:
            return AgentCase.ENTRY_NOT_CONFIRMED
        if reading.transit in ("confirmed", "pending"):
            return AgentCase.NOTHING_TO_DO
    # Everything else the lane can answer and this build will not interpret: no
    # decision at all, an outcome outside the closed set, or an `allow` with a
    # transit that has no row -- a vehicle that backed out, or a lane that has
    # vended nothing since it restarted. **The catch-all is an ESCALATION, and
    # it is deliberately the same case as an unrecognised reason**: to a driver
    # they are one fact -- the lane said something we will not act on -- and a
    # case per shape of unreadable answer would be a set nobody can behave
    # differently about.
    return AgentCase.UNRECOGNISED_REASON


__all__ = [
    "DECISION_AGE_NOTE",
    "DEFAULT_DECISION_MAX_AGE_SECONDS",
    "FALLBACK_CASES",
    "OUTCOMES",
    "REQUIRED_FALLBACK_REASONS",
    "TRANSIT_STATES",
    "UNCONFIRMED_TRANSITS",
    "LaneReading",
    "derive",
]
