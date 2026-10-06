"""The lane's own words on its screen (U4c): a closed lane's message, and the board.

The lane is a foreign one written from the document's `lane` and `board`
blocks, served over a socket, and the agent reads it through its real poll.
Where the claim is about what a driver READS, the frame goes to a real
framebuffer file and is read back from the bytes by the fee frame's own reader
(`test_fee_frame.read_lines`), which accepts a line only if re-drawing it gives
the ink back exactly. Where the claim is about WHICH frame holds the screen, a
recording screen says what it was given.

`scripts/agent_fail_control.py` breaks the wrap, the order, the rotation and
the price line, and requires this file to go red each time.
"""

from __future__ import annotations

import itertools
import random

import pytest

from foreign_lane import ForeignLane, decided_at
from foreign_lane.lane import make_server
from gate_agent import board, display, font
from gate_agent.board import (
    BOARD_ITEM_SECONDS,
    MESSAGE_SCALE_MIN,
    BoardItem,
    closed_frame_for,
    item_now,
    items_of,
    message_layout,
    wrap,
)
from gate_agent.contract import AgentEventKind
from gate_agent.display import Frame, Geometry, frame_wanted
from gate_agent.fee import figure_for
from gate_agent.lines import DISPLAY_TEXT
from serving import serving
from test_ticket_flow import FakeScreen, events_of

np = pytest.importorskip("numpy")

from test_display import a_screen, picture_from  # noqa: E402
from test_fee_display import a_fee, an_agent, ticket_up  # noqa: E402
from test_fee_frame import read_lines  # noqa: E402

LANGUAGES = ("en", "es-ES")

#: The stand-in screens the display tests use: the ticket flow's 320x240 and
#: the fee frame's 800x480. 160 characters must be legible on both.
STAND_INS = [(320, 240), (800, 480)]

#: Gokhan's words for a full garage (U4c brief, rule 9), as the owner types them.
FULL = "Garage is full. Monthly parkers only."
FULL_ES = "Estacionamiento lleno. Solo mensuales."


def closed(message: str = FULL, reason: str = "full") -> dict:
    return {"state": "closed", "reason": reason, "message": message}


OPEN = {"state": "open", "reason": None, "message": None}


def a_real_screen(tmp_path, width=800, height=480, depth=32):
    where = tmp_path / f"fb-{width}x{height}x{depth}-{random.randrange(1 << 30)}"
    where.mkdir()
    device, sysfs = a_screen(where, width, height, depth)
    return device, display.open_display("exit", device, sysfs)


def read_off(device, screen) -> list[str]:
    return [text for text, _ in read_lines(picture_from(device, screen.geometry))]


def words_on(device, screen) -> list[str]:
    """What a message frame says, word by word: its line breaks are the
    layout's (`message_layout`), measured on their own."""
    return " ".join(read_off(device, screen)).split()


def is_black(device, screen) -> bool:
    return bool((picture_from(device, screen.geometry) == 0).all())


def poll(agent, times=2):
    for _ in range(times):
        agent.poll()


def a_lane() -> ForeignLane:
    lane = ForeignLane()
    lane.direction = "exit"
    lane.window = 64
    return lane


# ---------------------------------------------------------------------------
# the wrap: by words, never cut
# ---------------------------------------------------------------------------


def _letters(text: str) -> str:
    return "".join(text.split())


@pytest.mark.parametrize("per_line", [1, 3, 7, 13, 25, 51])
def test_the_wrap_keeps_every_character_in_order_and_breaks_between_words(per_line):
    rng = random.Random(per_line)
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.,"
    for _ in range(300):
        text = " ".join(
            "".join(rng.choice(alphabet) for _ in range(rng.randint(1, 30)))
            for _ in range(rng.randint(1, 12))
        )
        lines = wrap(text, per_line)
        assert _letters("".join(lines)) == _letters(text), "a character was cut"
        assert all(0 < len(line) <= per_line for line in lines)
        assert all(line == line.strip() for line in lines)
        # BY WORDS: a word that fits on a line is never broken across two.
        for word in text.split():
            if len(word) <= per_line:
                assert any(word in line.split() for line in lines), (word, lines)


