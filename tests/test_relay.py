"""The standalone relay: what goes on the wire, and WHAT ORDER things happen in.

**The order is the invariant** (SETTLED 7, round 6 E4). Where there is a lane,
the lane writes the identity and flushes it before its own relay moves. There is
no lane here, so the agent's own record is written and flushed first and the
relay pulses second -- and a test that records every call and refuses any other
order is what holds it, because an order is exactly the kind of property that
survives a refactor by accident and then does not.

**The wire is measured against what Axis DOCUMENTS**, not against what this
package believes: a fake answers as the document says and refuses anything else,
and the request it received is compared against the document's own worked
example. What nobody has is a real unit, and that is in the January list.
"""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from gate_agent import relay as relay_module
from gate_agent.relay import (
    PORT_CGI,
    AxisRelay,
    Relay,
    RelayRefusedUs,
    RelayUnreachable,
    pulse_action,
)
from serving import serving

USER = "root"
PASSWORD = "s3cret-for-a-test"


def a_relay(port=1, pulse_ms=500, url="http://127.0.0.1:1", kind="axis_vapix"):
    return Relay(
        kind=kind, url=url, port=port, pulse_ms=pulse_ms, username=USER, password=PASSWORD
    )


class FakeAxis:
    """A unit that answers as the document says, and refuses anything else.

    **It challenges with Digest**, because that is what a real one does and
    because the retry that carries the credential is the request a redirect
    would take somewhere else. A fake that accepted an unauthenticated request
    would leave the whole authentication path unmeasured.
    """

    def __init__(self) -> None:
        self.requests: list[tuple[str, str]] = []
        self.authenticated: list[str] = []
        #: What this unit answers instead of the documented empty body, for the
        #: test that requires anything else to be refused.
        self.body = b""
        self.status = 200
        #: Whether this unit refuses EVERY credential, which is what a wrong
        #: password looks like from out here. The fake does not verify a digest
        #: response -- that handshake is urllib's and the success path above
        #: exercises it -- so this is how "the unit said no" is measured without
        #: a second implementation of RFC 7616 in a test.
        self.refuse_always = False


def axis_server(unit: FakeAxis):
    class _Handler(BaseHTTPRequestHandler):
        server_version = "fake-axis"
        sys_version = ""

        def log_message(self, fmt, *args):
            pass

        def do_GET(self):  # noqa: N802  (http.server's spelling)
            unit.requests.append(("GET", self.path))
            header = self.headers.get("Authorization")
            if not header or unit.refuse_always:
                self.send_response(401)
                self.send_header(
                    "WWW-Authenticate",
                    'Digest realm="AXIS", qop="auth", nonce="0123456789abcdef"',
                )
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            unit.authenticated.append(self.path)
            self.send_response(unit.status)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(unit.body)))
            self.end_headers()
            self.wfile.write(unit.body)

        def do_POST(self):  # noqa: N802
            unit.requests.append(("POST", self.path))
            self.send_response(405)
            self.send_header("Content-Length", "0")
            self.end_headers()

    return ThreadingHTTPServer(("127.0.0.1", 0), _Handler)


# ---------------------------------------------------------------------------
# What goes on the wire
# ---------------------------------------------------------------------------


def test_the_action_is_the_grammar_axis_documents():
    """`P:/N\\` -- active, wait N milliseconds, inactive.

    Compared against the document's own worked examples rather than against a
    second call to our own function: *"Set output 1 to active, use `1:/`"*, and
    a two-pulse example `2:/300\\500/300\\`.
    """
    assert pulse_action(1, 500) == "1:/500\\"
    assert pulse_action(2, 300) == "2:/300\\"
    # The document's `1:/` is the same grammar with no wait in it, which is what
    # the prefix of ours has to be.
    assert pulse_action(1, 500).startswith("1:/")


def test_the_request_is_a_GET_to_the_documented_cgi_percent_encoded():
    """The whole request, seen from the UNIT's side.

    The three characters Axis requires to be encoded are `:`, `/` and `\\`, and
    `quote`'s default leaves `/` alone -- which is the mistake this asserts
    against, because a `/` on the wire is a path separator and the request would
    reach a CGI that is not this one.
    """
    unit = FakeAxis()
    with serving(axis_server(unit)) as url:
        AxisRelay(a_relay(url=url, port=1, pulse_ms=500)).pulse()
    assert unit.authenticated, "the unit was never given a credential"
    method, path = unit.requests[-1]
    assert method == "GET"
    assert path.startswith(PORT_CGI + "?")
    assert path == f"{PORT_CGI}?action=1%3A%2F500%5C", path
    # And the document's own example encodes the same way through the same code.
    assert "%2F" in path and "%5C" in path and "%3A" in path
    assert "/" not in path.split("?", 1)[1], "a `/` reached the wire unencoded"


