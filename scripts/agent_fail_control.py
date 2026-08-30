#!/usr/bin/env python3
"""The control for every guarantee the AGENT makes. A pass is the failure.

A test that has never been observed failing is not evidence of anything. This
runs the suite once intact, where it must pass, and once per break below, where
it must FAIL.

**Every break is applied to a COPY of the source in a temporary directory** and
no tracked file is edited. That is not hygiene alone: several of these
guarantees are properties of the SOURCE -- the sweep for a verb that opens
something, the one that keeps the socket module away from a lane -- and a
monkeypatch cannot break a source property, so a control that could only break
behaviour would leave exactly those unproven.

Every break fails in the REASSURING direction: a refusal becoming an answer, an
escalation becoming a confident guess, a silence becoming a sentence nobody
hears. That is the direction this kind of software fails in when nobody is
looking.

**The SIP measurements are not driven from here.** They take a real baresip, a
real registrar and real seconds, and each of them carries its own control inside
the test: a registrar that starts refusing, a recording measured on both sides of
the bridge, and a lane that would see a planted non-GET. Running them under every
break below would add half an hour to a check whose subject is elsewhere.
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
        "name": "a_verb_that_opens",
        "why": "the agent asks its user agent to open a barrier",
        "file": "src/gate_agent/agent.py",
        "from": "        self._say(session, UaLeg.DRIVER, f\"authorisation.{value.value}\")",
        "to": "        self.ua.vend()\n"
              "        self._say(session, UaLeg.DRIVER, f\"authorisation.{value.value}\")",
    },
    {
        "name": "an_act_exists",
        "why": "something in this package can act, so `can_vend` is no longer false",
        "file": "src/gate_agent/contract.py",
        "from": "ACTS: dict[Authorisation, str] = {}",
        "to": "ACTS: dict[Authorisation, str] = {Authorisation.OPEN_NOW: \"planted\"}",
    },
    {
        "name": "the_socket_module_can_see_a_lane",
        "why": "the module allowed to open a socket gains the target client",
        "file": "src/gate_agent/ua_baresip.py",
        "from": "from .ua import UaEvent",
        "to": "from .client import ReadOnlyClient  # noqa: F401\nfrom .ua import UaEvent",
    },
    {
        "name": "a_socket_outside_the_adapter",
        "why": "another module opens its own socket, which no existing sweep can see",
        "file": "src/gate_agent/agent.py",
        "from": "    def _measure_audio(self) -> None:",
        "to": "    def _reach_out(self):\n"
              "        import socket\n\n"
              "        return socket.create_connection((\"127.0.0.1\", 1))\n\n"
              "    def _measure_audio(self) -> None:",
    },
    {
        "name": "an_undeclared_intercom_is_guessed",
        "why": "a call from an undeclared identity is given the first declared lane",
        "file": "src/gate_agent/agent.py",
        "from": "        intercom = self._by_uri.get(uri)",
        "to": "        intercom = self._by_uri.get(uri) or next(iter(self._by_uri.values()), None)",
    },
    {
        "name": "an_unrecognised_reason_is_mapped",
        "why": "a reason outside the subset is answered with the nearest thing we know",
        "file": "src/gate_agent/cases.py",
        "from": "        return case if case is not None else AgentCase.UNRECOGNISED_REASON",
        "to": "        return case if case is not None else AgentCase.PLATE_UNCLEAR",
    },
    {
        "name": "a_dead_engine_is_a_marginal_read",
        "why": "`engine_unreachable` becomes the case that tells a driver to wipe a plate",
        "file": "src/gate_agent/cases.py",
        "from": '    "engine_unreachable": AgentCase.IDENTIFICATION_UNAVAILABLE,',
        "to": '    "engine_unreachable": AgentCase.PLATE_UNCLEAR,',
    },
    {
        "name": "a_never_alarm_code_is_a_fault",
        "why": "`never_alarm` comes from a list here instead of from the wire",
        "file": "src/gate_agent/agent.py",
        "from": '            if entry.get("state") == HealthState.ACTIVE.value and (\n'
                '                entry.get("never_alarm") is False\n'
                '            ):',
        "to": '            if entry.get("state") == HealthState.ACTIVE.value:',
    },
    {
        "name": "the_reason_subset_is_invented",
        "why": "this package branches on a reason no lane emits",
        "file": "src/gate_agent/cases.py",
        "from": '    "engine_unreachable",\n)',
        "to": '    "engine_unreachable",\n    "invented_reason",\n)',
    },
    {
        "name": "a_missing_audio_file_is_silence",
        "why": "a line with no recording starts anyway, and plays nothing",
        "file": "src/gate_agent/config.py",
        "from": "    if absent:\n        raise ConfigError(",
        "to": "    if False:\n        raise ConfigError(",
    },
    {
        "name": "a_language_is_skipped",
        "why": "the driver hears the first declared language only",
        "file": "src/gate_agent/agent.py",
        "from": "        for language in languages:\n"
                "            path = self.config.audio_directory / audio_name(line, language)",
        "to": "        for language in languages[:1]:\n"
              "            path = self.config.audio_directory / audio_name(line, language)",
    },
    {
        "name": "the_order_is_ours_not_the_sites",
        "why": "the driver hears the languages in this package's order, not the site's",
        "file": "src/gate_agent/agent.py",
        "from": "            (self.config.operator_language,) if operator else session.languages",
        "to": "            (self.config.operator_language,) if operator "
              "else tuple(sorted(session.languages))",
    },
    {
        "name": "a_language_switch_is_ignored",
        "why": "a driver who answered in one language goes on hearing every other one",
        "file": "src/gate_agent/agent.py",
        "from": "        session.languages = (language,)",
        "to": "        return",
    },
    {
        "name": "an_undeclared_language_is_switched_to",
        "why": "a call switches to a language this package has no words for",
        "file": "src/gate_agent/agent.py",
        "from": "        if language not in self.config.driver_languages:",
        "to": "        if False:",
    },
    {
        "name": "a_disabled_authorisation_is_accepted",
        "why": "a digit for something this site switched off is taken anyway",
        "file": "src/gate_agent/agent.py",
        "from": "        if value is None or value not in self.config.authorisations:",
        "to": "        if value is None:",
    },
    {
        "name": "a_disabled_authorisation_is_offered",
        "why": "the menu reads out options this site did not enable",
        "file": "src/gate_agent/agent.py",
        "from": "            if value in self.config.authorisations:",
        "to": "            if True:",
    },
    {
        "name": "the_person_is_never_told_it_cannot_open",
        "why": "`open_now` is recorded and the person is left believing a barrier moved",
        "file": "src/gate_agent/agent.py",
        "from": "        if value in CANNOT_ACT:",
        "to": "        if False:",
    },
    {
        "name": "the_bridge_comes_first",
        "why": "the two are put together before the person is briefed, so the driver hears it",
        "file": "src/gate_agent/agent.py",
        "from": "        session.state = State.BRIEFING\n        session.deadline = None",
        "to": "        session.state = State.BRIEFING\n        session.deadline = None\n"
              "        self.ua.bridge()",
    },
    {
        "name": "the_no_answer_timer_never_fires",
        "why": "the person does not pick up and the driver is never told",
        "file": "src/gate_agent/agent.py",
        "from": "        if session.state is State.CALLING_HUMAN and session.deadline is not"
                " None:\n            if now >= session.deadline:",
        "to": "        if session.state is State.CALLING_HUMAN and session.deadline is not"
              " None:\n            if False:",
    },
    {
        "name": "the_nothing_usable_timer_never_fires",
        "why": "no digit ever arrives and the driver waits for ever",
        "file": "src/gate_agent/agent.py",
        "from": "        if session.state in (State.WAITING_DIGIT, State.COLLECTING_NUMBER):\n"
                "            if session.deadline is not None and now >= session.deadline:",
        "to": "        if session.state in (State.WAITING_DIGIT, State.COLLECTING_NUMBER):\n"
              "            if False:",
    },
    {
        "name": "a_held_driver_hears_nothing",
        "why": "`hold` is keyed and the driver is never re-prompted",
        "file": "src/gate_agent/agent.py",
        "from": "        if session.state is State.HOLDING:\n"
                "            if session.deadline is not None and now >= session.deadline:",
        "to": "        if session.state is State.HOLDING:\n            if False:",
    },
    {
        "name": "a_second_call_is_answered",
        "why": "a call arriving during a case is answered, and the fall-through is taken away",
        "file": "src/gate_agent/agent.py",
        "from": "        if self.session is not None:",
        "to": "        if False:",
    },
    {
        "name": "an_orphaned_operator_leg",
        "why": "a driver hangs up and the person is left live, to be bridged into the next case",
        "file": "src/gate_agent/agent.py",
        "from": "                if session.operator_call:\n"
                "                    self.ua.hangup(session.operator_call)\n"
                "                    session.operator_call = None\n"
                "                self._end(session)",
        "to": "                self._end(session)",
    },
    {
        "name": "keyed_takes_anything",
        "why": "the one field a caller fills stops being digits, so a plate can go in it",
        "file": "src/gate_agent/contract.py",
        "from": "        if self.keyed is not None and (not self.keyed or not "
                "self.keyed.isdigit()):",
        "to": "        if False:",
    },
    {
        "name": "the_user_agent_version_is_not_checked",
        "why": "the agent drives a user agent whose control vocabulary it has guessed at",
        "file": "src/gate_agent/ua_baresip.py",
        "from": "        if version not in TESTED_VERSIONS:",
        "to": "        if False:",
    },
    {
        "name": "an_unknown_registration_is_ok",
        "why": "a registration nobody has heard about is published as healthy",
        "file": "src/gate_agent/agent.py",
        "from": "            HealthState.UNKNOWN\n            if registered is None",
        "to": "            HealthState.OK\n            if registered is None",
    },
    {
        "name": "a_code_can_be_absent",
        "why": "a health payload ships a subset, and an absent code reads as a healthy one",
        "file": "src/gate_agent/contract.py",
        "from": "        missing = {code.value for code in AgentCode} - {code for code, _ in seen}",
        "to": "        missing = set()",
    },
    {
        "name": "a_sentence_changed_without_its_audio",
        "why": "a line is edited and the file that plays still says the old thing",
        "file": "src/gate_agent/lines.py",
        "from": '"en": "This intercom is not configured. Goodbye.",',
        "to": '"en": "This door is out of service. Goodbye.",',
    },
    {
        "name": "an_intercom_needs_no_lane",
        "why": "an intercom is declared with no lane and the agent guesses at call time",
        "file": "src/gate_agent/config.py",
        "from": '        if "lane" not in table:',
        "to": "        if False:",
    },
    {
        "name": "a_site_may_enable_nothing",
        "why": "a person is called who can authorise nothing",
        "file": "src/gate_agent/config.py",
        "from": "    if not enabled:\n        raise ConfigError(",
        "to": "    if False:\n        raise ConfigError(",
    },
    {
        "name": "transfer_is_quietly_not_offered",
        "why": "a site switches on an option that reaches nobody, and is not told",
        "file": "src/gate_agent/config.py",
        "from": "    if Authorisation.TRANSFER in enabled and not transfer_sip_uri:",
        "to": "    if False:",
    },
    {
        "name": "one_account_for_both_legs",
        "why": "the two calls cannot be told apart, so the menu plays to the driver",
        "file": "src/gate_agent/config.py",
        "from": '    if aors["driver_aor"] == aors["operator_aor"]:',
        "to": "    if False:",
    },
    {
        "name": "a_sip_uri_may_carry_a_password",
        "why": "a credential in a SIP URI is in the file, in every backup, and on a read route",
        "file": "src/gate_agent/config.py",
        "from": "    if \":\" in rest:\n        raise ConfigError(",
        "to": "    if False:\n        raise ConfigError(",
    },
    {
        "name": "the_agent_surface_can_be_written_to",
        "why": "a method other than GET stops being the one shared refusal",
        "file": "src/gate_agent/agent_service.py",
        "from": "    do_POST = _method_not_allowed  # noqa: N815",
        "to": "    def do_POST(self):  # noqa: N802, N815\n        self._json(200, {\"ok\": True})",
    },
    {
        "name": "off_loopback_needs_no_token",
        "why": "which intercoms a site has, and when nobody answered, on an open port",
        "file": "src/gate_agent/agent_service.py",
        "from": "    assert_bind_allowed(host, port, token)",
        "to": "    pass",
    },
]


def stage() -> Path:
    directory = Path(tempfile.mkdtemp(prefix="gate-agent-agent-control-"))
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
        [sys.executable, "-m", "pytest", "-q", "-m", "not sip"],
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
            print(f"  {brk['name']:38} *** ANCHOR NOT FOUND in {brk['file']} ***",
                  file=sys.stderr)
            failures += 1
            continue
        path.write_text(source.replace(brk["from"], brk["to"], 1), encoding="utf-8")
        broken = run(directory)
        if broken.returncode == 0:
            print(
                f"  {brk['name']:38} *** PASSED WHEN {brk['why'].upper()} —"
                " the suite is not measuring this ***",
                file=sys.stderr,
            )
            failures += 1
        else:
            print(f"  {brk['name']:38} fails as required when {brk['why']} — {tail(broken)}")
    finally:
        shutil.rmtree(directory, ignore_errors=True)

if failures:
    print(
        f"\n{failures} control(s) failed. Do not trust this agent's guarantees.",
        file=sys.stderr,
    )
    sys.exit(1)
print("\nall controls OK — the suite fails on every property the agent exists to have.")
