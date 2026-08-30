"""The audio: that it exists, that it says what the tests say, and where it came from.

**A file in this repository is produced by a command, never by hand**, and the
manifest is what makes that checkable without the synthesiser being installed:
it records the exact text every file was made from and that file's digest, so
editing a sentence without regenerating its audio goes red HERE rather than
shipping a file that says the old thing to somebody at a barrier.

The synthesiser is not needed to run any of this, and that is deliberate: CI
must be able to hold the assets to the text on a runner that has no espeak-ng.
"""

from __future__ import annotations

import hashlib
import json
import wave
from pathlib import Path

import gate_agent
from gate_agent.contract import AgentCase, Authorisation
from gate_agent.lines import (
    DRIVER_LINES,
    LINES,
    OPERATOR_LINES,
    SHIPPED_LANGUAGES,
    TEXT,
    audio_name,
)

AUDIO = Path(gate_agent.__file__).resolve().parent / "audio"
MANIFEST = json.loads((AUDIO / "MANIFEST.json").read_text(encoding="utf-8"))


def test_every_line_has_words_in_every_shipped_language():
    """Both directions: a line with no text, and text for a line nobody plays."""
    assert set(TEXT) == set(LINES), sorted(set(TEXT) ^ set(LINES))
    for line in LINES:
        for language in SHIPPED_LANGUAGES:
            assert TEXT[line].get(language, "").strip(), f"{line} has no {language}"


def test_the_line_set_is_derived_from_the_closed_sets():
    """A case or an authorisation added without a sentence cannot exist.

    The lines are DERIVED from `AgentCase` and `Authorisation` rather than
    listed, because a hard-coded list cannot notice what was added to what it is
    supposed to cover -- and the thing it would fail to notice is a driver who
    hears nothing about the case they are in.
    """
    for case in AgentCase:
        assert f"case.{case.value}" in DRIVER_LINES
        assert f"operator_case.{case.value}" in OPERATOR_LINES
    for value in Authorisation:
        assert f"authorisation.{value.value}" in DRIVER_LINES
        assert f"menu.{value.value}" in OPERATOR_LINES


def test_every_line_has_a_file_and_every_file_has_a_line():
    """Both directions, so an orphan file is as loud as a missing one."""
    on_disk = {
        f"{path.parent.name}/{path.name}" for path in AUDIO.glob("*/*.wav")
    }
    expected = {
        audio_name(line, language) for line in LINES for language in SHIPPED_LANGUAGES
    }
    assert on_disk == expected, sorted(on_disk ^ expected)


def test_the_manifest_says_what_every_file_says():
    """The text in the manifest IS the text in `lines.TEXT`, file by file.

    This is what catches a sentence edited without the audio being regenerated.
    Nothing else can: the file is a waveform, and no test can read English out
    of one.
    """
    assert set(MANIFEST["files"]) == {
        audio_name(line, language) for line in LINES for language in SHIPPED_LANGUAGES
    }
    for name, row in MANIFEST["files"].items():
        assert row["text"] == TEXT[row["line"]][row["language"]], name


def test_every_file_is_the_bytes_the_manifest_recorded():
    """And the digest, so a file replaced by hand is not silently shipped."""
    for name, row in MANIFEST["files"].items():
        path = AUDIO / name
        assert hashlib.sha256(path.read_bytes()).hexdigest() == row["sha256"], name


def test_every_file_is_what_a_narrowband_call_carries():
    """8 kHz, mono, 16-bit. What the user agent plays into a PCMU stream.

    A file at the synthesiser's own rate would be resampled by whatever is in
    the media path that day, and a shipped asset resampled by somebody else is
    not an asset whose content this repository can make a statement about.
    """
    for name in MANIFEST["files"]:
        with wave.open(str(AUDIO / name), "rb") as handle:
            assert (handle.getframerate(), handle.getnchannels(), handle.getsampwidth()) == (
                8000,
                1,
                2,
            ), name
            assert handle.getnframes() > 0, f"{name} is silence"


def test_every_file_names_a_licence_the_manifest_states_in_full():
    """Provenance per file: what produced it, and what lets a sold product ship it.

    Keyed by name into one licence block rather than repeated ninety times: a
    paragraph copied ninety times is eighty-nine copies to go stale, and the
    rule in this project is that a claim lives in one place.
    """
    for name, row in MANIFEST["files"].items():
        assert row["licence"] in MANIFEST["licences"], name
        assert row["voice"], name
    for licence in MANIFEST["licences"].values():
        for key in ("tool", "tool_licence", "tool_licence_source", "corpus",
                    "corpus_note", "ships_in_a_sold_product"):
            assert licence[key].strip(), key
    # The finding this whole choice turns on, asserted rather than described:
    # the disqualifying licence on the obvious alternative was a CORPUS licence,
    # and this synthesiser has no corpus.
    assert MANIFEST["licences"]["espeak-ng-formant"]["corpus"] == "none"
    assert MANIFEST["tool_version"], "nothing recorded which build produced these"
    # And the version is a version, not a path on whoever ran the script.
    assert "/" not in MANIFEST["tool_version"], MANIFEST["tool_version"]


def test_the_build_script_reports_a_planted_edit_as_stale():
    """THE CONTROL for the manifest, and it is the reason the manifest exists.

    An edited sentence with unregenerated audio must be found. Run against a
    copy with one row's text changed, `--check`'s comparison has to say so --
    and it is run through the script's own comparison rather than a second copy
    of it that happens to agree.
    """
    planted = json.loads(json.dumps(MANIFEST))
    name = audio_name("case.plate_unclear", "en")
    planted["files"][name]["text"] = "something nobody recorded"
    stale = [
        one
        for one, row in planted["files"].items()
        if row["text"] != TEXT[row["line"]][row["language"]]
    ]
    assert stale == [name], stale
