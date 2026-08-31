"""THE CI FLAKE, measured: a handler thread that outlives the test that started it.

It turned `main` red once, on `3fcb780`, whose tree is byte-identical to a tip
CI had already passed twice:

    408 passed in 92.55s
    Fatal Python error: _enter_buffered_busy: could not acquire lock for
    <_io.BufferedWriter name='<stderr>'> at interpreter shutdown, possibly due
    to daemon threads
    Aborted (core dumped) ... exit code 134

Every test passed and the process then aborted while EXITING. The traceback
above it named `tests/test_targets.py`'s deliberately-slow handler, still inside
`do_GET` when the interpreter began finalising.

**The cause is one default and one early return.** `ThreadingHTTPServer` sets
`daemon_threads = True`; `socketserver._Threads.append` returns without
recording a daemon thread; so `server_close()` had nothing to join and every
handler still running was left to be killed mid-write at interpreter exit.

The two tests below are the same measurement twice, on the same slow handler,
differing in that one flag -- so what is measured is the repair and not a
difference between two setups. Neither uses `serving()`: this is a test OF that
fixture's teardown, and running it through the thing under test would be a check
that compares a claim with itself.

Both bound the sleep at half a second and join with a real timeout, so a
regression here is a red test rather than a suite that hangs.
"""

from __future__ import annotations

import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from serving import serving, track_handlers  # noqa: F401

#: Long enough that the handler is certainly still inside it when the client has
#: given up and the server has been closed, short enough that a leaked one is
#: gone before the next test needs the interpreter.
SLOW_SECONDS = 0.5

#: What the client waits. Below `SLOW_SECONDS`, so the request is abandoned
#: while the handler is still running -- which is the shape the flake had.
CLIENT_TIMEOUT = 0.1


class _Slow(BaseHTTPRequestHandler):
    server_version = "slow-for-a-test"
    sys_version = ""

    def log_message(self, fmt, *args):
        pass

    def do_GET(self):  # noqa: N802  (http.server's spelling)
        time.sleep(SLOW_SECONDS)
        body = b'{"ok":true}'
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _slow_server():
    return ThreadingHTTPServer(("127.0.0.1", 0), _Slow)


def _abandon_a_request(url: str) -> None:
    """Fire one request and give up on it while the handler is still inside it."""
    with pytest.raises((TimeoutError, urllib.error.URLError, OSError)):
        urllib.request.urlopen(f"{url}/anything", timeout=CLIENT_TIMEOUT)


def _run(daemon_threads: bool) -> list[threading.Thread]:
    """One abandoned request through a server closed the way `serving` closes one.

    Returns the handler threads that were still ALIVE after `server_close()`.
    """
    server = _slow_server()
    server.daemon_threads = daemon_threads
    started = track_handlers(server)
    loop = threading.Thread(
        target=lambda: server.serve_forever(poll_interval=0.02), daemon=True
    )
    loop.start()
    host, port = server.server_address[:2]
    try:
        _abandon_a_request(f"http://{host}:{port}")
        # The handler thread has to have STARTED, or this measures nothing at
        # all -- an empty register would read as "no thread leaked".
        deadline = time.monotonic() + 5.0
        while not started and time.monotonic() < deadline:
            time.sleep(0.01)
        assert started, "no handler thread ever started, so this run measures nothing"
    finally:
        server.shutdown()
        server.server_close()
        loop.join(timeout=5)
    alive = [one for one in started if one.is_alive()]
    for one in started:
        # Leave nothing running for the rest of the session, whichever way this
        # went: a test about leaked threads that leaks one is its own problem.
        one.join(timeout=5)
    return alive


def test_a_daemon_handler_thread_survives_server_close():
    """THE LEAK, exactly as it was. This is the positive control.

    Without it, the test below would be an assertion about a search: "no thread
    survived" is worth nothing unless the same measurement can see one that did.
    """
    alive = _run(daemon_threads=True)
    assert alive, (
        "a daemon handler thread was joined by server_close(), so this control no longer "
        "reproduces the flake and the test below proves nothing"
    )


def test_a_tracked_handler_thread_does_not():
    """THE REPAIR. Same handler, same abandoned request, one flag different."""
    assert _run(daemon_threads=False) == []


def test_the_fixture_itself_refuses_to_leave_one_running():
    """And `serving()` is what every other test in this suite tears down through.

    The repair belongs in the fixture rather than in each test, so this asserts
    the fixture's own contract: after its `finally`, nothing it started is alive.
    """
    server = _slow_server()
    with serving(server) as url:
        _abandon_a_request(url)
    # `serving` asserts this itself on the way out; asserting it again here from
    # outside is what makes that assertion visible as a guarantee rather than as
    # an internal detail of a helper.
    #
    # THE REGISTER IS THE ONE THE FIXTURE USED. `track_handlers(server)` here
    # would install a second wrapper and hand back an empty list, and every
    # assertion over it would pass whatever had happened.
    assert server.daemon_threads is False
    assert server.handler_threads, "no handler ran, so this asserts nothing"
    assert not [one for one in server.handler_threads if one.is_alive()]
