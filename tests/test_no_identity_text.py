"""No route of this monitor, and no message it sends, carries identity text.

The reason is structural rather than careful: the monitor reads `/v1/lane` and
`/v1/lane/health` and nothing else. It never reads `/v1/lane/state` or
`/v1/lane/events`, so it does not hold a plate to leak — and `Notification` has
nowhere to put one.

That is a claim about a SEARCH, so it is run against a lane that really did
handle a vehicle with a plate, and every sweep here carries the control that
proves it could have found one.
"""

from __future__ import annotations

import json

from conftest import config_for, monitor_for
from fakes import RecordingSink
from ours import PLATE_ON_THE_WIRE, our_lane, our_server
from serving import serving


def _monitor_over_our_lane():
    controller = our_lane()
    server = our_server(controller)
    return controller, server


def test_no_monitor_route_publishes_identity_text():
    """The three routes, serialised, from a monitor watching a real vehicle."""
    controller, server = _monitor_over_our_lane()
    with serving(server) as url:
        monitor = monitor_for(config_for(lane=url), [RecordingSink()])
        monitor.start()
        served = {
            "/v1/monitor": json.dumps(monitor.describe().to_dict()),
            "/v1/monitor/health": json.dumps(monitor.health().to_dict()),
            "/v1/monitor/events": json.dumps(monitor.events(0).to_dict()),
        }

    # The run is not vacuous: that lane really did decide about a vehicle with
    # this plate, and the plate really is in that lane's own record.
    assert controller.last_decision is not None
    assert controller.last_decision.identity.plate == PLATE_ON_THE_WIRE

    for route, body in served.items():
        assert PLATE_ON_THE_WIRE not in body, f"{route} published plate text: {body}"

    # THE CONTROL, per route: the same sweep over the same payload with a plate
    # planted in it must find one. Run route by route, because a control on the
    # events page says nothing about whether the health page was searched.
    for route, payload in (
        ("/v1/monitor", monitor.describe().to_dict()),
        ("/v1/monitor/health", monitor.health().to_dict()),
        ("/v1/monitor/events", monitor.events(0).to_dict()),
    ):
        planted = json.dumps({**payload, "planted": PLATE_ON_THE_WIRE})
        assert PLATE_ON_THE_WIRE in planted, (
            f"the sweep cannot see a plate planted in {route}'s payload, so its absence "
            "there says nothing"
        )


def test_the_monitor_never_asks_for_the_routes_that_carry_one():
    """The structural half, and it is the one that keeps the sweep true tomorrow.

    A sweep over today's payloads passes for a monitor that reads
    `/v1/lane/state` and happens not to render the plate. This is the assertion
    that stops it: those routes are never requested at all, so there is nothing
    held anywhere in this process to render by accident later.
    """
    _controller, server = _monitor_over_our_lane()
    with serving(server) as url:
        monitor = monitor_for(config_for(lane=url), [RecordingSink()])
        monitor.start()
        monitor.poll(force=True)

    paths = {path for _method, path in server.requests.seen}
    assert paths == {"/v1/lane", "/v1/lane/health"}, (
        f"the monitor read {sorted(paths)}; a plate lives on /v1/lane/state and "
        "/v1/lane/events, and this monitor has no business on either"
    )
    # THE CONTROL: the routes the monitor did not ask for EXIST and answer. Their
    # absence from the set above is therefore a fact about the monitor, not about
    # a lane that has no such routes -- which is what it would be if this were
    # not checked, and the assertion would then be about nothing.
    import urllib.request

    with serving(our_server(our_lane())) as url:
        for path in ("/v1/lane/state", "/v1/lane/events"):
            with urllib.request.urlopen(f"{url}{path}", timeout=5) as response:
                assert response.status == 200
