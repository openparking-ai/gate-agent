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


def breaks_of(meta: dict) -> list[dict]:
    return [{"name": name} for name in meta["names"]]


@pytest.mark.parametrize("script", SCRIPTS)
def test_the_slices_are_the_whole_set_each_break_once(script):
    meta = plan(script)
    assert meta["shards"] >= 1 and meta["total"] >= meta["shards"]
    assert len(meta["names"]) == meta["total"] == len(set(meta["names"]))
    shard = shard_module()
    seen = []
    for index in range(1, meta["shards"] + 1):
        indices, label = shard.select(
            breaks_of(meta), script, ["--shard", f"{index}/{meta['shards']}"]
        )
        assert label == f"{index}/{meta['shards']}"
        assert len(indices) >= 1, "an empty shard measures nothing and reports success"
        seen += list(indices)
    assert sorted(seen) == list(range(meta["total"])) and len(seen) == meta["total"]


@pytest.mark.parametrize("script", SCRIPTS)
def test_a_break_with_a_time_and_no_place_in_the_script_is_refused_by_name(script):
    """A break deleted from `BREAKS` cannot leave quietly: its measured time names it."""
    names = plan(script)["names"]
    shard = shard_module()
    times = shard.measured(script)
    times["breaks"][names[0]] = 60.0
    with pytest.raises(SystemExit) as refused:
        shard.cut(names[1:], times)
    assert refused.value.code == 2


def test_the_refusal_names_the_deleted_break(capsys):
    shard = shard_module()
    with pytest.raises(SystemExit):
        shard.cut(["kept"], {"control_a": 60.0, "breaks": {"kept": 60.0, "gone_away": 60.0}})
    assert "gone_away" in capsys.readouterr().err


def test_a_new_break_adds_to_one_shard_and_moves_no_other():
    shard = shard_module()
    names = [f"b{i}" for i in range(12)]
    times = {"control_a": 30.0, "breaks": {name: 10.0 + 7 * i for i, name in enumerate(names)}}
    before = shard.cut(names, times, jobs=2, budget=260.0)
    after = shard.cut([*names, "a_break_nobody_has_timed"], times, jobs=2, budget=260.0)
    assert len(after) == len(before) > 1, "the budget leaves room, so the count must hold"
    grown = [index for index, one in enumerate(after) if one != before[index]]
    assert len(grown) == 1, (before, after)
    assert set(after[grown[0]]) - set(before[grown[0]]) == {len(names)}


def test_a_shard_is_cut_by_its_cost_not_its_count():
    """Two costly breaks and four cheap ones: the costly two do not share a shard."""
    shard = shard_module()
    names = ["slow_a", "slow_b", "q1", "q2", "q3", "q4"]
    times = {"control_a": 10.0, "breaks": {"slow_a": 100.0, "slow_b": 100.0,
                                           "q1": 10.0, "q2": 10.0, "q3": 10.0, "q4": 10.0}}
    shards = shard.cut(names, times, jobs=1, budget=150.0)
    assert len(shards) == 2
    assert all(len({0, 1} & set(one)) == 1 for one in shards), shards


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
                breaks_of(one), script, ["--shard", f"{index}/{one['shards']}"]
            )
            ran, names, status = len(indices), [one["names"][i] for i in indices], 0
            if edit:
                ran, names, status = edit(script, index, ran, names, status)
            if ran is None:
                continue
            body = "".join(f"break {name} 61.5s\n" for name in names)
            (directory / f"{script}-{index}.txt").write_text(
                f"ran {ran} of {one['total']} breaks, shard {label}\n"
                f"control_a 70.0s\n{body}exit={status}\n",
                encoding="utf-8",
            )
    return directory


def test_verify_accepts_every_shard_reporting_the_whole_set(tmp_path):
    meta = {script: plan(script) for script in SCRIPTS}
    assert shard_module().verify(reports(tmp_path, meta), meta) == []


def first_of(meta: dict, script: str) -> str:
    return meta[script]["names"][shard_module().select(
        breaks_of(meta[script]), script, ["--shard", f"1/{meta[script]['shards']}"]
    )[0][0]]


@pytest.mark.parametrize(
    "why, edit",
    [
        ("a break dropped from a slice",
         lambda s, i, ran, n, st: (ran - 1, n[1:], st) if (s, i) == ("agent", 1) else
         (ran, n, st)),
        ("a loop that ran one fewer than it named",
         lambda s, i, ran, n, st: (ran - 1, n, st) if (s, i) == ("monitor", 1) else
         (ran, n, st)),
        ("one break in two slices",
         lambda s, i, ran, n, st: (ran + 1, [*n, "SECOND"], st) if (s, i) == ("agent", 2) else
         (ran, n, st)),
        ("a break the plan does not hold",
         lambda s, i, ran, n, st: (ran, [*n[:-1], "invented"], st) if (s, i) == ("agent", 1)
         else (ran, n, st)),
        ("a shard that went red",
         lambda s, i, ran, n, st: (ran, n, 1) if (s, i) == ("monitor", 2) else
         (ran, n, st)),
        ("a shard that never reported",
         lambda s, i, ran, n, st: (None, n, st) if (s, i) == ("agent", 2) else
         (ran, n, st)),
    ],
)
def test_verify_refuses_a_report_that_does_not_add_up(tmp_path, why, edit):
    meta = {script: plan(script) for script in SCRIPTS}
    agent_first = first_of(meta, "agent")

    def named(s, i, ran, n, st):
        ran, n, st = edit(s, i, ran, n, st)
        return ran, [agent_first if one == "SECOND" else one for one in n], st

    assert shard_module().verify(reports(tmp_path, meta, named), meta), why


def test_the_add_up_names_the_break_that_never_ran(tmp_path):
    meta = {script: plan(script) for script in SCRIPTS}
    dropped = first_of(meta, "agent")

    def edit(s, i, ran, n, st):
        return (ran - 1, [x for x in n if x != dropped], st) if s == "agent" else (ran, n, st)

    problems = shard_module().verify(reports(tmp_path, meta, edit), meta)
    assert any(dropped in one and "never ran" in one for one in problems), problems


def test_the_add_up_names_the_break_that_ran_twice(tmp_path):
    meta = {script: plan(script) for script in SCRIPTS}
    doubled = first_of(meta, "agent")

    def edit(s, i, ran, n, st):
        return (ran + 1, [*n, doubled], st) if (s, i) == ("agent", 2) else (ran, n, st)

    problems = shard_module().verify(reports(tmp_path, meta, edit), meta)
    assert any(f"ran twice ['{doubled}']" in one for one in problems), problems


def test_a_break_longer_than_the_budget_gets_a_shard_not_every_break_one():
    shard = shard_module()
    names = ["huge", *(f"q{i}" for i in range(8))]
    times = {"control_a": 10.0, "breaks": {"huge": 500.0, **{f"q{i}": 10.0 for i in range(8)}}}
    shards = shard.cut(names, times, jobs=1, budget=100.0)
    assert len(shards) < len(names), shards
    assert [0] in shards, "the break that alone exceeds the budget shares its shard with nothing"
