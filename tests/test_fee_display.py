"""The fee on the barrier display: the lane's `exit_fee`, drawn, read back, ruled against a ticket.

The lane is a foreign one written from the document's `exit_fee` block, served
over a socket, and the agent reads it through its real poll. Where the claim is
about what a driver reads, the frame goes to a REAL framebuffer file and is read
back from the bytes by the fee frame's own reader (`test_fee_frame.read_lines`),
at more than one geometry and one of them portrait. Where the claim is about
WHICH frame a screen holds, a recording screen says what it was given.

`scripts/agent_fail_control.py` breaks the feed and the rule and requires this
file to go red each time.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from conftest import FakeClock, agent_config_for, agent_for
from fake_ua import FakeUa
from foreign_lane import ForeignLane, decided_at
from foreign_lane.lane import make_server
from gate_agent import display
from gate_agent.config import Target, TicketSettings
from gate_agent.contract import AgentEventKind, TargetKind
from gate_agent.display import Frame, frame_wanted
from gate_agent.fee import FeeScreen, FeeState, fee_frame_for
from gate_agent.lines import DISPLAY_TEXT
from serving import serving
from test_ticket_flow import FakeScreen, events_of, foreign_decides

np = pytest.importorskip("numpy")

from test_display import a_screen, picture_from  # noqa: E402
from test_fee_frame import read_lines  # noqa: E402

LANGUAGES = ("en", "es-ES")
SIGNING_KEY = b"a-signing-key-long-enough-for-the-floor"

#: A landscape screen, a PORTRAIT one, and a small one at 24 bits.
GEOMETRIES = [(800, 480, 32), (480, 800, 32), (320, 240, 24)]


def a_fee(fee_minor=1000, currency="USD", digits=2, status="priced"):
    """`exit_fee` as the document's block shows it."""
    return {"decision_at": decided_at(), "status": status, "fee_minor": fee_minor,
            "currency": currency, "minor_unit_digits": digits}


def an_agent(tmp_path, url, screen, *, tickets=False):
    base = agent_config_for(tmp_path, lane_url=url)
    config = replace(
        base,
        lanes=(Target(name="exit", kind=TargetKind.LANE, url=url, poll_seconds=0.0,
                      timeout_seconds=5.0),),
        intercoms=(replace(base.intercoms[0], lane="exit", display=screen.name),),
        displays={screen.name: screen},
        tickets=(TicketSettings(signing_key=SIGNING_KEY, directory=tmp_path / "tickets")
                 if tickets else None),
        driver_languages=LANGUAGES,
    )
    return agent_for(config, FakeUa(), clock=FakeClock())


def a_real_screen(tmp_path, width, height, depth):
    where = tmp_path / f"fb-{width}x{height}x{depth}"
    where.mkdir()
    device, sysfs = a_screen(where, width, height, depth)
    return device, display.open_display("exit", device, sysfs)


def an_exit_lane():
    lane = ForeignLane()
    lane.direction = "exit"
    lane.window = 64
    return lane


def read_off(device, screen) -> list[str]:
    return [text for text, _ in read_lines(picture_from(device, screen.geometry))]


def sentence(line: str) -> list[str]:
    return [DISPLAY_TEXT[line][language] for language in LANGUAGES]


def poll(agent, times=2):
    for _ in range(times):
        agent.poll()


# ---------------------------------------------------------------------------
# the one value: the figure on the screen is the figure the lane published
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("width", "height", "depth"), GEOMETRIES)
def test_the_published_fee_is_the_figure_read_off_the_screen(tmp_path, width, height, depth):
    lane = an_exit_lane()
    with serving(make_server(lane)) as url:
        device, screen = a_real_screen(tmp_path, width, height, depth)
        agent = an_agent(tmp_path, url, screen)
        lane.exit_fee = a_fee(1000)
        poll(agent)
        lines = read_off(device, screen)
    assert lines[-1] == "10.00 USD"
    assert lines[:-1] and set(lines[:-1]) <= set(sentence("display.fee.label")), lines


def test_the_figure_is_read_and_not_added_up(tmp_path):
    """PLANTED: a fee no line on any ledger sums to. Only a screen that READS the
    published figure can show it."""
    lane = an_exit_lane()
    with serving(make_server(lane)) as url:
        device, screen = a_real_screen(tmp_path, 800, 480, 32)
        agent = an_agent(tmp_path, url, screen)
        lane.exit_fee = a_fee(777)
        poll(agent)
        assert read_off(device, screen)[-1] == "7.77 USD"


