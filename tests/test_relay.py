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


def settle(agent, clock, attempts: int = 500) -> None:
    """Poll until the relay pulse has been collected, WITHOUT moving the clock.

    The pulse runs on its own thread now -- `poll()` used to make the request
    itself and stopped the agent for the length of it -- so a test has to wait
    for a real thread rather than for a fixture's clock. The clock is left alone
    while waiting because `[escalation] nothing_usable_seconds` bounds how long
    the OPERATOR waits, and advancing two seconds per poll would fire that bound
    in a fifth of a second of real time.

    Afterwards the clock is moved, because what was queued for the operator is
    played on the file's own measured duration.
    """
    import time

    from gate_agent.contract import AgentEventKind

    settled = {AgentEventKind.RELAY_PULSED.value, AgentEventKind.RELAY_PULSE_FAILED.value}
    for _ in range(attempts):
        agent.poll()
        if any(one["kind"] in settled for one in agent.events(0).to_dict()["events"]):
            break
        time.sleep(0.005)
    for _ in range(10):
        agent.poll()
        clock.advance(2.0)


#: The challenge a real Axis unit sends, and the default this fake sends.
DIGEST_CHALLENGE = 'Digest realm="AXIS", qop="auth", nonce="0123456789abcdef"'


def _md5(text: str) -> str:
    import hashlib

    return hashlib.md5(text.encode("utf-8")).hexdigest()  # noqa: S324


def _fields(header: str) -> dict[str, str]:
    """The parameters of an `Authorization` or `WWW-Authenticate` header."""
    parts = header.split(None, 1)
    return {
        key.strip().lower(): value.strip().strip('"')
        for key, _, value in (
            one.partition("=") for one in (parts[1] if len(parts) > 1 else "").split(",")
        )
        if value
    }


class FakeAxis:
    """A unit that answers as the document says, and refuses anything else.

    **It challenges with Digest and it VERIFIES the response.** It used to check
    only that an `Authorization` header was PRESENT -- it said so itself -- so it
    verified no digest, no realm and no qop, and it accepted a `Basic` header
    identically. The round-7 brief described the Digest exchange as tested; it
    was not, and every row of the matrix below rests on this class actually
    computing RFC 7616's response from the realm, the nonce, the qop and the
    credential it was given.
    """

    def __init__(self, challenge: str = DIGEST_CHALLENGE) -> None:
        self.requests: list[tuple[str, str]] = []
        self.authenticated: list[str] = []
        #: What this unit answers instead of the documented empty body, for the
        #: test that requires anything else to be refused.
        self.body = b""
        self.status = 200
        #: Whether this unit refuses EVERY credential, which is what a wrong
        #: password looks like from out here.
        self.refuse_always = False
        #: What it challenges with. A test that means to exercise a challenge
        #: this build cannot use passes one here.
        self.challenge = challenge
        #: Every `Authorization` this unit checked: `(scheme, verified)`.
        self.checked: list[tuple[str, bool]] = []
        #: How long the unit takes to answer, in seconds. A unit that HOLDS THE
        #: CONNECTION for the length of the contact is the case the derived
        #: timeout exists for, and nothing has measured whether a real one does.
        self.answer_after = 0.0

    def verify(self, header: str, method: str, uri: str) -> bool:
        """RFC 7616's response, computed here from the challenge WE sent."""
        scheme = header.split()[0] if header.split() else ""
        given = _fields(header)
        sent = _fields(self.challenge)
        if scheme.lower() != "digest":
            self.checked.append((scheme, False))
            return False
        ha1 = _md5(f"{USER}:{sent.get('realm', '')}:{PASSWORD}")
        ha2 = _md5(f"{method}:{given.get('uri', uri)}")
        if sent.get("qop"):
            expected = _md5(
                f"{ha1}:{sent['nonce']}:{given.get('nc')}:{given.get('cnonce')}:"
                f"{given.get('qop')}:{ha2}"
            )
        else:
            expected = _md5(f"{ha1}:{sent['nonce']}:{ha2}")
        ok = (
            given.get("realm") == sent.get("realm")
            and given.get("nonce") == sent.get("nonce")
            and given.get("response") == expected
        )
        self.checked.append((scheme, ok))
        return ok


