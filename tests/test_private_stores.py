"""OP-4-9. Ticket records and captures are private: `0700` and `0600`.

The call and message records already were. A ticket record says when a car
was at which lane, and a capture is a photograph of whoever stood at the
barrier, and both were written with whatever the umask allowed -- `0644` on
most boxes, readable by every account on it.
"""

from __future__ import annotations

import os
import stat

import pytest

from cameras import jpeg
from gate_agent.store import CaptureStore, StoreUnwritable
from gate_agent.tickets import TicketStore
from test_capture_store import START
from test_tickets import a_record


def mode(path) -> int:
    return stat.S_IMODE(os.stat(path).st_mode)


@pytest.fixture
def open_umask():
    """The widest umask a box could have: nothing masked."""
    previous = os.umask(0)
    yield
    os.umask(previous)


def test_ticket_records_are_0600_in_a_0700_directory(tmp_path, open_umask):
    directory = tmp_path / "tickets"
    directory.mkdir(mode=0o755)
    directory.chmod(0o755)
    store = TicketStore(directory)
    store.open()
    record = a_record()
    store.write(record)
    assert mode(directory) == 0o700
    assert mode(directory / f"{record.ticket_id}.json") == 0o600
    # A new directory, too.
    fresh = TicketStore(tmp_path / "new" / "tickets")
    fresh.open()
    assert mode(tmp_path / "new" / "tickets") == 0o700


def test_captures_are_0600_in_a_0700_directory(tmp_path, open_umask):
    directory = tmp_path / "store"
    directory.mkdir()
    directory.chmod(0o755)
    store = CaptureStore(directory, 30, 1 << 20, now=lambda: START)
    store.open()
    record = store.write(jpeg(b"x" * 64), camera_id="front", reason="interval",
                         captured_at=START)
    assert mode(directory) == 0o700
    written = [one for one in directory.iterdir() if one.stem == record.image_path.stem]
    assert len(written) == 2, written
    assert all(mode(one) == 0o600 for one in written), [oct(mode(one)) for one in written]


def test_a_directory_that_cannot_be_made_private_is_refused(tmp_path, monkeypatch):
    """One this process does not own, say: tightening does nothing, and it is
    refused rather than used open."""
    tickets = tmp_path / "tickets"
    tickets.mkdir()
    tickets.chmod(0o755)
    captures = tmp_path / "store"
    captures.mkdir()
    captures.chmod(0o755)
    monkeypatch.setattr(os, "chmod", lambda *args, **kwargs: None)
    with pytest.raises(OSError, match="more than its owner"):
        TicketStore(tickets).open()
    with pytest.raises(StoreUnwritable, match="private"):
        CaptureStore(captures, 30, 1 << 20, now=lambda: START).open()
