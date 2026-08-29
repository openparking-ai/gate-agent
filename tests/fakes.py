"""The other targets a monitor can watch, and the sinks that record what it said.

OUR LANE is not in here. It is built from the real `lane_controller` package,
served over a real socket, and read by the monitor over HTTP -- see
`tests/ours.py`. A fake standing in for our own lane would make "the same code
reads ours and a third party's" a claim about two fakes.

What IS here is the identification service and the platform, which are faked at
the wire: both are read through one route each, and building a whole Vehicle ID
engine or a Postgres to exercise a monitor's poll loop would be measuring those
rather than this. Each fake records what reached it, method and path, so an
assertion about what the monitor DID is available for them too.
"""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse


class FakeIdentityService:
    """A Vehicle ID service's `GET /v1/health`, and nothing else.

    That contract keeps this route unauthenticated by its own decision: it
    carries no plate and no image, and a monitor holding the read credential in
    order to ask whether a process is alive is that credential in one more
    place. So there is no token here, and that is the contract's choice showing
    through rather than a shortcut in this fake.
    """

    def __init__(self, status: str = "ok", schema_version: int = 1) -> None:
        self.status = status
        self.schema_version = schema_version
        self.requests: list[tuple[str, str]] = []

    def health(self) -> dict:
        return {
            "status": self.status,
            "schema_version": self.schema_version,
            "engine": {"name": "fake", "version": "0.0.0", "weights_id": "sha256:0"},
            "threshold_applied": 0.99,
            "camera_faults": {},
            "time": "2026-08-30T14:03:11.482913+00:00",
        }


class FakePlatform:
    """The operator surface's devices route, and nothing else.

    `devices` is whatever a test sets. The shape is the platform's:
    `id, lane_id, name, created_at, last_seen_at, revoked_at`.
    """

    def __init__(self, devices=None, token: str = "operator-token") -> None:
        self.devices = list(devices or [])
        self.token = token
        self.requests: list[tuple[str, str]] = []
        #: Every Authorization header seen, so a test can prove the monitor
        #: presents the credential and that the fake would notice if it did not.
        self.authorizations: list[str | None] = []


def _handler_for(obj, route_for):
    class _Handler(BaseHTTPRequestHandler):
        server_version = "fake-target"
        sys_version = ""

        def log_message(self, fmt: str, *args) -> None:
            pass

        def _record(self) -> None:
            obj.requests.append((self.command, urlparse(self.path).path))

        def do_GET(self) -> None:  # noqa: N802
            self._record()
            if hasattr(obj, "authorizations"):
                obj.authorizations.append(self.headers.get("Authorization"))
            status, payload = route_for(obj, urlparse(self.path).path, self.headers)
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _refuse(self) -> None:
            self._record()
            self.send_response(405)
            self.send_header("Allow", "GET")
            self.send_header("Content-Length", "0")
            self.end_headers()

        do_POST = _refuse  # noqa: N815
        do_PUT = _refuse  # noqa: N815
        do_PATCH = _refuse  # noqa: N815
        do_DELETE = _refuse  # noqa: N815

    return _Handler


def _identity_route(obj, path, _headers):
    if path == "/v1/health":
        return 200, obj.health()
    return 404, {"error": "no such route"}


def _platform_route(obj, path, headers):
    if not path.endswith("/devices"):
        return 404, {"error": "no such route"}
    if headers.get("Authorization") != f"Bearer {obj.token}":
        # The platform's operator surface is authenticated and the tenant comes
        # from the token. A fake that served the list without one would let a
        # monitor that never presented its credential pass every test here.
        return 401, {"error": "operator token required"}
    return 200, {"devices": obj.devices}


def identity_server(service: FakeIdentityService, host: str = "127.0.0.1", port: int = 0):
    return ThreadingHTTPServer((host, port), _handler_for(service, _identity_route))


def platform_server(platform: FakePlatform, host: str = "127.0.0.1", port: int = 0):
    return ThreadingHTTPServer((host, port), _handler_for(platform, _platform_route))


# ---------------------------------------------------------------------------
# Sinks
# ---------------------------------------------------------------------------


class RecordingSink:
    """A sink that keeps everything it was handed, and can be told to fail.

    `delivered` holds the notification OBJECTS and `payloads` their dicts, so a
    sweep over what a sink would put on a wire has something real to sweep.
    """

    def __init__(self, name: str = "recording", kind: str = "log") -> None:
        self.name = name
        self.kind = kind
        self.delivered = []
        self.announced = []
        self.fail = False

    def deliver(self, notification) -> None:
        if self.fail:
            from gate_agent.sinks import DeliveryFailed

            raise DeliveryFailed(f"{self.name}: refused on purpose")
        self.delivered.append(notification)

    def announce(self, subject: str, payload: dict) -> None:
        if self.fail:
            from gate_agent.sinks import DeliveryFailed

            raise DeliveryFailed(f"{self.name}: refused on purpose")
        self.announced.append((subject, payload))

    @property
    def payloads(self) -> list[dict]:
        return [notification.to_dict() for notification in self.delivered]

    @property
    def codes(self) -> list[tuple[str, str]]:
        return [(one["code"], one["transition"]) for one in self.payloads]