def axis_server(unit: FakeAxis):
    class _Handler(BaseHTTPRequestHandler):
        server_version = "fake-axis"
        sys_version = ""

        def log_message(self, fmt, *args):
            pass

        def _challenge(self):
            self.send_response(401)
            self.send_header("WWW-Authenticate", unit.challenge)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_GET(self):  # noqa: N802  (http.server's spelling)
            unit.requests.append(("GET", self.path))
            header = self.headers.get("Authorization")
            if not header or unit.refuse_always:
                return self._challenge()
            if not unit.verify(header, "GET", self.path):
                return self._challenge()
            if unit.answer_after:
                import time

                time.sleep(unit.answer_after)
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
            settle(agent, clock)
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
    # AND NOTHING WAS PULSED, because there is nothing here to pulse: a door
    # with no relay never starts a thread and never writes either relay event.
    kinds = {one["kind"] for one in agent.events(0).to_dict()["events"]}
    assert "relay_pulsed" not in kinds and "relay_pulse_failed" not in kinds, kinds


# ---------------------------------------------------------------------------
# THE DIGEST MATRIX, against a unit that VERIFIES the digest
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("challenge", "kw", "outcome", "says"),
    [
        (DIGEST_CHALLENGE, {}, "pulsed", ""),
        # RFC 2069 -- a challenge with no `qop` at all, which is what an older
        # unit sends. It is answered, and it is answered CORRECTLY: the fake
        # computes the no-qop response.
        ('Digest realm="AXIS", nonce="0123456789abcdef"', {}, "pulsed", ""),
        # THE UNIT ANSWERED, and what it answered is something this build cannot
        # use. Every one of these was `RelayUnreachable`, an uncaught
        # `ValueError`, or a bare status with no reason on it.
        (
            'Digest realm="AXIS", qop="auth-int", nonce="0123456789abcdef"',
            {},
            "refused",
            "auth-int",
        ),
        ('Digest qop="auth", nonce="0123456789abcdef"', {}, "refused", "no realm"),
        ('Digest realm="AXIS", qop="auth"', {}, "refused", "no nonce"),
        ('Basic realm="AXIS"', {}, "refused", "'Basic'"),
        (DIGEST_CHALLENGE, {"refuse_always": True}, "refused", "401"),
        (DIGEST_CHALLENGE, {"body": b"<html>login</html>"}, "refused", "empty one"),
    ],
)
def test_the_digest_matrix(challenge, kw, outcome, says):
    """Seven challenges and a body, and NOTHING escapes as anything else.

    The row that mattered is `Digest` with no `realm`: `urllib`'s auth handlers
    raise a bare `ValueError` for a challenge they cannot parse, this module
    caught four classes, and `_standalone_opens` caught two -- so it raised
    straight out of `poll()`. The barrier did not move, the operator who had
    just authorised it was told nothing, and `relay_pulsed` stood on the event
    stream.
    """
    unit = FakeAxis(challenge)
    for key, value in kw.items():
        setattr(unit, key, value)
    with serving(axis_server(unit)) as url:
        relay = AxisRelay(a_relay(url=url))
        if outcome == "pulsed":
            relay.pulse()
            assert unit.authenticated, "the unit was never given a credential"
            assert unit.checked and all(ok for _scheme, ok in unit.checked), unit.checked
            return
        with pytest.raises(RelayRefusedUs) as refused:
            relay.pulse()
        assert says in str(refused.value), str(refused.value)


def test_a_unit_that_answers_is_never_reported_as_one_that_did_not():
    """B18, and it is the distinction this module states as load-bearing.

    `RelayUnreachable` is *"the unit did not answer"* and `RelayRefusedUs` is
    *"the unit ANSWERED, and what it answered was no"*, and they name different
    repairs -- a network and a device. `qop="auth-int"` was reported as silence,
    so whoever was sent looked at the network.
    """
    unit = FakeAxis('Digest realm="AXIS", qop="auth-int", nonce="0123456789abcdef"')
    with serving(axis_server(unit)) as url:
        with pytest.raises(RelayRefusedUs):
            AxisRelay(a_relay(url=url)).pulse()
        assert unit.requests, "the unit was never reached"
    # THE CONTROL: nothing listening at all IS `RelayUnreachable`, so the
    # distinction is measured in both directions.
    with pytest.raises(RelayUnreachable):
        AxisRelay(a_relay(url="http://127.0.0.1:1"), timeout=0.2).pulse()


