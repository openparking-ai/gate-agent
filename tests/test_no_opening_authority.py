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

#: The modules allowed to build a request that is not a GET. **It was one for
#: six rounds and it is two now**, and the second one is the whole of round 7:
#:
#:   * `sinks.py` points AWAY from a lane -- a webhook is how a third party's
#:     paging system takes the seat;
#:   * `act.py` is `POST /v1/lane/vend`, the agent asking a lane to open a
#:     barrier, which is the boundary every outside reviewer of this project has
#:     named.
#:
#: The old guarantee -- "nothing in this package can build a non-GET at a lane"
#: -- is GONE, and it is replaced rather than weakened. What replaces it is in
#: `test_the_act_modules_exemption_is_bounded` below, one property at a time:
#: one module, one method, one path, and no client at all without an act token.
MAY_POST = {"sinks.py", "act.py"}


def _is_request(node) -> bool:
    """Whether a call builds a `Request`, spelt either of the two ways.

    `urllib.request.Request(...)` is an `ast.Attribute`; `from urllib.request
    import Request` then `Request(...)` is an `ast.Name`, and the sweep used to
    see only the first -- so the second would have been invisible to it, and its
    own control could not tell "no non-GET" from "no match".
    """
    if not isinstance(node, ast.Call):
        return False
    if isinstance(node.func, ast.Attribute):
        return node.func.attr == "Request"
    return isinstance(node.func, ast.Name) and node.func.id == "Request"


