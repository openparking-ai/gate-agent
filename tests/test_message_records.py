"""Every message the monitor sends is a RECORD, and the record is the account.

Gokhan, 2026-10-30, on a car let out without paying: *"record it as exceptions
and add as much information as we can. what happened, who did what, gate agent
sent messages times, who received, etc"*, and *"gate agent records sending
messages for hardware, network failures"*. The platform builds the exception and
reads this half from `GET /v1/monitor/messages`. What is proven here, each with
its control:

  * the record SURVIVES A RESTART, and so does its cursor;
  * a hardware or network failure is a record EVEN WHEN `log` IS THE ONLY SINK,
    and the record says nobody was configured to receive it;
  * a failed delivery is recorded with its REASON and EVERY RETRY, each timed;
  * the SMTP reply code is the one a real SMTP conversation answered;
  * NO LOG LINE EVER PRINTS AN EMAIL ADDRESS, swept over every failure path;
  * the two BOUNDS delete and drop, oldest first, and SAY SO;
  * PAGINATION is stable: every record once, in order, across a restart;
  * the route needs the CREDENTIAL off loopback, and its QUERY is bounded.
"""

from __future__ import annotations

import json
import logging
import smtplib
import socketserver
import threading
import urllib.error
import urllib.request
from datetime import datetime, timedelta

import pytest

from conftest import FakeClock, FakeUtc, config_for, monitor_for, record_settings
from fakes import RecordingSink
from foreign_lane import ForeignLane
from foreign_lane import make_server as foreign_server
from gate_agent.config import EmailSinkConfig, LogSinkConfig, WebhookSinkConfig
from gate_agent.contract import MonitorCode, message_record_unfinished
from gate_agent.records import (
    ADDRESS,
    BadQuery,
    RecordQuery,
    RecordStore,
    parse_query,
    redact_addresses,
)
from gate_agent.service import MonitorService, make_server
from gate_agent.sinks import EmailSink, LogSink, WebhookSink
from serving import serving

ORDINARY_CODE = "outbox_depth_growing"
#: Fictional, and every one of them is a configured recipient below. The sweep
#: looks for these AND for anything email-shaped.
ADDRESSES = ("night-desk@example.com", "duty-manager@example.com")
SENDER = "monitor@example.com"


def raise_on(lane: ForeignLane, code: str = ORDINARY_CODE, state: str = "active") -> None:
    lane.states[code] = state
    lane.sources[code] = "measured"


def messages(monitor, **query) -> list[dict]:
    return monitor.messages(RecordQuery(**query)).to_dict()["messages"]


def transitions(monitor, code: str) -> list[dict]:
    """Every record about `code`, in the order the messages were SENT.

    Not the page's order: a page is in revision order, and a record that is
    retried or superseded later moves to the end of it.
    """
    found = [one for one in messages(monitor, limit=500) if one["code"] == code]
    return sorted(found, key=lambda one: (one["at"], one["transition"] != "raised"))


class ScriptedSmtp:
    """An SMTP "server" that answers what a test tells it to, in order.

    Each entry in `script` is an exception to raise or `None` to accept. On an
    accept it sets `data_reply` the way `sinks._ReplyKeepingSMTP` does, so the
    path from a server's answer to the record is the product's.
    """

    def __init__(self, script) -> None:
        self.script = list(script)
        self.sent = []

    def __call__(self, _config):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def send_message(self, message):
        outcome = self.script.pop(0) if self.script else None
        if outcome is not None:
            raise outcome
        self.sent.append(message)
        self.data_reply = (250, b"2.0.0 Ok: queued as 4F2A9C01")
        return {}


def email_sink(opener) -> EmailSink:
    return EmailSink(
        EmailSinkConfig(host="smtp.example.com", port=587, sender=SENDER, recipients=ADDRESSES),
        opener=opener,
    )


@pytest.fixture
def lane():
    served = ForeignLane()
    with serving(foreign_server(served)) as url:
        yield served, url


# ---------------------------------------------------------------------------
# GUARANTEE 1 -- durable across a restart, and so is the cursor
# ---------------------------------------------------------------------------


def test_every_message_is_a_record_and_it_survives_a_restart(lane):
    """The record is on the disk, read back by a process that never sent it.

    A second `Monitor` on the same directory is a restart: a new index rebuilt
    from the files, a new counter read off the disk. It serves the first run's
    records unchanged, and every record it writes is AFTER them.
    """
    served, url = lane
    records = record_settings()
    clock, utc = FakeClock(), FakeUtc()
    first = monitor_for(config_for(lane=url, poll_seconds=1.0, records=records),
                        [RecordingSink()], clock=clock, now=utc)
    first.start()
    raise_on(served)
    clock.advance(10)
    utc.advance(10)
    first.poll()
    before = messages(first, limit=500)
    raised = [one for one in before if one["code"] == ORDINARY_CODE]
    assert len(raised) == 1 and raised[0]["transition"] == "raised"
    head = first.messages(RecordQuery()).to_dict()["cursor"]

    second = monitor_for(config_for(lane=url, poll_seconds=1.0, records=records),
                         [RecordingSink()], clock=FakeClock(), now=utc)
    second.start()
    after = messages(second, limit=500)
    assert raised[0] in after, "the first run's record is not what the second run serves"
    # The restart re-raises what is still active, as the contract has always
    # said -- a second record, AFTER the first, never one that replaces it.
    again = [one for one in after if one["code"] == ORDINARY_CODE]
    assert len(again) == 2
    assert again[1]["revision"] > head
    # And the cursor the first run handed out still means the same thing.
    newer = second.messages(RecordQuery(since=head)).to_dict()
    assert newer["reset"] is False
    assert {one["record_id"] for one in newer["messages"]}.isdisjoint(
        {one["record_id"] for one in before}
    )


