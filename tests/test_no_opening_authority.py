"""THE INVARIANT: the monitor has no opening authority, proven from the attacker's side.

SETTLED §7: the monitor reads GETs and sends messages. It never calls a vend,
never resolves a transit, never writes to a lane. A monitor that could act would
be a new route to a barrier, and a new route to a barrier is the boundary every
outside reviewer of this project has named.

Three questions, and they are different questions:

  1. **Could it?** The source of every module in this package is walked, and
     every request it builds must be a `GET` with no body. This catches a route
     that exists and is never called, which behaviour cannot.
  2. **Did it?** A whole poll runs against lanes that RECORD what reached them,
     and the set of methods they saw must be exactly `{"GET"}`. This catches a
     client the sweep did not recognise, which a source check cannot.
  3. **Is the seam real?** The package must not import `lane_controller` at all.
     Without this the monitor could reach into a lane in-process and both checks
     above would still pass -- and "our own software is an ordinary client of the
     contract" would be a sentence rather than a property.

Every one of them carries a control, because each is an ABSENCE claim about a
search, and an absence claim with no positive control is a claim about the
search rather than about the world.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

import gate_agent
from conftest import config_for, monitor_for
from fakes import FakeIdentityService, FakePlatform, RecordingSink, identity_server, platform_server
from foreign_lane import ForeignLane
from foreign_lane import make_server as foreign_server
from ours import our_server
from serving import serving

PACKAGE = Path(gate_agent.__file__).resolve().parent
SOURCES = sorted(PACKAGE.glob("*.py"))

#: The one module allowed to build a request that is not a GET, and it points
#: AWAY from a lane: a webhook is how a third party's paging system takes the
#: seat. It is named here, once, so adding a second is a change to this list and
#: therefore a change somebody has to argue for.
MAY_POST = {"sinks.py"}


def _requests_in(path: Path) -> list[ast.Call]:
    """Every `urllib.request.Request(...)` construction in one module."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "Request"
    ]


def _method_of(call: ast.Call) -> str | None:
    for keyword in call.keywords:
        if keyword.arg == "method" and isinstance(keyword.value, ast.Constant):
            return keyword.value.value
    return None


def _has_body(call: ast.Call) -> bool:
    return any(keyword.arg == "data" for keyword in call.keywords)


def test_no_module_that_reads_a_target_can_build_anything_but_a_get():
    """Every request this package makes to a TARGET is a GET with no body."""
    swept = 0
    for path in SOURCES:
        if path.name in MAY_POST:
            continue
        for call in _requests_in(path):
            swept += 1
            assert _method_of(call) == "GET", (
                f"{path.name} builds a request with method {_method_of(call)!r}. Nothing in this "
                "package may reach a target with anything but a GET."
            )
            assert not _has_body(call), f"{path.name} builds a request with a body"
    # THE CONTROL: the sweep found requests at all. Without it, a rename of
    # `urllib.request` would make this pass by looking at nothing.
    assert swept, "the sweep found no requests, so it is not looking at the right thing"


def test_the_sweep_sees_a_planted_non_get():
    """The control for the sweep, run against source known to contain one.

    Parsed rather than written to disk, so nothing tracked is edited -- and the
    same two helpers the sweep uses are the ones exercised, rather than a second
    copy of the logic that happens to agree.
    """
    planted = ast.parse('urllib.request.Request(u, data=b"{}", method="POST")')
    calls = [
        node
        for node in ast.walk(planted)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "Request"
    ]
    assert len(calls) == 1
    assert _method_of(calls[0]) == "POST"
    assert _has_body(calls[0]) is True


def test_nothing_in_the_package_imports_the_lane_controller():
    """The monitor is a CONSUMER of the lane contract, not a part of the lane.

    This is the seat a third party takes. An import here would be a private path
    into our own lane, and the first thing to rot would be the seat: if we do not
    feel the contract's inadequacies, nobody fixes them.
    """
    offenders = []
    for path in SOURCES:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith(
                "lane_controller"
            ):
                offenders.append(f"{path.name}: from {node.module}")
            if isinstance(node, ast.Import):
                offenders.extend(
                    f"{path.name}: import {alias.name}"
                    for alias in node.names
                    if alias.name.startswith("lane_controller")
                )
    assert not offenders, (
        f"the monitor imports the lane it is supposed to be a client of: {offenders}"
    )

    # THE CONTROL: the same walk over a module that DOES import it finds it. The
    # test suite imports `lane_controller` deliberately -- to serve a real lane --
    # so `tests/ours.py` is the positive control, and its existence is what makes
    # the assertion above about `src/` rather than about a walk that sees nothing.
    ours = ast.parse((Path(__file__).parent / "ours.py").read_text(encoding="utf-8"))
    found = [
        node
        for node in ast.walk(ours)
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("lane_controller")
    ]
    assert found, "the control file no longer imports lane_controller; this sweep proves nothing"


