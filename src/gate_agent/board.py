"""The lane's own words on its screen: a closed lane's message, and the board.

**TWO FRAMES, ONE SOURCE.** Both are read off the lane's `GET /v1/lane/state`,
as the lane publishes them (`lane` and `board`, added within version 2), and
nothing here decides what they say:

  * **CLOSED.** The lane is closed by its owner, and the screen shows the
    owner's message for it -- upper case, whole, wrapped by words. It is shown
    ALONE: no price and no other item while it wants the screen. A message this
    font cannot draw, or one too long for this screen at a legible size, is
    never drawn with a letter missing or a line cut off: the frame says the lane
    is closed, in every declared driver language, instead.
  * **THE BOARD.** While the lane is open and nothing else wants the screen, the
    items the lane publishes -- the owner's messages in force now, and, where
    the owner switched it on, the price the LANE worked out for a few lengths of
    stay -- one after another, each for a whole `BOARD_ITEM_SECONDS`, turned on
    the board's own clock (`Rotation`), whatever the lane's poll interval.

**THE PRICE IS READ, NEVER WORKED OUT HERE.** The lane prices each line with
the engine and the tax it charges with, and this module writes each figure out
the way the fee frame does (`fee.figure_for`, with the digits the lane
published). A line whose figure cannot be written is left out; an item with no
line left is not shown at all.

**WHICH frame wins is `display.frame_wanted`**, and the rule is written there.

**LEGIBLE IS A NUMBER: `MESSAGE_SCALE_MIN`.** One module of a glyph is at least
that many pixels. Below it a message is not drawn smaller; a closed lane says
"closed" in the font's own sentence instead, and a board message that does not
fit leaves its turn black. The platform bounds a message at 160 characters, and
`tests/test_board.py` measures that 160 of the widest-wrapping words fit at this
scale on the stand-in screens the display tests use (320x240 and 800x480).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from . import font
from .display import DisplayUnavailable, Geometry, _blank_bitmap
from .fee import _largest_scale, _stack, figure_for
from .lines import DISPLAY_TEXT

log = logging.getLogger(__name__)

#: How long each board item holds the screen, in seconds. Long enough to read a
#: 160-character message through a windscreen while pulling up; a driver who
#: misses one sees it again on the next turn.
BOARD_ITEM_SECONDS = 8.0

#: The smallest scale a message is drawn at: one glyph module is this many
#: pixels. One pixel per module is a line nobody reads from a car.
MESSAGE_SCALE_MIN = 2

#: The lane's words for a closed lane (`lane.state`), read by name.
CLOSED = "closed"

#: The board's item kinds, as the lane publishes them. An item of any other
#: kind is not drawn: a payload this build cannot read is not guessed at.
MESSAGE = "message"
PRICES = "prices"


@dataclass(frozen=True, slots=True)
class BoardItem:
    """One item the board shows: an owner's message, or the price lines."""

    kind: str
    #: The message as the lane published it (as typed); drawn upper case.
    text: str | None = None
    #: The price lines as they are DRAWN -- `"1H 5.00 USD"` -- in the lane's order.
    lines: tuple[str, ...] = ()


def closing_of(state: object) -> str | None:
    """The owner's message when the lane says it is CLOSED, else `None`.

    A closed lane with no usable message is still closed: the answer is `""`,
    which draws the font's own sentence. An absent or unreadable `lane` is a
    lane that has said nothing about closing -- a lane older than this field --
    and is open.
    """
    if not isinstance(state, dict):
        return None
    lane = state.get("lane")
    if not isinstance(lane, dict) or lane.get("state") != CLOSED:
        return None
    message = lane.get("message")
    return message.strip() if isinstance(message, str) else ""


def length_label(minutes: int) -> str:
    """`60` is `1H`; a length that is not whole hours is `90MIN`."""
    if minutes % 60 == 0:
        return f"{minutes // 60}H"
    return f"{minutes}MIN"


def price_line(line: object) -> str | None:
    """One published price line, as drawn, or `None` when it cannot be written."""
    if not isinstance(line, dict):
        return None
    minutes, fee = line.get("minutes"), line.get("fee_minor")
    currency = line.get("currency")
    if isinstance(minutes, bool) or not isinstance(minutes, int) or minutes <= 0:
        return None
    if isinstance(fee, bool) or not isinstance(fee, int) or fee < 0:
        return None
    if not isinstance(currency, str):
        return None
    figure = figure_for(fee, currency, line.get("minor_unit_digits"))
    if figure is None:
        return None
    return f"{length_label(minutes)} {figure}"


