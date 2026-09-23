"""The fee frame, read back out of the bytes that were written.

**THE MEASUREMENT IS THE BYTES, as in `test_display.py`.** Every frame here is
written to a framebuffer file through `Display.show`, read back from the file
using the geometry alone, and then READ: `read_lines` finds each band of ink,
works out the whole scale and the centred grid of cells it was drawn at, and
matches every cell against every glyph in the font. It is not told what text
to expect, and it accepts a band only when re-drawing what it read reproduces
the band pixel for pixel -- so a figure drawn with a digit wrong, a line
clipped at an edge or a scale that is not whole comes back as something else
or as nothing, and the test goes red.

**ITS OWN CONTROL comes first**: a planted figure must read back as the planted
figure, and a frame with one pixel flipped inside a glyph must not read back as
the text it was drawn from. A reader that could not fail would prove nothing.

`scripts/agent_fail_control.py` breaks the frame and the record's reading and
requires this file to go red.
"""

from __future__ import annotations

import pytest

from gate_agent import display, fee, font
from gate_agent.display import DisplayUnavailable, Geometry
from gate_agent.fee import FeeScreen, FeeState, fee_frame_for, screen_for
from gate_agent.lines import DISPLAY_TEXT

np = pytest.importorskip("numpy")

from test_display import a_screen, picture_from  # noqa: E402

LANGUAGES = ("en", "es-ES")

#: Real screens and the arithmetic paths through them: 32, 24 and 16 bits per
#: pixel, a PORTRAIT screen, a padded stride and a small one.
GEOMETRIES = [
    (800, 480, 32, None),
    (800, 480, 16, None),
    (480, 800, 32, None),
    (1024, 600, 32, 4352),
    (320, 240, 24, None),
]

#: The lane's record for a stay it priced at ten dollars -- the shape
#: `ExitPricing.to_detail()` writes and the close carries as `local_decision`.
PRICED = {
    "status": "priced",
    "covered_by": [],
    "matched": [],
    "computed_from": {"synced_at": "2026-06-10T17:59:00+00:00"},
    "entry_at": "2026-06-10T14:30:00+00:00",
    "exit_at": "2026-06-10T18:00:00+00:00",
    "session_id": "s-9",
    "space_class": "standard",
    "fee_minor": 1000,
    "currency": "USD",
    "plan_version": "flat-2026-01",
    "breakdown": [
        {"code": "stay", "rule_id": None, "text": "ENTERED 14:30", "delta_minor": 0},
        {"code": "increment.first_period", "rule_id": "hourly", "text": "FIRST HOUR",
         "delta_minor": 250},
        {"code": "increment.repeat_periods", "rule_id": "hourly", "text": "3 MORE HOURS",
         "delta_minor": 750},
    ],
}
COVERED = {"status": "covered", "covered_by": ["garage_pass"], "matched": [{"pass": "p-1"}],
           "computed_from": {}}


# ---------------------------------------------------------------------------
# the reader the frames are measured with
# ---------------------------------------------------------------------------

_CELLS = {
    character: np.array(
        [[1 if pixel == "#" else 0 for pixel in row] for row in font.cell(character)],
        dtype=np.uint8,
    )
    for character in sorted(font.DRAWABLE)
}


def _read_cell(block: np.ndarray, scale: int) -> str | None:
    """One cell of `CELL_HEIGHT*scale` by `GLYPH_WIDTH*scale`, back to its glyph."""
    rows, columns = font.CELL_HEIGHT, font.GLYPH_WIDTH
    shaped = block.reshape(rows, scale, columns, scale)
    corner = shaped[:, :1, :, :1]
    if not (shaped == corner).all():
        return None  # not whole modules at this scale
    pattern = corner[:, 0, :, 0]
    hits = [c for c, cell in _CELLS.items() if np.array_equal(cell, pattern)]
    return hits[0] if len(hits) == 1 else None


