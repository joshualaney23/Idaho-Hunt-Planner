"""planner/dates.py against the shared fixture (T-05).

static/js/dates.test.mjs runs the same fixture, so the twins can't diverge.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from datetime import date
from pathlib import Path

import pytest

from planner import dates

ROOT = Path(__file__).resolve().parent.parent
CASES = json.loads((ROOT / "tests" / "fixtures" / "dates_cases.json").read_text())
WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def _iso_range(r: tuple[date, date] | None) -> list[str] | None:
    return None if r is None else [dates.to_iso(r[0]), dates.to_iso(r[1])]


def _id(case: dict) -> str:
    return f"{case['today']}-{case.get('weekday', '')}-{case['weekend_def']}"


def test_fixture_covers_every_weekday_for_both_weekend_defs() -> None:
    covered = {(c["weekday"], c["weekend_def"]) for c in CASES["default_range"]}
    assert covered >= {(w, d) for w in WEEKDAYS for d in dates.WEEKEND_DEFS}


def test_fixture_has_dst_boundary_case() -> None:
    # 2026-11-01 is the first Sunday of November (US fall-back).
    assert date(2026, 11, 1).weekday() == 6
    touched = {d for c in CASES["default_range"] for d in [c["today"], *c["expect"]]}
    assert "2026-11-01" in touched


@pytest.mark.parametrize("case", CASES["default_range"], ids=_id)
def test_default_range(case: dict) -> None:
    today = dates.parse_iso(case["today"])
    assert WEEKDAYS[today.weekday()] == case["weekday"], "fixture weekday label is wrong"
    assert _iso_range(dates.default_range(today, case["weekend_def"])) == case["expect"]


def test_default_weekend_def_is_fri_sun() -> None:
    assert dates.DEFAULT_WEEKEND_DEF == "fri_sun"
    wed = date(2026, 10, 14)
    assert dates.default_range(wed) == dates.default_range(wed, "fri_sun")


def test_unknown_weekend_def_raises() -> None:
    with pytest.raises(ValueError):
        dates.default_range(date(2026, 10, 14), "thu_sun")


@pytest.mark.parametrize("case", CASES["presets"], ids=_id)
def test_presets(case: dict) -> None:
    got = dates.presets(dates.parse_iso(case["today"]), case["weekend_def"], case["seasons"])
    assert {k: _iso_range(v) for k, v in got.items()} == case["expect"]


def test_opening_day_accepts_date_objects() -> None:
    seasons = [{"start_date": date(2026, 10, 25)}, {"start": "2026-11-01"}]
    assert dates.opening_day(seasons, date(2026, 10, 14)) == (date(2026, 10, 25),) * 2


@pytest.mark.parametrize("case", CASES["overlap"], ids=lambda c: f"{c['a']}x{c['b']}")
def test_overlap(case: dict) -> None:
    a1, a2, b1, b2 = map(dates.parse_iso, [*case["a"], *case["b"]])
    assert dates.overlap(a1, a2, b1, b2) == case["expect"]
    assert dates.overlap(b1, b2, a1, a2) == case["expect"]  # symmetric


@pytest.mark.parametrize("case", CASES["days"], ids=lambda c: f"{c['a']}..{c['b']}")
def test_days(case: dict) -> None:
    assert dates.days(dates.parse_iso(case["a"]), dates.parse_iso(case["b"])) == case["expect"]


@pytest.mark.parametrize("case", CASES["fmt_short"], ids=lambda c: c["date"])
def test_fmt_short(case: dict) -> None:
    assert dates.fmt_short(dates.parse_iso(case["date"])) == case["expect"]


@pytest.mark.parametrize("bad", CASES["parse_iso_invalid"])
def test_parse_iso_rejects(bad: str) -> None:
    with pytest.raises(ValueError):
        dates.parse_iso(bad)


def test_iso_round_trip() -> None:
    assert dates.to_iso(dates.parse_iso("2027-01-03")) == "2027-01-03"


# The JS twin runs the same fixture. Several time zones, so DST and UTC offset bugs show up.
TIME_ZONES = ["America/Boise", "America/Los_Angeles", "UTC", "Pacific/Kiritimati"]


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed (CI runs it)")
@pytest.mark.parametrize("tz", TIME_ZONES)
def test_js_twin_passes_same_fixture(tz: str) -> None:
    proc = subprocess.run(
        ["node", "--test", "static/js/dates.test.mjs"],
        cwd=ROOT,
        env={**os.environ, "TZ": tz},
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
