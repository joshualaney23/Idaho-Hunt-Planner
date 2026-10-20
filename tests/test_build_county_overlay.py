"""T-14: tests for scripts/build_county_overlay.py and the committed county overlay.

The synthetic fixture (`tests/fixtures/county_sample.json`) is three 0.1° GMU squares and
counties whose overlaps are hand-derivable, so the expectations here are independent of the
script: a unit wholly inside one county is 1.0, a meridian-split half is 0.5 within
projection distortion, a two-feature (multipart) county is unioned before measuring, and the
deliberate border sliver must be dropped.

The real-file assertions read the committed `static/data/county_units.json` and never touch
the network. The strongest of them is arithmetic the build cannot fake: the 44 counties tile
Idaho exactly, so every unit's fractions must add up to 1.
"""

from __future__ import annotations

import importlib.util
import io
import json
import zipfile
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURE = REPO_ROOT / "tests" / "fixtures" / "county_sample.json"
COUNTY_UNITS = REPO_ROOT / "static" / "data" / "county_units.json"


def _load_build_module():
    spec = importlib.util.spec_from_file_location(
        "build_county_overlay", REPO_ROOT / "scripts" / "build_county_overlay.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


build = _load_build_module()


@pytest.fixture(scope="module")
def sample() -> dict[str, Any]:
    raw = json.loads(FIXTURE.read_text())
    return build.build_overlay(raw, generated="T")


# ------------------------------------------------------------------- synthetic build


def test_unit_wholly_inside_one_county_is_full(sample):
    # Projection distortion: the two polygons share both endpoints of unit 1's long edges,
    # but their top/bottom chords along the 45.1° parallel differ, which costs ~1e-4.
    assert sample["counties"]["Alpha"]["1"] == pytest.approx(1.0, abs=0.001)


def test_unit_split_between_two_counties_is_half_in_each(sample):
    assert sample["counties"]["Alpha"]["2"] == pytest.approx(0.5, abs=0.01)
    assert sample["counties"]["Beta"]["2"] == pytest.approx(0.5, abs=0.01)


def test_multipart_county_features_are_unioned(sample):
    # Gamma arrives as two features (north and south quarter of unit 3), measured as one.
    assert list(sample["counties"]["Gamma"]) == ["3"]
    assert sample["counties"]["Gamma"]["3"] == pytest.approx(0.5, abs=0.01)


def test_border_sliver_is_dropped(sample):
    # Noise laps 0.00005° over unit 1's western border: 0.05 % of the unit, under the cut.
    assert sample["counties"]["Noise"] == {}


def test_every_unit_is_fully_accounted_for_across_counties(sample):
    totals = build.unit_totals(sample)
    assert sorted(totals) == ["1", "2", "3"]
    assert all(total == pytest.approx(1.0, abs=0.01) for total in totals.values())


def test_summary_flags_a_short_county_list_an_empty_county_and_a_missing_unit(sample):
    lines = build.summarize(sample, known_units=["1", "2", "3", "99"])
    assert lines[0].startswith("4 counties, 3 units, 5 county/unit pairs")
    assert any("expected 44 Idaho counties, got 4" in line for line in lines)
    assert any("cover no unit: Noise" in line for line in lines)
    assert any("99" in line and "absent" in line for line in lines)


def test_round_fraction_never_collapses_a_kept_overlap_to_zero():
    assert build.round_fraction(0.43879) == 0.4388
    assert build.round_fraction(1.0) == 1.0
    tiny = build.round_fraction(2.5e-05)
    assert tiny > 0 and tiny == pytest.approx(2.5e-05, rel=0.1)


def test_gmu_feature_without_a_name_is_rejected():
    with pytest.raises(ValueError, match="NAME"):
        build.build_overlay({"gmu": [{"properties": {}, "geometry": None}], "counties": []})


def test_county_overlay_needs_both_layers():
    raw = json.loads(FIXTURE.read_text())
    with pytest.raises(ValueError, match="no GMU features"):
        build.build_overlay({"gmu": [], "counties": raw["counties"]})
    with pytest.raises(ValueError, match="no county features"):
        build.build_overlay({"gmu": raw["gmu"], "counties": []})


# ------------------------------------------------------------- Census shapefile reading


def _county_zip(rows: list[tuple[str, str]]) -> bytes:
    """A minimal `cb_*_us_county_500k.zip` holding one square per (STATEFP, NAME) row."""
    import shapefile

    buffers = {ext: io.BytesIO() for ext in ("shp", "shx", "dbf")}
    writer = shapefile.Writer(**buffers)
    writer.field("STATEFP", "C", 2)
    writer.field("GEOID", "C", 5)
    writer.field("NAME", "C", 40)
    for index, (statefp, name) in enumerate(rows):
        writer.poly([[[index, 0], [index + 1, 0], [index + 1, 1], [index, 1], [index, 0]]])
        writer.record(statefp, f"{statefp}{index:03d}", name)
    writer.close()

    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as zf:
        for ext, buffer in buffers.items():
            zf.writestr(f"cb_2024_us_county_500k.{ext}", buffer.getvalue())
    return archive.getvalue()


def test_county_features_keeps_only_the_requested_state():
    payload = _county_zip([("16", "Ada"), ("41", "Baker"), ("16", "Nez Perce")])
    features = build.county_features(payload)
    assert [f["properties"]["NAME"] for f in features] == ["Ada", "Nez Perce"]
    assert features[0]["geometry"]["type"] in {"Polygon", "MultiPolygon"}


def test_county_archive_without_a_shapefile_is_rejected():
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("readme.txt", "nope")
    with pytest.raises(ValueError, match="missing"):
        build.county_features(archive.getvalue())


# ------------------------------------------------------------------------- CLI modes


def test_check_mode_reports_a_missing_file(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(build, "OUTPUT_PATH", tmp_path / "county_units.json")
    assert build.main(["--input", str(FIXTURE), "--check"]) == 1
    assert "missing" in capsys.readouterr().err


def test_check_mode_reports_a_stale_file(tmp_path, monkeypatch, capsys):
    path = tmp_path / "county_units.json"
    path.write_text(build.dumps({"vintage": 2024, "counties": {}}))
    monkeypatch.setattr(build, "OUTPUT_PATH", path)
    assert build.main(["--input", str(FIXTURE), "--check"]) == 1
    assert "stale" in capsys.readouterr().err


def test_write_then_check_round_trips(tmp_path, monkeypatch):
    path = tmp_path / "county_units.json"
    monkeypatch.setattr(build, "OUTPUT_PATH", path)
    assert build.main(["--input", str(FIXTURE)]) == 0
    assert build.main(["--input", str(FIXTURE), "--check"]) == 0
    written = json.loads(path.read_text())
    assert written["vintage"] == build.DEFAULT_VINTAGE
    assert written["counties"]["Beta"]["3"] == pytest.approx(0.5, abs=0.01)


def test_save_raw_writes_the_input_back(tmp_path, monkeypatch):
    monkeypatch.setattr(build, "OUTPUT_PATH", tmp_path / "county_units.json")
    raw_out = tmp_path / "raw.json"
    assert build.main(["--input", str(FIXTURE), "--save-raw", str(raw_out)]) == 0
    assert json.loads(raw_out.read_text()).keys() == {"gmu", "counties"}


# ----------------------------------------------------- committed county_units.json


@pytest.fixture(scope="module")
def committed() -> dict[str, Any]:
    return json.loads(COUNTY_UNITS.read_text())


def test_committed_file_shape(committed):
    assert committed["vintage"] == build.DEFAULT_VINTAGE
    assert "Census" in committed["source"] and "MapServer/3" in committed["source"]
    assert len(committed["counties"]) == build.IDAHO_COUNTY_COUNT
    assert COUNTY_UNITS.stat().st_size < 150 * 1024


def test_committed_counties_are_the_44_idaho_counties(committed):
    # Spot-checked against the Census county list: the alphabetical ends and three in between.
    names = sorted(committed["counties"])
    assert names[0] == "Ada" and names[-1] == "Washington"
    assert {"Nez Perce", "Bear Lake", "Twin Falls"} <= set(names)


def test_every_county_covers_at_least_one_unit(committed):
    assert [c for c, units in committed["counties"].items() if not units] == []


def test_every_unit_area_is_fully_accounted_for(committed):
    # The counties tile the state, so each unit's fractions must sum to 1 (±1 %). This is the
    # build's independent correctness check: a dropped or double-counted county breaks it.
    totals = build.unit_totals(committed)
    assert len(totals) == 100
    off = {unit: total for unit, total in totals.items() if abs(total - 1.0) > 0.01}
    assert off == {}


def test_fractions_are_in_range(committed):
    for units in committed["counties"].values():
        for unit, frac in units.items():
            assert unit and 0 < frac <= 1.0


def test_committed_units_match_the_seeded_unit_list(committed):
    known = {u["id"] for u in json.loads((REPO_ROOT / "static/data/units_seed.json").read_text())}
    used = {unit for units in committed["counties"].values() for unit in units}
    assert used == known


def test_spot_check_owyhee_county_holds_three_whole_units(committed):
    # Units 40, 41 and 42 lie wholly inside Owyhee County (IDFG unit maps).
    owyhee = committed["counties"]["Owyhee"]
    assert all(owyhee[unit] >= 0.98 for unit in ("40", "41", "42"))


def test_spot_check_a_unit_split_across_counties(committed):
    # Unit 1 is the northern panhandle: Boundary County plus the top of Bonner County.
    shares = {county: units["1"] for county, units in committed["counties"].items() if "1" in units}
    assert sorted(shares) == ["Bonner", "Boundary"]
    assert sum(shares.values()) == pytest.approx(1.0, abs=0.01)