def items_of(state: object) -> tuple[BoardItem, ...]:
    """The board items the lane published, in its order, that this can draw."""
    if not isinstance(state, dict):
        return ()
    board = state.get("board")
    raw = board.get("items") if isinstance(board, dict) else None
    if not isinstance(raw, list):
        return ()
    items: list[BoardItem] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        if item.get("kind") == MESSAGE:
            text = item.get("text")
            if isinstance(text, str) and text.strip() and not font.missing(text.strip().upper()):
                items.append(BoardItem(MESSAGE, text=text.strip()))
        elif item.get("kind") == PRICES:
            lines = item.get("lines")
            drawn = tuple(
                one for one in (price_line(line) for line in lines or ()) if one is not None
            ) if isinstance(lines, list) else ()
            if drawn:
                items.append(BoardItem(PRICES, lines=drawn))
    return tuple(items)


class BoardTooSlow(ValueError):
    """An interval the board cannot keep its turns at, refused at start by name."""


def check_pass(pass_seconds: float, name: str = "the agent's loop pass") -> None:
    """The board turns on the agent's loop, so the loop must pass more than once
    a turn -- or an item would hold the screen for two turns and the order would
    lag. The lane's `poll_seconds` is not checked: it only changes what is on
    the board, so any interval a site sets is one the board keeps its turns at.
    """
    if not pass_seconds < BOARD_ITEM_SECONDS / 2:
        raise BoardTooSlow(
            f"{name} is {pass_seconds:g} s: the board turns an item every "
            f"{BOARD_ITEM_SECONDS:g} s on that loop, so it must pass in under "
            f"{BOARD_ITEM_SECONDS / 2:g} s"
        )


class Rotation:
    """The board at one lane, turning on ITS OWN CLOCK, not on the state poll.

    Each item holds the screen for a whole `BOARD_ITEM_SECONDS`, then the next
    in the lane's order, round and round. The turn is measured from when the
    item went up, so however often the lane is read -- every 2 seconds or every
    32 -- no item is skipped and none is cut short: a turn that ends late moves
    on by ONE item, from then.

    **THE POLL ONLY CHANGES WHAT IS ON THE BOARD** (`update`). The item showing
    keeps the rest of its turn if the lane still publishes it, wherever it now
    sits in the order. One the lane stopped publishing comes down at once, and
    the item now in its place starts a whole turn.

    **A BOARD THAT LOST THE SCREEN** -- to a ticket, a fee or a closed lane's
    message, which take it at once -- is `pause`d; when it has the screen
    again, the item it was on starts a whole turn.
    """

    __slots__ = ("items", "index", "since")

    def __init__(self) -> None:
        self.items: tuple[BoardItem, ...] = ()
        self.index = 0
        #: When the item showing went up; `None` when it is not up (no items,
        #: paused, or a new one in its place that has not been drawn yet).
        self.since: float | None = None

    def update(self, items: tuple[BoardItem, ...]) -> None:
        """What the lane publishes now. The turn under way is kept if its item is."""
        if items == self.items:
            return
        showing = self.items[self.index] if self.items else None
        self.items = items
        if not items:
            self.index, self.since = 0, None
        elif showing in items:
            self.index = items.index(showing)
        else:
            self.index, self.since = min(self.index, len(items) - 1), None

    def pause(self) -> None:
        self.since = None

    def turn(self, now: float) -> tuple[BoardItem | None, bool]:
        """The item that has the screen at `now`, and whether it is a NEW one to
        draw: the board just took the screen, or a turn just ended."""
        if not self.items:
            return None, False
        if self.since is None:
            self.since = now
            return self.items[self.index], True
        if now - self.since >= BOARD_ITEM_SECONDS:
            self.index = (self.index + 1) % len(self.items)
            self.since = now
            return self.items[self.index], True
        return self.items[self.index], False


