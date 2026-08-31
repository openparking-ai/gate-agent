#!/usr/bin/env python3
"""The control for every guarantee this module makes -- monitor and capture.

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

THE CAPTURE PROCESS. Same rule, same direction: every break below fails in the
reassuring one -- a store that keeps less than it says, a camera that is dead
and quiet about it, an identity reaching a disk that must not hold one.

  a_post_in_the_camera   the camera client grows a POST. The capture process is
                         a READER of a camera and of a lane's read contract, and
                         a new route to a barrier is the boundary every outside
                         reviewer of this project has named.
  capture_imports_the_lane
                         it imports `lane_controller` instead of speaking the
                         contract. The quickest way to learn that a lane vended
                         is to import the lane, which is why this one is here.
  an_opener_of_its_own   the camera builds its own opener instead of taking one
                         from `redirects`. That opener follows a `Location`, and
                         the request it would follow one on is the RETRY -- the
                         one carrying the camera credential.
  credential_in_a_snapshot_url
                         a camera URL carrying `user:password@` is accepted, and
                         then published on `GET /v1/capture`.
  event_detail_is_copied the lane event's `detail` is carried onto the record. It
                         is where a lane puts what it knows, and `entry_pending`
                         really does put `plate_region` there.
  entry_pending_triggers a second trigger that photographs the same vehicle again
                         and reads the detail that carries an attribute of it.
  no_atomic_rename       the image is written straight to its final name. A crash
                         then leaves a record with a name and a truncated image,
                         which looks complete.
  index_from_memory      the index is kept across a restart instead of rebuilt by
                         reading the directory. A memory, not a check.
  orphans_are_kept       half a record is left on the disk and not reported. It is
                         a photograph no retention rule can reach, because the
                         rule reads the sidecar.
  purge_ignores_age      the retention window is not applied. The store keeps
                         personal data for as long as the disk allows.
  purge_ignores_size     the size cap is not applied. The store eats its disk and
                         `store_over_budget` never fires.
  over_budget_is_silent  a write that will not fit is dropped rather than refused
                         and named. A recording missing exactly the busiest hour.
  retention_default_typed
                         the document's retention default stops being the
                         constant's. Two copies, and the hand-written one lies.
  a_size_in_the_document a disk figure appears in a payload example. This round
                         publishes none: nothing here has measured one, and a
                         number in a document looks measured.
  frozen_on_any_change   `camera_feed_frozen` fires on snapshots that differ. A
                         warning that cries wolf is ignored, and then the real
                         one is ignored too.
  a_dead_camera_is_ok    a camera that did not answer reads `ok`. Gokhan's
                         "camera disconnected is a malfunction", deleted.
  images_need_no_token   the image route is served without the credential the
                         other routes require. The whole store, readable by
                         anyone who can enumerate a record id.
  an_id_becomes_a_path   the image route joins the requested id onto the
                         directory instead of looking it up in the index.
  capture_subject_is_dropped
                         the monitor files a capture's codes under the target's
                         name instead of the camera's. "A camera is dead" without
                         "which camera", at a site with four of them.

THE ROUND-4 CUT. One per blocker, each one the REVERT of the cut -- so what goes
red below is what the L3's probe measured, and a control that stayed green would
mean the suite is not holding the fix.

  a_record_the_contract_refuses_is_filed
                         the record is written to the disk without being built
                         through the contract first. `GET /v1/capture/records`
                         then raises on it for up to `retention_days`, while
                         `/health` answers 200.
  a_naive_timestamp_is_followed
                         a lane event with no UTC offset is followed with its
                         reference dropped instead of the page being refused.
                         The cursor moves past events nothing photographed.
  the_records_route_raises
                         the read route dies on a record it cannot publish
                         instead of reporting and purging it. One record, and
                         every consumer of that store is served nothing.
  purge_before_the_fit_check
                         the store is purged for a capture that can never fit,
                         and the write is refused anyway. One oversized answer
                         from a camera and a site's recording is gone.
  the_size_purge_is_unbounded
                         the size half runs while there is anything left rather
                         than for the headroom it was asked for. Same defect,
                         one level down.
  temp_files_survive_a_rebuild
                         a write that died leaves its image on the disk: outside
                         the index, outside `bytes_used`, outside every report,
                         and outside the retention rule -- for ever.
  a_live_write_leaves_its_temp_files
                         the other half: a write that ends any way but cleanly
                         leaves them behind while the process is still running.
  newest_at_by_position  the ends of a list in insertion order are published as
                         the oldest and newest held. One clock step back and
                         they come out the wrong way round.
  the_size_purge_takes_the_first_written
                         "oldest first" read off the front of the index rather
                         than by the value of `captured_at`.
  a_stepped_clock_is_silent
                         a record stamped ahead of the clock is held with the
                         age rule unable to reach it, and nothing says so.
  a_lost_backlog_is_ok   a `reset` from the lane raises no code. Four hundred
                         arrivals photographed nothing and the only trace is a
                         log line on a box in a gate housing.
  missed_events_are_not_counted
                         the count of what was never followed stays at zero.
  a_socket_timeout_is_a_deadline
                         the body is read with no deadline over it. One camera
                         answering slowly for ever holds the only poller thread,
                         and nothing goes active.
  a_camera_with_no_state_is_absent
                         the health payload is built without seeding every
                         declared camera, so a camera that has never answered
                         disappears from it.
  camera_completeness_is_not_refused
                         the payload class stops refusing a missing
                         (code, camera) pair -- the control under the control.
  the_two_clocks_note_moves
                         the constant the document publishes is edited. The
                         document's copy and the code's come apart.
  a_negative_difference_is_hidden
                         `capture_minus_lane_event_ms` is published as a
                         magnitude, so the case the contract paragraph exists to
                         describe stops being reachable.
  a_backwards_cursor_is_adopted
                         a cursor behind the one this process holds, with
                         `reset:false`, is taken -- so the same events are
                         photographed again on every poll, for ever.
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
        "from": '    "monitor_codes": lambda: [code.value for code in MonitorCode],',
        "to": '    "monitor_codes": lambda: [code.value for code in MonitorCode] '
        '+ ["hatch_left_open"],',
    },
    # -- the capture process ------------------------------------------------
    {
        "name": "a_post_in_the_camera",
        "why": "the camera client grows a POST",
        "file": "src/gate_agent/camera.py",
        "from": '        request = urllib.request.Request(self.snapshot_url, method="GET")',
        "to": '        request = urllib.request.Request(self.snapshot_url, data=b"{}", '
        'method="POST")',
    },
    {
        "name": "capture_imports_the_lane",
        "why": "the capture process imports the lane instead of speaking its contract",
        "file": "src/gate_agent/capture.py",
        "from": "from .camera import CameraRefusedUs, CameraUnreachable, SnapshotCamera",
        "to": "from lane_controller.contract import MalfunctionCode as _Unused  # noqa: F401\n"
        "from .camera import CameraRefusedUs, CameraUnreachable, SnapshotCamera",
    },
    {
        "name": "an_opener_of_its_own",
        "why": "the camera builds an opener that follows a redirect",
        "file": "src/gate_agent/camera.py",
        "from": "        self._opener = build_opener(*handlers)",
        "to": "        self._opener = urllib.request.build_opener(*handlers)",
    },
    {
        "name": "credential_in_a_snapshot_url",
        "why": "a camera URL carrying a credential is accepted and published",
        "file": "src/gate_agent/config.py",
        "from": '        _refuse_userinfo(url, f"[cameras.{camera_id}].snapshot_url")',
        "to": "        pass",
    },
    {
        "name": "event_detail_is_copied",
        "why": "a lane event's detail is carried onto the record",
        "file": "src/gate_agent/store.py",
        "from": '    "capture_minus_lane_event_ms",\n    "bytes",\n)',
        "to": '    "capture_minus_lane_event_ms",\n    "bytes",\n    "plate",\n)',
    },
    {
        "name": "entry_pending_triggers",
        "why": "`entry_pending` becomes a trigger",
        "file": "src/gate_agent/capture.py",
        "from": '    "vended": CaptureReason.LANE_VEND,\n}',
        "to": '    "vended": CaptureReason.LANE_VEND,\n'
        '    "entry_pending": CaptureReason.LANE_VEND,\n}',
    },
    {
        "name": "no_atomic_rename",
        "why": "the image is written straight to its final name",
        "file": "src/gate_agent/store.py",
        "from": "            _write_atomic_body(image_temp, image)",
        "to": "            _write_atomic_body(record.image_path, image)\n"
        "            _write_atomic_body(image_temp, image)",
    },
    {
        "name": "index_from_memory",
        "why": "the index is a memory rather than a read of the directory",
        "file": "src/gate_agent/store.py",
        "from": "        self.probe()\n        self.rebuild()",
        "to": "        self.probe()",
    },
    {
        "name": "orphans_are_kept",
        "why": "half a record is kept and not reported",
        "file": "src/gate_agent/store.py",
        "from": "                incomplete.append(record_id)",
        "to": "                pass",
    },
    {
        "name": "purge_ignores_age",
        "why": "the retention window is not applied",
        "file": "src/gate_agent/store.py",
        "from": "            if _at(record.captured_at) < cutoff:",
        "to": "            if False:",
    },
    {
        "name": "purge_ignores_size",
        "why": "the size cap is not applied",
        "file": "src/gate_agent/store.py",
        "from": "        while self._records and self.bytes_used() + headroom > self.max_bytes:",
        "to": "        while False:",
    },
    {
        "name": "over_budget_is_silent",
        "why": "a write that will not fit is dropped rather than refused and named",
        "file": "src/gate_agent/capture.py",
        "from": "        except StoreOverBudget as exc:\n"
        '            log.error("%s", exc)\n'
        "            self._code(CaptureCode.STORE_OVER_BUDGET, STORE, HealthState.ACTIVE)",
        "to": "        except StoreOverBudget:\n"
        "            self._code(CaptureCode.STORE_OVER_BUDGET, STORE, HealthState.OK)",
    },
    {
        "name": "retention_default_typed",
        "why": "the document's retention default stops being the constant's",
        "file": "src/gate_agent/config.py",
        "from": "DEFAULT_RETENTION_DAYS = 30",
        "to": "DEFAULT_RETENTION_DAYS = 14",
    },
    {
        "name": "a_size_in_the_document",
        "why": "a disk figure appears in a payload example",
        "file": "tests/test_capture_contract.py",
        "from": "    doc = doc_payloads()\n    checked = []",
        "to": "    doc = doc_payloads()\n"
        "    doc['capture_health']['store']['bytes_used'] = 21474836480\n"
        "    checked = []",
    },
    {
        "name": "frozen_on_any_change",
        "why": "`camera_feed_frozen` fires on snapshots that differ",
        "file": "src/gate_agent/capture.py",
        "from": "            else (HealthState.ACTIVE if previous == digest else HealthState.OK),",
        "to": "            else HealthState.ACTIVE,",
    },
    {
        "name": "a_dead_camera_is_ok",
        "why": "a camera that did not answer reads `ok`",
        "file": "src/gate_agent/capture.py",
        "from": "                CaptureCode.CAMERA_UNREACHABLE,\n                camera_id,\n"
        "                HealthState.ACTIVE,",
        "to": "                CaptureCode.CAMERA_UNREACHABLE,\n                camera_id,\n"
        "                HealthState.OK,",
    },
    {
        "name": "images_need_no_token",
        "why": "the image route is served without the credential the others need",
        "file": "src/gate_agent/capture_service.py",
        "from": "        url = urlparse(self.path)\n"
        "        if not self._authorised():\n"
        "            return self._unauthorised()",
        "to": "        url = urlparse(self.path)\n"
        "        if not self._authorised() and not url.path.startswith(IMAGES_PREFIX):\n"
        "            return self._unauthorised()",
    },
    {
        "name": "an_id_becomes_a_path",
        "why": "the image route joins a requested id onto the directory",
        "file": "src/gate_agent/capture.py",
        "from": "        record = self.store.get(record_id)\n        if record is None:\n"
        "            return None",
        "to": "        record = self.store.get(record_id)\n        if record is None:\n"
        "            try:\n"
        "                return (self.store.directory / (record_id + '.jpg')).read_bytes()\n"
        "            except OSError:\n"
        "                return None",
    },
    {
        "name": "capture_subject_is_dropped",
        "why": "the monitor files a capture's codes under the target's name",
        "file": "src/gate_agent/monitor.py",
        "from": "            subject=str(subject) if isinstance(subject, str) and subject "
        "else target,",
        "to": "            subject=target,",
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
    # ---- THE ROUND-4 CUT ---------------------------------------------------
    {
        "name": "a_record_the_contract_refuses_is_filed",
        "why": "a record the contract will not publish is written to the disk",
        "file": "src/gate_agent/store.py",
        "from": "        # THROUGH THE CONTRACT, before the disk. `RecordRef` is the class the\n"
        "        # records route builds its page from, so this is the same judgement that\n"
        "        # route will make later -- not a second one that could come to differ.\n"
        "        _refuse_unpublishable(record)",
        "to": "        pass",
    },
    {
        "name": "a_naive_timestamp_is_followed",
        "why": "a lane event with no UTC offset is followed instead of refusing the page",
        "file": "src/gate_agent/capture.py",
        "from": "            if naive:",
        "to": "            if False:",
    },
    {
        "name": "the_records_route_raises",
        "why": "the read route dies on a record it cannot publish",
        "file": "src/gate_agent/capture.py",
        "from": "            except ValueError as exc:",
        "to": "            except LookupError as exc:",
    },
    {
        "name": "purge_before_the_fit_check",
        "why": "the store is purged for a capture that can never fit",
        "file": "src/gate_agent/store.py",
        "from": "        if len(image) > self.max_bytes:",
        "to": "        if False:",
    },
    {
        "name": "the_size_purge_is_unbounded",
        "why": "the size half runs while there is anything left rather than for its headroom",
        "file": "src/gate_agent/store.py",
        "from": "        if headroom > self.max_bytes:",
        "to": "        if False:",
    },
    {
        "name": "temp_files_survive_a_rebuild",
        "why": "a write that died leaves its image outside the index and the retention rule",
        "file": "src/gate_agent/store.py",
        "from": "                _remove(path)\n                crashed += 1\n"
        "                continue",
        "to": "                continue",
    },
    {
        "name": "a_live_write_leaves_its_temp_files",
        "why": "a write that ends any way but cleanly leaves its temporary files behind",
        "file": "src/gate_agent/store.py",
        "from": "            for path in (image_temp, sidecar_temp):\n"
        "                _remove(path)",
        "to": "            pass",
    },
    {
        "name": "newest_at_by_position",
        "why": "the end of a list in insertion order is published as the newest held",
        "file": "src/gate_agent/store.py",
        "from": "        newest = max("
        "(record.captured_at for record in records), key=_at, default=None)",
        "to": "        newest = records[-1].captured_at if records else None",
    },
    {
        "name": "the_size_purge_takes_the_first_written",
        "why": "`oldest first` is read off the front of the index rather than by value",
        "file": "src/gate_agent/store.py",
        "from": "                oldest = min("
        "self._records, key=lambda one: (_at(one[1].captured_at), one[0]))",
        "to": "                oldest = self._records[0]",
    },
    {
        "name": "a_stepped_clock_is_silent",
        "why": "a record stamped ahead of the clock is held and nothing says so",
        "file": "src/gate_agent/store.py",
        "from": "        self.clock_stepped_back = bool(\n            self._records\n"
        "            and max("
        "_at(record.captured_at) for _cursor, record in self._records) > moment\n"
        "        )",
        "to": "        self.clock_stepped_back = False",
    },
    {
        "name": "a_lost_backlog_is_ok",
        "why": "a `reset` from the lane raises no code",
        "file": "src/gate_agent/capture.py",
        "from": "            self._code("
        "CaptureCode.LANE_BACKLOG_LOST, subject, HealthState.ACTIVE)",
        "to": "            self._code(CaptureCode.LANE_BACKLOG_LOST, subject, HealthState.OK)",
    },
    {
        "name": "missed_events_are_not_counted",
        "why": "the count of lane events never followed stays at zero",
        "file": "src/gate_agent/capture.py",
        "from": "            self._lane_events_missed += missed",
        "to": "            pass",
    },
    {
        "name": "a_socket_timeout_is_a_deadline",
        "why": "the snapshot body is read with no deadline over it",
        "file": "src/gate_agent/camera.py",
        "from": "                    if self._clock() >= deadline:",
        "to": "                    if False:",
    },
    {
        "name": "a_camera_with_no_state_is_absent",
        "why": "a camera that has never answered is absent from the health payload",
        "file": "src/gate_agent/capture.py",
        "from": "                *((code, camera.camera_id) for code in CAMERA_CODES "
        "for camera in config.cameras),",
        "to": "                *(),",
    },
    {
        "name": "camera_completeness_is_not_refused",
        "why": "the payload class stops refusing a missing (code, camera) pair",
        "file": "src/gate_agent/contract.py",
        "from": "        if absent:",
        "to": "        if False:",
    },
    {
        "name": "the_two_clocks_note_moves",
        "why": "the constant the document publishes is edited, so the two copies come apart",
        "file": "src/gate_agent/contract.py",
        "from": '    "This is a SUBTRACTION ACROSS TWO CLOCKS: `captured_at` is read from this '
        "process's clock \"",
        "to": '    "This is the measured delay: `captured_at` is read from this '
        "process's clock \"",
    },
    {
        "name": "a_negative_difference_is_hidden",
        "why": "the subtraction is published as a magnitude, so a negative stops being reachable",
        "file": "src/gate_agent/store.py",
        "from": "            difference_ms = int("
        "(captured_at - lane_moment).total_seconds() * 1000)",
        "to": "            difference_ms = abs("
        "int((captured_at - lane_moment).total_seconds() * 1000))",
    },
    {
        "name": "a_backwards_cursor_is_adopted",
        "why": "a cursor behind the one this process holds, with reset:false, is taken",
        "file": "src/gate_agent/capture.py",
        "from": "        if cursor < self._cursor:",
        "to": "        if False:",
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
        # `not sip` because the SIP measurements take a real baresip and real
        # seconds, and none of the breaks below is about them -- running them
        # under thirty breaks would add half an hour to a check whose subject is
        # elsewhere. They carry their own controls; see
        # `scripts/agent_fail_control.py` for the same note.
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
            print(
                f"  {brk['name']:29} *** ANCHOR NOT FOUND in {brk['file']} ***", file=sys.stderr
            )
            failures += 1
            continue
        path.write_text(source.replace(brk["from"], brk["to"], 1), encoding="utf-8")
        if not judge(brk["name"], brk["why"], COLLECTED, run(directory), width=29):
            failures += 1
    finally:
        shutil.rmtree(directory, ignore_errors=True)

if failures:
    print(
        f"\n{failures} control(s) failed. Do not trust this module's guarantees.",
        file=sys.stderr,
    )
    sys.exit(1)
print("\nall controls OK — the suite fails on every property these processes exist to have.")
