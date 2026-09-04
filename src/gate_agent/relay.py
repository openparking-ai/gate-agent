"""A STANDALONE intercom's own relay, and the only barrier this agent moves itself.

**Where there is a lane, this is not used and cannot be.** The lane writes the
identity and flushes it before its own relay moves; an agent pulsing a second
relay at the same gate would be a barrier that opened with no record on the
machine that keeps the records. `IntercomDescription` refuses `has_relay` on an
intercom with a lane, and the configuration refuses the table.

**Standalone has no lane, no loop and no presence source at all**, so the
invariant is met the only way it can be: **the agent's OWN RECORD is written and
FLUSHED before the relay pulses**, and nothing anywhere measures whether a
vehicle is there. The contract says that in those words rather than leaving a
reader to assume a loop.

**THE HUMAN IS THE PRESENCE CHECK.** There is no self-service ticket here: a
button that issues a ticket with no car behind it is the market failure Gokhan
named (SETTLED 3a). A press reaches a person, who can hear the driver, and the
relay moves on their word and on nothing else.

## What Axis documents, quoted

From **"Input and outputs | Axis developer documentation"**
(`https://developer.axis.com/vapix/network-video/input-and-outputs/`, read
2026-08-31):

  * the request is a **GET**:
    `GET /axis-cgi/io/port.cgi?<argument>=<value>[&=<value>...]`
  * the argument for an output is `action=<string>`, whose format is
    `[<Port ID>]:<a>[<wait><a>...]`, where `<a>` is `/` for active or `\\` for
    inactive and `<wait>` is a delay in milliseconds;
  * *"The `:`, `/` and `\\` characters must be percent-encoded in the URI."*
    The document's own worked example: to *"Set output 1 to active, use `1:/`.
    In the URI, the action argument becomes `action=1%3A%2F`"*, and a two-pulse
    example is `action=2%3A%2F300%5C500%2F300%5C`;
  * port numbering *"starts from one (where one corresponds to the physical port
    labeled '1')"*;
  * the documented success is **`200 OK`**, `Content-Type: text/plain`, with an
    **empty body for action arguments**;
  * the stated access level is **Viewer**.

So one pulse of `N` milliseconds on port `P` is `action=P:/N\\`, which on the
wire is `action=<P>%3A%2F<N>%5C`. That is the whole request this module makes.

**AND THE WHOLE PATH IS NOT MEASURED.** No Axis unit has been driven by this
code. What is tested here is a fake that answers as the document says and
refuses anything else; what nobody has seen is a real relay, a real barrier, and
what a real unit does with a `pulse_ms` its own barrier disagrees with. It is in
the January list and it is not softened.

**2N and Akuvox are NOT BUILT.** They have their own relay APIs and their own
authentication, and a second `kind` written without a device to try it against
would be a second untested path wearing the same name as a tested one. A site
with one of those declares no relay and the human opens the barrier.
"""

from __future__ import annotations

import logging
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

from .redirects import build_opener

log = logging.getLogger(__name__)

#: The one relay kind this version drives. Named rather than defaulted: a site
#: with a unit this does not speak to must find that out at installation.
AXIS_VAPIX = "axis_vapix"
RELAY_KINDS: tuple[str, ...] = (AXIS_VAPIX,)

#: The CGI Axis documents, and the ports it names. Constants because a
#: path a caller could choose is a client that can reach any CGI on that device.
PORT_CGI = "/axis-cgi/io/port.cgi"

#: The ports a unit of this tier has. **Axis numbers its ports from ONE**,
#: which the document states, and an off-by-one here is a relay that pulses the
#: wrong thing or nothing.
PORTS = (1, 2)

#: A response larger than this is not the documented empty body.
MAX_RESPONSE_BYTES = 4096