@pytest.mark.parametrize(
    ("currency", "fee_minor", "digits", "shown"),
    [
        # SEK is the currency two gates measured this display refusing while the
        # reader showed a cart: with the engine's digits, both show the money.
        ("SEK", 5000, 2, "50.00 SEK"),
        ("JPY", 5000, 0, "5000 JPY"),
        ("KWD", 5000, 3, "5.000 KWD"),
    ],
)
def test_the_digits_the_lane_publishes_put_the_decimal_point(
    tmp_path, currency, fee_minor, digits, shown
):
    lane = an_exit_lane()
    with serving(make_server(lane)) as url:
        device, screen = a_real_screen(tmp_path, 800, 480, 32)
        agent = an_agent(tmp_path, url, screen)
        lane.exit_fee = a_fee(fee_minor, currency, digits)
        poll(agent)
        assert read_off(device, screen)[-1] == shown


@pytest.mark.parametrize(
    ("currency", "digits"),
    [
        ("SEK", None),   # no count published and not on this build's list: not guessed
        ("USD", 0),      # the count and the list disagree: one of them is wrong
        ("SEK", 7),      # no currency has seven
        ("SEK", True),   # not a number
    ],
)
def test_a_figure_whose_digits_are_not_known_is_not_shown(tmp_path, currency, digits):
    lane = an_exit_lane()
    with serving(make_server(lane)) as url:
        device, screen = a_real_screen(tmp_path, 800, 480, 32)
        agent = an_agent(tmp_path, url, screen)
        lane.exit_fee = a_fee(5000, currency, digits)
        poll(agent)
        assert read_off(device, screen) == sentence("display.fee.not_shown")


@pytest.mark.parametrize(("width", "height", "depth"), GEOMETRIES)
@pytest.mark.parametrize(
    ("published", "line"),
    [
        ({"status": "covered"}, "display.fee.covered"),
        ({"status": "priced", "fee_minor": 0, "currency": "USD", "minor_unit_digits": 2},
         "display.fee.nothing_to_pay"),
        ({"status": "engine_refused"}, "display.fee.not_shown"),
        ({"status": "no_cached_entry"}, "display.fee.not_shown"),
        # a priced stay the reader has no cart for: the lane publishes no figure
        ({"status": "priced"}, "display.fee.not_shown"),
    ],
)
def test_every_state_with_no_payment_is_drawn_and_read_back(
    tmp_path, width, height, depth, published, line
):
    lane = an_exit_lane()
    with serving(make_server(lane)) as url:
        device, screen = a_real_screen(tmp_path, width, height, depth)
        agent = an_agent(tmp_path, url, screen)
        lane.exit_fee = {"decision_at": decided_at(), "fee_minor": None, "currency": None,
                         "minor_unit_digits": None, **published}
        poll(agent)
        assert read_off(device, screen) == sentence(line)


# ---------------------------------------------------------------------------
# when the lane takes it down, and when the lane says nothing
# ---------------------------------------------------------------------------


def test_the_fee_comes_down_when_the_lane_takes_it_down(tmp_path):
    lane = an_exit_lane()
    with serving(make_server(lane)) as url:
        device, screen = a_real_screen(tmp_path, 800, 480, 32)
        agent = an_agent(tmp_path, url, screen)
        lane.exit_fee = a_fee(1000)
        poll(agent)
        assert read_off(device, screen)[-1] == "10.00 USD"
        lane.exit_fee = None
        poll(agent)
        idle = picture_from(device, screen.geometry)
        assert (idle == 0).all(), "the next driver would read this one's fee"


def test_a_lane_that_cannot_be_read_leaves_the_fee_up(tmp_path):
    lane = an_exit_lane()
    with serving(make_server(lane)) as url:
        device, screen = a_real_screen(tmp_path, 800, 480, 32)
        agent = an_agent(tmp_path, url, screen)
        lane.exit_fee = a_fee(1000)
        poll(agent)
    poll(agent)  # the lane is gone: it has said nothing
    assert read_off(device, screen)[-1] == "10.00 USD"


def test_a_fee_frame_that_cannot_be_drawn_blanks_the_screen_and_says_so(tmp_path):
    """A screen with room for no line of the frame: not left showing whatever was
    there -- that may be an earlier car's fee -- and `display_unavailable`."""
    from gate_agent.display import Geometry

    lane = an_exit_lane()
    screen = FakeScreen()
    screen.geometry = screen.next_geometry = Geometry(width=4, height=4, bits_per_pixel=32,
                                                      stride=16)
    with serving(make_server(lane)) as url:
        agent = an_agent(tmp_path, url, screen)
        lane.exit_fee = a_fee(1000)
        poll(agent)
        codes = {entry["code"]: entry["state"] for entry in agent.health().to_dict()["codes"]
                 if entry["subject"] == screen.name}
    assert screen.frames == [] and screen.blanked >= 1
    assert codes["display_unavailable"] == "active"


