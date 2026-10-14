"""Build `static/data/gmu.json` and `static/data/units_seed.json` from the IDFG GIS service.

Run manually; the generated files are committed (DESIGN.md §9).

    python scripts/build_gmu_geometry.py            # fetch + write
    python scripts/build_gmu_geometry.py --check    # exit 1 if the committed files are stale

Pipeline (§9 steps 1-4):
  1. GET MapServer/3 as GeoJSON in EPSG:4326.
  2. Project to EPSG:8826 (Idaho Transverse Mercator, metres) and simplify to ~300 m,
     preserving the shared-boundary topology so neighbours don't gap.
  3. Scale into a 430 x 690 viewBox and emit SVG paths plus a polylabel label point.
  4. Warn when a unit known to the regulations DB has no geometry.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import shapely
from pyproj import Transformer
from shapely.geometry import MultiPolygon, Polygon, shape
from shapely.ops import polylabel

REPO_ROOT = Path(__file__).resolve().parent.parent
GMU_PATH = REPO_ROOT / "static" / "data" / "gmu.json"
UNITS_SEED_PATH = REPO_ROOT / "static" / "data" / "units_seed.json"
DB_PATH = REPO_ROOT / "data" / "planner.db"

SERVICE_URL = "https://gisportal-idfg.idaho.gov/hosting/rest/services/Hunting/MapServer/3/query"
QUERY = {
    "where": "1=1",
    "outFields": "NAME,ID,Elk_Zone",
    "outSR": "4326",
    "f": "geojson",
}
SOURCE = "IDFG GIS MapServer/3"

SOURCE_CRS = "EPSG:4326"
WORKING_CRS = "EPSG:8826"  # NAD83(2011) / Idaho Transverse Mercator, metres
SIMPLIFY_TOLERANCE_M = 300.0
POLYLABEL_TOLERANCE_M = 100.0
VIEWBOX_W = 430.0
VIEWBOX_H = 690.0
SQM_PER_ACRE = 4046.8564224
SIZE_BUDGET_BYTES = 150 * 1024

#: Units that are mapped but never huntable. Flagged ``"closed": true`` in gmu.json.
CLOSED_UNITS = {"YNP"}

#: Sort key for a unit whose name isn't ``<number><optional A/B/C>`` (e.g. "YNP"): sorts last.
NON_NUMERIC_SORT_KEY = 99990

_SUFFIX_RANK = {"": 0, "A": 1, "B": 2, "C": 3}
_NAME_RE = re.compile(r"^(\d+)([A-C]?)$")


# --------------------------------------------------------------------------- fetching


def fetch_geojson(url: str = SERVICE_URL, *, timeout: float = 30.0) -> dict[str, Any]:
    """GET the GMU FeatureCollection. 30 s timeout, one retry (§12 conventions)."""
    import requests  # imported lazily so the pure build functions stay importable offline

    last_error: Exception | None = None
    for _attempt in range(2):
        try:
            response = requests.get(url, params=QUERY, timeout=timeout)
            response.raise_for_status()
            return response.json()
        except Exception as exc:  # noqa: BLE001 - retried once, then re-raised
            last_error = exc
    raise RuntimeError(f"GMU geometry service request failed: {last_error}") from last_error


# ------------------------------------------------------------------- naming / ordering


def sort_key(name: str) -> int:
    """``int(numeric part) * 10 + suffix rank`` (none=0, A=1, B=2, C=3)."""
    match = _NAME_RE.match(name.strip().upper())
    if not match:
        return NON_NUMERIC_SORT_KEY
    number, suffix = match.groups()
    return int(number) * 10 + _SUFFIX_RANK[suffix]


# ------------------------------------------------------------------------- geometry


def _reproject(geometry: Any, transformer: Transformer) -> Any:
    """Reproject every coordinate of ``geometry`` with ``transformer``."""
    return shapely.transform(
        geometry,
        lambda coords: np.column_stack(transformer.transform(coords[:, 0], coords[:, 1])),
    )


def _polygons(geometry: Polygon | MultiPolygon) -> list[Polygon]:
    if isinstance(geometry, MultiPolygon):
        return [p for p in geometry.geoms if not p.is_empty]
    return [] if geometry.is_empty else [geometry]


def _simplify_coverage(geometries: Sequence[Polygon | MultiPolygon]) -> list[Any]:
    """Simplify the shared-boundary mesh so neighbours keep identical borders.

    Uses ``shapely.coverage_simplify`` (shapely >= 2.1); falls back to per-polygon
    ``simplify(preserve_topology=True)``, which is topology-safe per polygon only.
    """
    coverage_simplify = getattr(shapely, "coverage_simplify", None)
    if coverage_simplify is not None:
        try:
            parts = list(coverage_simplify(list(geometries), SIMPLIFY_TOLERANCE_M))
            if len(parts) == len(geometries) and not any(p.is_empty for p in parts):
                return parts
            raise ValueError("coverage_simplify returned an unusable result")
        except Exception as exc:  # noqa: BLE001 - fall back to per-polygon simplify
            print(
                f"warning: coverage_simplify failed ({exc}); per-polygon fallback",
                file=sys.stderr,
            )
    return [g.simplify(SIMPLIFY_TOLERANCE_M, preserve_topology=True) for g in geometries]


def _fit(bounds: tuple[float, float, float, float]) -> tuple[float, float, float]:
    """Return ``(scale, offset_x, offset_y)`` fitting projected bounds into the viewBox."""
    min_x, min_y, max_x, max_y = bounds
    width = max_x - min_x
    height = max_y - min_y
    scale = min(VIEWBOX_W / width, VIEWBOX_H / height)
    offset_x = (VIEWBOX_W - width * scale) / 2.0 - min_x * scale
    # SVG y grows downward, so the projected max_y maps to y=0.
    offset_y = (VIEWBOX_H - height * scale) / 2.0 + max_y * scale
    return scale, offset_x, offset_y


def _project_point(x: float, y: float, scale: float, dx: float, dy: float) -> tuple[float, float]:
    return round(x * scale + dx, 1), round(-y * scale + dy, 1)


def _num(value: float) -> str:
    """One-decimal SVG number, trimmed (``12.0`` -> ``12``, ``0.8`` -> ``.8``)."""
    text = f"{value:.1f}"
    if text.endswith(".0"):
        text = text[:-2]
    if text.startswith("0."):
        text = text[1:]
    elif text.startswith("-0."):
        text = "-" + text[2:]
    return text


def _join_numbers(tokens: Sequence[str]) -> str:
    """Join SVG path numbers with the fewest separators that still parse unambiguously.

    A separator can be dropped before a negative number (``1-2``), and before a number
    starting with ``.`` only when the previous number already has a decimal point
    (``1.5.4`` is maximal-munch parsed as ``1.5`` then ``.4``). ``12`` + ``.5`` must keep
    its space, or it reads as the single number ``12.5``.
    """
    out = ""
    prev_has_dot = False
    for token in tokens:
        if out and not (token[0] == "-" or (token[0] == "." and prev_has_dot)):
            out += " "
        out += token
        prev_has_dot = "." in token
    return out


def _ring_path(coords: Iterable[Sequence[float]], scale: float, dx: float, dy: float) -> str:
    """Encode one ring as ``M<abs> l<relative deltas> Z``.

    Relative deltas are computed from the already-rounded absolute points, so decoding
    reproduces them exactly while costing far fewer bytes than absolute coordinates.
    """
    points: list[tuple[float, float]] = []
    for x, y, *_ in coords:
        point = _project_point(x, y, scale, dx, dy)
        if not points or point != points[-1]:
            points.append(point)
    while len(points) > 1 and points[0] == points[-1]:
        points.pop()
    if len(points) < 3:
        return ""

    deltas: list[str] = []
    prev_x, prev_y = points[0]
    for x, y in points[1:]:
        deltas.append(_num(round(x - prev_x, 1)))
        deltas.append(_num(round(y - prev_y, 1)))
        prev_x, prev_y = x, y

    start = _join_numbers([_num(points[0][0]), _num(points[0][1])])
    return f"M{start}l{_join_numbers(deltas)}Z"


def _svg_path(geometry: Polygon | MultiPolygon, scale: float, dx: float, dy: float) -> str:
    parts: list[str] = []
    for polygon in _polygons(geometry):
        for ring in (polygon.exterior, *polygon.interiors):
            path = _ring_path(ring.coords, scale, dx, dy)
            if path:
                parts.append(path)
    return "".join(parts)


def _label_point(
    geometry: Polygon | MultiPolygon, scale: float, dx: float, dy: float
) -> list[float]:
    polygons = _polygons(geometry)
    if not polygons:
        return [0.0, 0.0]
    largest = max(polygons, key=lambda p: p.area)
    try:
        point = polylabel(largest, tolerance=POLYLABEL_TOLERANCE_M)
    except Exception:  # noqa: BLE001 - degenerate ring; representative_point always works
        point = largest.representative_point()
    x, y = _project_point(point.x, point.y, scale, dx, dy)
    return [x, y]


# ---------------------------------------------------------------------------- build


def build_payloads(
    geojson: dict[str, Any], *, generated: str | None = None
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Return ``(gmu_payload, units_seed_payload)`` from a GMU FeatureCollection."""
    features = geojson.get("features") or []
    if not features:
        raise ValueError("GMU FeatureCollection contains no features")

    transformer = Transformer.from_crs(SOURCE_CRS, WORKING_CRS, always_xy=True)

    names: list[str] = []
    zones: list[str | None] = []
    projected: list[Polygon | MultiPolygon] = []
    acres: list[float] = []

    for feature in features:
        props = feature.get("properties") or {}
        name = str(props.get("NAME") or "").strip()
        if not name:
            raise ValueError(f"feature {props!r} has no NAME")
        geometry = _reproject(shape(feature["geometry"]), transformer).buffer(0)
        if geometry.is_empty:
            raise ValueError(f"unit {name} has empty geometry")
        names.append(name)
        zone = props.get("Elk_Zone")
        zones.append(str(zone).strip() or None if zone else None)
        projected.append(geometry)
        # Acreage comes from the full-resolution projected polygon, not the simplified one.
        acres.append(round(geometry.area / SQM_PER_ACRE, 1))

    simplified = _simplify_coverage(projected)

    bounds_list = [g.bounds for g in simplified]
    bounds = (
        min(b[0] for b in bounds_list),
        min(b[1] for b in bounds_list),
        max(b[2] for b in bounds_list),
        max(b[3] for b in bounds_list),
    )
    scale, dx, dy = _fit(bounds)

    order = sorted(range(len(names)), key=lambda i: (sort_key(names[i]), names[i]))

    units: dict[str, Any] = {}
    seed: list[dict[str, Any]] = []
    for i in order:
        name = names[i]
        entry: dict[str, Any] = {
            "d": _svg_path(simplified[i], scale, dx, dy),
            "label": _label_point(simplified[i], scale, dx, dy),
            "zone": zones[i],
            "acres": acres[i],
        }
        if name in CLOSED_UNITS:
            entry["closed"] = True
        units[name] = entry
        seed.append(
            {
                "id": name,
                "sort_key": sort_key(name),
                "elk_zone": zones[i],
                "area_acres": acres[i],
            }
        )

    payload = {
        "viewBox": f"0 0 {VIEWBOX_W:g} {VIEWBOX_H:g}",
        "units": units,
        "generated": generated or datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": SOURCE,
    }
    return payload, seed


