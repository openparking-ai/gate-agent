"""Every refusal the configuration makes, and the reason each one exists.

These are not validation for tidiness. Each one is a configuration that would
RUN and be wrong in a way nobody would see: a monitor watching nothing that
reports all fine, a platform target pointed at a garage nobody chose, an email
sink with a placeholder recipient, a credential sitting in a file that gets
pasted into a chat window.

Every one is asserted with its opposite beside it — the same configuration with
the missing thing supplied, accepted — because a refusal that fires on
everything is not a check either.
"""

from __future__ import annotations

import pytest

from gate_agent.config import (
    DEFAULT_CAPTURE_INTERVAL_SECONDS,
    DEFAULT_EVENT_WINDOW_DEPTH,
    DEFAULT_LANE_QUIET_SECONDS,
    DEFAULT_POLL_SECONDS,
    DEFAULT_RETENTION_DAYS,
    DEFAULT_TIMEOUT_SECONDS,
    ConfigError,
    EmailSinkConfig,
    MonitorConfig,
    WebhookSinkConfig,
)
from gate_agent.contract import TargetKind

BASE = {"monitor": {"id": "monitor-1", "site_id": "site-1"}}


def config(**over):
    raw = {"monitor": dict(BASE["monitor"])}
    raw.update(over)
    return MonitorConfig.from_dict(raw)


def a_lane(**over):
    return {"lane": {"url": "http://127.0.0.1:8090", **over}}


@pytest.fixture
def token_file(tmp_path):
    """A real file holding a real token. There is no key that takes a value."""
    path = tmp_path / "platform.token"
    path.write_text("operator-token\n")
    return str(path)


# ---------------------------------------------------------------------------
# The empty target set
# ---------------------------------------------------------------------------


def test_a_monitor_with_no_targets_is_refused_by_name():
    """The one the whole module turns on.

    A monitor watching nothing has nothing to report and would report exactly
    that: all fine. That is the lie this module exists to prevent, so it is
    refused at startup with the reason in the message rather than running.
    """
    with pytest.raises(ConfigError) as refused:
        config()
    assert "watching nothing" in str(refused.value)
    assert "targets.lane" in str(refused.value)

    # The control: one target is enough, and the refusal is about the emptiness
    # rather than about anything else in the file.
    assert len(config(targets=a_lane()).targets) == 1


def test_standalone_is_a_mode_and_any_single_target_is_one(token_file):
    """A Vehicle ID service alone, a platform alone: both are configurations.

    Standalone is a MODE of every step of this module, stated and tested, not a
    smaller product.
    """
    only_identity = config(targets={"identity_service": {"url": "http://127.0.0.1:8088"}})
    assert [target.kind for target in only_identity.targets] == [TargetKind.IDENTITY_SERVICE]

    only_platform = config(
        targets={
            "platform": {
                "url": "http://platform.example/api/v1",
                "garage_id": "g-1",
                "token_file": token_file,
            }
        }
    )
    assert [target.kind for target in only_platform.targets] == [TargetKind.PLATFORM]


def test_a_target_kind_this_monitor_cannot_read_is_refused():
    """A target it has no reader for would be reported on as if understood."""
    with pytest.raises(ConfigError, match="no reader for"):
        config(targets={"barrier": {"url": "http://127.0.0.1:9000"}})


# ---------------------------------------------------------------------------
# Credentials: files, never values
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        {"targets": {"lane": {"url": "http://x", "token": "s3cret"}}},
        {"targets": {"platform": {"url": "http://x", "garage_id": "g", "auth_token": "s3cret"}}},
        {"sinks": {"webhook": {"url": "http://x", "bearer_token": "s3cret"}}},
        {"sinks": {"email": {"host": "h", "port": 25, "from": "a", "to": ["b"], "password": "p"}}},
        {"monitor": {"id": "m", "site_id": "s", "api_key": "k"}},
    ],
)
def test_a_key_that_would_hold_a_credential_by_value_is_refused_by_name(raw):
    """Refused loudly, not ignored.

    An undocumented key that WORKS is a key somebody uses, and the credential is
    then in the configuration file, in every backup of it, and in everything
    anyone ever pastes it into. The refusal names the file key that replaces it,
    so the person who wrote it knows what to write instead.
    """
    payload = {"monitor": dict(BASE["monitor"])}
    payload.update(raw)
    with pytest.raises(ConfigError) as refused:
        MonitorConfig.from_dict(payload)
    assert "credential as a value" in str(refused.value)
    assert "_file" in str(refused.value)