def test_a_delivery_the_last_run_did_not_finish_is_closed_as_interrupted(lane):
    """A crash between the two writes leaves `pending`; the next start says so."""
    _served, url = lane
    records = record_settings()
    store = RecordStore(
        records.directory, kind="m", time_key="at", lane_key="lane_id",
        unfinished=message_record_unfinished,
    )
    store.open()
    left = {
        "record_id": "m-" + "a" * 32, "kind": "transition", "site_id": "site-1",
        "monitor_id": "monitor-0", "lane_id": None, "target": "lane", "target_kind": "lane",
        "code": ORDINARY_CODE, "subject": None, "origin": "target", "transition": "raised",
        "at": "2026-08-30T13:00:00+00:00", "first_seen_at": "2026-08-30T13:00:00+00:00",
        "cleared_at": None, "source": "measured", "caveat": None, "status": None,
        "paged": False,
        "deliveries": [{
            "sink": "recording", "kind": "log", "recipients": [],
            "recipient_configured": False, "state": "pending", "attempts": [],
        }],
    }
    store.put(left["record_id"], left)
    store.close()  # the process that wrote it has gone

    monitor = monitor_for(config_for(lane=url, records=records), [RecordingSink()])
    monitor.start()
    closed = next(one for one in messages(monitor, limit=500)
                  if one["record_id"] == left["record_id"])
    assert closed["deliveries"][0]["state"] == "interrupted"
    assert closed["deliveries"][0]["next_attempt_at"] is None


# ---------------------------------------------------------------------------
# GUARANTEE 2 -- a hardware or network failure is a record with only `log`
# ---------------------------------------------------------------------------


def test_a_network_failure_with_only_log_is_still_a_record_and_says_nobody_was_paged(capsys):
    """`lane_unreachable` at a site whose only sink is `log`.

    The record exists, its one delivery is the log line, and it says in two
    fields what the startup line says in words: nobody is paged.
    """
    monitor = monitor_for(config_for(lane="http://127.0.0.1:1"), [LogSink(LogSinkConfig())])
    monitor.start()
    lost = transitions(monitor, MonitorCode.LANE_UNREACHABLE.value)
    assert len(lost) == 1, "the lane could not be reached and no message record says so"
    record = lost[0]
    assert record["transition"] == "raised"
    assert record["origin"] == "monitor"
    assert record["target_kind"] == "lane"
    assert record["paged"] is False
    assert [one["sink"] for one in record["deliveries"]] == ["log"]
    delivery = record["deliveries"][0]
    assert delivery["recipients"] == [] and delivery["recipient_configured"] is False
    assert delivery["state"] == "delivered"
    assert delivery["attempts"][0]["result"] == "accepted"
    # The control: the log line it records really was written.
    assert MonitorCode.LANE_UNREACHABLE.value in capsys.readouterr().out


def test_a_hardware_fault_a_lane_publishes_is_a_record_with_only_log(lane, capsys):
    """A code the LANE publishes -- equipment, passed through -- recorded the same."""
    served, url = lane
    clock = FakeClock()
    monitor = monitor_for(config_for(lane=url, poll_seconds=1.0),
                          [LogSink(LogSinkConfig())], clock=clock)
    monitor.start()
    raise_on(served, "boom_did_not_rise")
    clock.advance(10)
    monitor.poll()
    record = transitions(monitor, "boom_did_not_rise")
    assert record and record[0]["origin"] == "target" and record[0]["paged"] is False
    assert record[0]["lane_id"] == served.lane_id
    capsys.readouterr()


# ---------------------------------------------------------------------------
# GUARANTEE 3 -- a failed delivery: its reason, every retry, each timed
# ---------------------------------------------------------------------------


def test_a_failed_delivery_is_recorded_with_its_reason_and_every_retry(lane):
    """Refused, refused, refused: three attempts, three reasons, then `failed`."""
    served, url = lane
    clock, utc = FakeClock(), FakeUtc()
    # 451: SMTP's "try again later". A refusal worth retrying.
    refusal = smtplib.SMTPRecipientsRefused(
        {address: (451, b"4.3.0 mailbox temporarily unavailable") for address in ADDRESSES}
    )
    smtp = ScriptedSmtp([None] + [refusal] * 5)  # the startup message is accepted
    monitor = monitor_for(
        config_for(lane=url, poll_seconds=1.0, retry_attempts=2, retry_seconds=30.0),
        [RecordingSink(), email_sink(smtp)], clock=clock, now=utc,
    )
    monitor.start()
    raise_on(served)
    clock.advance(10)
    utc.advance(10)
    monitor.poll()

    delivery = transitions(monitor, ORDINARY_CODE)[0]["deliveries"][1]
    assert delivery["state"] == "retrying"
    assert delivery["next_attempt_at"] == (utc.moment + timedelta(seconds=30)).isoformat()
    for _ in range(2):
        clock.advance(31)
        utc.advance(31)
        monitor.poll()

    record = transitions(monitor, ORDINARY_CODE)[0]
    delivery = record["deliveries"][1]
    assert delivery["recipients"] == list(ADDRESSES)
    assert delivery["state"] == "failed"
    assert delivery["next_attempt_at"] is None
    attempts = delivery["attempts"]
    assert len(attempts) == 3, "one attempt and two retries, each on the record"
    assert all(one["result"] == "failed" for one in attempts)
    assert all(one["reply_code"] == 451 for one in attempts)
    assert all(one["transient"] is True for one in attempts)
    assert all("refused every recipient" in one["reason"] for one in attempts)
    times = [datetime.fromisoformat(one["at"]) for one in attempts]
    assert times == sorted(times) and len(set(times)) == 3
    assert delivery["sent_at"] == attempts[0]["at"]
    assert delivery["acknowledgement"] == "not_acknowledged"
    assert delivery["acknowledged_at"] is None and delivery["acknowledged_by"] is None


