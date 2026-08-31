"""EVERY credential file this package reads, and the permission guard on it.

`[intercoms.<uri>] dial_secret_file` had this guard for a whole round and it was
the only key that did. A lane's bearer token, the platform's OPERATOR token, a
webhook's token, a camera's `user:password` and the shared token on the read
surfaces could all be `0644`, and this package would start on them without a
word. A credential every account on the box can read is a credential every
account on the box holds; which one it is only decides what they can do with it.

**The enumeration is the point, and it is not a list somebody typed.** The rule
this project keeps breaking is naming the sites instead of naming the query, so
the table below is keyed ONE-TO-ONE against `config.SECRET_FILE_IS` and the last
test in this file compares the two sets both ways. A key that gains a credential
file without a refusal test here goes red, and so does a refusal test for a key
that no longer exists.

Three questions, and they are different questions:

  1. **Is every key guarded?** One case per key, each building a real
     configuration with a real `0644` file, each asserting the refusal names
     that key. The CONTROL for each is the same configuration at `0600`, which
     must be accepted -- a refusal that fires on everything is not a check.
  2. **Can a credential be read anywhere else?** The source of the package is
     walked for reads of a file, and the only ones allowed are
     `read_secret_file` itself, the TOML loaders, and the capture store's
     sidecars. A second reader is a second place for the guard to be missing,
     which is exactly how this came to guard one key out of six.
  3. **Does the guard actually look at the mode?** Its own positive control: the
     same file at `0600`, `0640`, `0604` and `0644`, with the first accepted and
     the other three refused, so "readable by more than its owner" is a
     measurement of the bits rather than of the word.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

import gate_agent
from conftest import DIAL_SECRET, agent_raw_for, capture_raw_for
from gate_agent.config import (
    SECRET_FILE_IS,
    SECRET_FORBIDDEN_MODE,
    AgentConfig,
    CaptureConfig,
    ConfigError,
    MonitorConfig,
    read_secret_file,
)

PACKAGE = Path(gate_agent.__file__).resolve().parent


def written(tmp_path, name: str, body: str, mode: int) -> Path:
    """A credential file at an EXPLICIT mode. Both sides of the guard come from
    here, so the only difference between a case and its control is the mode."""
    path = tmp_path / name
    path.write_text(body + "\n", encoding="utf-8")
    path.chmod(mode)
    return path


# ---------------------------------------------------------------------------
# One case per key. Each builds a real configuration through the real loader.
# ---------------------------------------------------------------------------


def _targets_token_file(tmp_path, mode):
    token = written(tmp_path, "platform.token", "operator-token", mode)
    return lambda: MonitorConfig.from_dict(
        {
            "monitor": {"id": "monitor-1", "site_id": "site-1"},
            "targets": {
                "platform": {
                    "url": "http://127.0.0.1:8080",
                    "garage_id": "garage-1",
                    "token_file": str(token),
                }
            },
        }
    )


def _webhook_token_file(tmp_path, mode):
    token = written(tmp_path, "hook.token", "hook-token", mode)
    return lambda: MonitorConfig.from_dict(
        {
            "monitor": {"id": "monitor-1", "site_id": "site-1"},
            "targets": {"lane": {"url": "http://127.0.0.1:8090"}},
            "sinks": {
                "webhook": {"url": "https://pager.example.com/hook", "token_file": str(token)}
            },
        }
    )


def _camera_auth_file(tmp_path, mode):
    auth = written(tmp_path, "camera.auth", "operator:s3cret", mode)
    directory = tmp_path / "store"
    directory.mkdir(exist_ok=True)
    return lambda: CaptureConfig.from_dict(capture_raw_for(directory, auth))


def _lanes_token_file(tmp_path, mode):
    token = written(tmp_path, "lane.token", "lane-token", mode)
    return lambda: AgentConfig.from_dict(
        agent_raw_for(tmp_path, lane_extra={"token_file": str(token)})
    )


def _dial_secret_file(tmp_path, mode):
    secret = written(tmp_path, "door1.dial-secret", DIAL_SECRET, mode)
    return lambda: AgentConfig.from_dict(agent_raw_for(tmp_path, dial_secret_file=str(secret)))


def _auth_token_file(tmp_path, mode):
    token = written(tmp_path, "surface.token", "shared-token", mode)
    return lambda: read_secret_file(str(token), "--auth-token-file", None)


#: Keyed on the `where` the refusal names, which is the same key
#: `config.SECRET_FILE_IS` is keyed on. The last test compares the two sets.
CASES = {
    "[targets.*].token_file": _targets_token_file,
    "[sinks.webhook].token_file": _webhook_token_file,
    "[cameras.*].auth_file": _camera_auth_file,
    "[lanes.*].token_file": _lanes_token_file,
    "[intercoms.*].dial_secret_file": _dial_secret_file,
    "--auth-token-file": _auth_token_file,
}


@pytest.mark.parametrize("key", sorted(CASES))
def test_a_credential_file_readable_by_more_than_its_owner_is_refused(key, tmp_path):
    """`0644` is refused, and the refusal NAMES the key and what the file is."""
    with pytest.raises(ConfigError) as refused:
        CASES[key](tmp_path, 0o644)()
    message = str(refused.value)
    assert "readable by more than its owner" in message, message
    assert "0644" in message, message
    # The key itself is in the message, with a site's own name where it has one:
    # somebody with six credential files needs to be told which one.
    stem = key.split(".")[-1] if key.startswith("[") else key
    assert stem in message, message
    # And WHAT the file is, so `chmod 600` is not an instruction with no reason
    # attached. The sentence comes from the one mapping; a key with none would
    # produce an empty clause here.
    assert SECRET_FILE_IS[key].split(".")[0] in message, message


@pytest.mark.parametrize("key", sorted(CASES))
def test_the_same_configuration_at_0600_is_accepted(key, tmp_path):
    """THE CONTROL, per key. A refusal that fires on everything is not a check."""
    assert CASES[key](tmp_path, 0o600)() is not None


def test_the_guard_reads_the_MODE_and_not_a_word(tmp_path):
    """The guard's own positive control, across the bits it claims to read.

    "Readable by more than its owner" is a claim about `0o077`. Group-readable
    and other-readable are different bits and a guard that checked one of them
    would pass every test above that uses `0644`.
    """
    verdicts = {}
    for mode in (0o600, 0o400, 0o640, 0o604, 0o644, 0o660):
        path = written(tmp_path, f"cred-{mode:o}", "a-credential", mode)
        try:
            read_secret_file(str(path), "--auth-token-file", None)
            verdicts[f"{mode:04o}"] = "accepted"
        except ConfigError:
            verdicts[f"{mode:04o}"] = "refused"
    assert verdicts == {
        "0600": "accepted",
        "0400": "accepted",
        "0640": "refused",
        "0604": "refused",
        "0644": "refused",
        "0660": "refused",
    }, verdicts
    # And the constant really is the one being read, both bits of it.
    assert SECRET_FORBIDDEN_MODE == 0o077


def test_an_empty_credential_file_is_refused_even_at_0600(tmp_path):
    """A truncated file is not "no credential configured".

    That is authentication silently turning itself off on exactly the target
    that needed it, and it is a separate refusal from the permission one -- so
    it is asserted at a mode the permission guard accepts.
    """
    path = written(tmp_path, "empty", "   ", 0o600)
    with pytest.raises(ConfigError, match="holds no credential"):
        read_secret_file(str(path), "--auth-token-file", None)


# ---------------------------------------------------------------------------
# Nothing else in the package reads a credential
# ---------------------------------------------------------------------------

#: The reads of a file this package makes that are NOT credential reads, each
#: named with what it reads. Anything else in the source is a second credential
#: reader until somebody adds it here, which is a change to this list and
#: therefore a change somebody argues for.
NOT_A_CREDENTIAL = {
    ("config.py", "from_file"): "the TOML configuration file itself",
    ("store.py", "_read_record"): "a capture's own sidecar, which holds no credential",
    ("store.py", "_write_atomic_body"): "a capture's image, and it is a WRITE",
    ("capture.py", "image"): "a capture's own image, served by `GET /v1/capture/images/<id>`",
}


#: What counts as touching a file on this disk, in the two spellings that reach
#: one: the BUILT-IN `open`, and `Path.read_text` / `Path.read_bytes`.
#:
#: An ATTRIBUTE spelt `.open` is deliberately not one of them. `wave.open`,
#: `urllib`'s opener and the capture store's own `open()` are all attribute
#: calls and none of them opens a path -- folding them in gave seven false hits
#: on the first run of this sweep, which is a check that measures the wrong
#: thing rather than a finding.
def _file_reads_in(path: Path):
    """Every read of a path in one module, with the function it is in."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for inner in ast.walk(node):
            if not isinstance(inner, ast.Call):
                continue
            if isinstance(inner.func, ast.Attribute):
                if inner.func.attr in ("read_text", "read_bytes"):
                    found.append((path.name, node.name, inner.func.attr))
            elif getattr(inner.func, "id", None) == "open":
                found.append((path.name, node.name, "open"))
    return found


