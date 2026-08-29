"""The foreign lane. Four routes, hand-built payloads, nothing else.

Every request it receives is RECORDED, method and path, so a test can ask the
question a monitor's reader should be asked from the attacker's side: what did
you actually do to this lane? An assertion about the monitor's source can be
evaded by a client the sweep did not recognise; an assertion about what arrived
at the lane cannot.
"""

from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from lane_controller.contract import NEVER_ALARM, MalfunctionCode

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
        self.sources: dict[str, str] = {}
        self.never_alarm_override: dict[str, bool] = {}

    def describe(self) -> dict:
        payload = {
            "lane_id": self.lane_id,
            "site_id": self.site_id,
            "direction": self.direction,
            "contract_version": 1,
            # No loops, so nothing to publish. Not `null` and not our five keys.
            "geometry": {},
            "event_window_depth": 2,
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
        return {
            "contract_version": 1,
            "decision": {
                "outcome": "fallback",
                "reason": VENDOR_REASON,
                "fallback": None,
                "cause": None,
                "presence": None,
                "at": "2026-08-30T14:03:11.482913+00:00",
                "read_ref": None,
            },
            "transit": {"state": "none", "since": None},
        }

    def health(self) -> dict:
        """Every code in the contract version this lane claims.

        `unknown` with no source is the truth for a lane with none of this
        instrumentation, and it is a COMPLETE payload rather than a short one.
        A test moves individual codes through `self.states`.
        """
        codes = [
            {
                "code": code.value,
                "state": self.states.get(code.value, "unknown"),
                "source": self.sources.get(code.value, "no_source"),
                "never_alarm": self.never_alarm_override.get(code.value, code in NEVER_ALARM),
                "caveat": NEVER_ALARM.get(code),
            }
            for code in MalfunctionCode
        ]
        if _break("short_health"):
            codes = codes[:-1]
        if _break("no_source_label"):
            for entry in codes:
                del entry["source"]
        payload = {"contract_version": 1, "codes": codes}
        if _break("future_version"):
            payload["contract_version"] = 99
        return payload

    def events(self, since: int) -> dict:
        log = [
            {
                "cursor": 1,
                "event_id": "fl-0001",
                "kind": "attendant_called",
                "lane_id": self.lane_id,
                "occurred_at": "2026-08-30T14:03:10.000000+00:00",
                "detail": {},
            },
        ]
        return {
            "contract_version": 1,
            "cursor": 1,
            "reset": since > 1,
            "dropped": 0,
            "events": [item for item in log if item["cursor"] > since],
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
