"""Every refusal the agent's configuration makes, and what each one prevents.

The rule this file exists to hold: **where no default is safe, the parameter is
DECLARED and startup refuses without it.** Almost nothing in an agent's
configuration has a safe default, because almost every value in it decides what
somebody at a barrier is told or what a person on a phone is allowed to say.

Each test is written from the failure it prevents rather than from the key it
checks, and the file is read through `AgentConfig.from_file` -- a TOML file on
disk, the way a site's is -- so the refusals are the ones an installer meets.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from conftest import DIAL_SECRET, secret_file, wav
from gate_agent.config import AgentConfig, ConfigError

ROOT = Path(__file__).resolve().parent.parent

BASE = """
[agent]
id = "agent-1"
site_id = "site-1"

[user_agent]
operator_aor = "sip:agent-operator@10.0.0.20"

[lanes.entry]
url = "http://127.0.0.1:8090"

[intercoms."sip:door1@10.0.0.9"]
lane = "entry"
name_audio = "NAME_AUDIO"
dial_secret_file = "DIAL_SECRET_FILE"

[languages]
driver = ["en"]
operator = "en"

[authorisations]
open_now = true
do_not_open = true

[escalation]
human_sip_uri = "sip:duty@10.0.0.5"
"""


def written(tmp_path, text: str = BASE, **replace):
    name_audio = wav(tmp_path / "door1.wav")
    text = text.replace("NAME_AUDIO", str(name_audio))
    text = text.replace(
        "DIAL_SECRET_FILE", str(secret_file(tmp_path / "door1.dial-secret"))
    )
    for old, new in replace.items():
        text = text.replace(old.replace("__", " "), new)
    path = tmp_path / "agent.toml"
    path.write_text(text, encoding="utf-8")
    return path


def refused(tmp_path, text, fragment: str):
    with pytest.raises(ConfigError) as raised:
        AgentConfig.from_file(written(tmp_path, text))
    assert fragment in str(raised.value), raised.value


def test_the_example_configuration_shape_loads(tmp_path):
    """The control for every refusal below: the base file is ACCEPTED.

    Without it, a test that asserts a mangled file is refused would pass on a
    build that refused every file there is.
    """
    config = AgentConfig.from_file(written(tmp_path))
    assert config.intercoms[0].lane == "entry"
    assert config.driver_languages == ("en",)
    assert len(config.authorisations) == 2


def test_an_intercom_with_no_lane_is_refused(tmp_path):
    """The agent never guesses which barrier somebody is standing at."""
    refused(tmp_path, BASE.replace('lane = "entry"\n', ""), "does not declare lane")


def test_an_intercom_pointing_at_a_lane_that_is_not_declared_is_refused(tmp_path):
    refused(tmp_path, BASE.replace('lane = "entry"', 'lane = "exit"'), "there is no [lanes.exit]")


def test_a_lane_with_no_intercom_is_refused(tmp_path):
    """Far more likely a mistyped URI than a deliberate spare lane -- and if it
    is a typo, every call at that door is answered `not configured`."""
    refused(
        tmp_path,
        BASE + '\n[lanes.exit]\nurl = "http://127.0.0.1:8091"\n',
        "has no intercom declared for it",
    )


def test_a_lane_may_not_be_called_none(tmp_path):
    """`none` is the word an intercom uses to say it has no lane."""
    refused(
        tmp_path,
        BASE.replace("[lanes.entry]", "[lanes.none]").replace('lane = "entry"', 'lane = "none"'),
        "is the word an intercom uses",
    )


def test_a_standalone_intercom_is_accepted(tmp_path):
    """And it is a MODE, not a degraded configuration."""
    text = BASE.replace('lane = "entry"', 'lane = "none"').replace(
        '[lanes.entry]\nurl = "http://127.0.0.1:8090"\n', ""
    )
    config = AgentConfig.from_file(written(tmp_path, text))
    assert config.intercoms[0].lane is None
    assert config.lanes == ()


def test_an_intercom_with_no_name_audio_is_refused(tmp_path):
    """The person on the phone has to be told which door they are needed at."""
    refused(tmp_path, BASE.replace("name_audio = \"NAME_AUDIO\"\n", ""),
            "does not declare name_audio")


def test_a_name_audio_that_is_not_there_is_refused(tmp_path):
    refused(tmp_path, BASE.replace("NAME_AUDIO", "/nowhere/door.wav"), "which is not a file")


def test_no_intercom_at_all_is_refused(tmp_path):
    text = BASE[: BASE.index("[intercoms.")] + BASE[BASE.index("[languages]") :]
    refused(tmp_path, text, "no intercom is declared")


def test_a_language_this_build_has_no_lines_for_is_refused(tmp_path):
    """A language declared with nothing behind it is a silence with a key on it."""
    refused(tmp_path, BASE.replace('driver = ["en"]', 'driver = ["en", "fr"]'),
            "which this build has no lines for")


def test_no_driver_language_is_refused(tmp_path):
    refused(tmp_path, BASE.replace('driver = ["en"]\n', ""), "does not declare driver")


def test_no_operator_language_is_refused(tmp_path):
    refused(tmp_path, BASE.replace('operator = "en"\n', ""), "does not declare operator")


def test_a_missing_audio_file_refuses_startup(tmp_path):
    """The sharpest one: drop ONE file and the agent will not run.

    Not skipped, not substituted, not played in another language. A driver at a
    barrier who hears silence has been told nothing and cannot tell it from a
    dead intercom.
    """
    import shutil

    from conftest import _shipped_audio
    from gate_agent.lines import audio_name

    directory = tmp_path / "audio"
    shutil.copytree(_shipped_audio(), directory)
    victim = directory / audio_name("case.plate_unclear", "en")
    assert victim.is_file(), "the control file is not where this test thinks it is"
    victim.unlink()
    refused(
        tmp_path,
        BASE.replace('[agent]\nid = "agent-1"',
                     f'[agent]\naudio_directory = "{directory}"\nid = "agent-1"'),
        "no audio for 1 line(s)",
    )
    # THE CONTROL: the same directory with the file restored is accepted, so the
    # refusal is about the missing file and not about the directory.
    shutil.copy(_shipped_audio() / audio_name("case.plate_unclear", "en"), victim)
    AgentConfig.from_file(
        written(
            tmp_path,
            BASE.replace('[agent]\nid = "agent-1"',
                         f'[agent]\naudio_directory = "{directory}"\nid = "agent-1"'),
        )
    )


def test_a_site_that_enables_no_authorisation_is_refused(tmp_path):
    """A person called at three in the morning who can authorise nothing."""
    refused(
        tmp_path,
        BASE.replace("open_now = true\ndo_not_open = true", "open_now = false"),
        "enables nothing",
    )


def test_an_authorisation_this_version_does_not_have_is_refused(tmp_path):
    refused(tmp_path, BASE.replace("open_now = true", "open_sesame = true"),
            "is not an authorisation")


def test_a_non_boolean_authorisation_is_refused(tmp_path):
    """A string that happens to be truthy is not a decision anybody made."""
    refused(tmp_path, BASE.replace("open_now = true", 'open_now = "yes"'), "must be true or false")


def test_transfer_without_a_uri_is_refused_rather_than_quietly_not_offered(tmp_path):
    """Silently dropping an option a site switched on is the quiet half of the
    same failure. It is refused, loudly, at startup."""
    refused(tmp_path, BASE.replace("open_now = true", "transfer = true"),
            "declares no transfer_sip_uri")


def test_no_human_to_call_is_refused(tmp_path):
    refused(tmp_path, BASE.replace('human_sip_uri = "sip:duty@10.0.0.5"\n', ""),
            "does not declare human_sip_uri")


def test_one_account_for_both_legs_is_refused(tmp_path):
    """Two calls on one account cannot be told apart by the user agent, so the
    menu meant for the person on the phone plays to the driver.

    The old shape of this was `driver_aor == operator_aor`. There is no driver
    account any more -- each intercom has its own -- so the same guarantee is
    re-proved in its new form: the outbound account may not be an intercom's.
    """
    refused(
        tmp_path,
        BASE.replace('operator_aor = "sip:agent-operator@10.0.0.20"',
                     f'operator_aor = "sip:agent-{DIAL_SECRET}@10.0.0.20"'),
        "the same as [user_agent].operator_aor",
    )


def test_a_missing_account_is_refused(tmp_path):
    refused(tmp_path, BASE.replace('operator_aor = "sip:agent-operator@10.0.0.20"\n', ""),
            "does not declare operator_aor")


def test_a_credential_as_a_value_is_refused_anywhere_in_the_file(tmp_path):
    """The same walk the other two processes make, over this file too."""
    refused(tmp_path, BASE + '\n[extra]\ntoken = "s3cret"\n', "would hold a credential")


def test_a_credential_in_a_lane_url_is_refused(tmp_path):
    refused(
        tmp_path,
        BASE.replace("http://127.0.0.1:8090", "http://ops:S3CRET@127.0.0.1:8090"),
        "userinfo in URL",
    )


def test_a_credential_in_a_sip_uri_is_refused(tmp_path):
    refused(
        tmp_path,
        BASE.replace("sip:duty@10.0.0.5", "sip:duty:S3CRET@10.0.0.5"),
        "userinfo in URL",
    )


# ---------------------------------------------------------------------------
# The round-5 cut: what the SITE's own audio file has to be
# ---------------------------------------------------------------------------


def test_a_name_audio_at_the_wrong_sample_rate_is_refused(tmp_path):
    """`_measure_audio` checks the rate it SAYS it checks.

    It named "8 kHz, mono, 16-bit" and compared channels and width only, so a
    44.1 kHz `name_audio` started -- and `name_audio` is the ONE file in this
    configuration that this package does not produce, which makes it exactly
    the one that would be at the wrong rate.
    """
    import pytest

    from conftest import agent_config_for, agent_for
    from gate_agent.agent import AudioMissing

    # THE CONTROL, first: at the rate a narrowband call carries, it starts.
    agent_for(agent_config_for(tmp_path, standalone=True, name_audio_rate=8000))

    with pytest.raises(AudioMissing) as raised:
        agent_for(agent_config_for(tmp_path, standalone=True, name_audio_rate=44100))
    assert "44100" in str(raised.value) and "8000" in str(raised.value)


def test_a_name_audio_longer_than_the_site_allows_is_refused(tmp_path):
    """`[speech] name_audio_max_seconds`, and an unbounded one used to start.

    The person's briefing waits for the whole of it and the driver at the
    barrier waits for the briefing, so an unbounded one holds somebody in a
    never-bridged call for as long as the file lasts. Measured on the build this
    replaces: a 200 MB WAV started, and its duration read as 12500 seconds.
    """
    import pytest

    from conftest import agent_config_for, agent_for
    from gate_agent.agent import AudioMissing

    # THE CONTROL: inside the bound, it starts.
    agent_for(
        agent_config_for(
            tmp_path, standalone=True, name_audio_seconds=4.0, name_audio_max_seconds=5.0
        )
    )

    with pytest.raises(AudioMissing) as raised:
        agent_for(
            agent_config_for(
                tmp_path, standalone=True, name_audio_seconds=9.0, name_audio_max_seconds=5.0
            )
        )
    assert "name_audio_max_seconds" in str(raised.value)


def test_the_three_new_settings_are_read_and_published(tmp_path):
    """`[cases]`, `[speech]` and `[user_agent] reconnect_seconds`, per site.

    Each with a published default, and each on `GET /v1/agent` -- a consumer
    cannot read the case table without knowing the age bound, and one reading
    `ua_unreachable` is entitled to know whether the agent is trying to come
    back. Read through a TOML file on disk, the way a site's is.
    """
    from gate_agent.cases import DEFAULT_DECISION_MAX_AGE_SECONDS
    from gate_agent.config import (
        DEFAULT_LINE_TIMEOUT_SECONDS,
        DEFAULT_NAME_AUDIO_MAX_SECONDS,
        DEFAULT_RECONNECT_SECONDS,
    )

    config = AgentConfig.from_file(written(tmp_path))
    assert config.decision_max_age_seconds == DEFAULT_DECISION_MAX_AGE_SECONDS
    assert config.line_timeout_seconds == DEFAULT_LINE_TIMEOUT_SECONDS
    assert config.name_audio_max_seconds == DEFAULT_NAME_AUDIO_MAX_SECONDS
    assert config.user_agent.reconnect_seconds == DEFAULT_RECONNECT_SECONDS

    declared = BASE + """
