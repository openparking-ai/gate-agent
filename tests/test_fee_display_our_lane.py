"""OUR lane's `exit_fee`, drawn: a validated stay, read through its close, on every screen.

`test_fee_display.py` drives a lane written from the document. This drives the
real `lane_controller` -- its `LaneService`, its `ValidatingScreen` -- so what is
measured is the figure our lane actually publishes at each moment of an exit
with a held validation, not the figure the document says it publishes:

- the discounted cart is up: the display reads the reader's figure;
- the close has SEALED what the reader showed, and the reader is not cleared
  yet: the display still reads the reader's figure, never the fee as priced;
- the reader is cleared: every screen goes black.

Each moment is read back off a real framebuffer at every geometry.
"""

from __future__ import annotations

import pytest
from lane_controller.reader import ValidatingScreen

from ours import our_lane, our_server
from serving import serving
from test_fee_display import GEOMETRIES, a_real_screen, an_agent, poll, read_off

np = pytest.importorskip("numpy")

from test_display import picture_from  # noqa: E402

SESSION = "s-validated"
#: A priced stay as the lane's exit decision hands it to the reader.
PRICED = {
    "status": "priced",
    "session_id": SESSION,
    "fee_minor": 1000,
    "currency": "USD",
    "breakdown": [{"delta_minor": 1000, "text": "Parking, 3 h 30 min"}],
}


class Reader:
    """The card reader under the validating screen: what it was handed."""

    def __init__(self) -> None:
        self.shown: list[dict | None] = []

    def present_exit(self, record) -> None:
        self.shown.append(record)


class Typed:
    """The driver types a phone number at the prompt."""

    def ask(self, prompt):
        return "2025550143"


def held(record, phone):
    """The platform holds a 2.00 validation for the stay."""
    return {
        "outcome": "held",
        "currency": record["currency"],
        "fee_before_minor": record["fee_minor"],
        "discount_minor": 200,
        "fee_minor": record["fee_minor"] - 200,
        "line": {"delta_minor": -200, "text": "Validation"},
    }


@pytest.mark.parametrize(("width", "height", "depth"), GEOMETRIES)
def test_a_validated_stay_reads_as_the_reader_showed_it_through_the_close(
    tmp_path, width, height, depth
):
    reader = Reader()
    screen_at_reader = ValidatingScreen(reader, Typed(), held)
    controller = our_lane()
    controller.reader = screen_at_reader
    with serving(our_server(controller)) as url:
        device, screen = a_real_screen(tmp_path, width, height, depth)
        agent = an_agent(tmp_path, url, screen)

        controller.show_reader(PRICED)
        assert reader.shown[-1]["fee_minor"] == 800, "the validation was not held"
        poll(agent)
        assert read_off(device, screen)[-1] == "8.00 USD"

        # The close, as the lane records it: what the reader showed is sealed
        # FIRST, and the reader is cleared after the close is on the queue.
        assert screen_at_reader.seal(SESSION) == {"fee_minor": 800, "currency": "USD"}
        poll(agent)
        assert read_off(device, screen)[-1] == "8.00 USD", "the fee as priced came back"

        controller.show_reader(None)
        poll(agent)
        assert not picture_from(device, screen.geometry).any(), "the fee was left up"
