"""A user agent that records what it was asked to do, and can be made to fail.

The real one is baresip and it is exercised for real in `test_agent_sip.py`,
against a real registrar, with real RTP -- because a fake shaped like a SIP stack
would make "the agent answers an intercom" a claim about this file.

What this is for is the DIALOGUE: which sentence is played to which leg, in which
order, when the two are put together, and what is recorded. Those are questions
about the agent, and asking them through a real UA would measure the media path
instead. So this records commands and hands events back, and every test that uses
it asserts on the RECORD rather than on the agent's own description of itself.

It is also the only place a **planted act** can come from. The agent has no verb
that opens anything and neither does this; the control for that is a fake that
DOES, so the assertion "nothing opened" is about the world rather than about a
search that found nothing.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from gate_agent.ua import (
    UaCall,
    UaEvent,
    UaEventKind,
    UaLeg,
    UaRefused,
    UaUnreachable,
    UaUnsupportedVersion,
)


@dataclass
class FakeUa:
    """Records every verb. Answers `poll()` from whatever a test has queued."""

    version_string: str = "4.11.0"
    tested_versions: tuple[str, ...] = ("4.11.0",)
    is_registered: bool | None = True
    #: (verb, argument) for everything asked of it, in order.
    commands: list[tuple[str, str]] = field(default_factory=list)
    #: (leg, path) for every file played, in order. The whole of "who heard
    #: what, and in which order" is here.
    played: list[tuple[str, str]] = field(default_factory=list)
    #: When `bridge()` was called, as a position in `commands`. `None` while the
    #: two legs are still private -- which is what a test asserts against when
    #: it checks the operator was briefed before anybody was put together.
    bridged_at: int | None = None
    events: list[UaEvent] = field(default_factory=list)
    #: What `calls()` reports. A test that asks what a reopened socket found
    #: puts `UaCall`s here.
    held: list = field(default_factory=list)
    #: Call ids this fake PLACED, which are the operator's leg by construction.
    dialled: set = field(default_factory=set)
    #: A test sets this to make the next verb fail the way a dead UA does.
    unreachable: bool = False
    #: A test sets this to make every `play` be REFUSED -- which is what a real
    #: user agent does for a file it cannot decode, an audio mode it will not
    #: play into, or a mixer stuck in a mode it cannot leave. It ANSWERS: the
    #: call is up and the driver is on it, hearing nothing. That is a different
    #: fact from `unreachable`, and the difference is whether anybody is paged.
    refuse_play: bool = False
    next_call_id: int = 100
    #: What `accounts()` answers. `None` means "whatever the configuration
    #: declares", which `conftest` fills in -- a fake that always answered the
    #: right thing could not measure the refusal, and one that always answered
    #: nothing could not measure anything else.
    held_accounts: tuple[str, ...] | None = None
    declared_accounts: tuple[str, ...] = ()

    def start(self) -> None:
        self.commands.append(("start", ""))
        if self.version_string not in self.tested_versions:
            raise UaUnsupportedVersion(
                f"the user agent is baresip {self.version_string!r}; this build was tested "
                f"against {self.tested_versions}"
            )

    def _check(self) -> None:
        if self.unreachable:
            raise UaUnreachable("the user agent's control socket is not open")

    def version(self) -> str:
        return self.version_string

    def registered(self) -> bool | None:
        return self.is_registered

    def answer(self, call_id: str) -> None:
        self._check()
        self.commands.append(("answer", call_id))
        # A real user agent answers and THEN establishes the media, and the
        # agent may not play into a call whose audio stream does not exist yet.
        # A fake that skipped this step would let a defect through that a driver
        # hears as a missing first sentence.
        self.established(call_id)

    def dial(self, uri: str) -> str:
        self._check()
        self.next_call_id += 1
        call_id = f"call-{self.next_call_id}"
        self.dialled.add(call_id)
        self.commands.append(("dial", f"{UaLeg.OPERATOR.value} {uri} -> {call_id}"))
        return call_id

    def play(self, call_id: str, path: str) -> None:
        self._check()
        if self.refuse_play:
            raise UaRefused(f"the user agent refused `mixausrc_enc_start`: {path!r}")
        leg = self.leg_of(call_id)
        self.commands.append(("play", f"{leg} {path}"))
        self.played.append((leg, path))

    def stop_playing(self, call_id: str) -> None:
        self.commands.append(("stop_playing", self.leg_of(call_id)))

    def leg_of(self, call_id: str) -> str:
        """Which leg a call is, from the calls THIS fake placed.

        The seam names a call, not a leg -- the real user agent identifies an
        audio stream by the call and nothing else -- so the fake works out which
        side it was for the benefit of the tests, from the ones it dialled.
        """
        return UaLeg.OPERATOR.value if call_id in self.dialled else UaLeg.DRIVER.value

    def bridge(self) -> None:
        self._check()
        self.bridged_at = len(self.commands)
        self.commands.append(("bridge", ""))

    def hangup(self, call_id: str) -> None:
        self.commands.append(("hangup", call_id))
        # AND THE CALL IS GONE. A fake that recorded the command and went on
        # holding the call could not measure a release at all: every assertion
        # about what is still up would read the same before and after.
        self.held = [one for one in self.held if one.call_id != call_id]

    def hangup_all(self) -> None:
        self.commands.append(("hangup_all", ""))
        self.held = []

    #: The calls this fake is holding, for a test that asks what a reopened
    #: control socket found. Set by a test; empty otherwise.
    def calls(self) -> tuple[UaCall, ...]:
        self._check()
        return tuple(self.held)

    def poll(self) -> tuple[UaEvent, ...]:
        self._check()
        events, self.events = tuple(self.events), []
        return events

    def close(self) -> None:
        """The seam the real adapter closes its control socket in.

        Here because the fake stands in for the WHOLE seam: `cli.cmd_agent`
        calls this on the way out, and a fake missing one verb makes an exit
        path untestable by construction.
        """
        self.commands.append(("close", ""))

    def reconnect(self) -> tuple[UaCall, ...]:
        """The seam's answer to a socket that was lost. Empty when nothing was.

        The real adapter refuses to work at all until this succeeds, because its
        socket is gone; `LosableUa` in `test_agent_contract.py` models that, and
        this base fake never loses one.
        """
        return ()

    # -- what a test drives it with ---------------------------------------

    def incoming(
        self, peer_uri: str, call_id: str = "call-1", *, account_user: str | None
    ) -> None:
        """A call arriving. **`account_user` has no default and that is the point.**

        It is the account the call came in AT, which is the only thing that says
        which intercom it is. A default here would be a fixture deciding the
        answer to the question every test using it is asking, and every test
        that means to exercise the answered path would go on passing if the
        agent stopped checking the account at all.
        """
        self.events.append(
            UaEvent(
                kind=UaEventKind.CALL_INCOMING,
                call_id=call_id,
                peer_uri=peer_uri,
                account_user=account_user,
            )
        )

    def refused_unknown_account(self, peer_uri: str) -> None:
        """What a user agent reports when it answered an INVITE `404` itself.

        There is no call and there never was one: the request-URI named no
        account it holds. Measured on baresip 4.11.0 as a `SIPSESS_FAILED`
        carrying `404 Not Found` and the caller's `From`.
        """
        self.events.append(
            UaEvent(kind=UaEventKind.CALL_TO_UNKNOWN_ACCOUNT, peer_uri=peer_uri)
        )

    #: The user parts of the accounts this fake is holding. A test that means to
    #: measure the startup refusal sets this to something that does not cover
    #: what the configuration declares.
    def accounts(self) -> tuple[str, ...]:
        if self.held_accounts is None:
            return tuple(self.declared_accounts)
        return tuple(self.held_accounts)

    def established(self, call_id: str, media: bool = True) -> None:
        """Answered, and -- unless a test says otherwise -- media up too.

        Two events, because a real user agent sends two and the gap between them
        is a whole sentence. `media=False` is how a test asks what happens in
        that gap.
        """
        self.events.append(UaEvent(kind=UaEventKind.CALL_ESTABLISHED, call_id=call_id))
        if media:
            self.events.append(UaEvent(kind=UaEventKind.CALL_MEDIA, call_id=call_id))

    def closed(self, call_id: str) -> None:
        self.events.append(UaEvent(kind=UaEventKind.CALL_CLOSED, call_id=call_id))

    def dtmf(self, call_id: str, digit: str) -> None:
        self.events.append(UaEvent(kind=UaEventKind.DTMF, call_id=call_id, digit=digit))

    def registration_lost(self) -> None:
        self.is_registered = False
        self.events.append(UaEvent(kind=UaEventKind.REGISTRATION_LOST))


class ActingUa(FakeUa):
    """A user agent that CAN open a barrier. The positive control, and only that.

    `tests/test_agent_no_opening_authority.py` runs the same `OPEN_NOW` dialogue
    against this and against `FakeUa`, and requires the barrier to move here and
    not there. Without it, "nothing opened" would be a statement about a search
    that found nothing rather than about the world.
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.barrier_moved = 0

    def vend(self) -> None:
        self.barrier_moved += 1
        self.commands.append(("vend", ""))
