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

    def __repr__(self) -> str:
        return (
            f"Relay(kind={self.kind!r}, url={self.url!r}, port={self.port!r}, "
            f"pulse_ms={self.pulse_ms!r}, username={self.username!r}, "
            "password=<not shown>)"
        )


class AxisRelay:
    """Pulses one Axis output port. HTTP Digest, through the one opener."""

    def __init__(self, relay: Relay, timeout: float = 5.0) -> None:
        self.relay = relay
        self.timeout = timeout
        manager = urllib.request.HTTPPasswordMgrWithDefaultRealm()
        manager.add_password(None, relay.url, relay.username, relay.password)
        # DIGEST FIRST, and Basic behind it, exactly as the camera does: a unit
        # that offers Digest is answered with Digest, and Basic is what an older
        # one challenges with. The opener comes from `redirects.build_opener`,
        # so this one does not follow a `Location` -- and the request it would
        # follow one on is the retry that carries the credential.
        self._opener = build_opener(
            urllib.request.HTTPDigestAuthHandler(manager),
            urllib.request.HTTPBasicAuthHandler(manager),
        )

    def pulse(self) -> None:
        """One pulse, or one of the two refusals. Nothing else happens here.

        **It does not decide anything.** The human decided; this is the wire.
        """
        action = pulse_action(self.relay.port, self.relay.pulse_ms)
        # `quote` with NO safe characters, because `:`, `/` and `\` are exactly
        # the three Axis requires to be percent-encoded and `quote`'s
        # default leaves `/` alone.
        query = "action=" + urllib.parse.quote(action, safe="")
        url = f"{self.relay.url.rstrip('/')}{PORT_CGI}?{query}"
        request = urllib.request.Request(url, method="GET")
        try:
            with self._opener.open(request, timeout=self.timeout) as response:
                body = response.read(MAX_RESPONSE_BYTES + 1)
                status = response.status
        except urllib.error.HTTPError as exc:
            raise RelayRefusedUs(
                f"the relay answered HTTP {exc.code} to a pulse", exc.code
            ) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise RelayUnreachable(f"the relay could not be reached: {exc}") from exc
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


def build(relay: Relay, timeout: float = 5.0) -> AxisRelay:
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
    "MAX_RESPONSE_BYTES",
    "PORTS",
    "PORT_CGI",
    "RELAY_KINDS",
    "AxisRelay",
    "Relay",
    "RelayRefusedUs",
    "RelayUnreachable",
    "build",
    "pulse_action",
]
