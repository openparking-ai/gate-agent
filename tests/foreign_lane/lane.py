"""The foreign lane. Four routes, hand-built payloads, nothing else.

Every request it receives is RECORDED, method and path, so a test can ask the
question a monitor's reader should be asked from the attacker's side: what did
you actually do to this lane? An assertion about the monitor's source can be
evaded by a client the sweep did not recognise; an assertion about what arrived
at the lane cannot.

**IT IMPORTS NOTHING FROM `lane_controller`, AND THAT IS THE POINT.** This file
used to open with `from lane_controller.contract import NEVER_ALARM,
MalfunctionCode` while its own docstring said it was written from the document
-- because the document withheld exactly those two sets. So the one artefact
that exists to show a stranger can take this seat was built on our Python
package, for the two things a stranger could not have got. That is now closed at
the source: `lane-controller/docs/CONTRACT.md` publishes the closed sets in full,
under **The closed sets**, and the literals below are a copy of that block.

`tests/test_targets.py` holds both halves of that: it reads this package's
imports and requires there to be none of ours, and -- in the suite that
legitimately has our lane installed -- it requires this literal to equal
`lane_controller.contract.MalfunctionCode`'s values. A copy that drifts from the
enum goes red there, which is the check the document's own block also carries on
the other side of the wire.
"""

from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

#: Every malfunction code, COPIED FROM `lane-controller/docs/CONTRACT.md`, from
#: the `<!--payload:sets-->` block under "The closed sets". Not imported: an
#: implementer of this contract has the document and nothing else, and a stub
#: that reached into our package would prove nothing about one.
MALFUNCTION_CODES = (
    "boom_did_not_rise",
    "boom_did_not_close",
    "vend_relay_fault",
    "arming_loop_stuck_occupied",
    "arming_loops_disagree",
    "closing_loops_never_firing",
    "camera_feed_lost",
    "camera_feed_frozen",
    "lens_obstructed_or_dark",
    "reference_not_recognised",
    "identity_service_down",
    "identity_service_degraded",
    "identity_service_unmeasured_weights",
    "platform_unreachable",
    "lane_gone_quiet",
    "outbox_depth_growing",
    "session_actions_dead_lettered",
    "intercom_registration_lost",
    "controller_on_battery",
    "disk_nearly_full",
    "clock_skew_rejected",
)

#: The codes a monitor may never page a human on, from the same block of the
#: same document.
NEVER_ALARM_CODES = ("reference_not_recognised",)

#: And this vendor's OWN caveat for the one it publishes. Written here rather
#: than copied from ours, because the contract says the caveat travels with the
#: code on the wire -- so a foreign lane's is a foreign lane's, and a monitor
#: that carried ours into a message about this lane would be putting our words
#: in a stranger's mouth.
VENDOR_CAVEAT = (
    "NOT an alarm. Our reader reports this whenever it cannot place the lane view, and an "
    "ordinary car arriving is one of the things it cannot place. Do not send anybody."
)

#: This vendor's own word for "the attendant took over at the barrier". It is
#: not in our `Fallback` and never will be.
VENDOR_REASON = "barrier_operator_intervened"


def _break(name: str) -> bool:
    """Whether the fail-control asked for this payload to be broken.

    A stub that quietly satisfied every assertion would be a fixture that
    measures nothing.
    """
    return os.environ.get("BREAK_FOREIGN_LANE") == name


