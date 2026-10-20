"""Build `static/data/county_units.json` from Census counties x IDFG GMUs (T-14, §9).

Run manually; the generated file is committed, like `gmu.json` (DESIGN.md §9).

    python scripts/build_county_overlay.py                 # fetch + write
    python scripts/build_county_overlay.py --check         # exit 1 if stale
    python scripts/build_county_overlay.py --input raw.json --save-raw raw.json

Pipeline (§9):
  1. Download the Census cartographic boundary file `cb_<vintage>_us_county_500k.zip`
     and keep the counties with `STATEFP == '16'` (Idaho: 44 counties).
  2. GET the GMU polygons from IDFG MapServer/3 as GeoJSON in EPSG:4326 (same layer §9
     uses for `gmu.json`).
  3. Project both to EPSG:8826 (Idaho Transverse Mercator, metres) with pyproj and
     intersect every county with every GMU it touches.
  4. Write, per county, the fraction of **each GMU's** area inside that county.

The consumer is §5.2: an upland area's county list resolves to the units whose summed
fractions over those counties are >= 0.98 (`full`) or in 2-98 % (`partial`). Because the
44 counties tile the state exactly, every unit's fractions sum to ~1.0 -- `summarize()`
reports any unit that does not, which is the build's own correctness check.
"""

from __future__ import annotations

import argparse
import collections
import io
import json
import sys
import zipfile
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import shapely
from pyproj import Transformer
from shapely.geometry import shape
from shapely.ops import unary_union
from shapely.strtree import STRtree

REPO_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_PATH = REPO_ROOT / "static" / "data" / "county_units.json"

#: Census cartographic boundary counties, 1:500k (§9). `GENZ<vintage>`.
COUNTY_URL = "https://www2.census.gov/geo/tiger/GENZ{vintage}/shp/cb_{vintage}_us_county_500k.zip"
GMU_URL = "https://gisportal-idfg.idaho.gov/hosting/rest/services/Hunting/MapServer/3/query"
SOURCE = "US Census cb_{vintage}_us_county_500k + IDFG GIS MapServer/3 (GMUs)"

#: Newest cartographic boundary vintage published by the Census Bureau.
DEFAULT_VINTAGE = 2024
IDAHO_STATEFP = "16"
#: Idaho has had 44 counties since 1919; a different count means the filter or file is wrong.
IDAHO_COUNTY_COUNT = 44

SOURCE_CRS = "EPSG:4326"
WORKING_CRS = "EPSG:8826"  # NAD83(2011) / Idaho Transverse Mercator, metres

#: MapServer/3 page size (the layer is ~100 features; paging is cheap insurance).
PAGE_SIZE = 200

#: Drop a (county, GMU) pair below this fraction of the GMU.
#:
#: County and GMU boundaries are digitised independently and simplified differently, so a
#: shared border laps over by a sliver on nearly every pair. §5.2's own floor is 2 % (below
#: that a unit is not even `partial`), so this cut is an order of magnitude lower: it removes
#: boundary noise without hiding anything the resolver would have used.
MIN_UNIT_FRAC = 0.001

#: `summarize()` complains when a unit's county fractions do not add up to ~1.
UNIT_TOTAL_TOLERANCE = 0.01


# --------------------------------------------------------------------------- fetching


def _fetch_paged(url: str, params: dict[str, str], *, timeout: float = 180.0) -> list[Any]:
    """GET every feature from an ArcGIS layer, paging past the transfer limit.

    One retry per page. Paging matters: an ArcGIS layer silently truncates an oversized
    response and sets `exceededTransferLimit` instead of failing (T-17b).
    """
    import requests  # imported lazily so the pure build functions stay importable offline

    features: list[Any] = []
    offset = 0
    while True:
        page = dict(params, resultOffset=str(offset), resultRecordCount=str(PAGE_SIZE))
        last_error: Exception | None = None
        payload: dict[str, Any] | None = None
        for _attempt in range(2):
            try:
                response = requests.get(url, params=page, timeout=timeout)
                response.raise_for_status()
                payload = response.json()
                break
            except Exception as exc:  # noqa: BLE001 - retried once, then re-raised
                last_error = exc
        if payload is None:
            raise RuntimeError(f"GIS request failed ({url}): {last_error}") from last_error
        batch = payload.get("features") or []
        features.extend(batch)
        if not batch or not payload.get("exceededTransferLimit"):
            return features
        offset += len(batch)


