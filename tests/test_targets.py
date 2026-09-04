"""The seat: the monitor reads OUR lane and a foreign one through the same code.

Round 2 built a lane contract a third party can implement, and proved it with one
consumer reading both. This is the other half of that proof, from the other side
of the wire: the consumer that contract was written for is this monitor, and if
it needed to know which lane it was talking to, the contract would be wrong.

**The lanes are parametrised, never tested separately.** A test written twice
would let the two drift and would prove nothing about the seat -- and the
property being measured is precisely that the monitor cannot tell them apart.

The two lanes are deliberately unlike each other. Ours has loops, an identifier
and codes it genuinely derives; the foreign one has none of that, answers
`unknown` for everything with `no_source` beside it, and falls back with a word
from its own vendor's vocabulary. Those are exactly the differences that would
force a special case if the monitor had baked our lane's shape into it.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from lane_controller import config as _lane_config
from lane_controller import contract as lane_contract
from lane_controller.contract import NEVER_ALARM, MalfunctionCode

from conftest import config_for, monitor_for
from fakes import RecordingSink
from foreign_lane import CONTRACT_VERSION as FOREIGN_CONTRACT_VERSION
from foreign_lane import MALFUNCTION_CODES, NEVER_ALARM_CODES, ForeignLane
from foreign_lane import make_server as foreign_server
from gate_agent.client import DEFAULT_TIMEOUT
from gate_agent.contract import KNOWN_LANE_VERSIONS, MonitorCode
from ours import our_server
from serving import serving

FOREIGN_DIR = Path(__file__).resolve().parent / "foreign_lane"



@pytest.fixture(params=["ours", "foreign"])
def lane(request):
    """The same two-line setup for both lanes, and nothing else differs.

    Both are reached over a socket, by URL. A test that read ours by calling
    `LaneService` directly and the foreign one over HTTP would be comparing two
    different things.
    """
    if request.param == "ours":
        server = our_server()
    else:
        server = foreign_server(ForeignLane())
    with serving(server) as url:
        yield request.param, url


def test_the_monitor_reads_either_lane_and_publishes_what_it_said(lane):
    """Both lanes, one poll, and the whole code table arrives on our surface."""
    _which, url = lane
    sink = RecordingSink()
    monitor = monitor_for(config_for(lane=url), [sink])
    monitor.start()

    health = monitor.health().to_dict()
    target = next(one for one in health["targets"] if one["name"] == "lane")

    # PRODUCED, not typed. `1` stood here, and it went stale the moment the
    # lane's contract went to 2 -- a number in a test is measurement too. Ours
    # comes from the installed package; the foreign stub publishes its own
    # constant, copied from the document the way its closed sets are.
    expected = (
        lane_contract.CONTRACT_VERSION if _which == "ours" else FOREIGN_CONTRACT_VERSION
    )
    assert target["contract_version"] == expected
    # And both really are on a version this build reads, or the poll above would
    # have been a refusal rather than a read.
    assert expected in KNOWN_LANE_VERSIONS
    assert target["polled_at"] is not None
    assert {entry["code"] for entry in target["codes"]} == {
        code.value for code in MalfunctionCode
    }
    # Every entry arrives with the state and the source the LANE gave. Nothing
    # here re-derives either, so there is nothing to compare against except the
    # payload -- which is the point.
    for entry in target["codes"]:
        assert set(entry) >= {"code", "state", "source", "never_alarm"}


def test_the_monitor_never_reads_a_lanes_unknown_as_ok(lane):
    """`unknown` is not `ok`, on either lane, and it is never paged on.

    The foreign lane answers `unknown` for every code, which is the truth for a
    lane with none of this instrumentation. A monitor that read those as healthy
    would report a clean lane it has measured nothing about; one that paged on
    them would page continuously about a lane that is working.
    """
    which, url = lane
    sink = RecordingSink()
    monitor = monitor_for(config_for(lane=url), [sink])
    monitor.start()

    target = next(one for one in monitor.health().to_dict()["targets"] if one["name"] == "lane")
    unknown = [entry for entry in target["codes"] if entry["state"] == "unknown"]
    assert unknown, "neither lane published an unknown code, so this asserts nothing"
    # None of them was mapped to `ok` on the way through.
    assert all(entry["state"] == "unknown" for entry in unknown)
    # And none of them produced a message.
    for code, _transition in sink.codes:
        assert code not in {entry["code"] for entry in unknown}
    if which == "foreign":
        # The strong form, available only on the lane that measures nothing: the
        # monitor said nothing at all about that lane's codes.
        assert not [one for one in sink.codes if one[0] in {c.value for c in MalfunctionCode}]


def test_the_monitor_has_no_branch_on_which_lane_it_is_reading():
    """The control for the parametrised tests: read out of the source.

    "The same code reads both" is a claim about this package, and running two
    lanes through it does not establish that no branch exists -- only that none
    fired on these two payloads. So the source is walked for a comparison against
    anything that identifies a lane, which is what such a branch would have to
    be.
    """
    import gate_agent

    package = Path(gate_agent.__file__).resolve().parent
    smells = ("lane_id ==", "vendor", "lane-1", "127.0.0.1:8090")
    for path in sorted(package.glob("*.py")):
        text = path.read_text(encoding="utf-8")
        for smell in smells:
            assert smell not in text, f"{path.name} branches on which lane it is reading: {smell!r}"
    # The control: this sweep can see a string in these files at all.
    assert any(
        "lane_unreachable" in path.read_text(encoding="utf-8") for path in package.glob("*.py")
    )


def test_the_foreign_lane_imports_nothing_of_ours_at_all():
    """A stub built on our machinery would prove nothing about a foreign lane.

    It used to import two things -- the malfunction codes and the never-alarm
    set -- because those were the two the lane contract WITHHELD. So the one
    artefact that exists to show a stranger can take this seat was written from
    the document plus our Python package, for exactly the parts a stranger could
    not have got, while three documents said "written from the document".

    The document publishes those sets now. This requires the import to be gone:
    read out of the source, not promised in a docstring.
    """
    offenders = []
    for path in sorted(FOREIGN_DIR.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith(
                "lane_controller"
            ):
                offenders.append(f"{path.name}: from {node.module} import ...")
            if isinstance(node, ast.Import):
                offenders.extend(
                    f"{path.name}: import {alias.name}"
                    for alias in node.names
                    if alias.name.startswith("lane_controller")
                )

    assert not offenders, (
        f"the foreign lane imports our package: {offenders}. Its code list is a literal copied "
        "from `lane-controller/docs/CONTRACT.md`; there is nothing left to import."
    )
    # THE CONTROL: the same walk over a file that DOES import that package finds
    # it. `tests/ours.py` serves a real lane and imports it deliberately, so an
    # assertion of absence above is about these files rather than about a walk
    # that sees nothing.
    control = ast.parse((FOREIGN_DIR.parent / "ours.py").read_text(encoding="utf-8"))
    assert [
        node
        for node in ast.walk(control)
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("lane_controller")
    ], "the control file no longer imports lane_controller; this sweep proves nothing"


def test_the_foreign_lanes_copied_code_list_is_the_one_the_contract_defines():
    """The other half, and it is the half only THIS suite can run.

    A literal copied from a document is a second copy of a closed set, and a
    second copy drifts. The document's own suite holds it to the enum on that
    side of the wire; this holds it on this side, in the one place where the
    real package is installed -- `lane_controller` is a TEST dependency here, and
    `tests/ours.py` serves a real lane built from it.

    Ordered comparison, because the copy is a copy: a set that arrives shuffled
    is a copy somebody retyped rather than took.
    """
    assert list(MALFUNCTION_CODES) == [code.value for code in MalfunctionCode], (
        "the foreign lane's copy of the code list has drifted from the contract it was copied "
        "from. It comes from `lane-controller/docs/CONTRACT.md`, under The closed sets."
    )
    assert list(NEVER_ALARM_CODES) == [code.value for code in NEVER_ALARM]
    # The control: neither comparison is two empty lists agreeing.
    assert MALFUNCTION_CODES and NEVER_ALARM_CODES


def test_a_lane_that_is_not_running_is_not_a_lane_that_is_fine():
    """A dead lane is `lane_unreachable`, active, and it is a message.

    Stated because the alternative -- a client that swallows a connection failure
    and reports nothing -- would make every assertion in this file pass against a
    lane that is switched off. This is Gokhan's "no connection", and it is the
    monitor's own measurement because nothing else can make it: a thing that is
    down cannot report that it is down.
    """
    sink = RecordingSink()
    # Port 1 on loopback: nothing listens, and the refusal is immediate.
    monitor = monitor_for(config_for(lane="http://127.0.0.1:1"), [sink])
    monitor.start()

    states = {
        (entry["code"], entry["state"]) for entry in monitor.health().to_dict()["codes"]
    }
    assert (MonitorCode.LANE_UNREACHABLE.value, "active") in states
    assert (MonitorCode.LANE_UNREACHABLE.value, "raised") in sink.codes

    # And it is not reported as a lane with nothing to say: the target carries no
    # codes and no `polled_at`, rather than a stale copy of its last health.
    target = next(one for one in monitor.health().to_dict()["targets"] if one["name"] == "lane")
    assert target["codes"] == []
    assert target["polled_at"] is None


# ---------------------------------------------------------------------------
# THE TWO TIMEOUTS, AND THE SEAM BETWEEN THEM
#
# A lane's health route may itself read a third machine -- the identification
# service -- and it bounds that read at its own `[lane] identity_health_timeout_s`.
# This monitor waits `client.DEFAULT_TIMEOUT` for the lane. If the two ever
# cross, a lane that is UP, serving, and correctly answering `unknown` about a
# hung identification service is published by its monitor as a DEAD LANE, and
# every real signal that lane publishes is retired at the same moment. A slow
# third machine becomes a fault attributed to the wrong one.
#
# The two numbers live in two repositories, so the relationship is stated in both
# contracts as an assumption -- and MEASURED here, against a real lane reading a
# socket that never answers. A measurement rather than a comparison of two
# constants: the constant on the lane's side is whatever version of that package
# is installed, and what matters is what the route actually does.
# ---------------------------------------------------------------------------


def _a_lane_that_takes(delay: float):
    """OUR real lane, on a real socket, answering after `delay` seconds.

    The delay is on the handler, so it is the WHOLE answer that is late -- which
    is what a lane's health route reading a third machine looks like from out
    here. Nothing about the lane's payload changes.
    """
    import time

    server = our_server()
    original = server.RequestHandlerClass.handle_one_request

    def slow(self):
        time.sleep(delay)
        original(self)

    server.RequestHandlerClass = type(
        "_SlowHandler", (server.RequestHandlerClass,), {"handle_one_request": slow}
    )
    return server


#: What the INSTALLED lane-controller bounds its identification-service health
#: read at. Read as an ATTRIBUTE, with no fallback: a `getattr(..., 0.0)` stood
#: here while the pin named a commit from before that bound existed, and `0.0`
#: is a number every timeout in this package exceeds -- so the assertion below
#: passed on a constant that was not there, which is a check that cannot fail.
#: The pin now names a build that has it. An `AttributeError` here is the
#: correct outcome for a pin that goes backwards, and it is the loud one.
LANE_IDENTITY_HEALTH_BOUND = _lane_config.DEFAULT_IDENTITY_HEALTH_TIMEOUT_S


def test_the_monitors_timeout_exceeds_the_lanes_own_bound_on_a_third_machine():
    """Two numbers in two repositories, compared by value rather than remembered.

    A lane's health route may read the identification service on the request and
    bounds that read itself. If this monitor's patience is not comfortably
    greater, a lane that is UP, serving, and correctly answering `unknown` about
    a hung identification service is published as a DEAD lane -- and all 21 of
    its codes are retired at the same moment, including the one that would have
    named the true fault.
    """
    assert DEFAULT_TIMEOUT > LANE_IDENTITY_HEALTH_BOUND, (
        f"this monitor waits {DEFAULT_TIMEOUT}s and the lane it reads spends up to "
        f"{LANE_IDENTITY_HEALTH_BOUND}s on another machine before answering. The two have "
        "crossed: an up lane reads as a dead one."
    )


def test_a_lane_that_answers_slowly_is_not_a_lane_that_is_down():
    """The behaviour the number above exists for, measured on a real lane.

    Everything a lane spends answering is time this monitor is waiting, and a
    monitor that gave up first would publish `lane_unreachable` and retire every
    code that lane publishes. The delay here is well inside this monitor's
    patience and well outside anything a local route takes.
    """
    import time

    sink = RecordingSink()
    server = _a_lane_that_takes(0.6)
    with serving(server) as url:
        monitor = monitor_for(config_for(lane=url), [sink])
        started = time.monotonic()
        monitor.start()
        elapsed = time.monotonic() - started

    health = monitor.health().to_dict()
    states = {entry["code"]: entry["state"] for entry in health["codes"]}
    target = next(one for one in health["targets"] if one["name"] == "lane")

    assert elapsed > 0.6, "the lane answered instantly, so nothing was waited for"
    assert states[MonitorCode.LANE_UNREACHABLE.value] == "ok"
    assert (MonitorCode.LANE_UNREACHABLE.value, "raised") not in sink.codes
    assert {entry["code"] for entry in target["codes"]} == {
        code.value for code in MalfunctionCode
    }, "the lane's codes were retired, so every real signal it publishes stopped"


def test_a_lane_slower_than_this_targets_timeout_is_unreachable():
    """THE CONTROL, and it is what makes the test above a measurement.

    A client that never timed out at all would satisfy every assertion there.
    The same lane, the same delay, and a timeout this site set below it: now the
    monitor gives up, says so, and retires what it can no longer see.
    """
    sink = RecordingSink()
    config = config_for(lane="http://127.0.0.1:1")
    server = _a_lane_that_takes(0.6)
    with serving(server) as url:
        from dataclasses import replace

        impatient = replace(
            config,
            targets=(replace(config.targets[0], url=url, timeout_seconds=0.2),),
        )
        monitor = monitor_for(impatient, [sink])
        monitor.start()

    states = {
        entry["code"]: entry["state"] for entry in monitor.health().to_dict()["codes"]
    }
    assert states[MonitorCode.LANE_UNREACHABLE.value] == "active"
    assert (MonitorCode.LANE_UNREACHABLE.value, "raised") in sink.codes
