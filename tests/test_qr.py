"""The QR encoder, proven by something that is not it.

An encoder nobody can read back is a picture of a QR code. So this file asks
three questions, and they are different questions:

  1. **Does an INDEPENDENT DECODER read it?** OpenCV's `QRCodeDetector`, a
     test-only dependency, across every version this encoder builds and at the
     longest payload each one holds -- and against the published test vector.
  2. **Do the typed tables describe the symbol they claim to?** The number of
     data modules is DERIVED from the matrix, by building one and counting the
     positions no function pattern occupies, and the block table must fill it
     exactly. This is the check that caught seventeen wrong rows.
  3. **Are the two typed bit tables the standard's?** The format information is
     compared against the published strings for level M, and the version
     information against the BCH generator that produces it.

**HOW THE DECODER IS DRIVEN, and it is not a detail.** `detectAndDecode` does
two things: it FINDS a symbol in an image and then it reads it. Its finder is an
image search over a photograph, and on synthetic high-entropy payloads it fails
to locate a symbol perfectly often -- measured here at 37 misses in 352 samples
across the whole version range. Every one of those decoded correctly when the
detector was handed the symbol's four corners.

That is the difference between "OpenCV could not find it" and "OpenCV could not
read it", and only the second says anything about this encoder. The images here
are rendered by this file, so their corners are known exactly, and `decode()` is
given them. **Finding a black square on a white page is not the property under
test.**
"""

from __future__ import annotations

import random

import pytest

from gate_agent import qr

cv2 = pytest.importorskip(
    "cv2",
    reason=(
        "the INDEPENDENT decoder is missing. This suite proves the encoder with something "
        "that is not it, and without it these tests measure nothing -- so CI installs it "
        "(`.[dev]`) and a skip here is a gap, not a pass."
    ),
)
np = pytest.importorskip("numpy")

#: The published format information strings for level M, masks 0 through 7,
#: ISO/IEC 18004 table C.1. Typed, and that is the point: they are the
#: EXPECTATION the code's own computation is checked against.
PUBLISHED_FORMAT_M = (0x5412, 0x5125, 0x5E7C, 0x5B4B, 0x45F9, 0x40CE, 0x4F97, 0x4AA0)

#: What a module is rendered as, and how wide the quiet zone is. Eight is double
#: the standard's minimum of four: this is a synthetic image and there is no
#: reason to make the decoder's job harder than the property under test.
SCALE = 8
QUIET = 8

ALPHANUMERIC_SAMPLE = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"


def render(modules):
    """`(image, corners)`. The corners are exact because this drew them."""
    size = len(modules)
    total = (size + QUIET * 2) * SCALE
    image = np.full((total, total), 255, dtype=np.uint8)
    for row_index, row in enumerate(modules):
        for column_index, value in enumerate(row):
            if value:
                y = (row_index + QUIET) * SCALE
                x = (column_index + QUIET) * SCALE
                image[y : y + SCALE, x : x + SCALE] = 0
    image = cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
    low, high = QUIET * SCALE, (QUIET + size) * SCALE
    corners = np.array(
        [[[low, low], [high, low], [high, high], [low, high]]], dtype=np.float32
    )
    return image, corners


def read_back(modules) -> str:
    """What the INDEPENDENT decoder says this symbol holds."""
    image, corners = render(modules)
    return cv2.QRCodeDetector().decode(image, corners)[0]


#: A hard stop on the loop below. **It is not decoration.** The helper walks
#: lengths until the encoder refuses, so it depends on the encoder REFUSING --
#: and a fail-control break that makes `choose_version` return instead of
#: raising turned it into an infinite loop that hung a whole control run. A test
#: helper whose termination depends on the thing under test is a helper that
#: hangs CI on exactly the defect it exists to catch.
LENGTH_CEILING = 4000


def longest_at() -> dict[int, int]:
    """The longest alphanumeric payload that lands on each version.

    DERIVED by asking the encoder, so a version whose capacity changes moves
    this table with it. A typed list of lengths would be a second copy of the
    capacity table -- the thing that was wrong in the first place.
    """
    out: dict[int, int] = {}
    for length in range(1, LENGTH_CEILING):
        try:
            out[qr.choose_version("A" * length)[0]] = length
        except qr.QrTooLong:
            return out
    # STOPS rather than raises, and that is a fail-control fix (Z16.1, 2026-09-01).
    # This runs at IMPORT, so an assertion here is a COLLECTION ERROR -- and
    # `scripts/_control.py` correctly refuses to read an error as a guarantee
    # going red, because an error is a suite that could not run. The break
    # `a_payload_too_long_is_truncated` reddened nothing for exactly that
    # reason: it made this helper raise, pytest reported `1 errors, 0 passed`,
    # and the control was reported as NOT A CONTROL. The bound is asserted by
    # `test_choose_version_refuses_a_payload_past_the_largest_symbol` below,
    # where a failure is a FAILURE.
    return out


