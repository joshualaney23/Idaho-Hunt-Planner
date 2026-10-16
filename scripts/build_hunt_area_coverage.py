"""Build `static/data/hunt_area_coverage/<year>.json` from the IDFG GIS service (T-17b).

Run manually; the generated file is committed, like `gmu.json` (DESIGN.md §9).

    python scripts/build_hunt_area_coverage.py --year 2026           # fetch + write
    python scripts/build_hunt_area_coverage.py --year 2026 --check   # exit 1 if stale
    python scripts/build_hunt_area_coverage.py --year 2026 --input raw.json

Pipeline (§12 T-17b step 1):
  1. GET MapServer/4 (`Controlled Hunts`: BigGame, HuntArea, Year) as GeoJSON in EPSG:4326,
     paging past the service's 2000-record transfer limit.
  2. Per species, use the newest polygon year <= the license year (no 2026 polygons exist, so
     2026 uses 2025) and record it as `geometry_year`; a year behind the license year is a
     review flag the importer raises as `warn`.
  3. Project both the hunt areas and the GMUs (MapServer/3, §9) to EPSG:8826 and intersect
     every hunt area with every GMU it touches.
  4. Write, per species and area id, the fraction of **each GMU's** area inside the hunt area.

Boundary noise is dropped; see `KEEP_*` below for the two-sided rule and why a one-sided
"< 0.5 % of the GMU" cut (§12's wording) silently deletes small real hunt areas.
"""

from __future__ import annotations

import argparse
import collections
import json
import sys
from collections.abc import Iterable, Sequence
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
OUTPUT_DIR = REPO_ROOT / "static" / "data" / "hunt_area_coverage"

HUNT_AREA_URL = "https://gisportal-idfg.idaho.gov/hosting/rest/services/Hunting/MapServer/4/query"
GMU_URL = "https://gisportal-idfg.idaho.gov/hosting/rest/services/Hunting/MapServer/3/query"
SOURCE = "IDFG GIS MapServer/4 (hunt areas) + MapServer/3 (GMUs)"

SOURCE_CRS = "EPSG:4326"
WORKING_CRS = "EPSG:8826"  # NAD83(2011) / Idaho Transverse Mercator, metres

#: Service page size. The layer holds ~2.2 k features and caps a single response at 2000.
PAGE_SIZE = 200

#: Keep a (hunt area, GMU) pair when the overlap is at least this fraction of the GMU...
KEEP_UNIT_FRAC = 0.005
#: ...or at least this fraction of the hunt area itself.
#:
#: §12 specifies only the first test. That alone deletes small real hunt areas: deer `38-1`
#: is a 9 km² depredation area lying *entirely* inside unit 38, which is 0.17 % of that GMU,
#: so the one-sided rule drops it and the area resolves to no unit at all. The second test
#: keeps it while still rejecting simplification noise (the largest noise overlap measured in
#: 2025 is 0.004 % of its hunt area, 500x below this cut).
KEEP_AREA_FRAC = 0.02

#: GIS `BigGame` value -> §5.3 species id. Deer/elk/pronghorn are the MVP (D2); the rest are
#: mapped so T-23/T-25/T-26 only have to pass `--species`.
SPECIES_BY_BIG_GAME: dict[str, str] = {
    "Deer": "deer",
    "Elk": "elk",
    "Pronghorn": "pronghorn",
    "Bear": "black_bear",
    "Moose": "moose",
    "Goat": "mountain_goat",
    "Rocky Mtn Sheep": "bighorn_rocky",
    "California Sheep": "bighorn_california",
    "Turkey": "turkey",
}
MVP_SPECIES = ("deer", "elk", "pronghorn")


# --------------------------------------------------------------------------- fetching


