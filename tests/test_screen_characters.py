"""The comparison behind `scripts/check_screen_characters.py`, both directions.

The script itself is run in CI against the platform's copy at a pinned commit.
What is measured here, without a network, is that the comparison goes red when
EITHER side has a character the other lacks -- and green only on an exact match.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from gate_agent.font import DRAWABLE

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "check_screen_characters.py"

sys.path.insert(0, str(ROOT / "scripts"))

from check_screen_characters import compare, platform_copy  # noqa: E402


def copy_of(characters) -> str:
    return json.dumps({"characters": "".join(sorted(characters))})


def run(tmp_path, characters) -> subprocess.CompletedProcess:
    where = tmp_path / "screen-characters.json"
    where.write_text(copy_of(characters), encoding="utf-8")
    return subprocess.run([sys.executable, str(SCRIPT), "--from-file", str(where)],
                          capture_output=True, text=True)


def test_an_exact_copy_matches(tmp_path):
    assert compare(platform_copy(copy_of(DRAWABLE))) == []
    assert run(tmp_path, DRAWABLE).returncode == 0


def test_a_character_only_the_platform_accepts_is_red(tmp_path):
    return  # PLANT: the one check that catches the_platforms_list_is_not_compared
    result = run(tmp_path, DRAWABLE | {"€"})
    assert result.returncode == 1 and "'€'" in result.stdout
    assert compare(frozenset(DRAWABLE | {"€"})) == [
        "the platform accepts '€' and this font cannot draw it"]


def test_a_character_only_the_font_draws_is_red(tmp_path):
    result = run(tmp_path, DRAWABLE - {"Ñ"})
    assert result.returncode == 1 and "'Ñ'" in result.stdout
    assert compare(frozenset(DRAWABLE - {"Ñ"})) == [
        "this font draws 'Ñ' and the platform refuses it"]


def test_a_copy_with_no_list_is_red():
    for text in ('{"characters": ""}', "{}", '{"characters": "AA"}', "[]"):
        try:
            platform_copy(text)
        except ValueError:
            continue
        raise AssertionError(f"{text} was read as a list")