def _read_band(ink: np.ndarray, top: int, bottom: int) -> tuple[str, int] | None:
    """The text in one band of ink rows, and its scale -- or None if it reads as nothing."""
    height, width = ink.shape
    band_columns = np.flatnonzero(ink[top : bottom + 1].any(axis=0))
    first, last = int(band_columns[0]), int(band_columns[-1])
    found = []
    for scale in range(1, (bottom - top + 1) // font.GLYPH_HEIGHT + 1):
        cell_top = bottom + 1 - font.CELL_HEIGHT * scale
        if cell_top < 0:
            continue
        pitch = (font.GLYPH_WIDTH + font.TRACKING) * scale
        for count in range(1, width // pitch + 2):
            across = font.width_of("x" * count) * scale
            left = (width - across) // 2
            if left < 0 or first < left or last >= left + across:
                continue
            text = []
            for index in range(count):
                x = left + index * pitch
                block = ink[cell_top : cell_top + font.CELL_HEIGHT * scale,
                            x : x + font.GLYPH_WIDTH * scale]
                character = _read_cell(block, scale)
                if character is None:
                    break
                text.append(character)
            else:
                read = "".join(text)
                if read != read.strip():
                    continue
                # ACCEPTED ONLY IF RE-DRAWING IT GIVES BACK THE BAND EXACTLY
                redrawn = np.zeros_like(ink[top : bottom + 1])
                glyphs = np.kron(np.array(font.render(read), dtype=bool),
                                 np.ones((scale, scale), dtype=bool))
                offset = cell_top - top
                clip = glyphs[max(0, -offset):]
                redrawn[max(0, offset) : max(0, offset) + clip.shape[0],
                        left : left + across] = clip
                if np.array_equal(redrawn, ink[top : bottom + 1]):
                    found.append((read, scale))
    return found[0] if len(found) == 1 else None


def read_lines(image: np.ndarray) -> list[tuple[str, int]]:
    """Every line of text on the frame, top to bottom, with the scale it was drawn at.

    Raises if a band of ink reads as no text, or as more than one: a frame
    with something on it this cannot read is a red test, not a skipped line.
    """
    ink = image == 0
    rows = np.flatnonzero(ink.any(axis=1))
    if rows.size == 0:
        return []
    bands, start = [], int(rows[0])
    for previous, row in zip(rows[:-1], rows[1:], strict=True):
        if row != previous + 1:
            bands.append((start, int(previous)))
            start = int(row)
    bands.append((start, int(rows[-1])))
    lines = []
    for top, bottom in bands:
        read = _read_band(ink, top, bottom)
        assert read is not None, f"rows {top}-{bottom} hold ink that reads as no line of text"
        lines.append(read)
    return lines


def written(tmp_path, record_or_screen, width=800, height=480, depth=32, stride=None):
    """The frame for a record, WRITTEN to a device and read back as an image."""
    device, sysfs = a_screen(tmp_path, width, height, depth, stride)
    screen = display.open_display("exit", device, sysfs)
    shown = (record_or_screen if isinstance(record_or_screen, FeeScreen)
             else screen_for(record_or_screen))
    screen.show(fee_frame_for(shown, LANGUAGES, screen.geometry))
    return device, screen.geometry


def words(line: str) -> list[str]:
    return [DISPLAY_TEXT[line][language] for language in LANGUAGES]


# ---------------------------------------------------------------------------
# the reader's own control -- first
# ---------------------------------------------------------------------------


def test_the_reader_reads_a_planted_figure_and_not_a_frame_one_pixel_off(tmp_path):
    planted = FeeScreen(FeeState.FEE_DUE, "10.01 USD", "priced")
    device, geometry = written(tmp_path, planted)
    image = picture_from(device, geometry)
    assert read_lines(image)[-1][0] == "10.01 USD"
    # one module of one glyph of the figure flipped: it must not read as before
    lines_before = read_lines(image)
    ink_rows = np.flatnonzero((image == 0).any(axis=1))
    bottom = int(ink_rows[-1])
    columns = np.flatnonzero(image[bottom] == 0)
    image[bottom, int(columns[0])] = 255
    with pytest.raises(AssertionError, match="reads as no line of text"):
        assert read_lines(image) != lines_before


# ---------------------------------------------------------------------------
# the record, read by name: the figure is the stored fee, never another
# ---------------------------------------------------------------------------


def test_the_figure_is_the_records_fee_written_in_its_minor_units():
    assert screen_for(PRICED) == FeeScreen(FeeState.FEE_DUE, "10.00 USD", "priced")
    assert screen_for({**PRICED, "fee_minor": 5}).figure == "0.05 USD"
    assert screen_for({**PRICED, "fee_minor": 123456}).figure == "1234.56 USD"
    assert screen_for({**PRICED, "fee_minor": 1500, "currency": "JPY"}).figure == "1500 JPY"
    assert screen_for({**PRICED, "fee_minor": 999, "currency": "EUR"}).figure == "9.99 EUR"


def test_the_figure_is_read_from_the_fee_not_added_up_from_the_lines():
    """PLANTED: a fee that is not its lines' sum. The lane never writes one; the
    only way this screen shows 7.77 is by reading `fee_minor`."""
    planted = {**PRICED, "fee_minor": 777}
    assert sum(line["delta_minor"] for line in planted["breakdown"]) != 777
    assert screen_for(planted).figure == "7.77 USD"


@pytest.mark.parametrize(
    ("record", "state"),
    [
        ({**PRICED, "fee_minor": 0}, FeeState.NOTHING_TO_PAY),
        (COVERED, FeeState.COVERED),
        ({"status": "engine_refused", "refusal": {"findings": []}}, FeeState.NOT_SHOWN),
        ({"status": "engine_invalid"}, FeeState.NOT_SHOWN),
        ({"status": "no_cached_entry", "computed_from": {}}, FeeState.NOT_SHOWN),
        ({"status": "stale_facts", "computed_from": {}}, FeeState.NOT_SHOWN),
        ({"status": "a_status_this_build_has_never_seen"}, FeeState.NOT_SHOWN),
        (None, FeeState.NOT_SHOWN),
        ({}, FeeState.NOT_SHOWN),
        ({**PRICED, "fee_minor": "1000"}, FeeState.NOT_SHOWN),
        ({**PRICED, "fee_minor": True}, FeeState.NOT_SHOWN),
        ({**PRICED, "fee_minor": -250}, FeeState.NOT_SHOWN),
        ({**PRICED, "currency": None}, FeeState.NOT_SHOWN),
        # a currency whose minor units this module does not know is not guessed at
        ({**PRICED, "currency": "XAU"}, FeeState.NOT_SHOWN),
    ],
)
def test_every_record_with_no_figure_to_draw_is_a_stated_state(record, state):
    screen = screen_for(record)
    assert screen.state is state and screen.figure is None


# ---------------------------------------------------------------------------
# the frame, written and read back, at every geometry
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("width", "height", "depth", "stride"), GEOMETRIES)
def test_the_fee_is_the_largest_thing_on_the_screen_under_its_label(
    tmp_path, width, height, depth, stride
):
    device, geometry = written(tmp_path, PRICED, width, height, depth, stride)
    lines = read_lines(picture_from(device, geometry))
    texts = [text for text, _ in lines]
    assert texts[-1] == "10.00 USD", texts
    labels = texts[:-1]
    assert labels == [one for one in words("display.fee.label") if one in labels], (
        "a label that is not the declared one, or out of order"
    )
    if (width, height) != (320, 240):
        assert labels == words("display.fee.label"), texts
    figure_scale = lines[-1][1]
    assert all(scale < figure_scale for _, scale in lines[:-1]), lines


@pytest.mark.parametrize(("width", "height", "depth", "stride"), GEOMETRIES)
@pytest.mark.parametrize(
    ("record", "line"),
    [
        ({**PRICED, "fee_minor": 0}, "display.fee.nothing_to_pay"),
        (COVERED, "display.fee.covered"),
        ({"status": "engine_refused"}, "display.fee.not_shown"),
        ({"status": "no_cached_entry"}, "display.fee.not_shown"),
    ],
)
def test_every_state_with_no_payment_is_a_sentence_in_every_language(
    tmp_path, width, height, depth, stride, record, line
):
    device, geometry = written(tmp_path, record, width, height, depth, stride)
    texts = [text for text, _ in read_lines(picture_from(device, geometry))]
    assert texts, "an empty frame for a state with no payment"
    assert texts == [one for one in words(line) if one in texts], texts
    if (width, height) in ((800, 480), (1024, 600), (480, 800)):
        assert texts == words(line), texts
    assert "10.00 USD" not in texts


def test_a_figure_with_no_room_is_not_shown_rather_than_left_off(tmp_path):
    """A screen too narrow for the figure gets the sentence that says the fee
    is not shown here -- never a label with nothing under it."""
    figure = "1234567890123456789012345678901.00 USD"  # wider than the sentence
    assert font.width_of(figure) > font.width_of(DISPLAY_TEXT["display.fee.not_shown"]["en"])
    long = FeeScreen(FeeState.FEE_DUE, figure, "priced")
    device, geometry = written(tmp_path, long, width=200, height=120)
    texts = [text for text, _ in read_lines(picture_from(device, geometry))]
    assert figure not in texts and "PARKING FEE" not in texts
    assert set(texts) <= set(words("display.fee.not_shown")) and texts, texts


def test_a_screen_with_room_for_no_line_is_refused_rather_than_left_blank():
    tiny = Geometry(width=40, height=12, bits_per_pixel=32, stride=160)
    with pytest.raises(DisplayUnavailable, match="empty frame"):
        fee_frame_for(screen_for(COVERED), LANGUAGES, tiny)


@pytest.mark.parametrize("depth", [16, 24, 32])
def test_the_fee_frame_is_monochrome_at_every_depth(tmp_path, depth):
    device, geometry = written(tmp_path, PRICED, 800, 480, depth)
    assert set(device.read_bytes()) == {0x00, 0xFF}


def test_every_state_is_drawn_and_nothing_is_ever_an_empty_frame():
    geometry = Geometry(width=800, height=480, bits_per_pixel=32, stride=3200)
    for state in FeeState:
        screen = FeeScreen(state, "10.00 USD" if state is FeeState.FEE_DUE else None, None)
        assert any(any(row) for row in fee_frame_for(screen, LANGUAGES, geometry)), state


def test_the_fee_lines_are_upper_case_drawable_and_say_nothing_about_paying():
    for state, line in fee.STATE_LINE.items():
        for language in LANGUAGES:
            text = DISPLAY_TEXT[line][language]
            assert text == text.upper() and not font.missing(text), (line, language)
            # nothing collects in this version: no sentence may tell a driver to pay
            for verb in ("PAY AT", "PLEASE PAY", "PAGUE", "INSERT", "TAP", "CARD", "TARJETA"):
                assert verb not in text, (state, language, text)
