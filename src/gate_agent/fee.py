"""The fee frame: what a barrier display at an EXIT shows the driver, from the lane's own decision.

**A NEW LAYOUT, NOT A NEW LINE.** The ticket frame (`display.frame_for`) is laid
out around a QR symbol, and a fee has no symbol. So this is its own frame: the
figure is what the driver needs and it is the largest thing on the screen, or,
when there is no figure, the sentence that says why is. It draws with the same
font, the same whole-module scaling and the same monochrome rule as the ticket
frame -- `display.to_bytes` writes it -- because the paragraph in `display.py`
about white being every bit set stays true only while nothing here draws a
colour.

**IT READS A FIGURE; IT NEVER COMPUTES ONE.** The input is the exit decision
the lane made before the barrier moved -- its record, `ExitPricing.to_detail()`
in `lane-controller`, the same record the lane puts on the close as
`local_decision` and the platform writes onto the row (0017). `screen_for`
reads its `status`, its `fee_minor` and its `currency` by name, and the only
thing done to the fee is to write it out in the currency's own minor units:
`1000` in `USD` is `10.00 USD`, by integer division, and a currency this module
does not know the minor units of is not guessed at -- the figure is NOT SHOWN,
and the frame says so. A figure drawn with the decimal point in the wrong place
is a wrong fee on a screen.

**THE DISPLAY CARRIES WHAT THE READER CANNOT: every state with no payment.**
Covered by a pass or an agreement; priced at zero; refused by the engine; a
stay the lane could not price at the barrier; a record this module cannot read.
The reader shows nothing in any of them, and a blank display would leave the
driver guessing -- so each is a SENTENCE, in every declared driver language:

  fee_due          the figure, under a label -- and no instruction to pay:
                   nothing collects in this version, and a screen that told a
                   driver to pay where nothing takes payment would be false
  nothing_to_pay   a priced stay of zero
  covered          a module's register covered the car
  not_shown        everything else: the engine refused, the lane had no entry
                   to price from, its facts were stale, the record was not one
                   this can read, or the currency's minor units are unknown

**A LINE THAT DOES NOT FIT IS LEFT OUT, never shrunk below one pixel per module
and never clipped** -- the ticket frame's rule. Two things are never left out:
the FIGURE (a fee frame with no figure on it says `not_shown` instead) and
EVERYTHING (a frame with no line on it at all is refused as
`DisplayUnavailable`, because an empty screen is not a state).

**NOT WIRED IN THIS VERSION.** Nothing in the agent yet reads a lane's exit
decision or draws this frame on a screen: the lane does not publish the record
on its read contract, and the agent owns its displays for tickets. This module
is the layout, proven by rendering and reading back; the feed is its own change.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from . import font
from .display import DisplayUnavailable, Geometry, _blank_bitmap, _draw
from .lines import DISPLAY_TEXT


class FeeState(StrEnum):
    """What the fee frame can say. CLOSED: a state added here is a sentence added to
    `STATE_LINE`, in every shipped language, or the import below refuses."""

    FEE_DUE = "fee_due"
    NOTHING_TO_PAY = "nothing_to_pay"
    COVERED = "covered"
    NOT_SHOWN = "not_shown"


#: The display line each state draws. For `fee_due` it is the label over the
#: figure; for the others it is the whole of what the driver reads.
STATE_LINE: dict[FeeState, str] = {
    FeeState.FEE_DUE: "display.fee.label",
    FeeState.NOTHING_TO_PAY: "display.fee.nothing_to_pay",
    FeeState.COVERED: "display.fee.covered",
    FeeState.NOT_SHOWN: "display.fee.not_shown",
}
if set(STATE_LINE) != set(FeeState):  # pragma: no cover - an import-time guard
    raise RuntimeError("every fee state needs a display line")

#: ISO 4217 minor units, for the currencies this module will write a figure in.
#: A LIST, and short on purpose: a currency not here is NOT SHOWN rather than
#: written with two decimals by default, because the default is exactly the
#: guess that puts a zero-decimal fee on a screen a hundred times too small.
MINOR_UNITS: dict[str, int] = {
    "USD": 2,
    "CAD": 2,
    "MXN": 2,
    "EUR": 2,
    "GBP": 2,
    "CHF": 2,
    "AUD": 2,
    "NZD": 2,
    "JPY": 0,
    "KRW": 0,
}

#: The lane record's own words for a stay it priced and a car a module covered
#: (`lane_controller.exit_pricing`). Spelled here, read by name: this package
#: imports nothing from the lane.
PRICED = "priced"
COVERED = "covered"

#: How much of the screen's HEIGHT the figure may take. The rest is the label
#: and the margin; on a wide screen the width decides first anyway.
FIGURE_SHARE = 0.45
#: How much of the height one sentence may take, on a frame with no figure.
SENTENCE_SHARE = 0.3


@dataclass(frozen=True, slots=True)
class FeeScreen:
    """What one exit decision puts on the display.

    `figure` is the fee as it is DRAWN -- `"10.00 USD"` -- and is present only
    on `fee_due`. `status` is the record's own status, kept for the log and
    never drawn.
    """

    state: FeeState
    figure: str | None
    status: str | None


def figure_for(fee_minor: int, currency: str) -> str | None:
    """The fee in the currency's minor units, or None when they are not known here."""
    exponent = MINOR_UNITS.get(currency)
    if exponent is None:
        return None
    if exponent == 0:
        return f"{fee_minor} {currency}"
    whole, part = divmod(fee_minor, 10**exponent)
    return f"{whole}.{part:0{exponent}d} {currency}"