def test_a_word_longer_than_a_line_is_carried_and_not_cut():
    assert wrap("ABCDEFGHIJ KL", 4) == ["ABCD", "EFGH", "IJ", "KL"]


# ---------------------------------------------------------------------------
# 160 characters, legible, at the stand-in geometries
# ---------------------------------------------------------------------------


def _messages_of_160():
    """The widest-wrapping 160-character messages: every word length from one
    character to one word of 160, and a seeded random mix."""
    for length in range(1, 161):
        text = ""
        while True:
            candidate = (text + " " if text else "") + "W" * length
            if len(candidate) > 160:
                break
            text = candidate
        # pad to exactly 160 with a last, shorter word
        if len(text) < 159:
            text = text + " " + "W" * (159 - len(text))
        yield length, text[:160]
    rng = random.Random(160)
    for index in range(200):
        text = ""
        while len(text) < 160:
            text += ("" if not text else " ") + "M" * rng.randint(1, 20)
        yield f"mix{index}", text[:160].rstrip()


@pytest.mark.parametrize(("width", "height"), STAND_INS)
def test_160_characters_fit_legibly_at_the_stand_in_geometries(width, height):
    geometry = Geometry(width=width, height=height, bits_per_pixel=32, stride=width * 4)
    worst = None
    for label, text in _messages_of_160():
        assert len(text) <= 160
        layout = message_layout(text, geometry)
        assert layout is not None, f"{label!r} does not fit at scale {MESSAGE_SCALE_MIN}"
        lines, scale = layout
        # LEGIBLE IS TWO PIXELS A MODULE, stated here as the number and not
        # read back from the constant it measures.
        assert scale >= 2 and MESSAGE_SCALE_MIN == 2
        assert _letters("".join(lines)) == _letters(text.upper())
        worst = scale if worst is None else min(worst, scale)
    assert worst >= MESSAGE_SCALE_MIN


@pytest.mark.parametrize(("width", "height"), STAND_INS)
def test_the_160_character_message_is_read_back_whole_off_the_screen(tmp_path, width, height):
    """The frame on the device, read back: every line, every letter, in order."""
    message = ("Garage is full tonight for the concert. Monthly parkers only. "
               "Everyone else please use the north garage on Elm Street, two blocks away!")
    assert len(message) == 135
    message = (message + " Thank you.")[:160]
    device, screen = a_real_screen(tmp_path, width, height)
    screen.show(closed_frame_for(message, LANGUAGES, screen.geometry))
    lines, scale = message_layout(message, screen.geometry)
    read = read_lines(picture_from(device, screen.geometry))
    assert [text for text, _ in read] == lines
    assert {one for _, one in read} == {scale}
    assert " ".join(lines).split() == message.upper().split()


def test_a_message_this_font_cannot_draw_says_the_lane_is_closed_instead(tmp_path):
    """NEVER A MISSING LETTER: an undrawable character is not left out of a
    message -- the frame says the lane is closed, in every declared language."""
    device, screen = a_real_screen(tmp_path)
    screen.show(closed_frame_for("Closed €5 parking", LANGUAGES, screen.geometry))
    assert read_off(device, screen) == [DISPLAY_TEXT["display.lane_closed"][one]
                                        for one in LANGUAGES]


def test_a_message_too_long_for_the_screen_is_never_cut(tmp_path):
    device, screen = a_real_screen(tmp_path, 160, 60)
    assert message_layout("W " * 80, screen.geometry) is None
    screen.show(closed_frame_for("W " * 80, ("en",), screen.geometry))
    assert read_off(device, screen) == [DISPLAY_TEXT["display.lane_closed"]["en"]]


# ---------------------------------------------------------------------------
# which frame wins (B4): every pair
# ---------------------------------------------------------------------------

