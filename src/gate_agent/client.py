"""The only thing in this package that talks to a target, and it only reads.

**The invariant this module exists to hold: the monitor has NO OPENING
AUTHORITY.** It reads GETs and it sends messages. It never calls a vend, never
resolves a transit, never writes to a lane. That is not a promise about
intention -- a monitor that could act would be a new route to a barrier, which is
the boundary every outside reviewer of this project has named, and an intention
is not a boundary.

So it is held three ways, and each one catches what the others cannot:

  * **There is one request builder in this file and it hard-codes `method="GET"`
    with no body.** `tests/test_no_opening_authority.py` walks the AST of every
    module in this package and requires every `urllib.request.Request` in it to
    be exactly that. A `data=` or another method anywhere goes red.
  * **Nothing outside this file talks to a target.** The same sweep requires
    that -- so the guarantee cannot be evaded by opening a socket somewhere else,
    which is what a source check scoped to one file would miss.
  * **A recording target is polled for real and asked what it saw.** The sweep
    is about the source; this is about the wire, and a monitor that reached a
    lane through something the sweep did not recognise would be caught here.

The sinks POST -- a webhook is how a third party's paging system takes the seat.
That is the opposite direction, to somewhere that is not a lane, and `sinks.py`
is forbidden by the same sweep from importing this module or reading a target.
Keeping the two apart in separate files is what makes either statement
checkable.

Standard library only, like every service in this estate: this runs beside a
lane, on a box in a gate housing, and every dependency is one more thing to
cross-compile, patch and have go wrong somewhere with no keyboard attached.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request

log = logging.getLogger(__name__)

#: A response larger than this is not a health payload. Reading an unbounded
#: body from a target that has gone strange is how a monitor becomes the second
#: thing that is down: this process is meant to outlive whatever it watches.
MAX_RESPONSE_BYTES = 1 << 20

#: Comfortably longer than a local health route takes and far shorter than any
#: poll interval, so a target that hangs delays one poll rather than stopping the
#: monitor. A SETTING AND AN ASSUMPTION -- nothing here measures how long a
#: loaded Jetson takes to answer its own health route.
DEFAULT_TIMEOUT = 5.0


class TargetUnreachable(Exception):
    """The target did not answer, or answered something unreadable.

    ONE exception for both, deliberately. To a monitor they are the same fact --
    "I asked and I do not have an answer" -- and the code it raises says exactly
    that. Splitting them would invite a caller to treat an unparseable body as
    less serious than a refused connection, and a target publishing nonsense is
    not the healthier of the two.

    What it is NOT is `ok`. Every path in this module that fails raises; there
    is no path that returns an empty dict, because a client that swallowed a
    connection failure and returned `{}` would make a dead lane read as a lane
    with nothing to report.
    """


class ReadOnlyClient:
    """Reads a target. Any target. Cannot do anything else."""

    def __init__(self, base_url: str, token: str | None = None, timeout: float = DEFAULT_TIMEOUT):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout

    def get(self, path: str) -> dict:
        """One GET, or `TargetUnreachable`. There is no other method here."""
        url = f"{self.base_url}{path}"
        request = urllib.request.Request(url, method="GET")
        if self.token:
            request.add_header("Authorization", f"Bearer {self.token}")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = response.read(MAX_RESPONSE_BYTES + 1)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise TargetUnreachable(f"{url}: {exc}") from exc
        if len(body) > MAX_RESPONSE_BYTES:
            raise TargetUnreachable(f"{url}: answered more than {MAX_RESPONSE_BYTES} bytes")
        try:
            parsed = json.loads(body)
        except ValueError as exc:
            raise TargetUnreachable(f"{url}: answered something that is not JSON") from exc
        if not isinstance(parsed, dict):
            raise TargetUnreachable(f"{url}: answered {type(parsed).__name__}, not an object")
        return parsed


__all__ = ["DEFAULT_TIMEOUT", "MAX_RESPONSE_BYTES", "ReadOnlyClient", "TargetUnreachable"]
