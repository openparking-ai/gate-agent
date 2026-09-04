"""Run a server on an ephemeral port for the length of a test.

Copied from `lane-controller/tests/serving.py`, because every target in this
suite is reached the same way the monitor reaches one: over a socket, by URL.
There is no in-process shortcut for our lane and none for a foreign one -- a
test that read ours by calling `LaneService` directly and the foreign one over
HTTP would be comparing two different things, and the property being measured
is that the monitor cannot tell them apart.

**A HANDLER THREAD MAY NOT OUTLIVE THE TEST THAT STARTED IT**, and this is where
that is enforced. It turned `main` red once, on a commit whose tree was
byte-identical to one CI had already passed twice:

    408 passed in 92.55s
    Fatal Python error: _enter_buffered_busy: could not acquire lock for
    <_io.BufferedWriter name='<stderr>'> at interpreter shutdown, possibly due
    to daemon threads
    Aborted (core dumped) ... Process completed with exit code 134

Every test passed and the process then aborted while exiting. `ThreadingHTTPServer`
sets `daemon_threads = True`, and `socketserver._Threads.append` **drops a
daemon thread on the floor rather than tracking it** -- so `server_close()` had
nothing to join, and a handler still inside `do_GET` when the interpreter began
finalising deadlocked on the stderr buffer lock while printing its own exception.
It is silent when it does not fire and it is a red `main` when it does.

The repair is one line and one assertion: handler threads are NOT daemons here,
so `socketserver` tracks them and `server_close()` joins them; and what was
tracked is then checked, so a thread that survives is a failed test rather than
an abort ten minutes later in somebody else's job.
"""

from __future__ import annotations

import contextlib
import threading


def track_handlers(server) -> list[threading.Thread]:
    """Register every handler thread this server starts, and return the register.

    OUR OWN, rather than `socketserver`'s `_threads`. That attribute is private,
    it holds a `_NoThreads` sentinel on 3.14 and `None` on 3.11, and -- the half
    that matters -- it is EMPTY for a server with `daemon_threads = True`,
    because `_Threads.append` drops a daemon thread on the floor. A check built
    on it would read empty for the exact configuration that has the bug, which
    is a check that cannot fail.

    `process_request_thread` runs INSIDE the handler thread, so
    `current_thread()` there is the thread that has to be gone by teardown.
    """
    register: list[threading.Thread] = []
    original = server.process_request_thread

    def tracked(request, client_address):
        register.append(threading.current_thread())
        return original(request, client_address)

    # An INSTANCE attribute, which shadows the class's method for
    # `self.process_request_thread(...)` and leaves every other server alone.
    server.process_request_thread = tracked
    return register


@contextlib.contextmanager
def serving(server):
    """Yield the base URL of `server`, running in a thread, then shut it down."""
    # NOT DAEMONS. `ThreadingHTTPServer`'s default is `True`, which makes
    # `socketserver` skip tracking them entirely and leaves `server_close()`
    # with nothing to join. With this, every handler is tracked and joined, and
    # the check below has something to measure.
    server.daemon_threads = False
    # Stashed on the server as well as held here, so a test ABOUT this teardown
    # can read the register this fixture actually used. Calling `track_handlers`
    # a second time would return a fresh empty list and any assertion over it
    # would be one that cannot fail.
    started = server.handler_threads = track_handlers(server)
    # A short poll interval, because `shutdown()` waits for the loop to notice
    # and the default is half a second -- which every server in this suite pays,
    # sixteen times over in the fail-control.
    thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.02),
                              daemon=True)
    thread.start()
    host, port = server.server_address[:2]
    try:
        yield f"http://{host}:{port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        leaked = [one for one in started if one.is_alive()]
        assert not leaked, (
            f"{len(leaked)} handler thread(s) survived server_close(): "
            f"{[one.name for one in leaked]}. A handler still running when the interpreter "
            "finalises is the abort this fixture exists to stop -- every test passes and the "
            "process then dies with exit 134."
        )