def county_features(zip_bytes: bytes, *, statefp: str = IDAHO_STATEFP) -> list[Any]:
    """Read one state's counties out of a `cb_*_us_county_500k.zip` as GeoJSON features.

    `pyshp` (requirements-dev.txt) is the only shapefile reader here; it is pure Python and
    reads straight from the in-memory zip, so nothing is unpacked to disk.
    """
    import shapefile  # pyshp; lazy for the same reason as `requests`

    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as archive:
        names = {Path(n).suffix.lower(): n for n in archive.namelist()}
        missing = {".shp", ".dbf", ".shx"} - set(names)
        if missing:
            raise ValueError(f"county archive is missing {sorted(missing)}")
        with (
            archive.open(names[".shp"]) as shp,
            archive.open(names[".dbf"]) as dbf,
            archive.open(names[".shx"]) as shx,
        ):
            reader = shapefile.Reader(
                shp=io.BytesIO(shp.read()), dbf=io.BytesIO(dbf.read()), shx=io.BytesIO(shx.read())
            )
            features = []
            for record in reader.shapeRecords():
                props = record.record.as_dict()
                if str(props.get("STATEFP") or "") != statefp:
                    continue
                features.append(
                    {
                        "type": "Feature",
                        "properties": {
                            "NAME": str(props.get("NAME") or "").strip(),
                            "GEOID": str(props.get("GEOID") or "").strip(),
                        },
                        "geometry": record.shape.__geo_interface__,
                    }
                )
    return features


def fetch_raw(vintage: int = DEFAULT_VINTAGE) -> dict[str, list[Any]]:
    """Fetch the GMU and Idaho county features used to build the overlay."""
    import requests

    url = COUNTY_URL.format(vintage=vintage)
    response = requests.get(url, timeout=300)
    response.raise_for_status()
    counties = county_features(response.content)
    if not counties:
        raise RuntimeError(f"no STATEFP={IDAHO_STATEFP} counties in {url}")
    return {
        "gmu": _fetch_paged(
            GMU_URL,
            {"where": "1=1", "outFields": "NAME", "outSR": "4326", "f": "geojson"},
        ),
        "counties": counties,
    }


# ------------------------------------------------------------------------- geometry


def _transformer() -> Transformer:
    return Transformer.from_crs(SOURCE_CRS, WORKING_CRS, always_xy=True)


def _project(geometry: Any, transformer: Transformer) -> Any:
    """Reproject every coordinate, then `buffer(0)` to repair self-touching rings."""
    projected = shapely.transform(
        geometry,
        lambda coords: np.column_stack(transformer.transform(coords[:, 0], coords[:, 1])),
    )
    return projected.buffer(0)


def round_fraction(value: float) -> float:
    """Round a coverage fraction to 4 dp, never down to a flat `0.0`.

    A kept pair must stay visibly non-zero: `0.0` would read as "no overlap" downstream.
    """
    rounded = round(value, 4)
    return rounded if rounded > 0 else float(f"{value:.2g}")


# ---------------------------------------------------------------------------- build


def build_overlay(
    raw: dict[str, list[Any]],
    *,
    vintage: int = DEFAULT_VINTAGE,
    generated: str | None = None,
) -> dict[str, Any]:
    """Build the county -> {unit: fraction of the unit} payload. Pure (no network)."""
    transformer = _transformer()

    units: dict[str, Any] = {}
    for feature in raw.get("gmu") or []:
        name = str((feature.get("properties") or {}).get("NAME") or "").strip()
        if not name:
            raise ValueError(f"GMU feature {feature.get('properties')!r} has no NAME")
        geometry = _project(shape(feature["geometry"]), transformer)
        if geometry.is_empty:
            raise ValueError(f"unit {name} has empty geometry")
        units[name] = geometry
    if not units:
        raise ValueError("no GMU features")

    # One county can arrive as several features (islands/detached parts), so parts are unioned.
    parts: dict[str, list[Any]] = collections.defaultdict(list)
    for feature in raw.get("counties") or []:
        county = str((feature.get("properties") or {}).get("NAME") or "").strip()
        if not county:
            raise ValueError(f"county feature {feature.get('properties')!r} has no NAME")
        parts[county].append(feature["geometry"])
    if not parts:
        raise ValueError("no county features")

    names = list(units)
    tree = STRtree([units[n] for n in names])

    counties: dict[str, dict[str, float]] = {}
    for county, geometries in sorted(parts.items()):
        area = unary_union([_project(shape(g), transformer) for g in geometries]).buffer(0)
        fractions: dict[str, float] = {}
        if area.area > 0:
            for index in tree.query(area):
                name = names[index]
                unit = units[name]
                unit_frac = area.intersection(unit).area / unit.area
                if unit_frac >= MIN_UNIT_FRAC:
                    fractions[name] = round_fraction(unit_frac)
        counties[county] = dict(sorted(fractions.items(), key=lambda kv: (-kv[1], kv[0])))

    return {
        "vintage": vintage,
        "counties": counties,
        "generated": generated or datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": SOURCE.format(vintage=vintage),
    }


