"""REST endpoints (DESIGN.md §6). Mounted under `/api`."""

from __future__ import annotations

import datetime as _dt
import io
import json
import tempfile
from functools import lru_cache
from pathlib import Path
from typing import Any

from flask import Blueprint, Response, current_app, jsonify, request, send_file

from planner import __version__, backup, db, diff, engine, importer
from planner import seasons as seasons_csv
from planner.dates import DEFAULT_WEEKEND_DEF, WEEKEND_DEFS, default_range, parse_iso, to_iso

api = Blueprint("api", __name__, url_prefix="/api")

PROFILE_DEFAULTS: dict[str, Any] = {
    "youth": 0,
    "weekend_def": DEFAULT_WEEKEND_DEF,
    "default_species": "deer",
}

# `my_permits.permit` vocabulary (§5) with the labels the UI shows.
PERMITS: list[tuple[str, str]] = [
    ("archery", "Archery permit"),
    ("muzzleloader", "Muzzleloader permit"),
    ("hunting_license", "Hunting license"),
    ("wmb", "Upland Game Bird permit (WMA)"),
    ("falconry", "Falconry permit"),
    ("hip", "HIP registration"),
    ("federal_duck_stamp", "Federal duck stamp"),
]
PERMIT_CODES = tuple(code for code, _ in PERMITS)

MIN_YEAR, MAX_YEAR = 1900, 2100
TAG_FIELDS = ("tag_type_id", "zone", "hunt_number", "label", "notes", "filled")


class ApiError(Exception):
    """Raised by handlers; rendered as `{error: {code, message, detail}}` (§6)."""

    def __init__(self, code: str, message: str, detail: Any = None, status: int = 400) -> None:
        super().__init__(message)
        self.code, self.message, self.detail, self.status = code, message, detail, status


@api.errorhandler(ApiError)
def _api_error(e: ApiError):
    body = {"error": {"code": e.code, "message": e.message, "detail": e.detail}}
    return jsonify(body), e.status


def _profile() -> dict[str, Any]:
    row = db.q1("SELECT youth, weekend_def, default_species FROM profile WHERE id = 1")
    out = dict(PROFILE_DEFAULTS)
    if row is not None:
        out.update({k: row[k] for k in row.keys() if row[k] is not None})
    if out["weekend_def"] not in WEEKEND_DEFS:
        out["weekend_def"] = DEFAULT_WEEKEND_DEF
    return out


def _today() -> _dt.date:
    """Server-local date: only a fallback, the browser computes its own range (§7.3)."""
    return _dt.date.today()


def _now() -> str:
    """Timestamp for audit columns (review, waivers, publish), matching the importer's."""
    return _dt.datetime.now().isoformat(timespec="seconds")


@api.get("/health")
def health():
    return jsonify({"ok": True, "version": __version__})


@api.get("/meta")
def meta():
    profile = _profile()
    published = db.q(
        "SELECT species_id, COUNT(*) AS n FROM seasons WHERE status = 'published'"
        " GROUP BY species_id"
    )
    counts = {r["species_id"]: r["n"] for r in published}
    species = [
        {
            "id": r["id"],
            "label": r["label"],
            "icon": r["icon"],
            "grp": r["grp"],
            "published_seasons": counts.get(r["id"], 0),
        }
        for r in db.q("SELECT id, label, icon, grp FROM species ORDER BY rowid")
    ]
    tag_types = [
        {
            "id": r["id"],
            "species_id": r["species_id"],
            "label": r["label"],
            "requires_zone": bool(r["requires_zone"]),
            "requires_hunt_no": bool(r["requires_hunt_no"]),
        }
        for r in db.q("SELECT * FROM tag_types ORDER BY rowid")
    ]
    documents = [
        dict(r)
        for r in db.q(
            "SELECT id, kind, title, license_year, year_end, published_at"
            " FROM source_documents WHERE status = 'published'"
            " ORDER BY license_year DESC, kind, id"
        )
    ]
    today = _today()
    years = {today.year}
    years.update(
        r[0]
        for r in db.q(
            "SELECT license_year FROM source_documents WHERE status = 'published'"
            " UNION SELECT year_end FROM source_documents WHERE status = 'published'"
            " UNION SELECT license_year FROM my_tags"
        )
    )
    start, end = default_range(today, profile["weekend_def"])
    return jsonify(
        {
            "version": __version__,
            "species": species,
            "tag_types": tag_types,
            # The Season Table filter: stored `seasons.weapon` values, plus 'rifle' (see below).
            "weapons": list(FILTER_WEAPONS),
            "restrictions": engine.RESTRICTIONS,
            "reasons": {
                code: {"label": label, "severity": "narrowing"}
                for code, label in engine.DERIVED_REASONS.items()
            },
            "license_years": sorted(years),
            "default_range": {
                "start": to_iso(start),
                "end": to_iso(end),
                "weekend_def": profile["weekend_def"],
                "today": to_iso(today),
            },
            "profile": profile,
            "documents": documents,
        }
    )


@api.get("/units")
def units():
    rows = db.q(
        "SELECT id, sort_key, elk_zone, region, area_acres FROM units ORDER BY sort_key, id"
    )
    return jsonify({"units": [dict(r) for r in rows]})


IDFG_UNIT_PAGE = "https://idfg.idaho.gov/ifwis/huntplanner/unit.aspx?ID={}"
_IDFG_LINKS_PATH = Path(__file__).resolve().parent.parent / "static" / "data" / "idfg_units.json"


@lru_cache(maxsize=1)
def _idfg_unit_ids() -> dict[str, int]:
    """GMU id → IDFG Hunt Planner page id (`scripts/build_idfg_unit_links.py`)."""
    try:
        return json.loads(_IDFG_LINKS_PATH.read_text())
    except (OSError, ValueError):
        return {}


@api.get("/units/<unit_id>/seasons")
def unit_seasons(unit_id: str):
    """Unit Brief (§4.3, T-08): every published season for one unit in a license year.

    All species are returned; `held_species` (species of the tags held that year) lets
    the drawer show those first. A season belongs to `year` when its document covers it
    (`license_year <= year <= year_end`, so two-year upland brochures count for both).
    """
    unit = db.q1(
        "SELECT id, sort_key, elk_zone, region, area_acres FROM units WHERE id = ?", unit_id
    )
    if unit is None:
        raise ApiError("unknown_unit", f"unknown unit {unit_id!r}", {"unit": unit_id}, status=404)
    year = _year(request.args.get("year"), default=_today().year)
    seasons = _dicts(
        "SELECT s.id, s.species_id, sp.label AS species_label, sp.icon AS species_icon,"
        " s.sub_species, s.tag_type_id AS tag_type, tt.label AS tag_type_label, s.zone,"
        " s.hunt_number, s.hunt_area, s.hunt_class, s.weapon, s.sex,"
        " s.start_date AS start, s.end_date AS end, s.restrictions, s.bag_limit, s.notes,"
        " su.coverage, su.coverage_note, s.source_page, s.document_id,"
        " d.title AS document_title, d.kind AS document_kind, d.license_year"
        " FROM season_units su"
        " JOIN seasons s ON s.id = su.season_id"
        " JOIN source_documents d ON d.id = s.document_id"
        " JOIN species sp ON sp.id = s.species_id"
        " JOIN tag_types tt ON tt.id = s.tag_type_id"
        " WHERE su.unit_id = ? AND s.status = 'published'"
        " AND ? BETWEEN d.license_year AND d.year_end"
        " ORDER BY sp.rowid, s.start_date, s.end_date, s.id",
        unit_id,
        year,
    )
    for s in seasons:
        s["restrictions"] = json.loads(s["restrictions"] or "[]")
    held = [
        r["species_id"]
        for r in db.q(
            "SELECT DISTINCT tt.species_id FROM my_tags t"
            " JOIN tag_types tt ON tt.id = t.tag_type_id"
            " WHERE t.license_year = ? AND tt.species_id IS NOT NULL ORDER BY tt.species_id",
            year,
        )
    ]
    page = _idfg_unit_ids().get(unit_id)
    return jsonify(
        {
            "unit": {**dict(unit), "idfg_url": IDFG_UNIT_PAGE.format(page) if page else None},
            "year": year,
            "held_species": held,
            "seasons": seasons,
        }
    )


def _dicts(sql: str, *args: Any) -> list[dict[str, Any]]:
    """Rows as plain dicts: the engine takes mappings (`sqlite3.Row` has no `.get`)."""
    cur = db.current().execute(sql, args)
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, row, strict=True)) for row in cur.fetchall()]


def _parse_date(name: str, raw: str) -> _dt.date:
    try:
        return parse_iso(raw)
    except ValueError:
        raise ApiError("bad_date", f"{name} must be a YYYY-MM-DD date", {name: raw}) from None


@api.get("/availability")
def availability():
    args = request.args
    profile = _profile()

    species = args.get("species") or profile["default_species"]
    if db.q1("SELECT 1 FROM species WHERE id = ?", species) is None:
        raise ApiError("unknown_species", f"unknown species {species!r}", {"species": species})

    if "start" in args or "end" in args:
        start = _parse_date("start", args.get("start", ""))
        end = _parse_date("end", args.get("end", args.get("start", "")))
    else:
        start, end = default_range(_today(), profile["weekend_def"])
    if end < start:
        raise ApiError(
            "bad_range", "end is before start", {"start": to_iso(start), "end": to_iso(end)}
        )

    weapon = args.get("weapon", "all")
    if weapon not in engine.WEAPONS:
        raise ApiError(
            "bad_weapon", f"weapon must be one of {', '.join(engine.WEAPONS)}", {"weapon": weapon}
        )

    untagged_raw = args.get("include_untagged", "0")
    if untagged_raw not in ("0", "1", "true", "false"):
        raise ApiError(
            "bad_flag", "include_untagged must be 0 or 1", {"include_untagged": untagged_raw}
        )
    include_untagged = untagged_raw in ("1", "true")

    tags_raw = args.get("tags", "mine")
    tag_ids: list[int] | None = None
    if tags_raw != "mine":
        try:
            tag_ids = [int(t) for t in tags_raw.split(",") if t.strip()]
        except ValueError:
            raise ApiError(
                "bad_tags", "tags must be 'mine' or a list of tag ids", {"tags": tags_raw}
            ) from None

    y1, y2 = start.year, end.year
    s_iso, e_iso = to_iso(start), to_iso(end)

    # One query per table. Published only; the date overlap is filtered in SQL.
    season_filter = (
        "s.status = 'published' AND s.species_id = ? AND s.start_date <= ? AND s.end_date >= ?"
    )
    seasons = _dicts(
        "SELECT s.id, s.document_id, d.license_year, s.status, s.species_id, s.tag_type_id,"
        " s.zone, s.hunt_number, s.hunt_class, s.weapon, s.sex, s.start_date, s.end_date,"
        " s.restrictions, s.source_page"
        f" FROM seasons s JOIN source_documents d ON d.id = s.document_id WHERE {season_filter}",
        species,
        e_iso,
        s_iso,
    )
    season_units = _dicts(
        "SELECT su.season_id, su.unit_id, su.coverage, su.coverage_note, su.coverage_frac"
        " FROM season_units su JOIN seasons s ON s.id = su.season_id"
        f" WHERE {season_filter}",
        species,
        e_iso,
        s_iso,
    )
    my_tags = _dicts(
        "SELECT id, license_year, tag_type_id, zone, hunt_number, filled FROM my_tags"
        " WHERE filled = 0 AND license_year BETWEEN ? AND ?",
        y1,
        y2,
    )
    if tag_ids is not None:
        wanted = set(tag_ids)
        my_tags = [t for t in my_tags if t["id"] in wanted]
    permits = _dicts(
        "SELECT license_year, permit FROM my_permits WHERE license_year BETWEEN ? AND ?", y1, y2
    )
    tag_types = _dicts("SELECT id, requires_zone, requires_hunt_no FROM tag_types")
    unit_ids = [r["id"] for r in db.q("SELECT id FROM units ORDER BY sort_key, id")]

    result = engine.evaluate(
        seasons,
        season_units,
        my_tags,
        permits,
        profile,
        species,
        start,
        end,
        weapon,
        include_untagged,
        tag_types=tag_types,
        unit_ids=unit_ids,
    )
    result["tags"] = "mine" if tag_ids is None else tag_ids
    # The biggest payload in the app: skip jsonify's key sorting and pretty-printing.
    body = json.dumps(result, separators=(",", ":"), ensure_ascii=False, check_circular=False)
    return Response(body, mimetype="application/json")