def test_a_retry_that_gets_through_is_delivered_and_the_sink_recovers(lane):
    """The control for the test above: a retry is a real second attempt."""
    served, url = lane
    clock, utc = FakeClock(), FakeUtc()
    # The startup message is taken; the raise is refused, and so is the message
    # about that refusal -- which the email sink is sent too; then it is back.
    closed = smtplib.SMTPServerDisconnected("closed")
    smtp = ScriptedSmtp([None, closed, closed])
    good = RecordingSink(name="good")
    monitor = monitor_for(
        config_for(lane=url, poll_seconds=1.0, retry_seconds=30.0),
        [good, email_sink(smtp)], clock=clock, now=utc,
    )
    monitor.start()
    raise_on(served)
    clock.advance(10)
    utc.advance(10)
    monitor.poll()
    clock.advance(31)
    utc.advance(31)
    monitor.poll()

    delivery = transitions(monitor, ORDINARY_CODE)[0]["deliveries"][1]
    assert delivery["state"] == "delivered"
    assert [one["result"] for one in delivery["attempts"]] == ["failed", "accepted"]
    assert delivery["attempts"][1]["reply_code"] == 250
    assert delivery["attempts"][1]["reply"] == "2.0.0 Ok: queued as 4F2A9C01"
    assert delivery["delivered_at"] == delivery["attempts"][1]["at"]
    assert (MonitorCode.SINK_DELIVERY_FAILED.value, "recovered") in good.codes
    raises = [message for message in smtp.sent if ORDINARY_CODE in message["Subject"]]
    assert len(raises) == 1, "the raise reached the mailbox once, on the retry"


class _SmtpConversation(socketserver.StreamRequestHandler):
    """Just enough of RFC 5321 to answer `smtplib` for real, over a socket."""

    refuse_rcpt = False

    def _say(self, line: str) -> None:
        self.wfile.write((line + "\r\n").encode())

    def handle(self) -> None:
        self._say("220 smtp.example.com ESMTP")
        data = False
        while True:
            line = self.rfile.readline()
            if not line:
                return
            text = line.decode("utf-8", "replace").rstrip("\r\n")
            if data:
                if text == ".":
                    data = False
                    self._say("250 2.0.0 Ok: queued as 7C1D00E2")
                continue
            verb = text.split(" ", 1)[0].upper()
            if verb in ("EHLO", "HELO"):
                self._say("250 smtp.example.com")
            elif verb == "MAIL":
                self._say("250 2.1.0 Ok")
            elif verb == "RCPT":
                if self.refuse_rcpt:
                    address = text.split(":", 1)[1].strip()
                    self._say(f"550 5.1.1 {address}: Recipient address rejected")
                else:
                    self._say("250 2.1.5 Ok")
            elif verb == "DATA":
                data = True
                self._say("354 End data with <CR><LF>.<CR><LF>")
            elif verb == "QUIT":
                self._say("221 2.0.0 Bye")
                return
            else:
                self._say("250 Ok")


@pytest.mark.parametrize("refuse", [False, True])
def test_the_reply_code_is_the_one_a_real_smtp_conversation_answered(refuse, caplog):
    """`smtplib` against a real socket: the 250 and its text, or the 550 and no address.

    The refusing server quotes the recipient back -- `550 5.1.1 <address>:
    Recipient address rejected` -- which is what real servers do, and the reason
    that is logged must not carry it.
    """
    handler = type("Conversation", (_SmtpConversation,), {"refuse_rcpt": refuse})
    server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        sink = EmailSink(EmailSinkConfig(
            host="127.0.0.1", port=server.server_address[1], sender=SENDER,
            recipients=ADDRESSES, tls=False,
        ))
        lane = ForeignLane()
        caplog.set_level(logging.DEBUG)
        with serving(foreign_server(lane)) as url:
            monitor = monitor_for(config_for(lane=url, retry_attempts=0), [sink])
            monitor.start()
        attempt = messages(monitor)[0]["deliveries"][0]["attempts"][0]
    finally:
        server.shutdown()
        server.server_close()
    if refuse:
        assert attempt["result"] == "failed"
        assert attempt["reply_code"] == 550
        assert not ADDRESS.search(attempt["reason"]), attempt["reason"]
        logged = "\n".join(record.getMessage() for record in caplog.records)
        assert "could not deliver" in logged, "the failure was not logged at all"
        assert not ADDRESS.search(logged), logged
    else:
        assert attempt["result"] == "accepted"
        assert attempt["reply_code"] == 250
        assert attempt["reply"] == "2.0.0 Ok: queued as 7C1D00E2"