def missing_units(unit_names: Iterable[str], db_path: Path = DB_PATH) -> list[str]:
    """Units referenced by the regulations DB that have no geometry (§9 step 4)."""
    if not db_path.exists():
        return []
    known = set(unit_names)
    with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as conn:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='units'"
        ).fetchone()
        if not row:
            return []
        db_units = {str(r[0]).strip() for r in conn.execute("SELECT id FROM units")}
    return sorted(db_units - known, key=sort_key)


# ------------------------------------------------------------------------ rendering


def dumps(payload: Any) -> str:
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=False) + "\n"


def _strip_generated(text: str) -> Any:
    data = json.loads(text)
    if isinstance(data, dict):
        data.pop("generated", None)
    return data


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="regenerate and exit non-zero if the committed files differ",
    )
    parser.add_argument(
        "--input",
        type=Path,
        help="read the GeoJSON FeatureCollection from a file instead of the network",
    )
    args = parser.parse_args(argv)

    if args.input:
        geojson = json.loads(args.input.read_text())
    else:
        geojson = fetch_geojson()

    payload, seed = build_payloads(geojson)
    gmu_text = dumps(payload)
    seed_text = dumps(seed)

    stale = missing_units(payload["units"])
    if stale:
        print(
            f"warning: units in the regs DB with no geometry: {', '.join(stale)}", file=sys.stderr
        )

    if args.check:
        failures = []
        for path, text in ((GMU_PATH, gmu_text), (UNITS_SEED_PATH, seed_text)):
            if not path.exists():
                failures.append(f"{path.relative_to(REPO_ROOT)} is missing")
            elif _strip_generated(path.read_text()) != _strip_generated(text):
                failures.append(f"{path.relative_to(REPO_ROOT)} is out of date")
        if failures:
            for failure in failures:
                print(f"check failed: {failure}", file=sys.stderr)
            return 1
        print(f"check ok: {len(payload['units'])} units, committed output matches")
        return 0

    GMU_PATH.parent.mkdir(parents=True, exist_ok=True)
    GMU_PATH.write_text(gmu_text)
    UNITS_SEED_PATH.write_text(seed_text)

    size = len(gmu_text.encode())
    print(f"wrote {GMU_PATH.relative_to(REPO_ROOT)}: {len(payload['units'])} units, {size:,} bytes")
    print(f"wrote {UNITS_SEED_PATH.relative_to(REPO_ROOT)}: {len(seed)} units")
    if size > SIZE_BUDGET_BYTES:
        print(
            f"warning: gmu.json is {size:,} bytes, over the {SIZE_BUDGET_BYTES:,} byte budget",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
