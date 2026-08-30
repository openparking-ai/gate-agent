"""The adapter for baresip, and the ONLY module here that opens a socket to it.

baresip's `ctrl_tcp` module is a JSON control channel: netstring-framed messages
in both directions, commands with an optional token echoed on the response, and
an asynchronous stream of events. This module speaks that and translates it into
the six verbs and six event kinds of `ua.py`. Nothing above this line knows the
word "baresip".

**Which baresip, and why it is the one.** Of the user agents that exist as a
process and can be driven over a local socket, this is the one that does all six
things the job needs -- register, answer, play a file into ONE call, receive DTMF
tagged with the call it arrived on, hold two calls at once and BRIDGE them on
command, and be driven over a socket that can be bound to loopback. Its licence
is BSD-3-Clause, which is the least demanding of any candidate. The findings on
the others are in the receipt for this round.

**What is load-bearing on the baresip side is CONFIGURATION, not code, and
`_check_configuration` reads it back OUT OF THE RUNNING PROCESS at startup.**
It used to say it was checked here and it was not: `grep -rn aubridge` over this
repository returned four hits and not one of them was code, while
`config/agent.example.toml` said the opposite -- correctly -- three files away.
Two copies of a claim, and the hand-written one was the one that lied.

Measured on baresip 4.11.0: `ctrl_tcp` answers `config` with the LOADED
configuration -- `audio_source`, `audio_player` and `call_hold_other_calls` all
appear in it by name -- and `modules` with the loaded module list by name. Both
commands come from the `debug_cmd` module, which is therefore a fifth install
requirement and is named in `docs/CONTRACT.md` beside the others. So this build
refuses to start, BY NAME:

  * against `aubridge` on either audio device -- that driver loops the player
    back into the source, which bridges every call to every other one whether
    the agent asked for it or not;
  * against `call_hold_other_calls yes` -- baresip's default is to hold every
    other call when a new one is established, so with it on, calling the
    operator puts the driver on hold under this process rather than because it
    asked;
  * with `mixminus`, `mixausrc` or `ctrl_tcp` missing -- `mixausrc` is what
    plays one file into ONE leg and `mixminus` is what the bridge is;
  * when `config` or `modules` is itself refused, naming `debug_cmd`, because a
    check that quietly did not run is the thing this replaced.

The fourth load-bearing fact is TWO accounts, one per leg -- baresip identifies
the stream to play into by the RTP CNAME, so two calls on ONE account cannot be
told apart and a menu meant for the operator plays to the driver. That one is
the SITE's file rather than the user agent's, and `config.py` refuses the pair
equal at startup.

**It cannot reach a lane.** This module holds no target, no lane URL and no
credential: it is given a host and a port that are the UA's, and
`tests/test_no_opening_authority.py` requires that it import neither the target
client nor the `Target` type -- the same rule, and the same sweep, that keeps the
webhook sink from being able to reach one.
"""

from __future__ import annotations

import json
import logging
import re
import socket
import threading
from collections import deque
from time import monotonic

from .ua import (
    UaCall,
    UaEvent,
    UaEventKind,
    UaLeg,
    UaMisconfigured,
    UaRefused,
    UaUnreachable,
    UaUnsupportedVersion,
)

log = logging.getLogger(__name__)

#: The versions of baresip this build has been TESTED against, and the only ones
#: it will start on. Not a floor and not a range: a range would be a claim about
#: versions nobody ran, and the whole reason this check exists is that a control
#: vocabulary is not a stable interface the way a contract is.
TESTED_VERSIONS: tuple[str, ...] = ("4.11.0",)

#: What baresip calls the things this package's seam names. A type outside this
#: map is DROPPED rather than passed through: an event this build cannot place
#: is not an event it can act on, and inventing a kind for it would put a
#: guess into the agent's record of what happened.
EVENT_KINDS: dict[str, UaEventKind] = {
    "CALL_INCOMING": UaEventKind.CALL_INCOMING,
    "CALL_ESTABLISHED": UaEventKind.CALL_ESTABLISHED,
    "CALL_RTPESTAB": UaEventKind.CALL_MEDIA,
    "CALL_CLOSED": UaEventKind.CALL_CLOSED,
    "CALL_DTMF_START": UaEventKind.DTMF,
    "REGISTER_OK": UaEventKind.REGISTERED,
    "REGISTER_FAIL": UaEventKind.REGISTRATION_LOST,
    "UNREGISTERING": UaEventKind.REGISTRATION_LOST,
}