ORDER = (Frame.TICKET, Frame.FEE, Frame.CLOSED, Frame.BOARD)


@pytest.mark.parametrize(
    "wants", list(itertools.product((False, True), repeat=4)),
    ids=lambda wants: "".join("1" if one else "0" for one in wants),
)
def test_the_order_holds_for_every_combination(wants):
    """TICKET, FEE, CLOSED, BOARD, BLANK -- for all sixteen combinations."""
    expected = next((frame for frame, up in zip(ORDER, wants, strict=True) if up), Frame.BLANK)
    assert frame_wanted(*wants) is expected


def test_a_closed_lane_is_shown_alone_over_its_board(tmp_path):
    lane = a_lane()
    with serving(make_server(lane)) as url:
        device, screen = a_real_screen(tmp_path)
        agent = an_agent(tmp_path, url, screen)
        lane.board = {"items": [{"kind": "message", "text": "Event tonight"}, PRICES]}
        lane.closing = closed()
        poll(agent)
        assert read_off(device, screen) == wrap_of(FULL, screen)
        for _ in range(6):  # whatever the rotation's turn, nothing else shows
            agent._clock.advance(BOARD_ITEM_SECONDS)
            poll(agent, 1)
            assert read_off(device, screen) == wrap_of(FULL, screen)


def wrap_of(message: str, screen) -> list[str]:
    lines, _ = message_layout(message, screen.geometry)
    return lines


# ---------------------------------------------------------------------------
# rule 6: the closed lane's screen, through the agent's real poll
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("message", [FULL, FULL_ES])
@pytest.mark.parametrize(("width", "height"), STAND_INS)
def test_a_closed_lane_shows_the_owners_message_upper_case_and_whole(
    tmp_path, message, width, height
):
    lane = a_lane()
    with serving(make_server(lane)) as url:
        device, screen = a_real_screen(tmp_path, width, height)
        agent = an_agent(tmp_path, url, screen)
        lane.closing = closed(message)
        poll(agent)
        lines = read_off(device, screen)
    assert " ".join(lines).split() == message.upper().split()


def test_reopening_gives_black(tmp_path):
    lane = a_lane()
    with serving(make_server(lane)) as url:
        device, screen = a_real_screen(tmp_path)
        agent = an_agent(tmp_path, url, screen)
        lane.closing = closed()
        poll(agent)
        assert read_off(device, screen)
        lane.closing = OPEN
        poll(agent)
        assert is_black(device, screen)


def test_a_lane_that_publishes_no_closing_is_open(tmp_path):
    lane = a_lane()
    screen = FakeScreen()
    with serving(make_server(lane)) as url:
        agent = an_agent(tmp_path, url, screen)
        poll(agent, 4)
    assert screen.frames == [] and screen.blanked == 0


def test_a_fee_wins_over_the_message_and_hands_the_screen_back_to_it(tmp_path):
    lane = a_lane()
    with serving(make_server(lane)) as url:
        device, screen = a_real_screen(tmp_path)
        agent = an_agent(tmp_path, url, screen)
        lane.closing = closed()
        lane.exit_fee = a_fee(1000)
        poll(agent)
        assert read_off(device, screen)[-1] == "10.00 USD"
        lane.exit_fee = None
        poll(agent)
        assert read_off(device, screen) == wrap_of(FULL, screen), "not back to the message"


def test_a_ticket_wins_over_the_message_and_hands_the_screen_back_to_it(tmp_path):
    """NEVER BLACK ON THE WAY: a ticket that was up when the lane closed holds the
    screen, and when it ends the next thing written is the message."""
    lane = a_lane()
    screen = FakeScreen()
    with serving(make_server(lane)) as url:
        agent = ticket_up(tmp_path, lane, url, screen)
        lane.closing = closed()
        poll(agent, 2)
        message = closed_frame_for(FULL, LANGUAGES, screen.geometry)
        assert screen.frames and screen.frames[-1] != message, "the message took a ticket's screen"
        blanked = screen.blanked
        lane.decision = {**lane.decision, "presence": False}  # the car reversed away
        poll(agent, 2)
        assert len(events_of(agent, AgentEventKind.TICKET_VOIDED)) == 1
    assert screen.blanked == blanked, "a frame was replaced by a blank one"
    assert screen.frames[-1] == message


