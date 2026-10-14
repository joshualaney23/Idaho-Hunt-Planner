"""Seed reference data: species, tag types, units, default elk `zone_units` (T-04, §5).

Every insert is `INSERT OR IGNORE`, so `seed()` runs on every start without duplicating
rows or overwriting user edits. Default `zone_units` for a license year are written only
when that year has no rows yet, so a zone row the user deleted is not brought back.
"""

from __future__ import annotations

import datetime as _dt
import json
import sqlite3
from pathlib import Path
from typing import Any

from planner import db

REPO_ROOT = Path(__file__).resolve().parent.parent
UNITS_SEED_PATH = REPO_ROOT / "static" / "data" / "units_seed.json"

# (id, label, icon, grp): the §5 / §5.3 species list. Icons are the §12 T-04 set
# 🦌 🫎 🐻 🐾 🐺 🐦 (deer-like, moose/elk, bear, other mammals, wolf, birds).
SPECIES: list[tuple[str, str, str, str]] = [
    # Big Game brochure
    ("deer", "Deer", "🦌", "big_game"),
    ("elk", "Elk", "🫎", "big_game"),
    ("pronghorn", "Pronghorn", "🦌", "big_game"),
    ("black_bear", "Black bear", "🐻", "big_game"),
    ("mountain_lion", "Mountain lion", "🐾", "big_game"),
    ("wolf", "Gray wolf", "🐺", "big_game"),
    # Upland Game, Turkey & Furbearer brochure
    ("forest_grouse", "Forest grouse", "🐦", "upland_bird"),
    ("quail", "Quail", "🐦", "upland_bird"),
    ("chukar", "Chukar", "🐦", "upland_bird"),
    ("gray_partridge", "Gray partridge", "🐦", "upland_bird"),
    ("sage_grouse", "Sage-grouse", "🐦", "upland_bird"),
    ("sharptail", "Sharp-tailed grouse", "🐦", "upland_bird"),
    ("pheasant", "Pheasant", "🐦", "upland_bird"),
    ("turkey", "Wild turkey", "🐦", "turkey"),
    ("cottontail", "Cottontail rabbit", "🐾", "upland_animal"),
    ("snowshoe_hare", "Snowshoe hare", "🐾", "upland_animal"),
    ("red_squirrel", "Red squirrel", "🐾", "upland_animal"),
    ("badger", "Badger", "🐾", "furbearer"),
    ("bobcat", "Bobcat", "🐾", "furbearer"),
    ("red_fox", "Red fox", "🐾", "furbearer"),
    ("predatory_unprotected", "Predatory & unprotected", "🐾", "predatory"),
    # Moose, Bighorn Sheep & Mountain Goat brochure (controlled only)
    ("moose", "Moose", "🫎", "big_game"),
    ("bighorn_rocky", "Rocky Mountain bighorn sheep", "🐾", "big_game"),
    ("bighorn_california", "California bighorn sheep", "🐾", "big_game"),
    ("mountain_goat", "Mountain goat", "🐾", "big_game"),
    # Migratory Game Bird brochure
    ("duck", "Ducks & mergansers", "🐦", "migratory"),
    ("goose_canada", "Canada goose", "🐦", "migratory"),
    ("goose_whitefronted", "White-fronted goose", "🐦", "migratory"),
    ("goose_light", "Light geese", "🐦", "migratory"),
    ("swan", "Tundra swan", "🐦", "migratory"),
    ("snipe", "Wilson's snipe", "🐦", "migratory"),
    ("coot", "American coot", "🐦", "migratory"),
    ("dove", "Mourning & white-winged dove", "🐦", "migratory"),
    ("crow", "American crow", "🐦", "migratory"),
    ("sandhill_crane", "Sandhill crane", "🐦", "migratory"),
]

# (id, species_id, label, requires_zone, requires_hunt_no): the §5 tag_types list.
TAG_TYPES: list[tuple[str, str | None, str, int, int]] = [
    ("deer_regular", "deer", "Regular deer tag", 0, 0),
    ("deer_whitetail", "deer", "White-tailed deer tag", 0, 0),
    ("elk_zone_a", "elk", "Elk A tag", 1, 0),
    ("elk_zone_b", "elk", "Elk B tag", 1, 0),
    # Species comes from the hunt; moose/sheep/goat/swan are controlled-only.
    ("controlled", None, "Controlled hunt tag", 0, 1),
    ("pronghorn_general", "pronghorn", "Pronghorn general tag", 0, 0),
    ("black_bear", "black_bear", "Black bear tag", 0, 0),
    ("mountain_lion", "mountain_lion", "Mountain lion tag", 0, 0),
    ("wolf", "wolf", "Gray wolf tag", 0, 0),
    ("turkey_general", "turkey", "General turkey tag", 0, 0),
    # Upland birds/animals, furbearers, predatory, ducks/geese/snipe/coot/dove/crow.
    ("hunting_license", None, "Hunting license (no species tag)", 0, 0),
    # Area-specific; `zone` = crane hunt area.
    ("sandhill_crane", "sandhill_crane", "Sandhill crane tag", 1, 0),
]


def load_units(path: Path = UNITS_SEED_PATH) -> list[dict[str, Any]]:
    """`units_seed.json` rows: `{id, sort_key, elk_zone, area_acres}` (T-02 output)."""
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def seed(
    conn: sqlite3.Connection, year: int | None = None, units_path: Path = UNITS_SEED_PATH
) -> None:
    """Insert reference rows (idempotent) and default zone_units for `year` (default: now)."""
    year = year if year is not None else _dt.date.today().year
    units = load_units(units_path)
    with db.tx(conn):
        db.exec_many(
            "INSERT OR IGNORE INTO species (id, label, icon, grp) VALUES (?, ?, ?, ?)",
            SPECIES,
            conn=conn,
        )
        db.exec_many(
            "INSERT OR IGNORE INTO tag_types"
            " (id, species_id, label, requires_zone, requires_hunt_no) VALUES (?, ?, ?, ?, ?)",
            TAG_TYPES,
            conn=conn,
        )
        db.exec_many(
            "INSERT OR IGNORE INTO units (id, sort_key, elk_zone, area_acres) VALUES (?, ?, ?, ?)",
            [(u["id"], u["sort_key"], u["elk_zone"], u["area_acres"]) for u in units],
            conn=conn,
        )
        seed_zone_units(conn, year)


def seed_zone_units(conn: sqlite3.Connection, year: int) -> int:
    """Copy GIS `units.elk_zone` into `zone_units` for `year` unless it has rows already.

    Returns the number of rows inserted. Units without an elk zone (YNP) are skipped.
    """
    if db.q1("SELECT 1 FROM zone_units WHERE license_year = ?", year, conn=conn):
        return 0
    cur = db.exec(
        "INSERT OR IGNORE INTO zone_units (license_year, zone, unit_id, coverage)"
        " SELECT ?, elk_zone, id, 'full' FROM units WHERE elk_zone IS NOT NULL",
        year,
        conn=conn,
    )
    return cur.rowcount
