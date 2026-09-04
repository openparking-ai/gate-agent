"""A QR encoder, in this repository, with no runtime dependency.

**Why our own.** `dependencies = []` is a property of this package and not a
convenience (SETTLED 1): this runs beside a lane, on a box in a gate housing,
and every dependency is one more thing to cross-compile, patch and have go wrong
somewhere with no keyboard attached. A QR encoder is about four hundred lines of
arithmetic that has not changed since 2006, and the alternative was a runtime
dependency on the one process that has to still be working when the rest is not.

**Why you should not believe it because it is here.** An encoder nobody can read
back is a picture of a QR code. So it is proven by an INDEPENDENT DECODER --
OpenCV's `QRCodeDetector`, a TEST-only dependency with a licence row -- across
every version and every payload length this package can reach, and against the
published test vector. A frame the independent decoder cannot read is a RED
TEST, not a warning.

**What it does and does not do.** ISO/IEC 18004 symbols, versions 1 to **22**, error
correction level **M**, in ALPHANUMERIC or BYTE mode.

**Twenty-two and not forty, and the reason is the rule this project runs on.**
The block table for every version was typed out from the standard, and a check
that derives the number of data modules from the MATRIX -- by building one and
counting the positions no function pattern occupies -- found SEVENTEEN of those
rows inconsistent with the symbol they describe: versions 23, 24, 25 and 27
through 40. A typed number survives review by looking measured, and a table of
forty rows repaired from memory is that failure at scale. So the rows that could
not be supported were DELETED rather than repaired, and this encoder refuses
above version 22 with the payload's length in the message.

**What that costs, stated:** version 22 at level M holds 1,782 alphanumeric
characters. A ticket payload is 135 for the shipped example, and reaching the
bound needs a `site_id` and a lane name of well over a thousand characters
between them. Nothing this package can produce approaches it, and a
configuration that did is refused rather than truncated. No Kanji mode, no ECI, no
structured append, no micro-QR: none of them is reachable from a ticket payload,
and an unreachable branch is one nothing measures. Mode is chosen from the
content -- alphanumeric where every character is in that mode's 45, byte
otherwise -- and the version is the smallest that holds it.

Level M is 15% recovery. It is the level the ticket payload is specified at
because the surface is a screen behind glass in weather, photographed at an
angle by a phone in one hand: L would be smaller and Q would be bigger, and
**which of them a real lane wants is NOT MEASURED** -- no display exists, no
photograph has been taken. M is stated as the choice it is.
"""

from __future__ import annotations

#: The 45 characters QR's ALPHANUMERIC mode encodes, in the order that IS their
#: value: the index of a character in this string is the number it encodes as.
#: From ISO/IEC 18004 table 5.
ALPHANUMERIC = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ $%*+-./:"

#: The mode indicators, four bits each.
MODE_ALPHANUMERIC = 0b0010
MODE_BYTE = 0b0100

#: The largest symbol this encoder builds. See the module docstring: the block
#: tables above 22 could not be supported here and were deleted rather than
#: repaired from memory.
MAX_VERSION = 22

#: Error correction level M's own two-bit value, as it goes into the format
#: information. **Not the same order as the levels' names**: the standard's
#: encoding is L=01, M=00, Q=11, H=10, and writing 0b01 here because M is the
#: second level is the mistake this comment exists to stop.
EC_M = 0b00

#: How many error-correction codewords per block, and how the data codewords are
#: split into blocks, for LEVEL M at each version. `(ec_per_block, ((count,
#: data_per_block), ...))`, from ISO/IEC 18004 table 9.
#:
#: **This table is not believed because it is typed.** `tests/test_qr.py`
#: derives the number of data modules from the MATRIX -- by building one and
#: counting the positions no function pattern occupies -- and requires the
#: codewords this table describes to fill it exactly, for every version. A wrong
#: row cannot survive that, and the independent decoder is the second check
#: behind it.
EC_M_BLOCKS: dict[int, tuple[int, tuple[tuple[int, int], ...]]] = {
    1: (10, ((1, 16),)),
    2: (16, ((1, 28),)),
    3: (26, ((1, 44),)),
    4: (18, ((2, 32),)),
    5: (24, ((2, 43),)),
    6: (16, ((4, 27),)),
    7: (18, ((4, 31),)),
    8: (22, ((2, 38), (2, 39))),
    9: (22, ((3, 36), (2, 37))),
    10: (26, ((4, 43), (1, 44))),
    11: (30, ((1, 50), (4, 51))),
    12: (22, ((6, 36), (2, 37))),
    13: (22, ((8, 37), (1, 38))),
    14: (24, ((4, 40), (5, 41))),
    15: (24, ((5, 41), (5, 42))),
    16: (28, ((7, 45), (3, 46))),
    17: (28, ((10, 46), (1, 47))),
    18: (26, ((9, 43), (4, 44))),
    19: (26, ((3, 44), (11, 45))),
    20: (26, ((3, 41), (13, 42))),
    21: (26, ((17, 42),)),
    22: (28, ((17, 46),)),
}