# ---------------------------------------------------------------------------
# GUARANTEE 4 -- no log line ever prints an email address
# ---------------------------------------------------------------------------


class _Exploding:
    """A sink that raises something nobody imported, quoting an address."""

    name = "exploding"
    kind = "log"

    def deliver(self, notification):
        raise RuntimeError(f"could not reach {ADDRESSES[1]} through the relay")

    def announce(self, subject, payload):
        raise RuntimeError(f"could not reach {ADDRESSES[1]} through the relay")


def test_no_log_line_ever_prints_an_email_address(lane, caplog, capsys):
    """Every failure path a sink has, each quoting an address, swept.

    `SMTPRecipientsRefused` prints the recipients it refused; `SMTPSenderRefused`
    the sender; a server's `SMTPDataError` text quotes whoever it likes; an
    `OSError` and an unexpected exception carry whatever their author wrote.
    Every one of them reaches `log.error` -- at startup, on a transition, on a
    retry -- and every log record and every byte on stdout and stderr is swept.
    """
    served, url = lane
    caplog.set_level(logging.DEBUG)
    clock, utc = FakeClock(), FakeUtc()
    failures = [
        smtplib.SMTPRecipientsRefused({ADDRESSES[0]: (550, f"<{ADDRESSES[0]}> no".encode())}),
        smtplib.SMTPSenderRefused(553, f"<{SENDER}> not allowed".encode(), SENDER),
        smtplib.SMTPDataError(554, f"rejected for {ADDRESSES[1]}".encode()),
        OSError(f"connection to relay for {ADDRESSES[0]} reset"),
        smtplib.SMTPServerDisconnected(f"bye {ADDRESSES[1]}"),
    ]
    smtp = ScriptedSmtp(failures * 3)

    class _Refused:
        status = 403

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    hook = WebhookSink(
        WebhookSinkConfig(url="https://paging.example.com/hooks/x", token="t"),
        opener=lambda request, timeout=None: _Refused(),
    )
    monitor = monitor_for(
        config_for(lane=url, poll_seconds=1.0, retry_attempts=2, retry_seconds=5.0),
        [LogSink(LogSinkConfig()), email_sink(smtp), hook, _Exploding()],
        clock=clock, now=utc,
    )
    monitor.start()
    for state in ("active", "ok", "active"):
        raise_on(served, state=state)
        for _ in range(4):
            clock.advance(6)
            utc.advance(6)
            monitor.poll()

    out = capsys.readouterr()
    logged = "\n".join(record.getMessage() for record in caplog.records)
    swept = "\n".join((logged, out.out, out.err))
    assert "could not deliver" in logged, "no failure was logged, so the sweep saw nothing"
    for address in ADDRESSES + (SENDER,):
        assert address not in swept, f"{address} reached a log line"
    assert not ADDRESS.search(swept), ADDRESS.search(swept).group(0)

    # THE CONTROLS. The addresses ARE on the record -- that is where they
    # belong -- so the sweep above is not passing because nothing had any.
    recorded = json.dumps(messages(monitor, limit=500))
    assert ADDRESSES[0] in recorded
    # And the sweep can see an address at all: the same pattern, planted.
    assert ADDRESS.search(f"planted {ADDRESSES[0]} here")
    assert redact_addresses(f"x {ADDRESSES[0]} y") == "x <address> y"


# ---------------------------------------------------------------------------
# GUARANTEE 5 -- the bounds delete, oldest first, and say so
# ---------------------------------------------------------------------------


def test_the_retention_rule_deletes_old_records_and_the_health_route_says_so(lane):
    served, url = lane
    clock, utc = FakeClock(), FakeUtc()
    monitor = monitor_for(
        config_for(lane=url, poll_seconds=1.0, records=record_settings(retention_days=1)),
        [RecordingSink()], clock=clock, now=utc,
    )
    monitor.start()
    raise_on(served)
    clock.advance(10)
    utc.advance(10)
    monitor.poll()
    held = monitor.health().to_dict()["records"]["held"]
    assert held >= 2

    clock.advance(2 * 86400)
    utc.advance(2 * 86400)
    monitor.poll()
    reads = monitor.health().to_dict()["records"]
    assert reads["held"] == 0
    assert reads["purged_for_age"] == held
    assert reads["retention_days"] == 1
    # A consumer that had not read them is TOLD, not served a page that looks
    # complete.
    page = monitor.messages(RecordQuery()).to_dict()
    assert page["gap"] is True and page["messages"] == []
    assert list(monitor._records.directory.glob("m-*.json")) == []


