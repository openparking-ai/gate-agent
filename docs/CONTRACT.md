# The gate agent contract

This is the public surface of the two processes in this package — the
**malfunction monitor** and the **capture process**. Everything else in the
repository is an implementation detail that may be rewritten; this is not.

They are **versioned together**, under one `contract_version`, so a consumer
holds one compatibility policy for this package rather than two.

Open Parking AI's own software integrates through exactly what is described
here. There is no private path, no second mode and no in-process shortcut
reserved for us — so if this contract is inadequate, we find out first.

---

## This monitor has NO OPENING AUTHORITY

It reads `GET`s and it sends messages. It never calls a vend, never resolves a
transit, never writes to a lane. There is no client in this package capable of a
method other than `GET`, and that is **swept out of the source and observed at
the targets**, not promised here: `tests/test_no_opening_authority.py` walks
every module for a request that is not a `GET`, and runs a whole poll against
lanes that record what arrived.

The one thing that leaves this process by any other method is a **webhook**,
which points at a paging system and never at a lane. It lives in its own module,
that module may not import the target client, and the sweep enforces both.

Three routes, all `GET`. Every other method is answered `405` with `Allow: GET`.

## Nothing this monitor opens follows a redirect

**Every URL this process opens is one a human wrote in its configuration file,
and a `Location` header does not change that.** A `3xx` is refused by the
opener, in both directions — the read-only client and the webhook sink.

It is stated here because following one is what a stdlib client does by default,
and because of what that costs. A redirect rebuilds the request from the old
one's headers, so **the bearer token travels with it**, to whatever host the
payload names: a target could take this monitor's operator credential, serve its
own payload as somebody else's health, and — pointing the webhook sink at a host
of its choosing — make a POST that was never delivered report SUCCESS, so
`sink_delivery_failed` never fired. This monitor is pointed at a third party's
lane **by design**, so "the target is trusted" is not available.

A 3xx from a target is therefore an ANSWER this monitor does not accept: it is
`<kind>_refused_us` with `302` (or whichever) as its status. A 3xx from a
webhook is `sink_delivery_failed`.

## This monitor is a CONSUMER of the lane contract

