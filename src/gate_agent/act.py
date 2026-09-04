"""The ONE thing in this package that asks a lane to open a barrier.

**IT IS ITS OWN FILE, and that is the whole design.** Until this round nothing
here could build a request that was not a `GET`, and
`tests/test_no_opening_authority.py` proved it by walking the source of every
module. That guarantee cannot survive this round unchanged -- the agent now
commands a vend -- so it is REPLACED rather than weakened, and the replacement
is named here so the exemption can be bounded:

  * **exactly one module may build a non-GET at a lane, and it is this one.**
    `sinks.py` may POST too and points AWAY from a lane; the sweep names both,
    and a third is a change to that list and therefore a change somebody has to
    argue for.
  * **it may build exactly one request**: `POST /v1/lane/vend`. The path is a
    constant in this file, the method is a constant, and the sweep requires both.
  * **it cannot be built without an ACT TOKEN.** The constructor refuses one
    that is `None`, so a lane a site declared no `act_token_file` for has no
    client at all -- not a client that would be refused by the lane, which is a
    round trip and a different failure.

**THE READ TOKEN DOES NOT AUTHORISE THIS.** Two credentials, two files, and the
lane's own bind rule makes the same split on its side: off loopback it refuses
to serve the vend route without a second token. A read token that also opened a
barrier would mean every consumer holding a read credential held an opening one.

**Nothing is followed.** The opener refuses every 3xx, exactly as the read
client's does, and here the stakes are higher by one whole category: the request
this would follow a redirect on is the one carrying the credential that opens a
barrier.

**WHAT THIS DOES NOT DECIDE.** Every refusal belongs to the lane. This client
sends an assertion and reports what came back; it does not check presence, it
does not check the malfunction table, it does not check whether the decision is
stale, and it must not -- `lane-controller/src/lane_controller/vend.py` says why
in its own first paragraph: *"if the caller asserted the completion and this lane
trusted it, the caller would be `POST /sessions/open` with a microphone
attached"*. A second copy of those refusals here would be a copy that comes to
disagree with the one the barrier actually obeys.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from dataclasses import dataclass

from .redirects import open_url

log = logging.getLogger(__name__)

#: The ONE route this client calls. A constant rather than a parameter: a path
#: a caller could choose is a client that can reach any route on a lane, and the
#: sweep in `tests/test_no_opening_authority.py` reads this name.
VEND_PATH = "/v1/lane/vend"

#: The one identity kind this contract version's vend route accepts. A plate is
#: deliberately not on it -- a plate is what a camera reads, and a caller that
#: could assert one would be handing the lane a measurement it did not make.
IDENTITY_KIND = "ticket"

#: A refusal body larger than this is not one. The same reason the read client
#: bounds its own: a process that reads an unbounded body from a target that has
#: gone strange is the second thing to be down.
MAX_RESPONSE_BYTES = 1 << 16


class LaneUnreachable(Exception):
    """The lane did not answer, or answered something unreadable, or 5xx'd.

    To a driver at a barrier these are one fact and the agent's answer is one
    answer: a person. Split from the refusal below because THAT one means the
    lane is up and said no, which is a different repair on a different machine.
    """


class LaneActRefusedUs(Exception):
    """The lane answered, and what it answered was that this agent may not act.

    A 401, a 403 or a 404 on the vend route. **This is not the same as a
    refusal of the vend** -- that is a 409 with a code from the lane's own
    published set, and it means the lane considered the request and said no
    about the vehicle or the lane. This means the lane would not consider it at
    all: the act token is wrong, or the route is not there because the lane is
    an older build.

    A human is told either way, and which of them it was decides which machine
    somebody goes to.
    """

    def __init__(self, message: str, status: int) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True, slots=True)
class VendAnswer:
    """What the lane said. Its words, not a translation of them.

    `code` is the lane's own refusal code on a 409, verbatim: a lane that is not
    ours has its own vocabulary and the contract requires a consumer to ESCALATE
    on one it does not recognise rather than map it onto the nearest thing it
    knows. Translating here would be that mapping.
    """

    commanded: bool
    #: The lane's refusal code on a 409, and `None` on a 202.
    code: str | None = None
    #: The malfunction the lane named, where its refusal was `malfunction_active`.
    malfunction: str | None = None
    #: The lane's own event cursor at the moment it accepted, off the 202 body.
    #:
    #: **NOT a `completion_id`.** The round-7 brief expected one in this answer
    #: and there is none: `lane_controller.contract.VendCommanded` carries
    #: `event_cursor` and `transit` and nothing else. The completion's own
    #: identifier is minted by the lane onto its `assisted_identity` EVENT,
    #: which is on `GET /v1/lane/events` and is not served to the caller. The
    #: cursor is the join to that record and is what this agent keeps.
    event_cursor: int | None = None
    #: What the lane says the transit is at the moment it answered: `pending`,
    #: because the crossing has not happened yet.
    transit: str | None = None


class LaneActClient:
    """Commands a vend at ONE lane, with that lane's own act token."""

    def __init__(self, base_url: str, act_token: str, timeout: float) -> None:
        if not act_token:
            # A client with no act token is refused HERE rather than built and
            # refused by the lane: a round trip that was always going to fail
            # is a driver waiting for an answer nobody could have given.
            raise ValueError(
                "a lane act client needs an act token. A lane with no act_token_file is "
                "READ-ONLY to this agent, and the agent must not build a client for one."
            )
        self.base_url = base_url.rstrip("/")
        self.act_token = act_token
        self.timeout = timeout

    def vend(
        self,
        *,
        authorised_by: str,
        ticket_ref: str,
        decision_at: str,
        idempotency_key: str,
    ) -> VendAnswer:
        """One `POST /v1/lane/vend`. The lane applies its own refusals.

        `decision_at` is ECHOED, never invented: it is the `at` this lane
        published for the decision the ticket was minted against, and sending
        `now()` instead would be a caller telling a lane which decision it is
        completing. The lane answers `decision_mismatch` to that, which is the
        right answer and the wrong thing to have asked.
        """
        body = json.dumps(
            {
                "authorised_by": authorised_by,
                "identity": {"kind": IDENTITY_KIND, "ticket_ref": ticket_ref},
                "decision_at": decision_at,
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}{VEND_PATH}", data=body, method="POST"
        )
        request.add_header("Content-Type", "application/json")
        request.add_header("Authorization", f"Bearer {self.act_token}")
        # REQUIRED by the lane, and there is no generated fallback on either
        # side: a key this agent invented per attempt would be unique per
        # request, which is the same as having none.
        request.add_header("Idempotency-Key", idempotency_key)
        try:
            with open_url(request, timeout=self.timeout) as response:
                raw = response.read(MAX_RESPONSE_BYTES + 1)
                status = response.status
        except urllib.error.HTTPError as exc:
            raw = exc.read(MAX_RESPONSE_BYTES + 1)
            status = exc.code
            if status >= 500:
                raise LaneUnreachable(f"the lane answered HTTP {status}") from exc
            if status != 409:
                # 401, 403, 404 and anything else that is not a named refusal.
                raise LaneActRefusedUs(
                    f"the lane answered HTTP {status} to a vend", status
                ) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise LaneUnreachable(f"the lane could not be reached: {exc}") from exc

        if len(raw) > MAX_RESPONSE_BYTES:
            raise LaneUnreachable("the lane answered more than a vend answer can be")
        try:
            payload = json.loads(raw)
        except ValueError as exc:
            raise LaneUnreachable("the lane answered something that is not JSON") from exc
        if not isinstance(payload, dict):
            raise LaneUnreachable("the lane answered something that is not an object")

        if status == 409:
            code = payload.get("code")
            if not isinstance(code, str) or not code:
                # A 409 with no code is a refusal this build cannot report to a
                # human, and reporting it as a generic failure would lose the
                # one thing that says which machine to go to.
                raise LaneUnreachable("the lane refused a vend and named no code")
            malfunction = payload.get("malfunction")
            return VendAnswer(
                commanded=False,
                code=code,
                malfunction=malfunction if isinstance(malfunction, str) else None,
            )
        if status != 202:
            raise LaneUnreachable(f"the lane answered HTTP {status} to a vend")
        cursor = payload.get("event_cursor")
        transit = payload.get("transit")
        return VendAnswer(
            commanded=True,
            event_cursor=cursor if isinstance(cursor, int) and not isinstance(cursor, bool)
            else None,
            transit=transit if isinstance(transit, str) else None,
        )


__all__ = [
    "IDENTITY_KIND",
    "MAX_RESPONSE_BYTES",
    "VEND_PATH",
    "LaneActClient",
    "LaneActRefusedUs",
    "LaneUnreachable",
    "VendAnswer",
]