def test_the_size_rule_drops_the_oldest_by_time_and_says_up_to_when(tmp_path):
    """Oldest by the VALUE of the time, not by the order they were written.

    Five records fit. The fifth WRITTEN is the oldest by time; the sixth makes
    room for itself. Read off the order of writing, the first written would go;
    read off the time, the fifth does.
    """
    moment = datetime.fromisoformat("2026-10-30T22:00:00+00:00")
    store = RecordStore(
        tmp_path, kind="m", time_key="at", lane_key="lane_id", unfinished=lambda body: False,
        max_bytes=2000, now=lambda: moment,
    )
    store.open()
    stamps = [moment - timedelta(minutes=minutes) for minutes in (1, 2, 3, 4, 9, 0)]
    for index, stamp in enumerate(stamps):
        store.put(f"m-{index:032x}", {"at": stamp.isoformat(), "lane_id": "lane-1",
                                      "pad": "x" * 300})
    reads = store.reads()
    assert reads["dropped_for_space"] == 1
    assert reads["bytes_used"] <= 2000
    kept = {datetime.fromisoformat(body["at"]) for body in store.page(RecordQuery())[0]}
    assert set(stamps) - kept == {moment - timedelta(minutes=9)}, (
        "the oldest by time was not the one that went"
    )
    assert reads["dropped_through"] == (moment - timedelta(minutes=9)).isoformat()
    assert reads["last_dropped_at"] == moment.isoformat()
    assert store.page(RecordQuery())[4] is True  # gap
    # And it was DELETED, not hidden: one file fewer on the disk.
    assert len(list(tmp_path.glob("m-*.json"))) == 5


# ---------------------------------------------------------------------------
# GUARANTEE 6 -- pagination is stable
# ---------------------------------------------------------------------------


def test_paging_serves_every_record_once_in_order_and_resumes_after_a_restart(tmp_path):
    def store():
        opened = RecordStore(tmp_path, kind="m", time_key="at", lane_key="lane_id",
                             unfinished=lambda body: False)
        opened.open()
        return opened

    first = store()
    base = datetime.fromisoformat("2026-10-30T21:00:00+00:00")
    ids = [f"m-{index:032x}" for index in range(25)]
    for index, record_id in enumerate(ids):
        first.put(record_id, {"at": (base + timedelta(minutes=index)).isoformat(),
                              "lane_id": "lane-1" if index % 2 else None})

    def everything(opened, since=0, **query):
        seen, cursors = [], []
        while True:
            records, cursor, more, reset, _gap = opened.page(
                RecordQuery(since=since, limit=10, **query)
            )
            assert reset is False
            seen += [body["revision"] for body in records]
            cursors.append(cursor)
            since = cursor
            if not more:
                return seen, cursors

    revisions, cursors = everything(first)
    assert revisions == list(range(1, 26))
    assert cursors == sorted(cursors)

    # A record that CHANGES comes back once, under its new revision.
    first.put(ids[3], {"at": (base + timedelta(minutes=3)).isoformat(), "lane_id": "lane-1",
                       "retried": True})
    changed, _ = everything(first, since=cursors[-1])
    assert changed == [26]

    # A RESTART: the cursor means the same thing to a process that never issued it.
    first.close()
    second = store()
    assert everything(second, since=26)[0] == []
    second.put("m-" + "f" * 32, {"at": base.isoformat(), "lane_id": None})
    assert everything(second, since=26)[0] == [27]
    assert second.page(RecordQuery(since=999))[3] is True  # reset: not this store's

    # The FILTERS: a lane's records and every record about no lane; a window.
    lane_only = [body for body in second.page(RecordQuery(lane="lane-1", limit=500))[0]]
    assert lane_only and all(body["lane_id"] in ("lane-1", None) for body in lane_only)
    assert any(body["lane_id"] is None for body in lane_only)
    start, end = base + timedelta(minutes=5), base + timedelta(minutes=10)
    window = second.page(RecordQuery(start=start, end=end, limit=500))[0]
    assert sorted(datetime.fromisoformat(body["at"]) for body in window) == [
        base + timedelta(minutes=minute) for minute in range(5, 10)
    ]


def test_a_record_id_is_never_a_path(tmp_path):
    store = RecordStore(tmp_path / "records", kind="m", time_key="at", lane_key="lane_id",
                        unfinished=lambda body: False)
    store.open()
    (tmp_path / "secret.json").write_text('{"revision": 1, "at": "x"}')
    for hostile in ("../secret", "m-../../secret", "head", "m-" + "g" * 32):
        assert store.get(hostile) is None
        with pytest.raises(ValueError):
            store.put(hostile, {"at": "2026-10-30T21:00:00+00:00"})


# ---------------------------------------------------------------------------
# GUARANTEE 7 -- the episode: first seen, cleared
# ---------------------------------------------------------------------------


def test_a_cleared_fault_says_when_it_was_first_seen_and_when_it_cleared(lane):
    served, url = lane
    clock, utc = FakeClock(), FakeUtc()
    monitor = monitor_for(config_for(lane=url, poll_seconds=1.0), [RecordingSink()],
                          clock=clock, now=utc)
    monitor.start()
    raise_on(served)
    clock.advance(10)
    utc.advance(10)
    monitor.poll()
    raised_at = utc()
    raise_on(served, state="ok")
    clock.advance(300)
    utc.advance(300)
    monitor.poll()
    raised, recovered = transitions(monitor, ORDINARY_CODE)
    assert raised["first_seen_at"] == raised_at and raised["cleared_at"] is None
    assert recovered["transition"] == "recovered"
    assert recovered["first_seen_at"] == raised_at
    assert recovered["cleared_at"] == utc()