# ───────── controlled hunts (T-17c, §6 `GET /api/hunts/<year>/<hunt_number>`) ─────────
#: Season statuses a hunt lookup reports, best first. `rejected` rows are never a hunt you
#: can hold a tag for, so a number whose rows were all rejected in review reads as unknown
#: (§12 T-17c: "Save is disabled for unknown numbers or numbers whose seasons are only
#: rejected"). Staged/approved rows are shown, flagged by `status`, because the user may
#: hold the tag before the import is published.
HUNT_STATUSES: tuple[str, ...] = ("published", "approved", "staged", "superseded")


def _hunt_seasons(year: int, hunt_number: str) -> list[dict[str, Any]]:
    """Every non-rejected season printed under one hunt number in one license year.

    One hunt number can print several date windows (T-17a), so this is a list. The year is
    the *document's* `license_year`, which is what a controlled tag matches on (§7.2).
    """
    marks = ", ".join("?" * len(HUNT_STATUSES))
    return _dicts(
        "SELECT s.id, s.status, s.species_id, sp.label AS species_label, sp.icon,"
        " s.tag_type_id, s.hunt_number, s.hunt_area, s.hunt_class, s.weapon, s.sex,"
        " s.start_date AS start, s.end_date AS end, s.restrictions, s.bag_limit, s.notes,"
        " s.source_page, s.document_id, d.license_year"
        " FROM seasons s"
        " JOIN source_documents d ON d.id = s.document_id"
        " JOIN species sp ON sp.id = s.species_id"
        f" WHERE s.hunt_number = ? AND d.license_year = ? AND s.status IN ({marks})"
        " ORDER BY s.start_date, s.end_date, s.id",
        hunt_number,
        year,
        *HUNT_STATUSES,
    )


def _hunt_units(season_ids: list[int]) -> list[dict[str, Any]]:
    """The hunt's units with their §5 coverage, worst coverage per unit, in map order."""
    if not season_ids:
        return []
    marks = ", ".join("?" * len(season_ids))
    rows = _dicts(
        "SELECT su.unit_id AS unit, su.coverage, su.coverage_note, su.coverage_frac"
        " FROM season_units su JOIN units u ON u.id = su.unit_id"
        f" WHERE su.season_id IN ({marks})"
        " ORDER BY u.sort_key, u.id",
        *season_ids,
    )
    out: dict[str, dict[str, Any]] = {}
    for r in rows:
        cur = out.get(r["unit"])
        # Several windows of one hunt share a unit: keep the narrowest coverage of them.
        if cur is None or (cur["coverage"] == "full" and r["coverage"] == "partial"):
            out[r["unit"]] = r
        elif (
            cur["coverage_frac"] is not None and (r["coverage_frac"] or 1.0) < cur["coverage_frac"]
        ):
            out[r["unit"]] = r
    return list(out.values())


