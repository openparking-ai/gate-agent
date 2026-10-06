#!/usr/bin/env python3
"""The characters an owner may type for a lane's screen are the ones this font draws.

**ONE LIST, TWO COPIES, CHECKED AGAINST EACH OTHER.** The platform refuses, at
the moment an owner closes a lane or writes a board message, any character this
display cannot draw (U4c, rule 7). It cannot import this package -- the two are
separate repositories in separate languages -- so it holds a COPY,
`src/screen-characters.json` in `openparking-ai/platform`, and this script
compares that copy to `font.DRAWABLE` here. The platform runs the same
comparison the other way round against this repository's `font.py`. A character
added to either side alone goes red on that side's next run:

  * one the font draws and the platform refuses is an owner told no for a
    message the screen could show;
  * one the platform accepts and the font cannot draw is a message the screen
    shows with a letter missing -- the failure this list exists to prevent.

**PINNED to a platform commit (`PLATFORM_COMMIT`)**, never to `main`: a moving
target on the other side of a boundary is a check that changes meaning without a
commit here. `--from-file` compares against a local copy instead, which is how
the plants in `tests/test_screen_characters.py` are measured without a network.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from gate_agent.font import DRAWABLE  # noqa: E402

#: The platform commit whose copy this build is held to.
PLATFORM_COMMIT = "9a68e4edb76a3ee30cfa6bd47635bbc8c0af8f87"
PLATFORM_PATH = "src/screen-characters.json"
URL = "https://raw.githubusercontent.com/openparking-ai/platform/{commit}/{path}"


def platform_copy(text: str) -> frozenset[str]:
    """The characters the platform's copy lists, or a `ValueError` naming why not."""
    document = json.loads(text)
    characters = document.get("characters") if isinstance(document, dict) else None
    if not isinstance(characters, str) or not characters:
        raise ValueError(f"{PLATFORM_PATH} carries no `characters` string")
    if len(set(characters)) != len(characters):
        raise ValueError(f"{PLATFORM_PATH} lists a character twice")
    return frozenset(characters)


def compare(theirs: frozenset[str], ours: frozenset[str] = DRAWABLE) -> list[str]:
    """Every difference, named. Empty is a match."""
    problems = []
    for character in sorted(theirs - ours):
        problems.append(f"the platform accepts {character!r} and this font cannot draw it")
    for character in sorted(ours - theirs):
        problems.append(f"this font draws {character!r} and the platform refuses it")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--from-file", type=Path,
                        help="compare against this local copy instead of the pinned commit")
    args = parser.parse_args(argv)
    if args.from_file is not None:
        text, where = args.from_file.read_text(encoding="utf-8"), str(args.from_file)
    else:
        url = URL.format(commit=PLATFORM_COMMIT, path=PLATFORM_PATH)
        with urllib.request.urlopen(url, timeout=30) as response:  # noqa: S310 - fixed https URL
            text, where = response.read().decode("utf-8"), url
    try:
        problems = compare(platform_copy(text))
    except ValueError as exc:
        print(f"FAIL: {where}: {exc}")
        return 1
    if problems:
        print(f"FAIL: the platform's list ({where}) and font.DRAWABLE disagree:")
        for problem in problems:
            print(f"  {problem}")
        return 1
    print(f"OK: {len(DRAWABLE)} characters, the same on both sides ({where})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
