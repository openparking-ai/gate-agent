"""The ticket format, both directions, and the record that outlives the call.

Every test here is written from what a wrong answer would cost. A ticket that
verifies when it should not is a barrier commanded open on somebody else's
authority; one that fails to verify when it should is a customer stranded at an
exit with a photograph that is worth nothing.

**THE TEST VECTOR IS THE SUBJECT OF THIS FILE.** A format described in prose is a
format two implementations will disagree about, and the exit that reads these
tickets is a LATER ROUND in a process that does not exist yet. So the exact
bytes are pinned here, produced by the shipped code, and `docs/CONTRACT.md`
publishes the same vector from this one copy.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta

import pytest

from gate_agent.tickets import (
    CONFIRMED,
    FIELD_SEPARATOR,
    ISSUED,
    TICKET_FORMAT,
    TICKET_REF_ALPHABET,
    TICKET_REF_LENGTH,
    VENDED,
    VOID_REASONS,
    BadTicket,
    Ticket,
    TicketRecord,
    TicketStore,
    confirmed,
    is_ticket_id,
    mint,
    payload_for,
    vended,
    verify,
    voided,
)

#: QR's ALPHANUMERIC mode alphabet, from the standard: forty-five characters.
#: Written out rather than imported from the encoder, because this is the
#: property the SERIALISATION has to have and checking it against the encoder's
#: own idea of it would be comparing two copies of one claim.
QR_ALPHANUMERIC = set("0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ $%*+-./:")

#: THE TEST VECTOR. A key, a ticket, and the exact payload the shipped code
#: produces from them. Not a secret and obviously not one -- what a fixture must
#: not do is look like the real thing, and what it must do is exercise the same
#: code path.
VECTOR_KEY = b"an-example-signing-key-for-the-docs-0000"
VECTOR_TICKET = Ticket(
    ticket_ref="K7M2QRTX",
    ticket_id="",
    site="site-1",
    lane="entry",
    issued_at="2026-08-31T14:03:11+00:00",
)
VECTOR_CANONICAL = b"OPT1\nK7M2QRTX\nsite-1\nentry\n2026-08-31T14:03:11+00:00"
VECTOR_PAYLOAD = (
    "J5IFIMIKJM3U2MSRKJKFQCTTNF2GKLJRBJSW45DSPEFDEMBSGYWTAOBNGMYVIMJUHIYDGORRGEVT"
    "AMB2GAYEGEYDVKSK52N4U6PL2WPOA7DRCKV6ZYSC3EUFLF5HHT36ZFVKYUA"
)


# ---------------------------------------------------------------------------
# The serialisation
# ---------------------------------------------------------------------------


def test_the_canonical_form_is_five_newline_separated_fields_in_this_order():
    """The format, asserted against the bytes rather than against a description.

    A prose description is what two implementations disagree about. This is the
    string the signature covers, and the exit that will verify one has to be
    able to build exactly it.
    """
    assert VECTOR_TICKET.canonical() == VECTOR_CANONICAL
    assert VECTOR_CANONICAL.decode("utf-8").split(FIELD_SEPARATOR) == [
        TICKET_FORMAT,
        "K7M2QRTX",
        "site-1",
        "entry",
        "2026-08-31T14:03:11+00:00",
    ]


def test_the_published_test_vector_is_what_this_build_produces():
    """The vector, and `docs/CONTRACT.md` publishes it from here.

    A format nobody can reproduce from the document is a format the exit will
    guess at. If this goes red, either the format changed -- which is a version
    bump, not an edit -- or the vector was typed.
    """
    assert payload_for(VECTOR_TICKET, VECTOR_KEY) == VECTOR_PAYLOAD


def test_the_payload_is_entirely_inside_qrs_alphanumeric_alphabet():
    """Gokhan's requirement, and it is why the payload is base32.

    A site's `site_id` and lane names are the site's own strings -- `site-1` is
    already outside the alphabet because of its lowercase letters -- so a
    payload built from the fields verbatim would either refuse a site its own
    naming or fall back to byte mode, which is nearly twice the size for the
    same content.
    """
    assert set(VECTOR_PAYLOAD) <= QR_ALPHANUMERIC
    # And not only for the tidy vector: a site that names itself in a way nobody
    # anticipated still encodes.
    _ticket, payload = mint(
        "Estacionamiento Almirante — Nivel 2",
        "carril de entrada #3",
        "2026-08-31T14:03:11+00:00",
        VECTOR_KEY,
    )
    assert set(payload) <= QR_ALPHANUMERIC
    assert verify(payload, VECTOR_KEY).site == "Estacionamiento Almirante — Nivel 2"


def test_the_signature_is_hmac_sha256_over_the_canonical_bytes():
    """Derived from the definition rather than from a second call to our own code.

    A check that compares two copies of one claim verifies nothing, so the
    expected value here is computed from the algorithm the document names.
    """
    raw = base64.b32decode(VECTOR_PAYLOAD + "=" * (-len(VECTOR_PAYLOAD) % 8))
    assert raw[:-32] == VECTOR_CANONICAL
    assert raw[-32:] == hmac.new(VECTOR_KEY, VECTOR_CANONICAL, hashlib.sha256).digest()


# ---------------------------------------------------------------------------
# Verification, and every way it must say no
# ---------------------------------------------------------------------------


def test_a_ticket_this_site_signed_verifies_and_comes_back_field_for_field():
    """The control for every refusal below: a real one is ACCEPTED."""
    back = verify(VECTOR_PAYLOAD, VECTOR_KEY)
    assert back.ticket_ref == VECTOR_TICKET.ticket_ref
    assert back.site == VECTOR_TICKET.site
    assert back.lane == VECTOR_TICKET.lane
    assert back.issued_at == VECTOR_TICKET.issued_at
    # `ticket_id` is NOT in the payload and does not come back. It is the vend's
    # `Idempotency-Key`; putting it on a screen would publish the value that
    # makes a vend happen once.
    #
    # Asserted against a FRESHLY MINTED one, which has a real id: the vector's
    # is the empty string, and `"" not in anything` is False -- an assertion
    # that could never have failed.
    assert back.ticket_id == ""
    minted, payload = mint("site-1", "entry", "2026-08-31T14:03:11+00:00", VECTOR_KEY)
    assert minted.ticket_id and minted.ticket_id not in payload
    assert verify(payload, VECTOR_KEY).ticket_id == ""
    # THE CONTROL for that absence: the REF is in there, through the same
    # search, so the id's absence is about the payload and not about the search.
    assert minted.ticket_ref in base64.b32decode(
        payload + "=" * (-len(payload) % 8)
    ).decode("utf-8", "replace")


def test_a_flipped_byte_is_refused():
    """Every single-character change, at every position. Not one sample of one.

    A test that flipped one byte would be satisfied by a verifier that checked
    the first field and nothing else.
    """
    accepted = []
    for index in range(len(VECTOR_PAYLOAD)):
        current = VECTOR_PAYLOAD[index]
        replacement = "A" if current != "A" else "B"
        forged = VECTOR_PAYLOAD[:index] + replacement + VECTOR_PAYLOAD[index + 1 :]
        try:
            verify(forged, VECTOR_KEY)
            accepted.append(index)
        except BadTicket:
            pass
    assert accepted == [], f"a forged payload verified at position(s) {accepted}"
    # THE CONTROL: the untouched payload verifies, so the refusals above are
    # about the changes and not about a verifier that refuses everything.
    assert verify(VECTOR_PAYLOAD, VECTOR_KEY).ticket_ref == VECTOR_TICKET.ticket_ref


def test_another_sites_key_does_not_verify_this_sites_ticket():
    """The key is PER SITE, which is the whole of what the signature says."""
    with pytest.raises(BadTicket, match="not this site"):
        verify(VECTOR_PAYLOAD, b"a-different-sites-key-0000000000000000")


def test_a_forged_ticket_signed_with_a_guessed_key_is_refused():
    """The attacker's version: a well-formed ticket, everything right but the key.

    This is the case that matters, and it is different from a flipped byte: the
    canonical form is public -- it is in the contract, with a worked example --
    so anybody can build one. What they cannot build is the signature.
    """
    forged = payload_for(
        Ticket(
            ticket_ref="AAAAAAAA",
            ticket_id="",
            site="site-1",
            lane="entry",
            issued_at="2026-08-31T14:03:11+00:00",
        ),
        b"the-attackers-own-key-000000000000000",
    )
    with pytest.raises(BadTicket):
        verify(forged, VECTOR_KEY)
    # THE CONTROL: the same ticket signed with the site's key verifies, so the
    # refusal above is about the KEY and not about the ticket being unusual.
    assert verify(
        payload_for(
            Ticket(
                ticket_ref="AAAAAAAA",
                ticket_id="",
                site="site-1",
                lane="entry",
                issued_at="2026-08-31T14:03:11+00:00",
            ),
            VECTOR_KEY,
        ),
        VECTOR_KEY,
    ).ticket_ref == "AAAAAAAA"


@pytest.mark.parametrize(
    ("payload", "why"),
    [
        ("", "empty"),
        ("!!!!not base32!!!!", "not base32"),
        ("AAAA", "too short to hold a signature"),
    ],
)
def test_a_payload_that_is_not_one_is_refused_without_raising_anything_else(payload, why):
    """Everything a camera might hand this function. `BadTicket`, never a crash.

    The exit will call this on whatever a phone's camera decoded, which is an
    UNAUTHENTICATED string from outside. A `ValueError` escaping here is a
    process that dies on a smudged photograph.
    """
    with pytest.raises(BadTicket):
        verify(payload, VECTOR_KEY)


def test_a_ticket_in_another_format_version_is_refused_by_the_tag():
    """A tag is refused rather than parsed on a guess -- and it is refused AFTER
    the signature, so this is a decision about a ticket this site really issued."""
    future = Ticket(
        ticket_ref="K7M2QRTX",
        ticket_id="",
        site="site-1",
        lane="entry",
        issued_at="2026-08-31T14:03:11+00:00",
    )
    canonical = future.canonical().replace(b"OPT1", b"OPT9", 1)
    signature = hmac.new(VECTOR_KEY, canonical, hashlib.sha256).digest()
    payload = base64.b32encode(canonical + signature).decode("ascii").rstrip("=")
    with pytest.raises(BadTicket, match="ticket format"):
        verify(payload, VECTOR_KEY)


def test_a_refusal_never_quotes_the_reference_it_refused():
    """A `ticket_ref` is personal data while the stay exists, and a refusal
    message is the one place a value reaches a log without anybody deciding to
    put it there. The lane's own contract makes the same choice."""
    forged = payload_for(
        Ticket(
            ticket_ref="PLANTED9",
            ticket_id="",
            site="site-1",
            lane="entry",
            issued_at="2026-08-31T14:03:11+00:00",
        ),
        b"the-attackers-own-key-000000000000000",
    )
    with pytest.raises(BadTicket) as refused:
        verify(forged, VECTOR_KEY)
    assert "PLANTED9" not in str(refused.value)
    # THE CONTROL: the planted value really is in the thing being refused, so
    # the absence above is about the message and not about the fixture.
    assert "PLANTED9" in base64.b32decode(forged + "=" * (-len(forged) % 8)).decode(
        "utf-8", "replace"
    )


