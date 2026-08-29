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
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address[:2]
    try:
        yield f"http://{host}:{port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