#: Where the alignment patterns' CENTRES go, per version, from ISO/IEC 18004
#: annex E. Version 1 has none. The pattern sits at every pair of these
#: coordinates except the three that would land on a finder.
ALIGNMENT_CENTRES: dict[int, tuple[int, ...]] = {
    1: (),
    2: (6, 18), 3: (6, 22), 4: (6, 26), 5: (6, 30), 6: (6, 34),
    7: (6, 22, 38), 8: (6, 24, 42), 9: (6, 26, 46), 10: (6, 28, 50),
    11: (6, 30, 54), 12: (6, 32, 58), 13: (6, 34, 62),
    14: (6, 26, 46, 66), 15: (6, 26, 48, 70), 16: (6, 26, 50, 74),
    17: (6, 30, 54, 78), 18: (6, 30, 56, 82), 19: (6, 30, 58, 86),
    20: (6, 34, 62, 90),
    21: (6, 28, 50, 72, 94), 22: (6, 26, 50, 74, 98),
}

#: The version information bit strings for versions 7 and up, from ISO/IEC 18004
#: annex D: 6 data bits and 12 of BCH(18,6). Typed here rather than computed,
#: and then CHECKED against the generator polynomial by a test -- which is the
#: only reason a typed table is allowed to stay in this repository.
VERSION_INFO: dict[int, int] = {
    7: 0x07C94, 8: 0x085BC, 9: 0x09A99, 10: 0x0A4D3, 11: 0x0BBF6, 12: 0x0C762,
    13: 0x0D847, 14: 0x0E60D, 15: 0x0F928, 16: 0x10B78, 17: 0x1145D, 18: 0x12A17,
    19: 0x13532, 20: 0x149A6, 21: 0x15683, 22: 0x168C9,
}


class QrTooLong(ValueError):
    """The payload does not fit the largest symbol this encoder builds.

    Its own exception, because it is the one failure here a CONFIGURATION can
    cause: a site whose name and lane name are long enough push a ticket past
    what a symbol holds. It is refused with the length in the message rather
    than truncated -- a truncated ticket is one the exit reads as a forgery.
    """


# ---------------------------------------------------------------------------
# GF(256), and the Reed-Solomon codewords
# ---------------------------------------------------------------------------

#: The field QR's error correction lives in: GF(256) with the primitive
#: polynomial x^8 + x^4 + x^3 + x^2 + 1 (0x11d). Built at import rather than
#: typed, because a 512-entry table of logarithms is exactly the kind of thing
#: nobody proof-reads.
_EXP = [0] * 512
_LOG = [0] * 256


def _build_tables() -> None:
    value = 1
    for power in range(255):
        _EXP[power] = value
        _LOG[value] = power
        value <<= 1
        if value & 0x100:
            value ^= 0x11D
    for power in range(255, 512):
        _EXP[power] = _EXP[power - 255]


_build_tables()


def _multiply(left: int, right: int) -> int:
    if left == 0 or right == 0:
        return 0
    return _EXP[_LOG[left] + _LOG[right]]


def _generator(degree: int) -> list[int]:
    """The RS generator polynomial of this degree, built up from its roots."""
    poly = [1]
    for power in range(degree):
        poly = _multiply_polynomials(poly, [1, _EXP[power]])
    return poly


def _multiply_polynomials(left: list[int], right: list[int]) -> list[int]:
    out = [0] * (len(left) + len(right) - 1)
    for i, a in enumerate(left):
        for j, b in enumerate(right):
            out[i + j] ^= _multiply(a, b)
    return out


def error_correction(data: list[int], count: int) -> list[int]:
    """`count` error-correction codewords for one block of data codewords."""
    generator = _generator(count)
    remainder = list(data) + [0] * count
    for index in range(len(data)):
        factor = remainder[index]
        if factor == 0:
            continue
        for offset, coefficient in enumerate(generator):
            remainder[index + offset] ^= _multiply(coefficient, factor)
    return remainder[len(data):]


# ---------------------------------------------------------------------------
# The bit stream
# ---------------------------------------------------------------------------


