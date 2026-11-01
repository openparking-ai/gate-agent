"""OP-4-3. A bearer credential is never sent over plain http off this box.

A lane's read or act token, a target's token and the webhook's token all travel
as `Authorization: Bearer`. Over `http` to another host that header is readable
and replayable by anything on the path -- and the act token opens a barrier.
Plain http is accepted to loopback only, and a URL that is not http or https is
refused whatever it carries.
"""

from __future__ import annotations

import pytest

from conftest import agent_raw_for
from gate_agent.config import AgentConfig, ConfigError, MonitorConfig


def _token(tmp_path, name="lane.token"):
    path = tmp_path / name
    path.write_text("a-token\n")
    path.chmod(0o600)
    return str(path)


def _agent(tmp_path, url, **extra):
    raw = agent_raw_for(tmp_path, lane_extra=extra)
    raw["lanes"]["entry"]["url"] = url
    return AgentConfig.from_dict(raw)


def _monitor(tmp_path, targets=None, sinks=None):
    return MonitorConfig.from_dict(
        {
            "monitor": {"id": "monitor-1", "site_id": "site-1"},
            "records": {"directory": str(tmp_path / "records")},
            "targets": targets or {"lane": {"url": "http://127.0.0.1:8090"}},
            **({"sinks": sinks} if sinks else {}),
        }
    )


def test_a_lane_token_over_plain_http_off_loopback_is_refused(tmp_path):
    token = _token(tmp_path)
    with pytest.raises(ConfigError, match="in the clear"):
        _agent(tmp_path, "http://10.0.0.7:8090", token_file=token)
    # The controls: loopback, and https, are accepted with the same token; and a
    # lane read with no token at all is not sending one.
    for url in ("http://127.0.0.1:8090", "http://localhost:8090", "http://[::1]:8090",
                "https://10.0.0.7:8090"):
        assert _agent(tmp_path, url, token_file=token).lanes[0].token == "a-token"
    assert _agent(tmp_path, "http://10.0.0.7:8090").lanes[0].token is None


def test_an_act_token_over_plain_http_off_loopback_is_refused(tmp_path):
    token = _token(tmp_path, "lane.act-token")
    with pytest.raises(ConfigError, match="in the clear"):
        _agent(tmp_path, "http://192.168.1.20:8090", act_token_file=token)


def test_a_target_token_and_the_webhook_token_are_held_to_the_same_rule(tmp_path):
    token = _token(tmp_path, "t")
    with pytest.raises(ConfigError, match="in the clear"):
        _monitor(tmp_path, {"lane": {"url": "http://10.0.0.7:8090", "token_file": token}})
    with pytest.raises(ConfigError, match="in the clear"):
        _monitor(tmp_path, sinks={"webhook": {"url": "http://pager.example.com/hook",
                                              "token_file": token}})
    # The controls.
    _monitor(tmp_path, {"lane": {"url": "https://10.0.0.7:8090", "token_file": token}})
    _monitor(tmp_path, sinks={"webhook": {"url": "https://pager.example.com/hook",
                                          "token_file": token}})


@pytest.mark.parametrize("url", ["file:///etc/passwd", "ftp://10.0.0.7/x", "gopher://x"])
def test_a_url_that_is_not_http_or_https_is_refused(tmp_path, url):
    with pytest.raises(ConfigError, match="http or https"):
        _agent(tmp_path, url)
    with pytest.raises(ConfigError, match="http or https"):
        _monitor(tmp_path, {"lane": {"url": url}})
