"""The agent's own local service. Three routes, all `GET`, and no fourth.

    GET /v1/agent                 who it is, what it answers, and `can_vend`
    GET /v1/agent/health          every agent code, every time
    GET /v1/agent/events?since=N  what it did, and never a plate

**There is deliberately no route that changes anything, and on this surface that
is the whole point of the round.** `ACT_ROUTES` is empty; every method other than
`GET` is answered by one shared refusal, swept exactly as the monitor's and the
capture process's are. A route here that opened a barrier would be the thing
every outside reviewer of this project has named, reachable by anything that can
open a TCP connection to a gate controller.

`can_vend` is on the first route and it is `false`, DERIVED from an empty act
table rather than written down -- so it cannot say `false` while something else
in this package can act.

**Local, always.** Loopback by default; off loopback it refuses to start without
a shared token -- `InsecureBind`, imported from `service` rather than restated,
one rule for all three surfaces on one device. The exposure here is its own kind:
this publishes which intercoms a site has, which of its lanes cannot be read, and
when a human was called and did not answer. That is a timetable of when nobody is
watching a garage.
"""

from __future__ import annotations

import hmac
import json
import logging
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from .contract import CONTRACT_VERSION
from .service import InsecureBind, assert_bind_allowed, bearer, is_loopback

log = logging.getLogger(__name__)

READ_ROUTES: tuple[str, ...] = (
    "/v1/agent",
    "/v1/agent/health",
    "/v1/agent/events",
)

#: Routes that CHANGE something. EMPTY, and this module has no way to grow one
#: quietly: the agent holds no client capable of a method other than `GET`, so a
#: route here would have nothing behind it to call.
ACT_ROUTES: tuple[str, ...] = ()

MAX_QUERY_CURSOR = 2**63 - 1


class AgentService:
    """An `Agent`, read through the contract. It holds nothing of its own."""

    def __init__(self, agent) -> None:
        self.agent = agent

    def describe(self):
        return self.agent.describe()

    def health(self):
        return self.agent.health()

    def events(self, since: int):
        return self.agent.events(since)


class _Handler(BaseHTTPRequestHandler):
    service: AgentService
    token: str | None = None

    server_version = "openparking-gate-agent"
    sys_version = ""

    def log_message(self, fmt: str, *args) -> None:
        log.debug("%s - %s", self.address_string(), fmt % args)

    def do_GET(self) -> None:  # noqa: N802  (http.server's spelling)
        url = urlparse(self.path)
        if not self._authorised():
            return self._unauthorised()

        if url.path == "/v1/agent":
            return self._json(200, self.service.describe().to_dict())

        if url.path == "/v1/agent/health":
            return self._json(200, self.service.health().to_dict())

        if url.path == "/v1/agent/events":
            raw = parse_qs(url.query).get("since", ["0"])[0]
            try:
                since = int(raw)
            except ValueError:
                return self._json(400, {"error": f"since must be an integer, got {raw!r}"})
            if since < 0 or since > MAX_QUERY_CURSOR:
                return self._json(400, {"error": f"since is out of range: {raw!r}"})
            return self._json(200, self.service.events(since).to_dict())

        return self._json(404, {"error": "no such route"})

    def _method_not_allowed(self) -> None:
        """The single refusal every non-GET method is answered by.

        Spelt once and shared, so the contract test can sweep the handler and
        require that every `do_*` other than `do_GET` IS this function -- a route
        that opened a barrier would have to stop being it.
        """
        self.send_response(405)
        self.send_header("Allow", "GET")
        body = json.dumps(
            {
                "error": "this surface is read-only; this agent opens nothing, here or at "
                "any lane. An authorisation is a record of what a person said.",
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

    def _authorised(self) -> bool:
        """Compared in constant time, because a token is a secret."""
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


def make_server(service: AgentService, host: str = "127.0.0.1", port: int = 8094, token=None):
    """Bound to loopback by default. Exposing it is a deployment decision."""
    assert_bind_allowed(host, port, token)
    handler = type("_BoundHandler", (_Handler,), {"service": service, "token": token or None})
    return ThreadingHTTPServer((host, port), handler)


__all__ = [
    "ACT_ROUTES",
    "READ_ROUTES",
    "AgentService",
    "InsecureBind",
    "is_loopback",
    "make_server",
]
