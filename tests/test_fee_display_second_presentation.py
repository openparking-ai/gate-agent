"""OUR lane's `exit_fee`, drawn: a validated stay closed, then the SAME stay put in
front of the reader again, on every screen. Each hand-over is a fresh record, as the
lane's exit decision makes one."""

from __future__ import annotations

import pytest
from lane_controller.reader import ValidatingScreen

from ours import our_lane, our_server
from serving import serving
from test_fee_display import GEOMETRIES, a_real_screen, an_agent, poll, read_off

np = pytest.importorskip("numpy")

from test_display import picture_from  # noqa: E402

SESSION = "s-validated"
PRICED = {
    "status": "priced",
    "session_id": SESSION,
    "fee_minor": 1000,
    "currency": "USD",
    "breakdown": [{"delta_minor": 1000, "text": "Parking, 3 h 30 min"}],
}


class Reader:
    def __init__(self) -> None:
        self.shown: list[dict | None] = []

    def present_exit(self, record) -> None:
        self.shown.append(record)


class Typed:
    """The driver types a phone number; the display is read while the prompt is up."""

    def __init__(self) -> None:
        self.at_prompt = None
        self.readings: list[tuple[int, str | None]] = []

    def ask(self, prompt):
        if self.at_prompt is not None:
            self.readings.append((prompt["fee_minor"], self.at_prompt()))
        return "2025550143"


def held(record, phone):
    return {
        "outcome": "held",
        "currency": record["currency"],
        "fee_before_minor": record["fee_minor"],
        "discount_minor": 200,
        "fee_minor": record["fee_minor"] - 200,
        "line": {"delta_minor": -200, "text": "Validation"},
    }


@pytest.mark.parametrize(("width", "height", "depth"), GEOMETRIES)
def test_a_closed_stay_presented_again_reads_as_the_reader_shows_it(
    tmp_path, width, height, depth
):
    reader = Reader()
    typed = Typed()
    screen_at_reader = ValidatingScreen(reader, typed, held)
    controller = our_lane()
    controller.reader = screen_at_reader
    with serving(our_server(controller)) as url:
        device, screen = a_real_screen(tmp_path, width, height, depth)
        agent = an_agent(tmp_path, url, screen)

        def reading():
            poll(agent)
            if not picture_from(device, screen.geometry).any():
                return None
            return read_off(device, screen)[-1]

        # The first hand-over: validated, sealed, cleared.
        controller.show_reader(dict(PRICED))
        assert reader.shown[-1]["fee_minor"] == 800, "the validation was not held"
        assert reading() == "8.00 USD"
        assert screen_at_reader.seal(SESSION) == {"fee_minor": 800, "currency": "USD"}
        assert reading() == "8.00 USD"
        controller.show_reader(None)
        assert reading() is None, "the fee was left up"

        # The same stay again: the close is recorded, so the reader is given the
        # fee as priced -- on the prompt and on the cart -- and so is every screen.
        typed.at_prompt = reading
        controller.show_reader(dict(PRICED))
        assert reader.shown[-1]["fee_minor"] == 1000
        assert typed.readings == [(1000, "10.00 USD")], "the first stay's discount came back"
        assert reading() == "10.00 USD", "the first stay's discount came back"
        controller.show_reader(None)
        assert reading() is None
