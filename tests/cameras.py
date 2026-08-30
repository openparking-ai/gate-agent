"""A camera at the wire: a snapshot route on a socket, with real HTTP auth.

**Every image in this suite is SYNTHETIC and is generated here.** No photograph,
no plate, no frame from any device: a minimal JPEG header, a body a test chooses,
and an end-of-image marker. `check-no-real-data.js` refuses an image FILE
anywhere in this repository, and this is the other half of that rule -- there is
no fixture image to refuse, because the bytes are built in the process that uses
them.

**The authentication is real, and that is the point of this file.** The one
camera implementation this round supports presents its credential through the
challenge-response the camera asks for: the first request carries none, the
camera answers `401` with `WWW-Authenticate`, and the client answers it. A fake
that accepted a credential on the first request, or that read one out of a query
string, would let a client that could do neither pass every test here. So this
server issues a real Digest challenge with a nonce and verifies the response
hash, and a Basic one where a test asks for Basic.

Every request is RECORDED, method and path, and so is every `Authorization`
header -- so a test can ask what actually arrived at the camera rather than what
the process says it did.
"""

from __future__ import annotations

import hashlib
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

#: A minimal JPEG: Start-Of-Image, a JFIF APP0 segment, and End-Of-Image. It is
#: not a picture of anything and nothing in this repository decodes it -- what is
#: measured against it is the bytes.
JPEG_HEADER = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
JPEG_END = b"\xff\xd9"


def jpeg(body: bytes = b"") -> bytes:
    """A synthetic JPEG carrying `body`, so two of them can be made to differ."""
    return JPEG_HEADER + body + JPEG_END


def _md5(text: str) -> str:
    # Digest access authentication is specified on MD5. It is not being used as
    # a security primitive here -- it is the wire format the camera speaks, and
    # what makes it a credential is that it is never sent in the clear.
    return hashlib.md5(text.encode("utf-8"), usedforsecurity=False).hexdigest()


class FakeCamera:
    """One snapshot route. What it answers is whatever a test set.

    `body` is the JPEG it returns. `status` overrides it with an HTTP status, so
    the two halves of a failed read -- nothing came back, and it answered NO --
    can each be produced on purpose rather than waited for.
    """

    realm = "camera"
    nonce = "0a0b0c0d"

    def __init__(
        self,
        body: bytes | None = None,
        username: str | None = "operator",
        password: str | None = "s3cret",
        scheme: str = "digest",
        status: int | None = None,
    ) -> None:
        self.body = jpeg(b"one") if body is None else body
        self.username = username
        self.password = password
        self.scheme = scheme
        self.status = status
        self.requests: list[tuple[str, str]] = []
        #: Every Authorization header seen, in order, so a test can prove the
        #: credential was NOT on the first request and WAS on the retry.
        self.authorizations: list[str | None] = []

    def challenge(self) -> str:
        if self.scheme == "basic":
            return f'Basic realm="{self.realm}"'
        return f'Digest realm="{self.realm}", nonce="{self.nonce}", qop="auth"'

    def accepts(self, header: str | None, method: str, path: str) -> bool:
        """Whether this credential is the one configured, checked properly."""
        if self.username is None:
            return True
        if not header:
            return False
        if self.scheme == "basic":
            import base64

            expected = base64.b64encode(
                f"{self.username}:{self.password}".encode()
            ).decode()
            return header == f"Basic {expected}"
        if not header.startswith("Digest "):
            return False
        fields = {}
        for part in header[len("Digest ") :].split(","):
            key, _, value = part.strip().partition("=")
            fields[key] = value.strip('"')
        if fields.get("username") != self.username or fields.get("nonce") != self.nonce:
            return False
        ha1 = _md5(f"{self.username}:{self.realm}:{self.password}")
        ha2 = _md5(f"{method}:{fields.get('uri', path)}")
        if fields.get("qop"):
            expected = _md5(
                f"{ha1}:{self.nonce}:{fields.get('nc', '')}:{fields.get('cnonce', '')}:"
                f"{fields['qop']}:{ha2}"
            )
        else:
            expected = _md5(f"{ha1}:{self.nonce}:{ha2}")
        return fields.get("response") == expected


class _Handler(BaseHTTPRequestHandler):
    camera: FakeCamera

    server_version = "fake-camera"
    sys_version = ""

    def log_message(self, fmt: str, *args) -> None:
        pass

    def _record(self) -> None:
        self.camera.requests.append((self.command, urlparse(self.path).path))
        self.camera.authorizations.append(self.headers.get("Authorization"))

    def do_GET(self) -> None:  # noqa: N802
        self._record()
        camera = self.camera
        if not camera.accepts(
            self.headers.get("Authorization"), self.command, urlparse(self.path).path
        ):
            self.send_response(401)
            self.send_header("WWW-Authenticate", camera.challenge())
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if camera.status is not None:
            self.send_response(camera.status)
            if 300 <= camera.status < 400:
                self.send_header("Location", "http://elsewhere.invalid/snap.jpg")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", "image/jpeg")
        self.send_header("Content-Length", str(len(camera.body)))
        self.end_headers()
        self.wfile.write(camera.body)

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


def camera_server(camera: FakeCamera, host: str = "127.0.0.1", port: int = 0):
    return ThreadingHTTPServer((host, port), type("_Bound", (_Handler,), {"camera": camera}))


class _DripHandler(BaseHTTPRequestHandler):
    """A camera that ANSWERS, and takes far longer than its timeout to finish.

    It is the hostile case a socket timeout cannot see: every individual read
    comes back well inside the timeout, so no single operation ever waits long
    enough to trip one, while the body as a whole takes `body * drip` seconds to
    arrive. Against a timeout an order of magnitude shorter, a client with a
    deadline over the read abandons it and a client with only a socket timeout
    reads the whole thing and calls it a picture.

    **It is BOUNDED on purpose.** A camera that dripped for ever would make a
    client with no deadline HANG rather than fail, and a control that hangs is
    not a control -- the fail-control that reverts the deadline has to go red,
    not stop. The body it eventually sends is a real synthetic JPEG.
    """

    body: int = 200
    drip: float = 0.02

    server_version = "drip-camera"
    sys_version = ""

    def log_message(self, fmt: str, *args) -> None:
        pass

    def do_GET(self) -> None:  # noqa: N802
        payload = JPEG_HEADER + b"\x00" * self.body + JPEG_END
        self.send_response(200)
        self.send_header("Content-Type", "image/jpeg")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        try:
            self.wfile.write(JPEG_HEADER)
            self.wfile.flush()
            for byte in payload[len(JPEG_HEADER) :]:
                time.sleep(self.drip)
                self.wfile.write(bytes([byte]))
                self.wfile.flush()
        except OSError:
            # The client stopped reading. That is the whole point of the test
            # that uses this, and it is not an error here.
            pass


def drip_camera_server(
    host: str = "127.0.0.1", port: int = 0, drip: float = 0.02, body: int = 200
):
    """A camera whose answer takes `body * drip` seconds to arrive."""
    return ThreadingHTTPServer(
        (host, port), type("_BoundDrip", (_DripHandler,), {"drip": drip, "body": body})
    )


__all__ = [
    "JPEG_END",
    "JPEG_HEADER",
    "FakeCamera",
    "camera_server",
    "drip_camera_server",
    "jpeg",
]
