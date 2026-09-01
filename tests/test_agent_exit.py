"""The screens go black on the way out, and a REAL process proves it.

`docs/CONTRACT.md` and `display.py` both published *"idle is a black frame, and
so is exit"*, and `display.py` said it happens *"in a `finally`"*. Nothing
anywhere blanked a screen on exit: there was no `finally` that did, and
`SIGTERM` -- which is how a service manager stops this -- never reached Python's
handler at all. So the ordinary case, `systemctl restart gate-agent` during a
package upgrade, left the last ticket on the screen, and by the contract's own
paragraph that code can never be vended. The document treated it as the crash
exception; it was what every restart did.

**This drives the REAL `cli.cmd_agent` in a REAL subprocess, against a REAL
framebuffer file**, and sends it a REAL `SIGTERM`. The only substitution is the
user agent, which is what `FakeUa` is for everywhere else in this suite: what is
being measured is the exit path, not baresip.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import DIAL_SECRET, secret_file, wav

ROOT = Path(__file__).resolve().parent.parent

#: What the framebuffer is filled with before the process starts, so "it was
#: blanked at startup" and "it was never touched" are different observations.
BEFORE = 0xAA
#: What the driver script writes onto it once the agent is up: a frame with
#: light pixels in it, which is what a ticket looks like to this file.
FRAME = 0xFF

SCRIPT = """
import os, signal, sys, threading, time

sys.path.insert(0, {src!r})
sys.path.insert(0, {tests!r})

from fake_ua import FakeUa
import gate_agent.cli as cli

fake = FakeUa()
fake.held_accounts = ("agent-" + {secret!r},)
cli.BaresipUa = lambda **kwargs: fake

FB = {framebuffer!r}
SIZE = {size!r}
BLANK = bytes(SIZE)


def when_it_is_up():
    # STARTUP blanked it, which is how this knows the agent is running.
    for _ in range(600):
        if open(FB, "rb").read() == BLANK:
            break
        time.sleep(0.02)
    # A ticket on the screen.
    with open(FB, "wb") as handle:
        handle.write(bytes([{frame}]) * SIZE)
    time.sleep(0.1)
    os.kill(os.getpid(), signal.SIGTERM)


