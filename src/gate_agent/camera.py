"""The camera: one `GET`, one JPEG, and a credential that came out of a file.

**ONE real implementation this round: an HTTP JPEG snapshot.** A `GET` of a URL
that answers `image/jpeg`, with the credential presented through standard HTTP
authentication -- the challenge-response the camera itself asks for when it
answers `401`.

**There is no RTSP here, and that is a decision rather than a gap.** Reading a
stream means carrying a decoder, and this package has no dependencies at all --
it runs beside a lane, on a box in a gate housing, and every dependency is one
more thing to cross-compile, patch and have go wrong somewhere with no keyboard
attached. A snapshot route is a `GET` and the standard library already has one.

**A credential never goes in the URL.** Some cameras document their snapshot
route with the username and the password as query parameters; a camera whose
only documented way in is that one is named unsupported in `docs/CONTRACT.md`
rather than made to work by writing a password into a configuration file, a
backup of that file, a process's argument vector and every access log between
here and the camera. The refusal is the same code the rest of this package uses
(`config._refuse_userinfo`), and it fires at startup.

Standard library only, like every other module here.
"""

from __future__ import annotations

import logging
import urllib.error
import urllib.request

from .redirects import build_opener

log = logging.getLogger(__name__)

#: A snapshot larger than this is not a snapshot. A process that read an
#: unbounded body from a camera that had gone strange would become the second
#: thing that is down, and this one is meant to outlive what it photographs.
#: It is a CEILING on a read, not a statement about how big a picture is: this
#: package has never seen a capture from any camera and says nothing about the
#: size of one.
MAX_SNAPSHOT_BYTES = 32 << 20

#: The published default for how long a camera has to answer one snapshot.
#: A per-site SETTING and an ASSUMPTION -- nothing here measures how long a
#: camera takes to encode a frame. Drawn well above a local JPEG read and well
#: below the interval it is taken on, so a camera that has stopped answering is
#: reported rather than quietly consuming the next capture's turn.
DEFAULT_SNAPSHOT_TIMEOUT = 10.0

#: What a JPEG starts with. Two bytes of Start-Of-Image and the marker after
#: them. Checked because a camera answering an HTML login page with
#: `Content-Type: image/jpeg` is a camera that has not sent a picture, and a
#: store full of login pages is worse than a store full of nothing: it reads as
#: a working installation.
JPEG_MAGIC = b"\xff\xd8\xff"


class CameraUnreachable(Exception):
    """Nothing came back, or what came back was not a picture.

    ONE exception for both, for the reason `client.TargetUnreachable` gives: to
    the process asking, they are the same fact -- "I asked and I do not have an
    image" -- and a camera publishing something that is not a JPEG is not the
    healthier of the two.

    A **5xx** is here. The camera's process answered, and what it answered is
    that it could not take the picture; the repair is at the camera either way.
    """


class CameraRefusedUs(Exception):
    """It ANSWERED, and the answer was no. `status` is its own.

    A `401` is the credential in the file beside this process, and the camera is
    working perfectly. A `404` is a snapshot route this camera does not have. A
    `3xx` is a camera steering this process at another host, which is refused by
    the opener rather than followed. Three different repairs on three different
    machines, and folded into "the camera is unreachable" a human is sent to the
    wrong one with nothing in the message to tell them.
    """

    def __init__(self, message: str, status: int) -> None:
        super().__init__(message)
        self.status = status


class SnapshotCamera:
    """Reads one camera. Can do nothing else.

    The credential is presented the way the camera asks for it: the first
    request carries none, the camera answers `401` with a `WWW-Authenticate`
    header naming a scheme, and urllib's handlers answer that challenge. Digest
    and Basic are both installed, and WHICH is used is the camera's choice, not
    this file's -- a client that decided for itself would have to be told, per
    camera, in a configuration key nobody can answer correctly.

    The opener comes from `redirects.build_opener`, so this one does not follow
    a `Location` either. That matters more here than anywhere else in the
    package: the retry is the request that carries the credential.
    """

    def __init__(
        self,
        camera_id: str,
        snapshot_url: str,
        username: str | None = None,
        password: str | None = None,
        timeout: float = DEFAULT_SNAPSHOT_TIMEOUT,
    ) -> None:
        self.camera_id = camera_id
        self.snapshot_url = snapshot_url
        self.timeout = timeout
        handlers = []
        if username is not None and password is not None:
            manager = urllib.request.HTTPPasswordMgrWithDefaultRealm()
            manager.add_password(None, snapshot_url, username, password)
            handlers = [
                urllib.request.HTTPDigestAuthHandler(manager),
                urllib.request.HTTPBasicAuthHandler(manager),
            ]
        self._opener = build_opener(*handlers)

    def snapshot(self) -> bytes:
        """One picture, or one of the two refusals. There is no other method."""
        request = urllib.request.Request(self.snapshot_url, method="GET")
        try:
            with self._opener.open(request, timeout=self.timeout) as response:
                body = response.read(MAX_SNAPSHOT_BYTES + 1)
        except urllib.error.HTTPError as exc:
            # It ANSWERED. Which half of the fault that is depends on the
            # status, and the status is carried so a human can tell them apart.
            if exc.code >= 500:
                raise CameraUnreachable(f"{self.camera_id}: HTTP {exc.code}") from exc
            raise CameraRefusedUs(f"{self.camera_id}: HTTP {exc.code}", exc.code) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise CameraUnreachable(f"{self.camera_id}: {exc}") from exc
        if len(body) > MAX_SNAPSHOT_BYTES:
            raise CameraUnreachable(
                f"{self.camera_id}: answered more than {MAX_SNAPSHOT_BYTES} bytes"
            )
        if not body.startswith(JPEG_MAGIC):
            # A login page served as `image/jpeg`, an error document, an empty
            # body. Refused rather than stored: a store full of those reads as a
            # working installation right up until somebody opens one.
            raise CameraUnreachable(
                f"{self.camera_id}: answered {len(body)} bytes that do not begin as a JPEG"
            )
        return body


__all__ = [
    "DEFAULT_SNAPSHOT_TIMEOUT",
    "JPEG_MAGIC",
    "MAX_SNAPSHOT_BYTES",
    "CameraRefusedUs",
    "CameraUnreachable",
    "SnapshotCamera",
]
