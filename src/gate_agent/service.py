"""The monitor's own local service. Three routes, all `GET`, and no fourth.

    GET /v1/monitor                 who this monitor is and what it watches
    GET /v1/monitor/health          its own codes, and every target's
    GET /v1/monitor/events?since=N  the notifications it sent

There is deliberately no route that changes anything. `ACT_ROUTES` below is
empty and every method other than `GET` is answered by one shared refusal, swept
by `tests/test_monitor_contract.py` exactly as the lane's is -- so a route that
mutated something would have to stop being that function, and the sweep goes red
in the same commit.

**Local, always.** It binds loopback by default. Off loopback it REFUSES to start
without a shared token: `InsecureBind`, the same rule and the same shape the lane
service and the Vehicle ID service apply. The exposure here is its own kind. This
surface does not publish where a vehicle was; it publishes which of a site's
lanes are broken, which cameras are dark and when nobody was told -- which is a
map for whoever wants to arrive while the equipment that would have noticed is
down.

`is_loopback` below is COPIED from `lane_controller.service`, character for
character, not re-derived. `tests/test_copied_not_rederived.py` reads both out of
the two installed packages and requires them to be identical, and requires the
two `assert_bind_allowed` implementations to agree on every case in a table. One
rule about when a credential is required, on a device that may be running both.
"""

from __future__ import annotations

import hmac
import ipaddress
import json
import logging
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from .contract import CONTRACT_VERSION

log = logging.getLogger(__name__)

READ_ROUTES: tuple[str, ...] = (
    "/v1/monitor",
    "/v1/monitor/health",
    "/v1/monitor/events",
)

#: Routes that CHANGE something. EMPTY, and this module has no way to grow one
#: quietly: the monitor holds no client capable of a method other than `GET`, so
#: a route here would have nothing behind it to call.
ACT_ROUTES: tuple[str, ...] = ()

MAX_QUERY_CURSOR = 2**63 - 1


class InsecureBind(Exception):
    """A bind that would expose this monitor's view with nothing in front of it.

    Raised before the socket is created. Copied from the lane service rather
    than re-derived: one rule, the same words, so two surfaces on one device
    cannot come to disagree about when a credential is required.
    """


def is_loopback(host: str) -> bool:
    """Whether binding to `host` keeps the service on this machine.

    Anything this cannot PROVE is loopback counts as not loopback. A hostname
    resolves at bind time, can resolve to more than one address, and can change
    under the service, so guessing here would mean guessing on the one question
    that decides whether a credential is required. `''` is every interface and
    is the widest of the lot.
    """
    if host in ("localhost", "localhost.localdomain"):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def assert_bind_allowed(host: str, port: int, token: str | None) -> None:
    """Raise unless this host may be bound with this credential.

    One implementation, called by `make_server` -- so the rule holds for every
    caller of this package -- and by the CLI before it builds anything, so a
    misconfiguration is reported in the moment.
    """
    if is_loopback(host) or token:
        return
    raise InsecureBind(
        f"refusing to bind {host or 'every interface'}:{port} with no token. Off loopback "
        "anything that can reach this port can read which of this site's lanes are broken, "
        "which cameras are dark, and when nobody was told. Configure a shared token with "
        "--auth-token-file, or bind 127.0.0.1."
    )


def bearer(header: str | None) -> str | None:
    """The token out of an Authorization header, or None if there is not one."""
    if not header:
        return None
    scheme, _, value = header.partition(" ")
    if scheme.lower() != "bearer" or not value.strip():
        return None
    return value.strip()


class MonitorService:
    """A `Monitor`, read through the contract.

    It holds nothing of its own. Every answer is computed from the monitor at
    the moment it is asked, so there is no second copy of anything to go stale.
    """

    def __init__(self, monitor) -> None:
        self.monitor = monitor

    def describe(self):
        return self.monitor.describe()

    def health(self):
        return self.monitor.health()

    def events(self, since: int):
        return self.monitor.events(since)


class _Handler(BaseHTTPRequestHandler):
    service: MonitorService
    token: str | None = None

    server_version = "openparking-gate-agent"
    sys_version = ""

    def log_message(self, fmt: str, *args) -> None:
        log.debug("%s - %s", self.address_string(), fmt % args)

    def do_GET(self) -> None:  # noqa: N802  (http.server's spelling)
        url = urlparse(self.path)
        if not self._authorised():
            return self._unauthorised()

        if url.path == "/v1/monitor":
            return self._json(200, self.service.describe().to_dict())

        if url.path == "/v1/monitor/health":
            return self._json(200, self.service.health().to_dict())

        if url.path == "/v1/monitor/events":
            raw = parse_qs(url.query).get("since", ["0"])[0]
            try:
                since = int(raw)
            except ValueError:
                return self._json(400, {"error": f"since must be an integer, got {raw!r}"})
            if since < 0 or since > MAX_QUERY_CURSOR:
                return self._json(400, {"error": f"since is out of range: {raw!r}"})
            return self._json(200, self.service.events(since).to_dict())

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
                "error": "this surface is read-only; a monitor changes nothing, here or "
                "anywhere else. It reads GETs and sends messages.",
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
        exactly the machine this credential exists to keep out.
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


def make_server(service: MonitorService, host: str = "127.0.0.1", port: int = 8092, token=None):
    """Bound to loopback by default. Exposing it is a deployment decision."""
    assert_bind_allowed(host, port, token)
    handler = type("_BoundHandler", (_Handler,), {"service": service, "token": token or None})
    return ThreadingHTTPServer((host, port), handler)


__all__ = [
    "ACT_ROUTES",
    "READ_ROUTES",
    "InsecureBind",
    "MonitorService",
    "assert_bind_allowed",
    "bearer",
    "is_loopback",
    "make_server",
]