def test_a_token_file_that_holds_nothing_is_refused(tmp_path):
    """A truncated file must not read as "no credential configured".

    That is authentication silently turning itself off on exactly the target
    that needed it.
    """
    empty = tmp_path / "empty.token"
    empty.write_text("   \n")
    with pytest.raises(ConfigError, match="holds no token"):
        MonitorConfig.from_dict(
            {
                **BASE,
                "targets": {
                    "platform": {
                        "url": "http://x",
                        "garage_id": "g",
                        "token_file": str(empty),
                    }
                },
            }
        )

    # The control: the same configuration with a real file is accepted, and the
    # token is read out of it rather than being the path.
    real = tmp_path / "real.token"
    real.write_text("operator-token\n")
    parsed = MonitorConfig.from_dict(
        {
            **BASE,
            "targets": {
                "platform": {"url": "http://x", "garage_id": "g", "token_file": str(real)}
            },
        }
    )
    assert parsed.targets[0].token == "operator-token"
    assert parsed.targets[0].authenticated is True


# ---------------------------------------------------------------------------
# Declared, where no default is safe
# ---------------------------------------------------------------------------


def test_a_platform_target_declares_its_garage_and_its_token(tmp_path):
    """Neither has a safe default, and the reasons are different.

    Without a token there is nothing to read at all. Without a garage the
    monitor lists no devices — and no devices reads exactly like no faults,
    which is the reassuring direction on the one code that says a lane has
    stopped reporting.
    """
    token = tmp_path / "t"
    token.write_text("operator-token")

    with pytest.raises(ConfigError, match="token_file"):
        config(targets={"platform": {"url": "http://x", "garage_id": "g"}})

    with pytest.raises(ConfigError, match="garage_id"):
        config(targets={"platform": {"url": "http://x", "token_file": str(token)}})

    # The control: with both, it is accepted.
    parsed = config(
        targets={"platform": {"url": "http://x", "garage_id": "g", "token_file": str(token)}}
    )
    assert parsed.targets[0].garage_id == "g"


def test_the_settings_have_published_defaults_and_they_are_the_published_ones(token_file):
    """A setting with a default is not a measurement, and the default is one copy.

    Compared against the module constants rather than against numbers typed
    here: a number typed in a test is a second copy of the value, and the copy is
    the one that stops matching.
    """
    parsed = config(targets=a_lane())
    assert parsed.targets[0].poll_seconds == DEFAULT_POLL_SECONDS
    assert parsed.renotify_seconds is None

    with_platform = config(
        targets={
            "platform": {"url": "http://x", "garage_id": "g", "token_file": token_file},
        }
    )
    assert with_platform.targets[0].lane_quiet_seconds == DEFAULT_LANE_QUIET_SECONDS

    # And each is overridable per site, which is what makes it a setting.
    tuned = config(targets=a_lane(poll_seconds=5), notify={"renotify_seconds": 60})
    assert tuned.targets[0].poll_seconds == 5.0
    assert tuned.renotify_seconds == 60.0


@pytest.mark.parametrize("value", [0, -1, "soon", True, [30]])
def test_an_interval_that_is_not_a_positive_number_of_seconds_is_refused(value):
    """`True` is in there on purpose: it is an `int` in Python and it is not a
    number of seconds. A bare `isinstance(value, int)` accepts it and a lane
    polled every `True` seconds is polled every one."""
    with pytest.raises(ConfigError, match="positive number of seconds"):
        config(targets=a_lane(poll_seconds=value))


# ---------------------------------------------------------------------------
# Sinks
# ---------------------------------------------------------------------------


def test_log_is_always_a_sink_and_a_site_with_only_log_is_valid():
    """It means NOBODY IS PAGED, and that is a real configuration.

    Valid where the logs are already collected. What must not happen is for it
    to be discovered the first time a lane goes down at midnight, which is why
    the process says it on the line it prints when it starts.
    """
    parsed = config(targets=a_lane())
    assert [sink.name for sink in parsed.sinks] == ["log"]


