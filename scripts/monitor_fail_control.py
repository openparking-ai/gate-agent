#!/usr/bin/env python3
"""The control for every guarantee this monitor makes.

A test that has never been observed failing is not evidence of anything. This
runs the suite once intact, where it must pass, and once for each break below,
where it must FAIL. A pass is the failure.

**Every break is applied to a COPY of the source in a temporary directory**, and
no tracked file is edited by this script. That is not just hygiene: several of
the guarantees here are properties of the SOURCE — the sweep that requires every
request in this package to be a `GET`, the one that requires it to import no lane
— and a monkeypatch cannot break a source property. A control that could only
break behaviour would leave exactly those unproven.

Each break removes one thing, and every one of them fails in the REASSURING
direction — an honest `unknown` becoming `ok`, a fault becoming silence, a
credential becoming convenient — because that is the direction a monitor fails in
when nobody is looking.

  a_post_in_the_client   the monitor grows a client that can POST. This is the
                         invariant: it has NO opening authority, and a new route
                         to a barrier is the boundary every outside reviewer of
                         this project has named.
  import_the_lane        it imports `lane_controller` instead of speaking the
                         contract. The seat a third party takes stops being the
                         seat we use, and nobody feels the contract's gaps.
  sinks_reach_a_target   the one module allowed to POST gains the target client.
                         The exemption becomes a hole shaped like the thing it
                         was carved around.
  unknown_is_ok          a target's `unknown` is published as `ok`. A clean bill
                         of health from an instrument that is not plugged in.
  states_not_transitions every poll re-sends whatever is active. A human told a
                         thousand times has been told nothing.
  never_alarm_from_a_list
                         the monitor holds its own copy of the never-alarm set
                         instead of reading the wire. The two drift, and the
                         drift is a technician dispatched because a car arrived.
  no_longer_measured_is_silent
                         a code that stops being measured says nothing. The loss
                         of a measurement is the event a monitor most easily
                         hides.
  dead_target_keeps_its_codes
                         a lane that stopped answering keeps publishing its last
                         known health, indistinguishable from now.
  empty_target_set_allowed
                         a monitor with nothing to watch starts, and reports all
                         fine. The lie this module exists to prevent.
  token_by_value         a configuration key takes a credential as a value. It
                         is then in the file, in every backup, and in every
                         paste of it.
  half_read_version      a target on a version this build cannot read has its
                         codes published anyway, as though understood.
  quiet_from_a_constant  `lane_gone_quiet` uses a threshold in the code instead
                         of the site's. A number nobody measured, applied
                         everywhere.
  never_seen_is_fine     a device that has never reported reads `ok`. A lane that
                         never came up, reported as working.
  a_sink_failure_is_quiet
                         a sink that cannot deliver is not reported. The failure
                         that hides every other failure.
  redirects_are_followed
                         the opener follows a 3xx again. A target then takes
                         this monitor's credential to a host of its choosing,
                         serves another host's payload as its own health, and
                         silences the webhook sink by redirecting it -- with
                         `deliver()` reporting success.
  never_alarm_is_coerced
                         `never_alarm` is bool(...) of whatever arrived. Absent
                         pages a technician because a car arrived; the string
                         "false" silences a code for ever.
  any_state_passes_through
                         a `state` outside the contract's three is published and
                         becomes the state the next one is compared against, so
                         the ACTIVE fault after it is told to nobody.
  refusal_is_silence     an HTTP refusal is published as `<kind>_unreachable`
                         again. A dead credential and a dead platform become one
                         message, on the wrong machine, with no status.
  lane_id_on_everything  the lane's id is stamped on every notification, so the
                         monitor's own sink failure reads as a lane's fault.
  startup_from_a_second_walk
                         the startup message is built from a second enumeration.
                         It then omits exactly this monitor's own blind spots --
                         the codes with no subject yet.
  userinfo_is_published  a credential in a target URL is accepted and echoed on
                         `GET /v1/monitor`, beside `authenticated: false`.
  the_foreign_lane_imports_ours
                         the stub that exists to prove a stranger can take this
                         seat takes its code list from our package again.
  doc_set_missing_a_member
                         the document publishes one fewer monitor code than the
                         enum holds -- a consumer that cannot be written from it.
  enum_gained_a_member   the code gains a monitor code the document does not
                         publish, which is what a future round does the day it
                         adds one.
  doc_values             the document publishes what the code contradicts:
                         `contract_version: 99` and a state outside the enum. The
                         shape check passes both, because it discards every leaf.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

BREAKS = [
    {
        "name": "a_post_in_the_client",
        "why": "the monitor grows a client that can POST",
        "file": "src/gate_agent/client.py",
        "from": '        request = urllib.request.Request(url, method="GET")',
        "to": '        request = urllib.request.Request(url, data=b"{}", method="POST")',
    },
    {
        "name": "import_the_lane",
        "why": "the monitor imports the lane instead of speaking its contract",
        "file": "src/gate_agent/monitor.py",
        "from": "from .client import ReadOnlyClient, TargetRefusedUs, TargetUnreachable",
        "to": "from lane_controller.contract import MalfunctionCode as _Unused  # noqa: F401\n"
        "from .client import ReadOnlyClient, TargetUnreachable",
    },
    {
        "name": "sinks_reach_a_target",
        "why": "the module allowed to POST gains the target client",
        "file": "src/gate_agent/sinks.py",
        "from": "from .config import EmailSinkConfig, LogSinkConfig, WebhookSinkConfig",
        "to": "from .client import ReadOnlyClient  # noqa: F401\n"
        "from .config import EmailSinkConfig, LogSinkConfig, WebhookSinkConfig",
    },
    {
        "name": "unknown_is_ok",
        "why": "a target's `unknown` is published as `ok`",
        "file": "src/gate_agent/monitor.py",
        "from": '            state=str(entry.get("state")),',
        "to": '            state=('
        '"ok" if entry.get("state") == "unknown" else str(entry.get("state"))'
        '),',
    },
    {
        "name": "states_not_transitions",
        "why": "every poll re-sends whatever is active",
        "file": "src/gate_agent/monitor.py",
        "from": (
            "        if state == active and previous in (ok, unknown):\n"
            "            return Transition.RAISED"
        ),
        "to": "        if state == active:\n            return Transition.RAISED",
    },
    {
        "name": "never_alarm_from_a_list",
        "why": "the monitor holds its own never-alarm set instead of reading the wire",
        "file": "src/gate_agent/monitor.py",
        "from": '            never_alarm=entry["never_alarm"],',
        "to": '            never_alarm=str(entry.get("code")) == "reference_not_recognised",',
    },
    {
        "name": "no_longer_measured_is_silent",
        "why": "a code that stops being measured says nothing",
        "file": "src/gate_agent/monitor.py",
        "from": (
            "        if state == unknown and previous in (ok, active):\n"
            "            return Transition.NO_LONGER_MEASURED"
        ),
        "to": "        if False:\n            return Transition.NO_LONGER_MEASURED",
    },
    {
        "name": "dead_target_keeps_its_codes",
        "why": "a lane that stopped answering keeps publishing its last known health",
        "file": "src/gate_agent/monitor.py",
        "from": (
            "            self._monitor_code(refused, target.name, "
            "HealthState.UNKNOWN, target.name)\n"
            "            self._retire(target.name)"
        ),
        "to": (
            "            self._monitor_code(refused, target.name, "
            "HealthState.UNKNOWN, target.name)"
        ),
    },
    {
        "name": "empty_target_set_allowed",
        "why": "a monitor with nothing to watch starts and reports all fine",
        "file": "src/gate_agent/config.py",
        "from": "        if not targets:\n            raise ConfigError(",
        "to": "        if False:\n            raise ConfigError(",
    },
    {
        "name": "token_by_value",
        "why": "a configuration key takes a credential as a value",
        "file": "src/gate_agent/config.py",
        "from": "    _refuse_credential_values(raw, \"\")",
        "to": "    pass",
    },
    {
        "name": "half_read_version",
        "why": "a target on an unreadable version has its codes published anyway",
        "file": "src/gate_agent/monitor.py",
        "from": "            self._retire(target.name)\n            entries = ()",
        "to": "            pass",
    },
    {
        "name": "quiet_from_a_constant",
        "why": "`lane_gone_quiet` uses a threshold in the code instead of the site's",
        "file": "src/gate_agent/monitor.py",
        "from": "            elif age > target.lane_quiet_seconds:",
        "to": "            elif age > 300.0:",
    },
    {
        "name": "never_seen_is_fine",
        "why": "a device that has never reported reads `ok`",
        "file": "src/gate_agent/monitor.py",
        "from": '            last = device.get("last_seen_at") or device.get("created_at")',
        "to": '            last = device.get("last_seen_at") or self._now()',
    },
    {
        "name": "a_sink_failure_is_quiet",
        "why": "a sink that cannot deliver is not reported",
        "file": "src/gate_agent/monitor.py",
        "from": "        self._sink_states(failures)",
        "to": "        return",
    },
    {
        "name": "redirects_are_followed",
        "why": "the opener follows a 3xx and hands over the credential",
        "file": "src/gate_agent/redirects.py",
        "from": "    def redirect_request(self, req, fp, code, msg, headers, newurl):\n"
        "        return None",
        "to": "    def _not_used(self, req, fp, code, msg, headers, newurl):\n"
        "        return None",
    },
    {
        "name": "never_alarm_is_coerced",
        "why": "`never_alarm` is bool(...) of whatever arrived",
        "file": "src/gate_agent/monitor.py",
        # Exactly the behaviour that stood here: absent reads as `false` and
        # pages a technician because a car arrived; `"false"` is truthy and
        # silences that code for ever.
        "from": "        if not isinstance(never_alarm, bool):\n"
        "            raise ContractViolation(",
        "to": '        entry["never_alarm"] = bool(never_alarm)\n'
        "        if False:\n"
        "            raise ContractViolation(",
    },
    {
        "name": "any_state_passes_through",
        "why": "a state outside the contract's three is published",
        "file": "src/gate_agent/monitor.py",
        "from": '        state = entry.get("state")\n        if state not in states:',
        "to": '        state = entry.get("state")\n        if state is None:',
    },
    {
        "name": "refusal_is_silence",
        "why": "an HTTP refusal is published as unreachable",
        "file": "src/gate_agent/client.py",
        "from": '            if exc.code >= 500:\n'
        '                raise TargetUnreachable(f"{url}: HTTP {exc.code}") from exc',
        "to": '            if exc.code >= 300:\n'
        '                raise TargetUnreachable(f"{url}: HTTP {exc.code}") from exc',
    },
    {
        "name": "lane_id_on_everything",
        "why": "the lane's id is stamped on every notification",
        "file": "src/gate_agent/monitor.py",
        "from": "        return self._lane_id if self._kinds.get(target) "
        "is TargetKind.LANE else None",
        "to": "        return self._lane_id",
    },
    {
        "name": "startup_from_a_second_walk",
        "why": "the startup message is built from a second enumeration",
        "file": "src/gate_agent/monitor.py",
        "from": "            for entry in health.codes\n"
        "            if entry.state == HealthState.UNKNOWN.value\n"
        "        ]",
        "to": "            for entry in health.codes\n"
        "            if entry.state == HealthState.UNKNOWN.value\n"
        "            and entry.subject != self.config.monitor_id\n"
        "        ]",
    },
    {
        "name": "userinfo_is_published",
        "why": "a credential in a target URL is accepted and echoed",
        "file": "src/gate_agent/config.py",
        "from": '        _refuse_userinfo(url, f"[targets.{kind.value}].url")',
        "to": "        pass",
    },
    {
        "name": "the_foreign_lane_imports_ours",
        "why": "the foreign lane takes its code list from our package",
        "file": "tests/foreign_lane/lane.py",
        "from": "MALFUNCTION_CODES = (",
        "to": "from lane_controller.contract import MalfunctionCode as _Ours  # noqa: F401\n"
        "MALFUNCTION_CODES = (",
    },
    {
        "name": "doc_set_missing_a_member",
        "why": "the document publishes fewer codes than the enum holds",
        "file": "tests/test_monitor_contract.py",
        "from": "    return {name: json.loads(body) for name, body in found}",
        "to": "    parsed = {name: json.loads(body) for name, body in found}\n"
        "    parsed['sets']['monitor_codes'] = parsed['sets']['monitor_codes'][:-1]\n"
        "    return parsed",
    },
    {
        "name": "enum_gained_a_member",
        "why": "the enum gains a code the document does not publish",
        # Applied to the DERIVATION rather than to the enum, so the break lands
        # on the doc-versus-code comparison alone: adding a member to
        # `MonitorCode` itself also empties `MONITOR_SOURCES` for it, and a
        # control that goes red for two reasons has measured neither.
        "file": "tests/test_monitor_contract.py",
        "from": 'PUBLISHED_SETS = {"monitor_codes": lambda: [code.value for code in MonitorCode]}',
        "to": 'PUBLISHED_SETS = {\n'
        '    "monitor_codes": lambda: [code.value for code in MonitorCode] + ["hatch_left_open"]\n'
        "}",
    },
    {
        "name": "doc_values",
        "why": "the document publishes values the code contradicts",
        "file": "tests/test_monitor_contract.py",
        "from": '    return {name: json.loads(body) for name, body in found}',
        "to": "    parsed = {name: json.loads(body) for name, body in found}\n"
        "    parsed['health']['contract_version'] = 99\n"
        "    parsed['health']['codes'][0]['state'] = 'probably_fine'\n"
        "    return parsed",
    },
]


def stage() -> Path:
    directory = Path(tempfile.mkdtemp(prefix="gate-agent-control-"))
    for entry in ("src", "tests", "docs", "config", "pyproject.toml"):
        source = ROOT / entry
        target = directory / entry
        if source.is_dir():
            shutil.copytree(source, target)
        else:
            shutil.copy2(source, target)
    return directory


def run(directory: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q"],
        cwd=directory,
        capture_output=True,
        text=True,
    )


def tail(result: subprocess.CompletedProcess, lines: int = 1) -> str:
    body = [line for line in result.stdout.strip().splitlines() if line.strip()]
    return " | ".join(body[-lines:]) if body else "(no output)"


failures = 0

print("== control A: the suite must PASS intact ==")
intact_dir = stage()
try:
    intact = run(intact_dir)
    if intact.returncode == 0:
        print(f"  control A OK — {tail(intact)}")
    else:
        print(
            f"  CONTROL A FAILED — the suite does not pass even intact: {tail(intact)}",
            file=sys.stderr,
        )
        print(intact.stdout, file=sys.stderr)
        failures += 1
finally:
    shutil.rmtree(intact_dir, ignore_errors=True)

print("\n== control B: each break must make it FAIL ==")
for brk in BREAKS:
    directory = stage()
    try:
        path = directory / brk["file"]
        source = path.read_text(encoding="utf-8")
        if brk["from"] not in source:
            # A break whose anchor has moved applies nothing, and the run then
            # reports a passing suite as a failed control -- for the wrong
            # reason. Named here so the two cannot be confused.
            print(
                f"  {brk['name']:29} *** ANCHOR NOT FOUND in {brk['file']} ***", file=sys.stderr
            )
            failures += 1
            continue
        path.write_text(source.replace(brk["from"], brk["to"], 1), encoding="utf-8")
        broken = run(directory)
        if broken.returncode == 0:
            print(
                f"  {brk['name']:29} *** PASSED WHEN {brk['why'].upper()} —"
                " the suite is not measuring this ***",
                file=sys.stderr,
            )
            failures += 1
        else:
            print(f"  {brk['name']:29} fails as required when {brk['why']} — {tail(broken)}")
    finally:
        shutil.rmtree(directory, ignore_errors=True)

if failures:
    print(
        f"\n{failures} control(s) failed. Do not trust this monitor's guarantees.",
        file=sys.stderr,
    )
    sys.exit(1)
print("\nall controls OK — the suite fails on every property this monitor exists to have.")