def test_read_secret_file_is_the_only_thing_that_reads_a_credential():
    """One reader, so there is one place for the guard to be.

    The sweep is over the whole package rather than over `config.py`, because
    the reader that was missing the guard was in `cli.py` -- a check scoped to
    the file where the guard lives could not have seen it.
    """
    offenders = []
    swept = 0
    for path in sorted(PACKAGE.glob("*.py")):
        for module, function, _call in _file_reads_in(path):
            swept += 1
            if function == "read_secret_file":
                continue
            if (module, function) in NOT_A_CREDENTIAL:
                continue
            offenders.append(f"{module}: {function}()")
    assert not offenders, (
        f"a file is read outside `read_secret_file` and outside the named exceptions: "
        f"{sorted(set(offenders))}. If it is not a credential, name it in NOT_A_CREDENTIAL "
        "with what it reads; if it is, read it through the guard."
    )
    assert swept, "the sweep found no file reads at all, so it is not looking at the right thing"


def test_that_sweep_sees_a_planted_reader():
    """The control, through the same helper the sweep uses.

    Written to a temporary file rather than tracked, and parsed by
    `_file_reads_in` itself -- a second copy of the logic that happened to agree
    would prove nothing.
    """
    import tempfile

    planted = Path(tempfile.mkdtemp()) / "planted.py"
    planted.write_text(
        "def _sneak(path):\n"
        "    return path.read_text(encoding='utf-8')\n"
        "\n"
        "def _sneak_two(path):\n"
        "    with open(path) as handle:\n"
        "        return handle.read()\n",
        encoding="utf-8",
    )
    found = _file_reads_in(planted)
    assert {(function, call) for _module, function, call in found} == {
        ("_sneak", "read_text"),
        ("_sneak_two", "open"),
    }, found
    # And the NEGATIVE half of the same control: an attribute spelt `.open` is
    # not a read of a path, so the sweep must not see one. Without this, the
    # exception list would silently be doing the work the matcher should do.
    other = Path(tempfile.mkdtemp()) / "other.py"
    other.write_text(
        "import wave\n"
        "def _not_a_file(path, store):\n"
        "    store.open()\n"
        "    return wave.open(str(path), 'rb')\n",
        encoding="utf-8",
    )
    assert _file_reads_in(other) == [], _file_reads_in(other)


def test_every_key_with_a_sentence_has_a_refusal_case_and_the_other_way_round():
    """The enumeration, keyed BY IDENTIFIER, generated from the same list both ways.

    A missing key and an orphan key each fail. This is the check that makes the
    table above an enumeration of the package rather than a list somebody typed
    and then stopped updating -- which is the failure this project has had
    reported against it more than once.
    """
    assert set(CASES) == set(SECRET_FILE_IS), {
        "keys with a sentence and no refusal case": sorted(set(SECRET_FILE_IS) - set(CASES)),
        "refusal cases for a key with no sentence": sorted(set(CASES) - set(SECRET_FILE_IS)),
    }
    # And every sentence says something. An empty one would put "This file holds
    # `chmod 600` it." in a refusal.
    for key, sentence in SECRET_FILE_IS.items():
        assert sentence.strip(), key
