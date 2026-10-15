"""REST endpoints (DESIGN.md §6). Mounted under `/api`."""

from __future__ import annotations

import datetime as _dt
import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from flask import Blueprint, Response, current_app, jsonify, request, send_file

from planner import __version__, db, engine, importer
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
            "weapons": list(engine.WEAPONS),
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
            # T-17c adds the real lookup against that year's published hunts.
            errors["hunt_number"] = "hunt numbers are digits (e.g. 1002)"
    else:
        hunt_number = None

    if errors:
        raise ApiError("validation_failed", "fix the highlighted fields", errors)
    return zone, hunt_number


def _tag_dict(row) -> dict[str, Any]:
    return {
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
    checks = _dicts(
        "SELECT * FROM import_checks WHERE document_id = ? ORDER BY section, check_name", doc_id
    )
    for c in checks:
        c["detail"] = json.loads(c["detail"]) if c["detail"] else None
    return jsonify({"document": _document_dict(row), "seasons": seasons, "checks": checks})


def _unit_sort(unit_id: str):
    from planner.importer.common import natural_key

    return natural_key(unit_id)


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