class ForeignLane:
    """A lane with no loops, no identity service and no platform."""

    lane_id = "fl-lane-a"
    site_id = "fl-site"
    direction = "entry"

    def __init__(self) -> None:
        #: (method, path) for every request that reached this lane. The monitor
        #: is not trusted to describe its own behaviour.
        self.requests: list[tuple[str, str]] = []
        #: What this lane says about one code, so a test can move it.
        self.states: dict[str, str] = {}
        #: What this lane's last decision and current transit are, so a test can
        #: move them. `None` for `decision` is what a lane that has decided
        #: nothing serves -- it keeps no state store, so that is the honest
        #: answer after a restart and not the same thing as "nothing has ever
        #: happened here". Added for the agent round: the case a driver is told
        #: is derived from exactly these two fields, so a fixture that could not
        #: move them could not exercise a single row of that table.
        self.decision: dict | None = {
            "outcome": "fallback",
            "reason": VENDOR_REASON,
            "fallback": None,
            "cause": None,
            "presence": None,
            "at": "2026-08-30T14:03:11.482913+00:00",
            "read_ref": None,
        }
        self.transit: dict = {"state": "none", "since": None}
        self.sources: dict[str, str] = {}
        self.never_alarm_override: dict[str, bool] = {}
        #: Whether this lane omits `never_alarm` entirely -- what a serialiser
        #: that drops falsey values does, and what an implementer who read the
        #: field as optional writes. The contract requires it on every entry;
        #: this is how a test asks what happens when it is not there.
        self.drop_never_alarm = False
        #: Whether a triggering event is served with NO `cursor`. A lane that
        #: does this has broken its own contract -- the cursor is the join --
        #: and a consumer has to have somewhere to put that fact.
        self.drop_event_cursor = False
        #: Whether `reset` is forced to `false` however far this lane's cursor
        #: has moved. The two contract breaks a consumer cannot tell apart from
        #: an ordinary page without checking are "the cursor went backwards" and
        #: "it went backwards and did not say so", and this is the second.
        self.suppress_reset = False
        #: How many events this lane can still serve behind its cursor, and what
        #: `GET /v1/lane` publishes as `event_window_depth`. Published from the
        #: same attribute the eviction uses, so a test that widens the window
        #: cannot leave the lane describing a narrower one.
        self.window = 2
        self.dropped = 0
        self._seq = 0
        #: This lane's event log. A CONSUMER of `GET /v1/lane/events` decides
        #: what to do from `kind`, `cursor` and `occurred_at`, and a stub whose
        #: log never moved could not exercise one.
        self.log: list[dict] = []
        self.record("attendant_called", "2026-08-30T14:03:10.000000+00:00")

    def record(self, kind: str, occurred_at: str, detail: dict | None = None) -> int:
        """One event onto this lane's log, evicting past the window it publishes."""
        self._seq += 1
        self.log.append(
            {
                "cursor": self._seq,
                "event_id": f"fl-{self._seq:04d}",
                "kind": kind,
                "lane_id": self.lane_id,
                "occurred_at": occurred_at,
                "detail": dict(detail or {}),
            }
        )
        while len(self.log) > self.window:
            self.log.pop(0)
            self.dropped += 1
        return self._seq

    def describe(self) -> dict:
        payload = {
            "lane_id": self.lane_id,
            "site_id": self.site_id,
            "direction": self.direction,
            "contract_version": 1,
            # No loops, so nothing to publish. Not `null` and not our five keys.
            "geometry": {},
            "event_window_depth": self.window,
            "capabilities": {
                "confirms_entry": False,
                "has_identity_service": False,
                "has_platform": False,
                "has_display": False,
                "can_vend": False,
            },
        }
        if _break("future_version"):
            payload["contract_version"] = 99
        return payload

    def state(self) -> dict:
        payload = {
            "contract_version": 1,
            "decision": self.decision,
            "transit": self.transit,
        }
        if _break("future_version"):
            payload["contract_version"] = 99
        return payload

    def health(self) -> dict:
        """Every code in the contract version this lane claims.

        `unknown` with no source is the truth for a lane with none of this
        instrumentation, and it is a COMPLETE payload rather than a short one.
        A test moves individual codes through `self.states`.
        """
        codes = [
            {
                "code": code,
                "state": self.states.get(code, "unknown"),
                "source": self.sources.get(code, "no_source"),
                "never_alarm": self.never_alarm_override.get(code, code in NEVER_ALARM_CODES),
                "caveat": VENDOR_CAVEAT if code in NEVER_ALARM_CODES else None,
            }
            for code in MALFUNCTION_CODES
        ]
        if self.drop_never_alarm:
            for entry in codes:
                del entry["never_alarm"]
        payload = {"contract_version": 1, "codes": codes}
        if _break("future_version"):
            payload["contract_version"] = 99
        return payload

    def events(self, since: int) -> dict:
        """The cursor semantics as the document states them, both halves.

        `since` ahead of this lane's own cursor is a `reset`, and so is `since`
        behind the oldest event still held -- the second is the one a bounded
        window makes possible, and a consumer served a short page without the
        flag cannot tell it from a complete one.
        """
        oldest = self.log[0]["cursor"] if self.log else None
        served = []
        for item in self.log:
            if item["cursor"] <= since:
                continue
            item = dict(item)
            if self.drop_event_cursor:
                del item["cursor"]
            served.append(item)
        reset = since > self._seq or (oldest is not None and since + 1 < oldest)
        return {
            "contract_version": 1,
            "cursor": self._seq,
            "reset": False if self.suppress_reset else reset,
            "dropped": self.dropped,
            "events": served,
        }


class _Handler(BaseHTTPRequestHandler):
    lane: ForeignLane

    server_version = "foreign-lane"
    sys_version = ""

    def log_message(self, fmt: str, *args) -> None:
        pass

    def _record(self) -> None:
        self.lane.requests.append((self.command, urlparse(self.path).path))

    def do_GET(self) -> None:  # noqa: N802
        self._record()
        url = urlparse(self.path)
        if url.path == "/v1/lane":
            return self._json(200, self.lane.describe())
        if url.path == "/v1/lane/state":
            return self._json(200, self.lane.state())
        if url.path == "/v1/lane/health":
            return self._json(200, self.lane.health())
        if url.path == "/v1/lane/events":
            raw = parse_qs(url.query).get("since", ["0"])[0]
            try:
                since = int(raw)
            except ValueError:
                return self._json(400, {"error": "since must be an integer"})
            return self._json(200, self.lane.events(since))
        return self._json(404, {"error": "no such route"})

    def _refuse(self) -> None:
        # This lane has no act surface either, and answers the same way ours
        # does. It records the attempt first, which is the point: a monitor that
        # tried would be caught here even if the source sweep missed it.
        self._record()
        self.send_response(405)
        self.send_header("Allow", "GET")
        self.send_header("Content-Length", "0")
        self.end_headers()

    do_POST = _refuse  # noqa: N815
    do_PUT = _refuse  # noqa: N815
    do_PATCH = _refuse  # noqa: N815
    do_DELETE = _refuse  # noqa: N815

    def _json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def make_server(lane: ForeignLane | None = None, host: str = "127.0.0.1", port: int = 0):
    handler = type("_BoundHandler", (_Handler,), {"lane": lane or ForeignLane()})
    return ThreadingHTTPServer((host, port), handler)