[cases]
decision_max_age_seconds = 45.0

[speech]
line_timeout_seconds = 3.0
name_audio_max_seconds = 2.0
"""
    declared = declared.replace(
        'operator_aor = "sip:agent-operator@10.0.0.20"',
        'operator_aor = "sip:agent-operator@10.0.0.20"\nreconnect_seconds = 9.0',
    )
    config = AgentConfig.from_file(written(tmp_path, declared))
    assert config.decision_max_age_seconds == 45.0
    assert config.line_timeout_seconds == 3.0
    assert config.name_audio_max_seconds == 2.0
    assert config.user_agent.reconnect_seconds == 9.0

    # And each one is REFUSED when it is not a positive number, by name -- the
    # rule every other setting in this file is held to.
    for table, key in (
        ("[cases]", "decision_max_age_seconds"),
        ("[speech]", "line_timeout_seconds"),
        ("[speech]", "name_audio_max_seconds"),
    ):
        refused(tmp_path, BASE + f"\n{table}\n{key} = -1\n", key)


# ---------------------------------------------------------------------------
# X2': the intercom is identified by the address it dialled
# ---------------------------------------------------------------------------


def test_an_intercom_with_no_dial_secret_is_refused(tmp_path):
    """There is no default and there cannot be one.

    The secret IS the identity. Without it the only thing left to route on is
    the `From` header, and a `From` header is a string the caller writes -- the
    exact defect this round exists to remove. A defaulted one would be a
    published address every installation shares.
    """
    refused(tmp_path, BASE.replace("dial_secret_file = \"DIAL_SECRET_FILE\"\n", ""),
            "does not declare dial_secret_file")


def test_a_dial_secret_by_value_is_refused(tmp_path):
    """The same rule as every other credential in this file: a path, never a value.

    A value here is that secret in this file, in every backup of it, and in
    everything anybody pastes it into.
    """
    refused(
        tmp_path,
        BASE.replace('dial_secret_file = "DIAL_SECRET_FILE"',
                     'dial_secret = "test-only-dial-secret-0000"'),
        "would hold a credential",
    )


def test_a_world_readable_dial_secret_is_refused(tmp_path):
    """Anybody who can read the file can call as that door.

    That is a person dispatched to a barrier nobody is standing at, from any
    account on the box.
    """
    path = secret_file(tmp_path / "loose.dial-secret")
    path.chmod(0o644)
    with pytest.raises(ConfigError) as raised:
        AgentConfig.from_file(
            written(tmp_path, BASE.replace("DIAL_SECRET_FILE", str(path)))
        )
    assert "readable by more than its owner" in str(raised.value)

    # THE CONTROL: the same file at 0600 is accepted, so the refusal is about
    # the permissions and not about the path.
    path.chmod(0o600)
    assert AgentConfig.from_file(
        written(tmp_path, BASE.replace("DIAL_SECRET_FILE", str(path)))
    ).intercoms[0].account_user.startswith("agent-")


def test_a_dial_secret_short_enough_to_have_been_typed_is_refused(tmp_path):
    """The floor is a choice this package made, and the message says so.

    What makes an address unguessable is that it was generated at RANDOM, and
    nothing here can see that. What this refuses is the case that needs no
    measurement.
    """
    from gate_agent.config import MINIMUM_DIAL_SECRET

    path = secret_file(tmp_path / "short.dial-secret", "a" * (MINIMUM_DIAL_SECRET - 1))
    with pytest.raises(ConfigError) as raised:
        AgentConfig.from_file(
            written(tmp_path, BASE.replace("DIAL_SECRET_FILE", str(path)))
        )
    assert f"shorter than {MINIMUM_DIAL_SECRET}" in str(raised.value)

    # THE CONTROL: one character longer is accepted.
    path = secret_file(tmp_path / "long.dial-secret", "a" * MINIMUM_DIAL_SECRET)
    assert AgentConfig.from_file(
        written(tmp_path, BASE.replace("DIAL_SECRET_FILE", str(path)))
    ).intercoms[0].account_user == "agent-" + "a" * MINIMUM_DIAL_SECRET


def test_a_dial_secret_that_is_not_a_sip_user_part_is_refused(tmp_path):
    """A `;` starts a parameter and an `@` ends the user part.

    An address that means something else is an intercom that can never call in,
    which is a door that silently does nothing.
    """
    for bad in ("secret;with-a-parameter", "secret@with-a-host", "secret with a space"):
        path = secret_file(tmp_path / "odd.dial-secret", bad)
        with pytest.raises(ConfigError) as raised:
            AgentConfig.from_file(
                written(tmp_path, BASE.replace("DIAL_SECRET_FILE", str(path)))
            )
        assert "not letters, digits" in str(raised.value), bad


def test_an_empty_dial_secret_file_is_refused(tmp_path):
    """A truncated file is not "no secret configured"."""
    path = secret_file(tmp_path / "empty.dial-secret", "")
    with pytest.raises(ConfigError) as raised:
        AgentConfig.from_file(
            written(tmp_path, BASE.replace("DIAL_SECRET_FILE", str(path)))
        )
    assert "holds no credential" in str(raised.value)


def test_two_intercoms_sharing_a_dial_secret_are_refused(tmp_path):
    """The secret IS the identity, so two doors sharing one have one identity.

    Every call at either would be answered as whichever this agent read first,
    and the lane a person is told about would not be the lane they are at.
    """
    second = secret_file(tmp_path / "door2.dial-secret", DIAL_SECRET)
    text = BASE + f"""
