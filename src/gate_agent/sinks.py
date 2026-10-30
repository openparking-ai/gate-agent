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

**Every delivery answers with what the far end said.** `deliver` returns a
`Receipt` -- the SMTP server's reply to the message, the webhook receiver's
status -- and a failure raises `DeliveryFailed` carrying the same evidence and a
plain reason. The monitor keeps both on the message record, which is how "who
received, and when" is answered from this box rather than from somebody's inbox.

**No email address ever reaches a log line from here.** A failure's reason is
built from what the SMTP library and the server said, and both quote recipients
back; it is reduced by `records.redact_addresses` inside `DeliveryFailed`, before
anything can print it. The addresses a site configured are on the message record
and nowhere else.

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
from dataclasses import dataclass
from email.message import EmailMessage
from urllib.parse import urlsplit

from .config import EmailSinkConfig, LogSinkConfig, WebhookSinkConfig
from .contract import Notification
from .records import redact_addresses
from .redirects import open_url

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

    The message is REDUCED HERE, once: every email-shaped string in it becomes
    `<address>`, because this text is logged and the library it came from quotes
    recipients. `reply_code` and `http_status` are the far end's answer where
    there was one, for the record.
    """

    def __init__(
        self, message: str, *, reply_code: int | None = None, http_status: int | None = None
    ) -> None:
        super().__init__(redact_addresses(message))
        self.reply_code = reply_code
        self.http_status = http_status


@dataclass(frozen=True, slots=True)
class Receipt:
    """What a sink's far end answered when it TOOK a message. Evidence, kept.

    Every field is `None` where that sink has no such answer -- the log sink's
    receipt is empty, because "written" is the whole of what it can say -- and
    a field is never filled with a value nobody answered.
    """

    #: The SMTP server's reply to the message itself, and its text, reduced.
    reply_code: int | None = None
    reply: str | None = None
    #: The webhook receiver's status.
    http_status: int | None = None
    #: Recipients the SMTP server refused while accepting the message for the
    #: rest. `send_message` returns them rather than raising, so a partial
    #: acceptance used to be counted as everybody told.
    refused_recipients: tuple[str, ...] = ()


class LogSink:
    """Structured JSON to stdout. One line, one notification.

    JSON and not prose because the reader is usually a collector, and a line a
    machine can key on is what makes this sink worth having at a site with no
    email relay. Written to stdout directly rather than through `logging` so a
    deployment that turns log levels down cannot silently turn the only
    always-on sink off.
    """

    kind = "log"
    #: Nobody is behind it. A site whose only sink this is pages nobody, and
    #: the message record says so with an empty list here.
    recipients: tuple[str, ...] = ()

    def __init__(self, config: LogSinkConfig) -> None:
        self.name = config.name
        self._stream = sys.stdout

    def deliver(self, notification: Notification) -> Receipt:
        self._write(notification.to_dict())
        return Receipt()

    def announce(self, subject: str, payload: dict) -> Receipt:
        self._write({"subject": subject, **payload})
        return Receipt()

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

    @property
    def recipients(self) -> tuple[str, ...]:
        """The addresses as configured. For the message RECORD, never a log line."""
        return tuple(self.config.recipients)

    def deliver(self, notification: Notification) -> Receipt:
        payload = notification.to_dict()
        return self._send(self.subject_for(payload), payload)

    @staticmethod
    def subject_for(payload: dict) -> str:
        """What a person sees on a phone at 3am, and it names the MACHINE.

        Two codes are spelt the same on two surfaces -- `platform_unreachable`
        and `lane_gone_quiet` are both a lane's word for something and this
        monitor's word for something else -- so a subject of site, code and
        transition alone was byte-identical whether the fault was a lane's
        uplink or this monitor's own. The `target` is what separates them, and
        it belongs in the line somebody reads before they open anything.
        """
        where = payload["target"]
        if payload.get("subject"):
            where = f"{where}/{payload['subject']}"
        return f"[{payload['site_id']}] {payload['code']} {payload['transition']} ({where})"

    def announce(self, subject: str, payload: dict) -> Receipt:
        return self._send(subject, payload)

    def _send(self, subject: str, payload: dict) -> Receipt:
        """Send, and return what the server ANSWERED -- or raise, with why.

        Every refusal is spelt from the server's code and its own words, never
        from the exception's `str`: `SMTPRecipientsRefused` prints the address
        it refused and `SMTPSenderRefused` the sender, and this reason is
        logged. `DeliveryFailed` reduces whatever is left.
        """
        message = self.compose(subject, payload)
        try:
            with self._open(self.config) as server:
                refused = server.send_message(message) or {}
                reply = getattr(server, "data_reply", None)
        except smtplib.SMTPRecipientsRefused as exc:
            codes = sorted({code for code, _ in exc.recipients.values()})
            raise DeliveryFailed(
                f"{self.name}: the server refused every recipient (SMTP {codes})",
                reply_code=codes[0] if codes else None,
            ) from exc
        except smtplib.SMTPResponseException as exc:
            raise DeliveryFailed(
                f"{self.name}: the server answered SMTP {exc.smtp_code}: "
                f"{_reply_text(exc.smtp_error)}",
                reply_code=exc.smtp_code,
            ) from exc
        except (OSError, smtplib.SMTPException) as exc:
            raise DeliveryFailed(f"{self.name}: {exc}") from exc
        code, text = reply if isinstance(reply, tuple) and len(reply) == 2 else (None, None)
        return Receipt(
            reply_code=code if isinstance(code, int) else None,
            reply=redact_addresses(_reply_text(text)) if text is not None else None,
            refused_recipients=tuple(sorted(str(one) for one in refused)),
        )

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

    @property
    def recipients(self) -> tuple[str, ...]:
        """The HOST the POST goes to, and nothing else of the URL.

        Not the path and not the query: a paging service's hook URL commonly
        carries its token in one of them, and this goes on a read route.
        """
        host = urlsplit(self.config.url).hostname
        return (host,) if host else ()

    def __init__(self, config: WebhookSinkConfig, opener=None) -> None:
        self.name = config.name
        self.config = config
        # The opener that refuses a 3xx. A redirected POST used to report
        # SUCCESS on a delivery that never happened, and carry this sink's
        # bearer token to whichever host the receiver named -- so the one piece
        # of machinery that exists to notice a sink failing was the piece it
        # walked past. See `redirects.py`.
        self._open = opener or open_url

    def deliver(self, notification: Notification) -> Receipt:
        return self._post({"kind": "transition", **notification.to_dict()})

    def announce(self, subject: str, payload: dict) -> Receipt:
        return self._post({"kind": "unmeasured", "subject": subject, **payload})

    def _post(self, body: dict) -> Receipt:
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
        except urllib.error.HTTPError as exc:
            # A 3xx arrives here too, because nothing is followed. A paging
            # endpoint that answers `Location` has not been paged, and reporting
            # that as delivered is how a sink comes to tell nobody for a month.
            raise DeliveryFailed(
                f"{self.name}: answered HTTP {exc.code}", http_status=exc.code
            ) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise DeliveryFailed(f"{self.name}: {exc}") from exc
        if not 200 <= status < 300:
            # A 4xx from a paging endpoint is a delivery that did not happen.
            # Treating a status nobody looked at as success is how a webhook
            # sink comes to page nobody for a month.
            raise DeliveryFailed(f"{self.name}: answered HTTP {status}", http_status=status)
        return Receipt(http_status=status)


class _ReplyKeepingSMTP(smtplib.SMTP):
    """`smtplib.SMTP`, keeping the server's answer to the message itself.

    `send_message` raises on any reply to DATA but 250 and returns nothing about
    the 250 it got -- the line a mail server puts its queue id in, which is the
    one thing that lets somebody find this message in that server's own log.
    So it is kept, as the server sent it, for the record.
    """

    data_reply: tuple[int, bytes] | None = None

    def data(self, msg):
        reply = super().data(msg)
        self.data_reply = reply
        return reply


def _smtp(config: EmailSinkConfig):
    server = _ReplyKeepingSMTP(config.host, config.port, timeout=SMTP_TIMEOUT)
    if config.tls:
        server.starttls()
    return server


def _reply_text(value) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace").strip()
    return str(value).strip()


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


__all__ = ["DeliveryFailed", "EmailSink", "LogSink", "Receipt", "WebhookSink", "build"]
