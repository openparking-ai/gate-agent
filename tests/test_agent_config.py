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

from conftest import wav
from gate_agent.config import AgentConfig, ConfigError

BASE = """
[agent]
id = "agent-1"
site_id = "site-1"

[user_agent]
driver_aor = "sip:agent@10.0.0.20"
operator_aor = "sip:agent-operator@10.0.0.20"

[lanes.entry]
url = "http://127.0.0.1:8090"

[intercoms."sip:door1@10.0.0.9"]
lane = "entry"
name_audio = "NAME_AUDIO"

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
    menu meant for the person on the phone plays to the driver."""
    refused(
        tmp_path,
        BASE.replace('operator_aor = "sip:agent-operator@10.0.0.20"',
                     'operator_aor = "sip:agent@10.0.0.20"'),
        "are the same address",
    )


def test_a_missing_account_is_refused(tmp_path):
    refused(tmp_path, BASE.replace('driver_aor = "sip:agent@10.0.0.20"\n', ""),
            "does not declare driver_aor")


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
