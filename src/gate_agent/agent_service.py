"""The agent's own local service. Three routes, all `GET`, and no fourth.

    GET /v1/agent                 who it is, what it answers, and `can_vend`
    GET /v1/agent/health          every agent code, every time
    GET /v1/agent/events?since=N  what it did, and never a plate

**There is deliberately no route that changes anything ON THIS SURFACE, and that
is the whole point of it.** `ACT_ROUTES` is empty; every method other than `GET`
is answered by one shared refusal, swept exactly as the monitor's and the
capture process's are. A route here that opened a barrier would be the thing
every outside reviewer of this project has named, reachable by anything that can
open a TCP connection to a gate controller.

**That is a statement about THIS SURFACE and not about the package.** From round
7 the agent can command a vend at a lane it holds an act token for and can pulse
a standalone intercom's relay -- `docs/CONTRACT.md`, "IT CAN NOW COMMAND A
VEND", is where that is described. What is true here is narrower and it is the
part that matters for a socket: nothing a CALLER of this surface sends can move
a barrier.

`can_vend` is on the first route, per lane, DERIVED from the act table and the
act token rather than written down -- so it cannot say `true` for a lane this
process holds nothing for, and cannot say `false` while it does.

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

#: Routes that CHANGE something. EMPTY, and it is empty by decision rather than
#: by incapacity: from round 7 this package DOES hold a client that can build a
#: `POST` (`act.py`, at a lane, with an act token). What is refused here is a
#: CALLER of this surface reaching it. A route added here would hand that client
#: to anything that can open a socket to the box.
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

    def act_surface(self) -> tuple[str, ...]:
        """What the agent behind this surface can ask a barrier to do.

        Passed straight through from `AgentConfig.act_surface` -- the ONE place
        this package says it, which the startup banner renders too. Nothing here
        decides anything; a second opinion is how the two come to disagree.
        """
        return self.agent.config.act_surface


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

        **The body was a fixed sentence and it went false.** It read "this agent
        opens nothing, here or at any lane" -- true until round 7 gave this
        package a vend route, and served over HTTP to any caller for the whole of
        the round that made it false. It is now DERIVED from
        `AgentConfig.act_surface`, the same property the startup banner renders,
        so what a caller is told about this process is what the process can
        actually do. The first clause is unchanged and is about the SURFACE,
        which is what a caller asked about.

        **It exposes no more than the route beside it already does.** The lane
        names and intercom URIs in the derived half are on `GET /v1/agent` in
        full, and this surface is loopback-only unless a site gave it a token --
        so a caller who can reach a `405` here can already read them. No
        credential, no `ticket_ref` and no act token is in it, which is the
        round-6 rule and is swept.
        """
        self.send_response(405)
        self.send_header("Allow", "GET")
        surface = self.service.act_surface()
        if surface:
            can = (
                "this process can ask a barrier to move where it was configured to: "
                + "; ".join(surface)
            )
        else:
            can = "nothing in this configuration can ask a barrier to move"
        body = json.dumps(
            {
                "error": "this surface is read-only and no request to it moves anything. "
                f"Separately, and not through this surface: {can}.",
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