# ---------------------------------------------------------------------------
# Minting
# ---------------------------------------------------------------------------


def test_a_minted_reference_is_confusable_free_and_the_shape_the_lane_accepts():
    """Two properties at once, and they pull in different directions.

    Confusable-free is for the person reading it aloud; the lane's shape
    (`is_ticket_ref`: 6-64 of `A-Z0-9-`) is what `POST /v1/lane/vend` will take.
    A reference that satisfied one and not the other would be caught at the
    barrier rather than here.
    """
    from lane_controller.contract import is_ticket_ref

    refs = {mint("s", "l", "2026-08-31T14:03:11+00:00", VECTOR_KEY)[0].ticket_ref
            for _ in range(200)}
    assert len(refs) > 190, "references repeat far more than 200 draws should"
    for ref in refs:
        assert len(ref) == TICKET_REF_LENGTH
        assert set(ref) <= set(TICKET_REF_ALPHABET)
        assert is_ticket_ref(ref), ref
    # The alphabet excludes exactly the pairs it says it does, and INCLUDES the
    # ones it says it keeps -- a control on both halves of that sentence.
    assert not set("IO01") & set(TICKET_REF_ALPHABET)
    assert set("ZS25") <= set(TICKET_REF_ALPHABET)


def test_a_minted_id_is_the_shape_an_idempotency_key_has_to_be():
    """It IS the `Idempotency-Key`, so the lane's shape rule decides it.

    Read from the installed lane package rather than restated: a copy of that
    rule here is a copy that comes apart, and the failure would be a vend the
    lane answers `400` at the moment a driver is waiting.
    """
    from lane_controller.contract import is_idempotency_key

    ids = {mint("s", "l", "2026-08-31T14:03:11+00:00", VECTOR_KEY)[0].ticket_id
           for _ in range(200)}
    assert len(ids) == 200, "a ticket id repeated, which is a vend that would not happen"
    for one in ids:
        assert is_idempotency_key(one), one
        assert is_ticket_id(one)


