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

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _control import intact, judge  # noqa: E402

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
        "from": "from .ua import (\n    UaCall,",
        "to": "from .client import ReadOnlyClient  # noqa: F401\nfrom .ua import (\n    UaCall,",
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
        "why": "a call at an undeclared account is given the first declared lane",
        "file": "src/gate_agent/agent.py",
        "from": "        intercom = self._by_account.get(event.account_user or \"\")",
        "to": "        intercom = self._by_account.get(event.account_user or \"\") or next(\n"
              "            iter(self._by_account.values()), None\n"
              "        )",
    },
    {
        # X2'. THE control for the whole round: routing by the `From` header is
        # exactly what was there before, and it is what let a fourth user agent
        # assert a declared door's address of record and have a complete
        # `authorisation_received` written naming a barrier nobody was at.
        "name": "routing_by_the_from_header",
        "why": "the caller's own `From` decides which intercom it is, so the forged record returns",
        "file": "src/gate_agent/agent.py",
        "from": "        intercom = self._by_account.get(event.account_user or \"\")",
        "to": "        intercom = next(\n"
              "            (one for one in self.config.intercoms if one.sip_uri == claimed),\n"
              "            None,\n"
              "        )",
    },
    {
        # And the other half: an account that identifies nothing, because every
        # call is placed at the same intercom whatever it arrived at.
        "name": "the_account_is_ignored",
        "why": "every caller is answered as the first declared intercom",
        "file": "src/gate_agent/agent.py",
        "from": "        intercom = self._by_account.get(event.account_user or \"\")",
        "to": "        intercom = next(iter(self._by_account.values()), None)",
    },
    {
        "name": "a_dial_secret_may_be_world_readable",
        "why": "an intercom's identity is readable by every account on the box",
        "file": "src/gate_agent/config.py",
        "from": "    if mode & SECRET_FORBIDDEN_MODE:",
        "to": "    if False:",
    },
    {
        "name": "a_dial_secret_may_be_short_enough_to_type",
        "why": "the one case that needs no measurement stops being refused",
        "file": "src/gate_agent/config.py",
        "from": "    if len(secret) < MINIMUM_DIAL_SECRET:",
        "to": "    if False:",
    },
    {
        "name": "two_intercoms_may_share_a_secret",
        "why": "two doors have one identity, so a person is sent to the wrong barrier",
        "file": "src/gate_agent/config.py",
        "from": "        if account_user in accounts:",
        "to": "        if False:",
    },
    {
        "name": "a_declared_intercom_needs_no_account",
        "why": "a door the user agent never got is answered `404` and reported nowhere",
        "file": "src/gate_agent/agent.py",
        "from": "        if missing:",
        "to": "        if False:",
    },
    {
        "name": "the_dial_secret_is_published",
        "why": "the account -- which is the secret -- goes onto the read surface",
        "file": "src/gate_agent/agent.py",
        "from": "                IntercomDescription(sip_uri=intercom.sip_uri, lane=intercom.lane)",
        "to": "                IntercomDescription(sip_uri=intercom.account_user, "
              "lane=intercom.lane)",
    },
    {
        "name": "the_intercom_repr_shows_the_secret",
        "why": "every log line and traceback touching a configuration carries it",
        "file": "src/gate_agent/config.py",
        "from": "            f\"name_audio={self.name_audio!r}, account_user=<not shown>)\"",
        "to": "            f\"name_audio={self.name_audio!r}, "
              "account_user={self.account_user!r})\"",
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
        "why": "a call arriving during a case is answered instead of refused unanswered",
        "file": "src/gate_agent/agent.py",
        "from": "        if self.session is not None:",
        "to": "        if False:",
    },
    {
        "name": "human_unreachable_is_a_latch",
        "why": "the code stays active for the life of the process after one missed call",
        "file": "src/gate_agent/agent.py",
        "from": "        self._code(AgentCode.HUMAN_UNREACHABLE, self.config.human_sip_uri,"
                " HealthState.OK)",
        "to": "        pass",
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
        "from": '"en": "Please keep holding. Somebody is dealing with this.",',
        "to": '"en": "Please hold on. Somebody is dealing with this.",',
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
        # Re-proved in its new form. There is no `driver_aor` any more, so the
        # guarantee it carried -- the two legs are never on one account -- is
        # now the refusal of an operator account that collides with an
        # intercom's.
        "name": "one_account_for_both_legs",
        "why": "the two calls cannot be told apart, so the menu plays to the driver",
        "file": "src/gate_agent/config.py",
        "from": "        if account_user == operator_user:",
        "to": "        if False:",
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
    # ---- THE ROUND-5 CUT. One break per blocker, and per one-liner. ----
    {
        "name": "the_identity_is_checked_before_the_live_case",
        "why": "an undeclared caller mid-case is answered and conferenced into the bridge",
        "file": "src/gate_agent/agent.py",
        "from": "        claimed = _bare_uri(event.peer_uri)\n"
                "        if self.session is not None:",
        "to": "        claimed = _bare_uri(event.peer_uri)\n"
              "        if self.session is not None and (event.account_user or \"\") "
              "in self._by_account:",
    },
    {
        "name": "a_stale_decision_is_acted_on",
        "why": "an hour-old decision is spoken as though it were about this driver",
        "file": "src/gate_agent/cases.py",
        "from": "        if age > max_age_seconds:\n"
                "            return AgentCase.STALE_DECISION",
        "to": "        if False:\n            return AgentCase.STALE_DECISION",
    },
    {
        "name": "a_decision_with_no_moment_is_fresh",
        "why": "a decision whose age cannot be read is treated as though it were new",
        "file": "src/gate_agent/cases.py",
        "from": "        if age is None:\n"
                "            # A decision with no readable moment on it. The catch-all, for the\n"
                "            # same reason as every other answer this build will not interpret.\n"
                "            return AgentCase.UNRECOGNISED_REASON",
        "to": "        if age is None:\n            age = 0.0",
    },
    {
        "name": "a_naive_lane_timestamp_is_followed",
        "why": "a moment with no timezone is compared against an aware one anyway",
        "file": "src/gate_agent/cases.py",
        "from": "    if moment.tzinfo is None:\n        return None",
        "to": "    if moment.tzinfo is None:\n        moment = moment.replace(tzinfo=now.tzinfo)",
    },
    {
        "name": "a_line_is_retried_for_ever",
        "why": "a user agent that refuses every file leaves a driver in permanent silence",
        "file": "src/gate_agent/agent.py",
        "from": "        if started is None or now - started <= self.config.line_timeout_seconds:\n"
                "            return",
        "to": "        if True:\n            return",
    },
    {
        "name": "a_failed_line_raises_no_code",
        "why": "the leg that cannot be spoken to is not named on the health surface",
        "file": "src/gate_agent/agent.py",
        "from": "        self._code(AgentCode.AUDIO_PLAYBACK_FAILED, leg.value, "
                "HealthState.ACTIVE)\n        session.speech[leg].clear()",
        "to": "        session.speech[leg].clear()",
    },
    {
        "name": "a_case_nobody_could_be_told_holds_the_call_open",
        "why": "neither leg can be spoken to and the driver's call is never released",
        "file": "src/gate_agent/agent.py",
        "from": "        self._not_spoken(session)\n\n    def _not_spoken",
        "to": "        return\n\n    def _not_spoken",
    },
    {
        "name": "a_torn_down_case_is_still_bridged",
        "why": "the case ended mid-poll and the rest of the same poll bridges it anyway",
        "file": "src/gate_agent/agent.py",
        "from": "        if self.session is not session:",
        "to": "        if False:",
    },
    {
        "name": "case_spoken_is_written_when_it_is_queued",
        "why": "the record says a driver was told their case at the moment it was queued",
        "file": "src/gate_agent/agent.py",
        "from": "        self._say(session, UaLeg.DRIVER, f\"case.{session.case.value}\")",
        "to": "        self._spoken(session)\n"
              "        self._say(session, UaLeg.DRIVER, f\"case.{session.case.value}\")",
    },
    {
        "name": "the_baresip_configuration_is_not_checked",
        "why": "the agent starts against an aubridge baresip, as the false sentence claimed",
        "file": "src/gate_agent/ua_baresip.py",
        "from": "        self._check_configuration()",
        "to": "        pass",
    },
    {
        "name": "a_missing_baresip_module_is_not_named",
        "why": "a baresip with no mixminus starts, and the bridge silently does not exist",
        "file": "src/gate_agent/ua_baresip.py",
        "from": "        missing = [one for one in REQUIRED_MODULES if one not in loaded_modules]",
        "to": "        missing = []",
    },
    {
        "name": "the_control_socket_is_never_reopened",
        "why": "a lost socket is a permanent outage and a human has to restart the agent",
        "file": "src/gate_agent/agent.py",
        "from": "        reconnect = getattr(self.ua, \"reconnect\", None)\n"
                "        if reconnect is None:\n            return",
        "to": "        reconnect = getattr(self.ua, \"reconnect\", None)\n        return",
    },
    {
        "name": "a_lost_socket_is_kept",
        "why": "the dead socket is left in place, so nothing can ever reopen it",
        "file": "src/gate_agent/ua_baresip.py",
        "from": "        self._sock = None\n        self._buffer = b\"\"\n"
                "        self._schedule_retry()",
        "to": "        self._buffer = b\"\"\n        self._schedule_retry()",
    },
    {
        "name": "an_orphaned_call_survives_a_reconnect",
        "why": "a leg left live after the socket came back is conferenced into the next case",
        "file": "src/gate_agent/agent.py",
        "from": "            try:\n                self.ua.hangup(call.call_id)\n"
                "            except UaUnreachable as exc:",
        "to": "            try:\n                pass\n"
              "            except UaUnreachable as exc:",
    },
    {
        "name": "the_backoff_is_unbounded",
        "why": "the gap between attempts grows past the site's setting and never comes back",
        "file": "src/gate_agent/ua_baresip.py",
        "from": "        self._retry_gap = min(max(self._retry_gap * 2, RECONNECT_FLOOR),\n"
                "                              max(self.reconnect_seconds, RECONNECT_FLOOR))",
        "to": "        self._retry_gap = self._retry_gap * 2\n"
              "        self._retry_gap = max(self._retry_gap, RECONNECT_FLOOR)",
    },
    {
        "name": "the_sample_rate_is_not_checked",
        "why": "the one file this package does not produce plays at the wrong rate",
        "file": "src/gate_agent/agent.py",
        "from": "            if channels != 1 or width != 2 or rate != NARROWBAND_RATE:",
        "to": "            if channels != 1 or width != 2:",
    },
    {
        "name": "a_name_audio_may_be_any_length",
        "why": "a driver is held in a never-bridged call for as long as the site's file lasts",
        "file": "src/gate_agent/agent.py",
        "from": "            if path in site_files and seconds > "
                "self.config.name_audio_max_seconds:",
        "to": "            if False:",
    },
    {
        "name": "the_operator_hanging_up_says_the_wrong_thing",
        "why": "a driver is told an instruction could not be taken when the person hung up",
        "file": "src/gate_agent/agent.py",
        "from": '            "driver.operator_hung_up" if hung_up else "driver.nothing_usable",',
        "to": '            "driver.nothing_usable",',
    },
    {
        "name": "the_two_clocks_note_gets_a_second_copy",
        "why": "the contract's sentence and the code's stop being one copy",
        "file": "src/gate_agent/cases.py",
        "from": "    \"NEGATIVE AGE IS REACHABLE -- a decision stamped after the moment this \"",
        "to": "    \"NEGATIVE AGE IS IMPOSSIBLE -- a decision stamped after the moment this \"",
    },
    {
        "name": "the_busy_refusal_is_justified_again",
        "why": "an unmeasured claim about a door station's call list comes back",
        "file": "docs/CONTRACT.md",
        "from": "**What the refusal IS, measured from the caller's side:**",
        "to": "That is deliberate: an unanswered call is what makes the intercom's own call\n"
              "list move on to the human's number.\n\n"
              "**What the refusal IS, measured from the caller's side:**",
    },
    {
        "name": "the_spanish_loses_its_region",
        "why": "Castilian ships under a generic tag and a site cannot see which it got",
        "file": "src/gate_agent/lines.py",
        "from": 'SHIPPED_LANGUAGES: tuple[str, ...] = ("en", "es-ES")',
        "to": 'SHIPPED_LANGUAGES: tuple[str, ...] = ("en", "es")',
    },
    {
        "name": "the_text_has_no_provenance",
        "why": "who wrote the words, and whether anybody reviewed them, is unrecorded again",
        "file": "src/gate_agent/audio/MANIFEST.json",
        "from": '  "text_provenance": {',
        "to": '  "text_provenance_removed": {',
    },
]


