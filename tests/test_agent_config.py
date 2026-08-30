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

import pytest

from conftest import DIAL_SECRET, secret_file, wav
from gate_agent.config import AgentConfig, ConfigError

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
    assert "holds no token" in str(raised.value)


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