class _Bits:
    def __init__(self) -> None:
        self.bits: list[int] = []

    def push(self, value: int, width: int) -> None:
        for shift in range(width - 1, -1, -1):
            self.bits.append((value >> shift) & 1)

    def __len__(self) -> int:
        return len(self.bits)


def _is_alphanumeric(text: str) -> bool:
    return all(character in ALPHANUMERIC for character in text)


def _count_bits(mode: int, version: int) -> int:
    """How wide the character-count indicator is. It depends on the VERSION, and
    getting it wrong produces a symbol that scans as gibberish rather than one
    that fails to scan."""
    if mode == MODE_ALPHANUMERIC:
        return 9 if version <= 9 else (11 if version <= 26 else 13)
    return 8 if version <= 9 else 16


def _data_codeword_count(version: int) -> int:
    ec_per_block, groups = EC_M_BLOCKS[version]
    return sum(count * data for count, data in groups)


def _encode_payload(text: str, mode: int, version: int) -> _Bits:
    bits = _Bits()
    bits.push(mode, 4)
    if mode == MODE_ALPHANUMERIC:
        bits.push(len(text), _count_bits(mode, version))
        # PAIRS, base 45, eleven bits each -- which is what makes this mode
        # smaller than byte mode for the same characters.
        for index in range(0, len(text) - 1, 2):
            pair = ALPHANUMERIC.index(text[index]) * 45 + ALPHANUMERIC.index(text[index + 1])
            bits.push(pair, 11)
        if len(text) % 2:
            bits.push(ALPHANUMERIC.index(text[-1]), 6)
    else:
        raw = text.encode("utf-8")
        bits.push(len(raw), _count_bits(mode, version))
        for byte in raw:
            bits.push(byte, 8)
    return bits