#: What `pulse_ms` may be. **BOUNDED, and both ends are refusals a site earns at
#: startup rather than discoveries at a barrier.** One millisecond is the
#: shortest contact anything could mean; ten seconds is the longest this build
#: will hold a door station's HTTP connection open for one press, and past it a
#: site has a barrier this build should not be driving. There is still no
#: DEFAULT -- the barrier's own specification decides the value inside these --
#: and the bounds are published in `docs/CONTRACT.md`.
PULSE_MS_BOUNDS = (1, 10_000)

#: How much longer than the pulse itself this build waits for the unit to
#: answer. **AN ASSUMPTION, and stated as one**: nothing here has driven a real
#: unit, so nothing has measured whether an Axis unit answers the request
#: immediately or holds the connection for the whole contact. Five seconds is
#: drawn from an HTTP request on a LAN, which is a guess about a network rather
#: than a measurement of this device.
#:
#: The timeout is DERIVED from it -- `pulse_ms / 1000 + answer_margin_s` -- and
#: that derivation is the point: a hard-coded 5.0 reported a unit mid-pulse on a
#: legal six-second barrier as a relay that could not be REACHED, while the
#: barrier was very probably opening.
DEFAULT_ANSWER_MARGIN_S = 5.0


class RelayUnreachable(Exception):
    """The unit did not answer. A human is told, and nothing moved."""


class RelayRefusedUs(Exception):
    """The unit ANSWERED, and what it answered was no.

    A `401` that survives the credential, a `403`, a `404` for a CGI this unit
    does not have, or a body that is not what the document says a success is.
    Separate from silence because it names a different repair: the credential in
    a file on this box, or a unit that is not the one this build speaks to.
    """

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


def pulse_action(port: int, milliseconds: int) -> str:
    """The `action` value for ONE pulse, in Axis's own grammar.

    `P:/N\\` -- active, wait N milliseconds, inactive -- which is the document's
    `[<Port ID>]:<a>[<wait><a>...]` with one wait in it. Built here rather than
    formatted at the call site so there is one place the grammar lives.
    """
    return f"{port}:/{milliseconds}\\"


@dataclass(frozen=True, slots=True, repr=False)
class Relay:
    """One declared relay. Its credential is never in its `repr`."""

    kind: str
    url: str
    port: int
    #: DECLARED, no default. **The barrier's vend input decides this, not us**:
    #: how long a contact must close for a given barrier to accept it is that
    #: barrier's specification, and a number this package chose would be a guess
    #: about somebody else's equipment written into every installation.
    pulse_ms: int
    username: str
    password: str
    #: How long past the pulse this build waits for the unit to answer.
    #: Published default, per site, because the number it is added to is the
    #: site's own.
    answer_margin_s: float = DEFAULT_ANSWER_MARGIN_S

    @property
    def timeout(self) -> float:
        """The HTTP timeout, DERIVED. One place, and it is here.

        A unit that holds the connection for the length of the contact is
        answering, not silent, and a fixed timeout shorter than the contact
        reported it as unreachable while the barrier moved. **Whether a real
        unit does hold it is NOT MEASURED** and is on the January list.
        """
        return self.pulse_ms / 1000 + self.answer_margin_s

    def __repr__(self) -> str:
        return (
            f"Relay(kind={self.kind!r}, url={self.url!r}, port={self.port!r}, "
            f"pulse_ms={self.pulse_ms!r}, answer_margin_s={self.answer_margin_s!r}, "
            f"username={self.username!r}, password=<not shown>)"
        )


class _Answered(urllib.request.BaseHandler):
    """Records that the unit ANSWERED, whatever it answered.

    The two exceptions this module raises differ on exactly one fact -- did
    anything come back from that address -- and that fact was being INFERRED
    from the exception class, which is where `qop="auth-int"` was reported as a
    relay that could not be reached. It is measured here instead: every
    response passes through this handler, including a `401`, and the flag says
    what the class cannot.

    `handler_order` is below `HTTPErrorProcessor`'s 1000 so this runs before the
    processor that turns a non-2xx into an `HTTPError`.
    """

    handler_order = 100

    def __init__(self) -> None:
        self.seen = False

    def http_response(self, request, response):
        self.seen = True
        return response

    https_response = http_response