def wrap(text: str, per_line: int) -> list[str]:
    """`text` as lines of at most `per_line` characters, broken between words.

    NEVER CUT: a word longer than a whole line is carried across lines at the
    character, so every character of every word is on the screen, in order. Runs
    of spaces are one space; a line never starts or ends with one.
    """
    if per_line < 1:
        return []
    lines: list[str] = []
    current = ""
    for word in text.split():
        while len(word) > per_line:
            if current:
                lines.append(current)
                current = ""
            lines.append(word[:per_line])
            word = word[per_line:]
        if not word:
            continue
        if not current:
            current = word
        elif len(current) + 1 + len(word) <= per_line:
            current = f"{current} {word}"
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def _margin(geometry: Geometry) -> int:
    return max(2, min(geometry.width, geometry.height) // 40)


def message_layout(
    text: str, geometry: Geometry, min_scale: int = MESSAGE_SCALE_MIN
) -> tuple[list[str], int] | None:
    """The message's lines and the LARGEST scale they fit at, or `None`.

    BY WORDS FIRST: the largest scale at which every word fits on a line of its
    own. Only when no legible scale does -- a word longer than the screen is
    wide -- is a word carried across lines (`wrap`), still whole. `None` when a
    character cannot be drawn or the message does not fit at `min_scale`.
    Upper-cased here, because the font draws upper case only.
    """
    upper = text.upper()
    if not upper.strip() or font.missing(upper):
        return None
    margin = _margin(geometry)
    inner_w, inner_h = geometry.width - 2 * margin, geometry.height - 2 * margin
    longest = max(len(word) for word in upper.split())
    largest = inner_h // font.CELL_HEIGHT
    for whole_words in (True, False):
        for scale in range(largest, min_scale - 1, -1):
            per_line = (inner_w // scale + font.TRACKING) // (font.GLYPH_WIDTH + font.TRACKING)
            if whole_words and longest > per_line:
                continue
            lines = wrap(upper, per_line)
            if not lines:
                continue
            height = len(lines) * font.CELL_HEIGHT * scale + (len(lines) - 1) * scale
            if height <= inner_h and all(font.width_of(one) * scale <= inner_w for one in lines):
                return lines, scale
    return None


def _sentence_frame(line: str, languages: tuple[str, ...], geometry: Geometry):
    """A fixed display line in every declared language, each as large as fits."""
    margin = _margin(geometry)
    inner_w, inner_h = geometry.width - 2 * margin, geometry.height - 2 * margin
    words = DISPLAY_TEXT[line]
    sentences = [words[language] for language in languages if words.get(language)]
    share = max(1, inner_h // max(1, len(sentences)))
    drawn = []
    room = inner_h
    for sentence in sentences:
        scale = _largest_scale(sentence, inner_w, share)
        needed = font.CELL_HEIGHT * scale + scale
        if scale < 1 or needed > room:
            continue
        drawn.append((sentence, scale))
        room -= needed
    if not drawn:
        raise DisplayUnavailable(
            f"a {geometry.width}x{geometry.height} screen has room for no line of "
            f"{line}, and an empty frame tells a driver nothing"
        )
    bitmap = _blank_bitmap(geometry.width, geometry.height)
    _stack(bitmap, drawn, geometry)
    return bitmap


def message_frame_for(text: str, geometry: Geometry):
    """The message, whole, at the largest legible scale; `None` if it cannot be."""
    layout = message_layout(text, geometry)
    if layout is None:
        return None
    lines, scale = layout
    bitmap = _blank_bitmap(geometry.width, geometry.height)
    _stack(bitmap, [(line, scale) for line in lines], geometry)
    return bitmap


def closed_frame_for(message: str, languages: tuple[str, ...], geometry: Geometry):
    """A closed lane's frame: the owner's message ALONE, or, where it cannot be
    drawn whole and legible here, the font's own sentence that the lane is closed."""
    frame = message_frame_for(message, geometry) if message else None
    if frame is not None:
        return frame
    if message:
        log.warning("a closed lane's message cannot be drawn whole on a %dx%d screen; "
                    "showing that the lane is closed", geometry.width, geometry.height)
    return _sentence_frame("display.lane_closed", languages, geometry)


def prices_frame_for(lines: tuple[str, ...], languages: tuple[str, ...], geometry: Geometry):
    """The price item: its heading in each language, then every line, at ONE
    scale -- the largest at which all of them fit. `None` if none does."""
    margin = _margin(geometry)
    inner_w, inner_h = geometry.width - 2 * margin, geometry.height - 2 * margin
    words = DISPLAY_TEXT["display.board.prices"]
    heading = [words[language] for language in languages if words.get(language)]
    texts = [*heading, *lines]
    for scale in range(inner_h // font.CELL_HEIGHT, 0, -1):
        height = len(texts) * font.CELL_HEIGHT * scale + (len(texts) - 1) * scale
        if height <= inner_h and all(font.width_of(one) * scale <= inner_w for one in texts):
            bitmap = _blank_bitmap(geometry.width, geometry.height)
            _stack(bitmap, [(one, scale) for one in texts], geometry)
            return bitmap
    return None


def board_frame_for(item: BoardItem, languages: tuple[str, ...], geometry: Geometry):
    """One board item's frame, or `None` when it cannot be drawn on this screen."""
    if item.kind == PRICES:
        return prices_frame_for(item.lines, languages, geometry)
    return message_frame_for(item.text or "", geometry)


__all__ = [
    "BOARD_ITEM_SECONDS",
    "MESSAGE_SCALE_MIN",
    "BoardItem",
    "BoardTooSlow",
    "Rotation",
    "board_frame_for",
    "check_pass",
    "closed_frame_for",
    "closing_of",
    "items_of",
    "length_label",
    "message_frame_for",
    "message_layout",
    "price_line",
    "prices_frame_for",
    "wrap",
]