def _requests_in(path: Path) -> list[ast.Call]:
    """Every `Request(...)` construction in one module, however it is spelt."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return [node for node in ast.walk(tree) if _is_request(node)]


def _method_of(call: ast.Call) -> str | None:
    for keyword in call.keywords:
        if keyword.arg == "method" and isinstance(keyword.value, ast.Constant):
            return keyword.value.value
    return None


def _has_body(call: ast.Call) -> bool:
    return any(keyword.arg == "data" for keyword in call.keywords)


def test_no_module_that_reads_a_target_can_build_anything_but_a_get():
    """Every request this package makes, outside the two named modules, is a GET."""
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
    planted = ast.parse(
        'urllib.request.Request(u, data=b"{}", method="POST")\n'
        # The other spelling, which the sweep could not see: a module that did
        # `from urllib.request import Request` would have passed it silently.
        'Request(u, data=b"{}", method="PUT")\n'
    )
    calls = [node for node in ast.walk(planted) if _is_request(node)]
    assert len(calls) == 2
    assert {_method_of(call) for call in calls} == {"POST", "PUT"}
    assert all(_has_body(call) for call in calls)


def test_the_act_modules_exemption_is_bounded():
    """THE REPLACEMENT for "nothing here can build a non-GET at a lane".

    That guarantee held for six rounds and this round ends it: the agent commands
    a vend. A mechanism change re-proves what the old mechanism guaranteed, one
    by one, so here is what now stands in its place -- read out of `act.py`'s own
    source, because a route that exists and is never called is invisible to
    behaviour.

      1. **ONE request.** Exactly one `Request(...)` is constructed in that
         module. A second is a second thing this exemption covers.
      2. **ONE method**, and it is `POST`.
      3. **ONE path**, and it is a module constant rather than a parameter: a
         path a caller could choose is a client that can reach any route on a
         lane.
      4. **The body is a body**, not a URL: nothing about the vend travels where
         a log or a `Referer` would carry it.
    """
    tree = ast.parse((PACKAGE / "act.py").read_text(encoding="utf-8"))
    calls = [node for node in ast.walk(tree) if _is_request(node)]
    assert len(calls) == 1, f"act.py builds {len(calls)} requests; the exemption covers one"
    assert _method_of(calls[0]) == "POST"
    assert _has_body(calls[0])

    # The URL is built from the module's own constant and the base URL, and from
    # nothing a caller passed. Read as SOURCE: a path that arrived as an
    # argument would be invisible to any test that only drives the client.
    url = calls[0].args[0]
    assert isinstance(url, ast.JoinedStr), "the vend URL is not an f-string of known parts"
    names = {
        node.value.attr
        for node in ast.walk(url)
        if isinstance(node, ast.FormattedValue)
        and isinstance(node.value, ast.Attribute)
    } | {
        node.value.id
        for node in ast.walk(url)
        if isinstance(node, ast.FormattedValue) and isinstance(node.value, ast.Name)
    }
    assert names == {"base_url", "VEND_PATH"}, names

    from gate_agent.act import VEND_PATH

    assert VEND_PATH == "/v1/lane/vend"

    # THE CONTROL for the sweep above: the same helpers see a second request,
    # another method and a path that came from an argument.
    planted = ast.parse(
        'urllib.request.Request(f"{base}{path}", data=b"{}", method="PUT")\n'
        'Request(url, method="DELETE")\n'
    )
    planted_calls = [node for node in ast.walk(planted) if _is_request(node)]
    assert len(planted_calls) == 2
    assert {_method_of(one) for one in planted_calls} == {"PUT", "DELETE"}


def test_an_act_client_cannot_exist_without_an_act_token():
    """A lane with no `act_token_file` has NO CLIENT, not a refused one.

    The difference is a round trip. A client built without a credential would be
    a vend attempted, refused by the lane, and reported to a person -- with a
    driver waiting through all of it for an answer nobody could have given.
    """
    import pytest as _pytest

    from gate_agent.act import LaneActClient

    for absent in (None, ""):
        with _pytest.raises(ValueError, match="act token"):
            LaneActClient("http://127.0.0.1:8090", absent, 5.0)
    # THE CONTROL: with one, it builds.
    assert LaneActClient("http://127.0.0.1:8090", "a-token", 5.0).act_token == "a-token"


def test_the_act_client_does_not_follow_a_redirect():
    """The request it would follow one on is the one carrying the credential
    that OPENS A BARRIER.

    The read client's own sweep already requires every opener in this package to
    come from `redirects.py`; this asserts it of the module where the stakes are
    a whole category higher, by name.
    """
    tree = ast.parse((PACKAGE / "act.py").read_text(encoding="utf-8"))
    opened = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert "open_url" in opened, "act.py does not open its request through redirects.open_url"
    assert "urlopen" not in opened and "build_opener" not in opened
    from_redirects = _names_from_redirects(tree)
    assert "open_url" in from_redirects, from_redirects


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


# ---------------------------------------------------------------------------
# THE CAPTURE PROCESS, ASKED THE SAME THREE QUESTIONS
# ---------------------------------------------------------------------------


#: The one module allowed to BUILD an opener, and it is the one that refuses to
#: follow anything. Named here, once, so a second is a change to this list.
#:
#: This exists because the camera made a second opener necessary: a camera
#: answers `401` with a challenge and expects the credential on the retry, which
#: needs authentication handlers. An opener built anywhere else would be an
#: opener with urllib's DEFAULT redirect handler in it -- and the retry is
#: exactly the request that carries `Authorization`, so a `Location` on it hands
#: a site's camera password to whichever host the camera names.
MAY_BUILD_AN_OPENER = {"redirects.py"}


def test_nothing_outside_the_redirect_module_opens_a_url_its_own_way():
    """Every opener in this package comes from `redirects.build_opener`.

    "Nothing is followed" is not a property of one opener. It is a property of
    the only function that makes them, and this is what keeps that true.
    """
    offenders = []
    swept = 0
    for path in SOURCES:
        if path.name in MAY_BUILD_AN_OPENER:
            continue
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        ours = _names_from_redirects(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            attribute = isinstance(node.func, ast.Attribute)
            name = node.func.attr if attribute else getattr(node.func, "id", None)
            if name not in ("urlopen", "build_opener"):
                continue
            swept += 1
            # A bare name is sanctioned only when THIS module imported it from
            # `.redirects`. An attribute spelling -- `urllib.request.urlopen` --
            # is never sanctioned, whichever module it is in.
            if attribute or name not in ours:
                offenders.append(f"{path.name}: {name}(...)")
    assert not offenders, (
        f"an opener is built outside redirects.py: {offenders}. That opener follows redirects, "
        "and the request it would follow one on is the one carrying a credential."
    )
    assert swept, "the sweep found no opener calls at all, so it is not looking at the right thing"


def _names_from_redirects(tree) -> set:
    """What this module imported from `redirects`, so a bare name can be placed."""
    return {
        alias.asname or alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and (node.module or "").endswith("redirects")
        for alias in node.names
    }


def test_that_sweep_sees_a_planted_opener():
    """The control, run against source known to contain both spellings.

    Parsed rather than written to disk, and through the same placement helper
    the sweep uses -- a second copy of the logic that happened to agree would
    prove nothing.
    """
    planted = ast.parse(
        "from .redirects import build_opener\n"
        "urllib.request.urlopen(u)\n"
        "urllib.request.build_opener(handler)\n"
        "build_opener(handler)\n"
    )
    ours = _names_from_redirects(planted)
    assert ours == {"build_opener"}
    caught, allowed = [], []
    for node in ast.walk(planted):
        if not isinstance(node, ast.Call):
            continue
        attribute = isinstance(node.func, ast.Attribute)
        name = node.func.attr if attribute else getattr(node.func, "id", None)
        if name not in ("urlopen", "build_opener"):
            continue
        (caught if attribute or name not in ours else allowed).append(name)
    assert caught == ["urlopen", "build_opener"], caught
    assert allowed == ["build_opener"], allowed


def test_a_whole_capture_run_touches_the_camera_and_the_lane_with_nothing_but_gets(tmp_path):
    """What the CAMERA and the LANE saw, asked from their side.

    The source sweep above cannot see a client it does not recognise. This can:
    a real capture process is run against two servers that record every request,
    including the ones they would refuse, and the set of methods must be exactly
    one. The lane in particular -- this process is a consumer of that contract
    and the lane's vend path is the boundary every outside reviewer named.
    """
    from cameras import FakeCamera, camera_server, jpeg
    from conftest import camera_config, capture_config_for, capture_for

    directory = tmp_path / "store"
    directory.mkdir()
    lane = ForeignLane()
    lane.window = 64
    camera = FakeCamera(body=jpeg(b"one"))
    with serving(camera_server(camera)) as camera_url, serving(foreign_server(lane)) as lane_url:
        config = capture_config_for(
            directory=directory,
            cameras=[camera_config("front", f"{camera_url}/snapshot", tmp_path)],
            lane=lane_url,
        )
        process = capture_for(config)
        process.start()
        lane.record("vended", "2026-08-30T14:00:00+00:00")
        process.poll(force=True)

    assert camera.requests and lane.requests, "nothing was touched, so this asserts nothing"
    assert {method for method, _ in camera.requests} == {"GET"}
    assert {method for method, _ in lane.requests} == {"GET"}
    assert {path for _, path in lane.requests} == {"/v1/lane", "/v1/lane/events"}, (
        "the capture process read a lane route it has no business on"
    )


def test_the_recorders_in_that_run_would_see_a_non_get(tmp_path):
    """The control for the test above, and it is not optional."""
    import urllib.error
    import urllib.request

    from cameras import FakeCamera, camera_server

    camera = FakeCamera(username=None)
    lane = ForeignLane()
    with serving(camera_server(camera)) as camera_url, serving(foreign_server(lane)) as lane_url:
        both = ((camera_url, camera, "/snapshot"), (lane_url, lane, "/v1/lane"))
        for url, recorder, path in both:
            try:
                urllib.request.urlopen(
                    urllib.request.Request(f"{url}{path}", data=b"{}", method="POST"), timeout=5
                )
            except urllib.error.HTTPError as exc:
                assert exc.code == 405
            assert ("POST", path) in recorder.requests


def test_nothing_in_the_capture_process_imports_the_lane_controller():
    """The seat again, from the second process in this package.

    Already covered by the package-wide sweep above, and asserted separately
    because it is the property this round could most easily have broken: the
    quickest way to learn that a lane vended is to import the lane.
    """
    import gate_agent.capture as capture_module
    import gate_agent.store as store_module

    for module in (capture_module, store_module):
        tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
        assert not [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("lane_")
        ], f"{module.__name__} imports the lane it is a client of"