# ---------------------------------------------------------------------------
# the board (B1-B3): what the lane publishes, one item at a time
# ---------------------------------------------------------------------------

PRICES = {
    "kind": "prices",
    "lines": [
        {"minutes": 60, "fee_minor": 500, "currency": "USD", "minor_unit_digits": 2},
        {"minutes": 120, "fee_minor": 875, "currency": "USD", "minor_unit_digits": 2},
        {"minutes": 180, "fee_minor": 1137, "currency": "USD", "minor_unit_digits": 2},
        {"minutes": 1440, "fee_minor": 2500, "currency": "USD", "minor_unit_digits": 2},
    ],
}


def test_the_price_drawn_is_the_figure_the_lane_published(tmp_path):
    """Check 14, the screen's half: every line on the screen is the lane's own
    figure, written with the digits it published -- read off the device."""
    lane = a_lane()
    with serving(make_server(lane)) as url:
        device, screen = a_real_screen(tmp_path)
        agent = an_agent(tmp_path, url, screen)
        lane.board = {"items": [PRICES]}
        poll(agent)
        lines = read_off(device, screen)
    heading = [DISPLAY_TEXT["display.board.prices"][one] for one in LANGUAGES]
    published = [
        f"{board.length_label(line['minutes'])} "
        f"{figure_for(line['fee_minor'], line['currency'], line['minor_unit_digits'])}"
        for line in PRICES["lines"]
    ]
    assert lines == heading + published
    assert published == ["1H 5.00 USD", "2H 8.75 USD", "3H 11.37 USD", "24H 25.00 USD"]


def test_the_digits_the_lane_published_are_the_ones_drawn():
    """A currency the build's own list does not know, priced by the engine with
    three digits: drawn with three, as the lane published."""
    item = items_of({"board": {"items": [{"kind": "prices", "lines": [
        {"minutes": 60, "fee_minor": 1500, "currency": "KWD", "minor_unit_digits": 3}]}]}})
    assert item == (BoardItem("prices", lines=("1H 1.500 KWD",)),)


def test_a_price_line_that_cannot_be_written_is_left_out_and_never_guessed():
    state = {"board": {"items": [{"kind": "prices", "lines": [
        {"minutes": 60, "fee_minor": 500, "currency": "USD", "minor_unit_digits": 2},
        {"minutes": 120, "fee_minor": 900, "currency": "SEK"},           # digits unknown
        {"minutes": 180, "fee_minor": True, "currency": "USD", "minor_unit_digits": 2},
        {"minutes": 0, "fee_minor": 1, "currency": "USD", "minor_unit_digits": 2},
    ]}]}}
    assert items_of(state) == (BoardItem("prices", lines=("1H 5.00 USD",)),)
    nothing = {"board": {"items": [{"kind": "prices", "lines": [
        {"minutes": 60, "fee_minor": 900, "currency": "SEK"}]}]}}
    assert items_of(nothing) == ()


def test_the_board_shows_what_the_lane_publishes_and_nothing_else(tmp_path):
    """Check 13, the screen's half: which messages are in force -- which lanes,
    which times -- is the LANE's to decide. The screen shows what it publishes,
    and a message it stops publishing leaves the screen."""
    lane = a_lane()
    with serving(make_server(lane)) as url:
        device, screen = a_real_screen(tmp_path)
        agent = an_agent(tmp_path, url, screen)
        lane.board = {"items": [{"kind": "message", "text": "Event tonight from 8 pm"}]}
        poll(agent)
        assert words_on(device, screen) == "EVENT TONIGHT FROM 8 PM".split()
        lane.board = {"items": []}
        poll(agent)
        assert is_black(device, screen)


