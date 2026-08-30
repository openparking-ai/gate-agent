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
import time
import urllib.error
import urllib.request

from .contract import CameraUnreachableCause
from .redirects import build_opener

log = logging.getLogger(__name__)

#: The published default for `[capture] max_snapshot_bytes`: the most this
#: process will read from one camera before it stops reading.
#:
#: A CEILING ON A READ, and not a statement about how big a picture is -- this
#: package has never seen a capture from any camera and says nothing about the
#: size of one. It is a SETTING because of what it decides: the store evicts to
#: make room for what arrives, so a ceiling above `[capture] max_bytes` would
#: let a camera that had gone strange decide how much of a site's store
#: survives. Startup refuses a value that is not below `max_bytes`.
DEFAULT_MAX_SNAPSHOT_BYTES = 32 << 20

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

#: How much of a body is read between two looks at the deadline. Small enough
#: that a camera dripping bytes is abandoned within one chunk of its timeout,
#: large enough that a real snapshot is a handful of reads rather than a
#: thousand. It is a granularity, not a limit.
CHUNK_BYTES = 64 << 10


class CameraUnreachable(Exception):
    """Nothing came back, or what came back was not a picture.

    ONE exception for both, for the reason `client.TargetUnreachable` gives: to
    the process asking, they are the same fact -- "I asked and I do not have an
    image" -- and a camera publishing something that is not a JPEG is not the
    healthier of the two.

    A **5xx** is here. The camera's process answered, and what it answered is
    that it could not take the picture; the repair is at the camera either way.

    `cause` says WHICH of them it was, out of `CameraUnreachableCause`, and it
    goes on the wire beside the code. One fact to the process asking, four
    different repairs to the person sent to fix it.
    """

    def __init__(self, message: str, cause: CameraUnreachableCause) -> None:
        super().__init__(message)
        self.cause = cause


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
        max_snapshot_bytes: int = DEFAULT_MAX_SNAPSHOT_BYTES,
        clock=time.monotonic,
    ) -> None:
        self.camera_id = camera_id
        self.snapshot_url = snapshot_url
        self.timeout = timeout
        self.max_snapshot_bytes = max_snapshot_bytes
        #: MONOTONIC, and not the process's wall clock. The deadline below is a
        #: duration, and a duration measured against a clock that can be stepped
        #: by NTP is a deadline that can be moved by something outside this box.
        self._clock = clock
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
        """One picture, or one of the two refusals. There is no other method.

        **`timeout_seconds` IS A DEADLINE ON THE WHOLE READ, not a socket
        option.** `urllib`'s `timeout` bounds one socket operation: a camera
        that answers, declares a length and then sends one byte every quarter
        second never times out, because no single read waits long enough. This
        process runs ONE poller thread, so a camera doing that holds every other
        camera's capture and the lane's event poll behind it -- for as long as
        the camera chooses. The body is therefore read in CHUNKS against a wall
        of `timeout` seconds from the moment the request went out, and past it
        this stops reading and says `timeout`. One camera cannot hold this
        process for longer than that camera's own timeout.
        """
        request = urllib.request.Request(self.snapshot_url, method="GET")
        deadline = self._clock() + self.timeout
        ceiling = self.max_snapshot_bytes
        try:
            with self._opener.open(request, timeout=self.timeout) as response:
                chunks: list[bytes] = []
                read = 0
                while read <= ceiling:
                    if self._clock() >= deadline:
                        raise CameraUnreachable(
                            f"{self.camera_id}: still answering after {self.timeout}s; "
                            f"{read} byte(s) arrived and the read was abandoned",
                            CameraUnreachableCause.TIMEOUT,
                        )
                    # `read1`, NOT `read`. `read(n)` blocks until it has n bytes
                    # or the body ends, so a camera dripping bytes spends hours
                    # inside ONE call and the deadline above is never reached.
                    # `read1` comes back with whatever one socket read produced,
                    # which is what makes this loop a deadline rather than a
                    # bound on the number of chunks.
                    chunk = response.read1(CHUNK_BYTES)
                    if not chunk:
                        break
                    chunks.append(chunk)
                    read += len(chunk)
                body = b"".join(chunks)
        except urllib.error.HTTPError as exc:
            # It ANSWERED. Which half of the fault that is depends on the
            # status, and the status is carried so a human can tell them apart.
            if exc.code >= 500:
                raise CameraUnreachable(
                    f"{self.camera_id}: HTTP {exc.code}", CameraUnreachableCause.SERVER_ERROR
                ) from exc
            raise CameraRefusedUs(f"{self.camera_id}: HTTP {exc.code}", exc.code) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            # A socket that timed out PAST THE DEADLINE is the same fault as a
            # body that was still arriving at it: a camera that has stopped
            # answering inside its own timeout. Told apart from a socket that
            # failed, because they are two different repairs.
            timed_out = self._clock() >= deadline
            raise CameraUnreachable(
                f"{self.camera_id}: {exc}",
                CameraUnreachableCause.TIMEOUT if timed_out else CameraUnreachableCause.NETWORK,
            ) from exc
        if len(body) > ceiling:
            raise CameraUnreachable(
                f"{self.camera_id}: answered more than {ceiling} bytes",
                CameraUnreachableCause.NOT_A_PICTURE,
            )
        if not body.startswith(JPEG_MAGIC):
            # A login page served as `image/jpeg`, an error document, an empty
            # body. Refused rather than stored: a store full of those reads as a
            # working installation right up until somebody opens one.
            raise CameraUnreachable(
                f"{self.camera_id}: answered {len(body)} bytes that do not begin as a JPEG",
                CameraUnreachableCause.NOT_A_PICTURE,
            )
        return body


__all__ = [
    "CHUNK_BYTES",
    "DEFAULT_MAX_SNAPSHOT_BYTES",
    "DEFAULT_SNAPSHOT_TIMEOUT",
    "JPEG_MAGIC",
    "CameraRefusedUs",
    "CameraUnreachable",
    "SnapshotCamera",
]
