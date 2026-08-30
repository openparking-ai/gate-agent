#!/usr/bin/env python3
"""Produce every audio file this package ships, from the text it ships.

    python3 scripts/build_audio.py            # regenerate everything
    python3 scripts/build_audio.py --check    # say what is stale, change nothing

**A file in this repository is produced by a command, never by hand.** The words
in `lines.TEXT` are the words the synthesiser is given, and the manifest records
the exact text every file was made from -- so editing a sentence without
rerunning this goes red in `tests/test_agent_audio.py` rather than shipping a
file that says the old thing to a driver at a barrier.

**Why this synthesiser.** The L1 for this module read the licence of the obvious
choice and found it disqualifying: Piper's most-reached-for English voices are
trained on a corpus whose licence is research-only and forbids redistribution,
and the repository-level licence tag does not say so. The problem is the
RECORDED CORPUS, so the answer is a synthesiser that has none. eSpeak NG's own
README says what it is, verbatim: *"eSpeak NG uses a 'formant synthesis' method.
This allows many languages to be provided in a small size. The speech is clear,
and can be used at high speeds, but is not as natural or smooth as larger
synthesizers which are based on human speech recordings."* There is no corpus to
have a licence, and MBROLA -- the optional backend that would bring separately
licensed voices -- is not used here.

What it sounds like is stated rather than sold: it is machine speech, and it is
intelligible at 8 kHz over a narrowband call, which is the only property this
job needs. A site that wants a voice may replace any file, because the manifest
records what each one has to say.

**Everything is 8 kHz mono 16-bit PCM**, which is what a narrowband SIP call
carries. The resampling below is done here rather than by the user agent so that
what ships is what plays: a file at the synthesiser's own rate would be
resampled by whatever is in the media path that day, and "the words are
byte-identical to the tested string" would stop being a statement about a file.
"""

from __future__ import annotations

import argparse
import array
import hashlib
import json
import shutil
import subprocess
import sys
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from gate_agent.lines import LINES, SHIPPED_LANGUAGES, TEXT, audio_name  # noqa: E402

ASSETS = ROOT / "src" / "gate_agent" / "audio"
MANIFEST = ASSETS / "MANIFEST.json"

#: The rate every shipped file is at. Narrowband SIP.
RATE = 8000

#: The voice each shipped language is spoken in. Per language, named, and in the
#: manifest beside every file -- a voice is part of what produced a file, and a
#: file whose voice nobody recorded cannot be reproduced.
VOICES: dict[str, str] = {"en": "en-us", "es": "es"}

#: Slower than the default 175 wpm. A driver at a barrier hears this once,
#: through a door station, with an engine running.
WORDS_PER_MINUTE = 150

#: The licence rows. One entry per licence, referenced BY KEY from every file in
#: the manifest -- ninety copies of a paragraph would be eighty-nine copies to
#: go stale, and the rule in this project is that a claim lives in one place.
LICENCES: dict[str, dict] = {
    "espeak-ng-formant": {
        "tool": "espeak-ng",
        "tool_licence": "GPL-3.0-or-later",
        "tool_licence_source": "espeak-ng/espeak-ng README.md, License Information: "
        "\"eSpeak NG Text-to-Speech is released under the GPL version 3 or later license.\" "
        "(read 2026-08-30)",
        "corpus": "none",
        "corpus_note": "eSpeak NG is a FORMANT synthesiser. Its own README says it "
        "\"is not as natural or smooth as larger synthesizers which are based on human "
        "speech recordings\" (read 2026-08-30) -- it is not based on recordings at all, so "
        "there is no speech corpus and no corpus licence. That is the whole reason it is "
        "here: the licence that disqualified the obvious alternative was a corpus licence. "
        "MBROLA, the optional backend that would bring separately licensed voices, is not "
        "used.",
        "ships_in_a_sold_product": "Yes. The audio incorporates no eSpeak NG code and no "
        "recorded speech, so under the ordinary reading of what a program's OUTPUT is, it "
        "is not covered by the tool's licence. That reading is STATED here rather than "
        "assumed, and it is bounded: every file is regenerable by this script from text "
        "this repository owns, with any other synthesiser, so nothing here is a dependency "
        "that cannot be replaced.",
    }
}


