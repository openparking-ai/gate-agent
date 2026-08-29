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


def our_lane(identities=None, events=None):
    """The standard installation: two arming loops, two closing loops."""
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
        loop=SimulatedLoopInput(arrivals=1),
        camera=CannedCameraFeed(),
        vend=RecordingVendOutput(),
        identifier=StubVehicleIdentifier(
            identities
            if identities is not None
            else [
                VehicleIdentity(
                    plate=PLATE_ON_THE_WIRE, plate_region="TR", confidence=0.97, presence=True
                )
            ]
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


def our_server(controller=None, port: int = 0, token: str | None = None):
    """`LaneService` on a socket, with every request recorded.

    The recorder wraps `handle_one_request` rather than each `do_*`, so a method
    the lane refuses is recorded exactly like one it serves -- an attempt that
    was refused is still an attempt, and it is the one worth catching.
    """
    controller = controller if controller is not None else our_lane()
    controller.run_once()
    server = make_server(LaneService(controller), port=port, token=token)
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