def test_an_email_sink_declares_every_field_and_has_no_default_recipient():
    """A default recipient pages somebody who never asked, or a placeholder.

    The second is the dangerous one: the site believes it is covered.
    """
    for missing in ("host", "port", "from", "to"):
        email = {"host": "h", "port": 25, "from": "a@example.com", "to": ["b@example.com"]}
        del email[missing]
        with pytest.raises(ConfigError, match="does not declare"):
            config(targets=a_lane(), sinks={"email": email})

    parsed = config(
        targets=a_lane(),
        sinks={"email": {"host": "h", "port": 25, "from": "a@example.com", "to": "b@example.com"}},
    )
    email = next(sink for sink in parsed.sinks if isinstance(sink, EmailSinkConfig))
    assert email.recipients == ("b@example.com",)
    # TLS is on unless a site turns it off: this traffic is a map of which of a
    # site's lanes are broken and when.
    assert email.tls is True


def test_a_webhook_sink_declares_its_token_as_a_file(tmp_path):
    with pytest.raises(ConfigError, match="token_file"):
        config(targets=a_lane(), sinks={"webhook": {"url": "https://paging.example/hook"}})

    token = tmp_path / "hook.token"
    token.write_text("hook-token")
    parsed = config(
        targets=a_lane(),
        sinks={"webhook": {"url": "https://paging.example/hook", "token_file": str(token)}},
    )
    webhook = next(sink for sink in parsed.sinks if isinstance(sink, WebhookSinkConfig))
    assert webhook.token == "hook-token"


def test_a_sink_this_version_does_not_have_is_refused():
    """The set is CLOSED, and SMS is a later round's provider.

    Refused rather than ignored, so a site that configured one finds out at
    startup instead of believing its people are being texted.
    """
    with pytest.raises(ConfigError) as refused:
        config(targets=a_lane(), sinks={"sms": {"to": "+10000000000"}})
    assert "SMS is a later round" in str(refused.value)


def test_a_token_file_path_resolves_against_the_configuration_file(tmp_path):
    """A relative path in a configuration file means "beside this file".

    Resolved against the file rather than against the working directory the
    process happened to start in, which is how a service that works by hand
    fails under a supervisor.
    """
    token = tmp_path / "platform.token"
    token.write_text("operator-token")
    config_file = tmp_path / "monitor.toml"
    config_file.write_text(
        '[monitor]\nid = "m"\nsite_id = "s"\n\n'
        '[targets.platform]\nurl = "http://x"\ngarage_id = "g"\ntoken_file = "platform.token"\n'
    )
    parsed = MonitorConfig.from_file(config_file)
    assert parsed.targets[0].token == "operator-token"


def test_the_example_configuration_parses():
    """The file an installer copies is the one this test reads.

    A worked example that does not parse is worse than none: whoever copies it
    debugs our documentation instead of their site.
    """
    from pathlib import Path

    example = Path(__file__).resolve().parent.parent / "config" / "monitor.example.toml"
    text = example.read_text(encoding="utf-8")
    assert "[targets.lane]" in text
    # The credential paths in it do not exist on this machine, which is correct
    # for an example -- so the parse is exercised with those lines commented out
    # the way an installer without a platform would leave them.
    import tomllib

    raw = tomllib.loads(text)
    raw["targets"].pop("platform")
    raw["sinks"].pop("webhook")
    parsed = MonitorConfig.from_dict(raw)
    assert {target.name for target in parsed.targets} == {"lane", "identity_service", "capture"}
    assert {type(sink).__name__ for sink in parsed.sinks} == {"LogSinkConfig", "EmailSinkConfig"}
    assert parsed.event_window_depth == DEFAULT_EVENT_WINDOW_DEPTH, (
        "the example declares a window depth that is not the published default, so an installer "
        "copying it gets something other than what the document says they get"
    )