{break_}
threading.Thread(target=when_it_is_up, daemon=True).start()
raise SystemExit(cli.main(["agent", "--config", {config!r}, "--port", "0"]))
"""


def a_framebuffer(tmp_path, width=64, height=32, depth=32):
    sysfs = tmp_path / "sys" / "fb0"
    sysfs.mkdir(parents=True, exist_ok=True)
    (sysfs / "virtual_size").write_text(f"{width},{height}\n", encoding="ascii")
    (sysfs / "bits_per_pixel").write_text(f"{depth}\n", encoding="ascii")
    device = tmp_path / "fb0"
    size = width * height * depth // 8
    device.write_bytes(bytes([BEFORE]) * size)
    return device, sysfs, size


def a_configuration(tmp_path, device, sysfs) -> Path:
    """A standalone door with a screen, as a site declares one."""
    key = secret_file(tmp_path / "tickets.key", "a-signing-key-long-enough-000000")
    text = f'''
[agent]
id = "agent-1"
site_id = "site-1"

[user_agent]
operator_aor = "sip:agent-operator@10.0.0.20"

[intercoms."sip:door1@10.0.0.9"]
lane = "none"
display = "front"
name_audio = "{wav(tmp_path / "door1.wav")}"
dial_secret_file = "{secret_file(tmp_path / "door1.dial-secret")}"

[displays.front]
framebuffer = "{device}"
sysfs = "{sysfs}"

[tickets]
signing_key_file = "{key}"
directory = "{tmp_path / "ticket-records"}"

[languages]
driver = ["en"]
operator = "en"

[authorisations]
open_now = true
do_not_open = true

[escalation]
human_sip_uri = "sip:duty@10.0.0.5"
'''
    path = tmp_path / "agent.toml"
    path.write_text(text, encoding="utf-8")
    return path


def run_until_sigterm(tmp_path, break_: str = "") -> bytes:
    """Start the real agent, put a frame up, `SIGTERM` it, read the file back."""
    device, sysfs, size = a_framebuffer(tmp_path)
    config = a_configuration(tmp_path, device, sysfs)
    script = SCRIPT.format(
        src=str(ROOT / "src"),
        tests=str(ROOT / "tests"),
        secret=DIAL_SECRET,
        framebuffer=str(device),
        size=size,
        frame=FRAME,
        config=str(config),
        break_=break_,
    )
    done = subprocess.run(
        # `-u` because a killed process's buffered stdout is lost, and one of
        # these runs is a process that gets killed.
        [sys.executable, "-u", "-c", script], capture_output=True, text=True, timeout=60
    )
    assert "gate-agent agent on http" in done.stdout, (done.stdout, done.stderr)
    return device.read_bytes()


@pytest.mark.skipif(os.name != "posix", reason="SIGTERM is a POSIX signal")
def test_sigterm_blanks_every_declared_screen(tmp_path):
    """A real process, a real framebuffer, a real `SIGTERM`."""
    after = run_until_sigterm(tmp_path)
    assert set(after) == {0}, f"the frame survived the exit: {sorted(set(after))}"


@pytest.mark.skipif(os.name != "posix", reason="SIGTERM is a POSIX signal")
def test_without_the_handler_the_frame_survives(tmp_path):
    """THE CONTROL, and it is the behaviour that shipped: with nothing raising
    into the `finally`, `SIGTERM` ends the process where it stands and the last
    ticket stays on the screen."""
    after = run_until_sigterm(tmp_path, break_="cli._raise_on_sigterm = lambda: None")
    assert set(after) == {FRAME}, (
        f"the control did not reproduce the defect: {sorted(set(after))}"
    )


@pytest.mark.skipif(os.name != "posix", reason="SIGTERM is a POSIX signal")
def test_without_the_blanking_the_frame_survives_too(tmp_path):
    """The other half of the control: the signal reaches the `finally` and the
    `finally` does not blank."""
    after = run_until_sigterm(tmp_path, break_="cli._blank_displays = lambda config: None")
    assert set(after) == {FRAME}, (
        f"the control did not reproduce the defect: {sorted(set(after))}"
    )


# ---------------------------------------------------------------------------
# The startup banner is DERIVED, not written down (Z16.2, 2026-09-01)
# ---------------------------------------------------------------------------


def _config_with(tmp_path, *, act_token=None, relay=None):
    """One agent config, with or without something that can move a barrier."""
    from dataclasses import replace

    from conftest import agent_config_for
    from gate_agent.config import Target
    from gate_agent.contract import TargetKind

    base = agent_config_for(tmp_path, lane_url="http://127.0.0.1:1/")
    lanes = (
        Target(
            name="entry",
            kind=TargetKind.LANE,
            url="http://127.0.0.1:1/",
            poll_seconds=2.0,
            act_token=act_token,
        ),
    )
    intercoms = (replace(base.intercoms[0], lane="entry", relay=relay),)
    return replace(base, lanes=lanes, intercoms=intercoms)


def test_the_startup_line_is_derived_from_what_this_process_can_act_on(tmp_path):
    """The line CHANGES when the act surface changes, and that is the guarantee.

    `cli.py` told every operator "OPENS NOTHING: no vend route here" at every
    start for the whole of round 7 -- the round that gave this package a vend
    route. A fixed sentence cannot go stale in a way anything measures, so this
    one is computed from the config the process loaded and this test is what
    measures it.
    """
    from gate_agent import cli

    nothing = cli.opening_line(_config_with(tmp_path))
    assert "OPENS NOTHING" in nothing
    assert "entry" not in nothing

    # A LANE THIS AGENT HOLDS AN ACT TOKEN FOR. The line must move, and it must
    # name the lane -- an operator reading the banner is asking WHICH barrier.
    vending = cli.opening_line(_config_with(tmp_path, act_token="an-act-token-0000"))
    assert vending != nothing, "the act surface changed and the line did not"
    assert "OPENS NOTHING" not in vending
    assert "vend at entry" in vending
    # And it says ASK, because whether the boom moves is the barrier's answer.
    assert "ASK" in vending.upper()


def test_the_startup_line_names_a_relay_a_standalone_site_declares(tmp_path):
    """The other half of the act surface: an intercom with its own relay."""
    from gate_agent import cli
    from gate_agent.relay import Relay

    relay = Relay(
        kind="axis_vapix",
        url="http://10.0.0.7/",
        port=1,
        pulse_ms=500,
        answer_margin_s=5.0,
        username="operator",
        password="secret",
    )
    line = cli.opening_line(_config_with(tmp_path, relay=relay))
    assert "OPENS NOTHING" not in line
    assert "pulse the relay at" in line and "sip:door1@10.0.0.9" in line
    # THE CREDENTIAL IS NOT IN IT. This line is printed at every start and goes
    # wherever a service manager's log goes.
    assert "secret" not in line and "operator" not in line