def test_the_timeout_is_derived_from_the_pulse_and_a_slow_unit_still_pulses():
    """B13. `pulse_ms` was unbounded and the HTTP timeout was a hard-coded 5.0 s
    no site could change.

    A barrier needing a six-second contact is a legal configuration this build
    accepts, and it was then reported as a relay that could not be REACHED --
    while the unit was mid-pulse and the barrier very probably opening, with
    `relay_pulsed` already on the event stream and the operator told the
    opposite. Three surfaces, three answers, and the gate open.
    """
    relay = a_relay(pulse_ms=6000)
    assert relay.timeout == 6.0 + 5.0, "the timeout is not derived from the pulse"
    assert AxisRelay(relay).timeout == 11.0

    unit = FakeAxis()
    unit.answer_after = 1.2
    with serving(axis_server(unit)) as url:
        # A unit that holds the connection for LONGER than the old fixed 5.0 s
        # would have been unreachable; here the margin is small and the pulse is
        # short, so the derivation is what decides.
        AxisRelay(a_relay(url=url, pulse_ms=500)).pulse()
    # THE CONTROL: the same unit under a timeout that does not cover its answer.
    unit2 = FakeAxis()
    unit2.answer_after = 1.2
    with serving(axis_server(unit2)) as url:
        with pytest.raises(RelayUnreachable):
            AxisRelay(a_relay(url=url, pulse_ms=500), timeout=0.3).pulse()


def test_a_basic_challenge_is_refused_rather_than_answered():
    """A credential that opens a barrier is not sent under a scheme that carries
    it in the clear. It used to be answered: `Basic` was behind Digest in the
    opener, and the fake accepted the header without looking at it."""
    unit = FakeAxis('Basic realm="AXIS"')
    with serving(axis_server(unit)) as url:
        with pytest.raises(RelayRefusedUs, match="Digest only"):
            AxisRelay(a_relay(url=url)).pulse()
    # AND NO `Basic` HEADER EVER REACHED IT.
    assert not any(scheme.lower() == "basic" for scheme, _ok in unit.checked), unit.checked


# ---------------------------------------------------------------------------
# WHAT THE RECORD SAYS, DRIVEN THROUGH THE WHOLE AGENT
# ---------------------------------------------------------------------------


def standalone_agent(tmp_path, url, *, port=1, pulse_ms=500):
    """A standalone door with a relay, a key and a store. No lane anywhere."""
    from dataclasses import replace

    from conftest import (
        FakeClock,  # noqa: I001
        agent_config_for,
        agent_for,
    )
    from fake_ua import FakeUa
    from gate_agent.config import TicketSettings

    base = agent_config_for(tmp_path, standalone=True)
    config = replace(
        base,
        intercoms=(
            replace(base.intercoms[0], relay=a_relay(url=url, port=port, pulse_ms=pulse_ms)),
        ),
        tickets=TicketSettings(
            signing_key=b"a-signing-key-long-enough-for-the-floor",
            directory=tmp_path / "tickets",
        ),
        driver_languages=("en",),
    )
    ua = FakeUa()
    clock = FakeClock()
    return agent_for(config, ua, clock=clock), ua, clock


def open_now_standalone(agent, ua, clock):
    """The whole dialogue to `OPEN_NOW`, then wait for the pulse to settle."""
    from conftest import INTERCOM_ACCOUNT

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
    settle(agent, clock)


def outcome_of(agent):
    """What the site's own record of this barrier says: events, code, record."""
    from gate_agent.tickets import TicketStore

    events = agent.events(0).to_dict()["events"]
    store = TicketStore(agent.config.tickets.directory)
    records = [store.read(one) for one in store.all_ids()]
    return {
        "pulsed": [one for one in events if one["kind"] == "relay_pulsed"],
        "failed": [one for one in events if one["kind"] == "relay_pulse_failed"],
        "records": [(one.state, one.void_reason) for one in records],
        "codes": {
            (entry["code"], entry["subject"]): entry["state"]
            for entry in agent.health().to_dict()["codes"]
        },
        "operator": [path for leg, path in agent.ua.played if leg == "operator"],
    }