def test_the_sinks_module_cannot_reach_a_target():
    """`sinks.py` may POST, and it may not have anything to POST AT.

    Splitting the two directions into two files is what makes either statement
    checkable. This is the half that keeps the exemption honest: the module that
    is allowed a POST must not be able to obtain a target's URL or its
    credential, or the exemption becomes a hole shaped exactly like the thing it
    was carved around.
    """
    tree = ast.parse((PACKAGE / "sinks.py").read_text(encoding="utf-8"))
    # `from .client import X` parses as module "client" at level 1, and
    # `from gate_agent.client import X` as module "gate_agent.client" at level 0.
    # Both are the same import and both must be caught; keying on the written
    # text would catch one of them, which is the half that gets used.
    modules = {
        ("." * node.level) + (node.module or "")
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    }
    names = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }
    forbidden = {".client", "gate_agent.client"}
    assert not (modules & forbidden), (
        f"sinks.py imports the target client: {sorted(modules & forbidden)}"
    )
    assert "Target" not in names, (
        "sinks.py imports the Target type, so it can see a target's URL and token"
    )
    assert "ReadOnlyClient" not in names, "sinks.py imports the target client by name"
    # The control: the sweep sees the imports this module really has, and it can
    # see one of the forbidden shapes when it is there.
    assert any(name.endswith("SinkConfig") for name in names), sorted(names)
    planted = ast.parse("from .client import ReadOnlyClient\nfrom gate_agent.client import x\n")
    planted_modules = {
        ("." * node.level) + (node.module or "")
        for node in ast.walk(planted)
        if isinstance(node, ast.ImportFrom)
    }
    assert planted_modules == forbidden


@pytest.fixture
def watched():
    """A monitor watching all three kinds of target, each recording what arrives."""
    foreign = ForeignLane()
    identity = FakeIdentityService()
    platform = FakePlatform(
        devices=[
            {
                "id": "device-1",
                "lane_id": "lane-1",
                "name": "entry",
                "created_at": "2026-08-30T13:00:00+00:00",
                "last_seen_at": "2026-08-30T13:59:00+00:00",
                "revoked_at": None,
            }
        ]
    )
    with serving(foreign_server(foreign)) as lane_url, serving(
        identity_server(identity)
    ) as identity_url, serving(platform_server(platform)) as platform_url:
        yield foreign, identity, platform, config_for(
            lane=lane_url, identity_service=identity_url, platform=platform_url
        )


def test_a_whole_poll_touches_every_target_with_nothing_but_gets(watched):
    """What the targets SAW. The question asked from their side, not the monitor's.

    A source sweep cannot see a client it does not recognise. This can: the
    monitor is run for real against three servers that record every request,
    including the ones they would refuse, and the set of methods must be exactly
    one.
    """
    foreign, identity, platform, config = watched
    monitor = monitor_for(config, [RecordingSink()])
    monitor.start()
    monitor.poll(force=True)

    seen = foreign.requests + identity.requests + platform.requests
    assert seen, "no target was touched at all, so this asserts nothing"
    assert {method for method, _ in seen} == {"GET"}, (
        f"the monitor used a method other than GET: {sorted(set(seen))}"
    )
    # And it really did read all three, so the set above is not one target's.
    assert {path for _, path in foreign.requests} >= {"/v1/lane", "/v1/lane/health"}
    assert {path for _, path in identity.requests} == {"/v1/health"}
    assert any(path.endswith("/devices") for _, path in platform.requests)


def test_the_recorder_would_see_a_non_get(watched):
    """The control for the test above, and it is not optional.

    A recorder that only ever records GETs would satisfy that assertion however
    the monitor behaved. So a POST is made directly, to the same lane, through
    the same handler, and it must appear.
    """
    foreign, _identity, _platform, config = watched
    import urllib.error
    import urllib.request

    url = config.targets[0].url
    try:
        urllib.request.urlopen(
            urllib.request.Request(f"{url}/v1/lane", data=b"{}", method="POST"), timeout=5
        )
    except urllib.error.HTTPError as exc:
        assert exc.code == 405
    assert ("POST", "/v1/lane") in foreign.requests


def test_our_lane_sees_nothing_but_gets_either():
    """The same question of OUR lane, which is the one the monitor could cheat on.

    A foreign lane could not be reached in-process even if this package wanted
    to. Ours could -- `lane_controller` is installed in this environment -- so the
    only reading of "no opening authority" that means anything is one asserted
    against our own lane, served over a socket, recording what arrives.
    """
    server = our_server()
    with serving(server) as url:
        monitor = monitor_for(config_for(lane=url), [RecordingSink()])
        monitor.start()
        monitor.poll(force=True)
    seen = server.requests.seen
    assert seen, "our lane was not touched at all"
    assert {method for method, _ in seen} == {"GET"}
    assert {path for _, path in seen} == {"/v1/lane", "/v1/lane/health"}