def unit_totals(payload: dict[str, Any]) -> dict[str, float]:
    """Per unit, the sum of its fractions across every county (should be ~1.0)."""
    totals: dict[str, float] = collections.defaultdict(float)
    for fractions in payload["counties"].values():
        for unit, frac in fractions.items():
            totals[unit] += frac
    return dict(sorted(totals.items()))


def summarize(payload: dict[str, Any], *, known_units: Sequence[str] = ()) -> list[str]:
    """Human-readable build report: counts, units that do not add up, missing units."""
    counties = payload["counties"]
    totals = unit_totals(payload)
    lines = [
        f"{len(counties)} counties, {len(totals)} units, "
        f"{sum(len(v) for v in counties.values())} county/unit pairs "
        f"(vintage {payload['vintage']})"
    ]
    if len(counties) != IDAHO_COUNTY_COUNT:
        lines.append(f"  !! expected {IDAHO_COUNTY_COUNT} Idaho counties, got {len(counties)}")
    empty = sorted(c for c, v in counties.items() if not v)
    if empty:
        lines.append(f"  !! {len(empty)} county/counties cover no unit: {', '.join(empty)}")
    off = {u: t for u, t in totals.items() if abs(t - 1.0) > UNIT_TOTAL_TOLERANCE}
    if off:
        detail = ", ".join(f"{u}={t:.3f}" for u, t in sorted(off.items())[:10])
        lines.append(f"  !! {len(off)} unit(s) do not sum to 1.0 across counties: {detail}")
    missing = sorted(set(known_units) - set(totals))
    if missing:
        lines.append(f"  !! {len(missing)} known unit(s) absent: {', '.join(missing)}")
    return lines


def seeded_units() -> list[str]:
    """Unit ids from the committed seed, so the build can flag a unit it never covered."""
    path = REPO_ROOT / "static" / "data" / "units_seed.json"
    if not path.exists():
        return []
    return [str(u["id"]) for u in json.loads(path.read_text())]


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
        "--vintage",
        type=int,
        default=DEFAULT_VINTAGE,
        help=f"Census cartographic boundary vintage (default: {DEFAULT_VINTAGE})",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="regenerate and exit non-zero if the committed file differs",
    )
    parser.add_argument(
        "--input",
        type=Path,
        help="read {'gmu':[…],'counties':[…]} GeoJSON features from a file, not the network",
    )
    parser.add_argument(
        "--save-raw", type=Path, help="write the fetched features to a file for reuse"
    )
    args = parser.parse_args(argv)

    raw = json.loads(args.input.read_text()) if args.input else fetch_raw(args.vintage)
    if args.save_raw:
        args.save_raw.write_text(json.dumps(raw))

    payload = build_overlay(raw, vintage=args.vintage)
    for line in summarize(payload, known_units=seeded_units()):
        print(line)

    text = dumps(payload)
    if args.check:
        if not OUTPUT_PATH.exists():
            print(f"{OUTPUT_PATH} is missing; run without --check", file=sys.stderr)
            return 1
        if _strip_generated(OUTPUT_PATH.read_text()) != _strip_generated(text):
            print(f"{OUTPUT_PATH} is stale; run without --check", file=sys.stderr)
            return 1
        print(f"{OUTPUT_PATH} is up to date")
        return 0

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(text)
    print(f"wrote {OUTPUT_PATH} ({len(text) / 1024:.1f} KB)")
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI
    raise SystemExit(main())
