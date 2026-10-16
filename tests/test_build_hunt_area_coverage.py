"""T-17b: tests for scripts/build_hunt_area_coverage.py and the committed coverage file.

The synthetic fixture (`tests/fixtures/hunt_area_sample.json`) is three 0.1° GMU squares and
hunt areas whose overlaps are hand-derivable, so the expectations here are independent of the
script: full containment is exactly 1.0, a meridian-split half is 0.5 within projection
distortion, and the deliberate sliver must be dropped.

The real-file assertions are the ticket's GIS acceptance criteria. They read the committed
`static/data/hunt_area_coverage/2026.json` and never touch the network.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURE = REPO_ROOT / "tests" / "fixtures" / "hunt_area_sample.json"
COVERAGE_2026 = REPO_ROOT / "static" / "data" / "hunt_area_coverage" / "2026.json"


def _load_build_module():
    spec = importlib.util.spec_from_file_location(
        "build_hunt_area_coverage", REPO_ROOT / "scripts" / "build_hunt_area_coverage.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


build = _load_build_module()


@pytest.fixture(scope="module")
def sample() -> dict[str, Any]:
    raw = json.loads(FIXTURE.read_text())
    return build.build_coverage(raw, 2026, species=("deer", "elk"), generated="T")


# ------------------------------------------------------------------ year selection


@pytest.mark.parametrize(
    ("years", "license_year", "expected"),
    [
        ([2020, 2023, 2025], 2026, 2025),  # no 2026 polygons -> newest older year
        ([2020, 2023, 2025], 2025, 2025),  # exact match wins
        ([2020, 2023, 2025], 2024, 2023),  # never use geometry from the future
        ([2027], 2026, 2027),  # only newer geometry exists: use it rather than nothing
    ],
)
def test_geometry_year_picks_newest_not_after_license_year(years, license_year, expected):
    assert build.geometry_year(years, license_year) == expected


def test_geometry_year_requires_a_year():
    with pytest.raises(ValueError):
        build.geometry_year([], 2026)


def test_round_fraction_never_collapses_a_kept_overlap_to_zero():
    assert build.round_fraction(0.43879) == 0.4388
    assert build.round_fraction(1.0) == 1.0
    tiny = build.round_fraction(2.5e-05)
    assert tiny > 0 and tiny == pytest.approx(2.5e-05, rel=0.1)


# ------------------------------------------------------------------- synthetic build


def test_area_covering_a_whole_unit_is_exactly_full(sample):
    assert sample["species"]["deer"]["A-full"]["units"] == {"1": 1.0}


def test_half_an_area_is_about_half(sample):
    units = sample["species"]["deer"]["A-half"]["units"]
    assert set(units) == {"2"}
    assert units["2"] == pytest.approx(0.5, abs=0.01)


def test_multipart_features_are_unioned_into_one_area(sample):
    # Two features, one per unit, each the northern half of its square.
    units = sample["species"]["deer"]["A-multipart"]["units"]
    assert sorted(units) == ["1", "2"]
    assert all(frac == pytest.approx(0.5, abs=0.01) for frac in units.values())


def test_small_area_fully_inside_a_unit_survives_the_sliver_cut(sample):
    # 2.5e-05 of the GMU: dropped by §12's one-sided rule, kept because all of it is in unit 3.
    units = sample["species"]["deer"]["A-tiny"]["units"]
    assert sorted(units) == ["3"]
    assert 0 < units["3"] < build.KEEP_UNIT_FRAC


def test_boundary_sliver_is_dropped(sample):
    # A-noise laps 0.0002° into unit 2: 0.2 % of unit 2 and 0.2 % of itself, so neither rule
    # keeps it, while the unit it really covers stays full.
    assert sample["species"]["deer"]["A-noise"]["units"] == {"3": 1.0}


def test_superseded_year_is_ignored_per_species(sample):
    # 'A-full' also exists as a 2023 polygon over unit 3; only the 2025 one is used.
    assert sample["species"]["deer"]["A-full"]["geometry_year"] == 2025
    assert "3" not in sample["species"]["deer"]["A-full"]["units"]


def test_species_without_recent_polygons_falls_back_to_its_own_newest_year(sample):
    elk = sample["species"]["elk"]
    assert list(elk) == ["E-1"]  # the service's trailing space in 'E-1 ' is stripped
    assert elk["E-1"]["geometry_year"] == 2023


def test_summary_flags_stale_geometry_and_empty_areas(sample):
    lines = build.summarize(sample)
    assert any("deer: 5 areas, geometry 2025 (stale: license year is 2026)" == x for x in lines)
    assert not any("resolve to no unit" in line for line in lines)


def test_unknown_species_is_rejected(tmp_path):
    assert build.main(["--year", "2026", "--species", "jackalope"]) == 2


def test_check_mode_reports_a_missing_file(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(build, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(build, "output_path", lambda year: tmp_path / f"{year}.json")
    assert build.main(["--year", "1999", "--input", str(FIXTURE), "--check"]) == 1
    assert "missing" in capsys.readouterr().err


def test_write_then_check_round_trips(tmp_path, monkeypatch):
    monkeypatch.setattr(build, "output_path", lambda year: tmp_path / f"{year}.json")
    assert build.main(["--year", "2026", "--input", str(FIXTURE)]) == 0
    assert build.main(["--year", "2026", "--input", str(FIXTURE), "--check"]) == 0
    written = json.loads((tmp_path / "2026.json").read_text())
    assert written["license_year"] == 2026
    assert written["species"]["deer"]["A-full"]["units"] == {"1": 1.0}


# ------------------------------------------------------- committed 2026 coverage file


@pytest.fixture(scope="module")
def committed() -> dict[str, Any]:
    return json.loads(COVERAGE_2026.read_text())


def test_committed_file_shape(committed):
    assert committed["license_year"] == 2026
    assert sorted(committed["species"]) == ["deer", "elk", "pronghorn"]
    assert COVERAGE_2026.stat().st_size < 150 * 1024


def test_every_hunt_area_resolves_to_at_least_one_unit(committed):
    empty = [
        f"{species}/{area}"
        for species, areas in committed["species"].items()
        for area, entry in areas.items()
        if not entry["units"]
    ]
    assert empty == []


def test_2026_uses_2025_geometry_everywhere(committed):
    years = {
        entry["geometry_year"]
        for areas in committed["species"].values()
        for entry in areas.values()
    }
    assert years == {2025}  # every area is a geometry_year < license_year review flag


def test_fractions_are_in_range(committed):
    for areas in committed["species"].values():
        for entry in areas.values():
            for unit, frac in entry["units"].items():
                assert unit and 0 < frac <= 1.0


def test_acceptance_deer_1_1_covers_its_nine_units(committed):
    units = committed["species"]["deer"]["1-1"]["units"]
    assert sorted(units) == ["1", "2", "3", "4", "4A", "5", "6", "7", "9"]
    assert all(frac == 1.0 for frac in units.values())


def test_acceptance_deer_11_is_exactly_unit_11_full(committed):
    units = committed["species"]["deer"]["11"]["units"]
    assert list(units) == ["11"]
    assert units["11"] >= 0.98  # §12 step 3: >= 0.98 -> coverage 'full'


def test_acceptance_deer_38_1_is_unit_38_partial(committed):
    units = committed["species"]["deer"]["38-1"]["units"]
    assert list(units) == ["38"]
    assert 0 < units["38"] < 0.98  # -> coverage 'partial' with this fraction


def test_committed_units_exist_in_the_seeded_unit_list(committed):
    known = {u["id"] for u in json.loads((REPO_ROOT / "static/data/units_seed.json").read_text())}
    used = {
        unit
        for areas in committed["species"].values()
        for entry in areas.values()
        for unit in entry["units"]
    }
    assert used <= known
