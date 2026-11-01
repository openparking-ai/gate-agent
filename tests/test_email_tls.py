"""OP-4-7. The email sink's STARTTLS verifies the server it is talking to.

`smtplib.SMTP.starttls()` with no context encrypts to whoever answers: the
certificate is not checked and neither is the host name. Anybody on the path
could answer as the mail server, read every page, and drop it.
"""

from __future__ import annotations

import socketserver
import ssl
import subprocess
import threading

import pytest

from gate_agent.config import EmailSinkConfig
from gate_agent.sinks import DeliveryFailed, EmailSink


@pytest.fixture(scope="module")
def self_signed(tmp_path_factory):
    """A certificate for 127.0.0.1 that no system store trusts."""
    where = tmp_path_factory.mktemp("tls")
    cert, key = where / "cert.pem", where / "key.pem"
    subprocess.run(
        ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "2",
         "-subj", "/CN=127.0.0.1", "-addext", "subjectAltName=IP:127.0.0.1",
         "-keyout", str(key), "-out", str(cert)],
        check=True, capture_output=True,
    )
    return cert, key


def _server(cert, key, received: list):
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert, key)

    class Conversation(socketserver.BaseRequestHandler):
        def handle(self):
            stream = self.request
            reader = stream.makefile("rb")

            def say(line):
                stream.sendall((line + "\r\n").encode())

            say("220 smtp.example.com ESMTP")
            data = False
            while True:
                line = reader.readline()
                if not line:
                    return
                text = line.decode("utf-8", "replace").rstrip("\r\n")
                if data:
                    if text == ".":
                        data = False
                        received.append("message")
                        say("250 2.0.0 Ok: queued")
                    continue
                verb = text.split(" ", 1)[0].upper()
                if verb == "EHLO":
                    stream.sendall(b"250-smtp.example.com\r\n250 STARTTLS\r\n")
                elif verb == "STARTTLS":
                    say("220 2.0.0 Ready")
                    try:
                        stream = context.wrap_socket(self.request, server_side=True)
                    except (ssl.SSLError, OSError):
                        return
                    reader = stream.makefile("rb")
                elif verb == "DATA":
                    data = True
                    say("354 go ahead")
                elif verb == "QUIT":
                    say("221 Bye")
                    return
                else:
                    say("250 Ok")

    server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), Conversation)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def _sink(port):
    return EmailSink(EmailSinkConfig(
        host="127.0.0.1", port=port, sender="monitor@example.com",
        recipients=("night-desk@example.com",), tls=True,
    ))


PAYLOAD = {"site_id": "site-1", "code": "lane_unreachable", "transition": "raised",
           "target": "lane"}


def test_a_server_whose_certificate_does_not_verify_is_refused(self_signed):
    received: list = []
    server = _server(*self_signed, received)
    try:
        with pytest.raises(DeliveryFailed) as refused:
            _sink(server.server_address[1]).announce("s", PAYLOAD)
    finally:
        server.shutdown()
        server.server_close()
    assert "did not verify" in str(refused.value)
    assert refused.value.transient is False
    assert received == [], "the message went to a server nobody verified"


def test_the_control_a_trusted_certificate_delivers(self_signed, monkeypatch):
    cert, _key = self_signed
    trusting = ssl.create_default_context

    monkeypatch.setattr(ssl, "create_default_context", lambda: trusting(cafile=str(cert)))
    received: list = []
    server = _server(*self_signed, received)
    try:
        _sink(server.server_address[1]).announce("s", PAYLOAD)
    finally:
        server.shutdown()
        server.server_close()
    assert received == ["message"]