class AxisRelay:
    """Pulses one Axis output port. HTTP Digest, through the one opener."""

    def __init__(self, relay: Relay, timeout: float | None = None) -> None:
        self.relay = relay
        # DERIVED from the pulse unless a caller names one, and the only caller
        # that names one is a test measuring the derivation itself.
        self.timeout = relay.timeout if timeout is None else timeout
        manager = urllib.request.HTTPPasswordMgrWithDefaultRealm()
        manager.add_password(None, relay.url, relay.username, relay.password)
        # DIGEST ONLY. Basic used to sit behind it, and a unit challenging
        # `Basic` was answered with the credential in a header that is base64 of
        # `user:password` -- on a device wired to a barrier, over a LAN, with
        # nothing saying it had happened. It is now a REFUSAL that names the
        # scheme, because a unit challenging Basic is a unit whose configuration
        # somebody has to change. The opener comes from `redirects.build_opener`,
        # so this one does not follow a `Location` -- and the request it would
        # follow one on is the retry that carries the credential.
        self._answered = _Answered()
        self._opener = build_opener(
            urllib.request.HTTPDigestAuthHandler(manager), self._answered
        )

    def pulse(self) -> None:
        """One pulse, or one of the two refusals. **Nothing else leaves here.**

        **It does not decide anything.** The human decided; this is the wire.

        **EVERY exception is mapped**, and that is the round-7 change rather
        than a tidy-up. This used to catch four classes, and `urllib`'s own auth
        machinery raises a bare `ValueError` for a challenge it cannot parse --
        a `Digest` with no `realm` is one -- so a unit on the site's LAN could
        raise straight out of `poll()`, past the caller, into the loop's blanket
        handler: the barrier did not move, the operator who had just authorised
        it was told NOTHING, and `relay_pulsed` stood on the event stream. Four
        surfaces and one of them right.

        The split is the one the two exceptions already publish, and it is kept
        exactly: **did the unit answer?** Silence is `RelayUnreachable`.
        Anything it answered that this build cannot use is `RelayRefusedUs`
        NAMING the reason -- a `401` that survives the credential, a `Basic`
        challenge, `qop="auth-int"`, a challenge with no realm, a challenge this
        build cannot parse at all, a body where the document says empty. They
        send somebody to different places, which is why the distinction is
        load-bearing enough to be worth this paragraph.
        """
        action = pulse_action(self.relay.port, self.relay.pulse_ms)
        # `quote` with NO safe characters, because `:`, `/` and `\` are exactly
        # the three Axis requires to be percent-encoded and `quote`'s
        # default leaves `/` alone.
        query = "action=" + urllib.parse.quote(action, safe="")
        url = f"{self.relay.url.rstrip('/')}{PORT_CGI}?{query}"
        request = urllib.request.Request(url, method="GET")
        self._answered.seen = False
        try:
            with self._opener.open(request, timeout=self.timeout) as response:
                body = response.read(MAX_RESPONSE_BYTES + 1)
                status = response.status
        except urllib.error.HTTPError as exc:
            raise RelayRefusedUs(_http_reason(exc), exc.code) from exc
        except Exception as exc:  # noqa: BLE001
            # NOTHING LEAVES HERE UNNAMED, and which of the two it is comes off
            # the MEASUREMENT above rather than off the exception's class.
            #
            # `qop="auth-int"` is the case that made the difference matter:
            # urllib raises a bare `URLError` from inside its own auth handler,
            # AFTER the unit's 401 arrived, and reading the class alone reported
            # a unit that had answered as one that could not be reached --
            # sending somebody to look at a network instead of at the device.
            if _timed_out(exc):
                # SILENCE, whatever came before it. A unit that challenged and
                # then said nothing inside the derived timeout has not answered
                # the request that matters, and the repair is the one silence
                # names. This is the case the timeout is DERIVED for: a unit
                # holding the connection for a six-second contact.
                raise RelayUnreachable(f"the relay could not be reached: {exc}") from exc
            if self._answered.seen:
                raise RelayRefusedUs(
                    f"the relay answered something this build cannot use: {exc}"
                ) from exc
            if isinstance(exc, urllib.error.URLError | TimeoutError | OSError):
                raise RelayUnreachable(f"the relay could not be reached: {exc}") from exc
            log.exception("the relay at %s raised %s", self.relay.url, type(exc).__name__)
            raise RelayRefusedUs(
                f"the relay raised {type(exc).__name__} before it answered, which this "
                f"build has no answer for: {exc}"
            ) from exc
        if status != 200:
            raise RelayRefusedUs(f"the relay answered HTTP {status} to a pulse", status)
        if body.strip():
            # THE DOCUMENT SAYS AN ACTION ANSWERS AN EMPTY BODY. Anything else
            # is a device that is not the one this build speaks to -- an error
            # page, a login form, a different product -- and treating it as a
            # success would be a barrier reported open on the strength of a
            # `200` from something else entirely.
            raise RelayRefusedUs(
                "the relay answered a body where Axis documents an empty one for an "
                "action argument; this is not a unit this build drives"
            )


