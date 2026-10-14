"""T-02: tests for scripts/build_gmu_geometry.py and the committed GMU geometry.

The path decoder here is written independently of the encoder (maximal-munch SVG number
parsing, exactly what a browser does), so a round-trip failure means the committed `d`
strings would render wrong.
"""

from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path
from typing import Any

import pytest
from shapely.geometry import MultiPolygon, Point, Polygon
from shapely.ops import unary_union

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURE = REPO_ROOT / "tests" / "fixtures" / "gmu_sample.geojson"
GMU_JSON = REPO_ROOT / "static" / "data" / "gmu.json"
UNITS_SEED_JSON = REPO_ROOT / "static" / "data" / "units_seed.json"

SIZE_BUDGET_BYTES = 150 * 1024
EXPECTED_UNIT_COUNT = 100  # features returned by MapServer/3 as of 2026


def _load_build_module():
    spec = importlib.util.spec_from_file_location(
        "build_gmu_geometry", REPO_ROOT / "scripts" / "build_gmu_geometry.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


build = _load_build_module()

# SVG path number: maximal munch, so "1.5.4" reads as 1.5 then .4 (as browsers do).
NUMBER_RE = re.compile(r"-?(?:\d+\.\d+|\.\d+|\d+)")


def decode_path(d: str) -> list[list[tuple[float, float]]]:
    """Decode ``M<abs> l<deltas> Z`` subpaths back into rings of absolute points."""
    rings: list[list[tuple[float, float]]] = []
    for segment in d.split("Z"):
        if not segment.strip():
            continue
        assert segment.startswith("M"), f"subpath does not start with M: {segment[:20]!r}"
        cut = segment.index("l")
        start = NUMBER_RE.findall(segment[1:cut])
        assert len(start) == 2, f"bad moveto: {segment[:20]!r}"
        x, y = float(start[0]), float(start[1])
        points = [(x, y)]
        deltas = NUMBER_RE.findall(segment[cut + 1 :])
        assert len(deltas) % 2 == 0, "odd number of relative coordinates"
        it = iter(deltas)
        for dx, dy in zip(it, it, strict=True):
            x = round(x + float(dx), 1)
            y = round(y + float(dy), 1)
            points.append((x, y))
        rings.append(points)
    return rings


def rings_to_polygons(d: str) -> list[Polygon]:
    return [Polygon(r) for r in decode_path(d) if len(r) >= 3]


# ------------------------------------------------------------------ number encoding


@pytest.mark.parametrize(
    ("value", "expected"),
    [(12.0, "12"), (0.8, ".8"), (-0.8, "-.8"), (-12.0, "-12"), (0.0, "0"), (123.45, "123.5")],
)
def test_num_is_trimmed_one_decimal(value: float, expected: str) -> None:
    assert build._num(value) == expected


@pytest.mark.parametrize(
    ("tokens", "expected"),
    [
        (["12", ".5"], "12 .5"),  # must keep the space or it reads as 12.5
        (["1.5", ".4"], "1.5.4"),  # maximal munch makes this unambiguous
        (["1", "-2"], "1-2"),
        (["1", "2"], "1 2"),
        ([".5", ".5"], ".5.5"),
        (["-.5", "3"], "-.5 3"),
    ],
)
def test_join_numbers_is_unambiguous(tokens: list[str], expected: str) -> None:
    assert build._join_numbers(tokens) == expected
    assert [float(n) for n in NUMBER_RE.findall(expected)] == [float(t) for t in tokens]


def test_sort_key() -> None:
    assert build.sort_key("1") == 10
    assert build.sort_key("4A") == 41
    assert build.sort_key("10") == 100
    assert build.sort_key("10A") == 101
    assert build.sort_key("36B") == 362
    assert build.sort_key("YNP") == build.NON_NUMERIC_SORT_KEY
    names = ["10A", "YNP", "2", "10", "4A", "4"]
    assert sorted(names, key=build.sort_key) == ["2", "4", "4A", "10", "10A", "YNP"]


# ---------------------------------------------------------------- synthetic coverage


@pytest.fixture(scope="module")
def sample_payloads() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    return build.build_payloads(json.loads(FIXTURE.read_text()), generated="2026-01-01T00:00:00Z")


def test_sample_payload_shape(sample_payloads) -> None:
    payload, seed = sample_payloads
    assert payload["viewBox"] == "0 0 430 690"
    assert payload["source"] == "IDFG GIS MapServer/3"
    assert list(payload["units"]) == ["7", "7A", "YNP"]  # sorted by sort_key
    assert payload["units"]["7"]["zone"] == "Dworshak"
    assert payload["units"]["YNP"]["zone"] is None
    assert payload["units"]["YNP"]["closed"] is True
    assert "closed" not in payload["units"]["7"]
    assert [u["id"] for u in seed] == ["7", "7A", "YNP"]
    assert [u["sort_key"] for u in seed] == [70, 71, build.NON_NUMERIC_SORT_KEY]
    assert seed[0]["elk_zone"] == "Dworshak"
    # 0.5 deg x 0.5 deg at 45N is about 39 km x 56 km = ~2,180 km2 = ~540k acres.
    assert 500_000 < seed[0]["area_acres"] < 580_000


def test_sample_paths_round_trip(sample_payloads) -> None:
    payload, _ = sample_payloads
    for name, unit in payload["units"].items():
        rings = decode_path(unit["d"])
        assert rings, f"{name} produced no rings"
        for ring in rings:
            assert len(ring) >= 3
            for x, y in ring:
                assert round(x, 1) == x and round(y, 1) == y


def test_shared_boundary_has_no_gap(sample_payloads) -> None:
    """Units 7 and 7A share a jogged border; after simplification it must stay shared."""
    payload, _ = sample_payloads
    left = unary_union([p.buffer(0) for p in rings_to_polygons(payload["units"]["7"]["d"])])
    right = unary_union([p.buffer(0) for p in rings_to_polygons(payload["units"]["7A"]["d"])])
    merged = unary_union([left, right])
    assert isinstance(merged, Polygon), "neighbours fell apart into separate pieces"
    assert not merged.interiors, "a hole opened between neighbours"
    assert abs(merged.area - (left.area + right.area)) < 0.05


def test_label_points_are_inside_their_unit(sample_payloads) -> None:
    payload, _ = sample_payloads
    for name, unit in payload["units"].items():
        largest = max(rings_to_polygons(unit["d"]), key=lambda p: p.area).buffer(0)
        x, y = unit["label"]
        assert largest.contains(Point(x, y)), f"{name} label point is outside the unit"


def test_build_is_deterministic(sample_payloads) -> None:
    payload, seed = sample_payloads
    again, seed_again = build.build_payloads(
        json.loads(FIXTURE.read_text()), generated="2026-01-01T00:00:00Z"
    )
    assert build.dumps(again) == build.dumps(payload)
    assert build.dumps(seed_again) == build.dumps(seed)


def test_build_payloads_rejects_empty_collection() -> None:
    with pytest.raises(ValueError):
        build.build_payloads({"type": "FeatureCollection", "features": []})


# ------------------------------------------------------------------------ --check


def test_check_mode(tmp_path, monkeypatch, capsys) -> None:
    gmu = tmp_path / "gmu.json"
    seed = tmp_path / "units_seed.json"
    monkeypatch.setattr(build, "GMU_PATH", gmu)
    monkeypatch.setattr(build, "UNITS_SEED_PATH", seed)
    monkeypatch.setattr(build, "REPO_ROOT", tmp_path)

    args = ["--input", str(FIXTURE)]
    assert build.main([*args, "--check"]) == 1  # missing files
    assert build.main(args) == 0  # write
    assert build.main([*args, "--check"]) == 0  # idempotent
    assert build.main(args) == 0 and build.main([*args, "--check"]) == 0  # rerun is stable

    stale = json.loads(gmu.read_text())
    stale["units"].pop("7A")
    gmu.write_text(json.dumps(stale))
    assert build.main([*args, "--check"]) == 1
    assert "out of date" in capsys.readouterr().err


def test_check_ignores_the_generated_timestamp(tmp_path, monkeypatch) -> None:
    gmu = tmp_path / "gmu.json"
    seed = tmp_path / "units_seed.json"
    monkeypatch.setattr(build, "GMU_PATH", gmu)
    monkeypatch.setattr(build, "UNITS_SEED_PATH", seed)
    monkeypatch.setattr(build, "REPO_ROOT", tmp_path)
    args = ["--input", str(FIXTURE)]
    assert build.main(args) == 0
    data = json.loads(gmu.read_text())
    data["generated"] = "1999-12-31T23:59:59Z"
    gmu.write_text(json.dumps(data))
    assert build.main([*args, "--check"]) == 0


def test_missing_units_without_a_db(tmp_path) -> None:
    assert build.missing_units(["1", "2"], db_path=tmp_path / "nope.db") == []


# ----------------------------------------------------------- committed real geometry


@pytest.fixture(scope="module")
def gmu_data() -> dict[str, Any]:
    return json.loads(GMU_JSON.read_text())


def test_committed_gmu_json_shape(gmu_data) -> None:
    assert gmu_data["viewBox"] == "0 0 430 690"
    assert gmu_data["source"] == "IDFG GIS MapServer/3"
    assert len(gmu_data["units"]) == EXPECTED_UNIT_COUNT
    assert GMU_JSON.stat().st_size < SIZE_BUDGET_BYTES
    for name, unit in gmu_data["units"].items():
        assert unit["d"].startswith("M") and unit["d"].endswith("Z"), name
        assert isinstance(unit["acres"], float) and unit["acres"] > 0, name
        lx, ly = unit["label"]
        assert 0 <= lx <= 430 and 0 <= ly <= 690, name
    assert gmu_data["units"]["YNP"]["closed"] is True
    assert sum(1 for u in gmu_data["units"].values() if u.get("closed")) == 1


def test_committed_units_are_sorted_and_seeded(gmu_data) -> None:
    seed = json.loads(UNITS_SEED_JSON.read_text())
    assert [u["id"] for u in seed] == list(gmu_data["units"])
    assert [u["sort_key"] for u in seed] == sorted(u["sort_key"] for u in seed)
    assert set(seed[0]) == {"id", "sort_key", "elk_zone", "area_acres"}
    for entry, (name, unit) in zip(seed, gmu_data["units"].items(), strict=True):
        assert entry["id"] == name
        assert entry["area_acres"] == unit["acres"]
        assert entry["elk_zone"] == unit["zone"]
    assert {u["id"] for u in seed} >= {"1", "4A", "36B", "YNP"}


def test_committed_acreage_matches_idaho(gmu_data) -> None:
    """Projection sanity: the units should sum to Idaho's 83,569 sq mi, within 1%."""
    total_acres = sum(u["acres"] for u in gmu_data["units"].values())
    square_miles = total_acres / 640
    assert abs(square_miles - 83_569) / 83_569 < 0.01, f"{square_miles:,.0f} sq mi"


def test_committed_paths_use_one_decimal(gmu_data) -> None:
    for name, unit in gmu_data["units"].items():
        for number in NUMBER_RE.findall(unit["d"]):
            fraction = number.partition(".")[2]
            assert len(fraction) <= 1, f"{name} has >1 decimal: {number}"


def test_committed_geometry_has_no_visible_slivers(gmu_data) -> None:
    """Acceptance criterion 2, as an assertion: the 100 units tile Idaho with no gaps."""
    polygons = [
        p.buffer(0) for unit in gmu_data["units"].values() for p in rings_to_polygons(unit["d"])
    ]
    merged = unary_union(polygons)
    parts = list(merged.geoms) if isinstance(merged, MultiPolygon) else [merged]
    assert len(parts) == 1, "units do not form one connected state"

    total = sum(p.area for p in polygons)
    overlap = total - merged.area
    assert overlap / total < 1e-4, f"units overlap by {overlap:.3f} svg units^2"

    holes = [Polygon(ring).area for part in parts for ring in part.interiors]
    # 1 viewBox unit is about 1.1 km, so a 0.05 unit^2 hole is far below one rendered pixel.
    assert all(area < 0.05 for area in holes), f"visible gap between units: {max(holes):.3f}"
    assert sum(holes) < 2.0
