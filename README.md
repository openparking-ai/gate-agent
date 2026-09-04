# Open Parking AI — gate agent

The intercom module. It ships **three processes**. Two of them open nothing; the
third can ask a barrier to move, where a site configured it to.

**The gate agent** answers the intercom. It works out which lane the call belongs
to **from the address the caller dialled** — each intercom is given an account of
its own whose user part only that site knows, so a call is that intercom if and
only if it arrived at that account. The caller's `From` header is recorded as a
claim and decides nothing: it is a string anybody can write, and the secret is
never in it — it is the number dialled. It then reads that lane's last decision
through the lane contract, says what happened in every language the site
declared, and when the case needs a person it calls one, stays in both calls, and
records the authorisation they key. **An authorisation is always a record of what
somebody said, and two of them are also acts**: `open now` and `open and flag`
reach `POST /v1/lane/vend` at a lane the site gave this agent an act token for.
**The lane decides** — it applies its own refusals to a human's completion
exactly as it does to a driver's, reading presence off its own loop at the moment
of the call. A driver's confirmed display code goes through the same one route.
Where a site declared no act token and no relay, the same code asks for nothing
and the person is told so.

**The malfunction monitor** watches whatever a site declares — a lane, an
identification service, a platform, a capture process — and tells a human what
changed. Gate broken, camera broken, no connection.

**The capture process** photographs the declared cameras every minute and every
time the lane vends, keeps what a per-site retention rule allows, and deletes the
rest. When a barrier is broken the camera's job changes from deciding to
recording; this is where the recording goes.

**The contract is [`docs/CONTRACT.md`](docs/CONTRACT.md).** Everything else here
is an implementation detail that may be rewritten. All three processes are
versioned under one `contract_version`.

```sh
pip install -e .
gate-agent agent   --config config/agent.example.toml
gate-agent monitor --config config/monitor.example.toml
gate-agent capture --config config/capture.example.toml
```

The agent drives an **external SIP user agent** — baresip, its own process,
over a local control socket. That is an install requirement rather than a
dependency: this package contains no SIP stack, checks the user agent's version
at startup, and refuses one it was not tested against.

## What they will not do

**The monitor and the capture process have no opening authority.** They read
`GET`s; one sends messages, one writes to its own disk. Neither calls a vend,
resolves a transit, or writes to a lane, and neither holds a client capable of a
method other than `GET` — swept out of the source, and observed at lanes and
cameras that record what arrived.

**The agent is the exception, and the exception is one module wide.** Exactly one
module may build a non-`GET` **at a lane** and it is `act.py`. It builds exactly
one request, `POST /v1/lane/vend`, to a path held in a constant, follows no
redirect, and cannot be constructed without an **act token** — so a lane a site
declared none for has no client at all rather than a client the lane would
refuse. The read token that learns where a vehicle was does not authorise it; the
lane refuses that with a `403`. The sweep that walks every module for a request
that is not a `GET` exempts two files by name — this one and the webhook sink
below — and a third is a change to that list, which somebody has to argue for.

**What none of them decides is whether a barrier moves.** The lane reads its own
loop at the moment of the call, applies its own malfunction table and its own
geometry, and answers — and it applies them to a human's completion exactly as
to a driver's. Nothing here checks any of that first, because a second copy of
those refusals is one that comes to disagree with the copy the barrier obeys.
And nothing in this estate has watched a boom move, so every sentence says
**asked**, never opened.

The one thing that leaves by another method is a **webhook**, which points at a
paging system and never at a lane. It lives in its own module, that module may
not import the target client, and the sweep enforces both.

**The agent never guesses which lane a call is about.** The mapping is per-site,
declared, and refused at startup if an intercom has no lane, a lane has no
intercom, or the user agent is not holding an intercom's account. A call at an
account nobody declared is refused **without being answered**, and no lane is
read. What that does NOT protect against is stated with the mechanism in the
contract: a secret in a device's configuration is only as private as that
device, and nothing here measures that.

**A driver is never told the wrong thing about why.** The case is derived from
what the lane published, never asked, and a reason this build does not recognise
reaches a person rather than being mapped onto the nearest thing we know. A dead
identification engine is never reported to a driver as a dirty number plate.