def stage() -> Path:
    directory = Path(tempfile.mkdtemp(prefix="gate-agent-agent-control-"))
    # `scripts` is here because the audio build script is itself a published
    # claim -- the manifest's provenance rows come out of it -- and a break that
    # cannot reach it is a guarantee nobody is measuring.
    for entry in ("src", "tests", "docs", "config", "scripts", "pyproject.toml"):
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


failures = 0

print("== control A: the suite must PASS intact ==")
intact_dir = stage()
try:
    COLLECTED = intact(run(intact_dir))
    if COLLECTED < 0:
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
            # reason. Named here so the two cannot be confused. The judgement
            # below catches the OTHER shape of the same mistake: an anchor that
            # is still there but whose replacement makes the suite ERROR.
            print(f"  {brk['name']:38} *** ANCHOR NOT FOUND in {brk['file']} ***",
                  file=sys.stderr)
            failures += 1
            continue
        path.write_text(source.replace(brk["from"], brk["to"], 1), encoding="utf-8")
        if not judge(brk["name"], brk["why"], COLLECTED, run(directory), width=38):
            failures += 1
    finally:
        shutil.rmtree(directory, ignore_errors=True)

if failures:
    print(
        f"\n{failures} control(s) failed. Do not trust this agent's guarantees.",
        file=sys.stderr,
    )
    sys.exit(1)
print("\nall controls OK — the suite fails on every property the agent exists to have.")