def test_a_record_store_that_refuses_is_a_code_and_the_message_still_goes_out(lane):
    served, url = lane
    records = record_settings()
    clock = FakeClock()
    sink = RecordingSink()
    monitor = monitor_for(config_for(lane=url, poll_seconds=1.0, records=records), [sink],
                          clock=clock)
    monitor.start()
    records.directory.chmod(0o500)
    try:
        raise_on(served)
        clock.advance(10)
        monitor.poll()
    finally:
        records.directory.chmod(0o700)
    assert (ORDINARY_CODE, "raised") in sink.codes, "a refused record stopped the page"
    assert (MonitorCode.RECORD_STORE_UNWRITABLE.value, "raised") in sink.codes
    states = {(one["code"], one["state"]) for one in monitor.health().to_dict()["codes"]}
    assert (MonitorCode.RECORD_STORE_UNWRITABLE.value, "active") in states
    assert monitor.health().to_dict()["records"]["writable"] is False


def test_a_directory_that_will_not_take_a_write_refuses_startup(tmp_path, lane):
    from gate_agent.records import RecordUnwritable

    _served, url = lane
    blocked = tmp_path / "records"
    blocked.mkdir()
    blocked.chmod(0o500)
    try:
        monitor = monitor_for(config_for(lane=url, records=record_settings(blocked)),
                              [RecordingSink()])
        with pytest.raises(RecordUnwritable, match="will not take a write"):
            monitor.start()
    finally:
        blocked.chmod(0o700)


# ---------------------------------------------------------------------------
# GUARANTEE 8 -- the route: its credential, and its query
# ---------------------------------------------------------------------------


@pytest.fixture
def watched(lane):
    served, url = lane
    monitor = monitor_for(config_for(lane=url), [RecordingSink()])
    monitor.start()
    return monitor


def _get(base, path, token=None):
    request = urllib.request.Request(f"{base}{path}")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"{}")


def test_the_messages_route_needs_the_credential_when_one_is_configured(watched):
    with serving(make_server(MonitorService(watched), port=0, token="s3cret")) as base:
        status, _body = _get(base, "/v1/monitor/messages")
        assert status == 401
        status, _body = _get(base, "/v1/monitor/messages", token="wrong")
        assert status == 401
        status, body = _get(base, "/v1/monitor/messages", token="s3cret")
        assert status == 200 and body["messages"]


def test_off_loopback_the_monitor_will_not_start_without_a_credential_file(tmp_path, capsys):
    from gate_agent.cli import main

    assert main(["monitor", "--config", str(tmp_path / "absent.toml"),
                 "--host", "0.0.0.0"]) == 2
    assert "no token" in capsys.readouterr().err


@pytest.mark.parametrize(
    "query",
    [
        "since=-1", "since=../../etc/passwd", "since=1e3", "since=" + "9" * 20,
        "since=%2e%2e%2f", "limit=0", "limit=501", "lane=", "lane=" + "x" * 129,
        "lane=a%0Ab", "from=2026-10-30T21:00:00", "to=not-a-time",
        "from=2026-10-30T22:00:00%2B00:00&to=2026-10-30T21:00:00%2B00:00",
        "lane_id=lane-1", "since=1&since=2", "x" * 1100,
    ],
)
def test_the_query_is_bounded_and_refuses_what_it_does_not_know(watched, query):
    with serving(make_server(MonitorService(watched), port=0)) as base:
        status, body = _get(base, f"/v1/monitor/messages?{query}")
    assert status == 400, (query, body)
    assert body["error"]
    with pytest.raises(BadQuery):
        parse_query(query)


def test_the_control_a_whole_query_is_accepted_and_echoed(watched):
    query = ("since=0&lane=fl-lane-a&from=2026-08-30T13:00:00%2B00:00"
             "&to=2026-08-30T15:00:00%2B00:00&limit=5")
    with serving(make_server(MonitorService(watched), port=0)) as base:
        status, body = _get(base, f"/v1/monitor/messages?{query}")
    assert status == 200
    assert body["query"] == {
        "since": 0, "lane": "fl-lane-a", "from": "2026-08-30T13:00:00+00:00",
        "to": "2026-08-30T15:00:00+00:00", "limit": 5,
    }
    assert body["messages"]


# ---------------------------------------------------------------------------
# GUARANTEE 9 -- a retry never says an old fact after a new one
# ---------------------------------------------------------------------------


class ScriptedHook:
    """A paging endpoint: answers each POST by a rule, and keeps every body."""

    def __init__(self, rule) -> None:
        self.rule = rule
        self.posted: list[dict] = []

    def __call__(self, request, timeout=None):
        body = json.loads(request.data.decode("utf-8"))
        self.posted.append(body)
        status = self.rule(body)
        if status >= 300:
            raise urllib.error.HTTPError(request.full_url, status, "no", {}, None)

        class _Answer:
            def __enter__(self_inner):
                return self_inner

            def __exit__(self_inner, *exc):
                return False

        _Answer.status = status
        return _Answer()

    def about(self, code: str) -> list[dict]:
        return [body for body in self.posted if body.get("code") == code]


def hook_sink(hook) -> WebhookSink:
    return WebhookSink(
        WebhookSinkConfig(url="https://paging.example.com/hooks/openparking", token="t"),
        opener=hook,
    )


def _fail_raised_once(status_raised=503, status_recovered=204):
    seen = {"raised": 0}

    def rule(body):
        if body.get("code") != ORDINARY_CODE:
            return 204
        if body["transition"] == "raised":
            seen["raised"] += 1
            return status_raised if seen["raised"] == 1 else 204
        return status_recovered

    return rule