def _fetch_paged(url: str, params: dict[str, str], *, timeout: float = 180.0) -> list[Any]:
    """GET every feature from an ArcGIS layer, paging past the transfer limit.

    One retry per page (§12 conventions). Paging matters: an unpaged query of MapServer/4
    returns 2000 of its 2162 features with `exceededTransferLimit: true` and no error, which
    reads as a plausible-but-wrong year/species inventory.
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


def fetch_years(big_game: Sequence[str]) -> dict[str, set[int]]:
    """Inventory the polygon years per `BigGame` value, without downloading geometry."""
    quoted = ",".join(f"'{name}'" for name in big_game)
    features = _fetch_paged(
        HUNT_AREA_URL,
        {
            "where": f"BigGame IN ({quoted})",
            "outFields": "BigGame,Year",
            "returnGeometry": "false",
            "f": "json",
        },
    )
    years: dict[str, set[int]] = collections.defaultdict(set)
    for feature in features:
        attrs = feature.get("attributes") or {}
        years[str(attrs.get("BigGame") or "").strip()].add(int(attrs.get("Year") or 0))
    return years


def fetch_raw(species: Sequence[str], license_year: int) -> dict[str, list[Any]]:
    """Fetch the GMU and hunt-area FeatureCollections used to build a coverage file.

    Geometry is downloaded only for the year each species actually uses, which is both much
    faster and the only way to stay under the service's transfer limit comfortably.
    """
    big_game = sorted(k for k, v in SPECIES_BY_BIG_GAME.items() if v in species)
    if not big_game:
        raise ValueError(f"no GIS BigGame value maps to {sorted(species)}")

    years = fetch_years(big_game)
    clauses = [
        f"(BigGame='{name}' AND Year={geometry_year(years[name], license_year)})"
        for name in big_game
        if years.get(name)
    ]
    if not clauses:
        raise ValueError(f"no hunt-area polygons for {', '.join(big_game)}")

    return {
        "gmu": _fetch_paged(
            GMU_URL,
            {"where": "1=1", "outFields": "NAME", "outSR": "4326", "f": "geojson"},
        ),
        "hunt_areas": _fetch_paged(
            HUNT_AREA_URL,
            {
                "where": " OR ".join(clauses),
                "outFields": "BigGame,HuntArea,Year",
                "outSR": "4326",
                "f": "geojson",
            },
        ),
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

    A tiny hunt area kept by `KEEP_AREA_FRAC` can be well under 1e-4 of its GMU (a 9 km²
    depredation area is 2e-5 of some units); storing that as `0.0` would read as "no overlap"
    downstream, so sub-0.0001 values keep two significant digits instead.
    """
    rounded = round(value, 4)
    return rounded if rounded > 0 else float(f"{value:.2g}")


def geometry_year(years: Iterable[int], license_year: int) -> int:
    """Newest polygon year not after `license_year`, else the newest year available.

    The service has no 2026 big-game polygons, so a 2026 build runs on 2025 geometry.
    """
    available = sorted({int(y) for y in years})
    if not available:
        raise ValueError("no polygon years available")
    usable = [y for y in available if y <= license_year]
    return usable[-1] if usable else available[-1]


# ---------------------------------------------------------------------------- build


def build_coverage(
    raw: dict[str, list[Any]],
    license_year: int,
    *,
    species: Sequence[str] = MVP_SPECIES,
    generated: str | None = None,
) -> dict[str, Any]:
    """Build the coverage payload from raw GMU and hunt-area GeoJSON features.

    Pure (no network), so tests run it over a synthetic fixture.
    """
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

    wanted = set(species)
    # (species id, area id, year) -> parts. One hunt area can arrive as several features
    # (multipart areas are stored one row per piece), so the parts are unioned.
    parts: dict[tuple[str, str, int], list[Any]] = collections.defaultdict(list)
    years: dict[str, set[int]] = collections.defaultdict(set)
    for feature in raw.get("hunt_areas") or []:
        props = feature.get("properties") or {}
        species_id = SPECIES_BY_BIG_GAME.get(str(props.get("BigGame") or "").strip())
        if species_id is None or species_id not in wanted:
            continue
        year = int(props.get("Year") or 0)
        years[species_id].add(year)
        # `HuntArea` values carry stray whitespace in the service ('60A-2X ').
        area_id = str(props.get("HuntArea") or "").strip()
        if not area_id:
            raise ValueError(f"hunt-area feature {props!r} has no HuntArea")
        parts[(species_id, area_id, year)].append(feature["geometry"])

    chosen = {sid: geometry_year(ys, license_year) for sid, ys in years.items()}

    names = list(units)
    tree = STRtree([units[n] for n in names])

    payload_species: dict[str, dict[str, Any]] = {}
    for (species_id, area_id, year), geometries in sorted(parts.items()):
        if year != chosen[species_id]:
            continue
        area = unary_union([_project(shape(g), transformer) for g in geometries]).buffer(0)
        fractions: dict[str, float] = {}
        if area.area > 0:
            for index in tree.query(area):
                name = names[index]
                unit = units[name]
                overlap = area.intersection(unit).area
                if overlap <= 0:
                    continue
                unit_frac = overlap / unit.area
                area_frac = overlap / area.area
                if unit_frac >= KEEP_UNIT_FRAC or area_frac >= KEEP_AREA_FRAC:
                    fractions[name] = round_fraction(unit_frac)
        entry = {
            "geometry_year": chosen[species_id],
            "units": dict(sorted(fractions.items(), key=lambda kv: (-kv[1], kv[0]))),
        }
        payload_species.setdefault(species_id, {})[area_id] = entry

    return {
        "license_year": license_year,
        "species": {sid: payload_species.get(sid, {}) for sid in sorted(payload_species)},
        "generated": generated or datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": SOURCE,
    }