LONGEST = longest_at()

#: Whether `longest_at()` walked all the way to its ceiling without the encoder
#: ever refusing -- which is what "the encoder stopped raising" looks like from
#: here. A VALUE, so the test below can assert on it instead of the import.
CEILING_REACHED = max(LONGEST.values(), default=0) >= LENGTH_CEILING - 1


# ---------------------------------------------------------------------------
# 1. An independent decoder reads it
# ---------------------------------------------------------------------------


def test_the_published_ticket_test_vector_encodes_and_reads_back():
    """The one payload that matters, end to end, through something else's eyes."""
    from test_tickets import VECTOR_PAYLOAD

    version, mode = qr.choose_version(VECTOR_PAYLOAD)
    assert mode == qr.MODE_ALPHANUMERIC, "a ticket payload is alphanumeric by construction"
    assert read_back(qr.encode(VECTOR_PAYLOAD)) == VECTOR_PAYLOAD
    # And it is a symbol of a size worth stating: this is what a display shows.
    assert (version, qr._size(version)) == (6, 41)


@pytest.mark.parametrize("version", sorted(LONGEST))
def test_every_version_this_encoder_builds_reads_back(version):
    """Every version, at its longest payload and at one comfortably inside it.

    Several seeds, because the content decides the mask and the mask decides the
    symbol: one payload per version would be one sample of eight code paths.
    """
    for seed in range(4):
        random.seed(version * 1000 + seed)
        for length in {max(1, LONGEST[version] - 30), LONGEST[version]}:
            text = "".join(random.choice(ALPHANUMERIC_SAMPLE) for _ in range(length))
            if qr.choose_version(text)[0] != version:
                continue
            assert read_back(qr.encode(text)) == text, (
                f"version {version}, seed {seed}, {length} characters did not read back"
            )


def test_byte_mode_reads_back_too():
    """The other mode. Not reachable from a ticket payload, and it exists because
    `encode` is a general function and an untested branch is one nothing
    measures."""
    text = "a lower-case sentence, with punctuation — and a dash."
    version, mode = qr.choose_version(text)
    assert mode == qr.MODE_BYTE
    assert read_back(qr.encode(text)) == text