def _hunt_dict(year: int, hunt_number: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    """The §6 hunt shape: what the Add-tag preview and the tag card show."""
    first = rows[0]
    for r in rows:
        r["restrictions"] = json.loads(r["restrictions"] or "[]")
    area = None
    if first["hunt_area"]:
        # `hunt_areas` is keyed per document too (§5): prefer the hunt's own document,
        # so a re-import of the same year can't hand back the superseded description.
        area = db.q1(
            "SELECT description, description_page FROM hunt_areas"
            " WHERE license_year = ? AND species_id = ? AND area_id = ?"
            " ORDER BY document_id = ? DESC, document_id DESC",
            year,
            first["species_id"],
            first["hunt_area"],
            first["document_id"],
        )
    status = next((s for s in HUNT_STATUSES if any(r["status"] == s for r in rows)), "staged")
    return {
        "year": year,
        "hunt_number": hunt_number,
        "tag_type_id": first["tag_type_id"],
        "species_id": first["species_id"],
        "species_label": first["species_label"],
        "icon": first["icon"] or "🎟️",
        # Hunt numbers are unique per species in the brochures we parse; if one ever isn't,
        # say so instead of guessing which hunt the user drew (D3).
        "species_ids": sorted({r["species_id"] for r in rows}),
        "hunt_class": first["hunt_class"],
        "weapon": first["weapon"],
        "sex": first["sex"],
        "hunt_area": first["hunt_area"],
        "description": area["description"] if area else None,
        "description_page": area["description_page"] if area else None,
        "status": status,
        "published": status == "published",
        "start": min(r["start"] for r in rows),
        "end": max(r["end"] for r in rows),
        "windows": [
            {
                "id": r["id"],
                "status": r["status"],
                "start": r["start"],
                "end": r["end"],
                "weapon": r["weapon"],
                "sex": r["sex"],
                "restrictions": r["restrictions"],
                "bag_limit": r["bag_limit"],
                "notes": r["notes"],
                "source_page": r["source_page"],
                "document_id": r["document_id"],
            }
            for r in rows
        ],
        "units": _hunt_units([r["id"] for r in rows]),
        "source_page": first["source_page"],
        "document_id": first["document_id"],
    }


def hunt_brief(year: int, hunt_number: str | None) -> dict[str, Any] | None:
    """The hunt behind a controlled tag, or None when that year prints no such hunt."""
    if not hunt_number:
        return None
    rows = _hunt_seasons(year, hunt_number)
    return _hunt_dict(year, hunt_number, rows) if rows else None


@api.get("/hunts/<int:year>/<hunt_number>")
def hunt_lookup(year: int, hunt_number: str):
    """Controlled-hunt lookup for the Add-tag dialog (§6, §12 T-17c).

    404 `unknown_hunt` covers both "no such number this year" and "its rows were rejected
    in review"; `detail.reason` says which, and the dialog keeps Save disabled either way.
    """
    year = _year(year)
    number = str(hunt_number).strip()
    if not number.isalnum():
        raise ApiError(
            "bad_hunt_number",
            "hunt numbers are digits (e.g. 1002)",
            {"hunt_number": hunt_number},
        )
    rows = _hunt_seasons(year, number)
    if not rows:
        rejected = db.q1(
            "SELECT 1 FROM seasons s JOIN source_documents d ON d.id = s.document_id"
            " WHERE s.hunt_number = ? AND d.license_year = ? AND s.status = 'rejected'",
            number,
            year,
        )
        raise ApiError(
            "unknown_hunt",
            f"hunt #{number} was rejected in review for {year}"
            if rejected
            else f"no hunt #{number} in the {year} regulations",
            {
                "year": year,
                "hunt_number": number,
                "reason": "rejected" if rejected else "not_found",
            },
            status=404,
        )
    return jsonify({"hunt": _hunt_dict(year, number, rows)})


# ───────── request helpers ─────────
def _body() -> dict[str, Any]:
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise ApiError("bad_request", "expected a JSON object body")
    return data


def _year(raw: Any, *, field: str = "year", default: int | None = None) -> int:
    """Parse a license year from a query argument."""
    if raw is None or raw == "":
        if default is not None:
            return default
        raise ApiError("bad_request", f"missing {field}", {field: "required"})
    try:
        year = int(raw)
    except (TypeError, ValueError):
        raise ApiError("bad_request", f"{field} must be a year", {field: "not a number"}) from None
    if not MIN_YEAR <= year <= MAX_YEAR:
        raise ApiError(
            "bad_request",
            f"{field} must be between {MIN_YEAR} and {MAX_YEAR}",
            {field: "out of range"},
        )
    return year


def _flag(value: Any, field: str) -> int:
    """Coerce a JSON boolean-ish value to 0/1."""
    if isinstance(value, bool):
        return int(value)
    if value in (0, 1, "0", "1"):
        return int(value)
    raise ApiError("validation_failed", f"{field} must be true or false", {field: "not a boolean"})


def _text(value: Any, field: str) -> str | None:
    """Trim an optional free-text field; empty becomes NULL."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise ApiError("validation_failed", f"{field} must be text", {field: "not text"})
    return value.strip() or None


# ───────── tags ─────────
def _zones(year: int) -> list[str]:
    """Zones that have units for `year` (elk A/B zone membership, §5 `zone_units`)."""
    rows = db.q("SELECT DISTINCT zone FROM zone_units WHERE license_year = ? ORDER BY zone", year)
    return [r["zone"] for r in rows]


def _tag_types() -> list[dict[str, Any]]:
    rows = db.q(
        "SELECT t.id, t.species_id, t.label, t.requires_zone, t.requires_hunt_no,"
        "       s.label AS species_label, s.icon, s.grp"
        "  FROM tag_types t LEFT JOIN species s ON s.id = t.species_id"
        " ORDER BY t.rowid"
    )
    return [
        {
            "id": r["id"],
            "species_id": r["species_id"],
            "label": r["label"],
            "requires_zone": bool(r["requires_zone"]),
            "requires_hunt_no": bool(r["requires_hunt_no"]),
            "species_label": r["species_label"],
            "icon": r["icon"] or "🎟️",
            "grp": r["grp"],
        }
        for r in rows
    ]


def _species() -> list[dict[str, Any]]:
    rows = db.q("SELECT id, label, icon, grp FROM species ORDER BY rowid")
    return [{"id": r["id"], "label": r["label"], "icon": r["icon"], "grp": r["grp"]} for r in rows]


def _tag_type(tag_type_id: Any):
    if not isinstance(tag_type_id, str) or not tag_type_id.strip():
        raise ApiError("validation_failed", "pick a tag type", {"tag_type_id": "required"})
    row = db.q1(
        "SELECT id, species_id, label, requires_zone, requires_hunt_no FROM tag_types WHERE id = ?",
        tag_type_id.strip(),
    )
    if row is None:
        raise ApiError(
            "validation_failed",
            f"unknown tag type {tag_type_id!r}",
            {"tag_type_id": "unknown tag type"},
        )
    return row


def _validate_tag(
    tag_type, year: int, zone: Any, hunt_number: Any
) -> tuple[str | None, str | None]:
    """Apply the §12 T-09 rules; returns the normalized `(zone, hunt_number)`.

    `requires_zone` elk tags must name a zone that has units in `zone_units` for that
    year; other zone tags (sandhill crane areas, which arrive with the migratory import
    in T-26) only need a non-empty area. `requires_hunt_no` tags need a hunt number.
    Fields a tag type doesn't use are cleared, so a controlled tag never keeps a zone.
    """
    errors: dict[str, str] = {}
    zone = _text(zone, "zone")
    hunt_number = _text(hunt_number, "hunt_number")

    if tag_type["requires_zone"]:
        known = _zones(year)
        if zone is None:
            errors["zone"] = "required for this tag type"
        elif tag_type["species_id"] == "elk" and zone not in known:
            errors["zone"] = f"no units in zone {zone!r} for {year}"
    else:
        zone = None

    if tag_type["requires_hunt_no"]:
        if hunt_number is None:
            errors["hunt_number"] = "required for this tag type"
        elif not hunt_number.isalnum():
            # Deliberately not "must be a hunt in this year's regulations": the dialog
            # looks the hunt up (`GET /api/hunts/<year>/<no>`, T-17c) and keeps Save
            # disabled when it isn't one, but a tag may be recorded before the brochure
            # is imported, so the server only checks the shape.
            errors["hunt_number"] = "hunt numbers are digits (e.g. 1002)"
    else:
        hunt_number = None

    if errors:
        raise ApiError("validation_failed", "fix the highlighted fields", errors)
    return zone, hunt_number


def _tag_dict(row) -> dict[str, Any]:
    tag = {
        "id": row["id"],
        "license_year": row["license_year"],
        "tag_type_id": row["tag_type_id"],
        "tag_type_label": row["tag_type_label"],
        "species_id": row["species_id"],
        "species_label": row["species_label"],
        "icon": row["icon"] or "🎟️",
        "zone": row["zone"],
        "hunt_number": row["hunt_number"],
        "label": row["label"],
        "notes": row["notes"],
        "filled": bool(row["filled"]),
        "created_at": row["created_at"],
        "requires_zone": bool(row["requires_zone"]),
        "requires_hunt_no": bool(row["requires_hunt_no"]),
    }
    # A controlled tag carries no species of its own: it comes from the hunt (§7.2), and
    # with it the species, units and dates the tag card shows (T-17c). None = that year's
    # regulations aren't imported yet, or the number isn't in them.
    if tag["requires_hunt_no"]:
        tag["hunt"] = hunt_brief(int(row["license_year"]), row["hunt_number"])
        if tag["hunt"]:
            tag["species_id"] = tag["hunt"]["species_id"]
            tag["species_label"] = tag["hunt"]["species_label"]
            tag["icon"] = tag["hunt"]["icon"]
    return tag


_TAG_SELECT = """
SELECT m.id, m.license_year, m.tag_type_id, m.zone, m.hunt_number, m.label, m.notes,
       m.filled, m.created_at, t.label AS tag_type_label, t.requires_zone, t.requires_hunt_no,
       t.species_id, s.label AS species_label, s.icon
  FROM my_tags m
  JOIN tag_types t ON t.id = m.tag_type_id
  LEFT JOIN species s ON s.id = t.species_id
"""


def _tag(tag_id: int) -> dict[str, Any]:
    row = db.q1(f"{_TAG_SELECT} WHERE m.id = ?", tag_id)
    if row is None:
        raise ApiError("not_found", f"no tag {tag_id}", {"id": tag_id}, status=404)
    return _tag_dict(row)


def _tags_for(year: int) -> list[dict[str, Any]]:
    rows = db.q(
        f"{_TAG_SELECT} WHERE m.license_year = ?"
        " ORDER BY m.filled, t.rowid, m.zone, m.hunt_number, m.id",
        year,
    )
    return [_tag_dict(r) for r in rows]


def _years(current: int) -> list[int]:
    rows = db.q(
        "SELECT license_year AS y FROM my_tags"
        " UNION SELECT license_year FROM my_permits"
        " UNION SELECT license_year FROM zone_units"
    )
    return sorted({current, current - 1, *(int(r["y"]) for r in rows)}, reverse=True)


@api.get("/tags")
def list_tags():
    """Tags for a license year, plus the metadata the Add-tag dialog needs."""
    year = _year(request.args.get("year"), default=_today().year)
    return jsonify(
        {
            "year": year,
            "years": _years(_today().year),
            "tags": _tags_for(year),
            "tag_types": _tag_types(),
            "species": _species(),
            "zones": _zones(year),
        }
    )


@api.post("/tags")
def create_tag():
    data = _body()
    year = _year(data.get("license_year"), field="license_year", default=_today().year)
    tag_type = _tag_type(data.get("tag_type_id"))
    zone, hunt_number = _validate_tag(tag_type, year, data.get("zone"), data.get("hunt_number"))
    cur = db.exec(
        "INSERT INTO my_tags (license_year, tag_type_id, zone, hunt_number, label, notes,"
        " filled, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        year,
        tag_type["id"],
        zone,
        hunt_number,
        _text(data.get("label"), "label"),
        _text(data.get("notes"), "notes"),
        _flag(data.get("filled", 0), "filled"),
        _dt.datetime.now().isoformat(timespec="seconds"),
    )
    return jsonify({"tag": _tag(int(cur.lastrowid or 0))}), 201


@api.put("/tags/<int:tag_id>")
def update_tag(tag_id: int):
    """Partial update. Only the keys present in the body change."""
    data = _body()
    current = _tag(tag_id)
    unknown = sorted(set(data) - set(TAG_FIELDS) - {"id", "license_year"})
    if unknown:
        raise ApiError("bad_request", f"unknown field(s): {', '.join(unknown)}", unknown)

    year = _year(data.get("license_year"), field="license_year", default=current["license_year"])
    tag_type = _tag_type(data.get("tag_type_id", current["tag_type_id"]))
    zone, hunt_number = _validate_tag(
        tag_type,
        year,
        data.get("zone", current["zone"]),
        data.get("hunt_number", current["hunt_number"]),
    )
    db.exec(
        "UPDATE my_tags SET license_year = ?, tag_type_id = ?, zone = ?, hunt_number = ?,"
        " label = ?, notes = ?, filled = ? WHERE id = ?",
        year,
        tag_type["id"],
        zone,
        hunt_number,
        _text(data.get("label", current["label"]), "label"),
        _text(data.get("notes", current["notes"]), "notes"),
        _flag(data.get("filled", current["filled"]), "filled"),
        tag_id,
    )
    return jsonify({"tag": _tag(tag_id)})


@api.delete("/tags/<int:tag_id>")
def delete_tag(tag_id: int):
    _tag(tag_id)  # 404 if it's gone
    db.exec("DELETE FROM my_tags WHERE id = ?", tag_id)
    return jsonify({"ok": True, "deleted": tag_id})


@api.post("/tags/copy")
def copy_tags():
    """Copy last year's tags forward.

    Controlled tags are never copied (hunt numbers are reused for different hunts each
    year, §12 T-09), filled status never carries over, and a tag type + zone that already
    exists in the target year is left alone so the copy is idempotent.
    """
    src = _year(request.args.get("from"), field="from")
    dst = _year(request.args.get("to"), field="to", default=_today().year)
    if src == dst:
        raise ApiError("bad_request", "from and to must differ", {"from": src, "to": dst})

    existing = {(t["tag_type_id"], t["zone"]) for t in _tags_for(dst)}
    copied, skipped = [], {"controlled": 0, "existing": 0, "invalid": 0}
    now = _dt.datetime.now().isoformat(timespec="seconds")
    with db.tx():
        for tag in _tags_for(src):
            if tag["requires_hunt_no"]:
                skipped["controlled"] += 1
                continue
            if (tag["tag_type_id"], tag["zone"]) in existing:
                skipped["existing"] += 1
                continue
            try:
                # A zone that no longer has units in the target year can't carry over.
                _validate_tag(_tag_type(tag["tag_type_id"]), dst, tag["zone"], None)
            except ApiError:
                skipped["invalid"] += 1
                continue
            cur = db.exec(
                "INSERT INTO my_tags (license_year, tag_type_id, zone, hunt_number, label,"
                " notes, filled, created_at) VALUES (?, ?, ?, ?, ?, ?, 0, ?)",
                dst,
                tag["tag_type_id"],
                tag["zone"],
                None,
                tag["label"],
                tag["notes"],
                now,
            )
            existing.add((tag["tag_type_id"], tag["zone"]))
            copied.append(int(cur.lastrowid or 0))
    return jsonify(
        {
            "from": src,
            "to": dst,
            "copied": len(copied),
            "skipped": skipped,
            "tags": _tags_for(dst),
        }
    )


# ───────── permits ─────────
def _permits_for(year: int) -> list[str]:
    rows = db.q("SELECT permit FROM my_permits WHERE license_year = ?", year)
    held = {r["permit"] for r in rows}
    return [code for code in PERMIT_CODES if code in held]


@api.get("/permits")
def get_permits():
    year = _year(request.args.get("year"), default=_today().year)
    return jsonify(
        {
            "year": year,
            "permits": _permits_for(year),
            "available": [{"code": c, "label": label} for c, label in PERMITS],
        }
    )


@api.put("/permits")
def put_permits():
    """Replace the permits held for a year with the posted list."""
    year = _year(request.args.get("year"), default=_today().year)
    data = _body()
    wanted = data.get("permits", [])
    if not isinstance(wanted, list) or any(not isinstance(p, str) for p in wanted):
        raise ApiError("validation_failed", "permits must be a list of codes", {"permits": "bad"})
    unknown = sorted(set(wanted) - set(PERMIT_CODES))
    if unknown:
        raise ApiError(
            "validation_failed", f"unknown permit(s): {', '.join(unknown)}", {"permits": unknown}
        )
    with db.tx():
        db.exec("DELETE FROM my_permits WHERE license_year = ?", year)
        db.exec_many(
            "INSERT OR IGNORE INTO my_permits (license_year, permit) VALUES (?, ?)",
            [(year, p) for p in dict.fromkeys(wanted)],
        )
    return jsonify({"year": year, "permits": _permits_for(year)})


# ───────── profile ─────────
@api.get("/profile")
def get_profile():
    return jsonify({"profile": _profile()})


@api.put("/profile")
def put_profile():
    """Partial update of the single profile row. No resident flag, ever (D1)."""
    data = _body()
    unknown = sorted(set(data) - set(PROFILE_DEFAULTS))
    if unknown:
        raise ApiError("bad_request", f"unknown field(s): {', '.join(unknown)}", unknown)
    current = _profile()
    youth = _flag(data.get("youth", current["youth"]), "youth")

    weekend_def = data.get("weekend_def", current["weekend_def"])
    if weekend_def not in WEEKEND_DEFS:
        raise ApiError(
            "validation_failed",
            f"weekend_def must be one of {', '.join(WEEKEND_DEFS)}",
            {"weekend_def": "unknown weekend definition"},
        )

    species = _text(data.get("default_species", current["default_species"]), "default_species")
    if species is not None and db.q1("SELECT 1 FROM species WHERE id = ?", species) is None:
        raise ApiError(
            "validation_failed",
            f"unknown species {species!r}",
            {"default_species": "unknown species"},
        )

    db.exec(
        "INSERT INTO profile (id, youth, weekend_def, default_species) VALUES (1, ?, ?, ?)"
        " ON CONFLICT(id) DO UPDATE SET youth = excluded.youth,"
        " weekend_def = excluded.weekend_def, default_species = excluded.default_species",
        youth,
        weekend_def,
        species,
    )
    return jsonify({"profile": _profile()})


# ───────── backup / restore / reset (T-21, §6) ─────────
#: `POST /api/reset` only fires when the body repeats this word, so the UI can put the
#: one destructive button behind a typed confirmation instead of a second "are you sure".
RESET_PHRASE = "RESET"


def _db_path() -> Path:
    return Path(current_app.config["DB_PATH"])


def _uploaded_bytes() -> bytes:
    """The restore payload: multipart `file` if present, else the raw request body."""
    upload = request.files.get("file")
    raw = upload.read() if upload is not None and upload.filename else (request.get_data() or b"")
    if not raw:
        raise ApiError("missing_file", "attach the backup as the `file` field", {"field": "file"})
    if len(raw) > backup.MAX_BACKUP_BYTES:
        raise ApiError(
            "file_too_large",
            f"backups are limited to {backup.MAX_BACKUP_BYTES // (1024 * 1024)} MB",
            {"bytes": len(raw), "limit": backup.MAX_BACKUP_BYTES},
            status=413,
        )
    return raw


@api.get("/backup")
def get_backup():
    """Download a consistent snapshot of `planner.db` (`sqlite3.Connection.backup`)."""
    data = backup.snapshot_bytes(db.current())
    name = backup.download_name()
    response = send_file(
        io.BytesIO(data),
        mimetype="application/vnd.sqlite3",
        as_attachment=True,
        download_name=name,
        etag=False,
        last_modified=None,
    )
    response.headers["Content-Length"] = str(len(data))
    return response


@api.post("/restore")
def post_restore():
    """Replace the database with an uploaded backup, keeping the old one aside.

    The upload is validated first (SQLite header, `quick_check`, the planner's core
    tables and a `schema_version` this build understands); nothing is touched when it
    fails. The replaced DB stays as `planner.db.bak-<timestamp>`, and a backup from an
    older schema is migrated forward after it lands.
    """
    data = _uploaded_bytes()
    if not backup.looks_like_sqlite(data):
        raise ApiError("invalid_backup", "that file is not a SQLite database", status=415)
    with tempfile.TemporaryDirectory(prefix="ihp-restore-") as tmp:
        staged = Path(tmp) / "upload.db"
        staged.write_bytes(data)
        try:
            result = backup.restore(db.current(), staged, _db_path())
        except backup.RestoreError as exc:
            status = 409 if exc.code == "incompatible_backup" else 422
            raise ApiError(exc.code, str(exc), exc.detail, status=status) from None
    return jsonify({"restored": True, **result, "profile": _profile()})


@api.post("/reset")
def post_reset():
    """Erase every tag, permit, document and season, behind a typed confirmation.

    Reference data (units, species, tag types) is re-seeded, the stored PDFs and the
    page cache are deleted, and the old database is kept as `planner.db.bak-<timestamp>`.
    """
    data = _body()
    if str(data.get("confirm", "")).strip() != RESET_PHRASE:
        raise ApiError(
            "confirmation_required",
            f"type {RESET_PHRASE} to confirm; nothing was deleted",
            {"confirm": f"expected {RESET_PHRASE!r}"},
        )
    result = backup.reset(db.current(), _db_path())
    removed = backup.clear_data_dir(_data_dir())
    return jsonify({"reset": True, **result, "removed": removed, "profile": _profile()})


# ───────── documents (T-10, §8.1) ─────────
def _data_dir() -> Path:
    """`data/` for this app: beside the DB, so tests never write into the repo's copy."""
    configured = current_app.config.get("DATA_DIR")
    return Path(configured) if configured else Path(current_app.config["DB_PATH"]).parent


def _regs_dir() -> Path:
    return _data_dir() / "regs"


def _document_row(doc_id: int):
    row = db.q1("SELECT * FROM source_documents WHERE id = ?", doc_id)
    if row is None:
        raise ApiError(
            "unknown_document", f"no document {doc_id}", {"document_id": doc_id}, status=404
        )
    return row


def _document_dict(row) -> dict[str, Any]:
    counts = db.q(
        "SELECT status, COUNT(*) AS n FROM seasons WHERE document_id = ? GROUP BY status",
        row["id"],
    )
    checks = db.q(
        "SELECT status, COUNT(*) AS n FROM import_checks WHERE document_id = ? GROUP BY status",
        row["id"],
    )
    return {
        "id": row["id"],
        "kind": row["kind"],
        "title": row["title"],
        "filename": row["filename"],
        "license_year": row["license_year"],
        "year_end": row["year_end"],
        "pages": row["pages"],
        "sha256": row["sha256"],
        "status": row["status"],
        "priority": row["priority"],
        "parser_version": row["parser_version"],
        "imported_at": row["imported_at"],
        "published_at": row["published_at"],
        "error": row["error"],
        "pdf_url": f"/api/documents/{row['id']}/pdf",
        "seasons": {r["status"]: r["n"] for r in counts},
        "checks": {r["status"]: r["n"] for r in checks},
        # Kept forever when a human published past the gate (T-22g); `None` otherwise.
        "publish_override": (
            {
                "reason": row["publish_override_reason"],
                "by": row["publish_override_by"],
                "at": row["publish_override_at"],
                "bypassed": json.loads(row["publish_override_detail"] or "null"),
            }
            if row["publish_override_reason"]
            else None
        ),
    }


@api.get("/documents")
def list_documents():
    rows = db.q("SELECT * FROM source_documents ORDER BY license_year DESC, priority DESC, id DESC")
    return jsonify({"documents": [_document_dict(r) for r in rows]})


@api.get("/documents/<int:doc_id>")
def get_document(doc_id: int):
    """One document with its staged rows and accuracy checks (§8.4 review UI, T-15)."""
    row = _document_row(doc_id)
    seasons = _dicts(
        "SELECT s.*, GROUP_CONCAT(su.unit_id) AS units FROM seasons s"
        " LEFT JOIN season_units su ON su.season_id = s.id"
        " WHERE s.document_id = ? GROUP BY s.id ORDER BY s.source_page, s.id",
        doc_id,
    )
    for s in seasons:
        s["restrictions"] = json.loads(s["restrictions"] or "[]")
        s["units"] = sorted((s["units"] or "").split(","), key=_unit_sort) if s["units"] else []
    checks = _checks_for(doc_id)
    sections = _sections_for(checks, _routing_rows(doc_id))
    return jsonify(
        {
            "document": _document_dict(row),
            "seasons": seasons,
            "checks": checks,
            # §8.7 display safety: a section with a waived check shows the ⚠ badge, and
            # every season carries its `section` so the UI can join the two (T-22b).
            # Each section also carries its §8.4 routing and reasons (T-22g).
            "sections": sections,
            "routing": _routing_summary(sections),
            "publish": _publish_state(doc_id),
        }
    )


def _unit_sort(unit_id: str):
    from planner.importer.common import natural_key

    return natural_key(unit_id)


# A document's own rows for the diff: everything it proposes or already publishes.
# A `rejected` row was thrown away and a `superseded` one belongs to another document.
DIFF_STATUSES = ("staged", "approved", "published")


def _diff_season_rows(where: str, *args: Any) -> list[dict[str, Any]]:
    """Season rows with their unit set and per-unit coverage, for `planner.diff`."""
    rows = _dicts(
        "SELECT s.*, d.license_year AS license_year FROM seasons s"
        " JOIN source_documents d ON d.id = s.document_id"
        f" WHERE {where} ORDER BY s.id",
        *args,
    )
    by_id = {row["id"]: row for row in rows}
    for row in rows:
        row["restrictions"] = json.loads(row["restrictions"] or "[]")
        row["units"] = []
        row["coverage"] = {}
    if by_id:
        marks = ", ".join("?" * len(by_id))
        units = db.q(
            "SELECT season_id, unit_id, coverage, coverage_note FROM season_units"
            f" WHERE season_id IN ({marks}) ORDER BY season_id, unit_id",
            *by_id,
        )
        for unit in units:
            row = by_id[unit["season_id"]]
            row["units"].append(unit["unit_id"])
            row["coverage"][unit["unit_id"]] = [unit["coverage"], unit["coverage_note"]]
    return rows


def _prior_published_year(doc_row) -> int | None:
    """The most recent license year before this document's that has published rows."""
    row = db.q1(
        "SELECT MAX(d.license_year) AS year FROM seasons s"
        " JOIN source_documents d ON d.id = s.document_id"
        " WHERE s.status = 'published' AND d.license_year < ? AND s.document_id != ?",
        doc_row["license_year"],
        doc_row["id"],
    )
    return None if row is None else row["year"]


@api.get("/documents/<int:doc_id>/diff")
def document_diff(doc_id: int):
    """Diff this document's rows against the last published license year (§8.4, T-15a).

    Read-only: pure SQL + Python, no parsing and no writes. A document whose year has
    no earlier published year gets every row `new` and an empty `removed`.
    """
    doc_row = _document_row(doc_id)
    marks = ", ".join("?" * len(DIFF_STATUSES))
    current = _diff_season_rows(
        f"s.document_id = ? AND s.status IN ({marks})", doc_id, *DIFF_STATUSES
    )
    prior_year = _prior_published_year(doc_row)
    prior = (
        _diff_season_rows(
            "s.status = 'published' AND d.license_year = ? AND s.document_id != ?",
            prior_year,
            doc_id,
        )
        if prior_year is not None
        else []
    )
    result = diff.diff_rows(current, prior)
    return jsonify(
        {
            "document": {
                "id": doc_row["id"],
                "title": doc_row["title"],
                "license_year": doc_row["license_year"],
            },
            "prior_license_year": prior_year,
            **result,
        }
    )


@api.post("/documents")
def upload_document():
    """Upload a regulation PDF, store it by sha256, and import it synchronously (§8.1).

    `kind` and `year` form fields override the classifier. An already-stored file is
    rejected with 409 and the id of the document that holds it.
    """
    upload = request.files.get("file")
    if upload is None or not upload.filename:
        raise ApiError("missing_file", "attach the PDF as the `file` field", {"field": "file"})
    data = upload.read()
    if len(data) > importer.MAX_PDF_BYTES:
        raise ApiError(
            "file_too_large",
            f"PDFs are limited to {importer.MAX_PDF_BYTES // (1024 * 1024)} MB",
            {"bytes": len(data), "limit": importer.MAX_PDF_BYTES},
            status=413,
        )
    if not importer.looks_like_pdf(data):
        raise ApiError(
            "not_a_pdf", "that file is not a PDF", {"filename": upload.filename}, status=415
        )

    sha = importer.sha256_bytes(data)
    existing = db.q1("SELECT id, status, title FROM source_documents WHERE sha256 = ?", sha)
    if existing is not None:
        raise ApiError(
            "duplicate_document",
            "this PDF is already imported",
            {"document_id": existing["id"], "status": existing["status"], "sha256": sha},
            status=409,
        )

    kind = (request.form.get("kind") or "").strip() or None
    year = _year(request.form.get("year"), field="year") if request.form.get("year") else None
    try:
        info = importer.classify_bytes(data, kind=kind, year=year, filename=upload.filename)
    except importer.ClassifyError as exc:
        raise ApiError(
            "unclassified", str(exc), {"sample": exc.sample, "filename": upload.filename}, 422
        ) from None

    path = importer.save_pdf(_regs_dir(), info.license_year, sha, data)
    cur = db.exec(
        "INSERT INTO source_documents (license_year, year_end, kind, title, filename, sha256,"
        " stored_path, priority, status, parser_version)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'uploaded', ?)",
        info.license_year,
        info.year_end,
        info.kind,
        info.title or upload.filename,
        upload.filename,
        sha,
        str(path),
        info.priority,
        importer.PARSER_VERSION,
    )
    doc_id = int(cur.lastrowid or 0)
    result = importer.run_import(doc_id, pdf_path=path, data_dir=_data_dir())
    body = {"document": _document_dict(_document_row(doc_id)), "import": result.to_json()}
    return jsonify(body), 201


@api.get("/documents/<int:doc_id>/pdf")
def document_pdf(doc_id: int):
    """The stored PDF, inline so `#page=N` jumps to a season's source page (§6)."""
    row = _document_row(doc_id)
    path = Path(row["stored_path"] or "")
    if not path.is_file():
        raise ApiError(
            "missing_file",
            "the stored PDF is gone; re-upload it",
            {"document_id": doc_id, "stored_path": str(path)},
            status=410,
        )
    response = send_file(path, mimetype="application/pdf", conditional=True)
    name = row["filename"] or path.name
    response.headers["Content-Disposition"] = f'inline; filename="{Path(name).name}"'
    return response


# ───────── review gate (T-22b, §8.4 + §8.7) ─────────
#: A check in one of these states is neither a `pass` nor a no-op, so it blocks publishing
#: until it is explicitly waived with a reason. `skipped` means the check found no facts
#: to measure, so it never blocks.
BLOCKING_CHECK_STATUSES = ("fail", "warn")
REVIEW_STATUSES = ("approved", "rejected")
#: Row states review can still change (a published or superseded row is history).
REVIEWABLE_STATUSES = ("staged", "approved", "rejected")

# ───────── inline row edits (T-15c, §8.4) ─────────
#: The `seasons` CHECK vocabularies, mirrored so an edit is rejected with a field name
#: instead of an opaque IntegrityError. Keep in step with `planner/schema.sql`.
WEAPONS = ("any", "archery", "muzzleloader", "short_range", "shotgun", "falconry")
#: Season Table Weapon filter choices. 'rifle' is a filter-only value: it shows the seasons
#: stored as 'any' (open to rifle hunters, not exclusively bow or muzzleloader).
FILTER_WEAPONS = ("rifle", "archery", "muzzleloader", "short_range", "falconry")
SEXES = ("antlered", "antlerless", "either", "any", "male", "na")
#: Free-text columns the review screen lets a human correct. `None` clears them.
EDITABLE_TEXT_FIELDS = ("zone", "hunt_number", "hunt_area", "sub_species", "bag_limit", "notes")
#: Everything `PUT …/seasons/<sid>` accepts besides `status`.
EDITABLE_FIELDS = (
    "units",
    "start_date",
    "end_date",
    "tag_type_id",
    "weapon",
    "sex",
    "restrictions",
    *EDITABLE_TEXT_FIELDS,
)


def _check_row(doc_id: int, check_id: int):
    row = db.q1("SELECT * FROM import_checks WHERE id = ? AND document_id = ?", check_id, doc_id)
    if row is None:
        raise ApiError(
            "unknown_check",
            f"no check {check_id} on document {doc_id}",
            {"document_id": doc_id, "check_id": check_id},
            status=404,
        )
    return row


def _check_dict(row: dict[str, Any]) -> dict[str, Any]:
    """One `import_checks` row for the API: detail decoded, waiver and gate flags added."""
    out = dict(row)
    out["detail"] = json.loads(out["detail"]) if out["detail"] else None
    out["waived"] = bool(out["waived_by_user"])
    out["blocks_publish"] = out["status"] in BLOCKING_CHECK_STATUSES and not out["waived"]
    return out


def _checks_for(doc_id: int) -> list[dict[str, Any]]:
    rows = _dicts(
        "SELECT * FROM import_checks WHERE document_id = ? ORDER BY section, check_name", doc_id
    )
    return [_check_dict(r) for r in rows]


#: Row states the routing looks at; a published or superseded row is history (§8.4).
ROUTABLE_STATUSES = ("staged", "approved", "rejected")
#: The SQL that says "this row is uncertain" — the only rows a human must approve by
#: hand (T-22g). A row the parser scored 1.0 and both extraction paths agreed on carries
#: no red flag of its own, so it rides along with its document even when its section was
#: sent to review. Section-wide problems are checks, and a check has its own gate: it
#: blocks publishing until a human waives it with a written reason.
#: A hand-edited row is uncertain again until someone decides on it: the edit means a
#: human disagreed with the parser, which is exactly §12 T-22g's "editing a row of an
#: auto-accepted section routes that section to review".
ROW_NEEDS_REVIEW_SQL = (
    "(confidence < 1.0 OR extract_agree = 0 OR (edited_at IS NOT NULL AND reviewed_by_user = 0))"
)
#: Why a section was sent to review, in the order the UI lists them (§8.4).
ROUTING_REASONS = (
    "check_blocking",
    "check_waived",
    "low_confidence",
    "two_path_disagreement",
    "edited",
)
#: The columns `_row_needs_review` reads; every query that feeds it selects them.
ROW_FLAG_COLUMNS = "id, section, status, confidence, extract_agree, reviewed_by_user, edited_at"


def _confidence(row: Any) -> float:
    value = row["confidence"]
    return float(1.0 if value is None else value)


def _row_needs_review(row: Any) -> bool:
    """The Python twin of `ROW_NEEDS_REVIEW_SQL`."""
    return (
        _confidence(row) < 1.0
        or not row["extract_agree"]
        or bool(row["edited_at"] and not row["reviewed_by_user"])
    )


def _routing_rows(doc_id: int) -> list[dict[str, Any]]:
    return _dicts(
        f"SELECT {ROW_FLAG_COLUMNS} FROM seasons"
        f" WHERE document_id = ? AND status IN {ROUTABLE_STATUSES} ORDER BY id",
        doc_id,
    )


def _blank_section(name: str) -> dict[str, Any]:
    return {
        "section": name,
        "checks": {},
        "waived": False,
        "waived_checks": [],
        "blocking_checks": [],
        "clean": True,
        "routing": "auto_accepted",
        "reasons": [],
        "rows": 0,
        "needs_review_rows": [],
        "unreviewed_rows": [],
    }


def _sections_for(
    checks: list[dict[str, Any]], rows: list[dict[str, Any]] | None = None
) -> list[dict[str, Any]]:
    """Per-section check summary and the §8.4 review routing (T-22g).

    `waived` is §8.7 display safety: every season of a section with a waived check shows the
    ⚠ "manually verified" badge. `clean` is the bulk-approve rule — a section whose checks
    all pass (a waiver is a human override, not a pass, so it never makes a section clean).

    `routing` is `auto_accepted` when every row of the section has confidence 1.0, no row
    disagrees between the two extraction paths, and every check on it is `pass` or
    `skipped`; any other section is sent to `review`, with its `reasons`. Routing is
    derived, never stored. Being sent to review does **not** mean every row must be
    approved: only the rows in `needs_review_rows` do (`unreviewed_rows` are the ones
    still `staged`). A failing check keeps its own gate — a waiver — instead of turning
    each of a section's clean rows into a click.
    """
    sections: dict[str, dict[str, Any]] = {}
    for c in checks:
        s = sections.setdefault(c["section"] or "", _blank_section(c["section"] or ""))
        s["checks"][c["status"]] = s["checks"].get(c["status"], 0) + 1
        if c["waived"]:
            s["waived"] = True
            s["waived_checks"].append(c["check_name"])
            s["reasons"].append("check_waived")
        if c["blocks_publish"]:
            s["blocking_checks"].append(c["check_name"])
            s["reasons"].append("check_blocking")
        if c["status"] != "pass":
            s["clean"] = False
    for r in rows or []:
        s = sections.setdefault(r["section"] or "", _blank_section(r["section"] or ""))
        s["rows"] += 1
        if _confidence(r) < 1.0:
            s["reasons"].append("low_confidence")
        if not r["extract_agree"]:
            s["reasons"].append("two_path_disagreement")
        if r["edited_at"] and not r["reviewed_by_user"]:
            s["reasons"].append("edited")
        if _row_needs_review(r):
            s["needs_review_rows"].append(r["id"])
            if r["status"] == "staged":
                s["unreviewed_rows"].append(r["id"])
    for s in sections.values():
        s["reasons"] = [reason for reason in ROUTING_REASONS if reason in s["reasons"]]
        s["routing"] = "review" if s["reasons"] else "auto_accepted"
    return [sections[k] for k in sorted(sections)]


def _routing_summary(sections: list[dict[str, Any]]) -> dict[str, Any]:
    """The review screen's one-line summary: how many sections went each way (§8.4)."""
    auto = [s["section"] for s in sections if s["routing"] == "auto_accepted"]
    review = [s["section"] for s in sections if s["routing"] == "review"]
    return {
        "auto_accepted": auto,
        "review": review,
        "rows_needing_review": sum(len(s["needs_review_rows"]) for s in sections),
    }


def _row_refs(sql: str, doc_id: int) -> list[dict[str, Any]]:
    return _dicts(
        f"SELECT {ROW_FLAG_COLUMNS}, source_page FROM seasons"
        f" WHERE document_id = ? AND {sql} ORDER BY id",
        doc_id,
    )


def _publishable_rows(doc_id: int) -> dict[str, list[int]]:
    """What a publish would do with this document's rows (T-22g).

    `approved` were approved by hand, `auto` are staged rows no red flag touched — they
    publish with the document — and `unreviewed` are staged rows that still need a human
    and only publish through an override.
    """
    rows = _dicts(
        f"SELECT {ROW_FLAG_COLUMNS} FROM seasons"
        " WHERE document_id = ? AND status IN ('staged', 'approved') ORDER BY id",
        doc_id,
    )
    out: dict[str, list[int]] = {"approved": [], "auto": [], "unreviewed": []}
    for r in rows:
        if r["status"] == "approved":
            out["approved"].append(int(r["id"]))
        elif _row_needs_review(r):
            out["unreviewed"].append(int(r["id"]))
        else:
            out["auto"].append(int(r["id"]))
    return out


def _publish_blockers(doc_id: int) -> dict[str, Any]:
    """Everything that keeps document `doc_id` from publishing (§8.4's conditions).

    Condition (1) is T-22g's: no *uncertain* row may still be `staged`. Rows in an
    auto-accepted section — and the clean rows of a section sent to review — publish with
    the document without an individual approval.
    """
    checks = [c for c in _checks_for(doc_id) if c["blocks_publish"]]
    unreviewed = _row_refs(f"status = 'staged' AND {ROW_NEEDS_REVIEW_SQL}", doc_id)
    disagreeing = _row_refs(
        "extract_agree = 0 AND reviewed_by_user = 0 AND status NOT IN ('superseded', 'published')",
        doc_id,
    )
    rows = sorted({r["id"] for r in unreviewed} | {r["id"] for r in disagreeing})
    return {
        "checks": [
            {
                "id": c["id"],
                "section": c["section"],
                "check_name": c["check_name"],
                "status": c["status"],
                "expected": c["expected"],
                "actual": c["actual"],
            }
            for c in checks
        ],
        "rows": rows,
        "unreviewed_rows": unreviewed,
        "disagreeing_rows": disagreeing,
        # Every blocker here can be pushed through with a written reason (T-22g).
        "override_available": True,
    }


def _publish_state(doc_id: int) -> dict[str, Any]:
    blockers = _publish_blockers(doc_id)
    return {
        "blocked": bool(blockers["checks"] or blockers["rows"]),
        "blockers": blockers,
        # What Publish would do right now, so the confirmation modal can say it (T-22g).
        "rows": {k: len(v) for k, v in _publishable_rows(doc_id).items()},
    }


def _season_row(doc_id: int, season_id: int):
    row = db.q1("SELECT * FROM seasons WHERE id = ? AND document_id = ?", season_id, doc_id)
    if row is None:
        raise ApiError(
            "unknown_season",
            f"no season {season_id} on document {doc_id}",
            {"document_id": doc_id, "season_id": season_id},
            status=404,
        )
    return row


def _season_dict(doc_id: int, season_id: int) -> dict[str, Any]:
    rows = _dicts(
        "SELECT s.*, GROUP_CONCAT(su.unit_id) AS units FROM seasons s"
        " LEFT JOIN season_units su ON su.season_id = s.id"
        " WHERE s.id = ? AND s.document_id = ? GROUP BY s.id",
        season_id,
        doc_id,
    )
    row = rows[0]
    row["restrictions"] = json.loads(row["restrictions"] or "[]")
    row["units"] = sorted((row["units"] or "").split(","), key=_unit_sort) if row["units"] else []
    return row


@api.get("/documents/<int:doc_id>/checks")
def document_checks(doc_id: int):
    """Accuracy results for the review screen's Accuracy panel (§8.4)."""
    _document_row(doc_id)
    checks = _checks_for(doc_id)
    sections = _sections_for(checks, _routing_rows(doc_id))
    return jsonify(
        {
            "checks": checks,
            "sections": sections,
            "routing": _routing_summary(sections),
            "publish": _publish_state(doc_id),
        }
    )


@api.post("/documents/<int:doc_id>/checks/<int:check_id>/waive")
def waive_check(doc_id: int, check_id: int):
    """Waive a failing check with a typed reason, kept on the check forever (§8.4)."""
    _document_row(doc_id)
    row = _check_row(doc_id, check_id)
    body = _body()
    reason = body.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        raise ApiError(
            "missing_reason",
            "a waiver needs a written reason",
            {"field": "reason"},
            status=400,
        )
    if row["status"] not in BLOCKING_CHECK_STATUSES:
        raise ApiError(
            "nothing_to_waive",
            f"check {row['check_name']} is {row['status']}; only a fail or warn can be waived",
            {"check_id": check_id, "status": row["status"]},
            status=409,
        )
    db.exec(
        "UPDATE import_checks SET waived_by_user = 1, waive_reason = ?, waived_by = ?,"
        " waived_at = ? WHERE id = ?",
        reason.strip(),
        _text(body.get("by"), "by") or "user",
        _now(),
        check_id,
    )
    checks = _checks_for(doc_id)
    waived = next(c for c in checks if c["id"] == check_id)
    return jsonify({"check": waived, "publish": _publish_state(doc_id)})


def _edit_choice(value: Any, field: str, allowed: tuple[str, ...]) -> str:
    if not isinstance(value, str) or value not in allowed:
        raise ApiError(
            "validation_failed",
            f"{field} must be one of {', '.join(allowed)}",
            {field: "invalid"},
        )
    return value


def _edit_units(value: Any) -> list[str]:
    """A row's unit set, validated against the seeded GMUs — never invent a unit (D3)."""
    if not isinstance(value, list) or not value:
        raise ApiError(
            "validation_failed", "units must be a non-empty list of unit ids", {"units": "invalid"}
        )
    wanted = sorted({str(u).strip() for u in value}, key=_unit_sort)
    if any(not u for u in wanted):
        raise ApiError("validation_failed", "a unit id cannot be blank", {"units": "invalid"})
    known = {str(r["id"]) for r in db.q("SELECT id FROM units")}
    missing = [u for u in wanted if u not in known]
    if missing:
        raise ApiError(
            "unknown_unit",
            f"no such unit: {', '.join(missing)}",
            {"units": missing},
        )
    return wanted


def _edit_restrictions(value: Any) -> list[str]:
    """§5.1 is a closed vocabulary: an unknown code is a typo, not a new rule."""
    if not isinstance(value, list):
        raise ApiError(
            "validation_failed", "restrictions must be a list of codes", {"restrictions": "invalid"}
        )
    codes = [str(c) for c in value]
    unknown = sorted({c for c in codes if c not in engine.RESTRICTIONS})
    if unknown:
        raise ApiError(
            "unknown_restriction",
            f"not in the §5.1 vocabulary: {', '.join(unknown)}",
            {"restrictions": unknown},
        )
    return engine.sort_reasons(codes)


def _season_edits(row, body: dict[str, Any]) -> tuple[dict[str, Any], list[str] | None]:
    """Validate the edit half of a `PUT …/seasons/<sid>` body.

    Returns the `seasons` column updates and, when the units were edited, the new unit
    set (it lives in `season_units`, so it is written separately).
    """
    cols: dict[str, Any] = {}
    units: list[str] | None = None
    for field in EDITABLE_FIELDS:
        if field not in body:
            continue
        value = body[field]
        if field == "units":
            units = _edit_units(value)
        elif field == "restrictions":
            cols["restrictions"] = json.dumps(_edit_restrictions(value))
        elif field in ("start_date", "end_date"):
            cols[field] = to_iso(_parse_date(field, str(value)))
        elif field == "weapon":
            cols["weapon"] = _edit_choice(value, "weapon", WEAPONS)
        elif field == "sex":
            cols["sex"] = _edit_choice(value, "sex", SEXES)
        elif field == "tag_type_id":
            cols["tag_type_id"] = _edit_choice(
                value, "tag_type_id", tuple(t["id"] for t in _tag_types())
            )
        else:
            cols[field] = _text(value, field)
    start = cols.get("start_date", row["start_date"])
    end = cols.get("end_date", row["end_date"])
    if start > end:
        raise ApiError(
            "validation_failed",
            f"start_date {start} is after end_date {end}",
            {"start_date": "invalid", "end_date": "invalid"},
        )
    return cols, units


@api.put("/documents/<int:doc_id>/seasons/<int:season_id>")
def review_season(doc_id: int, season_id: int):
    """Review and/or correct one staged row by hand (§8.4).

    `{"status": "approved"|"rejected"}` is the review action — this is what clears an
    `extract_agree = 0` row for the publish gate. Any of `EDITABLE_FIELDS` corrects the
    row in place (T-15c's inline editors); an edit on its own deliberately does **not**
    approve the row, so a corrected row is still reviewed explicitly. Both halves may
    arrive in one request, which is what `e` → edit → `a` does from the keyboard.
    """
    _document_row(doc_id)
    row = _season_row(doc_id, season_id)
    body = _body()
    status = body.get("status")
    edits = [f for f in EDITABLE_FIELDS if f in body]
    if status is None and not edits:
        raise ApiError(
            "validation_failed",
            f"send a status ({'/'.join(REVIEW_STATUSES)}) or one of: {', '.join(EDITABLE_FIELDS)}",
            {"status": "missing"},
        )
    if status is not None and status not in REVIEW_STATUSES:
        raise ApiError(
            "validation_failed",
            f"status must be one of {', '.join(REVIEW_STATUSES)}",
            {"status": "invalid"},
        )
    if row["status"] not in REVIEWABLE_STATUSES:
        raise ApiError(
            "already_published",
            f"season {season_id} is {row['status']} and can no longer be reviewed",
            {"season_id": season_id, "status": row["status"]},
            status=409,
        )
    cols, units = _season_edits(row, body)
    if cols or units is not None:
        # A hand edit sends the row (and its section) back to review until someone
        # decides on it — §12 T-22g. `status` in the same request clears it again.
        cols["edited_at"] = _now()
    if status is not None:
        cols.update({"status": status, "reviewed_by_user": 1, "reviewed_at": _now()})
    with db.tx():
        if cols:
            assignments = ", ".join(f"{c} = ?" for c in cols)
            db.exec(f"UPDATE seasons SET {assignments} WHERE id = ?", *cols.values(), season_id)
        if units is not None:
            db.exec("DELETE FROM season_units WHERE season_id = ?", season_id)
            db.exec_many(
                "INSERT INTO season_units (season_id, unit_id) VALUES (?, ?)",
                [(season_id, u) for u in units],
            )
    return jsonify(
        {
            "season": _season_dict(doc_id, season_id),
            "edited": sorted(edits),
            "publish": _publish_state(doc_id),
        }
    )


@api.post("/documents/<int:doc_id>/approve-all")
def approve_all(doc_id: int):
    """Bulk approve, §8.4's one allowed form: rows the checks agree on.

    A row qualifies only when both extraction paths agreed (`extract_agree = 1`), the
    parser scored it 1.0 **and** every check of its section passed — the same definition
    of "clean" the T-22g routing uses, so the two can never disagree. There is no
    confidence-only bulk approve, and a waived check never makes its section eligible:
    those rows are approved one by one.

    Since T-22g a clean row no longer *needs* approving to publish, so this is now a
    convenience (it records who looked), not the way out of the gate.
    """
    _document_row(doc_id)
    clean = {s["section"] for s in _sections_for(_checks_for(doc_id)) if s["clean"]}
    staged = _row_refs("status = 'staged'", doc_id)
    approved = [r for r in staged if not _row_needs_review(r) and (r["section"] or "") in clean]
    skipped = [r for r in staged if r not in approved]
    if approved:
        now = _now()
        db.exec_many(
            "UPDATE seasons SET status = 'approved', reviewed_by_user = 1, reviewed_at = ?"
            " WHERE id = ?",
            [(now, r["id"]) for r in approved],
        )

    def _why(r: dict[str, Any]) -> str:
        if not r["extract_agree"]:
            return "extract_disagreement"
        if _confidence(r) < 1.0:
            return "low_confidence"
        if r["edited_at"] and not r["reviewed_by_user"]:
            return "edited_not_reviewed"
        return "section_has_failing_check"

    return jsonify(
        {
            "approved": [r["id"] for r in approved],
            "skipped": [
                {"id": r["id"], "section": r["section"], "reason": _why(r)} for r in skipped
            ],
            "clean_sections": sorted(clean),
            "publish": _publish_state(doc_id),
        }
    )


def _supersede(doc_row, season_ids: list[int]) -> list[int]:
    """§8.5: older published rows of the same year that this document replaces."""
    superseded: list[int] = []
    for new_id in season_ids:
        s = db.q1("SELECT * FROM seasons WHERE id = ?", new_id)
        olds = db.q(
            "SELECT s.id FROM seasons s JOIN source_documents d ON d.id = s.document_id"
            " WHERE s.status = 'published' AND s.document_id != ? AND d.license_year = ?"
            " AND d.priority < ? AND s.species_id = ? AND s.tag_type_id = ?"
            " AND IFNULL(s.zone, '') = ? AND IFNULL(s.hunt_number, '') = ?"
            " AND s.weapon = ? AND s.sex = ?"
            " AND EXISTS (SELECT 1 FROM season_units a JOIN season_units b"
            "             ON a.unit_id = b.unit_id WHERE a.season_id = s.id AND b.season_id = ?)",
            doc_row["id"],
            doc_row["license_year"],
            doc_row["priority"],
            s["species_id"],
            s["tag_type_id"],
            s["zone"] or "",
            s["hunt_number"] or "",
            s["weapon"],
            s["sex"],
            new_id,
        )
        for old in olds:
            db.exec(
                "UPDATE seasons SET status = 'superseded', supersedes_id = ? WHERE id = ?",
                new_id,
                old["id"],
            )
            superseded.append(int(old["id"]))
    return superseded


@api.post("/documents/<int:doc_id>/publish")
def publish_document(doc_id: int):
    """Publish a document's rows — when the §8.4 gate is satisfied, or by override.

    The gate: (1) no **uncertain** row is still `staged` (confidence < 1.0 or the two
    extraction paths disagreed — a clean row publishes with the document, T-22g),
    (2) every check is `pass`, `skipped` or waived with a reason, and (3) every
    `extract_agree = 0` row was reviewed individually. Otherwise 409 `publish_blocked`,
    naming every blocking check and row.

    **Override (T-22g, Josh's call).** `{"override": {"reason": "…", "by": "…"}}`
    publishes anyway: the blockers it bypassed are stored on the document forever
    (`publish_override_*`) and every row it pushed through unreviewed is flagged
    `published_unreviewed = 1`, so the app can say which seasons no human confirmed. An
    override without a written reason is a 400 `missing_reason` — the same rule as a
    waiver, for the same reason (D3).
    """
    row = _document_row(doc_id)
    if row["status"] == "published":
        raise ApiError(
            "already_published",
            f"document {doc_id} is already published",
            {"document_id": doc_id, "published_at": row["published_at"]},
            status=409,
        )
    # Publish takes no body in the normal case, so an empty POST stays valid.
    body = request.get_json(silent=True)
    body = body if isinstance(body, dict) else {}
    override = body.get("override")
    reason = None
    by = "user"
    if override:
        source = override if isinstance(override, dict) else body
        raw = source.get("reason")
        if not isinstance(raw, str) or not raw.strip():
            raise ApiError(
                "missing_reason",
                "publishing past the review gate needs a written reason",
                {"field": "reason"},
                status=400,
            )
        reason = raw.strip()
        by = _text(source.get("by"), "by") or "user"

    blockers = _publish_blockers(doc_id)
    blocked = bool(blockers["checks"] or blockers["rows"])
    if blocked and reason is None:
        raise ApiError(
            "publish_blocked",
            "the §8.7 review gate is not satisfied",
            blockers,
            status=409,
        )
    overridden = blocked and reason is not None
    groups = _publishable_rows(doc_id)
    ids = sorted(groups["approved"] + groups["auto"] + (groups["unreviewed"] if overridden else []))
    unreviewed = sorted(groups["unreviewed"]) if overridden else []
    with db.tx():
        if ids:
            db.exec_many(
                "UPDATE seasons SET status = 'published' WHERE id = ?", [(i,) for i in ids]
            )
        if unreviewed:
            db.exec_many(
                "UPDATE seasons SET published_unreviewed = 1 WHERE id = ?",
                [(i,) for i in unreviewed],
            )
        superseded = _supersede(row, ids)
        db.exec(
            "UPDATE source_documents SET status = 'published', published_at = ? WHERE id = ?",
            _now(),
            doc_id,
        )
        if overridden:
            db.exec(
                "UPDATE source_documents SET publish_override_reason = ?, publish_override_by = ?,"
                " publish_override_at = ?, publish_override_detail = ? WHERE id = ?",
                reason,
                by,
                _now(),
                json.dumps(
                    {
                        "checks": blockers["checks"],
                        "rows": blockers["rows"],
                        "published_unreviewed": unreviewed,
                    }
                ),
                doc_id,
            )
    return jsonify(
        {
            "document": _document_dict(_document_row(doc_id)),
            "published": ids,
            "auto_published": sorted(groups["auto"]),
            "published_unreviewed": unreviewed,
            "superseded": superseded,
        }
    )


# ───────── Season Table, manual rows and the CSV round-trip (T-16, §6 + §12) ─────────
#: `source_documents.kind` for rows a human typed or imported from CSV. One per license
#: year, created on demand; it has no PDF, so `sha256`/`stored_path` stay NULL.
MANUAL_KIND = "manual"
#: A manual row corrects what a brochure says, so it outranks every parsed document in
#: §8.5 supersession (`_supersede` compares `source_documents.priority`).
MANUAL_PRIORITY = 100
#: Row states the Season Table can list. `published` is what the map uses, and the
#: default; the others let the table show work in progress next to it.
LISTABLE_STATUSES = ("published", "staged", "approved", "rejected", "superseded")
#: `seasons.hunt_class` CHECK vocabulary, mirrored for a named error (as WEAPONS/SEXES).
HUNT_CLASSES = ("general", "controlled", "extra", "youth", "outfitter", "other")
#: Columns a manual row carries. Everything else on `seasons` is parser provenance.
MANUAL_REQUIRED = (
    "species_id",
    "tag_type_id",
    "hunt_class",
    "weapon",
    "sex",
    "start_date",
    "end_date",
    "units",
)


def _manual_document(year: int):
    """The `manual` source document for `year`, created on first use."""
    row = db.q1(
        "SELECT * FROM source_documents WHERE kind = ? AND license_year = ?", MANUAL_KIND, year
    )
    if row is not None:
        return row
    db.exec(
        "INSERT INTO source_documents (license_year, year_end, kind, title, priority, status,"
        " parser_version, imported_at, published_at) VALUES (?, ?, ?, ?, ?, 'published', ?, ?, ?)",
        year,
        year,
        MANUAL_KIND,
        f"Manual entries {year}",
        MANUAL_PRIORITY,
        MANUAL_KIND,
        _now(),
        _now(),
    )
    return db.q1(
        "SELECT * FROM source_documents WHERE kind = ? AND license_year = ?", MANUAL_KIND, year
    )


def _sync_manual_document(doc_id: int) -> None:
    """A manual doc is `needs_review` while CSV rows wait for review, else `published`."""
    staged = db.q1(
        "SELECT COUNT(*) AS n FROM seasons WHERE document_id = ? AND status = 'staged'", doc_id
    )
    status = "needs_review" if staged and staged["n"] else "published"
    db.exec(
        "UPDATE source_documents SET status = ?, published_at = CASE WHEN ? = 'published'"
        " THEN IFNULL(published_at, ?) ELSE published_at END WHERE id = ?",
        status,
        status,
        _now(),
        doc_id,
    )


_SEASON_SELECT = (
    "SELECT s.*, d.license_year AS license_year, d.year_end AS year_end, d.kind AS document_kind,"
    " d.title AS document_title, sp.label AS species_label, tt.label AS tag_type_label,"
    " GROUP_CONCAT(su.unit_id) AS units FROM seasons s"
    " JOIN source_documents d ON d.id = s.document_id"
    " LEFT JOIN species sp ON sp.id = s.species_id"
    " LEFT JOIN tag_types tt ON tt.id = s.tag_type_id"
    " LEFT JOIN season_units su ON su.season_id = s.id"
)


def _season_rows(where: str, *args: Any) -> list[dict[str, Any]]:
    """Season rows for the table and the CSV, each with its unit set and labels."""
    rows = _dicts(f"{_SEASON_SELECT} WHERE {where} GROUP BY s.id ORDER BY s.id", *args)
    for row in rows:
        row["restrictions"] = json.loads(row["restrictions"] or "[]")
        raw_units = (row["units"] or "").split(",") if row["units"] else []
        row["units"] = sorted(raw_units, key=_unit_sort)
        row["manual"] = row["document_kind"] == MANUAL_KIND
    return rows


def _season_filters() -> tuple[str, list[Any], dict[str, Any]]:
    """Translate the Season Table's query string into one WHERE clause."""
    args = request.args
    status = args.get("status", "published")
    clauses: list[str] = []
    params: list[Any] = []
    applied: dict[str, Any] = {"status": status}
    if status == "all":
        marks = ", ".join("?" * len(LISTABLE_STATUSES))
        clauses.append(f"s.status IN ({marks})")
        params.extend(LISTABLE_STATUSES)
    elif status in LISTABLE_STATUSES:
        clauses.append("s.status = ?")
        params.append(status)
    else:
        raise ApiError(
            "validation_failed",
            f"status must be 'all' or one of {', '.join(LISTABLE_STATUSES)}",
            {"status": "invalid"},
        )
    if args.get("year"):
        year = _year(args["year"])
        clauses.append("? BETWEEN d.license_year AND d.year_end")
        params.append(year)
        applied["year"] = year
    for param, column in (
        ("species", "s.species_id"),
        ("tag_type", "s.tag_type_id"),
        ("weapon", "s.weapon"),
        ("hunt_class", "s.hunt_class"),
    ):
        value = args.get(param)
        if value == "rifle":
            # 'rifle' has no rows of its own: it means the seasons open to any weapon.
            value = "any"
        if value:
            clauses.append(f"{column} = ?")
            params.append(value)
            applied[param] = args.get(param)
    if args.get("document"):
        clauses.append("s.document_id = ?")
        params.append(int(args["document"]))
        applied["document"] = int(args["document"])
    unit = (args.get("unit") or "").strip()
    if unit:
        # Unit *search*: '1' finds 1, 1A, 10 … so typing narrows instead of jumping.
        clauses.append(
            "EXISTS (SELECT 1 FROM season_units f WHERE f.season_id = s.id"
            " AND UPPER(f.unit_id) LIKE ?)"
        )
        params.append(f"{unit.upper()}%")
        applied["unit"] = unit
    q = (args.get("q") or "").strip()
    if q:
        clauses.append(
            "(IFNULL(s.hunt_number,'') LIKE ? OR IFNULL(s.hunt_area,'') LIKE ?"
            " OR IFNULL(s.notes,'') LIKE ? OR IFNULL(s.zone,'') LIKE ?)"
        )
        params.extend([f"%{q}%"] * 4)
        applied["q"] = q
    return " AND ".join(clauses), params, applied


@api.get("/seasons")
def list_seasons():
    """The Season Table (§6): filter by year, species, tag type, weapon, unit and status."""
    where, params, applied = _season_filters()
    rows = _season_rows(where, *params)
    rows.sort(key=seasons_csv.export_sort_key)
    return jsonify({"seasons": rows, "count": len(rows), "filters": applied})


def _manual_season_row(season_id: int):
    """A season that `POST /api/seasons` created — the only kind the editor may change."""
    row = db.q1(
        "SELECT s.*, d.kind AS document_kind, d.license_year AS license_year FROM seasons s"
        " JOIN source_documents d ON d.id = s.document_id WHERE s.id = ?",
        season_id,
    )
    if row is None:
        raise ApiError("unknown_season", f"no season {season_id}", {"season_id": season_id}, 404)
    if row["document_kind"] != MANUAL_KIND:
        raise ApiError(
            "not_manual",
            f"season {season_id} comes from a parsed document; correct it in the review screen",
            {"season_id": season_id, "document_id": row["document_id"]},
            status=409,
        )
    return row


def _manual_values(body: dict[str, Any], row=None) -> tuple[int, dict[str, Any], list[str] | None]:
    """Validate a manual-row body → (license year, `seasons` columns, unit set).

    `row` is the existing row on an edit, so a PUT may send only what changed.
    """
    present = (lambda f: f in body) if row is not None else (lambda f: True)
    missing = [f for f in MANUAL_REQUIRED if row is None and body.get(f) in (None, "", [])]
    if row is None and (body.get("license_year") in (None, "")):
        missing.insert(0, "license_year")
    if missing:
        raise ApiError(
            "validation_failed",
            f"missing required field(s): {', '.join(missing)}",
            {f: "missing" for f in missing},
        )
    year = (
        _year(body["license_year"])
        if present("license_year") and body.get("license_year") not in (None, "")
        else int(row["license_year"])
    )
    cols: dict[str, Any] = {}
    units: list[str] | None = None
    if present("units"):
        units = _edit_units(body["units"])
    if present("restrictions"):
        cols["restrictions"] = json.dumps(_edit_restrictions(body.get("restrictions") or []))
    for field, allowed in (
        ("weapon", WEAPONS),
        ("sex", SEXES),
        ("hunt_class", HUNT_CLASSES),
        ("species_id", tuple(s["id"] for s in _species())),
        ("tag_type_id", tuple(t["id"] for t in _tag_types())),
    ):
        if present(field):
            cols[field] = _edit_choice(body.get(field), field, allowed)
    for field in ("start_date", "end_date"):
        if present(field):
            cols[field] = to_iso(_parse_date(field, str(body.get(field))))
    for field in EDITABLE_TEXT_FIELDS + ("hunt_area",):
        if field in body:
            cols[field] = _text(body.get(field), field)
    if "source_page" in body:
        page = body["source_page"]
        if page in (None, ""):
            cols["source_page"] = None
        elif not isinstance(page, int) or isinstance(page, bool) or page < 1:
            raise ApiError(
                "validation_failed",
                "source_page must be a positive page number",
                {"source_page": "invalid"},
            )
        else:
            cols["source_page"] = page
    start = cols.get("start_date", row["start_date"] if row is not None else None)
    end = cols.get("end_date", row["end_date"] if row is not None else None)
    if start and end and start > end:
        raise ApiError(
            "validation_failed",
            f"start_date {start} is after end_date {end}",
            {"start_date": "invalid", "end_date": "invalid"},
        )
    return year, cols, units


def _insert_season(doc_id: int, values: dict[str, Any], units: list[str], status: str) -> int:
    """Write one manual row (`seasons` + `season_units`) and return its id."""
    cols = {
        "document_id": doc_id,
        "status": status,
        "restrictions": "[]",
        "extract_agree": 1,  # typed by a human: there is no second path to disagree with
        "confidence": 1.0,
        "reviewed_by_user": 1 if status != "staged" else 0,
        "section": MANUAL_KIND,
        **values,
    }
    if status != "staged":
        cols["reviewed_at"] = _now()
    names = ", ".join(cols)
    marks = ", ".join("?" * len(cols))
    cur = db.exec(f"INSERT INTO seasons ({names}) VALUES ({marks})", *cols.values())
    season_id = int(cur.lastrowid)
    db.exec_many(
        "INSERT INTO season_units (season_id, unit_id) VALUES (?, ?)",
        [(season_id, u) for u in units],
    )
    return season_id


def _one_season(season_id: int) -> dict[str, Any]:
    return _season_rows("s.id = ?", season_id)[0]


@api.post("/seasons")
def create_season():
    """Add a season by hand: published at once under this year's `manual` document.

    The row is typed by a human, so it skips staging — but it still goes through the
    same §5/§5.1 validation as a review edit, and §8.5 supersession runs so it replaces
    the brochure row it corrects.
    """
    body = _body()
    year, cols, units = _manual_values(body)
    doc = _manual_document(year)
    with db.tx():
        season_id = _insert_season(doc["id"], cols, units or [], "published")
        superseded = _supersede(doc, [season_id])
        _sync_manual_document(doc["id"])
    return (
        jsonify(
            {
                "season": _one_season(season_id),
                "document": _document_dict(_document_row(doc["id"])),
                "superseded": superseded,
            }
        ),
        201,
    )


@api.put("/seasons/<int:season_id>")
def update_season(season_id: int):
    """Edit a manual row in place. Parsed rows stay under the review screen (409)."""
    row = _manual_season_row(season_id)
    body = _body()
    year, cols, units = _manual_values(body, row)
    doc_id = int(row["document_id"])
    with db.tx():
        if year != int(row["license_year"]):
            doc_id = int(_manual_document(year)["id"])
            cols["document_id"] = doc_id
        if cols:
            assignments = ", ".join(f"{c} = ?" for c in cols)
            db.exec(f"UPDATE seasons SET {assignments} WHERE id = ?", *cols.values(), season_id)
        if units is not None:
            db.exec("DELETE FROM season_units WHERE season_id = ?", season_id)
            db.exec_many(
                "INSERT INTO season_units (season_id, unit_id) VALUES (?, ?)",
                [(season_id, u) for u in units],
            )
        _sync_manual_document(doc_id)
        if doc_id != int(row["document_id"]):
            _sync_manual_document(int(row["document_id"]))
    return jsonify({"season": _one_season(season_id)})


@api.delete("/seasons/<int:season_id>")
def delete_season(season_id: int):
    """Delete a manual row. `season_units` goes with it (ON DELETE CASCADE)."""
    row = _manual_season_row(season_id)
    with db.tx():
        db.exec("DELETE FROM season_units WHERE season_id = ?", season_id)
        db.exec("DELETE FROM seasons WHERE id = ?", season_id)
        _sync_manual_document(int(row["document_id"]))
    return jsonify({"deleted": season_id})


def _vocabulary() -> seasons_csv.Vocabulary:
    """What the seeded database allows, for CSV validation (never invent values, D3)."""
    return seasons_csv.Vocabulary(
        species=frozenset(s["id"] for s in _species()),
        tag_types=frozenset(t["id"] for t in _tag_types()),
        units=frozenset(str(r["id"]) for r in db.q("SELECT id FROM units")),
        restrictions=frozenset(engine.RESTRICTIONS),
    )


@api.get("/export.csv")
def export_csv():
    """Season CSV (§6). Same filters as `GET /api/seasons`; deterministic row order."""
    where, params, applied = _season_filters()
    rows = _season_rows(where, *params)
    for row in rows:
        # Canonical §5.1 order in the file, so export → import → export is byte-identical
        # whatever order a parser happened to store the codes in.
        row["restrictions"] = engine.sort_reasons(row["restrictions"])
    text = seasons_csv.to_csv(rows)
    name = f"ihp-seasons-{applied.get('year', 'all')}-{applied['status']}.csv"
    return Response(
        text,
        mimetype="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="{name}"',
            "X-Season-Count": str(len(rows)),
        },
    )


