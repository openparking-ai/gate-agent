"""Run a server on an ephemeral port for the length of a test.

Copied from `lane-controller/tests/serving.py`, because every target in this
suite is reached the same way the monitor reaches one: over a socket, by URL.
There is no in-process shortcut for our lane and none for a foreign one -- a
test that read ours by calling `LaneService` directly and the foreign one over
HTTP would be comparing two different things, and the property being measured
is that the monitor cannot tell them apart.
"""

from __future__ import annotations

import contextlib
import threading


@contextlib.contextmanager
def serving(server):
    """Yield the base URL of `server`, running in a thread, then shut it down."""
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