#: How much of one framed message this module will read before deciding the UA
#: is answering something that is not a control message.
MAX_MESSAGE_BYTES = 1 << 20

#: How long a command waits for its response. A SETTING with a published
#: default, and an ASSUMPTION: nothing here measures how long baresip takes to
#: answer on a loaded gate controller. It is drawn short because there is a
#: driver waiting and long enough that a busy box is not called dead.
DEFAULT_UA_TIMEOUT = 5.0

#: The published default for `[user_agent] reconnect_seconds`: the LONGEST gap
#: between attempts to reopen the control socket. The first retry is
#: `RECONNECT_FLOOR` away and the gap doubles up to this, so an ordinary
#: `systemctl restart baresip` is recovered from in about a second while a user
#: agent that is gone for good is not hammered once per poll for ever.
DEFAULT_RECONNECT_SECONDS = 5.0

#: The first gap. Not a setting: it is short enough to be invisible and there is
#: nothing a site could usefully make it instead.
RECONNECT_FLOOR = 0.25

#: The audio driver that must not be on either device. It loops the player back
#: into the source, so every call is bridged to every other one whether the
#: agent asked or not.
FORBIDDEN_AUDIO_DRIVER = "aubridge"

#: The modules the agent cannot work without, by the name baresip lists them
#: under. `mixausrc` plays one file into ONE leg; `mixminus` IS the bridge;
#: `ctrl_tcp` is how this process says anything at all.
REQUIRED_MODULES: tuple[str, ...] = ("ctrl_tcp", "mixausrc", "mixminus")

#: The module that answers `config` and `modules`. Without it the two checks
#: below cannot run, which is a refusal rather than a check that quietly did not
#: happen.
INTROSPECTION_MODULE = "debug_cmd"

_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_VERSION = re.compile(r"(\d+\.\d+\.\d+)")
_CALL_ID = re.compile(r"call id:\s*([0-9a-fA-F]+)")
_CNAME = re.compile(r"cname=(\S+)")
#: One line of `listcalls`:  `[line 1, id 3aee..]  00:00:03   INCOMING            sip:x@y`
_CALL_LINE = re.compile(
    r"\[line\s+\d+,\s*id\s+(?P<id>[0-9a-fA-F]+)\]\s+\S+\s+(?P<state>[A-Z]+)\s+(?P<peer>.*)$"
)


