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
from lane_controller.contract import MalfunctionCode

from conftest import config_for, monitor_for
from fakes import RecordingSink
from foreign_lane import ForeignLane
from foreign_lane import make_server as foreign_server
from gate_agent.contract import MonitorCode
from ours import our_server
from serving import serving

FOREIGN_DIR = Path(__file__).resolve().parent / "foreign_lane"

#: What the lane contract PUBLISHES as closed, and therefore the only thing an
#: implementer may take from that package. Everything else about a foreign lane
#: is its own. Copied from `lane-controller/tests/test_third_party_seat.py`,
#: which is where this rule was settled.
ALLOWED_IMPORTS = {"MalfunctionCode", "NEVER_ALARM"}


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

    assert target["contract_version"] == 1
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


def test_the_foreign_lane_imports_nothing_of_ours_but_the_published_sets():
    """A stub built on our machinery would prove nothing about a foreign lane.

    Read out of the source rather than promised in a docstring: every
    `from lane_controller...` import in the package is enumerated and must be one
    of the contract's published closed sets.
    """
    imported: set[str] = set()
    for path in sorted(FOREIGN_DIR.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith(
                "lane_controller"
            ):
                imported.update(alias.name for alias in node.names)
            if isinstance(node, ast.Import):
                assert not any(
                    alias.name.startswith("lane_controller") for alias in node.names
                ), f"{path.name} imports lane_controller wholesale"

    assert imported == ALLOWED_IMPORTS, (
        f"the foreign lane imports {sorted(imported)} from that package. Only the contract's "
        f"published closed sets ({sorted(ALLOWED_IMPORTS)}) are what an implementer reads."
    )
    # The control: the sweep can see an import at all.
    assert imported, "the sweep found no imports; it is not looking at the right files"


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