def test_a_field_holding_the_separator_is_refused_rather_than_escaped():
    """An escape is a second rule to get wrong, and this one would be a ticket
    whose fields the exit splits differently from the agent that signed it."""
    with pytest.raises(BadTicket, match="separator"):
        Ticket(
            ticket_ref="K7M2QRTX",
            ticket_id="",
            site="site-1\nentry",
            lane="entry",
            issued_at="2026-08-31T14:03:11+00:00",
        ).canonical()


# ---------------------------------------------------------------------------
# The record
# ---------------------------------------------------------------------------


def a_record(ticket_id="0123456789ABCDEF0123456789ABCDEF", **over):
    body = {
        "ticket_id": ticket_id,
        "ticket_ref": "K7M2QRTX",
        "site": "site-1",
        "lane": "entry",
        "issued_at": datetime.now(UTC).isoformat(),
    }
    body.update(over)
    return TicketRecord(**body)


def test_a_record_survives_a_write_and_a_read_field_for_field(tmp_path):
    store = TicketStore(tmp_path / "tickets")
    store.open()
    record = a_record()
    store.write(record)
    assert store.read(record.ticket_id) == record
    assert store.all_ids() == (record.ticket_id,)


def test_the_states_a_ticket_moves_through_are_recorded_with_their_moments():
    record = a_record()
    assert record.state == ISSUED
    step = confirmed(record, "2026-08-31T14:04:00+00:00")
    assert (step.state, step.confirmed_at) == (CONFIRMED, "2026-08-31T14:04:00+00:00")
    step = vended(step, "2026-08-31T14:04:02+00:00", "c0ffee00")
    assert (step.state, step.vended_at, step.lane_answer) == (
        VENDED, "2026-08-31T14:04:02+00:00", "c0ffee00"
    )
    # The issue and the confirmation are still on it. A record that moved its
    # state and forgot when it was issued could not be purged by age.
    assert step.issued_at == record.issued_at and step.confirmed_at is not None


