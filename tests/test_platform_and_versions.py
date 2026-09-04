"""`lane_gone_quiet` off the platform, and what happens to a version nobody knows.

A lane that has gone quiet is quiet. The fault is invisible from the lane's own
end and the platform is the only thing that can see it — it writes
`lane_devices.last_seen_at` on every authenticated lane request. Until this
round it published that timestamp on no route; now it does, and this is the
read.

The threshold is a per-site setting and an assumption. Both sides of it are
exercised: a threshold with fixture inputs on only one side of it is not
exercised at all.
"""

from __future__ import annotations

import pytest

from conftest import FakeClock, FakeUtc, config_for, monitor_for
from fakes import FakeIdentityService, FakePlatform, RecordingSink, identity_server, platform_server
from foreign_lane import ForeignLane
from foreign_lane import make_server as foreign_server
from gate_agent.contract import MonitorCode, TargetKind
from gate_agent.monitor import UnsupportedContract
from serving import serving

NOW = "2026-08-30T14:00:00+00:00"


def device(**over) -> dict:
    fields = {
        "id": "device-1",
        "lane_id": "lane-1",
        "name": "entry",
        "created_at": "2026-08-30T13:00:00+00:00",
        "last_seen_at": "2026-08-30T13:59:00+00:00",
        "revoked_at": None,
    }
    fields.update(over)
    return fields


def watch(devices, quiet_seconds=900.0, token="operator-token"):
    platform = FakePlatform(devices=devices)
    sink = RecordingSink()
    with serving(platform_server(platform)) as url:
        monitor = monitor_for(
            config_for(platform=url, lane_quiet_seconds=quiet_seconds, platform_token=token),
            [sink],
            clock=FakeClock(),
            now=FakeUtc(NOW),
        )
        monitor.start()
    return monitor, sink, platform


def states(monitor) -> dict[tuple[str, str], str]:
    return {
        (entry["code"], entry["subject"]): entry["state"]
        for entry in monitor.health().to_dict()["codes"]
    }


# ---------------------------------------------------------------------------
# Both sides of the threshold
# ---------------------------------------------------------------------------


def test_a_device_seen_recently_is_ok_and_one_that_has_not_been_is_active():
    """The setting, exercised either side of itself.

    One minute ago against a fifteen-minute threshold, and two hours ago against
    the same threshold. A fixture that only ever landed on one side of a
    threshold would leave the branch it guards unexercised while reading as
    coverage.
    """
    recent, _sink, _platform = watch([device(last_seen_at="2026-08-30T13:59:00+00:00")])
    assert states(recent)[(MonitorCode.LANE_GONE_QUIET.value, "device-1")] == "ok"

    quiet, sink, _platform = watch([device(last_seen_at="2026-08-30T12:00:00+00:00")])
    assert states(quiet)[(MonitorCode.LANE_GONE_QUIET.value, "device-1")] == "active"
    assert (MonitorCode.LANE_GONE_QUIET.value, "raised") in sink.codes


def test_the_threshold_is_the_sites_and_moving_it_moves_the_answer():
    """The control for the test above: it is a SETTING, not a constant.

    The same device, the same timestamp, two thresholds, two answers. Without
    this the pair above could pass against a number hard-coded in the monitor.
    """
    seen = device(last_seen_at="2026-08-30T13:50:00+00:00")  # ten minutes ago
    lenient, _s, _p = watch([seen], quiet_seconds=900.0)
    strict, _s2, _p2 = watch([seen], quiet_seconds=60.0)

    assert states(lenient)[(MonitorCode.LANE_GONE_QUIET.value, "device-1")] == "ok"
    assert states(strict)[(MonitorCode.LANE_GONE_QUIET.value, "device-1")] == "active"


def test_a_device_never_seen_is_measured_from_when_it_was_created():
    """A credential issued a week ago and never used is a lane that never came up.

    Treating a null `last_seen_at` as unmeasurable would hide exactly the case
    worth knowing at an installation, and it is the reassuring direction.
    """
    monitor, _sink, _platform = watch(
        [device(last_seen_at=None, created_at="2026-08-30T10:00:00+00:00")]
    )
    assert states(monitor)[(MonitorCode.LANE_GONE_QUIET.value, "device-1")] == "active"

    # The control: a device created moments ago and never seen is not a fault.
    fresh, _s, _p = watch([device(last_seen_at=None, created_at="2026-08-30T13:59:00+00:00")])
    assert states(fresh)[(MonitorCode.LANE_GONE_QUIET.value, "device-1")] == "ok"


def test_a_revoked_device_is_not_a_fault():
    """A credential deliberately ended and then not seen is not a malfunction.

    Paging on it teaches whoever reads these messages to ignore them, which is
    how a monitor becomes worse than none.
    """
    monitor, sink, _platform = watch(
        [device(last_seen_at="2026-08-30T01:00:00+00:00", revoked_at="2026-08-30T02:00:00+00:00")]
    )
    assert (MonitorCode.LANE_GONE_QUIET.value, "device-1") not in states(monitor)
    assert sink.codes == []