It reads a lane over
[`lane-controller/docs/CONTRACT.md`](https://github.com/openparking-ai/lane-controller/blob/main/docs/CONTRACT.md)
and imports nothing from that package — the same seat a third party takes. The
test suite reads a real lane and a foreign one written from the document, through
the same code, parametrised so neither can be special-cased.

## Compatibility

`contract_version` is `1`, and every payload carries it.

- **Additive changes do not bump it.** New fields may appear. Ignore fields you
  do not recognise rather than rejecting the payload.
- **Anything a consumer could notice bumps it** — a field removed, renamed, or
  changed in meaning or type.
- **An unrecognised version is refused, not partially read.**

This is the same policy the [lane
contract](https://github.com/openparking-ai/lane-controller/blob/main/docs/CONTRACT.md)
and the [Vehicle ID
contract](https://github.com/openparking-ai/vehicle-id/blob/main/docs/CONTRACT.md)
state, in the same words, so one consumer can hold one policy for all three.

**The monitor applies that rule to what it reads, too.** A target answering with
a version it does not know is refused at startup, by name, with both versions in
the message. A target that changes version while the monitor is running raises
`target_contract_unsupported` and **its codes stop being passed through** —
half-understanding a payload about a lane is worse than admitting it cannot be
read. A target that is merely DOWN at startup is not refused: that is a
malfunction, not a misconfiguration, and refusing to start would mean being
absent at exactly the moment a monitor is wanted.

---

## What it watches, and what it does with what it sees

### Targets

Each is per-site declared, each is optional, and **at least one is required**.
An empty target set is refused at startup, named: a monitor watching nothing
reports "all fine", which is the lie this module exists to prevent.

| | |
|---|---|
| `lane` | A URL implementing the lane contract. The monitor reads `GET /v1/lane` once for identity and version, then polls `GET /v1/lane/health`. Ours or a third party's — there is no branch on which. |
| `identity_service` | A Vehicle ID `GET /v1/health` URL. Unauthenticated by that contract's own decision: it carries no plate and no image, and a monitor holding the read credential in order to ask whether a process is alive is that credential in one more place. |
| `platform` | The operator surface. Read with an **operator token from a file**, and its garage declared. |
| `capture` | A capture process — this package's own second process, or anything implementing the capture half of this contract. The monitor reads `GET /v1/capture/health`, whose entries are in the **lane's shape on purpose**, so the same code reads both. This is how *"camera disconnected is a malfunction"* gets from a camera nobody can reach to a human who can go and look at it. |

**Standalone is a MODE.** With no lane declared, the monitor watches whatever IS
declared — a Vehicle ID service alone, a platform alone — and it is not a
degraded configuration.

**The poll interval is a per-site setting with a published default, one value
per target.** It is also an **assumption**: nothing in this package measures how
often a lane's health changes, or how quickly a human needs to hear.

### Transitions, not states

A monitor that sent the current state would send the same message every poll for
as long as a fault lasted, and a human told a thousand times has been told
nothing. So what gets sent is what CHANGED:

| | |
|---|---|
| `raised` | `ok` or `unknown` became `active`. |
| `recovered` | `active` became `ok`. |
| `no_longer_measured` | `ok` or `active` became `unknown`. Sent **once**. Not a recovery and not a fault — the loss of a measurement, which is its own event and the one a monitor most easily hides. |
| `still_active` | The state held and this site set a re-notify interval. |

`unknown → ok` is deliberately silent: nothing was ever claimed about that code
and now it is fine, and a message would train the reader to skim.

**Nothing re-sends while a state holds** unless `[notify].renotify_seconds` is
set. There is **no default** — a default interval would be a decision about how
often to wake somebody, made for every site that never mentioned it. Only an
`active` state repeats; a held `ok` is not news and a repeated recovery is not a
fact about anything.

### An HTTP answer is not silence

A target that does not answer and a target that answers NO are two facts, and
they name different machines:

| | |
|---|---|
| `<kind>_unreachable` | Nothing came back. A network failure, a timeout, or a **5xx** — the target's process answered that it could not do the thing, and the repair is at that target either way. This is the "no connection" in the spec, and it is the monitor's own measurement because nothing else can make it: a thing that is down cannot report that it is down. |
| `<kind>_refused_us` | It ANSWERED, and the answer was no — a **3xx** or a **4xx**. It is up, it received the request, and it declined it. |

**Both carry the HTTP status**, on the health entry and in every sink's message,
because the status is the difference between three repairs on three different
machines: **401/403** is this monitor's own credential and the target is
healthy; **404** is a route this build expects and that target does not have,
which is what an OLDER target answers; **3xx** is a target trying to steer this
monitor somewhere else. Folded into one code with no status, an expired token
paged a human to a platform that was running perfectly, and they could not tell
from the message either.

Both are measured by this monitor, both follow the transition rule, and both are
scoped per target. A poll that ends in silence leaves `<kind>_refused_us`
**`unknown`** — nothing came back to refuse anything, and `ok` there would be a
claim about a measurement nobody made.

### `unknown` is never `ok`

The states this surface publishes for a target are **that target's own, passed
through**. Not re-derived, not re-labelled, not translated. A monitor that
restated a lane's health in its own words would be a second copy of that lane's
claim about itself, and the copy is the one that comes to disagree.

So `unknown` arrives as `unknown`. The monitor **never pages on it and never
hides it**: every unmeasured code with its `source` is on
`GET /v1/monitor/health` continuously, and in one message at startup.

**Those two are one enumeration.** The startup message is built from the same
answer the health route serves, so the two cannot disagree — and they did: the
message walked a different structure, and the codes it missed were exactly this
monitor's own blind spots. With no platform declared, nobody is measuring
whether a lane has gone quiet, and the one message whose purpose is *what does
this monitor NOT know?* did not say so.

### `never_alarm` is read from the payload

Whether a code may wake a human travels on the wire with that code. This package
holds **no list of its own**. If it did, the two would drift — and the drift
shows up as a technician dispatched because a car arrived on low-texture ground,
which is the failure the lane's caveat exists to prevent, reintroduced by its
reader.

### A payload this build cannot read is refused WHOLE

Two fields on a lane's health entry are what this monitor acts on, and neither
is guessed at:

- **`never_alarm` must be a JSON boolean, on every entry.** Absent is not
  `false`. Absent could be a lane with nothing to say or a lane whose serialiser
  dropped the field, and the two point in opposite directions — one dispatches a
  technician because a car arrived, the other silences a real fault. And a
  string is not a boolean: every non-empty string is truthy, so `"false"` would
  silence that code for ever with nothing anywhere reporting it.
- **`state` must be one of `ok`, `active`, `unknown`.** A value outside the set
  can never produce a transition, and it becomes the state a later one is
  compared against — so an `active` fault arriving after one is held, published
  as active, and told to nobody.

Either one makes the whole payload one this build cannot read, and the answer is
the answer this monitor already gives a version it cannot read:
`target_contract_unsupported`, **`active`**, named, **paged once**, and that
target's codes stop being passed through. Refused whole and not entry by entry,
because passing through the rest would publish a partial view of a lane's health
as though it were the whole.

Validating a value against the set the contract defines is not re-deriving it.

### The message

It carries the site, the code, the transition, the `source` the target gave,
that target's own `caveat` if it published one, the HTTP status when the code is
about an answer, and the time.

**`lane_id` is on a lane's notifications and on nobody else's.** `null` for the
platform's codes, the identity service's, and this monitor's own — the sink that
could not deliver and the target that could not be reached are not facts about a
lane. `code=platform_unreachable … lane_id=lane-1` reads as "lane-1 cannot reach
the platform", which is a different machine, a different fault and a different
repair from the true one; and on the two codes spelt the same on both surfaces
it puts a false discriminator beside the only true one.

**It carries no plate, no image reference and no event detail.** The monitor
reads `/health` — never `/events` and never `/state` — so it does not hold one,
and the notification has nowhere to put one. That is swept over every sink's
rendered output, with a planted plate as the control.

---

## `GET /v1/monitor` — who this monitor is, and what it watches

<!--payload:monitor-->
```json
{
  "monitor_id": "monitor-1",
  "site_id": "site-1",
  "contract_version": 1,
  "event_window_depth": 256,
  "targets": [
    {
      "name": "lane",
      "kind": "lane",
      "url": "http://127.0.0.1:8090",
      "poll_seconds": 30.0,
      "authenticated": false,
      "timeout_seconds": 10.0
    }
  ],
  "sinks": [
    {
      "name": "log",
      "kind": "log"
    }
  ]
}
```

`url` is here because knowing WHAT is being watched is the whole value of this
route, and a URL is not a credential. `authenticated` says whether one is
configured; **the credential itself is on no route, and is not in this process's
argument vector either** — every token is read from a file.

**What is published is scheme, host, port and path, and nothing else** — rebuilt
from those four parts rather than echoed as configured. A URL carries a
credential very well (`https://ops:S3CRET@example.com`), and that one was
accepted and republished here verbatim, beside `authenticated: false`, so the
one field a consumer would use to notice read the wrong way. **Userinfo in a
target or sink URL is now refused at startup, by name** — *credentials come from
files* — and this route rebuilds the address anyway, because one check is a
check and two is a boundary.

`timeout_seconds` is how long this monitor waits for that target's answer. A
per-site **setting** with a published default of **10 seconds**, and an
**assumption** — nothing here measures how long a loaded Jetson takes to answer
its own health route. What it is drawn against is the other side of the seam: a
lane's health route may itself read a third machine (the identification service)
and bounds that read at its own `[lane] identity_health_timeout_s`, published
default 1 second. **This monitor's timeout must comfortably exceed that one.**
If they cross, a lane that is up, serving, and correctly answering `unknown`
about a hung identification service is published here as a DEAD LANE, and every
real signal it publishes is retired at the same moment — a slow third machine,
reported as a fault on the wrong one. The two numbers live in two repositories,
so the relationship is stated in both contracts and measured in the test suite
against a real lane that answers slowly.

`event_window_depth` is how many notifications `GET /v1/monitor/events` can
still serve behind the current cursor. Fall further behind than this and you are
told **`reset`**, not served a short page — so a consumer's own catch-up policy
depends on the number, which is why it is published here rather than described.
It is `[monitor] event_window_depth`, a per-site setting with a published
default, and it is an **assumption**: nothing here measures how far behind a
consumer of that route falls. The lane contract publishes its own on
`GET /v1/lane`, in the same field name, for the same reason.

A sink is published as a name and a kind and **nothing else**. No host, no
recipient, no URL. A consumer is entitled to know that somebody is being told;
putting where they are on a read route would publish an address list.

## `GET /v1/monitor/health` — its own codes, and every target's

<!--payload:health-->
```json
{
  "contract_version": 1,
  "codes": [
    {
      "code": "lane_unreachable",
      "subject": "lane",
      "state": "ok",
      "source": "measured",
      "status": null
    }
  ],
  "targets": [
    {
      "name": "lane",
      "kind": "lane",
      "polled_at": "2026-08-30T14:00:00+00:00",
      "contract_version": 1,
      "codes": [
        {
          "code": "reference_not_recognised",
          "state": "unknown",
          "source": "not_measured",
          "never_alarm": true,
          "caveat": "NOT an alarm. …"
        }
      ]
    }
  ]
}
```

### `codes` — the monitor's own

One entry per `(code, subject)`. **Every member of `contract.MonitorCode` ships
on every response**, and a payload missing one is refused when it is built,
because a code that is absent reads to a consumer exactly like a code that is
fine. A code with no subject yet ships once, `unknown`, under the monitor's own
id.

`subject` is what the code is about: which target could not be reached, which
sink could not deliver, which device has gone quiet. It is part of the identity
of the entry — "a sink failed" and "which sink failed" are different facts, and
only one of them can be acted on.

`status` is the HTTP status the target answered with, for the codes that exist
because it answered — `<kind>_refused_us`. `null` on every other code. It is on
the entry and not only in the message because a human arriving here after the
message has scrolled away needs the same fact.

**The set is published here, in full**, so that a paging system which is not ours
can be written from this document alone. It used to say it did not list them, on
the reasoning that a hand-written copy of a set the code defines is the copy that
goes wrong — the same sentence the lane contract carried, and that reasoning is
right. The conclusion was wrong: withholding the set does not remove the second
copy, it moves it into every implementer's guess. So the copy is published and
held to the enum by a test that compares every member in BOTH directions —
dropping one here goes red, and so does adding one to `contract.MonitorCode`
without adding it here.

<!--payload:sets-->
```json
{
  "monitor_codes": [
    "lane_unreachable",
    "identity_service_unreachable",
    "platform_unreachable",
    "capture_unreachable",
    "agent_unreachable",
    "lane_refused_us",
    "identity_service_refused_us",
    "platform_refused_us",
    "capture_refused_us",
    "agent_refused_us",
    "target_contract_unsupported",
    "sink_delivery_failed",
    "lane_gone_quiet"
  ],
  "capture_codes": [
    "camera_unreachable",
    "camera_refused_us",
    "camera_feed_frozen",
    "store_unwritable",
    "store_over_budget",
    "store_record_incomplete",
    "clock_stepped_back",
    "lane_unreachable",
    "lane_refused_us",
    "lane_contract_unsupported",
    "lane_backlog_lost"
  ],
  "camera_unreachable_causes": [
    "timeout",
    "network",
    "server_error",
    "not_a_picture"
  ],
  "capture_reasons": [
    "interval",
    "lane_arrival",
    "lane_vend"
  ],
  "retention_days_bounds": [
    1,
    3650
  ],
  "max_snapshot_bytes_default": 33554432,
  "agent_codes": [
    "sip_registration_lost",
    "ua_unreachable",
    "ua_unsupported_version",
    "call_from_undeclared_intercom",
    "human_unreachable",
    "audio_missing",
    "audio_playback_failed",
    "lane_unavailable",
    "display_unavailable",
    "lane_act_refused",
    "relay_unreachable",
    "relay_refused_us"
  ],
  "agent_cases": [
    "malfunction_active",
    "identification_unavailable",
    "plate_not_read",
    "plate_unclear",
    "vehicle_not_recognised",
    "rules_unavailable",
    "entry_refused",
    "vehicle_not_detected",
    "entry_not_confirmed",
    "stale_decision",
    "unrecognised_reason",
    "lane_unavailable",
    "standalone",
    "nothing_to_do"
  ],
  "authorisations": [
    "open_now",
    "open_and_flag",
    "do_not_open",
    "hold",
    "transfer",
    "call_back"
  ],
  "authorisation_digits": {
    "1": "open_now",
    "2": "open_and_flag",
    "3": "do_not_open",
    "4": "hold",
    "5": "transfer",
    "6": "call_back"
  },
  "agent_event_kinds": [
    "call_answered",
    "case_spoken",
    "human_called",
    "authorisation_received",
    "human_unreachable",
    "nothing_usable",
    "call_from_undeclared_intercom",
    "call_refused_busy",
    "case_not_spoken",
    "call_ended",
    "leftover_calls_released",
    "ticket_issued",
    "ticket_confirmed",
    "ticket_voided",
    "vend_commanded",
    "vend_refused",
    "relay_pulsed"
  ],
  "shipped_languages": [
    "en",
    "es-ES"
  ]
}
```

`capture_codes`, `camera_unreachable_causes` and `capture_reasons` belong to the
capture process and are described under its own routes below;
`retention_days_bounds` is the range a site's `[capture] retention_days` must
fall inside, and `max_snapshot_bytes_default` is the published default for
`[capture] max_snapshot_bytes`. Every set here is held to the code by the same
test, in both directions.

**`max_snapshot_bytes_default` is a number in a document, and it is here rather
than in an example for one reason: it is a CEILING ON A READ and not a claim
about how big a picture is.** Nothing in this package has measured a capture
from any of the cameras it is written for. This is the most it will read from
one before it stops reading, it is a per-site setting, and startup refuses it
unless it is BELOW `[capture] max_bytes` — see the retention rule below for why
that bound is the one that matters.

Two of the monitor's codes deserve saying out loud, because they are spelt the
same as codes the LANE publishes and they are different facts:

- `platform_unreachable` here is whether **this monitor** can reach the platform.
  On a lane's health surface it is what **that lane** thinks of its own uplink.
- `lane_gone_quiet` here is derived from the platform's
  `lane_devices.last_seen_at`. On a lane's surface it is `not_measured`, and
  necessarily so: a lane that has gone quiet is quiet, and the fault is only
  visible from the other end.

They are held in separate namespaces internally so that one can never be filed
under the other, which would be a fault attributed to the wrong machine.

### `targets` — passed through

`codes` under a target is **that target's payload, unchanged**. Whatever entries
it published, exactly as it published them, with the `state` and the `source` it
gave. **`subject` is passed through when the target published one**, and the
target's own name when it did not: a lane's codes are about the lane and carry
none, while a capture process's are about a NAMED CAMERA — and a message saying a
camera is dead without saying which is a message somebody has to go and work out
at a site with four of them. `polled_at` is `null` until it has answered once, and `codes` is empty
then — which is a different fact from a target that answered with nothing to
say, and `<kind>_unreachable` is what separates them.

`contract_version` is the version the TARGET declared. It is `null` for a target
whose contract publishes no version — **the platform's operator surface does
not**, so `target_contract_unsupported` for that target stays `unknown`: nothing
was checked, and `ok` would be a claim about a measurement nobody made.

### `lane_gone_quiet`, and the two clocks it spans

A device the platform has not heard from for longer than
`[targets.platform].lane_quiet_seconds` reads `active`. That threshold is a
**per-site setting and an assumption**, and it is the more consequential of the
two settings in this module: nothing here measures how often a healthy lane talks
to its platform. A busy entry touches it on every vehicle; a quiet overnight exit
may not for hours with nothing wrong.

**The comparison spans two clocks** — the timestamp is the platform's and `now`
is this monitor's — so a monitor whose clock is wrong reports lanes as quiet that
are not. Stated rather than corrected: correcting it would mean measuring the
offset, which is a second measurement nobody has made.

A **revoked** device is skipped: a credential deliberately ended and then not
seen is not a fault, and paging on it teaches whoever reads these messages to
ignore them. A device that has **never** been seen is measured from when it was
created — a credential issued a week ago and never used is a lane that never came
up, which is exactly what is worth knowing at an installation.

## `GET /v1/monitor/events?since=N` — what it told somebody

<!--payload:events-->
```json
{
  "contract_version": 1,
  "cursor": 1,
  "reset": false,
  "dropped": 0,
  "events": [
    {
      "cursor": 1,
      "site_id": "site-1",
      "lane_id": "lane-1",
      "target": "lane",
      "code": "outbox_depth_growing",
      "subject": null,
      "transition": "raised",
      "source": "measured",
      "caveat": null,
      "at": "2026-08-30T14:00:00+00:00",
      "status": null
    }
  ]
}
```

Deliberately the same shape and the same semantics as the lane contract's
`GET /v1/lane/events` and the Vehicle ID service's `GET /v1/reads?since=N`, field
for field, so **one consumer can hold one cursor policy for three surfaces**.

- The cursor is **monotonic within one run** and is **not durable across a
  restart**. It is a catch-up window for a consumer that blinked, not a record of
  anything; the durable record of what this monitor said is whatever its sinks
  delivered it to.
- `since` ahead of this monitor's own cursor sets **`reset`**. An empty list
  without that flag is indistinguishable from "nothing happened", which is how a
  consumer silently misses everything after a restart.
- `since` behind the oldest notification still held **also** sets `reset`. The
  window is bounded, and a consumer further behind than that would otherwise
  receive a page with the evicted notifications simply absent from it, which
  looks exactly like a complete one.
- `dropped` counts what the window has evicted. Published because a gap nobody
  knows about is worse than one that is counted — and on this surface a gap is a
  fault nobody was told about.

**This route serves TRANSITIONS.** The one startup message saying what is not
being measured is not one, and is not here: the same information is on
`GET /v1/monitor/health` continuously, which is strictly better than one entry in
a bounded window.

---

## How a human is told

A closed set this version: `log`, `email`, `webhook`. **SMS is a later round's
provider and is deliberately absent** — the set being closed and published is
what makes adding one an additive change rather than a configuration key that
quietly already works.

| | |
|---|---|
| `log` | Structured JSON to stdout, one line per notification. **Always on**, and the one sink that needs no provider, no credential and no network. |
| `email` | SMTP, per-site host, port, from and to, **TLS by default**. |
| `webhook` | A `POST` of the notification object to a per-site URL with a bearer token from a file. This is how a third party's paging system takes the seat. |

**Every sink is per-site declared and none has a default recipient.** A default
recipient pages somebody who never asked, or — far more likely — a placeholder
nobody reads, while the site believes it is covered.

**A site with only `log` declared is valid, and it means NOBODY IS PAGED.** The
notifications go to this process's stdout and to whatever collects it. That is a
real configuration where the logs are already watched, and the process says so on
the line it prints when it starts.

**There is no SMTP credential in this version**, and that is stated rather than
left to be discovered: a credential is a file and a decision about which file,
and nobody has made it. A site needing an authenticated relay finds out at
startup rather than after a fortnight of rejected alerts.

**A sink that fails to deliver is itself a monitor code.** `sink_delivery_failed`
is reported per sink on the health route and told to **every other sink that
works** — never wrong silently applies to the messenger too, and a paging system
nobody can reach is the failure that hides every other failure. A failure while
reporting a failure is recorded and told to nobody, so one dead endpoint cannot
become a loop.

## Running it

```sh
gate-agent monitor --config monitor.toml
```

Binds `127.0.0.1:8092`. **Local by design** — this is meant to run beside the
lane it watches.

**Off loopback it refuses to start without a credential.** `--host` anything but
loopback requires `--auth-token-file`, and with a token every route requires
`Authorization: Bearer <token>` and answers `401` without it. The exposure that
rule exists for is its own kind: this surface does not publish where a vehicle
was, it publishes which of a site's lanes are broken, which cameras are dark and
when nobody was told — which is a map for whoever wants to arrive while the
equipment that would have noticed is down.

**Every token is read from a FILE, never from a value.** A value on the command
line is readable by every user on the box for as long as the process runs; a
value in a configuration file is a credential in that file, in every backup of it
and in everything anyone ever pastes it into. A configuration key that would hold
one is **refused by name**, with the name of the file key that replaces it.

There is no flag that turns any of that off.

## What is NOT here, stated rather than left to be discovered

- **No act surface.** No vend, no resolve, no route that changes anything, here
  or at a target.
- **No state store.** Everything this monitor knows is lost on a restart, and the
  startup message says what it does not yet know rather than reporting the last
  thing it happened to remember. **A restart therefore re-sends `raised` for
  every code that is still active**, because every code starts `unknown` and a
  code seen for the first time as `active` is a transition into a fault. That is
  deliberate — a monitor that started while a lane was already broken must say
  so — and it is stated here rather than left to be worked out from the
  transition table plus the absence of a store.
- **No SMS**, no SIP, no audio, no DTMF. The agent that answers a driver at the
  barrier is a later round in this same repository.
- **No SMTP credential.** See above.
- **`identity_service_degraded` is not read from the identity target.** That
  service publishes `status: degraded` when a read was lost or its queue held a
  line it could not read, and the LANE reads it — it reaches this surface as a
  passed-through lane code. A monitor with an identity service declared and **no
  lane** therefore does not see degradation. That is a gap, and it is named here
  rather than filled by a second observer of one field.
- **No aggregation.** "N in a row is a fault" needs an N, and nobody has measured
  one. The codes that need it stay `not_measured` at the lane and arrive here
  saying so.

---

# The capture process

`gate-agent capture --config capture.toml`

Gokhan's spec, his words: *"camera captures an image every minute and every time
the gate opens"*, *"camera disconnected is a malfunction"*, *"we need to save the
picture of the cars."* SETTLED 3g, on what a broken barrier changes: **the
camera's job changes from DECIDING to RECORDING** — capture every entry,
timestamp and image so the entries can be reconstructed. Before this existed,
nothing in this estate kept an image anywhere: a lane grabs frames at an arrival,
hands them to the identifier, and drops them.

## It has NO OPENING AUTHORITY either

It reads a camera and it reads a lane's READ contract, both `GET`, and it writes
to its own directory. There is **no client in this package capable of a method
other than `GET`**, swept out of the source and observed at the lane and at the
camera. It has no route that changes anything: it cannot capture on demand, it
cannot delete a record, and it cannot move a retention window.

**The lane is not touched by this process at all.** It learns that a car arrived
and that the lane vended from `GET /v1/lane/events?since=` — the read contract,
which is the seat a third party takes. A store the lane had to `POST` into would
make the lane's vend path depend on a process that need not exist, and that vend
path is the boundary every outside reviewer of this project has named.

**And that seat costs something, which is MEASURED rather than described.** This
process learns about an arrival by POLLING, so the picture is taken when the
event was SEEN, not when the frames were grabbed. Every lane-triggered record
carries `capture_minus_lane_event_ms`, which is that subtraction, on that record,
at that site. A sentence here could only describe one installation — and what
that number spans, which is two machines' clocks, is stated under the records
route below.

**A LANE THIS PROCESS DID NOT WRITE IS THE DESIGNED CASE, NOT THE EXOTIC ONE.**
That is the whole point of the seat. So a page this build cannot read is refused
WHOLE, and said out loud: see "When a lane breaks its own contract" below.

## Nothing it stores identifies a vehicle

Not a plate, not a plate region, not a colour, not a make — and **not a lane
event's `detail`**, which is where a lane puts what it knows and where
`entry_pending` really does carry `plate_region`. What a record carries about its
trigger is the event's **cursor** and the time **the lane** recorded, which is a
reference and not a copy.

The join to who the car was therefore lives in exactly two places and neither is
here: the lane's platform holds the durable record under that cursor, and this
store holds the image. A plate in this directory would be a second copy of an
identity, on a box in a gate housing, outside every retention mechanism that
already exists for one.

This is swept over every route and over every byte in the store, with a plate
planted in a lane event's `detail` as the positive control.

## The two triggers

| | |
|---|---|
| `interval` | The clock. `[capture] interval_seconds` elapsed. Gokhan's *"every minute"*, as a per-site setting with that as its published default — and an **assumption**: nothing here measures what a garage needs to reconstruct. |
| `lane_arrival` | The lane recorded `frames_captured`: a vehicle presented and it grabbed frames for it. |
| `lane_vend` | The lane recorded `vended`. This is *"every time the gate opens"* in the only phrasing that is measurable — **nothing anywhere knows whether the boom moved.** A lane's vend output has one method, no feedback and no `close()`, by design, and boom-as-marker is unbuilt. |

`entry_pending` is **not** a trigger. It arrives after `vended`, so it would
photograph the same vehicle twice, and it carries `plate_region` in its `detail`
— which this process does not read, from any event.

**Standalone is a MODE.** With no lane declared the process takes interval
captures only and **says so on the line it prints when it starts**. That is not a
degraded configuration: a garage with a camera and no gate is a customer of this
process.

**A lane that is DOWN is not a reason to stop.** `lane_unreachable` goes active,
the interval captures continue, and that is precisely the job SETTLED 3g gives
this process — the camera records because the barrier cannot be trusted.

**On start it takes the lane's place at the current cursor** and photographs
nothing for what is already in that lane's window. Those cars have gone; a
picture taken now and filed against one of their events would be an image of an
empty lane carrying a reference to a vehicle, which reads as a record *of* that
vehicle.

### When a lane breaks its own contract, and when it merely loses a backlog

Two different facts, two codes, and neither of them is a log line on a box.

**`lane_backlog_lost`** — the lane answered `reset`: it restarted, or it evicted
further than this process had fallen behind. This process takes its place at the
new cursor and photographs nothing for the gap, for the same reason as the first
read. **What was in that gap can never be photographed** — those cars have gone
— so it is `active` until the next page that is not a reset, and
`lane_events_missed` on the health route counts how many events that was, since
this process started. SETTLED 3g's capture mode exists so the entries can be
reconstructed, and the busiest hour is exactly the hour that outruns a window: a
gap nobody knows about is worse than one that is counted, which is the same
sentence `dropped` exists for.

**`lane_contract_unsupported`** — the lane served a page this build cannot read.
**The page is refused WHOLE**: the cursor is NOT adopted, nothing is photographed
under a lane reason, and the next poll asks for the same events again — so it
recovers by itself the moment the lane serves a page that can be read. It is the
same answer, in the same shape, as the monitor's `target_contract_unsupported`.

A page is refused when:

| | |
|---|---|
| `occurred_at` **carries no UTC offset** | It is not a moment this process can subtract from its own — two machines, two timezones — and a capture filed under a lane reason with its reference dropped is a record this package's own contract refuses to publish. |
| `occurred_at` is absent, is not a string, or is not a timestamp | Same fact, one step earlier. |
| a triggering event carries **no cursor** | The cursor IS the join to who the car was. A capture with no reference filed under a lane reason is the same refused record. |
| the page's `cursor` **went backwards** with `reset: false` | The lane contract says the cursor is monotonic within a run and that a restart sets `reset`. Adopting a backwards cursor re-serves the same events on the next poll and photographs them **again**, for ever — and every duplicate consumes `max_bytes`, so the size rule then evicts real captures to make room for them. |
| the page carries no `cursor`, or no `events` list | There is nothing here to follow. |

An event of a kind this build does not trigger on is **not** a contract break: a
lane gaining an event kind is the ordinary case, and this contract says a
consumer ignores what it does not recognise.

## The camera

**One real implementation this version: an HTTP JPEG snapshot.** A `GET` of a URL
that answers `image/jpeg`, with the credential presented through **standard HTTP
authentication** — the challenge-response the camera itself asks for when it
answers `401`. Both Basic and Digest are answered; which one is used is the
camera's choice, because a client that decided for itself would need a per-camera
setting nobody can answer correctly.

**There is no RTSP**, and that is a decision rather than a gap: reading a stream
needs a decoder, and this package has no dependencies at all — it runs on a box
in a gate housing where every dependency is one more thing to cross-compile,
patch and have go wrong with no keyboard attached.

**`[cameras.<id>] timeout_seconds` IS A DEADLINE ON THE WHOLE READ, not a socket
option.** A camera that answers, declares a length and then sends one byte every
quarter second never trips a per-operation timeout, because no single read waits
long enough. This process runs ONE poller thread, so such a camera would hold
every other camera's interval capture and the lane's event poll behind it, for as
long as it chose, with nothing going `active`. The body is therefore read in
chunks against a wall of `timeout_seconds` from the moment the request went out,
and past it the read is abandoned: `camera_unreachable` with a `cause` of
`timeout`. **One camera cannot hold this process for longer than that camera's
own timeout.**

**`[capture] max_snapshot_bytes` is the most this process reads from one camera**
— a per-site setting whose published default is in the closed sets above, and a
CEILING ON A READ rather than a claim about how big a picture is. **Startup
refuses it unless it is below `[capture] max_bytes`**, and that bound is the
point of the setting: the store evicts to make room for what arrives, so a
ceiling at or above the whole cap would let one camera's answer decide how much
of a site's store survives — and how long that answer is, is the camera's to
choose.

**A credential never goes in the URL.** `[cameras.<id>].snapshot_url` carrying
userinfo is refused at startup, by name, by the same code that refuses it in a
target or a sink URL — and the address is REBUILT from scheme, host, port and
path before it is published, so a query string cannot carry one out either.

### Cameras this build does not support, and why

**A camera whose documented snapshot API takes the credential as a QUERY
PARAMETER is not supported by this build.** It is not made to work by writing a
password into a configuration file, every backup of that file, and every access
log, proxy log and browser history between this process and the camera.

Of the two default-tier cameras the lane's reference-hardware note names:

| | |
|---|---|
| **AXIS P1465-LE** | **Supported.** Its snapshot route is `GET /axis-cgi/jpg/image.cgi`, and VAPIX authenticates with HTTP Basic or Digest — headers, never a query string. |
| **Reolink RLC-810A** | **NOT SUPPORTED by this build.** Reolink's own support documentation gives the snapshot route as `/cgi-bin/api.cgi?cmd=Snap&…&user=…&password=…` — the credential is a query parameter, and its token alternative is a query parameter too. There is no documented header-authenticated snapshot route to use instead. A site with these cameras needs either a camera that authenticates in a header, or a later round that decides deliberately to accept a credential in a URL. This build does not decide that quietly. |

**Neither claim is a measurement.** Nobody here has one of these devices. Each is
a claim about a document, and the receipt for this round names the document, the
URL and the date it was read.

### What a failed read is called

The same split the monitor makes for a target, because it is the same fact:

| | |
|---|---|
| `camera_unreachable` | Nothing came back — a network failure, a timeout, a **5xx**, or a body that does not begin as a JPEG. This is *"camera disconnected is a malfunction"*. It carries a **`cause`** out of `camera_unreachable_causes`: one fact to this process, four different repairs to the person sent to fix it. `null` until it has been measured. |
| `camera_refused_us` | It ANSWERED, and the answer was no — a **3xx** or a **4xx**, with its status on the entry. **401** is the credential in the file beside this process and the camera is fine; **404** is a snapshot route this camera does not have; **3xx** is a camera steering this process somewhere else, which is refused rather than followed. |

A body that is not a JPEG is refused rather than stored: a login page served as
`image/jpeg` fills a store with documents that read as a working installation
right up until somebody opens one.

The four causes, in full:

| | |
|---|---|
| `timeout` | The deadline passed. The body was still arriving, or was not arriving at all, and this process stopped reading. |
| `network` | The socket failed, or nothing answered on it. |
| `server_error` | A **5xx**: the camera's own process answered that it could not take the picture. The repair is at the camera either way, which is why it is here and not under `camera_refused_us`. |
| `not_a_picture` | Something came back and it was not a JPEG, or it was longer than `[capture] max_snapshot_bytes`. |

**Nothing this process opens follows a redirect**, in either direction and for
both the camera and the lane. It matters most at the camera: the retry is the
request that carries `Authorization`, so a camera answering `302` to it would
hand a site's camera password to whichever host it named.

## The store

**One record is two files**, under `[capture] directory`:

- **the JPEG exactly as the camera sent it**, never re-encoded — so the size
  measured is the camera's and not this package's;
- **a sidecar** of `captured_at`, `camera_id`, `reason`, `lane_event_cursor`,
  `lane_event_at`, `capture_minus_lane_event_ms` and `bytes`. Seven fields, and
  there is no eighth.

**A record's name is a timestamp, the camera id and a sequence number, and
nothing else.** A directory listing is readable by anyone who can read the
directory, and a filename is the one part of a file that survives being copied
somewhere with no context.

**Written atomically.** Both files are written under temporary names in the same
directory and then renamed.

**A crash before the first rename leaves no record — AND NO IMAGE.** Those
temporary files hold the JPEG. Left on the disk they are outside the index,
outside `bytes_used`, outside every report, and outside the retention rule
itself, because that rule reads a sidecar and there is none to read: a
photograph of a car, kept for ever, on a box in a gate housing. So both halves
are closed. **A live write removes its own temporary files in a `finally`** —
whatever ended it. And **every temporary file the next index rebuild finds is
removed and COUNTED** as `purged_by_crash`: nothing but a crash can leave one,
because this process is the only writer of this directory and a live write
cleans up after itself. The count is published because how often a site loses
power mid-write is a fact about that site.

The window this cannot close is **one `rename` wide** — between the image's and
the sidecar's — and a crash inside it leaves an image with no sidecar, which is
**reported and purged, never silently kept**. Closing it would need a filesystem
that renames two names at once, so it is named here rather than claimed away.

**A record is BUILT THROUGH THE CONTRACT before anything touches the disk.** The
class `GET /v1/capture/records` builds its page from is the class the write is
validated by, so there is no second opinion to drift: **a record this process
could not publish is a record it does not file**, and the poll that produced it
is refused. Without that, a lane page this build half-accepted could leave a file
on a disk that makes the records route raise for every consumer until it aged
out — up to `retention_days`, with the health route saying `ok` throughout.

**The index is rebuilt by reading the directory, every start. A check, never a
memory.** There is no manifest to go stale and no counter to be wrong, and a file
somebody deleted by hand is simply not in the index. It is also how the three
kinds of half record are found: an image with no sidecar, a sidecar with no
image, and a sidecar **the contract will not accept** — one that will not parse,
one missing a field, or one carrying a combination this process would not have
written. All three are `store_record_incomplete`, and all three are deleted — a
half record is a photograph nobody can say anything about, sitting under a
retention rule that **cannot reach it**, because the rule reads the sidecar.

**And `GET /v1/capture/records` never dies on what it finds.** A record it cannot
publish is reported and purged on the spot rather than raised: a route that
raised would answer nothing, for every consumer, until that one record aged out.

**One process per directory.** Two capture processes sharing one store would each
rebuild an index the other is writing into, and the size cap would be enforced
twice against one disk. Stated rather than locked.

## The retention rule, and a purge that DELETES

`[capture] retention_days` — a per-site setting, published default and bounds in
the closed sets above. **That window and those bounds are the platform's identity
retention window and bounds**, chosen once by Gokhan for personal data at rest in
this estate: a stored photograph of a car at a barrier is personal data in most
places this installs, so it gets the same answer rather than a second one. The
**single copy of the default is `config.DEFAULT_RETENTION_DAYS`**, published into
this document by the payload mechanism below and held to it by a value test —
editing the document goes red, and so does editing the constant. The platform
keeps its copy in a database column and nowhere else; a lane has no database and
this process has no column, so this is where the one copy lives.

`[capture] max_bytes` — **DECLARED, no default, refused at startup if absent.**
Nothing in this package has ever seen a capture from any of the cameras it is
written for, so there is no measurement here to draw a default from, and a
plausible number would be a figure that looked measured.

`[capture] directory` — **DECLARED, no default**, and refused at startup if it is
not there or will not take a write. It is **not created**: a path that is not
there is a typo or a disk that did not mount, and creating it would put a site's
captures on the root filesystem of the box the lane runs on. Writability is asked
of the **disk** — a byte written and deleted — because the permission bits answer
for the wrong thing on a read-only mount, a full disk and a directory with an
ACL.

**The purge runs at startup and around every write. Age first, then size.** A
record older than the window goes because it is old, whatever the disk has room
for; the cap is then applied to what is left, oldest first. Reversing them would
let a large recent day evict a record the retention rule was still keeping
deliberately, which is a window nobody can state.

**A capture that cannot fit is refused BEFORE anything is purged.** `len(image)`
is checked against `max_bytes` first, and **nothing is deleted** — because no
amount of deleting makes room for a capture larger than the whole cap, so a purge
run for one destroys a site's store to make room for a write that is then refused
anyway. The size rule also evicts **only what THIS capture needs**: it is bounded
by that headroom and is never "while there is anything left", which is the same
defect one level down.

**Oldest is by VALUE, not by position.** The index is in insertion order, and a
clock that steps back writes an earlier record after a later one — so "oldest
first" read off the front of a list is whatever happened to be written first.
`oldest_at` and `newest_at` are the minimum and maximum of `captured_at` for the
same reason.

### `clock_stepped_back`, and what it suspends

**A record stamped after the clock that reads it is not deleted early and is not
ignored: it is named.** `clock_stepped_back` is measured on every purge, as
`newest_at` later than `now`, and is `active` for as long as that holds.

**While it is active the AGE rule is suspended for those records** — a record
stamped later than now is not older than any window, so the retention rule
cannot reach it — **and the size rule is the only bound on them.** That is stated
rather than corrected: this process cannot know which of the two readings is the
right one, and deleting a record because a clock moved is exactly the failure a
retention window exists to prevent.

**One clock is not a monotonic one.** This package reads every moment — the stamp
on a record, the window it is measured against, the projection — from a single
callable, and that is worth having; it is not a fix for a clock that steps. A box
in a gate housing with no RTC battery, which is the environment this package
names for itself, is corrected by NTP after it comes up.

**It DELETES, and that differs from what the platform does on purpose.** The
platform's identity retention nulls a vehicle's attributes and keeps the row,
because the row is a foreign key and a money record hangs off it. Here there is
no foreign key and no money record: **the image IS the datum**, and a retention
rule that kept it would not be one.

**Every purge is a log line and a counter on the health route** —
`purged_by_age` and `purged_by_size` — so a store that is silently eating itself
because the cap is too small is visible rather than quiet.

`store_over_budget` is what is left when the rule cannot work: a single capture
is larger than the whole cap, or one purge could not get under it. The write is
**refused and named** rather than dropped — a store that quietly discarded what it
could not fit would be a recording missing exactly the busiest hour.

A correctly configured process cannot reach it, and that is deliberate:
`max_snapshot_bytes` is refused at startup unless it is below `max_bytes`, so
nothing a camera can answer with is larger than this store can hold. It remains
the store's own refusal, so that a caller handing it more is told rather than
obeyed.

## `GET /v1/capture` — who this process is, and what it is set to do

<!--payload:capture-->
```json
{
  "capture_id": "capture-1",
  "site_id": "site-1",
  "contract_version": 1,
  "directory": "/var/lib/openparking/captures",
  "interval_seconds": 60.0,
  "retention_days": 30,
  "max_bytes": null,
  "max_snapshot_bytes": null,
  "lane_declared": true,
  "lane_url": "http://127.0.0.1:8090",
  "cameras": [
    {
      "camera_id": "front",
      "snapshot_url": "http://127.0.0.1:8080/axis-cgi/jpg/image.cgi",
      "authenticated": true
    }
  ]
}
```

**`interval_seconds` and `retention_days` in that example are the published
defaults, and a test compares them by value against the constants they come
from.** Every SIZE in every example on this page is `null`, and that is
deliberate: **no disk figure, no bytes-per-image and no capacity appears anywhere
in this document.** Nothing in this package has measured one — the figures are
READS, on the health route, against one site's own disk. A number here would look
measured, and a test refuses one.

`directory` is on this route because where a site's personal data is kept is the
first question anybody asks of this process, and the answer is a path on a box
rather than a credential.

`max_snapshot_bytes` is on it **beside `max_bytes`**, because the relationship
between the two is the thing a site has to get right and startup refuses them the
wrong way round.

`snapshot_url` and `lane_url` are **rebuilt** from scheme, host, port and path,
the way `GET /v1/monitor` rebuilds a target's. `authenticated` says a credential
is configured. It is not the credential: **every credential in this package is
read from a FILE**, is on no route, and is not in this process's argument vector.

`lane_declared` is `false` at a standalone site, and `lane_url` is `null` with
it. It decides whether any record here can ever carry a lane event reference.

## `GET /v1/capture/health` — every code, and what is on the disk

<!--payload:capture_health-->
```json
{
  "contract_version": 1,
  "codes": [
    {
      "code": "camera_feed_frozen",
      "subject": "front",
      "state": "ok",
      "source": "measured",
      "never_alarm": false,
      "caveat": "IDENTICAL means identical: this compares the bytes of two consecutive snapshots and nothing else. A camera that burns a clock, a date or a frame counter into the image is therefore NEVER frozen by this measure, however dead its sensor -- the overlay changes the bytes. A camera with no overlay pointed at an empty lane at night can be byte-identical while working perfectly. This measure is a cheap true negative, not a test of whether a camera is seeing.",
      "status": null,
      "cause": null
    }
  ],
  "lane_events_missed": null,
  "store": {
    "bytes_used": null,
    "record_count": null,
    "oldest_at": "2026-08-30T14:00:00+00:00",
    "newest_at": "2026-08-30T14:03:11.482913+00:00",
    "mean_bytes_per_record": null,
    "records_last_24h": null,
    "bytes_last_24h": null,
    "projected_bytes_per_day": null,
    "purged_by_age": null,
    "purged_by_size": null,
    "purged_by_crash": null
  }
}
```

### `codes`

One entry per `(code, subject)`, and **every member of `capture_codes` ships on
every response** — a payload missing one is refused when it is built, because a
code that is absent reads to a consumer exactly like a code that is fine.

**AND COMPLETE PER `(code, camera)`, which is the same rule one level down.**
Every declared camera ships under **every camera code**, on every response,
`unknown` until its first attempt — and a payload missing one of those pairs is
refused when it is built, exactly as a missing code is. Without it, a camera that
has never produced a state disappears from this payload the moment any OTHER
camera reports: and the camera that has not answered since the process started is
the one worth asking about. At a site with four cameras, Gokhan's *"camera
disconnected is a malfunction"* would fail for the camera that is worst broken.

The lane's four codes ship under this process's own id at a **standalone** site:
there is no lane to name, and *"no lane is declared"* is not the same fact as
*"the lane is answering"*.

The entry is the **lane contract's entry, field for field** — `state`, `source`,
a boolean `never_alarm` on every entry, and a `caveat` — plus two fields that
belong to a code rather than to the shape: a `status` on the codes that exist
because something answered, and a `cause` on `camera_unreachable`. That the
first four are the lane's is not a coincidence: it is what lets a monitor read
this surface with the code that already reads a lane, and a monitor passing these
entries on carries the four it knows.

`lane_events_missed`, beside `codes`, is **how many lane events this process is
known not to have followed since it started** — see `lane_backlog_lost` above.
`0` at a standalone site: there is no lane to miss events from.

**Everything here is `measured` and nothing here is `never_alarm`.** Every code
is a physical thing that needs a person: a camera that has stopped answering, a
disk that will not take a write, a store eating itself under a cap that is too
small.

`camera_feed_frozen` is the one that comes closest to a false alarm, and what it
measures is published in its caveat rather than softened into silence: it
compares the **bytes** of two consecutive snapshots and nothing else. **A camera
that burns a clock, a date or a frame counter into the image is therefore never
frozen by this measure, however dead its sensor** — the overlay changes the
bytes. And a camera with no overlay pointed at an empty lane at night can be
byte-identical while working perfectly. It is a cheap true negative, not a test
of whether a camera is seeing. It is `unknown` until there have been two
snapshots, and `unknown` again after any read that failed — comparing across an
outage answers a different question.

### `store` — the sizing, as READS

Every field is measured from the directory when the route is asked. **This
document states that they exist and what each is derived from. It states no
value**, because nothing here has ever measured one.

| | |
|---|---|
| `bytes_used`, `record_count` | Summed over the index, which is read off the disk. |
| `oldest_at`, `newest_at` | The minimum and maximum `captured_at` held, **by value**. Not the ends of the index: a clock that steps back writes an earlier record after a later one, and read by position these two come out the wrong way round. |
| `mean_bytes_per_record` | `bytes_used` over `record_count`. `null` at an empty store. |
| `records_last_24h`, `bytes_last_24h` | Over records whose `captured_at` is within 24 hours of now. |
| `projected_bytes_per_day` | `bytes_last_24h` scaled to a day over however much of that window this store has actually been recording for. **`null` under an hour of data** — multiplying four minutes by three hundred and sixty is a number that looks measured. |
| `purged_by_age`, `purged_by_size` | How many records each half of the purge has removed since this process started. |
| `purged_by_crash` | How many temporary files an index rebuild has removed since this process started. One of them is a write that died — nothing else can leave one — so this is how often a site is losing power mid-write. |

## `GET /v1/capture/records?since=N` — the sidecars, never the bytes

<!--payload:capture_records-->
```json
{
  "contract_version": 1,
  "cursor": 2,
  "reset": false,
  "dropped": null,
  "records": [
    {
      "cursor": 2,
      "id": "20260830T140311482Z_front_000002",
      "captured_at": "2026-08-30T14:03:11.482913+00:00",
      "camera_id": "front",
      "reason": "lane_vend",
      "lane_event_cursor": 7,
      "lane_event_at": "2026-08-30T14:03:11.102913+00:00",
      "capture_minus_lane_event_ms": 380,
      "bytes": null,
      "image_url": "/v1/capture/images/20260830T140311482Z_front_000002"
    }
  ]
}
```

Deliberately the same shape and the same semantics as the lane contract's
`GET /v1/lane/events` and the Vehicle ID service's `GET /v1/reads?since=N`, field
for field, so **one consumer holds one cursor policy for every surface in this
estate.**

- The cursor is **monotonic within one run** and is **not durable across a
  restart**. The store is durable; the cursor over it is not. On start the index
  is rebuilt by reading the directory and numbered from one in capture order, so
  a saved position no longer refers to the same record once anything has been
  purged.
- `since` ahead of this process's own cursor sets **`reset`**.
- `since` behind the oldest record still held **also** sets `reset`. Here the
  window is the STORE, and what evicts from it is the retention rule and the size
  cap — so this flag is how a consumer learns that what it was going to fetch has
  been **deleted**, rather than simply not served.
- `dropped` is how many records the purge has removed since this process started.
  A gap nobody knows about is worse than one that is counted.

**The bytes are never inline.** A records page is a page a consumer polls, and a
JPEG on it would make the cheapest read the most expensive one.

### `capture_minus_lane_event_ms`, and the two clocks it spans

Present on exactly the records a lane triggered, and `null` on an interval
capture, which has no lane event to subtract. **It used to be called
`trigger_to_capture_ms`** — a name that said "delay", which is a measurement, for
a number that is a subtraction across two machines. It is named for what it is,
and what it is says so here, in the one copy this document and the code share:

> This is a SUBTRACTION ACROSS TWO CLOCKS: `captured_at` is read from this
> process's clock and `lane_event_at` from the lane's. It is not a measured
> delay. A NEGATIVE VALUE IS REACHABLE and is served -- it means the two
> clocks disagree by at least that much, with the lane's ahead. Nothing here
> measures the offset between them, so nothing here can separate the offset
> from the time this process took to see the event, and correcting it would
> mean a second measurement nobody has made. Where the two clocks are the
> same box, or are disciplined to the same source, it is the cost of this
> process being a CONSUMER of the lane's contract rather than something the
> lane calls.

This is the monitor half's "`lane_gone_quiet`, and the two clocks it spans"
applied to this field, and for the same reason. The sentence lives in
`contract.CAPTURE_MINUS_LANE_EVENT_NOTE`, is published here from that one copy,
and a value test holds the two together: editing either goes red.

## `GET /v1/capture/images/<id>` — one JPEG

`Content-Type: image/jpeg`, the bytes exactly as the camera sent them. `404` for
an id this store has no record of.

**The id is looked up in the index. It is never joined onto the directory** — so
`../` in one finds no record rather than finding a file, and the only paths this
process opens are ones it wrote.

## Running it

```sh
gate-agent capture --config capture.toml
```

Binds `127.0.0.1:8093`. **Local by design** — this is meant to run beside the
lane it photographs.

**Off loopback it refuses to start without a credential.** `--host` anything but
loopback requires `--auth-token-file`, and with a token **every route requires
`Authorization: Bearer <token>`, including the images.** That is stated because
an image route left open "because it is just a JPEG" is the whole store readable
by anyone who can enumerate a record id.

The rule is `InsecureBind`, the same one the monitor, the lane service and the
Vehicle ID service apply — imported here rather than restated, so three surfaces
on one device cannot come to disagree about when a credential is required. The
exposure it exists for is the sharpest of the three: the monitor publishes which
of a site's lanes are broken; **this publishes pictures of cars and when they
were taken.**

## What is NOT here, stated rather than left to be discovered

- **No act surface.** No capture on demand, no delete, no route that moves a
  retention window. A record is deleted by the retention rule and by nothing
  else — a delete route would be a way to remove the one image that mattered
  from a store whose purpose is that the entries can be reconstructed
  afterwards, and it would need an authorisation model this package does not
  have.
- **No plate reading from a stored capture.** This process records; it does not
  identify. SETTLED 3g's point is that the camera does not need to be
  trustworthy enough to DECIDE in order to be useful enough to RECORD.
- **No lane change of any kind.** The lane's own capture MODE — the accuracy bar
  changing when the boom is broken — is the lane's decision path and a later
  round. This round gives it somewhere to record.
- **No RTSP**, and no camera that authenticates in a query string. See above.
- **No second store.** Nothing is uploaded anywhere, and this process reports to
  no platform. What leaves this box is what somebody fetches from these routes.
- **No sizing.** How much disk a site needs is a read of that site's disk, on the
  health route. Nothing in this package has measured a capture from any of the
  cameras it is written for, and no figure appears in this document. The one
  number that does — `max_snapshot_bytes_default` — is a ceiling on a read, is a
  setting, and says so where it is published.
- **No clock discipline, and no measurement of an offset.** This process reads
  one clock. It does not correct one, it does not compare one against a lane's,
  and it does not know how far out either is. What it does instead is say when
  the two disagree in a way that changes what it keeps: `clock_stepped_back` on
  its own, and `capture_minus_lane_event_ms` on every lane-triggered record.

---

Built by 72 Knots Method by 72Knots.ai

---

# The gate agent

The third process in this package, under the same `contract_version` as the two
above. It answers the intercom, works out which lane the call belongs to, reads
that lane's last decision through the lane contract, says what happened in every
language the site declared, and when the case needs a person it calls one, stays
in both calls, and records the authorisation they key.

## It OPENS NOTHING, and that is what this version is for

An **authorisation is a RECORD of what a person said. It is never an act.**
`OPEN_NOW` ends in an event, a message to the driver, and one fixed sentence to
the person who keyed it saying that this version cannot operate the barrier.

That is not a promise about intention. There is no vend route on this surface;
`ACT_ROUTES` is empty and every method other than `GET` is answered `405`. There
is no vend route on the lane contract this build reads, and that contract's own
`capabilities.can_vend` is `false`. And the only client in this package cannot
build a request that is not a `GET`, which is swept out of the source and
observed at the lane, not asserted here.

`can_vend` on `GET /v1/agent` is **derived from an empty act table**, so it
cannot say `false` while something in this package can act.

## What has been measured, and what has not

**The SIP path has been exercised against baresip 4.11.0, in CI, on a real
socket:** a registrar built in the test suite, a second baresip instance placing
a call as the intercom, a third answering as the person, real RTP between them,
real RFC 4733 DTMF, and the audio each side received written to a file and
measured. That test is what establishes that a call is answered, that the two
legs are private until the agent bridges them, and that a digit arrives tagged
with the leg it came in on.

**It has never been run against an Axis, a 2N, or any other real intercom, and
it cannot be here.** Call setup time, audio quality, echo, and DTMF detection
through a door station's microphone and a real garage's network are **NOT
MEASURED** — and neither is **whether a door station's call list advances on the
`486 Busy Here` this agent sends when it is already on a case, or only on a
no-answer timeout.** Nobody should read the CI result as a statement about any of
them.

**Install requirement, and this package cannot enforce it:** the intercom's own
call list must name the AGENT FIRST and the human's number on no-answer. That is
what makes a dead agent degrade to a plain intercom instead of to silence, and it
is the whole of "a partial failure stays partial" at this seam. It is configured
on the intercom, by whoever installs it.

## The user agent is an install requirement, not a dependency

SIP, RTP, DTMF and audio are an external user agent's job. This package is
dependency-free and contains no SIP stack; it drives **baresip** over that
program's `ctrl_tcp` control socket and holds only the dialogue state and the
records. The version is read at startup and **a version this build was not
tested against is refused** — the `schema_version` rule applied to a process,
because a control vocabulary is not a versioned contract and a command that has
grown a parameter is a call answered and then handled wrongly.

Tested version: **baresip 4.11.0** (BSD-3-Clause). Debian and Ubuntu package
older releases that do not carry the two modules this needs, so it is built from
source at that tag on the target and on the CI runner.

Seven things on the baresip side are load-bearing, and every one of them is
configuration rather than code. **Four of them are now CHECKED, at startup, by
reading the running process back** — `ctrl_tcp` answers `config` with the loaded
settings, `modules` with the loaded module list, both from baresip's `debug_cmd`
module (which is why that module is in the required list), and `reginfo` with
every account it holds. The agent refuses to start on any of them, naming the
setting or the intercom. Reproduce: `pytest -k baresip_configuration` and
`pytest -k account`.

| | checked at startup? | |
|---|---|---|
| `ctrl_tcp_listen 127.0.0.1:4444` | no — the agent reaches it, so it is on a socket by definition | Loopback. That socket can place a call, bridge two of them, and play audio at whoever is on the line. baresip's own default is every interface. |
| `call_hold_other_calls no` | **yes**, read from `config` | baresip's default holds every other call when a new one is established, which would put the driver on hold the moment the agent calls the operator. |
| an audio device that is **not** `aubridge` | **yes**, `audio_source` and `audio_player` read from `config` | baresip's own module documentation says what it is, verbatim: it "can be used to connect two audio devices together, so that all output to AUPLAY device is bridged as the input to a AUSRC device". That is every call bridged to every other one whether the agent asked or not. **What it then does to this agent is NOT MEASURED and no number here depends on it** — an earlier sentence claimed the operator heard the driver before `conference` was sent, and an independent session could not reproduce it. The refusal does not rest on that claim; it rests on what the module is. |
| modules `ctrl_tcp`, `mixausrc`, `mixminus` (and `aufile`, `account`, `menu`, `debug_cmd`) | **yes** for the first three, read from `modules` | `mixausrc` is what plays one file into ONE leg; `mixminus` is what the bridge is; `debug_cmd` is what answers the two commands this check is made of. Without them the agent can answer a call and do nothing else. |
| one account per declared intercom, named by its `dial_secret_file` | **yes**, read from `reginfo` at startup | It is what says which intercom a call is from. A missing one is refused by name — the intercom's name, never the secret's. |
| `sip_cuser_random` **unset** | no — baresip 4.11.0 does not report it on `config` (measured; `filter_registrar` is in that output and this is not) | With it on, every account's contact user gets a random suffix and an INVITE aimed at the account's own user part is answered `404 Not Found` — measured, with the plain configuration as the control. **It fails CLOSED:** the intercom cannot get in at all, so this is a door that never works and never a door somebody else can open. |
| **nothing else attached to that control socket** | no — it is not a setting | baresip's `ctrl_tcp` accepts exactly ONE client. A second connection — a console, a script, a monitoring tool — takes the agent's away. **The agent now REOPENS it:** see below. |

### The control socket is reopened, and how long that takes is a setting

A lost `ctrl_tcp` used to be a permanent outage. The socket was raised on and
never replaced, so an ordinary `systemctl restart baresip`, a package upgrade, an
OOM kill, or the second client above left the agent alive, its user agent
registered, and every call ringing at a process that would never answer one —
`ua_unreachable` `active` for the life of the process, with the only repair a
human restarting the agent.

`[user_agent] reconnect_seconds` (published default **5.0**, on `GET /v1/agent`)
is the LONGEST gap between attempts to reopen it. The first retry is a quarter of
a second away and the gap doubles up to the setting, so a service restart is
recovered from in about a second and a user agent that is gone for good is not
hammered once per poll for ever. `ua_unreachable` **recovers** on the reconnect.

**What happens to the calls that were up.** Whatever case was in progress is
gone — its legs were torn down or are beyond reach, and nothing can say what was
said while nobody was listening — so the session is dropped with
`case_not_spoken`, and every call the user agent is still holding is dealt with
by the same rule any new call gets: one still **ringing** is answered, and
anything else is released rather than left live to be conferenced into the next
case.

**One account per leg, and the legs are on different ones.** baresip identifies
the audio stream to play into by the local account, so two calls on one account
cannot be told apart — and the menu meant for the person on the phone would play
to the driver at the barrier. The driver's leg sits on whichever intercom
account the call arrived at (below); `[user_agent] operator_aor` is the one this
agent dials OUT from, declared, and startup refuses it if it collides with any
intercom's.

## Which intercom a call is from

**IT IS THE ADDRESS THE CALLER DIALLED. IT IS NOT WHO THE CALLER SAYS IT IS.**

Each declared intercom gets an account of its own on the user agent, whose user
part is a long random string the site generates once:
`[intercoms.<sip-uri>] dial_secret_file` names a file holding it — a FILE, no
default, and refused if anybody but its owner can read it, exactly like every
other credential this package takes. The installer does three things once:
writes the secret, adds `<sip:agent-<secret>@<the agent's host>>` to the user
agent's own `accounts` file, and programs that same address into the door
station as the number it calls. **A call is that intercom if and only if it
arrived AT that account.**

Why this and not the `From` header: baresip routes an inbound INVITE by the
REQUEST-URI's user part and reports the account it chose on the control socket
(measured on 4.11.0, with a registrar and without one), and the secret therefore
never travels in a header a caller writes. It is the number dialled. A caller
who asserts a declared door's own address of record — which used to be answered
as that lane, ring a person, and write a complete authorisation record naming a
barrier nobody was standing at — now reaches an account it cannot name, and the
user agent answers it `404 Not Found` before this agent sees anything.

**What this does NOT do, stated plainly because it is what an integrator needs
to decide with.** A secret in a device's configuration is only as private as
that device: anybody who can read the door station's own configuration can call
as that door. That is a different exposure from a `From` header, which anybody
on the same network can write without touching anything at all, but it is not
nothing — and **nothing here measures it.** It is also not a secure channel:
this is UDP SIP on the site's own network unless the site puts TLS under it, and
an attacker who can READ the intercom's traffic can read the address it dials.
What the mechanism removes is the attacker who can only SEND.

`[intercoms.<sip-uri>] lane = "<lane name>"`, **per site, no default**, and
startup refuses an intercom with no lane or a declared lane with no intercom. An
agent that guessed which lane would be guessing which barrier somebody is
standing at.

**The table key is a LABEL.** It is what appears on events, on the read surface
and in front of a person; it is what the `From` header is compared against for
the record; and **nothing is routed on it.** It is recorded as
`caller_stated_identity`, reduced by shape rather than character for character —
a door station sends `"Door 1" <sip:door1@10.0.0.9:5060>;tag=…` on one call and
the bare URI on the next, and two spellings of one claim would read as two
callers.

A call at an account no `[intercoms.*]` owns is **refused without being
answered**. So is one the user agent refused itself. Both are an event
(`call_from_undeclared_intercom`) and a code of the same name, carrying what the
caller claimed to be and nothing else, because a claim is all there was. **No
lane is read and none is guessed**, and nothing is played: this version has no
sentence for a caller it cannot place, because answering one means speaking to
somebody about a barrier it would have to guess at.

**That is what happens when the agent is not already on a case.** The account is
looked at only then — see "One case at a time" below: a live case refuses every
new call, whoever it is from, before anybody's identity is read.

**Startup refuses an intercom whose account the user agent is not holding**,
naming that intercom and never the secret. Without it, a door the installer
added to this file and forgot to add to the user agent's would be answered
`404 Not Found` by baresip and reported nowhere, while this agent published a
working surface.

**`lane = "none"` is STANDALONE**, and it is a mode rather than a degraded
configuration: a garage with an intercom and no lane is the whole product for
that site, and every call there is a human case from the first second.
`[lanes.none]` is refused by name so that one spelling cannot mean two things.

## One case at a time

`concurrent_cases` on `GET /v1/agent` is **1**, and it is a real limit rather
than a policy: the user agent's bridge is site-wide, so a second case bridged
while the first is open would put two strangers and two operators into one
conversation.

A call arriving during a case is **refused without being answered**, whoever it
is from — the identity is not looked at first, because being undeclared is the
default state of every caller on a network and the limit has to hold against all
of them.

**What the refusal IS, measured from the caller's side:** the agent hangs the
unanswered call up, and baresip sends **`486 Busy Here` after `180 Ringing`**.
That is read out of a second caller's own user agent, not out of ours. It is
recorded as `call_refused_busy`, with the caller's identity on the record.

**Whether a door station's call list advances on a `486` — rather than only on a
no-answer timeout — is NOT MEASURED.** It is a property of an Axis or a 2N unit,
and this package has never been run against either.

## The case set, and how it is derived

The case is a **pure function of what the lane published** — `GET /v1/lane/state`
and `GET /v1/lane/health`, GET only, with the timeout that lane's target
declares. It is **derived and never asked**: a driver at a barrier does not know
whether the identification service is down or their plate was marginal, and a
menu offering them the choice would be a guess with a keypad.

The order matters and is part of the contract. Standalone first, because there is
no lane to ask. Then whether the lane could be read at all. Then a malfunction,
because a broken lane's last decision is not a fact about the vehicle standing at
it. Then the outcome — and the transit decides something only under `allow`,
because under `deny` and `no_vehicle` there was no vend for closing loops to have
confirmed.

| the lane says | case | ends with |
|---|---|---|
| any malfunction `active` whose `never_alarm` is `false` on the wire | `malfunction_active` | a person |
| `outcome: fallback`, `reason: engine_unreachable` | `identification_unavailable` | a person |
| `outcome: fallback`, `reason: no_plate_read` | `plate_not_read` | the instruction, then a person |
| `outcome: fallback`, `reason: low_confidence` | `plate_unclear` | the instruction, then a person |
| `outcome: fallback`, `reason: unknown_vehicle` | `vehicle_not_recognised` | a person |
| `outcome: fallback`, `reason: stale_rules` | `rules_unavailable` | a person |
| `outcome: deny` | `entry_refused` | a person |
| `outcome: no_vehicle` | `vehicle_not_detected` | a person |
| `outcome: allow`, transit `held` or `unconfirmable` | `entry_not_confirmed` | a person |
| a decision whose `decision.at` is older than `[cases] decision_max_age_seconds` | `stale_decision` | a person |
| a decision whose `decision.at` is missing, unparseable, or carries no timezone | `unrecognised_reason` | a person |
| a `reason` outside the required subset, a decision the lane has not made, or any other answer this build will not interpret | `unrecognised_reason` | a person |
| the lane did not answer, refused us, or speaks a version this build cannot read | `lane_unavailable` | a person |
| `lane = "none"` | `standalone` | a person |
| `outcome: allow`, transit `confirmed` or `pending` | `nothing_to_do` | one message, and the call ends |

**`identification_unavailable` never mentions the plate.** A dead identification
engine and a marginal read used to arrive as the same code; telling somebody to
clean a number plate that nothing looked at is the standing acceptance of this
project broken in the module's first sentence.

### The decision has an AGE, and a stale one never ends a call

`GET /v1/lane/state` publishes `decision.at`. The agent reads it, and the case
function is given a clock, so the age of the decision is checked **before any
outcome branch** — a decision the lane made for somebody else is not a fact about
the driver standing at the barrier now, whatever it said.

`[cases] decision_max_age_seconds`, per site, **published default 120**. It is a
SETTING AND AN ASSUMPTION, and that is said rather than implied: **nothing has
measured how long a lane decision stays the same car's.** Two minutes is drawn
from a person walking from a stopped car to a door station and pressing a button,
which is a guess about people and not a measurement of them. What is not a guess
is which way the error falls — past the bound the driver gets a person, which is
what every other case in the set already gets.

**This is the only guard in front of `nothing_to_do`**, and `nothing_to_do` is
the one case in the whole set that reaches nobody. Before it existed, a lane
whose last decision was an hour-old `allow`/`confirmed` — which is exactly what a
presence gate that does not arm leaves behind — told the next driver "this
entrance has nothing outstanding for you" and hung up on them. `nothing_to_do` is
now reachable only from a FRESH `allow` with transit `confirmed` or `pending`.

> This is a COMPARISON ACROSS TWO CLOCKS: `decision.at` is read from the LANE's
> clock and `now` from this process's. It is not a measured age. A NEGATIVE AGE
> IS REACHABLE — a decision stamped after the moment this process reads it — and
> it is treated as FRESH, because the alternative is sending a driver to a person
> on the strength of a clock offset nobody has measured. Nothing here measures
> the offset between the two, so nothing here can separate it from the age it is
> trying to read. Where the two clocks are the same box, or are disciplined to
> the same source, this is the cost of being a CONSUMER of the lane's contract
> rather than something the lane calls.

That sentence lives in `cases.DECISION_AGE_NOTE`, is published here from that one
copy, and a value test holds the two together: editing either goes red. It is the
capture process's `capture_minus_lane_event_ms` note applied to this field, and
for the same reason.

**A `decision.at` this build cannot read is `unrecognised_reason`, not fresh.** A
missing stamp, one that does not parse, and one with no timezone all fall to the
catch-all — the round-4 rule, and the same reason: a naive moment compared
against an aware one is a guess about which machine it came from.

**`vehicle_not_detected` is the case the intercom exists for.** The presence gate
is unvalidated on real vehicles, and a real car it wrongly refuses has no other
recourse: there is a driver at the barrier and the lane believes the lane is
empty.

**An unrecognised reason ESCALATES and is never mapped onto the nearest thing we
know.** A lane that is not ours has its own vocabulary and will emit it. The lane
contract requires a consumer to escalate on one it does not recognise, and this
is that requirement implemented.

`reason` is the one set the lane contract does **not** publish, so the required
subset is a copy held in `cases.REQUIRED_FALLBACK_REASONS` and compared against
the installed lane package by a test, in both directions.

## Languages

`[languages] driver = [...]` and `operator = "..."`, **declared per site, no
default**, and startup refuses either if it is empty or names a language this
build has no lines for.

**The driver has no keypad**, so every sentence they hear plays in EVERY declared
driver language, in the declared ORDER, one after the other. The person on the
phone hears the operator language only; they are staff, and a menu played twice
is a menu somebody keys over.

**The language is PER CALL and can narrow mid-call.** Gokhan's spec: *"if the
customer starts speaking in Spanish, no English, it should start Spanish from
there."* One function does it — `Agent.set_language(call, language)` — and from
the next sentence on, that call is spoken in that language and no other. A
language the site did not declare is **refused**, not accepted and then found to
have no audio: a switch that silently did nothing would leave a driver being
spoken to in a language they have just said they do not have.

**What NOTICES they switched is not in this version.** Hearing a language is
automatic speech recognition, which is a later step and is gated on a
measurement of narrowband SIP audio nobody has made. What is here is the state
that step will set, so that step adds a detector and nothing else.

**Every string is a value in the repository, per language, tested**, and the
audio file for it is a file the package installs whose name derives from the
line's key and the language. **A line with no words in a declared language, or
no audio file, refuses startup** — not skipped, not substituted, not played in
another language, because a driver who hears silence at a barrier has been told
nothing and cannot tell it from a dead intercom.

This version ships **English and Spanish**, end to end: text, audio, and the
mechanism proven with two rather than described with one.

### Where the audio comes from

Every file is produced by `scripts/build_audio.py` from the text in
`lines.TEXT`, and `audio/MANIFEST.json` records, per file, the exact text it was
made from, its digest, its voice and its licence — so a sentence edited without
regenerating its audio goes red rather than shipping a file that says the old
thing.

The synthesiser is **eSpeak NG** (GPL-3.0-or-later). It was chosen for the
licence and not for the voice. The obvious alternative was disqualified by the
licence of its **recorded corpus** — research use only, no redistribution — and
eSpeak NG has no corpus at all: its own README says it "is not as natural or
smooth as larger synthesizers which are based on human speech recordings", which
is the same sentence read as a licence fact. It is machine speech. It is
intelligible at 8 kHz over a narrowband call, which is the property this job
needs, and a site that wants a voice replaces the files — the manifest records
what each one has to say.

**Who wrote the WORDS, and from what**, is a row per language in the manifest —
`text_provenance` — and every file names its row. The manifest recorded the
voice, the tool and the tool's licence for the AUDIO and nothing at all about the
TEXT, which is the thing the audio is only a rendering of. Both rows say what was
NOT done as plainly as what was: the English and the Spanish were written by the
software that wrote the rest of this package, and neither has been through a
professional editor, a translation service, or a native speaker.

**The Spanish ships as `es-ES`, not `es`.** It is Castilian — `matrícula`,
`aparcamiento`, `almohadilla`, `Pulse` — and under a generic tag a garage in
Texas or Bogotá would declare "Spanish", get this, and hear several words that
are wrong for its drivers. A regional tag is the one thing that makes that
visible in the site's own configuration file.

The name of a DOOR is not in this repository and cannot be: `[intercoms.<uri>]
name_audio` is a file the SITE supplies, played to the person on the phone
before the case, and startup refuses an intercom without one. It is also the one
audio file this package does not produce, so it is the one whose properties are
checked rather than known: **8 kHz, mono, 16-bit** like everything else the agent
plays, and no longer than `[speech] name_audio_max_seconds` (published default
**10.0**). The person's briefing waits for the whole of it, and a driver at the
barrier waits for the briefing.

### A line that cannot be played is a code, a timer and a true record

Playing a file can be REFUSED by the user agent. Usually that is benign and a
fifth of a second from resolving — the call's audio stream has not come up yet —
so the line is kept and retried. What had no answer was the other cause: a file
the user agent will not decode, an audio mode it will not play into, a mixer
stuck in a mode it cannot leave. Retried for ever, that is a driver in an
answered call hearing nothing, with no timer, no code, and a log saying their
case was spoken. **Measured on this build: thirty-three hours of it, and a clean
health surface throughout.**

`[speech] line_timeout_seconds`, per site, **published default 10.0**. The clock
starts when a line becomes DUE rather than when it is first refused, because a
leg whose media never comes up is never even attempted and to the driver that is
the same silence. Past it:

| the leg | what happens |
|---|---|
| the driver's | `audio_playback_failed` `active`, subject `driver`. The case is still a case, so it goes to **the person** — briefed the same way, and timed the same way. |
| the person's | `audio_playback_failed` `active`, subject `operator`. Nothing is left that can tell anybody anything, so the case ends with **`case_not_spoken`** and the driver's call is RELEASED rather than held open in silence. |

`SPEAKING_CASE` and `BRIEFING` are both bounded by this. Neither used to be:
both advanced only on a leg falling silent, which a queue that never drains never
does.

**`case_spoken` is written when the last file of the case has FINISHED playing**,
never when it is queued. It used to be written at the moment the first file was
put on the queue, which is a claim about a queue — and it stayed true in the log
through the thirty-three hours above.

> **The finish is timed from the file's own measured duration, not from a signal
> the user agent sends, and that is a MEASUREMENT rather than a choice.** baresip
> 4.11.0 emits **no** playback-complete event for `mixausrc_enc_start`, the verb
> every sentence here is played with: its `mixausrc` module logs the end of a
> file at debug level and raises no `bevent`, and `BEVENT_END_OF_FILE` is emitted
> only from the call's own audio-device error handler, which this path does not
> go through. Measured on a live call, with the positive control that DTMF events
> arrived on the same drained control socket in the same window. The duration is
> read out of each file at startup, which is a property of something this package
> ships and can measure.

## The person, the bridge, and the authorisation set

`[escalation] human_sip_uri` is declared, with no default.

The agent places a **second call** to that address and stays in both. Before
bridging it plays the person the intercom's `name_audio` and the case in the
operator language — **the lane and the case, and nothing else. No plate.** This
agent never reads a plate: `GET /v1/lane/state` carries `read_ref` and not a
plate, and this package does not read even that, so there is none here to leave
out.

Then the menu, then the bridge. Everything before the bridge is private to one
leg, which is what lets the person be told the case without the driver hearing
it.

**The digits are fixed and published**, and a site enables a subset rather than
renumbering the set — the person keying it is often the same person across
several garages at three in the morning, and a mapping that moved between sites
would be a wrong decision made by muscle memory.

| digit | authorisation | what it does in THIS version |
|---|---|---|
| 1 | `open_now` | records it; the person hears the one fixed sentence saying this version cannot operate the barrier |
| 2 | `open_and_flag` | the same, and the record carries the value |
| 3 | `do_not_open` | records it; the driver is told |
| 4 | `hold` | records it, and the driver is re-prompted every `hold_reprompt_seconds` |
| 5 | `transfer` | records it. Needs `[escalation] transfer_sip_uri`, and startup refuses the pair otherwise rather than quietly not offering an option a site switched on |
| 6 | `call_back` | records the number keyed, ending with `#`. Digits only |

`[authorisations]` is a block of booleans, **no default**, and a site that
enables none is refused: a person called who can authorise nothing has been rung
for nothing. **Only what is enabled is offered**, and a digit outside the enabled
set is re-prompted twice and then treated as nothing usable — never mapped onto
the nearest enabled thing.

**Two timers, both per-site settings with published defaults, and both
assumptions** — nothing here measures how long a person takes to reach a phone:

- `no_answer_seconds`, default **30.0** — the person did not pick up. The driver
  is told, `human_unreachable` is recorded and the code of the same name goes
  `active`.
- `nothing_usable_seconds`, default **20.0** — no digit this site accepts. Same
  shape.
- `hold_reprompt_seconds`, default **45.0** — how often a driver on hold is told
  they are still on hold, because silence on a door station is indistinguishable
  from a dead intercom.

**Neither timer opens anything, and neither does any authorisation.**

## `GET /v1/agent` — who it is, and what it answers

<!--payload:agent-->
```json
{
  "agent_id": "agent-1",
  "site_id": "site-1",
  "contract_version": 1,
  "can_vend": true,
  "intercoms": [
    {
      "sip_uri": "sip:door1@10.0.0.9",
      "lane": "entry",
      "has_display": true,
      "has_relay": false
    }
  ],
  "lanes": [
    {
      "name": "entry",
      "can_vend": true
    }
  ],
  "user_agent": {
    "kind": "baresip",
    "version": "4.11.0",
    "reconnect_seconds": 5.0,
    "tested_versions": [
      "4.11.0"
    ],
    "registered": true
  },
  "languages": {
    "driver": [
      "en",
      "es-ES"
    ],
    "operator": "en"
  },
  "authorisations": [
    "open_now",
    "open_and_flag",
    "do_not_open",
    "hold",
    "call_back"
  ],
  "event_window_depth": 256,
  "concurrent_cases": 1,
  "escalation": {
    "no_answer_seconds": 30.0,
    "nothing_usable_seconds": 20.0,
    "hold_reprompt_seconds": 45.0,
    "transfer_declared": false
  },
  "cases": {
    "decision_max_age_seconds": 120.0
  },
  "speech": {
    "line_timeout_seconds": 10.0,
    "name_audio_max_seconds": 10.0
  }
}
```

`user_agent.version` is what the UA reported about itself and is `null` before it
has answered once; `tested_versions` is what this build will start on. The
control socket's address is **not** published: it is a local socket, and
publishing where a process's control channel lives is publishing a way in.

`authorisations` is what this site enabled, in the order of the closed set.

## `GET /v1/agent/health` — every code, every time

<!--payload:agent_health-->
```json
{
  "contract_version": 1,
  "codes": [
    {
      "code": "sip_registration_lost",
      "subject": "sip:agent@10.0.0.20",
      "state": "ok",
      "source": "measured",
      "never_alarm": false
    }
  ]
}
```

Entries are in the **lane's shape** — `state`, `source`, `never_alarm` on the
wire — so a monitor reads this with the code that already reads a lane and a
capture process. One reader, one passthrough rule, and no third dialect for
`never_alarm` to be read wrong in.

One entry per `(code, subject)`, and **every member of the set ships on every
response**: a code that is absent reads to a consumer exactly like a code that is
fine. A code with no subject yet ships once, `unknown`, under this agent's own
id, and `lane_unavailable` ships once per **declared** lane whether or not a call
has been taken at it.

| code | subject | what it means |
|---|---|---|
| `sip_registration_lost` | this agent | **This is the lane contract's `intercom_registration_lost`, measured where it can be measured.** A lane cannot see whether the agent is registered; the agent can. Both documents say so, in the same words. The subject is the agent rather than an account: it holds one account per intercom now and their user parts are the dial secrets, so naming them here would publish every one of them. Any account the user agent reports in error makes this `active`. `unknown` until the UA has said something — including the whole of a standalone site, which has no registrar to register with — because a registration nobody has heard about is not one known to be lost, and publishing the second pages somebody to a working site |
| `ua_unreachable` | this agent | the user agent's control socket did not answer. The agent is up and cannot answer a call |
| `ua_unsupported_version` | this agent | the UA is a version this build was not tested against |
| `call_from_undeclared_intercom` | this agent | a call arrived at an account no `[intercoms.*]` owns, or the user agent refused one outright because it named no account it holds. **The subject is the agent and not the caller**: the caller's identity is a string the caller wrote, so keying the code on it would let anybody who can dial this agent add a row per identity they invented. It clears when a call at a DECLARED account is answered — which is a measurement, not a timer — and is `unknown` until either happens |
| `human_unreachable` | the escalation address | the person did not answer inside `no_answer_seconds`. **It recovers**: the next call they answer clears it, because a code that could only ever go one way is a latch that reads like a state |
| `audio_missing` | the line, or the file | a file the agent reaches for is not there. Startup refuses on this, so on a running agent it is a file that has gone missing since |
| `lane_unavailable` | the lane's name | per declared lane |

Nothing here is `never_alarm`: every member is a reason somebody at a barrier
cannot be helped.

## `GET /v1/agent/events?since=N` — what it did

<!--payload:agent_events-->
```json
{
  "contract_version": 1,
  "cursor": 2,
  "reset": false,
  "dropped": 0,
  "events": [
    {
      "cursor": 1,
      "kind": "call_answered",
      "site_id": "site-1",
      "agent_id": "agent-1",
      "intercom": "sip:door1@10.0.0.9",
      "lane": "entry",
      "case": null,
      "authorisation": null,
      "human": null,
      "at": "2026-08-30T14:00:00+00:00",
      "keyed": null,
      "caller_stated_identity": "sip:door1@10.0.0.9",
      "released": null,
      "ticket_id": null,
      "authorised_by": null,
      "code": null,
      "reason": null,
      "lane_event_cursor": null,
      "relay_port": null,
      "relay_ms": null
    }
  ]
}
```

The same cursor shape and the same semantics as the lane's, the monitor's and the
capture process's, field for field, so one consumer holds one policy for all of
them: monotonic within one run, not durable across a restart, `reset` when
`since` is ahead of the cursor or behind the oldest event still held, and
`dropped` counting what the window evicted.

**There is no field here for a plate, and there is no plate to put in one.**
`keyed` is the only value on this surface a caller supplies — the call-back
number — and it is **digits only, refused otherwise**, because a field a caller
fills is the field a plate ends up in.

`intercom` and `human` are addresses a site declared, not people: this surface
says which door and which rota, and whoever wants to know who was on shift asks
the rota. `intercom` is the site's own LABEL for the door — the
`[intercoms.<sip-uri>]` key — and on a refusal it is this agent's id, because
there is no door to name.

**`caller_stated_identity` is a CLAIM and its name says so.** It is the `From`
header of the call, reduced to `sip:user@host`. Anybody who can send an INVITE
can write any value in it, so nothing is decided by it: what identified the
intercom is the ACCOUNT the call arrived at, which never appears on this surface
because its user part is a secret. On a refusal it is the whole record of who
tried, and it is worth exactly what a stranger's word is worth.

## Running it

```sh
gate-agent agent --config agent.toml
```

Binds `127.0.0.1:8094`. **Local by design.** Off loopback it refuses to start
without a credential — the same rule and the same words as the other two
surfaces, imported rather than restated. The exposure here is its own kind: this
publishes which intercoms a site has, which of its lanes cannot be read, and when
a person was called and did not answer, which is a timetable of when nobody is
watching a garage.

## What is NOT here, stated rather than left to be discovered

- **No act surface, and no route that changes anything.** Not on this agent, and
  not on the lane contract this build reads.
- **No display code and no SMS.** A `plate_not_read` case speaks the instruction
  and then reaches a person, because there is no completion path in this version
  for a driver to use.
- **No voice recognition.** Nothing here listens to what a driver says; the case
  is derived from the lane, never asked.
- **No state store.** The event window is a catch-up buffer, not a record. What
  happened at a site durably is whatever a monitor's sinks delivered and whatever
  the platform holds.
- **No measurement of a real intercom.** Stated at the top of this section, and
  it is the sentence to read before any of the others.