def test_a_void_reason_outside_the_closed_set_is_refused():
    with pytest.raises(ValueError, match="not a void reason"):
        voided(a_record(), "2026-08-31T14:05:00+00:00", "felt_like_it")
    for reason in VOID_REASONS:
        assert voided(a_record(), "2026-08-31T14:05:00+00:00", reason).void_reason == reason


def test_the_purge_deletes_what_is_past_the_retention_rule_and_keeps_what_is_not(tmp_path):
    """It DELETES. There is no foreign key here and no money record hanging off
    a ticket -- the platform has those where there is one -- so a retention that
    nulled fields and kept the row would not be a retention rule at all."""
    store = TicketStore(tmp_path / "tickets", retention_days=30)
    store.open()
    now = datetime.now(UTC)
    old = a_record("AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
                   issued_at=(now - timedelta(days=31)).isoformat())
    young = a_record("BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB",
                     issued_at=(now - timedelta(days=29)).isoformat())
    store.write(old)
    store.write(young)
    assert store.purge(now) == 1
    assert store.all_ids() == (young.ticket_id,)
    # And the file is GONE, not emptied.
    assert not (store.directory / f"{old.ticket_id}.json").exists()


def test_the_purge_measures_the_ISSUE_and_not_the_last_change(tmp_path):
    """A ticket voided a month after it was issued is not a fresh one. The
    personal data was created when the ticket was, and that is the age."""
    store = TicketStore(tmp_path / "tickets", retention_days=30)
    store.open()
    now = datetime.now(UTC)
    stale = voided(
        a_record("CCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC",
                 issued_at=(now - timedelta(days=40)).isoformat()),
        now.isoformat(),
        "window_elapsed",
    )
    store.write(stale)
    assert store.purge(now) == 1