[intercoms."sip:door2@10.0.0.10"]
lane = "none"
name_audio = "NAME_AUDIO"
dial_secret_file = "{second}"
"""
    refused(tmp_path, text, "have the same dial_secret")

    # THE CONTROL: a DIFFERENT secret at the same second door is accepted.
    other = secret_file(tmp_path / "door2b.dial-secret", "test-only-dial-secret-2222")
    config = AgentConfig.from_file(
        written(tmp_path, text.replace(str(second), str(other)))
    )
    assert len(config.intercoms) == 2
    assert len({one.account_user for one in config.intercoms}) == 2


# ---------------------------------------------------------------------------
# [tickets] — required exactly where the agent can offer or command anything
# ---------------------------------------------------------------------------

TICKETS = """
[tickets]
signing_key_file = "SIGNING_KEY_FILE"
directory = "TICKET_DIRECTORY"
"""


def with_tickets(tmp_path, text: str = BASE, key: str = "a-signing-key-long-enough-000000") -> str:
    """`BASE` plus a `[tickets]` section with a real key file at `0600`."""
    path = secret_file(tmp_path / "tickets.key", key)
    return (text + TICKETS).replace("SIGNING_KEY_FILE", str(path)).replace(
        "TICKET_DIRECTORY", str(tmp_path / "ticket-records")
    )


def with_act_token(tmp_path, text: str) -> str:
    token = secret_file(tmp_path / "lane.act-token", "an-act-token-for-a-test")
    return text.replace(
        'url = "http://127.0.0.1:8090"',
        f'url = "http://127.0.0.1:8090"\nact_token_file = "{token}"',
    )


def test_an_agent_with_no_display_and_no_act_token_needs_no_tickets_section(tmp_path):
    """Round 5 exactly, and it is a supported configuration.

    An agent that can neither offer a ticket nor command a vend has nothing to
    sign. Requiring a key there would be a site generating a credential to
    satisfy a file rather than to protect anything.
    """
    config = AgentConfig.from_file(written(tmp_path))
    assert config.tickets is None
    assert config.can_vend_at == ()


def test_a_lane_with_an_act_token_and_no_tickets_section_is_refused_by_name(tmp_path):
    """And the refusal says WHICH declaration made it necessary.

    A site told "you need [tickets]" has to work out why; a site told
    "[lanes.entry] declares act_token_file" does not.
    """
    refused(tmp_path, with_act_token(tmp_path, BASE), "[tickets] is not declared")
    refused(tmp_path, with_act_token(tmp_path, BASE), "[lanes.entry] declares act_token_file")

    # THE CONTROL: the same file WITH the section is accepted, so the refusal is
    # about the missing section and not about the act token.
    both = with_act_token(tmp_path, with_tickets(tmp_path))
    config = AgentConfig.from_file(written(tmp_path, both))
    assert config.can_vend_at == ("entry",)
    assert config.lane("entry").can_act is True


def test_a_signing_key_short_enough_to_have_been_typed_is_refused(tmp_path):
    """A FLOOR THIS REPOSITORY CHOSE, and the message says so.

    What makes a key unguessable is that it was generated at random, which
    nothing here can see from a file's contents. What this refuses is the one
    case needing no measurement.
    """
    refused(tmp_path, with_tickets(tmp_path, key="too-short"), "refuses anything shorter")
    # THE CONTROL: one character over the floor is accepted.
    assert AgentConfig.from_file(
        written(tmp_path, with_tickets(tmp_path, key="k" * 32))
    ).tickets is not None


def test_tickets_with_no_directory_is_refused(tmp_path):
    """There is no default: it is the one place on this box a `ticket_ref` is
    written down, and a default would put a record of every arrival somewhere
    nobody chose."""
    text = with_tickets(tmp_path)
    refused(tmp_path, text.replace(f'directory = "{tmp_path / "ticket-records"}"\n', ""),
            "does not declare directory")


def test_a_signing_key_as_a_VALUE_is_refused_by_the_credential_sweep(tmp_path):
    """`signing_key = "..."` is a credential in a configuration file, which is a
    credential in every backup of it. The refusal is the one every credential
    key already gets, by name."""
    refused(tmp_path, BASE + '\n[tickets]\nsigning_key = "abc"\n', "signing_key")


def test_the_settings_have_the_published_defaults(tmp_path):
    """Published in `docs/CONTRACT.md`, and read from the code that defines them."""
    from gate_agent.tickets import (
        DEFAULT_CONFIRM_WINDOW_S,
        DEFAULT_HELP_WINDOW_S,
        DEFAULT_RETENTION_DAYS,
    )

    tickets = AgentConfig.from_file(written(tmp_path, with_tickets(tmp_path))).tickets
    assert tickets.retention_days == DEFAULT_RETENTION_DAYS == 30
    assert tickets.confirm_window_s == DEFAULT_CONFIRM_WINDOW_S == 90.0
    assert tickets.help_window_s == DEFAULT_HELP_WINDOW_S == 60.0


def test_the_signing_key_is_not_in_the_repr_of_the_settings(tmp_path):
    """The generated `__repr__` would put the key every ticket at this site is
    signed with into every log line, traceback and test failure that touches a
    configuration. The intercom's dial secret has the same guard."""
    tickets = AgentConfig.from_file(
        written(tmp_path, with_tickets(tmp_path, key="PLANTEDKEY" + "0" * 30))
    ).tickets
    assert "PLANTEDKEY" not in repr(tickets)
    assert "PLANTEDKEY" not in repr(AgentConfig.from_file(
        written(tmp_path, with_tickets(tmp_path, key="PLANTEDKEY" + "0" * 30))
    ))
    # THE CONTROL: the key really is the one loaded, so the absence above is
    # about the repr and not about a key that never arrived.
    assert tickets.signing_key.startswith(b"PLANTEDKEY")


