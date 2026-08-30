"""The SIP seat: what the agent needs a user agent to do, and nothing more.

**There is no SIP stack in this package and there will not be one.** SIP, RTP,
DTMF (RFC 4733) and audio are somebody else's twenty years of work, and every
one of them is a protocol that fails in ways nobody writing a gate agent has
seen. So the agent drives an EXTERNAL user agent -- its own process, its own
package, driven over a LOCAL control socket -- and holds nothing but the
dialogue state and the records.

This module is the seam, and it is deliberately six verbs wide:

    version / registered   who the UA is and whether it is registered
    answer / dial / hangup which calls exist
    calls                  which calls the UA is holding RIGHT NOW
    play                   one audio file into ONE CALL, named by its id
    bridge                 both calls hear each other, from this moment
    poll                   what happened, as this package's own event set

**`calls` is here because a control socket can be LOST.** Everything else on
this seam is a report of something that happened while the agent was listening;
`calls` is the one question that has to be asked after it was not. A call that
arrived while the socket was down produced an event nobody received, and the
only way to find out whether somebody is still ringing at the door is to ask.

**`bridge` is a verb and not a mode.** The agent has to be able to speak to the
operator PRIVATELY -- the case, and a menu of digits -- and only then put the two
together. A user agent that mixes every call the moment both exist cannot do
this round's job, and that is why the seam names bridging as something the agent
asks for at a moment of its choosing.

**A blind transfer is not on this seam.** `REFER` hands a call away and the agent
is no longer in it, so there is nothing left to receive an authorisation with --
the whole reason the L1 asked for a back-to-back user agent. `TRANSFER` here is
an authorisation this version RECORDS, not a SIP method it sends.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class UaUnreachable(Exception):
    """The user agent's control socket did not answer, or answered unreadably.

    ONE exception for both, the way `TargetUnreachable` is one for a target: to
    an agent they are the same fact -- there is a driver at a barrier and this
    process cannot work the phone.
    """


class UaRefused(UaUnreachable):
    """The user agent ANSWERED, and what it answered was no.

    Not the same fact as a dead control socket, and the difference decides
    whether a human is paged. The commonest one is benign and is a moment away
    from resolving: a file played into a call whose audio stream has not come up
    yet is refused, and the same command a fifth of a second later succeeds.
    Reported as `ua_unreachable`, that transient would put a code on the health
    surface saying the agent cannot work the phone while it is working the phone.

    It is a SUBCLASS because for most verbs the two really are one fact -- a
    dial that was refused and a dial that could not be sent both mean the person
    was not called -- so every caller that does not care about the difference
    keeps working. The one that cares catches this first.
    """


class UaMisconfigured(Exception):
    """The user agent is running on a configuration this agent cannot work on.

    Read back out of the process at startup, not assumed from a file this
    package does not own. A separate exception from `UaUnsupportedVersion`
    because it is a separate fact -- the right program, set up wrongly -- and a
    site reading the message needs to be told which setting, by name.
    """


class UaUnsupportedVersion(Exception):
    """The user agent is a version this build was never tested against.

    The `schema_version` rule, applied to a process. Raised at startup, before a
    registration is attempted, because a UA whose control vocabulary this build
    has guessed at is a UA that will answer a call and then do something else.
    """


class UaLeg(StrEnum):
    """Which side of the case a call is. Two, and there is never a third.

    A user agent identifies the stream it plays into by the local account, not
    by the call, so the two legs are placed on two accounts -- which is also how
    baresip's own back-to-back module does it. `agent.py` never learns that;
    it asks for a leg.
    """

    DRIVER = "driver"
    OPERATOR = "operator"


class UaEventKind(StrEnum):
    """What the seam reports. CLOSED, and it is THIS package's vocabulary.

    A user agent's own event names do not appear above this line. The adapter
    translates, and anything it does not recognise is dropped rather than passed
    through under a name the agent would have to guess at.
    """

    CALL_INCOMING = "call_incoming"
    CALL_ESTABLISHED = "call_established"
    #: The call's MEDIA is up -- RTP is flowing. Separate from
    #: `CALL_ESTABLISHED` because they are separate moments and the gap between
    #: them is a whole sentence: a user agent refuses a file played into a call
    #: whose audio stream does not exist yet, and the driver hears the first
    #: thing they are told not at all.
    CALL_MEDIA = "call_media"
    CALL_CLOSED = "call_closed"
    #: One DTMF digit, with the call it arrived on. The call matters: a digit
    #: from the DRIVER's leg is not an authorisation, and the only thing that
    #: separates them is which call it came in on.
    DTMF = "dtmf"
    REGISTERED = "registered"
    REGISTRATION_LOST = "registration_lost"


@dataclass(frozen=True, slots=True)
class UaCall:
    """One call the user agent is holding, as `calls()` reports it.

    `ringing` is the whole point of this type: a call that has ARRIVED and has
    not been answered is the one an agent that has just got its socket back can
    still do something about. Everything else it can only hang up.
    """

    call_id: str
    peer_uri: str | None
    ringing: bool


@dataclass(frozen=True, slots=True)
class UaEvent:
    kind: UaEventKind
    call_id: str | None = None
    #: The SIP identity at the other end. For an incoming call this is the
    #: INTERCOM, and it is the only thing that says which lane the call is
    #: about -- see `[intercoms.*]`.
    peer_uri: str | None = None
    digit: str | None = None


__all__ = [
    "UaCall",
    "UaEvent",
    "UaMisconfigured",
    "UaRefused",
    "UaEventKind",
    "UaLeg",
    "UaUnreachable",
    "UaUnsupportedVersion",
]
