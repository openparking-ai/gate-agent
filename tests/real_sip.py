"""A real SIP world for the agent to be measured in: a registrar, and real UAs.

Everything here is REAL except the registrar, and the registrar is real SIP over
a real UDP socket -- it just answers `200 OK` to a `REGISTER` and nothing else,
which is all that is needed to establish that the user agent registers and that
the agent sees it.

**Why this is not a fake.** The dialogue tests use a fake user agent, and a fake
cannot answer the questions this file exists for: whether a call is actually
answered, whether the two legs are actually private before the bridge, and
whether a DTMF digit actually arrives tagged with the leg it came in on. Those
are properties of SIP, RTP and RFC 4733, and the only way to measure them is with
the software that implements them.

**The intercom and the person are baresip too**, each in its own process with its
own configuration directory, port and control socket. The intercom SENDS A TONE
-- its audio source is a WAV -- and the person WRITES WHAT IT HEARS to a file, so
"the person could not hear the driver until the agent bridged them" is a
measurement of a waveform rather than a claim about a command.

**The loopback trick, and it is not optional.** baresip's network layer filters
loopback addresses out of the interfaces it will use, so `net_interface lo`
finds nothing and every call fails with `no laddr for 127.0.0.1`. Its own filter
returns TRUE for an interface setting that parses as an ADDRESS, before the
loopback check -- so `net_interface 127.0.0.1` is what makes a whole SIP world
fit inside one machine with no packet leaving it.
"""

from __future__ import annotations

import math
import os
import random
import re
import shutil
import socket
import struct
import subprocess
import sys
import threading
import time
import wave
from pathlib import Path

#: Set in CI. With it, a missing baresip is a FAILURE rather than a skip: a
#: guarantee test that can skip silently is not a guarantee, and this file holds
#: the only measurements in the suite that touch SIP at all.
REQUIRE = os.environ.get("REQUIRE_SIP") == "1"

MODULES = (
    "g711.so", "l16.so", "auconv.so", "auresamp.so", "aufile.so",
    "ctrl_tcp.so", "mixausrc.so", "mixminus.so",
)
#: `debug_cmd` is here because the AGENT REQUIRES IT: `config` and `modules`,
#: which are how this build reads the user agent's loaded configuration back at
#: startup, are that module's commands. A baresip without it is refused by name,
#: which `test_agent_contract.py` measures against a fake socket and this file's
#: world would otherwise fail at `start()`.
APP_MODULES = ("account.so", "menu.so", "debug_cmd.so")


def baresip() -> str | None:
    return shutil.which("baresip")


def module_path() -> str | None:
    """Where this baresip keeps its modules, from its own startup output."""
    binary = baresip()
    if binary is None:
        return None
    for candidate in (
        Path(binary).resolve().parent.parent / "lib" / "baresip" / "modules",
        Path("/usr/lib/baresip/modules"),
        Path("/usr/local/lib/baresip/modules"),
    ):
        if candidate.is_dir():
            return str(candidate)
    return None