# ---------------------------------------------------------------------------
# [displays] and [intercoms.<uri>] display
# ---------------------------------------------------------------------------


def a_display(tmp_path, width=800, height=480, depth=32):
    """A framebuffer and its sysfs, as a driver presents them."""
    sysfs = tmp_path / "sys" / "fb0"
    sysfs.mkdir(parents=True, exist_ok=True)
    (sysfs / "virtual_size").write_text(f"{width},{height}\n", encoding="ascii")
    (sysfs / "bits_per_pixel").write_text(f"{depth}\n", encoding="ascii")
    device = tmp_path / "fb0"
    device.write_bytes(b"")
    return device, sysfs


def with_display(tmp_path, text: str, **screen) -> str:
    device, sysfs = a_display(tmp_path, **screen)
    return (
        text.replace('lane = "entry"', 'lane = "entry"\ndisplay = "front"')
        + f'\n[displays.front]\nframebuffer = "{device}"\nsysfs = "{sysfs}"\n'
    )


def test_a_declared_display_is_opened_and_its_geometry_read_at_startup(tmp_path):
    """Read, never configured. A site that typed its own resolution would be a
    site whose display is silently wrong the day somebody changes a cable."""
    config = AgentConfig.from_file(
        written(tmp_path, with_display(tmp_path, with_tickets(tmp_path), width=1024, height=600))
    )
    assert set(config.displays) == {"front"}
    assert (config.displays["front"].geometry.width,
            config.displays["front"].geometry.height) == (1024, 600)
    assert config.intercoms[0].display == "front"