def test_every_item_gets_its_turn_in_order(tmp_path):
    """Check 16: four items, each holding the screen for BOARD_ITEM_SECONDS, in
    the lane's order, and round again -- read off the device each time."""
    texts = ["FIRST NOTICE", "SECOND NOTICE", "THIRD NOTICE"]
    lane = a_lane()
    with serving(make_server(lane)) as url:
        device, screen = a_real_screen(tmp_path)
        agent = an_agent(tmp_path, url, screen)
        lane.board = {"items": [*({"kind": "message", "text": one.lower()} for one in texts),
                                PRICES]}
        agent._clock.value = 0.0
        seen = []
        for _ in range(8):
            poll(agent, 1)
            seen.append(words_on(device, screen))
            agent._clock.advance(BOARD_ITEM_SECONDS)
    heading = [DISPLAY_TEXT["display.board.prices"][one] for one in LANGUAGES]
    prices = heading + ["1H 5.00 USD", "2H 8.75 USD", "3H 11.37 USD", "24H 25.00 USD"]
    turn = [texts[0].split(), texts[1].split(), texts[2].split(), " ".join(prices).split()]
    assert seen == turn + turn


def test_the_rotation_gives_every_item_the_same_share():
    items = tuple(BoardItem("message", text=str(n)) for n in range(5))
    counts = {item: 0 for item in items}
    for tick in range(1000):
        counts[item_now(items, tick * BOARD_ITEM_SECONDS / 4)] += 1
    assert set(counts.values()) == {200}


def test_an_item_this_font_cannot_draw_is_not_on_the_board():
    state = {"board": {"items": [{"kind": "message", "text": "Prix €5"},
                                 {"kind": "message", "text": "Open late"},
                                 {"kind": "weather", "text": "Sunny"}]}}
    assert items_of(state) == (BoardItem("message", text="Open late"),)


def test_the_board_lines_are_upper_case_and_drawable():
    for line in ("display.lane_closed", "display.board.prices"):
        for language in LANGUAGES:
            text = DISPLAY_TEXT[line][language]
            assert text == text.upper() and not font.missing(text)


def test_a_fee_wins_over_the_board(tmp_path):
    lane = a_lane()
    with serving(make_server(lane)) as url:
        device, screen = a_real_screen(tmp_path)
        agent = an_agent(tmp_path, url, screen)
        lane.board = {"items": [{"kind": "message", "text": "Open late"}]}
        lane.exit_fee = a_fee(1000)
        poll(agent)
        assert read_off(device, screen)[-1] == "10.00 USD"
        lane.exit_fee = None
        poll(agent)
        assert words_on(device, screen) == ["OPEN", "LATE"]


def test_the_decision_moment_does_not_matter_to_the_board(tmp_path):
    """The board is the lane's, not the car's: a decision at the lane does not
    take it down."""
    lane = a_lane()
    with serving(make_server(lane)) as url:
        device, screen = a_real_screen(tmp_path)
        agent = an_agent(tmp_path, url, screen)
        lane.board = {"items": [{"kind": "message", "text": "Open late"}]}
        lane.decision = {**lane.decision, "at": decided_at()}
        poll(agent)
        assert words_on(device, screen) == ["OPEN", "LATE"]


def test_a_message_that_fits_only_below_legible_size_is_not_drawn_small(tmp_path):
    """One pixel a module is not legible from a car: a message that only fits at
    that size is not drawn at it -- the lane says it is closed instead."""
    device, screen = a_real_screen(tmp_path, 240, 40)
    message = "NORTH GARAGE ONLY TONIGHT PLEASE"
    assert message_layout(message, screen.geometry, min_scale=1) is not None
    assert message_layout(message, screen.geometry) is None
    screen.show(closed_frame_for(message, ("en",), screen.geometry))
    assert read_off(device, screen) == [DISPLAY_TEXT["display.lane_closed"]["en"]]