def test_the_capture_example_configuration_is_refused_as_shipped_and_says_why():
    """**The example is refused on purpose, and that is the lesson in it.**

    `[capture] max_bytes` is commented out because it has no default and there
    is no measurement in this package to draw one from. An example that shipped
    a number would be this package inventing a disk budget for a site it has
    never seen, in the one file an installer copies without reading.
    """
    import tomllib
    from pathlib import Path

    from gate_agent.config import CaptureConfig

    example = Path(__file__).resolve().parent.parent / "config" / "capture.example.toml"
    raw = tomllib.loads(example.read_text(encoding="utf-8"))
    assert "max_bytes" not in raw["capture"], "the example now ships an invented disk budget"
    with pytest.raises(ConfigError, match="does not declare max_bytes"):
        CaptureConfig.from_dict(raw, relative_to=example.parent)

    # And with that one question answered, the rest of the file parses -- so the
    # refusal above is about `max_bytes` and not about a broken example.
    # Above the published `max_snapshot_bytes` default, because the ceiling on
    # one read is refused unless it is below the cap on the whole store -- so a
    # site whose store is smaller than that ceiling answers both questions, and
    # this one is answering only the one the example asks.
    raw["capture"]["max_bytes"] = 64 << 20
    auth = Path(raw["cameras"]["front"]["auth_file"])
    assert not auth.exists(), "this test would be reading a real credential file"
    raw["cameras"]["front"]["auth_file"] = "front.auth"
    import tempfile

    directory = Path(tempfile.mkdtemp())
    (directory / "front.auth").write_text("operator:s3cret\n", encoding="utf-8")
    raw["capture"]["directory"] = str(directory)
    parsed = CaptureConfig.from_dict(raw, relative_to=directory)
    assert [camera.camera_id for camera in parsed.cameras] == ["front"]
    assert parsed.lane is not None
    assert parsed.retention_days == DEFAULT_RETENTION_DAYS
    assert parsed.interval_seconds == DEFAULT_CAPTURE_INTERVAL_SECONDS


# ---------------------------------------------------------------------------
# A CREDENTIAL IN A URL
#
# Six key names are refused because each would hold a credential as a VALUE.
# `url` is a key that works, is documented, and carries one very well:
# `https://ops:S3CRET@example.com` was accepted, and then republished
# verbatim on `GET /v1/monitor` beside `authenticated: false` -- so the one
# field a consumer would use to notice read the wrong way.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "https://ops:S3CRET-PASSWORD@example.com",
        "http://ops@example.com:8090",
        "http://:S3CRET-PASSWORD@127.0.0.1:8090",
    ],
)
def test_a_target_url_carrying_userinfo_is_refused_by_name(url):
    with pytest.raises(ConfigError, match="userinfo in URL: credentials come from files"):
        config(targets=a_lane(url=url))
    # The opposite, beside it: the same host with no credential in it is fine.
    assert config(targets=a_lane(url="https://lane.example.com")).targets


def test_a_webhook_url_carrying_userinfo_is_refused_too(tmp_path):
    """The other direction leaves this process, and it leaves with a token.

    A webhook is the seat a third party's paging system takes, so its URL comes
    from whoever runs that system -- which is exactly where a convenient
    `user:password@` arrives from.
    """
    token = tmp_path / "webhook.token"
    token.write_text("page-me\n")
    sinks = {"webhook": {"url": "https://ops:S3CRET@example.com", "token_file": str(token)}}
    with pytest.raises(ConfigError, match="userinfo in URL"):
        config(targets=a_lane(), sinks=sinks)

    sinks["webhook"]["url"] = "https://pager.example.com/hook"
    assert config(targets=a_lane(), sinks=sinks).sinks


def test_the_credential_sweep_descends_into_a_list_of_tables():
    """An array of tables is a list of dicts, and the walk stepped over it.

    Nothing in today's schema is one, which is why this is worth closing now:
    the day a site declares two webhooks, the sweep would not have been looking.
    """
    with pytest.raises(ConfigError, match="would hold a credential as a value"):
        config(targets=a_lane(), pagers=[{"url": "https://pager.example.com", "token": "s3cret"}])
    # The control: the same shape without a credential key in it is accepted by
    # the sweep -- it refuses a credential, not a list.
    assert config(targets=a_lane(), pagers=[{"url": "https://pager.example.com"}]).targets


# ---------------------------------------------------------------------------
# The client timeout is a per-site setting with a published default
# ---------------------------------------------------------------------------


def test_the_timeout_is_a_setting_with_a_published_default():
    assert config(targets=a_lane()).targets[0].timeout_seconds == DEFAULT_TIMEOUT_SECONDS
    assert config(targets=a_lane(timeout_seconds=2.5)).targets[0].timeout_seconds == 2.5
    for bad in (0, -1, "quickly", True):
        with pytest.raises(ConfigError, match="timeout_seconds"):
            config(targets=a_lane(timeout_seconds=bad))


def test_the_timeout_default_is_the_one_the_client_waits():
    """One copy. The constant lives beside the code that waits on the socket."""
    from gate_agent.client import DEFAULT_TIMEOUT

    assert DEFAULT_TIMEOUT_SECONDS == DEFAULT_TIMEOUT