def summarize(payload: dict[str, Any]) -> list[str]:
    """Human-readable build report: area counts, stale geometry, areas with no unit."""
    lines: list[str] = []
    license_year = payload["license_year"]
    for species_id, areas in payload["species"].items():
        year = next(iter(areas.values()))["geometry_year"] if areas else None
        empty = sorted(a for a, e in areas.items() if not e["units"])
        stale = f" (stale: license year is {license_year})" if year and year < license_year else ""
        lines.append(f"{species_id}: {len(areas)} areas, geometry {year}{stale}")
        if empty:
            lines.append(f"  !! {len(empty)} area(s) resolve to no unit: {', '.join(empty)}")
    return lines


# ------------------------------------------------------------------------ rendering


def dumps(payload: Any) -> str:
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=False) + "\n"


def output_path(license_year: int) -> Path:
    return OUTPUT_DIR / f"{license_year}.json"


def _strip_generated(text: str) -> Any:
    data = json.loads(text)
    if isinstance(data, dict):
        data.pop("generated", None)
    return data


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--year", type=int, required=True, help="license year, e.g. 2026")
    parser.add_argument(
        "--species",
        default=",".join(MVP_SPECIES),
        help=f"comma-separated species ids, or 'all' (default: {','.join(MVP_SPECIES)})",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="regenerate and exit non-zero if the committed file differs",
    )
    parser.add_argument(
        "--input",
        type=Path,
        help="read {'gmu':[…],'hunt_areas':[…]} GeoJSON features from a file, not the network",
    )
    parser.add_argument(
        "--save-raw", type=Path, help="write the fetched features to a file for reuse"
    )
    args = parser.parse_args(argv)

    if args.species == "all":
        species = sorted(set(SPECIES_BY_BIG_GAME.values()))
    else:
        species = [s.strip() for s in args.species.split(",") if s.strip()]
    unknown = sorted(set(species) - set(SPECIES_BY_BIG_GAME.values()))
    if unknown:
        print(f"unknown species id(s): {', '.join(unknown)}", file=sys.stderr)
        return 2

    raw = json.loads(args.input.read_text()) if args.input else fetch_raw(species, args.year)
    if args.save_raw:
        args.save_raw.write_text(json.dumps(raw))

    payload = build_coverage(raw, args.year, species=species)
    for line in summarize(payload):
        print(line)

    path = output_path(args.year)
    text = dumps(payload)
    if args.check:
        if not path.exists():
            print(f"{path} is missing; run without --check", file=sys.stderr)
            return 1
        if _strip_generated(path.read_text()) != _strip_generated(text):
            print(f"{path} is stale; run without --check", file=sys.stderr)
            return 1
        print(f"{path} is up to date")
        return 0

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    print(f"wrote {path} ({len(text) / 1024:.1f} KB)")
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI
    raise SystemExit(main())
