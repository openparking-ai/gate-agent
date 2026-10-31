"""Where the records live, who else can read them, and who else may write them.

The record files hold the addresses a site pages and the numbers a person keyed.
Proven here, each with its control:

  * every file is `0600` and the directory `0700` -- tightened at start even
    when it already existed wider, and refused, in words, when it cannot be;
  * the monitor and the agent may share one directory: their files, heads and
    half-written leftovers are their own, and neither sweeps the other's;
  * a second process of the same kind on the same directory is refused, naming
    the lock -- even on the default directory;
  * an UNDECLARED directory does not stop a monitor or an agent starting: one
    warning says where the records are, and if even that cannot be used, the
    process runs with `record_store_unwritable` active and still pages.
"""

from __future__ import annotations

import logging
import os
import stat

import pytest

from conftest import (
    INTERCOM_ACCOUNT,
    FakeClock,
    agent_config_for,
    config_for,
    monitor_for,
    record_settings,
)
from fake_ua import FakeUa
from fakes import RecordingSink
from foreign_lane import ForeignLane
from foreign_lane import make_server as foreign_server
from gate_agent.config import AgentConfig, MonitorConfig, RecordSettings
from gate_agent.contract import AgentCode, MonitorCode
from gate_agent.records import (
    DEFAULT_RECORD_DIRECTORY,
    RecordQuery,
    RecordStore,
    RecordStoreInUse,
    RecordUnwritable,
)
from serving import serving

AT = "2026-10-30T21:00:00+00:00"


def store_of(directory, kind="m") -> RecordStore:
    return RecordStore(directory, kind=kind, time_key="at", lane_key="lane_id",
                       unfinished=lambda body: False)


def mode_of(path) -> int:
    return stat.S_IMODE(os.stat(path).st_mode)


@pytest.fixture
def lane():
    served = ForeignLane()
    with serving(foreign_server(served)) as url:
        yield served, url


def test_every_file_is_0600_and_the_directory_is_tightened_to_0700(tmp_path):
    directory = tmp_path / "records"
    directory.mkdir()
    directory.chmod(0o755)
    old = os.umask(0o022)
    try:
        store = store_of(directory)
        store.open()
        store.put("m-" + "1" * 32, {"at": AT, "lane_id": None})
        store.put("m-" + "1" * 32, {"at": AT, "lane_id": None, "again": True})
    finally:
        os.umask(old)
    assert mode_of(directory) == 0o700, oct(mode_of(directory))
    files = sorted(path.name for path in directory.iterdir())
    assert {"head.messages.json", "lock.messages", "m-" + "1" * 32 + ".json"} <= set(files)
    for name in files:
        assert mode_of(directory / name) == 0o600, (name, oct(mode_of(directory / name)))


def test_a_directory_that_cannot_be_made_private_is_refused_in_words(tmp_path, monkeypatch, lane):
    import gate_agent.records as records

    def refuse(path, mode):
        raise PermissionError(1, "Operation not permitted")

    directory = tmp_path / "records"
    directory.mkdir()
    directory.chmod(0o755)
    monkeypatch.setattr(records.os, "chmod", refuse)
    with pytest.raises(RecordUnwritable, match=r"could not be made private \(0700\)"):
        store_of(directory).open()
    # Through a monitor whose site DECLARED it: refused at start.
    _served, url = lane
    monitor = monitor_for(config_for(lane=url, records=record_settings(directory)),
                          [RecordingSink()])
    with pytest.raises(RecordUnwritable, match="could not be made private"):
        monitor.start()


def test_a_second_process_of_the_same_kind_is_refused_and_named(tmp_path, lane):
    first = store_of(tmp_path)
    first.open()
    with pytest.raises(RecordStoreInUse, match="another process holds .*lock.messages"):
        store_of(tmp_path).open()
    # Refused even on the DEFAULT directory, where an unusable one is not.
    _served, url = lane
    monitor = monitor_for(
        config_for(lane=url, records=RecordSettings(directory=tmp_path, declared=False)),
        [RecordingSink()],
    )
    with pytest.raises(RecordStoreInUse):
        monitor.start()
    # The control: once the holder lets go, the same directory opens.
    first.close()
    second = store_of(tmp_path)
    second.open()
    second.close()


