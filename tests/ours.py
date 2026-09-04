"""OUR lane, built from the real `lane_controller` package and served for real.

The point of the whole seat is that the monitor reads our lane and a foreign one
through the SAME code, over the same protocol, with no branch on which. That is
only measured if ours is genuinely ours: a fake shaped like a lane would make the
comparison a claim about two fakes.

So this builds a real `LaneController` with the simulated seams that package
ships -- no camera, no barrier, no vision model -- wraps it in the real
`LaneService`, and serves it on a socket. `lane_controller` is a TEST dependency
of this repository and appears nowhere in `src/`, which
`tests/test_no_opening_authority.py` enforces.

Every request that reaches it is RECORDED, method and path, the way the foreign
lane records them. An assertion about the monitor's source can be evaded by a
client the sweep did not recognise; an assertion about what arrived at the lane
cannot.
"""

from __future__ import annotations

from lane_controller import (
    CameraConfig,
    DecisionCache,
    EventQueue,
    GateConfig,
    LaneConfig,
    LaneController,
    LoopConfig,
    VehicleIdentity,
)
from lane_controller.service import LaneService, _Handler, make_server
from lane_controller.simulated import (
    CannedCameraFeed,
    OccupancyLoopInput,
    RecordingVendOutput,
    ScriptedClosingLoops,
    SimulatedLoopInput,
    StubVehicleIdentifier,
)

#: A plate, carried by a real vehicle through a real decision, so the sweep for
#: identity text on the monitor's output has something that could leak.
PLATE_ON_THE_WIRE = "PURGEME9"


def our_lane(identities=None, events=None, arrivals: int = 1):
    """The standard installation: two arming loops, two closing loops.

    `arrivals` is how many times this lane's loop will report a vehicle. One is
    enough for a test that wants a lane with a decision on it; a test that
    drives a SECOND decision -- to void a ticket, or to arrive after a consumer
    has taken up its cursor -- needs more, and the identifier is given one
    identity per arrival so the two cannot run out of step.
    """
    config = LaneConfig(
        lane_id="lane-1",
        site_id="site-1",
        camera=CameraConfig(camera_id="sim-cam-1", rtsp_url="", frames_per_read=3),
        gate=GateConfig(),
        loops=LoopConfig(
            arming_loops=2,
            arming_spacing_m=1.5,
            closing_loops=2,
            closing_spacing_m=1.5,
            confirmation_window_seconds=10.0,
        ),
    )
    cache = DecisionCache()
    cache.load([])
    cache.default_action = "allow"
    return LaneController(
        config,
        loop=SimulatedLoopInput(arrivals=arrivals),
        camera=CannedCameraFeed(),
        vend=RecordingVendOutput(),
        identifier=StubVehicleIdentifier(
            list(identities)
            if identities is not None
            else [
                VehicleIdentity(
                    plate=PLATE_ON_THE_WIRE, plate_region="TR", confidence=0.97, presence=True
                )
            ]
            * arrivals
        ),
        arming_loop_b=OccupancyLoopInput(),
        closing_loops=ScriptedClosingLoops([]),
        cache=cache,
        events=events or EventQueue(),
    )


class Requests:
    """What reached our lane. Recorded on the handler, not asked of the client."""

    def __init__(self) -> None:
        self.seen: list[tuple[str, str]] = []


def our_server(
    controller=None,
    port: int = 0,
    token: str | None = None,
    act_token: str | None = None,
    arrive: bool = True,
):
    """`LaneService` on a socket, with every request recorded.

    The recorder wraps `handle_one_request` rather than each `do_*`, so a method
    the lane refuses is recorded exactly like one it serves -- an attempt that
    was refused is still an attempt, and it is the one worth catching.
    """
    controller = controller if controller is not None else our_lane()
    if arrive:
        # ONE arrival before serving, so a monitor reading this lane finds a
        # decision on it. A test that needs the decision to happen while
        # something is WATCHING -- which is every ticket test, because a first
        # read adopts the cursor and acts on nothing already in the window --
        # passes `arrive=False` and calls `run_once()` itself.
        controller.run_once()
    # `act_token` is what makes the REAL vend route serve at all: the lane
    # refuses it outright when none is configured, which is its own decision and
    # the reason a test that wants to exercise a vend has to hand one over.
    server = make_server(
        LaneService(controller), port=port, token=token, act_token=act_token
    )
    requests = Requests()

    original = server.RequestHandlerClass.handle_one_request

    def recording(self):
        original(self)
        command = getattr(self, "command", None)
        path = getattr(self, "path", None)
        if command and path:
            requests.seen.append((command, path.split("?")[0]))

    server.RequestHandlerClass = type(
        "_RecordingHandler",
        (server.RequestHandlerClass,),
        {"handle_one_request": recording},
    )
    server.requests = requests
    return server


__all__ = ["PLATE_ON_THE_WIRE", "LaneService", "_Handler", "our_lane", "our_server"]