def test_a_late_retry_after_the_recovery_is_superseded_and_never_sent(lane):
    """Raised gets a 503; the fault clears; recovered is delivered; the retry is due.

    Sent, the old `raised` would reach the pager after its `recovered` and
    reopen the incident. It is not sent: the delivery is `superseded`, by the
    record of the recovery that already reached that sink.
    """
    served, url = lane
    clock, utc = FakeClock(), FakeUtc()
    hook = ScriptedHook(_fail_raised_once())
    monitor = monitor_for(config_for(lane=url, poll_seconds=1.0, retry_seconds=60.0),
                          [hook_sink(hook)], clock=clock, now=utc)
    monitor.start()
    raise_on(served)
    clock.advance(10)
    utc.advance(10)
    monitor.poll()
    raise_on(served, state="ok")
    clock.advance(10)
    utc.advance(10)
    monitor.poll()
    clock.advance(61)
    utc.advance(61)
    monitor.poll()

    raised, recovered = transitions(monitor, ORDINARY_CODE)
    delivery = raised["deliveries"][0]
    assert delivery["state"] == "superseded"
    assert delivery["superseded_by"] == recovered["record_id"]
    assert delivery["next_attempt_at"] is None
    assert len(delivery["attempts"]) == 1, "the superseded retry was attempted anyway"
    assert [body["transition"] for body in hook.about(ORDINARY_CODE)] == ["raised", "recovered"]


def test_a_retry_for_a_code_that_cleared_unheard_is_superseded_by_the_state(lane):
    """The recovery was refused for good, so nothing later reached the sink.

    The code is `ok` now, and a `raised` sent now is false: superseded, by the
    code's own state, with nothing named.
    """
    served, url = lane
    clock, utc = FakeClock(), FakeUtc()
    hook = ScriptedHook(_fail_raised_once(status_recovered=400))
    monitor = monitor_for(config_for(lane=url, poll_seconds=1.0, retry_seconds=60.0),
                          [hook_sink(hook)], clock=clock, now=utc)
    monitor.start()
    raise_on(served)
    clock.advance(10)
    utc.advance(10)
    monitor.poll()
    raise_on(served, state="ok")
    clock.advance(10)
    utc.advance(10)
    monitor.poll()
    clock.advance(61)
    utc.advance(61)
    monitor.poll()

    raised, recovered = transitions(monitor, ORDINARY_CODE)
    assert recovered["deliveries"][0]["state"] == "failed"
    assert raised["deliveries"][0]["state"] == "superseded"
    assert raised["deliveries"][0]["superseded_by"] is None
    assert [body["transition"] for body in hook.about(ORDINARY_CODE)] == ["raised", "recovered"]


@pytest.mark.parametrize(
    ("failure", "retried"),
    [
        (smtplib.SMTPRecipientsRefused({ADDRESSES[0]: (550, b"5.1.1 no such user")}), False),
        (smtplib.SMTPDataError(554, b"5.7.1 rejected"), False),
        (smtplib.SMTPDataError(452, b"4.3.1 insufficient storage"), True),
        (smtplib.SMTPServerDisconnected("closed"), True),
        (OSError("network is unreachable"), True),
    ],
)
def test_only_a_failure_a_moment_can_mend_is_retried_by_email(lane, failure, retried):
    served, url = lane
    clock = FakeClock()
    smtp = ScriptedSmtp([None, failure, failure])
    monitor = monitor_for(config_for(lane=url, poll_seconds=1.0),
                          [RecordingSink(), email_sink(smtp)], clock=clock)
    monitor.start()
    raise_on(served)
    clock.advance(10)
    monitor.poll()
    delivery = transitions(monitor, ORDINARY_CODE)[0]["deliveries"][1]
    assert delivery["attempts"][0]["transient"] is retried
    assert delivery["state"] == ("retrying" if retried else "failed")


@pytest.mark.parametrize(("status", "retried"), [(404, False), (401, False), (302, False),
                                                 (429, True), (503, True)])
def test_only_a_failure_a_moment_can_mend_is_retried_by_webhook(lane, status, retried):
    served, url = lane
    clock = FakeClock()
    hook = ScriptedHook(lambda body: status if body.get("code") == ORDINARY_CODE else 204)
    monitor = monitor_for(config_for(lane=url, poll_seconds=1.0), [hook_sink(hook)],
                          clock=clock)
    monitor.start()
    raise_on(served)
    clock.advance(10)
    monitor.poll()
    delivery = transitions(monitor, ORDINARY_CODE)[0]["deliveries"][0]
    assert delivery["attempts"][0]["http_status"] == status
    assert delivery["attempts"][0]["transient"] is retried
    assert delivery["state"] == ("retrying" if retried else "failed")