def test_the_documented_two_pulse_example_encodes_as_the_document_shows():
    """A control on the encoder itself, against a string the document prints.

    Ours makes one pulse; the document's example makes two. Running its exact
    action through the same encoding must give its exact URI fragment -- which
    is a check of the ENCODER against something we did not write.
    """
    import urllib.parse

    assert (
        urllib.parse.quote("2:/300\\500/300\\", safe="") == "2%3A%2F300%5C500%2F300%5C"
    )


def test_a_unit_that_answers_a_body_is_refused():
    """Axis documents an EMPTY body for an action argument.

    Anything else is a device that is not the one this build drives -- an error
    page, a login form, a different product -- and treating a `200` from it as a
    success would be a barrier reported open on the strength of somebody else's
    web server.
    """
    unit = FakeAxis()
    unit.body = b"<html>Not an Axis</html>"
    with serving(axis_server(unit)) as url:
        with pytest.raises(RelayRefusedUs, match="empty one"):
            AxisRelay(a_relay(url=url)).pulse()
    # THE CONTROL: the same unit answering the documented empty body succeeds.
    unit.body = b""
    with serving(axis_server(unit)) as url:
        AxisRelay(a_relay(url=url)).pulse()


def test_a_unit_that_refuses_the_credential_is_relay_refused_us():
    """A `401` that survives the credential. It names the credential in a file
    on this box, which is a different repair from a unit that is not there."""
    unit = FakeAxis()
    unit.refuse_always = True
    with serving(axis_server(unit)) as url:
        with pytest.raises(RelayRefusedUs) as refused:
            AxisRelay(a_relay(url=url)).pulse()
    assert refused.value.status == 401
    assert len(unit.requests) >= 2, "urllib never retried with a credential"
    # THE CONTROL: the same unit accepting the credential succeeds, so the
    # refusal is about the answer and not about the request.
    unit.refuse_always = False
    with serving(axis_server(unit)) as url:
        AxisRelay(a_relay(url=url)).pulse()


def test_a_unit_that_is_not_there_is_relay_unreachable():
    with pytest.raises(RelayUnreachable):
        AxisRelay(a_relay(url="http://127.0.0.1:1"), timeout=0.2).pulse()


def test_a_relay_kind_this_build_does_not_drive_is_refused_by_name():
    """2N and Akuvox have their own APIs and their own authentication. A kind
    written without a device to try it against would be an untested path wearing
    the same name as a tested one."""
    for kind in ("2n", "akuvox", "generic_http"):
        with pytest.raises(ValueError, match="not a relay kind"):
            relay_module.build(a_relay(kind=kind))
    # THE CONTROL: the one it does drive builds.
    assert isinstance(relay_module.build(a_relay()), AxisRelay)


def test_the_credential_is_not_in_the_relays_repr():
    """The generated `__repr__` would put a password that pulses a barrier into
    every log line and traceback that touches a configuration."""
    text = repr(a_relay())
    assert PASSWORD not in text
    assert "password=<not shown>" in text
    # THE CONTROL: the credential really is on the object, so its absence above
    # is about the repr.
    assert a_relay().password == PASSWORD


def test_the_relay_does_not_follow_a_redirect():
    """The request it would follow one on is the retry that carries the
    credential that opens a barrier."""
    moved = []

    class _Redirector(BaseHTTPRequestHandler):
        server_version = "somewhere-else"
        sys_version = ""

        def log_message(self, fmt, *args):
            pass

        def do_GET(self):  # noqa: N802
            moved.append(self.path)
            self.send_response(302)
            self.send_header("Location", "http://example.invalid/taken")
            self.send_header("Content-Length", "0")
            self.end_headers()

    server = ThreadingHTTPServer(("127.0.0.1", 0), _Redirector)
    with serving(server) as url:
        with pytest.raises(RelayRefusedUs) as refused:
            AxisRelay(a_relay(url=url)).pulse()
    assert refused.value.status == 302
    assert moved, "the redirector was never reached"


# ---------------------------------------------------------------------------
# THE ORDER, which is the invariant
# ---------------------------------------------------------------------------