def test_a_device_that_leaves_the_listing_stops_being_measured():
    """It has not recovered. It has stopped being measured, and that is said once."""
    platform = FakePlatform(devices=[device(last_seen_at="2026-08-30T12:00:00+00:00")])
    sink = RecordingSink()
    clock = FakeClock()
    with serving(platform_server(platform)) as url:
        monitor = monitor_for(
            config_for(platform=url, poll_seconds=1.0), [sink], clock=clock, now=FakeUtc(NOW)
        )
        monitor.start()
        assert (MonitorCode.LANE_GONE_QUIET.value, "raised") in sink.codes

        platform.devices = []
        clock.advance(60)
        monitor.poll()

    assert (MonitorCode.LANE_GONE_QUIET.value, "no_longer_measured") in sink.codes
    assert states(monitor)[(MonitorCode.LANE_GONE_QUIET.value, "device-1")] == "unknown"


def test_an_unparseable_timestamp_is_unknown_and_never_ok():
    """Zero would read as "seen just now", which is the reassuring direction."""
    monitor, _sink, _platform = watch([device(last_seen_at="yesterday afternoon")])
    assert states(monitor)[(MonitorCode.LANE_GONE_QUIET.value, "device-1")] == "unknown"


def test_the_monitor_presents_its_operator_credential():
    """And the platform would notice if it did not.

    The tenant comes from that token: a monitor that never presented it would
    read nothing, and reading nothing is indistinguishable from a garage with no
    devices — which is indistinguishable from no faults.
    """
    monitor, _sink, platform = watch([device()])
    assert platform.authorizations
    assert set(platform.authorizations) == {"Bearer operator-token"}

    # The control: with the wrong credential the platform refuses, and the
    # monitor says so rather than reporting a garage with no devices.
    #
    # It says REFUSED, not unreachable. A 401 is an answer, and it names a
    # different machine: the platform is running perfectly and the credential in
    # a file beside this process is wrong. The status travels with it so the
    # human reading the message can tell that from a platform that is down.
    refused, sink, _p = watch([device()], token="not-the-token")
    assert states(refused)[(MonitorCode.PLATFORM_REFUSED_US.value, "platform")] == "active"
    assert states(refused)[(MonitorCode.PLATFORM_UNREACHABLE.value, "platform")] == "ok"
    assert (MonitorCode.PLATFORM_REFUSED_US.value, "raised") in sink.codes
    assert (MonitorCode.PLATFORM_UNREACHABLE.value, "raised") not in sink.codes
    status = {
        entry["code"]: entry["status"] for entry in refused.health().to_dict()["codes"]
    }
    assert status[MonitorCode.PLATFORM_REFUSED_US.value] == 401
    assert [one["status"] for one in sink.payloads if one["code"].endswith("refused_us")] == [401]


def test_the_platform_publishes_no_version_so_that_code_stays_unknown():
    """`ok` there would be a claim about a measurement nobody made.

    The platform's operator surface carries no `contract_version`. Nothing was
    checked, so `target_contract_unsupported` for that target answers `unknown`
    and says so, rather than reporting a version agreement that was never tested.
    """
    monitor, _sink, _platform = watch([device()])
    assert (
        states(monitor)[(MonitorCode.TARGET_CONTRACT_UNSUPPORTED.value, "platform")] == "unknown"
    )


# ---------------------------------------------------------------------------
# A version this build does not know
# ---------------------------------------------------------------------------


def test_a_lane_on_a_version_this_build_cannot_read_is_refused_at_startup(monkeypatch):
    """Half-understanding a payload about a lane is worse than admitting it.

    The lane contract's own rule -- an unrecognised version is refused, not
    partially read -- applied by the consumer it was written for.
    """
    monkeypatch.setenv("BREAK_FOREIGN_LANE", "future_version")
    lane = ForeignLane()
    with serving(foreign_server(lane)) as url:
        monitor = monitor_for(config_for(lane=url), [RecordingSink()])
        with pytest.raises(UnsupportedContract) as refused:
            monitor.start()
    assert "99" in str(refused.value)
    assert "lane" in str(refused.value)


def test_a_lane_that_is_merely_down_at_startup_is_not_refused():
    """The control for the test above, and it is the more important half.

    A monitor that refused to start while a lane was rebooting would be absent
    at exactly the moment it is wanted. That is a malfunction, not a
    misconfiguration, and it is reported as one.
    """
    sink = RecordingSink()
    monitor = monitor_for(config_for(lane="http://127.0.0.1:1"), [sink])
    monitor.start()  # must not raise
    assert states(monitor)[(MonitorCode.LANE_UNREACHABLE.value, "lane")] == "active"