def test_relay_pulsed_is_written_only_after_the_unit_answered(tmp_path):
    """B5. It was recorded BEFORE the request and stood when the pulse failed.

    The operator was told the truth; the RECORD was not -- and at a standalone
    site `/v1/agent/events` is what a monitor reads, what the site keeps, and
    the only machine-readable account of a barrier there is. The ticket record
    was left `issued` in both failure cases too.
    """
    unit = FakeAxis()
    with serving(axis_server(unit)) as url:
        agent, ua, clock = standalone_agent(tmp_path, url)
        open_now_standalone(agent, ua, clock)
        answered = outcome_of(agent)
    assert len(answered["pulsed"]) == 1 and answered["failed"] == []
    assert unit.authenticated, "the barrier was never asked to move"
    assert answered["records"] == [("vended", None)], answered["records"]
    assert any("operator.vend_commanded" in one for one in answered["operator"])

    # THE UNIT REFUSES US: it answered, and what it answered was no.
    refusing = FakeAxis()
    refusing.refuse_always = True
    with serving(axis_server(refusing)) as url:
        agent, ua, clock = standalone_agent(tmp_path / "refused", url)
        open_now_standalone(agent, ua, clock)
        refused = outcome_of(agent)
    assert refused["pulsed"] == [], "the record says the relay was pulsed and it was not"
    assert len(refused["failed"]) == 1
    assert refused["failed"][0]["cause"], "a failure with no cause sends nobody anywhere"
    assert refused["failed"][0]["relay_port"] == 1
    assert refused["records"] == [("voided", "relay_failed")], refused["records"]
    assert refused["codes"][("relay_refused_us", "sip:door1@10.0.0.9")] == "active"
    assert any("operator.cannot_open" in one for one in refused["operator"])

    # AND A UNIT THAT IS NOT THERE AT ALL.
    agent, ua, clock = standalone_agent(tmp_path / "silent", "http://127.0.0.1:1")
    open_now_standalone(agent, ua, clock)
    silent = outcome_of(agent)
    assert silent["pulsed"] == [] and len(silent["failed"]) == 1
    assert silent["records"] == [("voided", "relay_failed")], silent["records"]
    assert silent["codes"][("relay_unreachable", "sip:door1@10.0.0.9")] == "active"
    assert any("operator.cannot_open" in one for one in silent["operator"])
    # NO CREDENTIAL on the cause, which is the one new free-text field here.
    import json as _json

    assert PASSWORD not in _json.dumps(silent["failed"] + refused["failed"])


def test_a_challenge_this_build_cannot_use_still_reaches_the_operator(tmp_path):
    """B6, through the WHOLE agent. `poll()` used to raise `ValueError` here.

    The barrier did not move; the operator -- who had just authorised opening
    it -- was told nothing at all: no `operator.vend_commanded`, no
    `operator.cannot_open`, and the call was torn down in silence. Neither relay
    code fired, `relay_pulsed` stood, and `cli.py` swallowed the exception into a
    log line. Four surfaces, and the only correct one was the log.
    """
    unit = FakeAxis('Digest qop="auth", nonce="0123456789abcdef"')
    with serving(axis_server(unit)) as url:
        agent, ua, clock = standalone_agent(tmp_path, url)
        open_now_standalone(agent, ua, clock)     # this used to RAISE
        seen = outcome_of(agent)
    assert seen["pulsed"] == [], seen
    assert len(seen["failed"]) == 1 and "no realm" in seen["failed"][0]["cause"]
    assert seen["codes"][("relay_refused_us", "sip:door1@10.0.0.9")] == "active"
    assert any("operator.cannot_open" in one for one in seen["operator"]), seen["operator"]
    assert seen["records"] == [("voided", "relay_failed")], seen["records"]


def test_the_agent_keeps_polling_while_the_relay_is_being_pulsed(tmp_path):
    """The pulse is not made inside `poll()` any more.

    It was, so for up to the whole timeout the agent played nothing, answered
    nothing and polled no lane -- on the one path in this repository that moves
    a barrier. Measured: a poll DURING the pulse returns, and it returns quickly.
    """
    import time

    unit = FakeAxis()
    unit.answer_after = 0.75
    with serving(axis_server(unit)) as url:
        agent, ua, clock = standalone_agent(tmp_path, url)
        from conftest import INTERCOM_ACCOUNT

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
        started = time.monotonic()
        agent.poll()          # STARTS the pulse
        agent.poll()          # and this one is not held by it
        during = time.monotonic() - started
        assert during < 0.5, f"poll() was held for {during:.2f}s by the pulse"
        settle(agent, clock)
        assert len(outcome_of(agent)["pulsed"]) == 1


def test_anything_the_unit_raises_is_still_one_of_the_two_refusals():
    """THE CATCH-ALL, measured rather than assumed.

    `pulse()` used to map four exception classes, and `urllib`'s own auth
    machinery raises outside them for a challenge it cannot parse -- which is
    how a unit on the site's LAN raised straight out of `poll()`. The question
    a control has to answer is not "does this build handle the shapes we have
    seen" but "can anything at all leave this method unnamed", so this plants
    one that no real unit produces today.
    """

    class Exploding:
        def open(self, request, timeout=None):
            raise ValueError("a challenge this build cannot parse")

    relay = AxisRelay(a_relay())
    relay._opener = Exploding()
    with pytest.raises(RelayRefusedUs, match="ValueError"):
        relay.pulse()

    class Exploding2:
        def open(self, request, timeout=None):
            raise KeyError("something nobody has seen")

    relay2 = AxisRelay(a_relay())
    relay2._opener = Exploding2()
    with pytest.raises(RelayRefusedUs, match="KeyError"):
        relay2.pulse()