def test_a_record_whose_age_cannot_be_read_is_LEFT_rather_than_swept(tmp_path):
    """A purge acting on an age it did not measure is not a retention rule.

    Deleting what it cannot parse would be the quiet version of the mistake this
    whole file exists to avoid: a rule that removes a site's records for a
    reason nobody configured.
    """
    store = TicketStore(tmp_path / "tickets", retention_days=1)
    store.open()
    strange = store.directory / "DDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDD.json"
    strange.write_text(json.dumps({"ticket_id": "x"}), encoding="utf-8")
    naive = store.directory / "EEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEE.json"
    naive.write_text(json.dumps({"issued_at": "2020-01-01T00:00:00"}), encoding="utf-8")
    assert store.purge(datetime.now(UTC)) == 0
    assert strange.exists() and naive.exists()
    # THE CONTROL: a readable, aware, old one in the same directory IS purged,
    # so the two above were left for their own reason and not because the purge
    # did nothing at all.
    store.write(a_record("FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF",
                         issued_at="2020-01-01T00:00:00+00:00"))
    assert store.purge(datetime.now(UTC)) == 1


def test_a_crash_leaves_no_half_written_record(tmp_path):
    """Written to a temporary name and renamed, so a crash leaves the previous
    version or the new one and never half of either."""
    store = TicketStore(tmp_path / "tickets")
    store.open()
    leftover = store.directory / ".writing-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA.json"
    leftover.write_text("{half", encoding="utf-8")
    store.open()
    assert not leftover.exists()
    assert store.all_ids() == ()


def test_a_ticket_id_that_is_not_one_never_becomes_a_path(tmp_path):
    """A record is named by its id, and an id carrying a `/` or a `..` would
    write a site's records somewhere nobody declared."""
    store = TicketStore(tmp_path / "tickets")
    store.open()
    for bad in ("../escape", "a/b", "", "0123456789abcdef0123456789abcdef"):
        with pytest.raises(ValueError, match="uppercase hex"):
            store.write(a_record(bad))


# ---------------------------------------------------------------------------
# Properties of the SOURCE, because behaviour cannot show them
# ---------------------------------------------------------------------------


def test_the_signature_is_compared_in_constant_time():
    """`hmac.compare_digest`, read out of the source, because BEHAVIOUR cannot
    tell it from `==`.

    `list(a) != list(b)` gives the same answers to every input this suite can
    write. What differs is how long it takes on a near-miss, and a `==` there
    leaks how much of a forged signature was right one byte at a time -- to
    anybody who can measure the call, and the exit that will make it is a route
    somebody drives up to. A timing test would measure this flakily and on a
    loaded CI runner would measure the runner.

    So it is a source property, swept the way `test_no_opening_authority.py`
    sweeps for a request that is not a GET.
    """
    import ast
    from pathlib import Path

    import gate_agent.tickets as module

    tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
    verify_fn = next(
        node for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "verify"
    )
    compares = [
        node for node in ast.walk(verify_fn)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "compare_digest"
    ]
    assert compares, (
        "`verify` does not call `hmac.compare_digest`. A signature compared with `==` leaks "
        "how much of a forged one was right, one byte at a time."
    )
    # AND NOTHING ELSE COMPARES THE SIGNATURE. A `compare_digest` that is
    # present while an `==` decides the answer is the shape this would take.
    signature_names = {"signature", "expected"}
    loose = [
        node for node in ast.walk(verify_fn)
        if isinstance(node, ast.Compare)
        and isinstance(node.left, ast.Name)
        and node.left.id in signature_names
    ]
    assert not loose, "the signature is compared with an operator somewhere in `verify`"

    # THE CONTROL: the same sweep over source that DOES compare it loosely finds
    # it, so the two assertions above are about this module rather than about a
    # walk that matches nothing.
    planted = ast.parse(
        "def verify(a, b):\n"
        "    if signature != expected:\n"
        "        raise BadTicket('no')\n"
    )
    planted_fn = next(
        node for node in ast.walk(planted)
        if isinstance(node, ast.FunctionDef) and node.name == "verify"
    )
    assert not [
        node for node in ast.walk(planted_fn)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "compare_digest"
    ]
    assert [
        node for node in ast.walk(planted_fn)
        if isinstance(node, ast.Compare)
        and isinstance(node.left, ast.Name)
        and node.left.id in signature_names
    ]
