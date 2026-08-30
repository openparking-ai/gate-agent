"""The capture process's local service. Four routes, all `GET`, and no fifth.

    GET /v1/capture                    who it is, what it is set to do
    GET /v1/capture/health             every capture code, and what is on the disk
    GET /v1/capture/records?since=N    the sidecars, never the bytes
    GET /v1/capture/images/<id>        one JPEG

There is deliberately no route that changes anything -- no route that captures on
demand, none that deletes a record, and none that moves the retention window.
`ACT_ROUTES` is empty and every method other than `GET` is answered by one shared
refusal, swept exactly as the monitor's is.

**A record is deleted by the retention rule and by nothing else.** A delete route
would be a way to remove the one image that mattered, from a store whose entire
purpose is that the entries can be reconstructed afterwards, and it would need an
authorisation model this package does not have.

**Local, always, and this one publishes photographs.** It binds loopback by
default, and off loopback it refuses to start without a shared token:
`InsecureBind`, imported from `service` rather than restated -- one rule about
when a credential is required, on a device that may be running three surfaces.
The exposure here is the sharpest of the three. The monitor publishes which of a
site's lanes are broken; this publishes PICTURES OF CARS AND WHEN THEY WERE
TAKEN. **The token is required on every route including the images**, which is
stated because an image route left open "because it is just a JPEG" is the whole
store readable by anyone who can enumerate a record id.
"""

from __future__ import annotations

import hmac
import json
import logging
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

from .contract import CONTRACT_VERSION
from .service import InsecureBind, assert_bind_allowed, bearer, is_loopback

log = logging.getLogger(__name__)

READ_ROUTES: tuple[str, ...] = (
    "/v1/capture",
    "/v1/capture/health",
    "/v1/capture/records",
)

#: The prefix the image route serves under. Not in `READ_ROUTES` because it is
#: the only route with a variable in it and a sweep that treated it as a fixed
#: path would ask for a record called nothing.
IMAGES_PREFIX = "/v1/capture/images/"

#: Routes that CHANGE something. EMPTY. Nothing in this package can capture on
#: demand, delete a record, or move a retention window over a socket.
ACT_ROUTES: tuple[str, ...] = ()

MAX_QUERY_CURSOR = 2**63 - 1


class CaptureService:
    """A `CaptureProcess`, read through the contract.

    It holds nothing of its own. Every answer is computed from the process at
    the moment it is asked, so there is no second copy of anything to go stale --
    and the store's figures are read off the disk on the request that asks for
    them.
    """

    def __init__(self, process) -> None:
        self.process = process

    def describe(self):
        return self.process.describe()

    def health(self):
        return self.process.health()

    def records(self, since: int):
        return self.process.records(since)

    def image(self, record_id: str):
        return self.process.image(record_id)


class _Handler(BaseHTTPRequestHandler):
    service: CaptureService
    token: str | None = None

    server_version = "openparking-gate-agent"
    sys_version = ""

    def log_message(self, fmt: str, *args) -> None:
        log.debug("%s - %s", self.address_string(), fmt % args)

    def do_GET(self) -> None:  # noqa: N802  (http.server's spelling)
        url = urlparse(self.path)
        if not self._authorised():
            return self._unauthorised()

        if url.path == "/v1/capture":
            return self._json(200, self.service.describe().to_dict())

        if url.path == "/v1/capture/health":
            return self._json(200, self.service.health().to_dict())

        if url.path == "/v1/capture/records":
            raw = parse_qs(url.query).get("since", ["0"])[0]
            try:
                since = int(raw)
            except ValueError:
                return self._json(400, {"error": f"since must be an integer, got {raw!r}"})
            if since < 0 or since > MAX_QUERY_CURSOR:
                return self._json(400, {"error": f"since is out of range: {raw!r}"})
            return self._json(200, self.service.records(since).to_dict())

        if url.path.startswith(IMAGES_PREFIX):
            # The id is LOOKED UP in the store's index. It is never joined onto
            # the directory, so `../` in it finds no record rather than finding
            # a file: the only paths this process opens are ones it wrote.
            record_id = unquote(url.path[len(IMAGES_PREFIX) :])
            body = self.service.image(record_id)
            if body is None:
                return self._json(404, {"error": "no such record"})
            return self._image(body)

        return self._json(404, {"error": "no such route"})

    # --- every other method, through ONE refusal --------------------------

    def _method_not_allowed(self) -> None:
        """The single refusal every non-GET method is answered by.

        Spelled once and shared, so the contract test can sweep the handler and
        require that every `do_*` other than `do_GET` IS this function.
        """
        self.send_response(405)
        self.send_header("Allow", "GET")
        body = json.dumps(
            {
                "error": "this surface is read-only; a capture process changes nothing, here "
                "or anywhere else. It reads a camera, reads a lane, and writes to its own "
                "disk. A record is deleted by the retention rule and by nothing else.",
                "contract_version": CONTRACT_VERSION,
            }
        ).encode("utf-8")
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_POST = _method_not_allowed  # noqa: N815
    do_PUT = _method_not_allowed  # noqa: N815
    do_PATCH = _method_not_allowed  # noqa: N815
    do_DELETE = _method_not_allowed  # noqa: N815

    # --- plumbing ---------------------------------------------------------

    def _authorised(self) -> bool:
        """Compared in constant time, because a token is a secret.

        `hmac.compare_digest` and not `==`: the ordinary comparison returns as
        soon as two bytes differ, and that timing is enough to recover a token
        one character at a time from a machine on the same LAN -- which is
        exactly the machine this credential exists to keep away from a store of
        photographs.
        """
        if self.token is None:
            return True
        presented = bearer(self.headers.get("Authorization"))
        return presented is not None and hmac.compare_digest(presented, self.token)

    def _unauthorised(self) -> None:
        self.send_response(401)
        self.send_header("WWW-Authenticate", "Bearer")
        body = json.dumps({"error": "a bearer token is required for this route"}).encode("utf-8")
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _image(self, body: bytes) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "image/jpeg")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def make_server(service: CaptureService, host: str = "127.0.0.1", port: int = 8093, token=None):
    """Bound to loopback by default. Exposing it is a deployment decision."""
    assert_bind_allowed(host, port, token)
    handler = type("_BoundHandler", (_Handler,), {"service": service, "token": token or None})
    return ThreadingHTTPServer((host, port), handler)


__all__ = [
    "ACT_ROUTES",
    "IMAGES_PREFIX",
    "READ_ROUTES",
    "CaptureService",
    "InsecureBind",
    "assert_bind_allowed",
    "is_loopback",
    "make_server",
]
