"""How a human is told. Three sinks, and the set is CLOSED this version.

`log` is always on and is the only one that needs no provider, no credential and
no network. `email` and `webhook` are per-site and declared. SMS is a later
round's provider and is deliberately absent: the set being closed and published
is what makes adding one an additive change rather than a key that quietly
already works.

**A site with only `log` declared is valid, and it means NOBODY IS PAGED.** The
notifications go to this process's stdout and to whatever collects it. That is a
real configuration for a site whose logs are already watched, and it is said out
loud at startup and in the receipt rather than left to be discovered the first
time a lane goes down at midnight.

**Every sink is handed the SAME object.** There is no richer rendering held back
for one of them: the webhook posts `Notification.to_dict()` and the email is
built from the same fields, so a plate that is not on the notification cannot be
on either. That is not a convention -- it is why the plate sweep over sink output
means anything.

**This module POSTs, and it is the only one that may.** A webhook is how a third
party's paging system takes the seat, and it points away from the lane rather
than at it. `tests/test_no_opening_authority.py` requires that nothing here
imports the target client and that nothing here is reachable from a target's
URL, which is what keeps "the monitor cannot act on a lane" true while this file
exists.
"""

from __future__ import annotations

import json
import logging
import smtplib
import sys
import urllib.error
import urllib.request
from email.message import EmailMessage

from .config import EmailSinkConfig, LogSinkConfig, WebhookSinkConfig
from .contract import Notification

log = logging.getLogger(__name__)

#: A webhook that has not answered by now is not going to. Short, because a
#: monitor blocked on one sink is a monitor not polling anything -- and the
#: failure is itself reported, so a slow endpoint is visible rather than
#: swallowed.
WEBHOOK_TIMEOUT = 10.0
SMTP_TIMEOUT = 20.0


class DeliveryFailed(Exception):
    """A sink could not deliver, with what went wrong attached.

    Raised rather than returned, so a sink that forgets to check a result cannot
    exist. Every caller of `deliver` treats this as `sink_delivery_failed` for
    that sink, reports it on the monitor's own surface, and tells every OTHER
    sink that works -- never wrong silently applies to the messenger too.
    """


class LogSink:
    """Structured JSON to stdout. One line, one notification.

    JSON and not prose because the reader is usually a collector, and a line a
    machine can key on is what makes this sink worth having at a site with no
    email relay. Written to stdout directly rather than through `logging` so a
    deployment that turns log levels down cannot silently turn the only
    always-on sink off.
    """

    kind = "log"

    def __init__(self, config: LogSinkConfig) -> None:
        self.name = config.name
        self._stream = sys.stdout

    def deliver(self, notification: Notification) -> None:
        self._write(notification.to_dict())

    def announce(self, subject: str, payload: dict) -> None:
        self._write({"subject": subject, **payload})

    def _write(self, payload: dict) -> None:
        try:
            self._stream.write(json.dumps(payload, sort_keys=True, default=str) + "\n")
            self._stream.flush()
        except OSError as exc:
            raise DeliveryFailed(f"{self.name}: {exc}") from exc


class EmailSink:
    """SMTP, TLS by default, no credential in this version.

    The subject line carries the site, the code and what changed, because that is
    what a person sees on a phone at 3am before they open anything.
    """

    kind = "email"

    def __init__(self, config: EmailSinkConfig, opener=None) -> None:
        self.name = config.name
        self.config = config
        # Injectable so a test can exercise the whole path -- rendering,
        # recipients, failure -- without an SMTP server, and so the failure case
        # can be produced on purpose rather than waited for.
        self._open = opener or _smtp

    def deliver(self, notification: Notification) -> None:
        payload = notification.to_dict()
        self._send(
            f"[{payload['site_id']}] {payload['code']} {payload['transition']}", payload
        )

    def announce(self, subject: str, payload: dict) -> None:
        self._send(subject, payload)

    def _send(self, subject: str, payload: dict) -> None:
        message = self.compose(subject, payload)
        try:
            with self._open(self.config) as server:
                server.send_message(message)
        except (OSError, smtplib.SMTPException) as exc:
            raise DeliveryFailed(f"{self.name}: {exc}") from exc

    def compose(self, subject: str, payload: dict) -> EmailMessage:
        """The message, built from the notification and from nothing else.

        Every line below comes from the payload it was handed. Nothing is
        fetched to enrich it, which is what makes "the monitor never holds a
        plate" a property of this file too and not only of the polling code.
        """
        message = EmailMessage()
        message["Subject"] = subject
        message["From"] = self.config.sender
        message["To"] = ", ".join(self.config.recipients)
        message.set_content(_as_text(payload))
        return message


class WebhookSink:
    """A POST of the notification object, with a bearer token from a file.

    The body is `Notification.to_dict()` under `kind: "transition"` -- the same
    object the log sink prints and `GET /v1/monitor/events` serves. A third
    party's paging system therefore integrates against one shape, published in
    this module's contract, rather than against a rendering invented for it.

    `kind` is there because this endpoint receives two things: a transition, and
    the one startup message saying what is not being measured. A receiver that
    could not tell them apart would have to guess from which fields happened to
    be present, which is the shape of every quiet misread in this project.
    """

    kind = "webhook"

    def __init__(self, config: WebhookSinkConfig, opener=None) -> None:
        self.name = config.name
        self.config = config
        self._open = opener or urllib.request.urlopen

    def deliver(self, notification: Notification) -> None:
        self._post({"kind": "transition", **notification.to_dict()})

    def announce(self, subject: str, payload: dict) -> None:
        self._post({"kind": "unmeasured", "subject": subject, **payload})

    def _post(self, body: dict) -> None:
        request = urllib.request.Request(
            self.config.url,
            data=json.dumps(body, default=str).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.config.token}",
            },
            method="POST",
        )
        try:
            with self._open(request, timeout=WEBHOOK_TIMEOUT) as response:
                status = getattr(response, "status", 200)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise DeliveryFailed(f"{self.name}: {exc}") from exc
        if not 200 <= status < 300:
            # A 4xx from a paging endpoint is a delivery that did not happen.
            # Treating a status nobody looked at as success is how a webhook
            # sink comes to page nobody for a month.
            raise DeliveryFailed(f"{self.name}: answered HTTP {status}")


def _smtp(config: EmailSinkConfig):
    server = smtplib.SMTP(config.host, config.port, timeout=SMTP_TIMEOUT)
    if config.tls:
        server.starttls()
    return server


def _as_text(payload: dict) -> str:
    """The notification as lines a person reads. One field per line, all of them.

    Rendered from the payload's own keys rather than from a list written here,
    so a field added to `Notification` appears in the email in the same commit.
    A hand-written template is the copy that goes stale, and the thing it would
    drop is whichever field was added most recently.
    """
    return "\n".join(
        f"{key}: {value}" for key, value in sorted(payload.items()) if value is not None
    )


def build(config) -> object:
    """The sink for one declared configuration. The mapping lives here only."""
    if isinstance(config, LogSinkConfig):
        return LogSink(config)
    if isinstance(config, EmailSinkConfig):
        return EmailSink(config)
    if isinstance(config, WebhookSinkConfig):
        return WebhookSink(config)
    raise ValueError(f"no sink for {type(config).__name__}")


__all__ = ["DeliveryFailed", "EmailSink", "LogSink", "WebhookSink", "build"]