def test_an_intercom_with_no_display_is_a_supported_configuration(tmp_path):
    """Round 5 exactly: its cases go to a human and it offers no ticket."""
    config = AgentConfig.from_file(written(tmp_path))
    assert config.displays == {}
    assert config.intercoms[0].display is None


def test_an_intercom_pointing_at_a_display_nobody_declared_is_refused(tmp_path):
    """It would publish `has_display` and show a driver nothing."""
    text = with_display(tmp_path, with_tickets(tmp_path)).replace(
        'display = "front"', 'display = "side"'
    )
    refused(tmp_path, text, "there is no [displays.side]")


def test_a_display_with_a_depth_this_build_cannot_write_is_refused_at_startup(tmp_path):
    """Not at the first arrival. An installer is standing there now; a driver
    will not be until three in the morning."""
    refused(
        tmp_path,
        with_display(tmp_path, with_tickets(tmp_path), depth=8),
        "bits per pixel",
    )


def test_a_display_whose_geometry_cannot_be_read_is_refused_naming_the_file(tmp_path):
    text = with_display(tmp_path, with_tickets(tmp_path))
    (tmp_path / "sys" / "fb0" / "virtual_size").unlink()
    refused(tmp_path, text, "virtual_size")


def test_a_display_with_no_framebuffer_declared_is_refused(tmp_path):
    """No default: a guessed device is a frame written to whatever else is on
    that box."""
    text = with_display(tmp_path, with_tickets(tmp_path))
    device_line = [one for one in text.splitlines() if one.startswith("framebuffer")][0]
    refused(tmp_path, text.replace(device_line + "\n", ""), "does not declare framebuffer")


