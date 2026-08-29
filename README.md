# Open Parking AI — gate agent

The intercom module. Its first process is the **malfunction monitor**: it watches
whatever a site declares — a lane, an identification service, a platform — and
tells a human what changed. Gate broken, camera broken, no connection.

The agent itself — the SIP endpoint that answers a driver at the barrier — joins
it in this repository later. It is the same module, and this is the half of it
that has to be right first, because a monitor that is wrong is a monitor nobody
believes.

**The contract is [`docs/CONTRACT.md`](docs/CONTRACT.md).** Everything else here
is an implementation detail that may be rewritten.

```sh
pip install -e .
gate-agent monitor --config config/monitor.example.toml
```

## What it will not do

**It has no opening authority.** It reads `GET`s and it sends messages. It never
calls a vend, never resolves a transit, never writes to a lane. There is no
client in this package capable of a method other than `GET` — swept out of the
source, and observed at lanes that record what arrived.

The one thing that leaves by another method is a **webhook**, which points at a
paging system and never at a lane. It lives in its own module, that module may
not import the target client, and the sweep enforces both.

**It watches nothing silently.** A monitor with no target declared reports "all
fine", so it refuses to start and says why. A target it cannot reach is a
malfunction with a name. A code nobody measured stays `unknown` and is never read
as `ok`.

**It never pages on a code the wire marks `never_alarm`.** That flag travels in
the payload with the code, and this package holds no list of its own — two lists
drift, and the drift is a technician dispatched because a car arrived.

## It is an ordinary client of the lane contract

It reads a lane over
[`lane-controller/docs/CONTRACT.md`](https://github.com/openparking-ai/lane-controller/blob/main/docs/CONTRACT.md)
and **imports nothing from that package**. That is the same seat a third party
takes, and it is checked rather than intended: the suite reads a real lane and a
foreign one written from the document, through the same code, parametrised so
neither can be special-cased. If our own software had a private path, we would
stop feeling the contract's gaps — and nobody else would fix them.

`openparking-lane-controller` is a **test** dependency, pinned to a commit, and
appears nowhere in `src/`.

## What it watches

| | |
|---|---|
| `lane` | Anything implementing the lane contract. Identity read once; health polled. |
| `identity_service` | A Vehicle ID `GET /v1/health`. Unauthenticated by that contract's own decision — it carries no plate and no image. |
| `platform` | The operator surface, for `lane_devices.last_seen_at`: a lane that has gone quiet is quiet, so the fault is only visible from the other end. |

Each is per-site declared and optional; **at least one is required**. Standalone
is a mode, not a smaller product.

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
GET /v1/monitor                 who it is, what it watches, what it can tell
GET /v1/monitor/health          its own codes, and every target's, passed through
GET /v1/monitor/events?since=N  the notifications it sent
```

All `GET`. Loopback by default; off loopback it refuses to start without a
credential, and every token is read from a **file**, never taken as a value.

A target's codes are **passed through** — the state and the source that target
gave, unchanged. A monitor that restated a lane's health in its own words would
be a second copy of that lane's claim about itself, and the copy is the one that
comes to disagree.

## Tests

```sh
pip install -e '.[dev]'
pytest -q
python scripts/monitor_fail_control.py
```

The second one is the point. It breaks each property this monitor exists to have
and requires the suite to go red on every one — every break in the reassuring
direction, because that is the direction a monitor fails in when nobody is
looking. Both run in CI.

## Licence and contributing

AGPL-3.0-or-later. Contributions welcome; see
[CONTRIBUTING.md](CONTRIBUTING.md) and [CLA.md](CLA.md). The CLA is required and
it is not negotiable.

---

Built by 72 Knots Method by 72Knots.ai