def test_a_retry_interrupted_by_a_restart_is_resumed_after_it(lane):
    """The recovery got a 503; the monitor restarted; the recovery still arrives.

    The fault is still `ok` when the new run comes up -- which the new run says
    nothing about, `unknown -> ok` being silent -- so the retry the last run
    left is the only way the pager ever hears the incident ended.
    """
    served, url = lane
    records = record_settings()
    utc = FakeUtc()
    first_hook = ScriptedHook(
        lambda body: 503 if body.get("transition") == "recovered" else 204
    )
    clock = FakeClock()
    first = monitor_for(config_for(lane=url, poll_seconds=1.0, records=records),
                        [hook_sink(first_hook)], clock=clock, now=utc)
    first.start()
    raise_on(served)
    clock.advance(10)
    utc.advance(10)
    first.poll()
    raise_on(served, state="ok")
    clock.advance(10)
    utc.advance(10)
    first.poll()
    recovered = transitions(first, ORDINARY_CODE)[1]
    assert recovered["deliveries"][0]["state"] == "retrying"

    # THE PROCESS DIES, and a new one starts 30 seconds later.
    first.close()
    utc.advance(30)
    second_hook = ScriptedHook(lambda body: 204)
    clock = FakeClock()
    second = monitor_for(config_for(lane=url, poll_seconds=1.0, records=records),
                         [hook_sink(second_hook)], clock=clock, now=utc)
    second.start()
    assert not second_hook.about(ORDINARY_CODE), "resumed before it was due"
    clock.advance(31)
    utc.advance(31)
    second.poll()

    delivered = [body for body in second_hook.about(ORDINARY_CODE)]
    assert [body["transition"] for body in delivered] == ["recovered"]
    assert delivered[0]["record_id"] == recovered["record_id"]
    resumed = next(one for one in messages(second, limit=500)
                   if one["record_id"] == recovered["record_id"])
    assert resumed["deliveries"][0]["state"] == "delivered"
    assert [one["result"] for one in resumed["deliveries"][0]["attempts"]] == [
        "failed", "accepted",
    ]


def test_every_payload_carries_the_record_id_and_a_retry_carries_the_same_one(lane, capsys):
    served, url = lane
    clock, utc = FakeClock(), FakeUtc()
    hook = ScriptedHook(_fail_raised_once())
    smtp = ScriptedSmtp([])
    monitor = monitor_for(config_for(lane=url, poll_seconds=1.0, retry_seconds=60.0),
                          [LogSink(LogSinkConfig()), email_sink(smtp), hook_sink(hook)],
                          clock=clock, now=utc)
    monitor.start()
    raise_on(served)
    clock.advance(10)
    utc.advance(10)
    monitor.poll()
    clock.advance(61)
    utc.advance(61)
    monitor.poll()

    startup = next(one for one in messages(monitor, limit=500) if one["kind"] == "unmeasured")
    raised = transitions(monitor, ORDINARY_CODE)[0]
    assert hook.posted[0]["kind"] == "unmeasured"
    assert hook.posted[0]["record_id"] == startup["record_id"]
    tries = hook.about(ORDINARY_CODE)
    assert len(tries) == 2, "the retry did not happen"
    assert {body["record_id"] for body in tries} == {raised["record_id"]}
    logged = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line]
    assert raised["record_id"] in {line.get("record_id") for line in logged}
    assert startup["record_id"] in {line.get("record_id") for line in logged}
    mailed = [str(message) for message in smtp.sent]
    assert any(f"record_id: {raised['record_id']}" in body for body in mailed)
    # And the events route carries it too, so the two can be joined.
    events = monitor.events(0).to_dict()["events"]
    assert raised["record_id"] in {event["record_id"] for event in events}


# ---------------------------------------------------------------------------
# GUARANTEE 10 -- the lane filter, a partial acceptance, and the webhook's host
# ---------------------------------------------------------------------------


def test_the_lane_filter_leaves_out_every_other_lane(tmp_path):
    store = RecordStore(tmp_path, kind="m", time_key="at", lane_key="lane_id",
                        unfinished=lambda body: False)
    store.open()
    at = "2026-10-30T21:00:00+00:00"
    for index, lane_id in enumerate(("lane-1", "lane-2", None, "lane-2", "lane-1")):
        store.put(f"m-{index:032x}", {"at": at, "lane_id": lane_id})
    served = [body["lane_id"] for body in store.page(RecordQuery(lane="lane-1"))[0]]
    assert sorted(served, key=str) == sorted(["lane-1", "lane-1", None], key=str)
    # The control: unfiltered, the other lane IS there to be left out.
    assert "lane-2" in [body["lane_id"] for body in store.page(RecordQuery())[0]]


class _PartlyAccepting(ScriptedSmtp):
    def send_message(self, message):
        self.sent.append(message)
        self.data_reply = (250, b"2.0.0 Ok: queued as 9B00C2")
        return {ADDRESSES[1]: (550, b"5.1.1 mailbox unavailable")}


def test_a_partly_accepted_email_records_who_was_refused(lane):
    """`send_message` returns the refused instead of raising. Told is not everyone."""
    served, url = lane
    clock = FakeClock()
    monitor = monitor_for(config_for(lane=url, poll_seconds=1.0),
                          [email_sink(_PartlyAccepting([]))], clock=clock)
    monitor.start()
    raise_on(served)
    clock.advance(10)
    monitor.poll()
    attempt = transitions(monitor, ORDINARY_CODE)[0]["deliveries"][0]["attempts"][0]
    assert attempt["result"] == "accepted"
    assert attempt["refused_recipients"] == [ADDRESSES[1]]


def test_the_webhook_recipient_is_its_host_and_never_its_token(lane):
    _served, url = lane
    sink = WebhookSink(
        WebhookSinkConfig(url="https://paging.example.com/hooks/T0KEN-ABC123?key=SECRET-42",
                          token="t"),
        opener=ScriptedHook(lambda body: 204),
    )
    assert sink.recipients == ("paging.example.com",)
    monitor = monitor_for(config_for(lane=url), [sink])
    monitor.start()
    recorded = json.dumps(messages(monitor, limit=500))
    assert "paging.example.com" in recorded
    assert "T0KEN-ABC123" not in recorded and "SECRET-42" not in recorded
