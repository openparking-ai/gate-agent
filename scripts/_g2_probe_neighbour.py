"""G2 PLANT (do not merge): can two breaks on one runner see each other?

Runs `_shard.control` -- the pool the shards use, unchanged -- on a one-test
suite. Control A (intact) SERVES on 127.0.0.1:8092 for 60 s. The one break,
`neighbour_answers`, flips the test to a CLIENT that passes only if something
answers on 8092 within 45 s, and it starts no server of its own. The two run at
once in the pool (control A first, the break beside it).

    isolated   -- as CI runs the shards: each suite in its own network
                  namespace. Nothing can answer, the break goes red, and the
                  control reports it caught: exit 0.
    shared     -- POSITIVE CONTROL: the same pool, both suites on the runner's
                  own network. Control A's server answers, the break passes,
                  and the control must report `neighbour_answers` NOT caught:
                  exit 1. If this is green, the isolated run proves nothing.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import _shard  # noqa: E402
from _control import intact, judge  # noqa: E402

PROBE = '''
import http.server, socket, threading, time

MODE = "serve"
PORT = 8092


def test_probe():
    if MODE == "serve":
        server = http.server.HTTPServer(("127.0.0.1", PORT), http.server.BaseHTTPRequestHandler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        time.sleep(60)
        server.shutdown()
        return
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        try:
            socket.create_connection(("127.0.0.1", PORT), timeout=1).close()
            print("a neighbour answered on", PORT)
            return
        except OSError:
            time.sleep(0.5)
    raise AssertionError(f"nothing answered on {PORT}")


def test_stays_green():
    pass
'''

BREAKS = [
    {
        "name": "neighbour_answers",
        "file": "test_probe.py",
        "from": 'MODE = "serve"',
        "to": 'MODE = "client"',
        "why": "a break that only passes if the neighbour's server answered",
    }
]


def stage() -> Path:
    directory = Path(tempfile.mkdtemp(prefix="g2-probe-"))
    (directory / "test_probe.py").write_text(PROBE, encoding="utf-8")
    return directory


mode = sys.argv[1:]
if mode not in (["isolated"], ["shared"]):
    sys.exit("usage: _g2_probe_neighbour.py isolated|shared")

ok, why = _shard._isolation()
if not ok:
    sys.exit(f"no network namespaces here, nothing measured: {why}")
if mode == ["shared"]:
    real = _shard.suite
    _shard.suite = lambda directory, command, isolated: real(directory, command, False)

command = [sys.executable, "-m", "pytest", "-q", "-s", "-p", "no:cacheprovider"]
failures = _shard.control(
    "probe", BREAKS, [0], f"probe {mode[0]}", stage, command, 20, intact, judge, parallel=True
)
print(f"probe {mode[0]}: {failures} control(s) failed")
sys.exit(1 if failures else 0)
