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
from gate_agent.contract import OPENING_AUTHORISATIONS, AgentCase, Authorisation
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


def test_a_door_that_can_act_has_its_own_authorisation_line_and_it_claims_less():
    """THE BRANCH, at the line table: two keys per authorisation that can act.

    The unsuffixed key is the one spoken where nothing at this door can ask for
    anything, and it says so. The `.acting` key is spoken where this agent is
    about to ask, and there the same clause is a lie the driver hears one
    sentence before `ticket.vend_commanded`.

    **The invariant is the DIRECTION.** The acting sentence has to be a prefix
    of the other one -- word for word, in every shipped language -- so a claim
    can only ever be DROPPED for the door that acts and never added there. A
    site-dependent promise that exists only on the acting key would be one no
    driver at a door that cannot act ever hears anybody check.

    Derived from `OPENING_AUTHORISATIONS`, which is `ACTS` itself: an
    authorisation that becomes an act gets the pair, and one that is not an act
    does not get a second sentence nobody plays.
    """
    for value in Authorisation:
        base = f"authorisation.{value.value}"
        acting = f"{base}.acting"
        if value not in OPENING_AUTHORISATIONS:
            assert acting not in DRIVER_LINES, acting
            continue
        assert acting in DRIVER_LINES, acting
        for language in SHIPPED_LANGUAGES:
            told = TEXT[acting][language].strip()
            instead = TEXT[base][language].strip()
            assert told and told != instead, f"{acting} in {language}"
            assert instead.startswith(told), (
                f"{acting} in {language} says something {base} does not: "
                f"{told!r} is not the opening of {instead!r}"
            )


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


def test_a_planted_edit_is_found_by_the_same_predicate_the_script_uses():
    """THE CONTROL for the manifest, and it is the reason the manifest exists.

    **What this actually does, said plainly:** it plants an edited sentence in a
    COPY of the manifest and re-implements the script's staleness predicate --
    `row["text"] != TEXT[line][language]` -- inline, here. It is NOT run through
    `build_audio.py --check`, and the docstring used to claim it was. The
    guarantee that a sentence cannot be edited without its audio being
    regenerated is held by `test_the_manifest_says_what_every_file_says`, which
    compares the shipped manifest against `lines.TEXT` and against the bytes on
    disk; this asserts that the predicate those comparisons rest on is capable
    of firing at all.
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


def test_the_manifest_records_who_wrote_the_words_and_from_what():
    """The provenance of the TEXT, per language, and every file names its row.

    The manifest recorded the voice, the tool and the tool's licence for the
    AUDIO and nothing at all about the TEXT -- and the text is the thing the
    audio is only a rendering of. `lines.py` said "the English is the source and
    the Spanish is a translation of it", which says what the relationship is and
    not who made it.
    """
    provenance = MANIFEST["text_provenance"]
    assert set(provenance) == set(SHIPPED_LANGUAGES), sorted(
        set(provenance) ^ set(SHIPPED_LANGUAGES)
    )
    for language, row in provenance.items():
        for field in ("written_by", "from", "reviewed_by"):
            assert row.get(field, "").strip(), f"{language} does not record {field}"
    # Every file points at the row for its own language, so a language added
    # without a provenance row cannot ship.
    for name, row in MANIFEST["files"].items():
        assert row["text_provenance"] == row["language"], name
        assert row["text_provenance"] in provenance, name
    # The Spanish row says what was NOT done, because that is the load-bearing
    # half: no native speaker and no professional translator.
    assert "native speaker" in provenance["es-ES"]["reviewed_by"].lower()


def test_the_spanish_ships_under_a_regional_tag():
    """`es-ES`, not `es`. The register is Castilian and the key says so.

    Under a generic tag a garage in Texas or Bogota declares "Spanish", gets
    this, and hears several words that are wrong for its drivers -- a register
    chosen for them by a package that never asked.
    """
    assert "es-ES" in SHIPPED_LANGUAGES
    assert "es" not in SHIPPED_LANGUAGES
    assert all(name.split("/")[0] in SHIPPED_LANGUAGES for name in MANIFEST["files"])
    # And the words that make it Castilian are actually in there, so the claim
    # in the key is about the text rather than about a tag somebody typed.
    spanish = " ".join(TEXT[line]["es-ES"] for line in LINES)
    for word in ("matrícula", "aparcamiento", "almohadilla"):
        assert word in spanish, f"{word} is not in the Spanish this key claims to be"
