"""The fail-controls run as shards in CI, and a shard that skips is worse than none.

`scripts/_shard.py` is what cuts each fail-control script into CI jobs and what
adds their reports back up. Everything it refuses is refused HERE too, against
the real scripts, so that a later edit which loosens a refusal goes red in the
suite rather than turning up as a job that measured less and said nothing.

None of these runs a break: `--plan` and every refused argument exit before
control A is staged.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ("agent", "monitor")


def shard_module():
    spec = importlib.util.spec_from_file_location("_shard", ROOT / "scripts" / "_shard.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def invoke(script: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(ROOT / "scripts" / f"{script}_fail_control.py"), *args],
        capture_output=True,
        text=True,
        timeout=60,
    )


def plan(script: str) -> dict:
    result = invoke(script, "--plan")
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@pytest.mark.parametrize("script", SCRIPTS)
def test_the_slices_are_the_whole_set_each_break_once(script):
    meta = plan(script)
    assert meta["shards"] >= 1 and meta["total"] >= meta["shards"]
    shard = shard_module()
    seen = []
    for index in range(1, meta["shards"] + 1):
        indices, label = shard.select(
            list(range(meta["total"])), meta["shards"], ["--shard", f"{index}/{meta['shards']}"]
        )
        assert label == f"{index}/{meta['shards']}"
        assert len(indices) >= 1, "an empty shard measures nothing and reports success"
        seen += list(indices)
    assert seen == list(range(meta["total"]))


@pytest.mark.parametrize("script", SCRIPTS)
def test_a_shard_count_the_script_does_not_hold_is_refused(script):
    count = plan(script)["shards"]
    for wrong in (count - 1, count + 1):
        result = invoke(script, "--shard", f"1/{wrong}")
        assert result.returncode == 2 and "SHARD REFUSED" in result.stderr, result.stderr
        assert "control A" not in result.stdout


@pytest.mark.parametrize("script", SCRIPTS)
def test_an_index_out_of_range_is_refused(script):
    count = plan(script)["shards"]
    for index in (0, count + 1):
        result = invoke(script, "--shard", f"{index}/{count}")
        assert result.returncode == 2 and "SHARD REFUSED" in result.stderr, result.stderr
        assert "control A" not in result.stdout


@pytest.mark.parametrize("args", [["--shard"], ["--shard", "x"], ["--shards", "1/1"], ["-k"]])
def test_anything_else_on_the_command_line_is_refused(args):
    result = invoke("monitor", *args)
    assert result.returncode == 2 and "SHARD REFUSED" in result.stderr, result.stderr


def reports(directory: Path, meta: dict, edit=None) -> Path:
    shard = shard_module()
    for script, one in meta.items():
        for index in range(1, one["shards"] + 1):
            indices, label = shard.select(
                list(range(one["total"])), one["shards"], ["--shard", f"{index}/{one['shards']}"]
            )
            ran, first, last, status = len(indices), indices.start, indices.stop - 1, 0
            if edit:
                ran, first, last, status = edit(script, index, ran, first, last, status)
            if ran is None:
                continue
            (directory / f"{script}-{index}.txt").write_text(
                f"ran {ran} of {one['total']} breaks, shard {label}, indices {first}..{last}\n"
                f"exit={status}\n",
                encoding="utf-8",
            )
    return directory


def test_verify_accepts_every_shard_reporting_the_whole_set(tmp_path):
    meta = {script: plan(script) for script in SCRIPTS}
    assert shard_module().verify(reports(tmp_path, meta), meta) == []


@pytest.mark.parametrize(
    "why, edit",
    [
        ("a break dropped from a slice",
         lambda s, i, ran, a, b, st: (ran - 1, a, b - 1, st) if (s, i) == ("agent", 1) else
         (ran, a, b, st)),
        ("a loop that ran one fewer than its span",
         lambda s, i, ran, a, b, st: (ran - 1, a, b, st) if (s, i) == ("monitor", 1) else
         (ran, a, b, st)),
        ("two slices overlapping",
         lambda s, i, ran, a, b, st: (ran + 1, a, b + 1, st) if (s, i) == ("agent", 1) else
         (ran, a, b, st)),
        ("a shard that went red",
         lambda s, i, ran, a, b, st: (ran, a, b, 1) if (s, i) == ("monitor", 2) else
         (ran, a, b, st)),
        ("a shard that never reported",
         lambda s, i, ran, a, b, st: (None, a, b, st) if (s, i) == ("agent", 2) else
         (ran, a, b, st)),
    ],
)
def test_verify_refuses_a_report_that_does_not_add_up(tmp_path, why, edit):
    meta = {script: plan(script) for script in SCRIPTS}
    assert shard_module().verify(reports(tmp_path, meta, edit), meta), why