def test_the_monitor_and_the_agent_share_a_directory_without_touching_each_other(
    tmp_path, lane
):
    _served, url = lane
    shared = tmp_path / "shared"
    # A crash left one half-written file of each kind.
    shared.mkdir(mode=0o700)
    (shared / (".tmp-c-" + "c" * 32 + ".json")).write_text("{")
    (shared / (".tmp-m-" + "d" * 32 + ".json")).write_text("{")

    monitor = monitor_for(config_for(lane=url, records=record_settings(shared)),
                          [RecordingSink()])
    monitor.start()
    assert (shared / (".tmp-c-" + "c" * 32 + ".json")).exists(), (
        "the monitor swept away a half-written file that was the agent's"
    )
    assert not (shared / (".tmp-m-" + "d" * 32 + ".json")).exists()

    from conftest import agent_for

    base = agent_config_for(tmp_path, lane_url=url)
    config = AgentConfig(**{
        name: getattr(base, name) for name in AgentConfig.__dataclass_fields__
        if name != "records"
    }, records=record_settings(shared))
    ua = FakeUa()
    agent = agent_for(config, ua, clock=FakeClock())
    ua.incoming("sip:door1@10.0.0.9", account_user=INTERCOM_ACCOUNT)
    agent.poll()

    assert (shared / "head.messages.json").exists() and (shared / "head.calls.json").exists()
    messages = monitor.messages(RecordQuery(limit=500)).to_dict()["messages"]
    calls = agent.calls(RecordQuery(limit=500)).to_dict()["calls"]
    assert messages and calls
    assert all(one["record_id"].startswith("m-") for one in messages)
    assert all(one["call_id"].startswith("c-") for one in calls)
    # Each kind's counter is its own file, so neither restarts on the other's.
    heads = {name: (shared / name).read_text() for name in ("head.messages.json",
                                                            "head.calls.json")}
    assert heads["head.messages.json"] != heads["head.calls.json"]


def test_an_undeclared_directory_is_the_published_default():
    raw = {
        "monitor": {"id": "monitor-1", "site_id": "site-1"},
        "targets": {"lane": {"url": "http://127.0.0.1:8090"}},
    }
    records = MonitorConfig.from_dict(raw).records
    assert records.directory == DEFAULT_RECORD_DIRECTORY and records.declared is False
    raw["records"] = {"directory": "/srv/records"}
    records = MonitorConfig.from_dict(raw).records
    assert str(records.directory) == "/srv/records" and records.declared is True


def test_an_undeclared_directory_that_cannot_be_used_does_not_stop_the_monitor(
    tmp_path, lane, caplog
):
    """It starts, says where the records were meant to go ONCE, and still pages."""
    served, url = lane
    blocked = tmp_path / "records"
    blocked.mkdir()
    blocked.chmod(0o500)
    caplog.set_level(logging.WARNING)
    clock = FakeClock()
    sink = RecordingSink()
    try:
        monitor = monitor_for(
            config_for(lane=url, poll_seconds=1.0,
                       records=RecordSettings(directory=blocked, declared=False)),
            [sink], clock=clock,
        )
        monitor.start()
        served.states["outbox_depth_growing"] = "active"
        served.sources["outbox_depth_growing"] = "measured"
        clock.advance(10)
        monitor.poll()
    finally:
        blocked.chmod(0o700)
    warned = [record for record in caplog.records if "is not declared" in record.getMessage()]
    assert len(warned) == 1
    assert ("outbox_depth_growing", "raised") in sink.codes, "an unusable default stopped a page"
    states = {(one["code"], one["state"]) for one in monitor.health().to_dict()["codes"]}
    assert (MonitorCode.RECORD_STORE_UNWRITABLE.value, "active") in states


def test_an_undeclared_directory_that_cannot_be_used_does_not_stop_the_agent(tmp_path, lane):
    from conftest import agent_for

    _served, url = lane
    blocked = tmp_path / "blocked"
    blocked.mkdir()
    blocked.chmod(0o500)
    base = agent_config_for(tmp_path, lane_url=url)
    config = AgentConfig(**{
        name: getattr(base, name) for name in AgentConfig.__dataclass_fields__
        if name != "records"
    }, records=RecordSettings(directory=blocked, declared=False))
    try:
        ua = FakeUa()
        agent = agent_for(config, ua, clock=FakeClock())
        ua.incoming("sip:door1@10.0.0.9", account_user=INTERCOM_ACCOUNT)
        agent.poll()
    finally:
        blocked.chmod(0o700)
    assert ("answer", "call-1") in ua.commands
    states = {(one["code"], one["state"]) for one in agent.health().to_dict()["codes"]}
    assert (AgentCode.RECORD_STORE_UNWRITABLE.value, "active") in states