def test_the_record_is_written_and_flushed_before_the_relay_pulses(tmp_path):
    """SETTLED 7, round 6 E4, met the only way standalone can meet it.

    Where there is a lane, the LANE writes the identity and flushes it before
    its relay moves. There is no lane here, so this agent's own record is what
    must exist first -- otherwise a barrier opens and this site has no record
    that it did, which is the one thing the invariant exists to prevent.

    Every call is RECORDED and the order is asserted whole, rather than by
    checking that the record exists afterwards: "it happened at some point" is
    what a reordered implementation would also satisfy.
    """
    from conftest import INTERCOM_ACCOUNT, FakeClock, agent_config_for, agent_for
    from fake_ua import FakeUa
    from gate_agent import tickets as tickets_module
    from gate_agent.config import TicketSettings
    from gate_agent.contract import AgentEventKind

    calls: list[str] = []

    unit = FakeAxis()
    with serving(axis_server(unit)) as url:
        from dataclasses import replace

        base = agent_config_for(tmp_path, standalone=True)
        config = replace(
            base,
            intercoms=(
                replace(
                    base.intercoms[0],
                    relay=a_relay(url=url, port=2, pulse_ms=700),
                ),
            ),
            tickets=TicketSettings(
                signing_key=b"a-signing-key-long-enough-for-the-floor",
                directory=tmp_path / "tickets",
            ),
            driver_languages=("en",),
        )
        ua = FakeUa()
        clock = FakeClock()
        agent = agent_for(config, ua, clock=clock)

        # EVERY WRITE AND EVERY PULSE, in the order they happen.
        original_write = tickets_module.TicketStore.write
        original_pulse = AxisRelay.pulse

        def recording_write(self, record):
            original_write(self, record)
            calls.append(f"record:{record.state}")

        def recording_pulse(self):
            calls.append("pulse")
            return original_pulse(self)

        tickets_module.TicketStore.write = recording_write
        AxisRelay.pulse = recording_pulse
        try:
            ua.incoming("sip:door1@10.0.0.9", call_id="driver-1",
                        account_user=INTERCOM_ACCOUNT)
            for _ in range(60):
                agent.poll()
                clock.advance(2.0)
                if any(verb == "dial" for verb, _ in ua.commands):
                    break
            operator = [arg for verb, arg in ua.commands if verb == "dial"][0].split("-> ")[1]
            ua.established(operator)
            for _ in range(60):
                agent.poll()
                clock.advance(2.0)
                if ua.bridged_at is not None:
                    break
            ua.dtmf(operator, "1")
            for _ in range(10):
                agent.poll()
                clock.advance(2.0)
        finally:
            tickets_module.TicketStore.write = original_write
            AxisRelay.pulse = original_pulse

    assert "pulse" in calls, f"the relay never pulsed: {calls}"
    # THE ORDER: a record is written BEFORE the pulse, and the pulse is not
    # first. Asserted on the sequence rather than on membership.
    assert calls.index("pulse") > 0, calls
    assert calls[0].startswith("record:"), calls

    events = agent.events(0).to_dict()["events"]
    pulsed = [one for one in events if one["kind"] == AgentEventKind.RELAY_PULSED.value]
    assert len(pulsed) == 1, events
    assert pulsed[0]["relay_port"] == 2
    assert pulsed[0]["relay_ms"] == 700
    assert pulsed[0]["lane"] is None
    # NO CREDENTIAL, anywhere on that record.
    import json as _json

    assert PASSWORD not in _json.dumps(pulsed[0])
    assert USER not in _json.dumps(pulsed[0])
    # And the unit really was asked for port 2.
    assert unit.requests[-1][1] == f"{PORT_CGI}?action=2%3A%2F700%5C"


def test_a_standalone_intercom_with_no_relay_hears_cannot_open(tmp_path):
    """The other half. `operator.cannot_open` is spoken where nothing can act,
    and a standalone door with no relay is exactly that."""
    from conftest import INTERCOM_ACCOUNT, FakeClock, agent_config_for, agent_for
    from fake_ua import FakeUa

    ua = FakeUa()
    clock = FakeClock()
    agent = agent_for(agent_config_for(tmp_path, standalone=True), ua, clock=clock)
    ua.incoming("sip:door1@10.0.0.9", call_id="driver-1", account_user=INTERCOM_ACCOUNT)
    for _ in range(60):
        agent.poll()
        clock.advance(2.0)
        if any(verb == "dial" for verb, _ in ua.commands):
            break
    operator = [arg for verb, arg in ua.commands if verb == "dial"][0].split("-> ")[1]
    ua.established(operator)
    for _ in range(60):
        agent.poll()
        clock.advance(2.0)
        if ua.bridged_at is not None:
            break
    ua.dtmf(operator, "1")
    for _ in range(10):
        agent.poll()
        clock.advance(2.0)
    spoken = [path for leg, path in ua.played if leg == "operator"]
    assert any("operator.cannot_open" in one for one in spoken), spoken