def test_a_display_makes_the_tickets_section_required_and_says_which_intercom(tmp_path):
    """A display is a thing that SHOWS a ticket, so a site with one needs a key
    to sign them with -- and the refusal names the declaration that did it."""
    refused(tmp_path, with_display(tmp_path, BASE), "[tickets] is not declared")
    refused(tmp_path, with_display(tmp_path, BASE), "declares display")


def test_a_language_whose_display_line_this_build_lacks_is_refused(tmp_path, monkeypatch):
    """A driver shown a code with no instruction under it in their language.

    Refused at STARTUP rather than drawn as a blank: a blank is a driver told
    nothing.
    """
    from gate_agent import lines

    monkeypatch.setitem(lines.DISPLAY_TEXT["display.instruction"], "es-ES", "")
    text = with_display(tmp_path, with_tickets(tmp_path)).replace(
        'driver = ["en"]', 'driver = ["en", "es-ES"]'
    )
    refused(tmp_path, text, "no display text for")


def test_a_display_line_the_font_cannot_draw_is_refused(tmp_path, monkeypatch):
    """Never a blank and never a substitution. A character with no glyph would be
    a HOLE in the frame at three in the morning."""
    from gate_agent import lines

    monkeypatch.setitem(
        lines.DISPLAY_TEXT["display.instruction"], "en", "take a photo of the code"
    )
    refused(tmp_path, with_display(tmp_path, with_tickets(tmp_path)), "cannot draw")


def test_a_site_with_no_display_is_not_asked_for_display_words(tmp_path, monkeypatch):
    """THE CONTROL for the two refusals above: they fire only where something
    will be drawn. A site running round 5's agent is not refused for a language
    whose display line this package has not written."""
    from gate_agent import lines

    monkeypatch.setitem(lines.DISPLAY_TEXT["display.instruction"], "en", "")
    assert AgentConfig.from_file(written(tmp_path)) is not None