def screen_for(record: object) -> FeeScreen:
    """The screen for one exit decision's record, read by name. Never raises."""
    if not isinstance(record, dict):
        return FeeScreen(FeeState.NOT_SHOWN, None, None)
    status = record.get("status")
    status = status if isinstance(status, str) else None
    if status == COVERED:
        return FeeScreen(FeeState.COVERED, None, status)
    if status != PRICED:
        return FeeScreen(FeeState.NOT_SHOWN, None, status)
    fee, currency = record.get("fee_minor"), record.get("currency")
    if isinstance(fee, bool) or not isinstance(fee, int) or fee < 0:
        return FeeScreen(FeeState.NOT_SHOWN, None, status)
    if not isinstance(currency, str):
        return FeeScreen(FeeState.NOT_SHOWN, None, status)
    if fee == 0:
        return FeeScreen(FeeState.NOTHING_TO_PAY, None, status)
    figure = figure_for(fee, currency)
    if figure is None:
        return FeeScreen(FeeState.NOT_SHOWN, None, status)
    return FeeScreen(FeeState.FEE_DUE, figure, status)


def lines_for(state: FeeState, languages: tuple[str, ...]) -> tuple[str, ...]:
    """The state's sentence in every declared driver language, in declared order."""
    words = DISPLAY_TEXT[STATE_LINE[state]]
    return tuple(words[language] for language in languages if words.get(language))


def _largest_scale(text: str, width: int, height: int) -> int:
    """The largest whole scale at which `text` fits `width` by `height`, or 0."""
    across = font.width_of(text)
    if across <= 0:
        return 0
    return max(0, min(width // across, height // font.CELL_HEIGHT))


def _stack(bitmap, lines: list[tuple[str, int]], geometry: Geometry) -> None:
    """Lines at their scales, each centred across, the block centred down."""
    gaps = [scale for _, scale in lines[1:]]
    total = sum(font.CELL_HEIGHT * scale for _, scale in lines) + sum(gaps)
    cursor = max(0, (geometry.height - total) // 2)
    for text, scale in lines:
        left = max(0, (geometry.width - font.width_of(text) * scale) // 2)
        _draw(bitmap, font.render(text), cursor, left, scale)
        cursor += font.CELL_HEIGHT * scale + scale


def fee_frame_for(
    screen: FeeScreen, languages: tuple[str, ...], geometry: Geometry
) -> list[list[int]]:
    """The whole frame as rows of 0/1, `1` meaning DARK -- the ticket frame's convention.

    `fee_due`: the label in each language, then the figure, as large as the
    width and `FIGURE_SHARE` of the height allow. A figure that does not fit at
    one pixel per module makes this a `not_shown` frame instead. Every other
    state: its sentence in each language, each as large as fits. A line that
    does not fit is left out; a frame left with no line is `DisplayUnavailable`.
    """
    margin = max(2, min(geometry.width, geometry.height) // 40)
    inner_w, inner_h = geometry.width - 2 * margin, geometry.height - 2 * margin
    bitmap = _blank_bitmap(geometry.width, geometry.height)

    if screen.state is FeeState.FEE_DUE:
        figure_scale = _largest_scale(screen.figure or "", inner_w, int(inner_h * FIGURE_SHARE))
        if figure_scale < 1:
            return fee_frame_for(FeeScreen(FeeState.NOT_SHOWN, None, screen.status),
                                 languages, geometry)
        room = inner_h - font.CELL_HEIGHT * figure_scale
        labels: list[tuple[str, int]] = []
        for label in lines_for(FeeState.FEE_DUE, languages):
            scale = min(max(1, figure_scale // 3),
                        _largest_scale(label, inner_w, inner_h))
            needed = font.CELL_HEIGHT * scale + scale
            if scale < 1 or needed > room:
                # LEFT OUT rather than clipped or shrunk to nothing: the figure
                # is what the driver needs, and it is drawn whatever happens.
                continue
            labels.append((label, scale))
            room -= needed
        _stack(bitmap, [*labels, (screen.figure, figure_scale)], geometry)
        return bitmap

    drawn: list[tuple[str, int]] = []
    room = inner_h
    for sentence in lines_for(screen.state, languages):
        scale = _largest_scale(sentence, inner_w, int(inner_h * SENTENCE_SHARE))
        needed = font.CELL_HEIGHT * scale + scale
        if scale < 1 or needed > room:
            continue
        drawn.append((sentence, scale))
        room -= needed
    if not drawn:
        raise DisplayUnavailable(
            f"a {geometry.width}x{geometry.height} screen has room for no line of the "
            f"{screen.state.value} frame, and an empty frame tells a driver nothing"
        )
    _stack(bitmap, drawn, geometry)
    return bitmap


__all__ = [
    "FIGURE_SHARE",
    "MINOR_UNITS",
    "SENTENCE_SHARE",
    "STATE_LINE",
    "FeeScreen",
    "FeeState",
    "fee_frame_for",
    "figure_for",
    "lines_for",
    "screen_for",
]