def test_an_idle_screen_is_left_alone(tmp_path):
    lane = an_exit_lane()
    screen = FakeScreen()
    with serving(make_server(lane)) as url:
        agent = an_agent(tmp_path, url, screen)
        poll(agent, 4)
    assert screen.frames == [] and screen.blanked == 0


# ---------------------------------------------------------------------------
# which frame wins
# ---------------------------------------------------------------------------


def test_the_rule_answers_for_every_pair():
    assert frame_wanted(True, True) is Frame.TICKET
    assert frame_wanted(True, False) is Frame.TICKET
    assert frame_wanted(False, True) is Frame.FEE
    assert frame_wanted(False, False) is Frame.BLANK


def fee_frame(fee_minor=1000):
    figure = f"{fee_minor // 100}.{fee_minor % 100:02d} USD"
    return fee_frame_for(FeeScreen(FeeState.FEE_DUE, figure, "priced"), LANGUAGES,
                         FakeScreen().geometry)


def is_fee(frame, fee_minor=1000) -> bool:
    return frame == fee_frame(fee_minor)


def ticket_up(tmp_path, lane, url, screen):
    agent = an_agent(tmp_path, url, screen, tickets=True)
    foreign_decides(agent, lane, "no_plate_read")
    assert len(events_of(agent, AgentEventKind.TICKET_ISSUED)) == 1
    return agent


def test_a_ticket_minted_over_a_fee_takes_the_screen(tmp_path):
    lane = an_exit_lane()
    screen = FakeScreen()
    with serving(make_server(lane)) as url:
        lane.exit_fee = a_fee(1000)
        agent = ticket_up(tmp_path, lane, url, screen)
        poll(agent)
    assert any(is_fee(frame) for frame in screen.frames), "the fee was up first"
    assert not is_fee(screen.frames[-1]), "a ticket is up and the fee holds the screen"


def test_a_fee_published_while_a_ticket_is_up_does_not_take_the_screen(tmp_path):
    lane = an_exit_lane()
    screen = FakeScreen()
    with serving(make_server(lane)) as url:
        agent = ticket_up(tmp_path, lane, url, screen)
        lane.exit_fee = a_fee(1000)
        poll(agent, 4)
    assert screen.frames and not any(is_fee(frame) for frame in screen.frames)


def test_a_ticket_that_ends_while_a_fee_is_published_gives_the_screen_to_the_fee(tmp_path):
    """NEVER BLACK ON THE WAY: the ticket is voided and the next thing on the
    screen is the fee, with no blank written between them."""
    lane = an_exit_lane()
    screen = FakeScreen()
    with serving(make_server(lane)) as url:
        agent = ticket_up(tmp_path, lane, url, screen)
        lane.exit_fee = a_fee(1000)
        blanked = screen.blanked
        lane.decision = {**lane.decision, "presence": False}  # the car reversed away
        poll(agent, 2)
        assert len(events_of(agent, AgentEventKind.TICKET_VOIDED)) == 1
    assert screen.blanked == blanked, "a frame was replaced by a blank one"
    assert is_fee(screen.frames[-1])


def test_a_fee_that_comes_down_while_a_ticket_is_up_leaves_the_ticket(tmp_path):
    lane = an_exit_lane()
    screen = FakeScreen()
    with serving(make_server(lane)) as url:
        lane.exit_fee = a_fee(1000)
        agent = ticket_up(tmp_path, lane, url, screen)
        before = screen.blanked
        lane.exit_fee = None
        poll(agent, 4)
    assert screen.blanked == before
    assert not is_fee(screen.frames[-1])


def test_a_ticket_that_ends_with_no_fee_published_goes_black(tmp_path):
    lane = an_exit_lane()
    screen = FakeScreen()
    with serving(make_server(lane)) as url:
        agent = ticket_up(tmp_path, lane, url, screen)
        before = screen.blanked
        lane.decision = {**lane.decision, "presence": False}
        poll(agent, 2)
    assert screen.blanked == before + 1


def test_a_new_fee_replaces_the_last_without_a_blank_between(tmp_path):
    lane = an_exit_lane()
    screen = FakeScreen()
    with serving(make_server(lane)) as url:
        agent = an_agent(tmp_path, url, screen)
        lane.exit_fee = a_fee(1000)
        poll(agent)
        lane.exit_fee = a_fee(2500)
        poll(agent)
    assert screen.blanked == 0
    assert is_fee(screen.frames[-1], 2500)