**It watches nothing silently.** A monitor with no target declared reports "all
fine", so it refuses to start and says why. A target it cannot reach is a
malfunction with a name. A code nobody measured stays `unknown` and is never read
as `ok`.

**It never pages on a code the wire marks `never_alarm`.** That flag travels in
the payload with the code, and this package holds no list of its own — two lists
drift, and the drift is a technician dispatched because a car arrived.

**Nothing it stores identifies a vehicle.** A capture record is the JPEG the
camera sent and seven fields saying when it was taken, by which camera, why, and
which lane event it answers by CURSOR. No plate, no plate region, no vehicle
attribute, and nothing from a lane event's `detail` — which is where a lane puts
what it knows. Swept over every route and every byte in the store, with a plate
planted in a lane event as the control.

**Nothing it keeps is kept for ever.** `[capture] retention_days`, published
default and bounds in the contract, and the purge DELETES: there is no foreign
key here and no money record, so the image is the datum and a rule that kept it
would not be one. Where the store goes and how big it may get are DECLARED — the
process refuses to start without them, because nothing in this package has ever
seen a capture from any of the cameras it is written for and a default would be a
disk budget it invented.

## It is an ordinary client of the lane contract

It reads a lane over
[`lane-controller/docs/CONTRACT.md`](https://github.com/openparking-ai/lane-controller/blob/main/docs/CONTRACT.md)
and **imports nothing from that package**. That is the same seat a third party
takes, and it is checked rather than intended: the suite reads a real lane and a
foreign one written from the document, through the same code, parametrised so
neither can be special-cased. If our own software had a private path, we would
stop feeling the contract's gaps — and nobody else would fix them.

**The foreign lane imports nothing of ours either**, which is newer than the
sentence above and is the half that was not true: it used to take the malfunction
codes and the never-alarm set from our Python package, because those were the two
things the lane contract withheld. That contract publishes them now, in full, and
the stub carries a literal copied from it with a test holding the copy to the
enum.

`openparking-lane-controller` is a **test** dependency, pinned to a commit, and
appears nowhere in `src/`.

### The test-only dependencies, and what each one is for

`dependencies = []` is the package's own property and it is not a convenience:
this runs beside a lane, on a box in a gate housing, and every dependency is one
more thing to cross-compile, patch and have go wrong somewhere with no keyboard
attached. Three things are installed to TEST it, none of them reaches `src/`,
and each is named with its licence and the job it does.

| | licence | what it is for |
|---|---|---|
| `pytest` | MIT | the suite |
| `ruff` | MIT | the lint job |
| `openparking-lane-controller` | AGPL-3.0-or-later | a REAL lane, served on a socket, beside a foreign one written from the document. A fake shaped like our lane would make "the same code reads ours and a third party's" a claim about two fakes. |
| `opencv-python-headless` | Apache-2.0 (OpenCV 4.5.0 and later; the project relicensed from BSD-3-Clause) | the INDEPENDENT DECODER for the QR encoder. An encoder nobody can read back is a picture of a QR code, so every symbol this package builds is read by something that is not this repository. Headless because a test runner has no display. |

**The audio's own licence rows are elsewhere and are not these**:
`src/gate_agent/audio/MANIFEST.json` carries one per file, naming the
synthesiser, its licence, and the provenance of the text — a build-time tool,
not a dependency of anything.

## What the monitor watches

| | |
|---|---|
| `lane` | Anything implementing the lane contract. Identity read once; health polled. |
| `identity_service` | A Vehicle ID `GET /v1/health`. Unauthenticated by that contract's own decision — it carries no plate and no image. |
| `platform` | The operator surface, for `lane_devices.last_seen_at`: a lane that has gone quiet is quiet, so the fault is only visible from the other end. |
| `capture` | The capture process. Its codes are in the lane's entry shape, so one reader serves both — and that is the path a dead camera takes to a human. |

Each is per-site declared and optional; **at least one is required**. Standalone
is a mode, not a smaller product.

## What the capture process photographs

Every camera it is given, on `[capture] interval_seconds`, and one snapshot per
camera on every `frames_captured` and every `vended` the lane records. It learns
those from `GET /v1/lane/events?since=` — the read contract, the seat a third
party takes — so **the lane does not know this process exists and does not have
to**. With no lane declared it takes interval captures only and says so when it
starts; a garage with a camera and no gate is a customer of this process.

That seat costs something, and the cost is measured rather than described: the
picture is taken when the event was SEEN, and every lane-triggered record carries
`capture_minus_lane_event_ms`. It is named for the subtraction it is, because it
spans this process's clock and the lane's — the contract says what it can and
cannot be read as.

**One camera implementation this version: an HTTP JPEG snapshot with standard
HTTP authentication.** No RTSP — a stream needs a decoder and this package has no
dependencies. **A camera whose only documented snapshot route takes the password
as a query parameter is named unsupported in the contract**, with the reason,
rather than made to work by putting a password in a URL. Of the two default-tier
cameras the lane's reference-hardware note names, that is the **Reolink
RLC-810A**: the only snapshot route its own documentation gives puts the password
in the query string, so this build does not support it. The **AXIS P1465-LE** is
supported — VAPIX authenticates in a header.

`[cameras.<id>] timeout_seconds` is a deadline on the whole read, not a socket
option, so one slow camera cannot hold the poller; and `[capture]
max_snapshot_bytes` — refused unless it is below `[capture] max_bytes` — is what
stops a camera deciding how much of a site's store survives.

## How a human is told

`log` (always on, needs nothing), `email` (SMTP, TLS by default), `webhook` (a
POST with a bearer token from a file — how a third party's paging system takes
the seat). The set is closed this version; SMS is a later round's provider.

**No sink has a default recipient.** A site with only `log` is valid and it means
nobody is paged; the process says so on the line it prints when it starts.

**A sink that cannot deliver is itself a monitor code**, reported on the health
route and to every other sink that works. Never wrong silently applies to the
messenger too.

## What it publishes

```
GET /v1/monitor                    who it is, what it watches, what it can tell
GET /v1/monitor/health             its own codes, and every target's, passed through
GET /v1/monitor/events?since=N     the notifications it sent

GET /v1/capture                    who it is, what it is set to do
GET /v1/capture/health             every capture code, and what is on the disk
GET /v1/capture/records?since=N    the sidecars, never the bytes
GET /v1/capture/images/<id>        one JPEG
```

All `GET`. Loopback by default; off loopback each refuses to start without a
credential, and every token is read from a **file**, never taken as a value. On
the capture surface the credential is required on **every** route including the
images: an image route left open "because it is just a JPEG" is the whole store
readable by anyone who can enumerate a record id.

**How much disk a site needs is a READ, on `GET /v1/capture/health`**, against
that site's own directory. This repository publishes no size, no rate and no
capacity anywhere, because nothing in it has measured one.

A target's codes are **passed through** — the state and the source that target
gave, unchanged. A monitor that restated a lane's health in its own words would
be a second copy of that lane's claim about itself, and the copy is the one that
comes to disagree.

## Tests

```sh
pip install -e '.[dev]'
pytest -q
python scripts/monitor_fail_control.py
python scripts/agent_fail_control.py
```

The last two are the point. Each breaks a property these processes exist to have
and requires the suite to go red on it — for the monitor and the capture process:
no opening authority, `unknown` never reading as `ok`, a plate never reaching the
store, a purge that actually deletes, a camera that is dead never reading as
fine; for the agent: an unmeasured presence never getting a ticket, a press never
confirming a code the driver was not shown, a second press never becoming a
second vend, a refusal always reaching the person, a relay never recorded as
pulsed when it was not. Every break is in the reassuring direction, because that
is the direction this kind of software fails in when nobody is looking. All three
run in CI, on 3.11 and 3.12, and a break that makes the suite ERROR rather than
FAIL is reported as a broken control rather than as a working one.

**There is no image file in this repository, and CI refuses one.** Every image in
the tests is synthetic and built in the process that uses it.

## Licence and contributing

AGPL-3.0-or-later. Contributions welcome; see
[CONTRIBUTING.md](CONTRIBUTING.md) and [CLA.md](CLA.md). The CLA is required and
it is not negotiable.

---

Built by 72 Knots Method by 72Knots.ai