# ---------------------------------------------------------------------------
# ONE DISPLAY PER LANE, AND THE FIELDS A TICKET IS MADE OF
# ---------------------------------------------------------------------------


def a_second_door(text: str, display: str | None) -> str:
    """A second intercom on the SAME lane, with or without a screen of its own."""
    from conftest import OTHER_SECRET

    return text + (
        '\n[intercoms."sip:door2@10.0.0.9"]\n'
        'lane = "entry"\n'
        'name_audio = "NAME_AUDIO"\n'
        'dial_secret_file = "SECOND_SECRET"\n'
        + (f'display = "{display}"\n' if display else "")
    ).replace("OTHER_SECRET", OTHER_SECRET)


def written_two_doors(tmp_path, text: str):
    from conftest import OTHER_SECRET
    from conftest import secret_file as _secret_file

    second = _secret_file(tmp_path / "door2.dial-secret", OTHER_SECRET)
    return written(tmp_path, text.replace("SECOND_SECRET", str(second)))


def test_two_intercoms_with_displays_on_one_lane_are_refused_naming_both(tmp_path):
    """B15. A lane has ONE pending ticket, and every screen at it was shown it.

    So one code stood on two door stations and a press at EITHER confirmed it
    and opened the barrier -- whoever photographed the second screen was holding
    the first driver's ticket. The design's whole binding is that a press proves
    somebody at THAT barrier pressed; with two, it proved somebody at one of
    these barriers did.
    """
    text = with_display(tmp_path, with_tickets(tmp_path))
    text = a_second_door(text, "front")
    with pytest.raises(ConfigError) as raised:
        AgentConfig.from_file(written_two_doors(tmp_path, text))
    assert "sip:door1@10.0.0.9" in str(raised.value)
    assert "sip:door2@10.0.0.9" in str(raised.value)


def test_a_second_door_at_that_lane_with_no_display_is_accepted(tmp_path):
    """THE CONTROL, and it is the ordinary two-door lane: the ticket is bound to
    the one screen that shows it, and a press at the other door is round 5."""
    text = a_second_door(with_display(tmp_path, with_tickets(tmp_path)), None)
    config = AgentConfig.from_file(written_two_doors(tmp_path, text))
    assert [one.display for one in config.intercoms] == ["front", None]


@pytest.mark.parametrize(
    ("before", "after"),
    [
        ('site_id = "site-1"', r'site_id = "site\nwith-a-newline"'),
        ("[lanes.entry]", r'[lanes."entry\nnewline"]'),
        (
            '[intercoms."sip:door1@10.0.0.9"]',
            r'[intercoms."sip:door1\n@10.0.0.9"]',
        ),
    ],
)
def test_a_value_that_cannot_be_a_ticket_field_is_refused_at_startup(
    tmp_path, before, after
):
    """B16. `Ticket.canonical()` refuses a field holding the separator, and
    nothing checked the CONFIGURATION.

    So a site named with a newline started, published a healthy surface, and
    refused its FIRST MINT at three in the morning: `BadTicket` out of `_issue`,
    through `_consider_ticket` and `_follow_lanes`, into `poll()`'s blanket
    handler -- a traceback per arrival, no ticket ever issued, `_advance()`
    skipped for that poll, and nothing on the health surface saying why. The
    same class as the font check, which IS a startup refusal.

    The three values are the three that end up in a ticket: `site`, `lane`, and
    -- for a standalone door, whose ticket names the door because there is no
    lane -- the intercom's URI.
    """
    text = with_display(tmp_path, with_tickets(tmp_path))
    if before == "[lanes.entry]":
        text = text.replace('lane = "entry"', r'lane = "entry\nnewline"')
    text = text.replace(before, after)
    with pytest.raises(ConfigError) as raised:
        AgentConfig.from_file(written(tmp_path, text))
    assert "separator" in str(raised.value), raised.value
    assert "Refusing to start" in str(raised.value)


def test_the_control_is_that_the_same_file_without_the_newline_loads(tmp_path):
    """Every refusal above is about the newline and not about the file."""
    config = AgentConfig.from_file(
        written(tmp_path, with_display(tmp_path, with_tickets(tmp_path)))
    )
    assert config.site_id == "site-1"


# ---------------------------------------------------------------------------
# THE RELAY'S TWO NUMBERS
# ---------------------------------------------------------------------------


