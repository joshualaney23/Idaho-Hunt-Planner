"""REST endpoints (DESIGN.md §6). Mounted under `/api`."""

from __future__ import annotations

import datetime as _dt
import json
from typing import Any

from flask import Blueprint, Response, jsonify, request

from planner import __version__, db, engine
from planner.dates import DEFAULT_WEEKEND_DEF, WEEKEND_DEFS, default_range, parse_iso, to_iso

api = Blueprint("api", __name__, url_prefix="/api")

PROFILE_DEFAULTS: dict[str, Any] = {
    "youth": 0,
    "weekend_def": DEFAULT_WEEKEND_DEF,
    "default_species": "deer",
}


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