def tone(path: Path, hz: float = 440.0, seconds: float = 60.0, rate: int = 8000) -> Path:
    """A WAV a baresip can use as its audio source, loud enough to measure."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(
            b"".join(
                struct.pack("<h", int(12000 * math.sin(2 * math.pi * hz * i / rate)))
                for i in range(int(rate * seconds))
            )
        )
    return path


def rms_profile(path: Path, window: float = 0.5) -> list[int]:
    """The loudness of a recording in half-second buckets.

    A profile rather than one number, because what is being measured is a
    CHANGE at a moment: silence, and then the driver's tone from the instant the
    agent bridged the two legs.
    """
    if not path.exists():
        return []
    with wave.open(str(path), "rb") as handle:
        rate = handle.getframerate()
        raw = handle.readframes(handle.getnframes())
    values = struct.unpack(f"<{len(raw) // 2}h", raw[: len(raw) // 2 * 2])
    step = int(rate * window)
    return [
        int(math.sqrt(sum(v * v for v in values[i : i + step]) / max(1, len(values[i : i + step]))))
        for i in range(0, len(values), step)
    ]


def tone_share(path: Path, hz: float = 440.0, window: float = 0.5) -> list[float]:
    """How much of each half-second is the DRIVER'S TONE, bucket by bucket.

    A loudness profile is not enough here and that matters: the person hears the
    agent's own briefing before the bridge, so "the recording is silent" would
    be false for a reason that has nothing to do with whether they could hear
    the driver. What is measured instead is the share of each bucket's energy at
    the exact frequency the intercom is sending -- speech spreads its energy
    across the band, and a pure tone does not.
    """
    if not path.exists():
        return []
    with wave.open(str(path), "rb") as handle:
        rate = handle.getframerate()
        raw = handle.readframes(handle.getnframes())
    values = struct.unpack(f"<{len(raw) // 2}h", raw[: len(raw) // 2 * 2])
    step = int(rate * window)
    shares = []
    for start in range(0, len(values), step):
        chunk = values[start : start + step]
        if len(chunk) < step // 2:
            break
        total = sum(v * v for v in chunk) or 1
        # Goertzel, which is the cheapest way to ask for one bin.
        omega = 2 * math.pi * hz / rate
        coeff = 2 * math.cos(omega)
        s1 = s2 = 0.0
        for value in chunk:
            s0 = value + coeff * s1 - s2
            s2, s1 = s1, s0
        power = s1 * s1 + s2 * s2 - coeff * s1 * s2
        shares.append(min(1.0, (power / len(chunk)) / total))
    return shares


#: THE PORTS A BARESIP IS GIVEN COME FROM HERE, NOT FROM THE KERNEL. This used
#: to bind port 0, read the number, CLOSE the socket and hand the number over --
#: and between that close and baresip's own bind, anything asking the kernel for
#: a port could be given the same one. Measured on `dc9453f`, 3.11: nine errors,
#: `tcp: sock_bind: ... Address already in use [98] (af=2, 127.0.0.1:49658)`.
#: It also probed the SIP port on UDP alone, and baresip binds it on UDP AND TCP,
#: and binds the next port up on TCP for TLS -- measured with `lsof` against a
#: running instance -- so two of the four ports it binds were never checked.
#:
#: So: a block BELOW the kernel's ephemeral range, which nothing asking for port
#: 0 is ever given; every port probed on TCP and UDP before it is handed out;
#: and no port handed out twice by this process. RTP gets its own block below
#: that, named in each instance's configuration, so baresip's own media sockets
#: cannot land on a port handed to another instance either.
PORTS = range(20000, 30000)
RTP_PORTS = (10000, 19998)
_handed_out: set[int] = set()
_handing = threading.Lock()


def ephemeral_range() -> tuple[int, int]:
    """The range the kernel picks from for port 0, read from the kernel."""
    linux = Path("/proc/sys/net/ipv4/ip_local_port_range")
    if linux.exists():
        low, high = linux.read_text().split()
        return int(low), int(high)
    if sys.platform == "darwin":
        read = [
            subprocess.run(
                ["sysctl", "-n", f"net.inet.ip.portrange.{end}"],
                capture_output=True, text=True, check=True,
            ).stdout
            for end in ("first", "last")
        ]
        return int(read[0]), int(read[1])
    raise AssertionError(f"cannot read the ephemeral port range on {sys.platform}")


def _bindable(port: int) -> bool:
    for kind in (socket.SOCK_STREAM, socket.SOCK_DGRAM):
        with socket.socket(socket.AF_INET, kind) as sock:
            try:
                sock.bind(("127.0.0.1", port))
            except OSError:
                return False
    return True


def reserve(count: int) -> int:
    """The first of `count` consecutive ports nobody holds, now handed out."""
    low, high = ephemeral_range()
    for block in (PORTS, range(RTP_PORTS[0], RTP_PORTS[1] + 1)):
        if block.start <= high and low < block.stop:
            raise AssertionError(
                f"ports {block.start}..{block.stop - 1} overlap this machine's ephemeral range "
                f"{low}..{high}, so the kernel could hand one of them to anybody"
            )
    with _handing:
        offset = random.randrange(len(PORTS))
        for step in range(len(PORTS)):
            first = PORTS.start + (offset + step) % len(PORTS)
            wanted = range(first, first + count)
            if wanted.stop > PORTS.stop or _handed_out.intersection(wanted):
                continue
            if all(_bindable(port) for port in wanted):
                _handed_out.update(wanted)
                return first
    raise AssertionError(f"no {count} consecutive free ports in {PORTS.start}..{PORTS.stop - 1}")


class PortTaken(AssertionError):
    """baresip could not bind a port it was given: something else holds it."""


class BaresipDied(AssertionError):
    """baresip exited before its control socket opened, for a reason of its own."""


class Registrar:
    """A SIP registrar, and nothing else. It answers `REGISTER` with `200 OK`.

    Enough to establish that the user agent registers and that the agent's
    `sip_registration_lost` follows it, which is what the lane contract calls
    `intercom_registration_lost` and cannot itself see.

    `refuse` makes it answer `403` instead: the control, and the only way to
    watch a registration actually be lost.
    """

    def __init__(self) -> None:
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.settimeout(0.2)
        self.port = self.sock.getsockname()[1]
        self.refuse = False
        self.registrations: list[str] = []
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True)

    def start(self) -> Registrar:
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=3)
        self.sock.close()

    def _serve(self) -> None:
        while not self._stop.is_set():
            try:
                data, peer = self.sock.recvfrom(65535)
            except (TimeoutError, OSError):
                continue
            text = data.decode("utf-8", "replace")
            if not text.startswith("REGISTER"):
                continue
            self.registrations.append(text.splitlines()[0])
            self.sock.sendto(self._answer(text).encode("utf-8"), peer)

    def _answer(self, request: str) -> str:
        headers = {}
        for line in request.splitlines()[1:]:
            if not line.strip():
                break
            name, _, value = line.partition(":")
            headers.setdefault(name.strip().lower(), value.strip())
        status = "403 Forbidden" if self.refuse else "200 OK"
        lines = [f"SIP/2.0 {status}"]
        for name in ("via", "from"):
            if name in headers:
                lines.append(f"{name.title()}: {headers[name]}")
        if "to" in headers:
            lines.append(f"To: {headers['to']};tag=registrar")
        for name in ("call-id", "cseq"):
            if name in headers:
                lines.append(f"{name.upper() if name == 'call-id' else 'CSeq'}: {headers[name]}")
        if "contact" in headers and not self.refuse:
            lines.append(f"Contact: {headers['contact']};expires=60")
        lines += ["Content-Length: 0", "", ""]
        return "\r\n".join(lines)


class Instance:
    """One baresip, in its own directory, on its own ports."""

    def __init__(
        self,
        root: Path,
        name: str,
        accounts: list[str],
        audio_source: str = "aufile,SILENCE",
        audio_player: str = "aufile,DEVNULL",
    ) -> None:
        self.root = root / name
        self.root.mkdir(parents=True, exist_ok=True)
        self.name = name
        # THREE PORTS, consecutive: SIP on UDP and TCP, the next one up for TLS,
        # which baresip binds whether or not anybody uses it, and the control
        # socket.
        self.sip_port = reserve(3)
        self.ctrl_port = self.sip_port + 2
        self.log = self.root / "baresip.log"
        self.recording = self.root / "heard.wav"
        silence = tone(self.root / "silence.wav", hz=0.0, seconds=600.0)
        audio_source = audio_source.replace("SILENCE", str(silence))
        audio_player = audio_player.replace("DEVNULL", str(self.recording))
        (self.root / "config").write_text(
            "\n".join(
                [
                    f"sip_listen              127.0.0.1:{self.sip_port}",
                    # baresip filters loopback out of the interfaces it will
                    # use unless the setting parses as an address.
                    "net_interface           127.0.0.1",
                    # Its default holds every other call when a new one is
                    # established, which would put the driver on hold the
                    # moment the agent calls the operator.
                    "call_hold_other_calls   no",
                    "call_max_calls          4",
                    f"audio_player            {audio_player}",
                    f"audio_source            {audio_source}",
                    # The RING TONE has its own device, and it is named here
                    # because leaving it unset is not harmless: baresip falls
                    # back to the first registered player with an empty device
                    # name, which is `aufile` writing `speaker.wav` INTO THE
                    # WORKING DIRECTORY. It reached a commit on this branch
                    # before anybody noticed.
                    f"audio_alert             aufile,{self.root / 'alert.wav'}",
                    "ausrc_format            s16",
                    "auplay_format           s16",
                    "auenc_format            s16",
                    "audec_format            s16",
                    "audio_telev_pt          101",
                    f"rtp_ports               {RTP_PORTS[0]}-{RTP_PORTS[1]}",
                    f"module_path             {module_path()}",
                    *(f"module                  {one}" for one in MODULES),
                    f"ctrl_tcp_listen         127.0.0.1:{self.ctrl_port}",
                    *(f"module_app              {one}" for one in APP_MODULES),
                    "",
                ]
            ),
            encoding="utf-8",
        )
        (self.root / "accounts").write_text("\n".join(accounts) + "\n", encoding="utf-8")
        (self.root / "contacts").write_text("", encoding="utf-8")
        self.process: subprocess.Popen | None = None

    def start(self) -> Instance:
        handle = open(self.log, "wb")
        self.process = subprocess.Popen(
            [baresip(), "-f", str(self.root)], stdout=handle, stderr=subprocess.STDOUT
        )
        for _ in range(100):
            if self.process.poll() is not None:
                break
            try:
                with socket.create_connection(("127.0.0.1", self.ctrl_port), timeout=0.2):
                    pass
            except OSError:
                time.sleep(0.1)
                continue
            # Something answered. It is ours only if baresip is still alive and
            # did not fail a bind on the way up -- a control port somebody else
            # holds would answer too.
            time.sleep(0.2)
            if self.process.poll() is None and not self._refused_bind():
                return self
            break
        self._fail()

    def _refused_bind(self) -> str | None:
        text = self.log.read_text("utf-8", "replace") if self.log.exists() else ""
        text = re.sub(r"\x1b\[[0-9;]*m", "", text)
        found = re.search(r"[^\n]*Address already in use[^\n]*", text)
        return found.group(0).strip() if found else None

    def _fail(self) -> None:
        """Say WHICH thing happened. Both used to read "never opened its control
        socket", which is why a port collision took a red main to notice."""
        ports = f"sip {self.sip_port} (udp+tcp), tls {self.sip_port + 1}, ctrl {self.ctrl_port}"
        exited = self.process.poll() if self.process is not None else None
        state = "running" if exited is None else f"exited {exited}"
        taken = self._refused_bind()
        self.stop()
        if taken:
            raise PortTaken(
                f"{self.name}: PORT TAKEN -- a port it was given was bound by something else "
                f"first ({ports}; baresip {state}): {taken}\n{self.tail()}"
            )
        if exited is not None:
            raise BaresipDied(
                f"{self.name}: BARESIP DIED -- exit {exited} before its control socket opened, "
                f"and no bind was refused ({ports}):\n{self.tail()}"
            )
        raise AssertionError(
            f"{self.name}: baresip is running but its control socket never opened "
            f"({ports}):\n{self.tail()}"
        )

    def stop(self) -> None:
        if self.process is not None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
            self.process = None

    def tail(self, lines: int = 25) -> str:
        if not self.log.exists():
            return "(no log)"
        return "\n".join(self.log.read_text("utf-8", "replace").splitlines()[-lines:])