def with_relay(tmp_path, pulse_ms=500, margin=None) -> str:
    """A STANDALONE door with a relay, as a site declares one."""
    credentials = tmp_path / "relay.auth"
    credentials.write_text("root:s3cret\n", encoding="utf-8")
    credentials.chmod(0o600)
    text = (
        BASE.replace("[lanes.entry]\nurl = \"http://127.0.0.1:8090\"\n", "")
        .replace('lane = "entry"', 'lane = "none"')
    )
    return with_tickets(tmp_path, text) + (
        "\n[intercoms.\"sip:door1@10.0.0.9\".relay]\n"
        'kind = "axis_vapix"\n'
        f'url = "http://10.0.0.9"\n'
        "port = 1\n"
        f"pulse_ms = {pulse_ms}\n"
        + (f"answer_margin_s = {margin}\n" if margin is not None else "")
        + f'credentials_file = "{credentials}"\n'
    )


@pytest.mark.parametrize("pulse_ms", [0, -1, 10_001, 60_000])
def test_a_pulse_outside_the_published_bounds_is_refused(tmp_path, pulse_ms):
    """B13's first half. It accepted any positive integer, and the HTTP timeout
    is derived from it -- so an unbounded pulse is an unbounded time for which
    this process holds a connection open for one press."""
    with pytest.raises(ConfigError) as raised:
        AgentConfig.from_file(written(tmp_path, with_relay(tmp_path, pulse_ms=pulse_ms)))
    assert "pulse_ms" in str(raised.value)


@pytest.mark.parametrize("pulse_ms", [1, 500, 6000, 10_000])
def test_a_pulse_inside_them_is_accepted_and_the_timeout_follows_it(tmp_path, pulse_ms):
    """THE CONTROL on both ends, and the derivation measured on the value."""
    from gate_agent.relay import DEFAULT_ANSWER_MARGIN_S

    config = AgentConfig.from_file(written(tmp_path, with_relay(tmp_path, pulse_ms=pulse_ms)))
    relay = config.intercoms[0].relay
    assert relay.pulse_ms == pulse_ms
    assert relay.answer_margin_s == DEFAULT_ANSWER_MARGIN_S
    assert relay.timeout == pulse_ms / 1000 + DEFAULT_ANSWER_MARGIN_S


def test_the_answer_margin_is_a_published_default_a_site_can_move(tmp_path):
    """It is an ASSUMPTION -- nothing has driven a real unit -- so it is a
    setting with a default rather than a constant, like every other number in
    this configuration that nobody has measured."""
    config = AgentConfig.from_file(
        written(tmp_path, with_relay(tmp_path, pulse_ms=6000, margin=2.5))
    )
    assert config.intercoms[0].relay.answer_margin_s == 2.5
    assert config.intercoms[0].relay.timeout == 8.5


def test_the_example_config_parses_and_declares_round_sevens_act_surface():
    """The file an installer copies, checked (Z16.2, 2026-09-01).

    `config/agent.example.toml` spent the whole of round 7 saying *"There is no
    vend route in this package"* -- the round that added one -- and declaring
    none of the surface below. Nothing read it, so nothing could notice.

    **The key list here is a CHECKLIST and not a derivation**, and it is written
    down as such rather than dressed up: there is no single place in
    `config.py` to derive "the keys round 7 added" from, and a test that
    pretended otherwise would be the second copy this repository keeps banning.
    What it buys is that the installer-facing file cannot lose a key silently;
    what parses it for real is `AgentConfig.from_file`, tested above against
    files these tests write.
    """
    import tomllib

    from gate_agent.tickets import (
        DEFAULT_CONFIRM_WINDOW_S,
        DEFAULT_HELP_WINDOW_S,
        DEFAULT_RETENTION_DAYS,
    )

    example = ROOT / "config" / "agent.example.toml"
    raw = tomllib.loads(example.read_text(encoding="utf-8"))

    assert "tickets" in raw, "[tickets] is required the moment a display or an act token is"
    assert "signing_key_file" in raw["tickets"]
    assert "directory" in raw["tickets"]
    assert "displays" in raw and raw["displays"], "no display is declared"
    assert any("display" in one for one in raw["intercoms"].values()), (
        "no intercom points at a display, so no door in this example offers a ticket"
    )
    assert any("act_token_file" in one for one in raw["lanes"].values()), (
        "no lane declares an act token, so this example can vend nowhere"
    )
    relays = [one["relay"] for one in raw["intercoms"].values() if "relay" in one]
    assert relays, "no intercom declares a relay"
    for relay in relays:
        assert set(relay) >= {"kind", "url", "port", "pulse_ms", "credentials_file"}

    # AND IT NO LONGER SAYS THE OPPOSITE OF WHAT THE CODE DOES.
    text = example.read_text(encoding="utf-8")
    assert "There is no vend route in this package" not in text

    # The DEFAULTS it quotes in comments are the code's own. A commented default
    # that has drifted is worse than none: an installer reads it and plans
    # around a number nothing uses.
    for value in (DEFAULT_RETENTION_DAYS, DEFAULT_CONFIRM_WINDOW_S, DEFAULT_HELP_WINDOW_S):
        assert str(value) in text, f"the example does not quote the published default {value}"