def _timed_out(exc: BaseException) -> bool:
    """Whether this is a deadline that passed, wrapped or not."""
    return isinstance(exc, TimeoutError) or isinstance(
        getattr(exc, "reason", None), TimeoutError
    )


def _http_reason(exc: urllib.error.HTTPError) -> str:
    """What to say about an HTTP answer this build will not act on.

    A `401` that reached here survived the credential, so the useful thing is
    the CHALLENGE the unit sent: `Basic` names a unit whose configuration has to
    change, and `qop="auth-int"` names one this build does not speak to. Both
    used to arrive as a bare status, and one of them arrived as `unreachable`.
    """
    challenge = None
    try:
        challenge = exc.headers.get("WWW-Authenticate")
    except AttributeError:
        pass
    if exc.code != 401 or not challenge:
        return f"the relay answered HTTP {exc.code} to a pulse"
    parts = challenge.split(None, 1)
    scheme = parts[0] if parts else ""
    if scheme.lower() != "digest":
        return (
            f"the relay challenged {scheme!r} and this build answers Digest only. A "
            "credential that opens a barrier is not sent under a scheme that carries it "
            "in the clear."
        )
    fields = {
        key.strip().lower(): value.strip().strip('"')
        for key, _, value in (
            part.partition("=") for part in (parts[1] if len(parts) > 1 else "").split(",")
        )
        if value
    }
    missing = [name for name in ("realm", "nonce") if name not in fields]
    if missing:
        # urllib answers a challenge it cannot read by sending nothing at all,
        # so the retry never carries a credential and the unit's second 401 is
        # the only thing that arrives. Reported as a missing credential, it sent
        # somebody to a password file about a unit whose challenge is malformed.
        return (
            "the relay's Digest challenge names no "
            + " and no ".join(missing)
            + ", so there is nothing to compute a response from"
        )
    return (
        "the relay answered HTTP 401 to every attempt; the credential in this site's "
        "file is not one it accepts"
    )


def build(relay: Relay, timeout: float | None = None) -> AxisRelay:
    """The one relay this version builds, refused by name for anything else."""
    if relay.kind != AXIS_VAPIX:
        raise ValueError(
            f"{relay.kind!r} is not a relay kind this build drives. It speaks "
            f"{AXIS_VAPIX!r} and nothing else: 2N and Akuvox have their own APIs and their "
            "own authentication, and a second kind written without a device to try it "
            "against would be an untested path wearing the same name as a tested one."
        )
    return AxisRelay(relay, timeout)


__all__ = [
    "AXIS_VAPIX",
    "DEFAULT_ANSWER_MARGIN_S",
    "MAX_RESPONSE_BYTES",
    "PORTS",
    "PORT_CGI",
    "PULSE_MS_BOUNDS",
    "RELAY_KINDS",
    "AxisRelay",
    "Relay",
    "RelayRefusedUs",
    "RelayUnreachable",
    "build",
    "pulse_action",
]