#: The 3-row example file the UI links to (shipped in `docs/`, §12 T-16).
SEASON_TEMPLATE_PATH = Path(__file__).resolve().parent.parent / "docs" / "seasons_template.csv"


@api.get("/seasons/template.csv")
def seasons_template():
    """The example CSV, so the import flow starts from a file with the right header."""
    # Bytes, not `read_text`: universal newlines would turn the file's CRLF into LF and
    # the served template would no longer be the file we ship.
    return Response(
        SEASON_TEMPLATE_PATH.read_bytes(),
        mimetype="text/csv",
        headers={"Content-Disposition": 'attachment; filename="seasons_template.csv"'},
    )


@api.post("/import.csv")
def import_csv():
    """Import a season CSV: every row is **staged** under its year's `manual` document.

    Strict by design (D3): one bad line fails the whole upload and the response names
    every problem with its line number, so nothing half-imports.
    """
    upload = request.files.get("file")
    if upload is not None and upload.filename:
        raw = upload.read()
    else:
        raw = request.get_data() or b""
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ApiError("invalid_csv", "the file is not UTF-8 text", {"errors": []}) from None
    if not text.strip():
        raise ApiError(
            "invalid_csv",
            "the file is empty: expected a header row and at least one season",
            {"errors": [], "rows": 0},
        )
    parsed = seasons_csv.parse_csv(text, _vocabulary())
    if parsed.errors:
        raise ApiError(
            "invalid_csv",
            f"{len(parsed.errors)} problem(s) in the CSV; nothing was imported",
            {
                "errors": [e.as_dict() for e in parsed.errors],
                "lines": sorted({e.line for e in parsed.errors}),
                "rows_ok": len(parsed.rows),
            },
        )
    if not parsed.rows:
        raise ApiError("invalid_csv", "no season rows in the file", {"errors": [], "rows": 0})
    created: list[int] = []
    docs: dict[int, int] = {}
    with db.tx():
        for row in parsed.rows:
            year = row.pop("license_year")
            units = row.pop("units")
            row["restrictions"] = json.dumps(engine.sort_reasons(row["restrictions"]))
            if year not in docs:
                docs[year] = int(_manual_document(year)["id"])
            created.append(_insert_season(docs[year], row, units, "staged"))
        for doc_id in docs.values():
            _sync_manual_document(doc_id)
    return (
        jsonify(
            {
                "imported": len(created),
                "season_ids": created,
                "documents": [
                    _document_dict(_document_row(doc_id)) for doc_id in sorted(docs.values())
                ],
                "status": "staged",
            }
        ),
        201,
    )
