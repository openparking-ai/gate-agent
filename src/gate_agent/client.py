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

from .redirects import open_url

log = logging.getLogger(__name__)

#: A response larger than this is not a health payload. Reading an unbounded
#: body from a target that has gone strange is how a monitor becomes the second
#: thing that is down: this process is meant to outlive whatever it watches.
MAX_RESPONSE_BYTES = 1 << 20

#: The published default for how long this monitor waits for a target's answer.
#: Overridable per target, `[targets.<kind>].timeout_seconds`, and published on
#: `GET /v1/monitor` beside the poll interval.
#:
#: A SETTING AND AN ASSUMPTION -- nothing here measures how long a loaded Jetson
#: takes to answer its own health route. What it IS drawn against is the other
#: side of the seam. A lane's health route may itself read a third machine (the
#: identification service), and it bounds that read at its own
#: `[lane] identity_health_timeout_s`. **This number must comfortably exceed
#: that one**, or a lane that is up, serving, and correctly answering `unknown`
#: about a hung identification service is published by its monitor as a dead
#: lane -- and every real signal that lane was publishing is retired at the same
#: moment. The two live in different repositories, so the relationship is stated
#: in both contracts and measured in `tests/test_targets.py` against a real lane
#: reading a socket that never answers.
DEFAULT_TIMEOUT = 10.0


class TargetUnreachable(Exception):
    """The target did not answer, or answered something unreadable.

    ONE exception for both, deliberately. To a monitor they are the same fact --
    "I asked and I do not have an answer" -- and the code it raises says exactly
    that. Splitting them would invite a caller to treat an unparseable body as
    less serious than a refused connection, and a target publishing nonsense is
    not the healthier of the two.

    A **5xx** is here too. The target's process answered, but what it answered
    is that it could not do the thing, and the repair is at that target either
    way.

    What it is NOT is `ok`. Every path in this module that fails raises; there
    is no path that returns an empty dict, because a client that swallowed a
    connection failure and returned `{}` would make a dead lane read as a lane
    with nothing to report.

    And what it is NOT is `TargetRefusedUs`. See below: that distinction is the
    difference between three repairs, on three different machines.
    """


class TargetRefusedUs(Exception):
    """The target answered, and what it answered was no. `status` is its own.

    **AN HTTP ANSWER IS NOT SILENCE.** A 401, a 403, a 404 and a 302 are not a
    target that is down: they are a target that is up, that received the
    request, and that declined it -- and each one names a DIFFERENT machine as
    the problem:

      * **401 / 403** -- this monitor's credential. The platform is running
        perfectly and the token in a file beside this process is wrong or
        expired. Paging somebody to a healthy platform is the failure, and
        without the status in the message they cannot tell either.
      * **404** -- a route this build expects and that target does not have,
        which is what an OLDER platform answers. Reported as "the platform is
        down" it sends an operator to a machine that is fine.
      * **3xx** -- a redirect, which this process does not follow (see
        `redirects.py`). A target steering a monitor at another host is an
        answer worth a human's attention on its own.

    So it is its own monitor code, `<kind>_refused_us`, per target, with the
    status carried on the health entry and in every sink's message. A human
    reading one can tell a dead credential from an older platform from a dead
    platform; folded into `<kind>_unreachable` they could not.
    """

    def __init__(self, message: str, status: int) -> None:
        super().__init__(message)
        self.status = status


class ReadOnlyClient:
    """Reads a target. Any target. Cannot do anything else."""

    def __init__(self, base_url: str, token: str | None = None, timeout: float = DEFAULT_TIMEOUT):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout

    def get(self, path: str) -> dict:
        """One GET, or one of the two refusals. There is no other method here.

        **Nothing is followed.** The opener refuses every 3xx, so a `Location`
        header cannot steer this client at another host or carry this monitor's
        credential there -- see `redirects.py` for what that used to do.
        """
        url = f"{self.base_url}{path}"
        request = urllib.request.Request(url, method="GET")
        if self.token:
            request.add_header("Authorization", f"Bearer {self.token}")
        try:
            with open_url(request, timeout=self.timeout) as response:
                body = response.read(MAX_RESPONSE_BYTES + 1)
        except urllib.error.HTTPError as exc:
            # It ANSWERED. Which half of the fault that is depends on the
            # status, and the status is carried so a human can tell them apart.
            if exc.code >= 500:
                raise TargetUnreachable(f"{url}: HTTP {exc.code}") from exc
            raise TargetRefusedUs(f"{url}: HTTP {exc.code}", exc.code) from exc
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


__all__ = [
    "DEFAULT_TIMEOUT",
    "MAX_RESPONSE_BYTES",
    "ReadOnlyClient",
    "TargetRefusedUs",
    "TargetUnreachable",
]
