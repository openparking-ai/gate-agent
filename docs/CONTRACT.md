# The monitor contract

This is the monitor's public surface. Everything else in the repository is an
implementation detail that may be rewritten; this is not.

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

This document does not list the codes, for the same reason the lane contract does
not list its own: a hand-written copy of a set the code defines is the copy that
goes wrong. Two of them deserve saying out loud anyway, because they are spelt
the same as codes the LANE publishes and they are different facts:

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
gave. `polled_at` is `null` until it has answered once, and `codes` is empty
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

Built by 72 Knots Method by 72Knots.ai
