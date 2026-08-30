"""The SIP seat: what the agent needs a user agent to do, and nothing more.

**There is no SIP stack in this package and there will not be one.** SIP, RTP,
DTMF (RFC 4733) and audio are somebody else's twenty years of work, and every
one of them is a protocol that fails in ways nobody writing a gate agent has
seen. So the agent drives an EXTERNAL user agent -- its own process, its own
package, driven over a LOCAL control socket -- and holds nothing but the
dialogue state and the records.

This module is the seam, and it is deliberately seven verbs wide:

    version / registered   who the UA is and whether it is registered
    accounts               which local accounts the UA holds
    answer / dial / hangup which calls exist
    calls                  which calls the UA is holding RIGHT NOW
    play                   one audio file into ONE CALL, named by its id
    bridge                 both calls hear each other, from this moment
    poll                   what happened, as this package's own event set

**`accounts` is here because the LOCAL ACCOUNT a call arrived at is what says
which intercom it is.** Not the `From` header: a `From` is a string the caller
writes. Each declared intercom is given its own account, with a user part only
that intercom's installer knows, so an inbound call is that intercom if and only
if it arrived AT that account. `accounts` is how the agent refuses to start when
one of those accounts is not there -- which would otherwise be a door whose calls
are answered `404 Not Found` at three in the morning and nowhere else.

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

    The two legs sit on DIFFERENT local accounts. The driver's is whichever
    intercom account the call arrived at -- one per declared intercom -- and the
    operator's is the one account this agent dials out from, declared as
    `[user_agent] operator_aor`. Startup refuses an operator account that
    collides with an intercom's, because two calls on one account cannot be told
    apart and the menu meant for the person on the phone would play to the
    driver at the barrier.
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
    #: An INVITE the USER AGENT ITSELF refused, because its request-URI named no
    #: account the user agent holds. **There is no call**: it was answered
    #: `404 Not Found` before this process saw anything, which is the ordinary
    #: fate of a caller who does not know an intercom's dial address. It is on
    #: the seam because the alternative is that the commonest kind of unwanted
    #: caller leaves no trace at all. It carries `peer_uri` -- what that caller
    #: claimed to be -- and nothing else, because there is nothing else.
    CALL_TO_UNKNOWN_ACCOUNT = "call_to_unknown_account"


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
    #: The USER PART of the local account this call arrived at, as the user
    #: agent reports it after the fact. `None` when the user agent did not say.
    #: It is the same question `UaEvent.account_user` answers and it is asked
    #: separately because the events for a call that arrived while the socket
    #: was down went to nobody -- and without it a recovered call cannot be
    #: placed at an intercom, so it is refused rather than guessed at.
    account_user: str | None = None


@dataclass(frozen=True, slots=True)
class UaEvent:
    kind: UaEventKind
    call_id: str | None = None
    #: The SIP identity the OTHER END CLAIMED -- its `From` header. **It decides
    #: nothing.** A `From` is a string the caller writes, so it is recorded as
    #: `caller_stated_identity` and never routed on; what says which intercom a
    #: call is is `account_user` below.
    peer_uri: str | None = None
    #: The USER PART of the LOCAL account this call arrived at. This is the
    #: identification: each declared intercom has an account of its own whose
    #: user part only that intercom's installer knows, so a call at that account
    #: is that intercom and a caller who cannot dial it reaches nothing.
    account_user: str | None = None
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