def test_a_version_that_changes_under_a_running_monitor_stops_the_passthrough(monkeypatch):
    """Its codes stop being published rather than being published as understood.

    A state whose meaning may have changed is not a state, and passing it through
    anyway is exactly the half-read the contract forbids.
    """
    lane = ForeignLane()
    lane.states["outbox_depth_growing"] = "active"
    lane.sources["outbox_depth_growing"] = "measured"
    sink = RecordingSink()
    clock = FakeClock()
    with serving(foreign_server(lane)) as url:
        monitor = monitor_for(config_for(lane=url, poll_seconds=1.0), [sink], clock=clock)
        monitor.start()
        target = next(one for one in monitor.health().to_dict()["targets"] if one["name"] == "lane")
        assert target["codes"], "the passthrough was empty before the break, so this proves nothing"

        monkeypatch.setenv("BREAK_FOREIGN_LANE", "future_version")
        clock.advance(60)
        monitor.poll()

    assert states(monitor)[(MonitorCode.TARGET_CONTRACT_UNSUPPORTED.value, "lane")] == "active"
    assert (MonitorCode.TARGET_CONTRACT_UNSUPPORTED.value, "raised") in sink.codes
    target = next(one for one in monitor.health().to_dict()["targets"] if one["name"] == "lane")
    assert target["codes"] == []
    assert ("outbox_depth_growing", "no_longer_measured") in sink.codes


def test_an_identity_service_on_a_schema_this_build_knows_is_read():
    """The ordinary case, so the version machinery is not only exercised broken."""
    identity = FakeIdentityService(schema_version=1)
    with serving(identity_server(identity)) as url:
        monitor = monitor_for(config_for(identity_service=url), [RecordingSink()])
        monitor.start()
    assert (
        states(monitor)[(MonitorCode.IDENTITY_SERVICE_UNREACHABLE.value, "identity_service")]
        == "ok"
    )
    assert (
        states(monitor)[
            (MonitorCode.TARGET_CONTRACT_UNSUPPORTED.value, "identity_service")
        ]
        == "ok"
    )


def test_an_identity_service_on_a_schema_this_build_does_not_know_is_not_read():
    identity = FakeIdentityService(schema_version=99)
    sink = RecordingSink()
    with serving(identity_server(identity)) as url:
        monitor = monitor_for(config_for(identity_service=url), [sink])
        monitor.start()
    assert (
        states(monitor)[
            (MonitorCode.TARGET_CONTRACT_UNSUPPORTED.value, "identity_service")
        ]
        == "active"
    )


# ---------------------------------------------------------------------------
# ONE COPY OF THE LANE VERSION SET
# ---------------------------------------------------------------------------


def test_the_lane_version_set_is_defined_exactly_once_in_the_package():
    """Three copies of one number is three places to forget, and it was forgotten.

    On 2026-08-31 the lane contract went to 2 while `agent.py`, `monitor.py` and
    `capture.py` each still held their own `(1,)`. The monitor and the whole
    capture process refused to start against the lane on `lane-controller` main
    and the agent read every lane as `lane_unavailable` -- and every one of those
    failures is in the REASSURING direction, because each says only that the
    lane cannot be read.

    So the set is DEFINED in `contract.py` and nowhere else. The three consumers
    import it, which is why the identity check below is the assertion: a second
    definition would be a different tuple object even if it held the same
    numbers.
    """
    import ast
    from pathlib import Path

    import gate_agent
    from gate_agent import agent as agent_module
    from gate_agent import capture as capture_module
    from gate_agent import contract as contract_module
    from gate_agent import monitor as monitor_module
    from gate_agent.contract import KNOWN_LANE_VERSIONS
    from gate_agent.monitor import KNOWN_VERSIONS

    package = Path(gate_agent.__file__).resolve().parent
    definitions = []
    for path in sorted(package.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            targets = (
                [node.target] if isinstance(node, ast.AnnAssign) else
                list(node.targets) if isinstance(node, ast.Assign) else []
            )
            for target in targets:
                if isinstance(target, ast.Name) and target.id == "KNOWN_LANE_VERSIONS":
                    definitions.append(path.name)
    assert definitions == ["contract.py"], (
        f"KNOWN_LANE_VERSIONS is assigned in {definitions}; it lives in contract.py alone"
    )

    # THE CONTROL for the sweep: it can see an assignment when there is one, in
    # both spellings, so the list above is a statement about the package rather
    # than about a walk that matches nothing.
    planted = ast.parse(
        "KNOWN_LANE_VERSIONS: tuple[int, ...] = (9,)\nKNOWN_LANE_VERSIONS = (9,)\n"
    )
    seen = [
        target.id
        for node in ast.walk(planted)
        for target in (
            [node.target] if isinstance(node, ast.AnnAssign)
            else list(node.targets) if isinstance(node, ast.Assign) else []
        )
        if isinstance(target, ast.Name)
    ]
    assert seen == ["KNOWN_LANE_VERSIONS", "KNOWN_LANE_VERSIONS"], seen

    # And every consumer is holding THAT object, not a copy that agrees today.
    for module in (agent_module, capture_module, monitor_module, contract_module):
        assert module.KNOWN_LANE_VERSIONS is KNOWN_LANE_VERSIONS, module.__name__
    assert KNOWN_VERSIONS[TargetKind.LANE] is KNOWN_LANE_VERSIONS
