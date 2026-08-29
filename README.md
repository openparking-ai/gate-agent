# Open Parking AI — gate agent

The intercom module. Its first process is the **malfunction monitor**: it watches
whatever a site declares — a lane, an identification service, a platform — and
tells a human what changed. Gate broken, camera broken, no connection. The agent
itself, the SIP endpoint that answers a driver at the barrier, joins it here
later.

**Nothing is on `main` yet but the licence and the gates.** The monitor is in the
first pull request, where it can be reviewed before it becomes the thing this
repository is. That is deliberate and it is how every module in this project
arrives.

## Licence and contributing

AGPL-3.0-or-later for the code. Contributions are welcome; see
[CONTRIBUTING.md](CONTRIBUTING.md) and [CLA.md](CLA.md). The CLA is required and
it is not negotiable — section 3 says why in plain terms.

Related: [lane-controller](https://github.com/openparking-ai/lane-controller) ·
[vehicle-id](https://github.com/openparking-ai/vehicle-id) ·
[platform](https://github.com/openparking-ai/platform) ·
[knowhow](https://github.com/openparking-ai/knowhow)

---

Built by 72 Knots Method by 72Knots.ai
