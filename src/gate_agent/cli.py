"""The command line, and the module's STANDALONE face.

    gate-agent monitor --config monitor.toml

**Standalone is a MODE, not a smaller product.** A monitor with no lane declared
watches whatever IS declared -- a Vehicle ID service on its own, a platform on
its own -- and says so. What it will not do is start with nothing declared: a
monitor watching nothing reports "all fine", which is the one lie this module
exists to prevent, so an empty target set is refused here with that reason
printed.

The line it prints on start says what it watches, what it can tell, and -- when
`log` is the only sink -- that **nobody is paged**. That is a valid
configuration and a deliberate one at a site whose logs are already collected;
it is said out loud at the moment somebody starts the process rather than
discovered the first time a lane goes down at midnight.
"""

from __future__ import annotations

import argparse
import logging
import sys
import threading
from pathlib import Path

from .config import ConfigError, MonitorConfig
from .monitor import Monitor, UnsupportedContract
from .service import (
    InsecureBind,
    MonitorService,
    assert_bind_allowed,
    is_loopback,
    make_server,
)
from .sinks import build as build_sink


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="gate-agent", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    monitor = sub.add_parser("monitor", help="watch the declared targets and tell a human")
    monitor.add_argument(
        "--config",
        type=Path,
        required=True,
        help="the monitor's TOML configuration. Targets and sinks are DECLARED, never "
             "defaulted, and a file that declares no target is refused here rather than "
             "running as a monitor that reports nothing wrong about nothing",
    )
    monitor.add_argument("--host", default="127.0.0.1")
    monitor.add_argument("--port", type=int, default=8092)
    monitor.add_argument(
        "--auth-token-file",
        type=Path,
        help="a file holding the shared token every route of this monitor's own surface must "
             "carry. Required for any --host that is not loopback. A FILE and not a value, "
             "because a value on the command line is readable by every user on the box for as "
             "long as the process runs -- spelt the way vehicle-id and lane-controller spell it",
    )
    return parser


def _token(args) -> str | None:
    """The shared token, read from the file that holds it.

    An empty or whitespace-only file is not a token and is refused rather than
    read as "no token configured" -- which would be a truncated file silently
    turning the credential off on the one bind that requires one.
    """
    if not args.auth_token_file:
        return None
    try:
        raw = args.auth_token_file.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"could not read {args.auth_token_file}: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
    token = raw.strip()
    if not token:
        print(f"{args.auth_token_file} holds no token", file=sys.stderr)
        raise SystemExit(2)
    return token


def cmd_monitor(args) -> int:
    # The bind refusal BEFORE anything is built or read, so a configuration no
    # file would fix is reported in the moment.
    token = _token(args)
    try:
        assert_bind_allowed(args.host, args.port, token)
    except InsecureBind as exc:
        print(f"\n{exc}\n", file=sys.stderr)
        return 2

    try:
        config = MonitorConfig.from_file(args.config)
    except ConfigError as exc:
        print(f"\n{exc}\n", file=sys.stderr)
        return 2

    sinks = [build_sink(sink) for sink in config.sinks]
    monitor = Monitor(config, sinks)

    reach = "local only by design" if is_loopback(args.host) else "EXPOSED"
    print(f"gate-agent monitor on http://{args.host}:{args.port}  ({reach})")
    print(f"  site {config.site_id}, monitor {config.monitor_id}")
    print(f"  watching: {', '.join(target.name for target in config.targets)}")
    print(f"  telling:  {', '.join(sink.name for sink in sinks)}")
    if [sink.name for sink in sinks] == ["log"]:
        # Said at the moment somebody starts it, because "the monitor is
        # running" and "somebody will be told" are different facts and this is
        # the configuration where they come apart.
        print("  NOBODY IS PAGED: `log` is the only sink, so notifications go to stdout only")
    print("  READ ONLY: this monitor has no route to a vend, here or at any target")

    try:
        monitor.start()
    except UnsupportedContract as exc:
        print(f"\n{exc}\n", file=sys.stderr)
        return 2

    server = make_server(MonitorService(monitor), host=args.host, port=args.port, token=token)
    stop = threading.Event()
    poller = threading.Thread(target=_poll_forever, args=(monitor, stop), daemon=True)
    poller.start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print()
    finally:
        stop.set()
        server.server_close()
    return 0


def _poll_forever(monitor: Monitor, stop: threading.Event, tick: float = 1.0) -> None:
    """Ask the monitor to poll whatever is due, once a second.

    The interval that matters is per target and lives in the configuration; this
    is only how often the process wakes up to check whether one has elapsed. A
    target polled every thirty seconds is not polled every second because of
    this loop.
    """
    while not stop.wait(tick):
        try:
            monitor.poll()
        except Exception:  # noqa: BLE001
            # A monitor that dies is a monitor that reports nothing, which is
            # the failure this module exists to prevent. Anything a poll raises
            # is logged and the loop continues; what the poll could not measure
            # is already `unknown` on the health route.
            logging.getLogger(__name__).exception("a poll raised; continuing")


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    args = build_parser().parse_args(argv)
    return {"monitor": cmd_monitor}[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())