class BaresipUa:
    """One baresip, driven over `ctrl_tcp`. Answers, plays, bridges, hangs up."""

    kind = "baresip"

    def __init__(
        self,
        host: str,
        port: int,
        driver_aor: str,
        operator_aor: str,
        timeout: float = DEFAULT_UA_TIMEOUT,
        connect=socket.create_connection,
        reconnect_seconds: float = DEFAULT_RECONNECT_SECONDS,
        clock=None,
    ) -> None:
        self._address = (host, port)
        self._aors = {UaLeg.DRIVER: driver_aor, UaLeg.OPERATOR: operator_aor}
        self._timeout = timeout
        self._connect = connect
        self.reconnect_seconds = reconnect_seconds
        self._clock = clock or monotonic
        #: When the next attempt to REOPEN the socket is allowed, and how long
        #: the gap after it will be. Bounded backoff, reset on every success.
        self._retry_at = 0.0
        self._retry_gap = RECONNECT_FLOOR
        self._sock = None
        self._buffer = b""
        self._events: deque[UaEvent] = deque()
        self._responses: deque[dict] = deque()
        self._lock = threading.Lock()
        self._token = 0
        self._version: str | None = None
        #: `None` until the UA has said something about registration. NOT
        #: `False`: a registration nobody has heard about is not a registration
        #: known to be lost, and publishing the second would page somebody to a
        #: site that is working.
        self._registered: bool | None = None
        #: Which account each call belongs to, learnt from the events.
        self._accounts: dict[str, str] = {}
        #: The RTP CNAME of each call's audio stream, READ OUT OF THE USER AGENT
        #: rather than derived from the configuration.
        #:
        #: This is the one thing in this adapter that had to be measured rather
        #: than assumed. baresip identifies the stream to play into by CNAME and
        #: matches it WHOLE -- a prefix does not match -- and the CNAME is not
        #: the account's address of record: it is the user part at the LOCAL SIP
        #: address and port, which is a different host and port from the AOR
        #: whenever a site registers with a registrar that is not on the same
        #: box. Built from the configuration, every playback failed with
        #: `Invalid argument`, which the agent reports as `ua_unreachable` and a
        #: driver hears as silence. Asked for, it is exact.
        self._cnames: dict[str, str] = {}

    # -- the connection ----------------------------------------------------

    def start(self) -> None:
        """Connect, learn the version, CHECK THE CONFIGURATION, and refuse."""
        self._open()
        self._read_registration()
        self._check_configuration()
        version = self.version()
        if version not in TESTED_VERSIONS:
            raise UaUnsupportedVersion(
                f"the user agent is baresip {version!r}; this build was tested against "
                f"{TESTED_VERSIONS}. Refusing to start: a control vocabulary is not a "
                "versioned contract, so a command that has been renamed or has grown a "
                "parameter would be a call answered and then handled wrongly, with a "
                "driver at the barrier."
            )

    def _check_configuration(self) -> None:
        """Read the LOADED configuration back, and refuse a fatal one by name.

        Not from the site's copy of a file this package does not own: from the
        running process, over the same socket everything else goes over.
        """
        try:
            loaded = _ANSI.sub("", self._command("config"))
            modules = _ANSI.sub("", self._command("modules"))
        except UaRefused as exc:
            raise UaMisconfigured(
                f"the user agent refused `config`/`modules` ({exc}). Those two come from "
                f"baresip's `{INTROSPECTION_MODULE}` module, which is an install "
                "requirement of this agent precisely so the settings below can be CHECKED "
                "rather than assumed. Add `module_app "
                f"{INTROSPECTION_MODULE}.so` to the user agent's configuration."
            ) from exc
        settings = _settings(loaded)
        for key in ("audio_source", "audio_player"):
            value = settings.get(key, "")
            if value.split(",")[0].strip() == FORBIDDEN_AUDIO_DRIVER:
                raise UaMisconfigured(
                    f"the user agent's {key} is `{value}`. `{FORBIDDEN_AUDIO_DRIVER}` loops "
                    "the player back into the source, so every call is bridged to every "
                    "other one whether this agent asked for it or not -- the operator hears "
                    "the driver before `conference` is ever sent, and on this build every "
                    "playback is refused for ever besides. Refusing to start."
                )
        hold = settings.get("call_hold_other_calls", "")
        if hold and hold.lower() not in ("no", "false", "0"):
            raise UaMisconfigured(
                f"the user agent's call_hold_other_calls is `{hold}`. With it on, calling "
                "the operator puts the driver at the barrier ON HOLD under this process "
                "rather than because it asked. Refusing to start."
            )
        loaded_modules = {
            line.split()[0] for line in modules.splitlines()
            if "type=" in line and line.split()
        }
        missing = [one for one in REQUIRED_MODULES if one not in loaded_modules]
        if missing:
            raise UaMisconfigured(
                f"the user agent has not loaded {', '.join(missing)}. `mixausrc` is what "
                "plays one file into ONE leg, `mixminus` is what the bridge is, and "
                "`ctrl_tcp` is how this process says anything at all. Refusing to start."
            )

    def calls(self) -> tuple[UaCall, ...]:
        """Which calls the user agent is holding RIGHT NOW, and which are ringing.

        Asked rather than remembered, and asked only after the control socket
        has been lost: the events for anything that happened while it was down
        went to nobody, so a call that is still ringing at the door is a fact
        only the user agent has.
        """
        data = _ANSI.sub("", self._command("listcalls"))
        found = []
        for line in data.splitlines():
            match = _CALL_LINE.search(line)
            if match is None:
                continue
            found.append(
                UaCall(
                    call_id=match.group("id"),
                    peer_uri=match.group("peer").strip() or None,
                    # baresip's own word for a call that has arrived and has not
                    # been answered. `RINGING` is the OUTGOING side of the same
                    # moment and is not one this agent can answer.
                    ringing=match.group("state") == "INCOMING",
                )
            )
        return tuple(found)

    def _open(self) -> None:
        try:
            self._sock = self._connect(self._address, timeout=self._timeout)
        except OSError as exc:
            raise UaUnreachable(f"{self._address[0]}:{self._address[1]}: {exc}") from exc
        self._sock.settimeout(self._timeout)
        self._buffer = b""
        self._responses.clear()
        self._retry_gap = RECONNECT_FLOOR
        self._retry_at = 0.0

    def _lost(self) -> None:
        """The socket is gone. Drop it, and schedule the next attempt.

        Dropping it is what makes the reconnect possible at all: it used to be
        left in place, so every later command raised on a socket that would
        never answer again and the agent reported `ua_unreachable` for the life
        of the process -- for an ordinary `systemctl restart baresip`.
        """
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
        self._sock = None
        self._buffer = b""
        self._schedule_retry()

    def _schedule_retry(self) -> None:
        """When the next attempt is allowed, and how long the gap after it is."""
        self._retry_at = self._clock() + self._retry_gap
        self._retry_gap = min(max(self._retry_gap * 2, RECONNECT_FLOOR),
                              max(self.reconnect_seconds, RECONNECT_FLOOR))

    def reconnect(self) -> tuple[UaCall, ...]:
        """Reopen the control socket if it is time to, and say what is up.

        Returns the calls the user agent is holding when a socket was actually
        reopened, and an empty tuple otherwise -- so a caller can tell "there
        was nothing to do" from "it came back and here is what it found".
        """
        if self._sock is not None:
            return ()
        if self._clock() < self._retry_at:
            raise UaUnreachable(
                f"the user agent's control socket is not open; the next attempt to reopen "
                f"it is {self._retry_at - self._clock():.2f}s away"
            )
        try:
            self._open()
        except UaUnreachable:
            # A REFUSED reopen schedules the next one. Without this the backoff
            # only ever grew on a socket that was lost while in use, so a user
            # agent that stayed down was retried once per poll for ever -- the
            # bound was a number in a document.
            self._schedule_retry()
            raise
        self._accounts.clear()
        self._cnames.clear()
        with self._lock:
            self._events.clear()
        self._read_registration()
        return self.calls()

    def close(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            finally:
                self._sock = None

    # -- the six verbs -----------------------------------------------------

    def version(self) -> str:
        if self._version is None:
            data = _ANSI.sub("", self._command("about"))
            found = _VERSION.search(data)
            if not found:
                raise UaUnreachable(
                    "the user agent did not say which version it is. This build refuses to "
                    "drive a process it cannot identify."
                )
            self._version = found.group(1)
        return self._version

    def registered(self) -> bool | None:
        return self._registered

    def _read_registration(self) -> None:
        """Ask what the registration is NOW, rather than waiting to be told.

        The control channel only forwards events to a client that is connected,
        and a user agent registers when IT starts -- which is before this
        process opens the socket. Without this read, an agent that came up after
        a perfectly healthy registration would publish `unknown` for as long as
        that registration lasted, and the first thing it ever said about the one
        code it exists to measure would be that it does not know.
        """
        try:
            data = _ANSI.sub("", self._command("reginfo"))
        except UaUnreachable:
            return
        for line in data.splitlines():
            if self._aors[UaLeg.DRIVER] not in line:
                continue
            # `OK`, `ERR` and `zzz` are the user agent's own three words for it,
            # and the third one means it has not tried yet -- which is `None`
            # here and never `False`. A registration nobody has heard about is
            # not one known to be lost.
            if "OK" in line:
                self._registered = True
            elif "ERR" in line:
                self._registered = False
            return

    def answer(self, call_id: str) -> None:
        self._command("accept", call_id)

    def dial(self, uri: str, leg: UaLeg = UaLeg.OPERATOR) -> str:
        """Place a call FROM `leg`'s account, and return the call it created.

        The account matters and is not cosmetic: it is what `play` targets, so a
        call placed from the wrong one is a call this process cannot speak to
        privately afterwards.
        """
        self._command("uafind", self._aors[leg])
        data = self._command("dial", uri)
        found = _CALL_ID.search(data)
        if not found:
            raise UaUnreachable(f"the user agent did not name the call it placed: {data!r}")
        call_id = found.group(1)
        self._accounts[call_id] = self._aors[leg]
        return call_id

    def play(self, call_id: str, path: str) -> None:
        """One audio file, into ONE call, with that call's own audio silenced.

        `0` is the volume the call's live audio is faded to and `100` the volume
        of the file: the agent has no microphone and its audio source is
        silence, so this is a statement about what the file replaces rather than
        a mix.
        """
        self._command(
            "mixausrc_enc_start", f"aufile {path} 0 100 cname={self._cname(call_id)}"
        )

    def stop_playing(self, call_id: str) -> None:
        self._command("mixausrc_enc_stop", f"cname={self._cname(call_id)}")

    def _cname(self, call_id: str) -> str:
        """The call's own CNAME, asked of the user agent and then remembered."""
        if call_id not in self._cnames:
            self._command("callfind", call_id)
            found = _CNAME.search(_ANSI.sub("", self._command("audio_debug")))
            if not found:
                raise UaUnreachable(
                    f"the user agent did not name the audio stream of call {call_id}. "
                    "Without it there is nothing to play a file into, and a driver at a "
                    "barrier hears silence."
                )
            self._cnames[call_id] = found.group(1)
        return self._cnames[call_id]

    def bridge(self) -> None:
        """From this moment, both calls hear each other. Not before.

        Everything before this is private to a leg, which is what lets the
        operator be told the case and offered a menu without the driver hearing
        either.
        """
        self._command("conference")

    def hangup(self, call_id: str) -> None:
        """End ONE call. Selected first, because the command acts on `current`."""
        self._command("callfind", call_id)
        self._command("hangup")
        self._accounts.pop(call_id, None)
        self._cnames.pop(call_id, None)

    def hangup_all(self) -> None:
        self._command("hangupall", "")
        self._accounts.clear()
        self._cnames.clear()

    def poll(self) -> tuple[UaEvent, ...]:
        """Everything the UA has said since the last poll, in this seam's words."""
        self._drain()
        with self._lock:
            events = tuple(self._events)
            self._events.clear()
        return events

    # -- the wire ----------------------------------------------------------

    def _command(self, command: str, params: str = "") -> str:
        """One command, and its response. Events arriving meanwhile are kept.

        Responses are matched BY TOKEN and not by arrival order: baresip
        interleaves events with responses, and a reader that took the next
        message as the answer would read an incoming call as the result of the
        command that was in flight when it arrived.
        """
        if self._sock is None:
            raise UaUnreachable("the user agent's control socket is not open")
        self._token += 1
        token = str(self._token)
        payload = json.dumps(
            {"command": command, "params": params, "token": token}
        ).encode("utf-8")
        try:
            self._sock.sendall(b"%d:%s," % (len(payload), payload))
        except OSError as exc:
            self._lost()
            raise UaUnreachable(f"{command}: {exc}") from exc
        while True:
            for message in list(self._responses):
                if message.get("token") == token:
                    self._responses.remove(message)
                    if not message.get("ok"):
                        # It ANSWERED, and what it answered was no. A different
                        # fact from a dead socket, and the difference decides
                        # whether anybody is paged.
                        raise UaRefused(
                            f"the user agent refused `{command}`: {message.get('data')!r}"
                        )
                    return str(message.get("data") or "")
            self._read_more()

    def _drain(self) -> None:
        """Read whatever is already there, without waiting for anything."""
        if self._sock is None:
            raise UaUnreachable("the user agent's control socket is not open")
        self._sock.settimeout(0.0)
        try:
            while True:
                try:
                    chunk = self._sock.recv(65536)
                except (BlockingIOError, TimeoutError):
                    return
                except OSError as exc:
                    self._lost()
                    raise UaUnreachable(f"reading from the user agent: {exc}") from exc
                if not chunk:
                    self._lost()
                    raise UaUnreachable("the user agent closed its control socket")
                self._buffer += chunk
                self._parse()
        finally:
            if self._sock is not None:
                self._sock.settimeout(self._timeout)

    def _read_more(self) -> None:
        try:
            chunk = self._sock.recv(65536)
        except TimeoutError as exc:
            self._lost()
            raise UaUnreachable("the user agent did not answer in time") from exc
        except OSError as exc:
            self._lost()
            raise UaUnreachable(f"reading from the user agent: {exc}") from exc
        if not chunk:
            self._lost()
            raise UaUnreachable("the user agent closed its control socket")
        self._buffer += chunk
        self._parse()

    def _parse(self) -> None:
        """Netstrings out of the buffer: `<length>:<payload>,`."""
        while b":" in self._buffer:
            head, _, rest = self._buffer.partition(b":")
            try:
                length = int(head)
            except ValueError as exc:
                self._lost()
                raise UaUnreachable(
                    f"the user agent framed a message this build cannot read: {head[:32]!r}"
                ) from exc
            if length > MAX_MESSAGE_BYTES:
                self._lost()
                raise UaUnreachable(f"the user agent announced {length} bytes in one message")
            if len(rest) < length + 1:
                return
            body, self._buffer = rest[:length], rest[length + 1 :]
            try:
                message = json.loads(body)
            except ValueError:
                # Not a control message. Dropped rather than guessed at; the
                # command that is waiting will time out and say so, which names
                # the UA rather than inventing an answer from it.
                log.warning("the user agent sent something that is not JSON")
                continue
            if not isinstance(message, dict):
                continue
            if message.get("event"):
                self._event(message)
            else:
                self._responses.append(message)

    def _event(self, message: dict) -> None:
        kind = EVENT_KINDS.get(str(message.get("type")))
        if kind is None:
            return
        call_id = message.get("id")
        account = message.get("accountaor")
        if isinstance(call_id, str) and isinstance(account, str):
            self._accounts[call_id] = account
        if kind is UaEventKind.REGISTERED:
            self._registered = True
        elif kind is UaEventKind.REGISTRATION_LOST:
            self._registered = False
        with self._lock:
            self._events.append(
                UaEvent(
                    kind=kind,
                    call_id=call_id if isinstance(call_id, str) else None,
                    peer_uri=message.get("peeruri")
                    if isinstance(message.get("peeruri"), str)
                    else None,
                    digit=str(message.get("param"))
                    if kind is UaEventKind.DTMF and message.get("param") is not None
                    else None,
                )
            )
        if kind is UaEventKind.CALL_CLOSED and isinstance(call_id, str):
            self._accounts.pop(call_id, None)
            self._cnames.pop(call_id, None)


def _settings(text: str) -> dict[str, str]:
    """baresip's `config` output as key -> value. Comments and blanks dropped."""
    out: dict[str, str] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        parts = stripped.split(None, 1)
        if len(parts) == 2:
            out.setdefault(parts[0], parts[1].split("#")[0].strip())
    return out


__all__ = [
    "DEFAULT_RECONNECT_SECONDS",
    "DEFAULT_UA_TIMEOUT",
    "EVENT_KINDS",
    "FORBIDDEN_AUDIO_DRIVER",
    "INTROSPECTION_MODULE",
    "RECONNECT_FLOOR",
    "REQUIRED_MODULES",
    "TESTED_VERSIONS",
    "BaresipUa",
]