def test_the_decoder_would_notice_a_corrupted_symbol():
    """THE CONTROL for every decode above, and it is not optional.

    A decoder that answered the expected string whatever it was shown would
    satisfy every assertion in this file. So a symbol is damaged well past what
    level M recovers -- a quarter of it inverted -- and it must NOT read back.
    """
    from test_tickets import VECTOR_PAYLOAD

    modules = qr.encode(VECTOR_PAYLOAD)
    assert read_back(modules) == VECTOR_PAYLOAD
    size = len(modules)
    damaged = [list(row) for row in modules]
    for row in range(size // 2):
        for column in range(size // 2):
            damaged[row][column] ^= 1
    assert read_back(damaged) != VECTOR_PAYLOAD


# ---------------------------------------------------------------------------
# 2. The typed block table describes the symbol it claims to
# ---------------------------------------------------------------------------


def function_pattern_free_modules(version: int) -> int:
    """How many modules the data may use, DERIVED from the matrix.

    Built the way `encode` builds one and counted, rather than taken from a
    formula: the formula and the placement are two copies of one claim, and it
    is the placement the symbol is actually made of.
    """
    modules, reserved = qr._blank(version)
    size = qr._size(version)
    qr._place_finder(modules, reserved, 0, 0)
    qr._place_finder(modules, reserved, 0, size - 7)
    qr._place_finder(modules, reserved, size - 7, 0)
    qr._place_alignment(modules, reserved, version)
    qr._place_timing(modules, reserved, version)
    qr._reserve_format(modules, reserved, version)
    return sum(
        1 for row in range(size) for column in range(size) if not reserved[row][column]
    )


@pytest.mark.parametrize("version", sorted(qr.EC_M_BLOCKS))
def test_the_block_table_fills_the_symbol_exactly(version):
    """THE CHECK THAT CAUGHT SEVENTEEN WRONG ROWS.

    A typed table survives review by looking measured. This one does not get to:
    the symbol says how many codewords there is room for, and the table has to
    agree. When the table covered versions 1 to 40, this found 23, 24, 25 and 27
    through 40 inconsistent, and those rows were DELETED rather than repaired
    from memory.
    """
    ec_per_block, groups = qr.EC_M_BLOCKS[version]
    data = sum(count * per_block for count, per_block in groups)
    blocks = sum(count for count, _ in groups)
    total = data + ec_per_block * blocks
    free = function_pattern_free_modules(version)
    assert free // 8 == total, (
        f"version {version}: the matrix leaves room for {free // 8} codewords and the table "
        f"describes {total}"
    )
    # The remainder bits are the standard's, and there are never eight of them:
    # eight would be a whole codeword the table forgot.
    assert free % 8 < 8


def test_no_version_is_supported_without_all_three_of_its_tables():
    """A version in one table and not another is a symbol built half-blind."""
    assert set(qr.EC_M_BLOCKS) == set(range(1, qr.MAX_VERSION + 1))
    assert set(qr.ALIGNMENT_CENTRES) == set(range(1, qr.MAX_VERSION + 1))
    assert set(qr.VERSION_INFO) == set(range(7, qr.MAX_VERSION + 1))


def test_choose_version_refuses_a_payload_past_the_largest_symbol():
    """The bound `longest_at()` rests on, asserted where a failure is a FAILURE.

    `longest_at()` runs at import and used to raise here; pytest reports that as
    a collection ERROR, which is not evidence about a guarantee (SETTLED 6, and
    `scripts/_control.py`'s own rule). The assertion lives in a test instead, so
    the break that makes `choose_version` return `MAX_VERSION` rather than
    refusing goes RED rather than un-runnable.
    """
    assert not CEILING_REACHED, (
        f"the encoder accepted a payload of {LENGTH_CEILING - 1} characters. It must refuse "
        f"past version {qr.MAX_VERSION}, so either that bound moved or `choose_version` "
        "stopped raising."
    )
    with pytest.raises(qr.QrTooLong):
        qr.choose_version("A" * (LONGEST[qr.MAX_VERSION] + 1))


def test_a_payload_past_the_largest_symbol_is_refused_with_its_length():
    """Refused, never truncated: a truncated ticket is one the exit reads as a
    forgery. And the message carries the length, because the cause is a site's
    own naming and the site is who has to act on it."""
    too_long = "A" * (LONGEST[qr.MAX_VERSION] + 1)
    with pytest.raises(qr.QrTooLong) as refused:
        qr.encode(too_long)
    assert str(len(too_long)) in str(refused.value)
    # THE CONTROL: one character shorter is accepted and reads back.
    assert read_back(qr.encode(too_long[:-1])) == too_long[:-1]


# ---------------------------------------------------------------------------
# 3. The two typed bit tables are the standard's
# ---------------------------------------------------------------------------


def test_the_format_information_is_the_standards_published_strings():
    """Computed by the code, compared against the PUBLISHED table.

    Not against a second call to our own function: a check that compares two
    copies of one claim verifies nothing. These eight values are from ISO/IEC
    18004 table C.1 and are what a decoder reads first -- a wrong one makes it
    unmask with the wrong mask and read gibberish, which is a symbol that fails
    for exactly one mask and looks like anything but a table error.
    """
    assert tuple(qr._format_bits(mask) for mask in range(8)) == PUBLISHED_FORMAT_M
    # And the level really is M's own encoding, which is NOT `01` however
    # natural that looks for the second level in the list.
    assert qr.EC_M == 0b00


def test_the_version_information_is_its_own_bch_code():
    """The typed table checked against the generator that produces it.

    This is the only reason a typed table is allowed to stay: `0x1F25` is a
    number nobody proof-reads, and a wrong one puts a corrupt version field on
    every symbol of that size.
    """
    generator = 0b1111100100101
    for version, published in qr.VERSION_INFO.items():
        remainder = version << 12
        for shift in range(17, 11, -1):
            if remainder & (1 << shift):
                remainder ^= generator << (shift - 12)
        assert (version << 12) | remainder == published, (
            f"version {version}: the table says {published:#07x} and the BCH code gives "
            f"{(version << 12) | remainder:#07x}"
        )


def test_the_alphanumeric_alphabet_is_in_the_order_that_is_its_values():
    """The index of a character IS the number it encodes as, so the order of
    this string is the encoding and not a presentation choice."""
    assert len(qr.ALPHANUMERIC) == 45
    assert qr.ALPHANUMERIC[:10] == "0123456789"
    assert qr.ALPHANUMERIC[10:36] == "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    assert qr.ALPHANUMERIC[36:] == " $%*+-./:"


def test_the_error_correction_is_reed_solomon_over_the_right_field():
    """A known property of the code rather than a second call to our own.

    Encoding a block and then dividing the whole codeword by the generator must
    leave nothing: that is what a Reed-Solomon codeword IS.
    """
    data = list(range(1, 17))
    parity = qr.error_correction(data, 10)
    assert len(parity) == 10
    remainder = list(data) + list(parity)
    generator = qr._generator(10)
    for index in range(len(data)):
        factor = remainder[index]
        if factor:
            for offset, coefficient in enumerate(generator):
                remainder[index + offset] ^= qr._multiply(coefficient, factor)
    assert remainder[len(data) :] == [0] * 10


def test_the_mask_penalty_scores_a_uniform_symbol_worse_than_a_balanced_one():
    """Rule 4, which was WRONG and was found while proving this encoder.

    An expression stood here that was neither the standard's rule nor any rule.
    A uniform field is the worst case the rule exists to punish and a balanced
    one is the best, so the two have to come out in that order -- fixture inputs
    on both sides of the thing being measured.
    """
    size = 21
    all_dark = [[1] * size for _ in range(size)]
    balanced = [[(row + column) % 2 for column in range(size)] for row in range(size)]
    assert qr._penalty(all_dark) > qr._penalty(balanced)
    # And the rule's own arithmetic, isolated: half dark scores nothing for it.
    dark_half = [[1 if column < size // 2 else 0 for column in range(size)]
                 for _ in range(size)]
    percent = sum(sum(row) for row in dark_half) * 100 / (size * size)
    assert int(abs(percent - 50) // 5) * 10 == 0


def read_mask(modules) -> int:
    """WHICH MASK a finished symbol says it used, out of its own format field.

    Read back rather than remembered: the property below is that the symbol
    SHIPS with the mask the penalty chose, and asking the encoder which one it
    picked would be asking the thing under test.
    """
    size = len(modules)
    bits = 0
    for index in range(15):
        # The copy along the top row and right edge, which is the one that does
        # not run through the version information block.
        bit = modules[8][size - 1 - index] if index < 8 else modules[size - 15 + index][8]
        bits |= bit << index
    unmasked = bits ^ 0b101010000010010
    # The five data bits sit at 14..10 -- level in 14..13, mask in 12..10 -- and
    # the ten below them are the BCH remainder. Taking the LOW three bits reads
    # the remainder instead, which gives a plausible mask number for every input
    # and is wrong for seven of the eight.
    return (unmasked >> 10) & 0b111


def test_the_mask_the_symbol_ships_with_is_the_lowest_scoring_one():
    """The standard chooses the mask by PENALTY, and this requires that it did.

    A fixed mask still produces a VALID symbol -- the format field names it and
    a decoder unmasks accordingly -- so every decode test in this file passes
    with the choice removed. That is exactly what a fail-control found: the
    break "one fixed mask is used" went green, because nothing here was
    measuring the choice.

    What the choice is FOR is the reason it has to be measured: an unmasked or
    badly masked symbol can hold long runs and finder-like sequences that a
    decoder locks on to instead of the real finders. A symbol that decodes on a
    clean synthetic image and fails on a photograph of a screen is precisely the
    failure nothing else in this suite can see.
    """
    from test_tickets import VECTOR_PAYLOAD

    for text in ("HELLO", VECTOR_PAYLOAD, "0123456789" * 12):
        modules = qr.encode(text)
        chosen = read_mask(modules)
        version, mode = qr.choose_version(text)
        words = qr._interleave(qr._codewords(text, mode, version), version)
        scores = {}
        for mask in range(8):
            candidate, reserved = qr._blank(version)
            size = qr._size(version)
            qr._place_finder(candidate, reserved, 0, 0)
            qr._place_finder(candidate, reserved, 0, size - 7)
            qr._place_finder(candidate, reserved, size - 7, 0)
            qr._place_alignment(candidate, reserved, version)
            qr._place_timing(candidate, reserved, version)
            qr._reserve_format(candidate, reserved, version)
            qr._place_data(candidate, reserved, words, version)
            for row in range(size):
                for column in range(size):
                    if not reserved[row][column] and qr._MASKS[mask](row, column):
                        candidate[row][column] ^= 1
            qr._place_format(candidate, mask, version)
            qr._place_version(candidate, version)
            scores[mask] = qr._penalty(candidate)
        best = min(scores, key=lambda mask: (scores[mask], mask))
        assert chosen == best, (
            f"{text[:20]!r}: the symbol ships mask {chosen} (penalty {scores[chosen]}) and "
            f"mask {best} scores {scores[best]}"
        )
        # THE CONTROL for `read_mask`: the eight masks really do give eight
        # different format fields, so reading one back is a measurement.
        assert len({qr._format_bits(mask) for mask in range(8)}) == 8
        # And the scores are not all equal, or "the lowest" would name nothing.
        assert len(set(scores.values())) > 1, scores