def _codewords(text: str, mode: int, version: int) -> list[int]:
    total = _data_codeword_count(version)
    bits = _encode_payload(text, mode, version)
    capacity = total * 8
    if len(bits) > capacity:
        raise QrTooLong(f"{len(bits)} bits do not fit version {version}'s {capacity}")
    # The terminator: up to four zero bits, and fewer when there is no room.
    bits.push(0, min(4, capacity - len(bits)))
    # Then to a byte boundary.
    while len(bits) % 8:
        bits.bits.append(0)
    words = [
        int("".join(str(bit) for bit in bits.bits[index : index + 8]), 2)
        for index in range(0, len(bits), 8)
    ]
    # And the two pad codewords, alternating, until the block is full.
    pads = (0xEC, 0x11)
    while len(words) < total:
        words.append(pads[(len(words) - len(bits) // 8) % 2])
    return words


def _interleave(words: list[int], version: int) -> list[int]:
    """Data and error correction, block by block, taken a column at a time.

    Interleaving is what makes a burst of damage -- a thumb, a scratch, a
    reflection -- fall across several blocks instead of destroying one.
    """
    ec_per_block, groups = EC_M_BLOCKS[version]
    blocks: list[list[int]] = []
    offset = 0
    for count, data_per_block in groups:
        for _ in range(count):
            blocks.append(words[offset : offset + data_per_block])
            offset += data_per_block
    ec_blocks = [error_correction(block, ec_per_block) for block in blocks]

    out: list[int] = []
    for index in range(max(len(block) for block in blocks)):
        for block in blocks:
            if index < len(block):
                out.append(block[index])
    for index in range(ec_per_block):
        for block in ec_blocks:
            out.append(block[index])
    return out


# ---------------------------------------------------------------------------
# The matrix
# ---------------------------------------------------------------------------


def _size(version: int) -> int:
    return version * 4 + 17


def _blank(version: int):
    """`(modules, reserved)`. `reserved` is every position a function pattern
    or a format field owns, which is what the data placement walks around."""
    size = _size(version)
    modules = [[0] * size for _ in range(size)]
    reserved = [[False] * size for _ in range(size)]
    return modules, reserved


def _place_finder(modules, reserved, top: int, left: int) -> None:
    for row in range(-1, 8):
        for column in range(-1, 8):
            y, x = top + row, left + column
            if not (0 <= y < len(modules) and 0 <= x < len(modules)):
                continue
            inside = 0 <= row < 7 and 0 <= column < 7
            dark = inside and (
                row in (0, 6) or column in (0, 6) or (2 <= row <= 4 and 2 <= column <= 4)
            )
            modules[y][x] = 1 if dark else 0
            reserved[y][x] = True


def _place_alignment(modules, reserved, version: int) -> None:
    centres = ALIGNMENT_CENTRES[version]
    size = _size(version)
    for row in centres:
        for column in centres:
            # The three that would sit on a finder are skipped, and this is the
            # rule rather than a list: a pattern whose centre is inside a
            # finder's 8x8 keep-out.
            if (row, column) in ((6, 6), (6, centres[-1]), (centres[-1], 6)):
                continue
            for dy in range(-2, 3):
                for dx in range(-2, 3):
                    y, x = row + dy, column + dx
                    if not (0 <= y < size and 0 <= x < size):
                        continue
                    dark = max(abs(dy), abs(dx)) != 1
                    modules[y][x] = 1 if dark else 0
                    reserved[y][x] = True


def _place_timing(modules, reserved, version: int) -> None:
    size = _size(version)
    for index in range(8, size - 8):
        bit = 1 if index % 2 == 0 else 0
        for y, x in ((6, index), (index, 6)):
            modules[y][x] = bit
            reserved[y][x] = True


def _reserve_format(modules, reserved, version: int) -> None:
    size = _size(version)
    for index in range(9):
        for y, x in ((8, index), (index, 8)):
            if y < size and x < size:
                reserved[y][x] = True
    for index in range(8):
        reserved[8][size - 1 - index] = True
        reserved[size - 1 - index][8] = True
    # THE DARK MODULE. Always dark, always here, and it is not part of the
    # format information however much it looks like it.
    modules[size - 8][8] = 1
    reserved[size - 8][8] = True
    if version >= 7:
        for index in range(18):
            row, column = index // 3, index % 3
            reserved[size - 11 + column][row] = True
            reserved[row][size - 11 + column] = True


def _place_data(modules, reserved, words: list[int], version: int) -> None:
    """Two columns at a time, upward then downward, skipping the timing column."""
    size = _size(version)
    bits = [(word >> shift) & 1 for word in words for shift in range(7, -1, -1)]
    index = 0
    column = size - 1
    upward = True
    while column > 0:
        if column == 6:
            # The vertical timing pattern is a whole column and the walk steps
            # over it rather than round each of its modules.
            column -= 1
        rows = range(size - 1, -1, -1) if upward else range(size)
        for row in rows:
            for offset in (0, 1):
                x = column - offset
                if reserved[row][x]:
                    continue
                modules[row][x] = bits[index] if index < len(bits) else 0
                index += 1
        upward = not upward
        column -= 2


_MASKS = (
    lambda r, c: (r + c) % 2 == 0,
    lambda r, c: r % 2 == 0,
    lambda r, c: c % 3 == 0,
    lambda r, c: (r + c) % 3 == 0,
    lambda r, c: (r // 2 + c // 3) % 2 == 0,
    lambda r, c: (r * c) % 2 + (r * c) % 3 == 0,
    lambda r, c: ((r * c) % 2 + (r * c) % 3) % 2 == 0,
    lambda r, c: ((r + c) % 2 + (r * c) % 3) % 2 == 0,
)


def _format_bits(mask: int) -> int:
    """The 15-bit format information: 5 data bits, BCH(15,5), then the mask.

    The final XOR with `0x5412` is what stops an all-zero format field -- level
    M with mask 0 -- from being a blank strip that a decoder cannot lock on to.
    """
    value = (EC_M << 3) | mask
    # Straight polynomial division, written plainly rather than cleverly.
    remainder = value << 10
    generator = 0b10100110111
    for shift in range(14, 9, -1):
        if remainder & (1 << shift):
            remainder ^= generator << (shift - 10)
    return ((value << 10) | remainder) ^ 0b101010000010010


def _place_format(modules, mask: int, version: int) -> None:
    size = _size(version)
    bits = _format_bits(mask)
    for index in range(15):
        bit = (bits >> index) & 1
        # The copy beside the top-left finder.
        if index < 6:
            modules[index][8] = bit
        elif index == 6:
            modules[7][8] = bit
        elif index == 7:
            modules[8][8] = bit
        elif index == 8:
            modules[8][7] = bit
        else:
            modules[8][14 - index] = bit
        # And the second copy, split between the other two corners.
        if index < 8:
            modules[8][size - 1 - index] = bit
        else:
            modules[size - 15 + index][8] = bit


def _place_version(modules, version: int) -> None:
    if version < 7:
        return
    size = _size(version)
    bits = VERSION_INFO[version]
    for index in range(18):
        bit = (bits >> index) & 1
        row, column = index // 3, index % 3
        modules[size - 11 + column][row] = bit
        modules[row][size - 11 + column] = bit


def _penalty(modules) -> int:
    """The standard's four penalty rules. Lower is better; the best mask wins.

    Masking is not cosmetic: an unmasked symbol can contain long runs and
    finder-like sequences that a decoder locks on to instead of the real
    finders, and the penalty score is how the standard says to avoid them.
    """
    size = len(modules)
    score = 0

    # Rule 1: runs of five or more of one colour, in each direction.
    for line in list(modules) + [list(column) for column in zip(*modules, strict=True)]:
        run, previous = 1, line[0]
        for value in line[1:]:
            if value == previous:
                run += 1
            else:
                if run >= 5:
                    score += 3 + (run - 5)
                run, previous = 1, value
        if run >= 5:
            score += 3 + (run - 5)

    # Rule 2: every 2x2 block of one colour.
    for row in range(size - 1):
        for column in range(size - 1):
            block = (
                modules[row][column], modules[row][column + 1],
                modules[row + 1][column], modules[row + 1][column + 1],
            )
            if len(set(block)) == 1:
                score += 3

    # Rule 3: the finder-like 1:1:3:1:1 sequence with four light modules beside
    # it, in either direction.
    pattern_a = [1, 0, 1, 1, 1, 0, 1, 0, 0, 0, 0]
    pattern_b = [0, 0, 0, 0, 1, 0, 1, 1, 1, 0, 1]
    for line in list(modules) + [list(column) for column in zip(*modules, strict=True)]:
        for index in range(size - 10):
            window = list(line[index : index + 11])
            if window in (pattern_a, pattern_b):
                score += 40

    # Rule 4: how far the proportion of dark modules is from a half. The
    # standard's own arithmetic -- `k = floor(|percent - 50| / 5)`, penalty
    # `k * 10` -- written plainly. What stood here was a `min` of two
    # expressions that was not this rule and was not any rule; it scored every
    # symbol slightly wrongly, which is a mask chosen slightly wrongly. Found
    # while proving the encoder against an independent decoder, and it is a
    # defect on the day it was written whatever symbol it happened to produce.
    dark = sum(sum(row) for row in modules)
    percent = dark * 100 / (size * size)
    score += int(abs(percent - 50) // 5) * 10
    return score


def choose_version(text: str, mode: int | None = None) -> tuple[int, int]:
    """The smallest version that holds this payload, and the mode it holds it in.

    Alphanumeric where every character is in that mode's 45 -- which every
    ticket payload is, by construction -- and byte otherwise. Returned together
    because the character-count indicator's width depends on the version and the
    version depends on how many bits the mode needs: they are one decision.
    """
    picked = mode if mode is not None else (
        MODE_ALPHANUMERIC if _is_alphanumeric(text) else MODE_BYTE
    )
    for version in range(1, MAX_VERSION + 1):
        bits = 4 + _count_bits(picked, version)
        if picked == MODE_ALPHANUMERIC:
            bits += 11 * (len(text) // 2) + 6 * (len(text) % 2)
        else:
            bits += 8 * len(text.encode("utf-8"))
        if bits <= _data_codeword_count(version) * 8:
            return version, picked
    raise QrTooLong(
        f"a payload of {len(text)} characters does not fit a version-{MAX_VERSION} symbol at "
        "level M. "
        "A ticket this long comes from a site_id and a lane name long enough to push it "
        "there; it is refused rather than truncated, because a truncated ticket is one the "
        "exit reads as a forgery."
    )


def encode(text: str, mode: int | None = None) -> list[list[int]]:
    """One QR symbol as rows of 0/1, no quiet zone. The whole public surface."""
    version, picked = choose_version(text, mode)
    words = _interleave(_codewords(text, picked, version), version)

    best = None
    for mask in range(8):
        modules, reserved = _blank(version)
        size = _size(version)
        _place_finder(modules, reserved, 0, 0)
        _place_finder(modules, reserved, 0, size - 7)
        _place_finder(modules, reserved, size - 7, 0)
        _place_alignment(modules, reserved, version)
        _place_timing(modules, reserved, version)
        _reserve_format(modules, reserved, version)
        _place_data(modules, reserved, words, version)
        for row in range(size):
            for column in range(size):
                if not reserved[row][column] and _MASKS[mask](row, column):
                    modules[row][column] ^= 1
        _place_format(modules, mask, version)
        _place_version(modules, version)
        score = _penalty(modules)
        if best is None or score < best[0]:
            best = (score, modules)
    return best[1]


__all__ = [
    "ALIGNMENT_CENTRES",
    "ALPHANUMERIC",
    "EC_M",
    "EC_M_BLOCKS",
    "MODE_ALPHANUMERIC",
    "MODE_BYTE",
    "VERSION_INFO",
    "QrTooLong",
    "choose_version",
    "encode",
    "error_correction",
]
