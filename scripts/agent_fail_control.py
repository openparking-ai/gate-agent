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
    # `an_act_exists` STOOD HERE and is retired, not lost. It planted an entry
    # in an empty `ACTS` and required `can_vend` to stop being false -- which
    # measured that the table was empty, and round 7 fills it by design. What it
    # was really protecting is that `can_vend` is DERIVED and not written down;
    # `can_vend_ignores_the_act_token` below is that property under the new
    # mechanism, and it is a stronger question: not "can anything act" but "does
    # this agent hold what acting at THIS lane needs".
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
        # It used to be spelt `a_dial_secret_may_be_world_readable`, because the
        # dial secret was the only key with a guard. The guard is now one
        # function and every credential file goes through it, so this one break
        # takes the permission check off all six at once -- and the suite has a
        # refusal per key to go red.
        "name": "a_credential_file_may_be_world_readable",
        "why": "every credential this package reads may be read by every account on the box",
        "file": "src/gate_agent/config.py",
        "from": "    if mode & SECRET_FORBIDDEN_MODE:",
        "to": "    if False:",
    },
    {
        "name": "a_credential_is_read_without_the_guard",
        "why": "one key goes back to reading its own file, so the guard has a hole again",
        "file": "src/gate_agent/config.py",
        "from": '            token = read_secret_file(table["token_file"], '
                'f"[lanes.{name}].token_file", relative_to)',
        "to": '            token = Path(table["token_file"]).read_text('
              'encoding="utf-8").strip()',
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
        "from": "                    sip_uri=intercom.sip_uri,",
        "to": "                    sip_uri=intercom.account_user,",
    },
    {
        "name": "the_intercom_repr_shows_the_secret",
        "why": "every log line and traceback touching a configuration carries it",
        "file": "src/gate_agent/config.py",
        "from": '            f"relay={self.relay!r}, account_user=<not shown>)"',
        "to": '            f"relay={self.relay!r}, account_user={self.account_user!r})"',
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
        # It used to anchor on `if value in CANNOT_ACT:`, a typed tuple that no
        # longer exists: whether an authorisation CAN act is now a question
        # about the lane rather than about the authorisation. The property is
        # the same one and this is the half where the sentence must be SAID;
        # `cannot_open_is_spoken_where_something_can_act` is the half where it
        # must not be.
        "name": "the_person_is_never_told_it_cannot_open",
        "why": "`open_now` is recorded and the person is left believing a barrier moved",
        "file": "src/gate_agent/agent.py",
        "from": '            self._say(session, UaLeg.OPERATOR, "operator.cannot_open",\n'
                "                      operator=True)",
        "to": "            pass",
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
        # F6. The standalone relay, and the invariant it has to meet.
        "name": "the_relay_pulses_before_the_record_is_written",
        "why": "a barrier opens with nothing written down, which is the invariant broken",
        "file": "src/gate_agent/agent.py",
        "from": "        pending = self._mint_standalone(session.intercom)",
        "to": "        pending = None",
    },
    {
        "name": "a_relay_may_sit_beside_a_lane",
        "why": "two things open one barrier and the lane has no record of one of them",
        "file": "src/gate_agent/config.py",
        "from": "    if lane != STANDALONE:",
        "to": "    if False:",
    },
    {
        "name": "the_relay_action_is_not_percent_encoded",
        "why": "a `/` reaches the wire as a path separator and the request misses the CGI",
        "file": "src/gate_agent/relay.py",
        "from": '        query = "action=" + urllib.parse.quote(action, safe="")',
        "to": '        query = "action=" + urllib.parse.quote(action)',
    },
    {
        "name": "any_body_from_the_relay_is_a_success",
        "why": "a login page answering 200 is read as a barrier that was asked to open",
        "file": "src/gate_agent/relay.py",
        "from": "        if body.strip():",
        "to": "        if False:",
    },
    {
        "name": "a_relay_kind_this_build_cannot_drive_is_accepted",
        "why": "a 2N unit is sent Axis's own CGI and the site is told it opened",
        "file": "src/gate_agent/relay.py",
        "from": "    if relay.kind != AXIS_VAPIX:",
        "to": "    if False:",
    },
    {
        "name": "the_relay_credential_is_in_the_repr",
        "why": "a password that pulses a barrier reaches every traceback and log line",
        "file": "src/gate_agent/relay.py",
        # RE-ANCHORED 2026-09-01 (Z16.1): Z3 added `answer_margin_s` to this
        # repr, which moved the password onto a line of its own. The break then
        # applied to nothing and the control measured nothing.
        "from": '            f"username={self.username!r}, password=<not shown>)"',
        "to": '            f"username={self.username!r}, password={self.password!r})"',
    },
    {
        # F3-F5. The ticket, the press and the vend. Every break here is one
        # that fails in the REASSURING direction: a code on a screen for a car
        # nobody measured, a second vend for one arrival, a barrier told to open
        # on a decision that was not the one asked about.
        "name": "an_unmeasured_presence_gets_a_ticket",
        "why": "`None` is read as presence, so a code goes up for a car nobody measured",
        # RE-ANCHORED 2026-09-01 (Z16.1). Z1 moved the offer decision out of
        # `agent.py` into `cases.offers_ticket`, so this moves with it -- FILE
        # included. **This is a fraud-boundary control** (SETTLED 3f: an
        # unmeasured presence must never cause a transaction), so it is
        # re-anchored rather than retired, whatever it costs.
        "file": "src/gate_agent/cases.py",
        "from": "        and reading.presence is True\n    )",
        "to": "        and reading.presence is not False\n    )",
    },
    {
        "name": "presence_is_read_as_truthiness",
        "why": "a lane publishing the string `false` is read as a vehicle being there",
        "file": "src/gate_agent/agent.py",
        "from": "    return value if isinstance(value, bool) else None",
        "to": "    return bool(value) if value is not None else None",
    },
    {
        "name": "any_case_gets_a_ticket",
        "why": "a refused vehicle and an empty lane are offered a code as well",
        # RE-ANCHORED 2026-09-01 (Z16.1): moved into `cases.offers_ticket` by Z1.
        "file": "src/gate_agent/cases.py",
        "from": "        decision_case(reading, now, max_age_seconds) in TICKET_CASES\n",
        "to": "        True\n",
    },
    {
        "name": "a_new_decision_leaves_the_old_ticket_up",
        "why": "a code stays on a screen for the car in front of the one at the barrier",
        "file": "src/gate_agent/agent.py",
        # RE-ANCHORED 2026-09-01 (Z16.1): Z6 inserted `_end_help_at` between
        # the void and the offer check, so the old two-line anchor stopped
        # matching. Anchored on the comment that follows instead.
        "from": '        self._void_at(lane, "lane_decided_again")\n'
                "        # AND IT ENDS THE HELP WINDOW.",
        "to": "        # AND IT ENDS THE HELP WINDOW.",
    },
    {
        "name": "a_ticket_never_expires",
        "why": "a code from an hour ago is still confirmable by whoever walks up next",
        "file": "src/gate_agent/agent.py",
        "from": "            if now >= pending.expires:",
        "to": "            if False:",
    },
    {
        "name": "a_first_read_acts_on_the_whole_window",
        "why": "a restarted agent puts up a code for a car that has already gone",
        "file": "src/gate_agent/agent.py",
        "from": "            self._cursors[lane.name] = cursor\n"
                '            log.info("following lane %s from cursor %d", lane.name, cursor)\n'
                "            return",
        "to": "            self._cursors[lane.name] = 0",
    },
    {
        "name": "the_help_window_is_ignored",
        "why": "a second press is a second vend, and one arrival becomes two stays",
        "file": "src/gate_agent/agent.py",
        # RE-ANCHORED 2026-09-01 (Z16.1): Z6 replaced `_confirmed_at` and
        # `_help_lines` with one `Help` record per intercom, so the clock
        # comparison this named no longer exists. `_help_at` is where the
        # window is now recognised; a `None` from it is "this is not help",
        # which is a second vend.
        "from": "        help_window = self._help.get(intercom.sip_uri)\n"
                "        if help_window is None:\n            return None",
        "to": "        help_window = self._help.get(intercom.sip_uri)\n"
              "        if True:\n            return None",
    },
    {
        "name": "decision_at_is_invented",
        "why": "the vend names this process's clock instead of the decision it completes",
        "file": "src/gate_agent/agent.py",
        "from": "                decision_at=pending.decision_at,",
        "to": "                decision_at=self._now(),",
    },
    {
        "name": "the_idempotency_key_is_fresh_each_time",
        "why": "a retry vends a second time, which is the commonest idempotency bug there is",
        "file": "src/gate_agent/agent.py",
        "from": "                idempotency_key=pending.ticket.ticket_id,",
        "to": "                idempotency_key=__import__('secrets')\n"
              "                .token_bytes(16).hex().upper(),",
    },
    {
        "name": "a_vend_is_commanded_without_an_act_token",
        "why": "a read-only lane is asked to open, and the read token is offered as authority",
        "file": "src/gate_agent/act.py",
        "from": "        if not act_token:",
        "to": "        if False:",
    },
    {
        "name": "the_driver_is_told_the_barrier_is_open",
        "why": "an unmeasured claim about a boom nothing in this estate has watched move",
        "file": "src/gate_agent/lines.py",
        "from": "The barrier has been asked to open. Please drive forward when it does.",
        "to": "The barrier is now open. Please drive forward.",
    },
    {
        "name": "a_refusal_code_reaches_nobody",
        "why": "the person is told it was refused and not told which refusal it was",
        "file": "src/gate_agent/agent.py",
        # RE-ANCHORED 2026-09-01 (Z16.1): Z5 made the briefing ALWAYS carry
        # `operator.ticket_refused` and then the code's sentence or
        # `operator.vend_refused.unknown`, so the conditional tuple this named
        # is gone. Emptying the pair is the same reassuring failure.
        "from": '        lines = ("operator.ticket_refused", refusal)',
        "to": "        lines = ()",
    },
    {
        "name": "cannot_open_is_spoken_where_something_can_act",
        "why": "a person is told the system cannot open a barrier it is about to open",
        "file": "src/gate_agent/agent.py",
        "from": "        if lane is None or pending is None or lane not in self._acts:",
        "to": "        if True:",
    },
    {
        # Z19. The DRIVER's half of the same branch. The person's sentence was
        # made conditional in round 7 and the driver's was not, so at a door
        # that can act the driver heard "this system cannot open the barrier
        # itself" and then "the barrier has been asked to open" -- the false one
        # first. This restores the unconditional key.
        "name": "the_driver_is_told_it_cannot_open_where_it_can",
        "why": "the driver is told this system has no route to the barrier, "
               "one sentence before it asks one to open",
        "file": "src/gate_agent/agent.py",
        "from": '        key = f"authorisation.{value.value}"\n'
                '        self._say(session, UaLeg.DRIVER, f"{key}.acting" if acting else key)',
        "to": '        self._say(session, UaLeg.DRIVER, f"authorisation.{value.value}")',
    },
    {
        "name": "the_ticket_ref_goes_on_an_event",
        "why": "the identifier of a stay reaches a surface outside the retention rule",
        "file": "src/gate_agent/agent.py",
        # RE-ANCHORED 2026-09-01 (Z16.1): Z2 added `_call_being_spoken_at`
        # after this emission, so the trailing `def _show` stopped matching.
        "from": "            ticket_id=ticket.ticket_id,\n        )\n\n"
                "    def _call_being_spoken_at",
        "to": "            ticket_id=ticket.ticket_id,\n"
              "            keyed=ticket.ticket_ref,\n        )\n\n"
              "    def _call_being_spoken_at",
    },
    {
        "name": "can_vend_ignores_the_act_token",
        "why": "the surface says a lane can be vended at when this agent holds nothing",
        "file": "src/gate_agent/agent.py",
        "from": "                    can_vend=lane.name in self._acts\n"
                "                    and self.config.tickets is not None,",
        "to": "                    can_vend=True,",
    },
    {
        # F2. The display and the font.
        "name": "an_idle_display_is_white",
        "why": "a blank frame is a floodlight pointed at a windscreen at night",
        "file": "src/gate_agent/display.py",
        "from": "        self.show([[1] * width for _ in range(height)])",
        "to": "        self.show([[0] * width for _ in range(height)])",
    },
    {
        "name": "a_padded_stride_is_ignored",
        "why": "a frame is written at the wrong stride, which is diagonal noise",
        "file": "src/gate_agent/display.py",
        "from": "            stride = max(int(published), stride)",
        "to": "            stride = stride",
    },
    {
        "name": "a_depth_this_build_cannot_write_is_accepted",
        "why": "a monochrome frame goes to a screen whose channel order it has guessed",
        "file": "src/gate_agent/display.py",
        "from": "    if depth not in SUPPORTED_DEPTHS:",
        "to": "    if False:",
    },
    {
        "name": "the_symbol_is_scaled_by_a_fraction_of_a_module",
        "why": "a module drawn 3.4 pixels wide is one a camera reads wrongly at its edges",
        "file": "src/gate_agent/display.py",
        "from": "    scale = max(1, int(short * SYMBOL_SHARE) // across)",
        "to": "    scale = max(1, int(short * SYMBOL_SHARE * 1.4) // across)",
    },
    {
        "name": "a_character_with_no_glyph_is_left_blank",
        "why": "a hole in the frame instead of a startup refusal naming the string",
        "file": "src/gate_agent/font.py",
        # RE-ANCHORED 2026-09-01 (Z16.1), and this one was never a control at
        # all rather than having drifted. It broke `cell`'s raise -- which
        # `render` never reaches, because `missing()` is consulted first and
        # raises with the naming message. Breaking a SHADOWED guard changes
        # nothing observable, so the suite stayed green and the break reported
        # PASSED WHEN. Anchored on `render`'s own guard, which is what turns an
        # undrawable character into a refusal instead of a hole.
        "from": "    absent = missing(text)\n"
                "    if absent:\n"
                "        raise UndrawableCharacter(\n"
                "            f\"this font has no glyph for "
                "{', '.join(repr(one) for one in absent)}\"\n"
                "        )\n"
                "    rows = [[0] * width_of(text) for _ in range(CELL_HEIGHT)]\n"
                "    for index, character in enumerate(text):\n"
                "        left = index * (GLYPH_WIDTH + TRACKING)\n"
                "        for row, line in enumerate(cell(character)):",
        "to": "    rows = [[0] * width_of(text) for _ in range(CELL_HEIGHT)]\n"
              "    for index, character in enumerate(text):\n"
              "        left = index * (GLYPH_WIDTH + TRACKING)\n"
              "        for row, line in enumerate(\n"
              "            cell(character)\n"
              "            if character in DRAWABLE\n"
              "            else (\".\" * GLYPH_WIDTH,) * CELL_HEIGHT\n"
              "        ):",
    },
    {
        "name": "a_display_language_with_no_words_is_accepted",
        "why": "a driver is shown a code with no instruction under it in their language",
        "file": "src/gate_agent/config.py",
        "from": "    missing = missing_display_text(driver_languages)\n    if missing:",
        "to": "    missing = missing_display_text(driver_languages)\n    if False:",
    },
    {
        "name": "a_display_nobody_declared_is_accepted",
        "why": "a door publishes `has_display` and shows a driver nothing",
        "file": "src/gate_agent/config.py",
        "from": "            if display not in (displays or {}):",
        "to": "            if False:",
    },
    {
        # F2. The QR encoder. Every break here is one the independent decoder
        # in `tests/test_qr.py` has to notice -- an encoder proven only by
        # itself is a picture of a QR code.
        "name": "the_mask_is_never_chosen",
        "why": "one fixed mask is used, so a symbol a decoder cannot lock on to still ships",
        "file": "src/gate_agent/qr.py",
        "from": "        if best is None or score < best[0]:",
        "to": "        if best is None:",
    },
    {
        "name": "the_error_correction_is_dropped",
        "why": "a symbol ships with no recovery, so a thumbprint loses a ticket",
        "file": "src/gate_agent/qr.py",
        "from": "    ec_blocks = [error_correction(block, ec_per_block) for block in blocks]",
        "to": "    ec_blocks = [[0] * ec_per_block for _ in blocks]",
    },
    {
        "name": "the_format_information_names_the_wrong_level",
        "why": "a decoder unmasks with the wrong level and reads gibberish",
        "file": "src/gate_agent/qr.py",
        "from": "EC_M = 0b00",
        "to": "EC_M = 0b01",
    },
    {
        "name": "the_block_table_is_off_by_one",
        "why": "the codewords no longer fill the symbol, and nothing said so",
        "file": "src/gate_agent/qr.py",
        "from": "    6: (16, ((4, 27),)),",
        "to": "    6: (16, ((4, 28),)),",
    },
    {
        "name": "a_payload_too_long_is_truncated",
        "why": "a ticket is cut to fit, and the exit reads the remainder as a forgery",
        "file": "src/gate_agent/qr.py",
        "from": "    raise QrTooLong(\n        f\"a payload of ",
        "to": "    return MAX_VERSION, picked\n    raise QrTooLong(\n        f\"a payload of ",
    },
    {
        # F1. A SOURCE property, and it has to be: `list(a) != list(b)` behaves
        # identically to `compare_digest` and differs only in TIMING, which
        # nothing in this suite measures and which a timing test would measure
        # flakily. So the sweep reads the comparison out of the source, the way
        # the no-opening-authority sweeps read the request builders.
        "name": "the_signature_is_compared_with_equals",
        "why": "a forged signature leaks how much of it was right, one byte at a time",
        "file": "src/gate_agent/tickets.py",
        "from": "    if not hmac.compare_digest(signature, expected):",
        "to": "    if list(signature) != list(expected):",
    },
    {
        "name": "a_ticket_is_verified_without_its_signature",
        "why": "any well-formed payload is accepted, so anybody can mint this site's tickets",
        "file": "src/gate_agent/tickets.py",
        "from": "    if not hmac.compare_digest(signature, expected):",
        "to": "    if False:",
    },
    {
        "name": "a_ticket_has_more_than_one_spelling",
        "why": "base32 padding bits go unchecked, so one ticket has several payloads",
        "file": "src/gate_agent/tickets.py",
        "from": '    if base64.b32encode(raw).decode("ascii").rstrip("=") != text:',
        "to": "    if False:",
    },
    {
        "name": "a_reference_may_hold_a_confusable",
        "why": "`I`, `O`, `0` and `1` come back, and a person reads one out wrongly",
        "file": "src/gate_agent/tickets.py",
        "from": 'TICKET_REF_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"',
        "to": 'TICKET_REF_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ012345"',
    },
    {
        "name": "a_ticket_ref_is_drawn_from_random",
        "why": "references become predictable, so the next one can be guessed",
        "file": "src/gate_agent/tickets.py",
        "from": "            secrets.choice(TICKET_REF_ALPHABET) for _ in range(TICKET_REF_LENGTH)",
        "to": "            TICKET_REF_ALPHABET[0] for _ in range(TICKET_REF_LENGTH)",
    },
    {
        "name": "the_signing_key_may_be_typed",
        "why": "the one case needing no measurement stops being refused",
        "file": "src/gate_agent/config.py",
        "from": "    if len(key) < MINIMUM_SIGNING_KEY:",
        "to": "    if False:",
    },
    {
        # Flipping `repr=False` on the decorator was a NO-OP and this is why:
        # `dataclass` does not overwrite a `__repr__` defined in the class body,
        # so the explicit one won either way and the break measured nothing.
        # The break has to be to the method that actually renders it.
        "name": "the_signing_key_is_in_the_repr",
        "why": "the key every ticket is signed with reaches every traceback and log line",
        "file": "src/gate_agent/config.py",
        "from": '            f"TicketSettings(signing_key=<not shown>, '
                'directory={self.directory!r}, "',
        "to": '            f"TicketSettings(signing_key={self.signing_key!r}, '
              'directory={self.directory!r}, "',
    },
    {
        "name": "the_purge_keeps_what_it_cannot_read",
        "why": "a purge deletes on an age it never measured",
        "file": "src/gate_agent/tickets.py",
        "from": "                log.warning(\n"
                '                    "%s: a ticket record with no readable issued_at", path.name\n'
                "                )\n"
                "                continue",
        "to": "                path.unlink(missing_ok=True)\n"
              "                removed += 1\n"
              "                continue",
    },
    {
        "name": "a_ticket_id_may_become_a_path",
        "why": "a record with a `..` in its id is written somewhere nobody declared",
        "file": "src/gate_agent/tickets.py",
        "from": "        if not _RECORD_ID.match(record.ticket_id):",
        "to": "        if False:",
    },
    {
        # F0.7. The round-5 merge gate's other open item. `_reconnect()` covers
        # a socket lost inside a RUNNING process; `start()` covered nothing, so
        # a restarted agent answered the next call with the previous process's
        # legs still live -- and this user agent's bridge is site-wide.
        "name": "a_restart_keeps_the_previous_processs_calls",
        "why": "a new process leaves the old one's legs up, to be bridged into the next case",
        "file": "src/gate_agent/agent.py",
        "from": "        self._release_leftover_calls()",
        "to": "        pass",
    },
    {
        # F0.6. The flake that turned `main` red on a tree CI had already
        # passed twice: a handler thread still inside `do_GET` when the
        # interpreter finalises, killed mid-write, exit 134 after every test
        # passed. Restoring the daemon default takes `server_close()`'s
        # tracking away and the control in
        # `tests/test_no_handler_thread_survives.py` goes red.
        "name": "a_handler_thread_may_outlive_its_test",
        "why": "handler threads are daemons again, so server_close() has nothing to join",
        "file": "tests/serving.py",
        "from": "    server.daemon_threads = False",
        "to": "    server.daemon_threads = True",
    },
    {
        # Z16.2, 2026-09-01. The banner said "OPENS NOTHING: no vend route here"
        # at every start of the round that GAVE this package a vend route, and
        # nothing was measuring it because a fixed sentence cannot go stale in a
        # way a test can see. The line is derived now; this is what proves it is
        # still derived.
        #
        # RE-ANCHORED by Z17, 2026-09-03, and the move is the point: the sentence
        # now lives in `config.opening_line` beside `AgentConfig.act_surface`, the
        # ONE property both it and the served `405` body render. Breaking it here
        # breaks both surfaces at once, which is what one source is for.
        "name": "the_startup_line_is_written_down_again",
        "why": "every operator is told at every start that this process opens nothing, "
               "while it holds an act token for a lane",
        "file": "src/gate_agent/config.py",
        "from": "    surface = config.act_surface",
        "to": "    return (\n"
              "        \"  OPENS NOTHING: no vend route here, none at any lane on this \"\n"
              "        \"contract version\"\n"
              "    )\n"
              "    surface = config.act_surface",
    },
    {
        # Z17, 2026-09-03. THE CONTROL FOR THE SWEEP, and it is the break the
        # whole round exists for: the README's own sentence, put back, in the file
        # a reader of this repository opens first.
        #
        # It is planted in README.md rather than in a module because that is where
        # the claim did the most damage and because staging the README is itself
        # part of the repair -- while it was not staged, this break could not have
        # been written at all.
        "name": "the_readme_says_the_package_cannot_open_a_barrier",
        "why": "the front page of a public repository states, as a property, that nothing "
               "here can open a barrier -- in the round that gave it a vend route",
        "file": "README.md",
        "from": "The intercom module. It ships **three processes**. Two of them open nothing; the",
        "to": "The intercom module. It ships **three processes**, and none of them can open a\n"
              "barrier. There is no client in this package capable of a method other than "
              "`GET`.\n"
              "The intercom module. It ships **three processes**. Two of them open nothing; the",
    },
    {
        # Z17, 2026-09-03. The other half of the same guarantee, on the surface
        # that is not prose: the JSON body a caller receives.
        "name": "the_served_refusal_is_written_down_again",
        "why": "a caller is told this agent opens nothing at any lane by a process that is "
               "holding an act token for one",
        "file": "src/gate_agent/agent_service.py",
        "from": "        surface = self.service.act_surface()",
        "to": "        surface = ()",
    },
    {
        "name": "the_text_has_no_provenance",
        "why": "who wrote the words, and whether anybody reviewed them, is unrecorded again",
        "file": "src/gate_agent/audio/MANIFEST.json",
        "from": '  "text_provenance": {',
        "to": '  "text_provenance_removed": {',
    },
    # -----------------------------------------------------------------
    # THE ROUND-7 CUT. One break per blocker the L3 found, each of them a
    # revert of the cut rather than a nearby edit -- the question a control has
    # to answer is "does the thing that was fixed still fail without the fix".
    # -----------------------------------------------------------------
    {
        "name": "the_offer_consults_the_health",
        "why": "a ticket is suppressed by any active code, so a lane whose engine is "
               "down offers none -- the case the module exists for",
        "file": "src/gate_agent/cases.py",
        "from": "    return (\n        decision_case(reading, now, max_age_seconds) "
                "in TICKET_CASES\n        and reading.presence is True\n    )",
        "to": "    return (\n        not reading.malfunctions\n"
              "        and decision_case(reading, now, max_age_seconds) in TICKET_CASES\n"
              "        and reading.presence is True\n    )",
    },
    {
        "name": "the_press_does_not_mint",
        "why": "a press inside the poll gap rings a person while the ticket is minted "
               "behind the driver",
        "file": "src/gate_agent/agent.py",
        "from": "        elif pending is None and intercom.display and self._offers_a_ticket_at(",
        "to": "        elif False and pending is None and intercom.display and self._offers_a_ticket_at(",  # noqa: E501
    },
    {
        "name": "a_press_confirms_an_untold_ticket",
        "why": "the vend of a code the driver was never shown and never photographed",
        "file": "src/gate_agent/agent.py",
        "from": "            if pending.told_at is None:",
        "to": "            if False:",
    },
    {
        "name": "relay_pulsed_is_written_whatever_happened",
        "why": "the site's only machine-readable account of a barrier says the relay was "
               "pulsed when it was not",
        "file": "src/gate_agent/agent.py",
        "from": "        if pulse.outcome == \"\":",
        "to": "        if True:",
    },
    {
        "name": "the_relay_maps_only_the_classes_it_used_to",
        "why": "a challenge urllib cannot parse raises out of poll(), the barrier does not "
               "move, and the operator who authorised it is told nothing",
        "file": "src/gate_agent/relay.py",
        "from": "        except Exception as exc:  # noqa: BLE001",
        "to": "        except (urllib.error.URLError, TimeoutError, OSError) as exc:",
    },
    {
        "name": "an_answered_unit_is_reported_as_silence",
        "why": "`RelayUnreachable` for a unit that answered sends somebody to look at a "
               "network instead of at the device",
        "file": "src/gate_agent/relay.py",
        "from": "            if self._answered.seen:",
        "to": "            if False:",
    },
    {
        "name": "the_relay_timeout_is_fixed_again",
        "why": "a legal six-second barrier is reported as a relay that could not be reached, "
               "while the unit is mid-pulse",
        "file": "src/gate_agent/relay.py",
        "from": "        self.timeout = relay.timeout if timeout is None else timeout",
        "to": "        self.timeout = 5.0 if timeout is None else timeout",
    },
    {
        "name": "pulse_ms_is_unbounded_again",
        "why": "an unbounded pulse is an unbounded time this process holds a connection "
               "open for one press",
        "file": "src/gate_agent/config.py",
        "from": "    if not PULSE_MS_BOUNDS[0] <= pulse <= PULSE_MS_BOUNDS[1]:",
        "to": "    if False:",
    },
    {
        "name": "a_refused_vend_is_lane_decided_again",
        "why": "the record asserts a cause that did not happen, in the one place a "
               "standalone site keeps",
        "file": "src/gate_agent/agent.py",
        "from": "        self._finish_ticket(pending, \"lane_refused\", answer.code)",
        "to": "        self._finish_ticket(pending, \"lane_decided_again\", answer.code)",
    },
    {
        "name": "an_unreachable_lane_is_lane_decided_again",
        "why": "the same wrong cause on the path where there is no lane_answer either",
        "file": "src/gate_agent/agent.py",
        "from": "            self._finish_ticket(pending, \"lane_unreachable\", None)",
        "to": "            self._finish_ticket(pending, \"lane_decided_again\", None)",
    },
    {
        "name": "an_act_the_lane_refuses_is_lane_decided_again",
        "why": "a 401 on the vend route is recorded as a decision the lane never made",
        "file": "src/gate_agent/agent.py",
        "from": "            self._finish_ticket(pending, \"act_refused\", None)",
        "to": "            self._finish_ticket(pending, \"lane_decided_again\", None)",
    },
    {
        "name": "a_refusal_with_no_words_reaches_nobody",
        "why": "a third party's own refusal code leaves the person briefed as an ordinary "
               "case and then offered OPEN_NOW",
        "file": "src/gate_agent/agent.py",
        "from": "        refusal = named if named in self._durations_for_lines() "
                "else UNKNOWN_REFUSAL",
        "to": "        refusal = named if named in self._durations_for_lines() else None",
    },
    {
        "name": "the_person_is_not_told_a_ticket_was_refused",
        "why": "the operator is briefed as an ordinary case with no line saying a ticket "
               "was confirmed and refused",
        "file": "src/gate_agent/agent.py",
        "from": "        lines = (\"operator.ticket_refused\", refusal)",
        "to": "        lines = (refusal,)",
    },
    {
        "name": "the_help_window_outlives_its_ticket",
        "why": "the NEXT driver is briefed with two sentences that are false about them, "
               "and the operator decides on that briefing",
        "file": "src/gate_agent/agent.py",
        "from": "            if help_window.lane == lane:\n                del self._help[uri]",
        "to": "            if False:\n                del self._help[uri]",
    },
    {
        "name": "a_restart_settles_nothing",
        "why": "`restarted` goes back to being a published reason no code path writes, and "
               "a confirmed record stands with nothing saying whether the barrier opened",
        "file": "src/gate_agent/agent.py",
        "from": "        for ticket_id in self._store.all_ids():",
        "to": "        for ticket_id in ():",
    },
    {
        "name": "nothing_blanks_a_screen_on_exit",
        "why": "an ordinary `systemctl restart` leaves the last ticket on the screen",
        "file": "src/gate_agent/cli.py",
        "from": "        _blank_displays(config)",
        "to": "        pass",
    },
    {
        "name": "sigterm_never_reaches_python",
        "why": "the `finally` that blanks the screens is never run by the signal a service "
               "manager actually sends",
        "file": "src/gate_agent/cli.py",
        "from": "    _raise_on_sigterm()",
        "to": "    pass",
    },
    {
        "name": "the_screen_is_never_re_asserted",
        "why": "a display that dies between frames leaves a code the driver cannot see, a "
               "health surface saying ok, and a press that vends it",
        "file": "src/gate_agent/agent.py",
        "from": "            self._reassert(lane.name)",
        "to": "            pass",
    },
    {
        "name": "the_geometry_is_read_once",
        "why": "a framebuffer that changes mode is written at the old stride, which is "
               "diagonal noise, while the agent believes a code is up",
        "file": "src/gate_agent/agent.py",
        "from": "                now = screen.reread_geometry()",
        "to": "                now = screen.geometry",
    },
    {
        # NOT the round-6 break of the same shape: that one is `padding bits go
        # unchecked` above, and a second entry with its name would have been two
        # rows reporting one measurement.
        "name": "case_and_whitespace_spellings_verify",
        "why": "lower case, mixed case and whitespace all verify, so the exit can file one "
               "stay twice",
        "file": "src/gate_agent/tickets.py",
        "from": "    text = payload\n",
        "to": "    text = payload.strip().upper()\n",
    },
    {
        "name": "two_screens_on_one_lane_are_accepted",
        "why": "one code on two door stations, and whoever photographs the second screen "
               "holds the first driver's ticket",
        "file": "src/gate_agent/config.py",
        "from": "    _one_display_per_lane(intercoms)",
        "to": "    pass",
    },
    {
        "name": "a_ticket_field_is_checked_at_the_mint_only",
        "why": "a site named with a newline starts, publishes a healthy surface, and "
               "refuses its first ticket at three in the morning",
        "file": "src/gate_agent/config.py",
        "from": "        _refuse_unticketable_fields(str(agent[\"site_id\"]), lanes, intercoms)",
        "to": "        pass",
    },
]


def stage() -> Path:
    directory = Path(tempfile.mkdtemp(prefix="gate-agent-agent-control-"))
    # `scripts` is here because the audio build script is itself a published
    # claim -- the manifest's provenance rows come out of it -- and a break that
    # cannot reach it is a guarantee nobody is measuring.
    #
    # `README.md` is here from Z17, for the same reason and a sharper one: it is
    # the file a reader opens first, `test_unmeasured_claims.py` sweeps it, and
    # while it was not staged that sweep silently measured every file except that
    # one. A break planted in it could not go red because the file was not there
    # to break. `test_the_readme_is_in_the_swept_set` is what goes red if it
    # leaves this tuple again.
    for entry in ("src", "tests", "docs", "config", "scripts", "pyproject.toml", "README.md"):
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