def synthesise(text: str, voice: str) -> bytes:
    """One line, spoken, at whatever rate the synthesiser uses."""
    if shutil.which("espeak-ng") is None:
        raise SystemExit(
            "espeak-ng is not installed. It is a BUILD-time tool and not a dependency of "
            "this package: it produces the files in assets/audio/ once, and nothing at "
            "runtime needs it. Install it (Debian/Ubuntu: apt-get install espeak-ng; "
            "macOS: brew install espeak-ng) and run this again."
        )
    out = subprocess.run(
        ["espeak-ng", "-v", voice, "-s", str(WORDS_PER_MINUTE), "-w", "/dev/stdout", text],
        check=True,
        capture_output=True,
    )
    return out.stdout


def resample(raw: bytes, rate: int) -> bytes:
    """A WAV's frames at `RATE`, mono, 16-bit, with a box filter over the window.

    Plain nearest-sample decimation from 22 kHz to 8 kHz aliases audibly on
    fricatives; averaging the samples that fall inside each output window is the
    cheapest thing that does not, and it needs nothing but the standard library.
    """
    if rate == RATE:
        return raw
    samples = array.array("h")
    samples.frombytes(raw[: len(raw) // 2 * 2])
    ratio = rate / RATE
    out = array.array("h")
    total = int(len(samples) / ratio)
    for i in range(total):
        start = int(i * ratio)
        end = max(start + 1, int((i + 1) * ratio))
        window = samples[start:end]
        out.append(int(sum(window) / len(window)) if window else 0)
    return out.tobytes()


def wav_frames(blob: bytes) -> tuple[bytes, int, int, int]:
    """The frames out of a WAV in memory, with its rate, channels and width."""
    import io

    with wave.open(io.BytesIO(blob), "rb") as handle:
        return (
            handle.readframes(handle.getnframes()),
            handle.getframerate(),
            handle.getnchannels(),
            handle.getsampwidth(),
        )


def write_wav(path: Path, frames: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(RATE)
        handle.writeframes(frames)


def _tool_version() -> str:
    """Just the version. `espeak-ng --version` also prints where its data lives,
    which is a path on whoever ran this -- a builder's home directory is not a
    property of the product and does not belong in a file this repository
    publishes."""
    if shutil.which("espeak-ng") is None:
        return ""
    raw = subprocess.run(
        ["espeak-ng", "--version"], capture_output=True, text=True, check=False
    ).stdout.strip()
    return raw.split("Data at:")[0].strip()


def build(check_only: bool) -> int:
    files: dict[str, dict] = {}
    stale: list[str] = []
    for language in SHIPPED_LANGUAGES:
        for line in LINES:
            text = TEXT[line][language]
            name = audio_name(line, language)
            path = ASSETS / name
            if not check_only:
                blob = synthesise(text, VOICES[language])
                raw, rate, channels, width = wav_frames(blob)
                if channels != 1 or width != 2:
                    raise SystemExit(f"{name}: synthesiser gave {channels}ch/{width}B")
                write_wav(path, resample(raw, rate))
            if not path.exists():
                stale.append(f"{name}: missing")
                continue
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            files[name] = {
                "line": line,
                "language": language,
                "text": text,
                "sha256": digest,
                "bytes": path.stat().st_size,
                "sample_rate": RATE,
                "voice": VOICES[language],
                "words_per_minute": WORDS_PER_MINUTE,
                "licence": "espeak-ng-formant",
            }
    tool_version = _tool_version()
    manifest = {
        "generated_by": "scripts/build_audio.py",
        "tool_version": tool_version,
        "licences": LICENCES,
        "files": dict(sorted(files.items())),
    }
    if check_only:
        old = json.loads(MANIFEST.read_text(encoding="utf-8")) if MANIFEST.exists() else {}
        for name, row in manifest["files"].items():
            was = old.get("files", {}).get(name)
            if was is None:
                stale.append(f"{name}: not in the manifest")
            elif was["text"] != row["text"]:
                stale.append(f"{name}: the text changed and the audio did not")
            elif was["sha256"] != row["sha256"]:
                stale.append(f"{name}: the file changed and the manifest did not")
        for name in old.get("files", {}):
            if name not in manifest["files"]:
                stale.append(f"{name}: in the manifest and not in the line set")
        for one in stale:
            print(one)
        print(f"{len(manifest['files'])} file(s) checked, {len(stale)} stale")
        return 1 if stale else 0
    MANIFEST.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", "utf-8")
    total = sum(row["bytes"] for row in manifest["files"].values())
    print(f"{len(manifest['files'])} file(s), {total} bytes, {tool_version}")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="report staleness, change nothing")
    raise SystemExit(build(parser.parse_args().check))
