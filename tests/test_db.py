"""T-04: schema, migrations, seed, FK enforcement, query helpers, RESTRICTIONS."""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

import pytest

from planner import db, seed
from planner.app import create_app
from planner.engine import RESTRICTIONS

REPO_ROOT = Path(__file__).resolve().parent.parent
DESIGN = (REPO_ROOT / "docs" / "DESIGN.md").read_text(encoding="utf-8")
TABLES = {
    "schema_version", "units", "species", "tag_types", "zone_units", "source_documents",
    "seasons", "season_units", "hunt_areas", "import_checks", "profile", "my_permits", "my_tags",
}  # fmt: skip


def _design_section(heading: str) -> str:
    start = DESIGN.index(heading)
    nxt = re.search(r"^#{2,3} ", DESIGN[start + len(heading) :], re.M)
    return DESIGN[start : start + len(heading) + (nxt.start() if nxt else len(DESIGN))]


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "t.db")
    yield c
    c.close()


@pytest.fixture
def seeded(conn):
    db.migrate(conn)
    seed.seed(conn, year=2026)
    return conn


def _count(conn, table: str, where: str = "", *args) -> int:
    return conn.execute(f"SELECT COUNT(*) FROM {table} {where}", args).fetchone()[0]


# ───────── schema & migrations ─────────
def test_schema_sql_matches_design_section_5():
    block = re.search(r"```sql\n(.*?)```", _design_section("## 5. Data model"), re.S).group(1)
    sql = (REPO_ROOT / "planner" / "schema.sql").read_text(encoding="utf-8")
    assert block.strip() in sql


def test_connect_pragmas(conn):
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert isinstance(conn.execute("SELECT 1 AS one").fetchone(), sqlite3.Row)


def test_migrate_fresh_db_and_rerun(conn):
    assert db.schema_version(conn) == 0
    assert db.migrate(conn) == 1
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert TABLES <= names
    assert [tuple(r) for r in conn.execute("SELECT version FROM schema_version")] == [(1,)]
    assert db.migrate(conn) == 1  # no-op, no second version row
    assert _count(conn, "schema_version") == 1


def test_migrations_apply_in_order_once(conn, tmp_path):
    mdir = tmp_path / "migrations"
    mdir.mkdir()
    (mdir / "003_b.sql").write_text("ALTER TABLE probe ADD COLUMN b TEXT;")
    (mdir / "002_a.sql").write_text("CREATE TABLE probe (a TEXT);")
    (mdir / "README.md").write_text("ignored")
    assert db.migrate(conn, mdir) == 3
    cols = [r["name"] for r in conn.execute("PRAGMA table_info(probe)")]
    assert cols == ["a", "b"]
    assert db.migrate(conn, mdir) == 3  # already applied: CREATE TABLE would fail if rerun
    assert [tuple(r) for r in conn.execute("SELECT version FROM schema_version")] == [(3,)]


def test_failed_migration_rolls_back(conn, tmp_path):
    mdir = tmp_path / "migrations"
    mdir.mkdir()
    (mdir / "002_bad.sql").write_text("CREATE TABLE half (x);\nTHIS IS NOT SQL;")
    with pytest.raises(sqlite3.OperationalError):
        db.migrate(conn, mdir)
    assert db.schema_version(conn) == 1
    assert not conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'half'").fetchone()
    assert not conn.in_transaction


def test_migration_numbering_rules(tmp_path):
    (tmp_path / "001_dup.sql").write_text("")
    with pytest.raises(RuntimeError, match="> 1"):
        db.migration_files(tmp_path)
    (tmp_path / "001_dup.sql").unlink()
    (tmp_path / "002_a.sql").write_text("")
    (tmp_path / "002_b.sql").write_text("")
    with pytest.raises(RuntimeError, match="duplicate"):
        db.migration_files(tmp_path)


def test_repo_migrations_dir_is_valid():
    db.migration_files()  # raises on bad names/numbers


# ───────── seed ─────────
def test_seed_counts(seeded):
    assert _count(seeded, "units") >= 100
    assert _count(seeded, "tag_types") >= 11
    assert _count(seeded, "species") == len(seed.SPECIES) == 35
    # every unit with a GIS elk zone gets a default zone_units row for the year
    with_zone = _count(seeded, "units", "WHERE elk_zone IS NOT NULL")
    assert _count(seeded, "zone_units", "WHERE license_year = 2026") == with_zone == 99


def test_seed_matches_units_seed_json(seeded):
    expected = json.loads((REPO_ROOT / "static/data/units_seed.json").read_text())
    rows = seeded.execute(
        "SELECT id, sort_key, elk_zone, area_acres FROM units ORDER BY sort_key"
    ).fetchall()
    assert [dict(r) for r in rows] == sorted(expected, key=lambda u: u["sort_key"])
    four_a = seeded.execute("SELECT * FROM units WHERE id = '4A'").fetchone()
    assert (four_a["sort_key"], four_a["elk_zone"]) == (41, "Panhandle")
    ynp = seeded.execute("SELECT elk_zone FROM units WHERE id = 'YNP'").fetchone()
    assert ynp["elk_zone"] is None
    zone = seeded.execute(
        "SELECT zone, coverage FROM zone_units WHERE license_year = 2026 AND unit_id = '4A'"
    ).fetchone()
    assert tuple(zone) == ("Panhandle", "full")


def test_species_ids_match_design():
    sql = _design_section("## 5. Data model")
    comment = sql[sql.index("CREATE TABLE species") : sql.index("CREATE TABLE tag_types")]
    design_ids = set(re.findall(r"'([a-z_]+)'", comment.split("grp:")[0]))
    assert {s[0] for s in seed.SPECIES} == design_ids
    grps = set(re.findall(r"'([a-z_]+)'", comment.split("grp:")[1]))
    assert {s[3] for s in seed.SPECIES} <= grps
    icons = {s[0]: s[2] for s in seed.SPECIES}
    assert (icons["turkey"], icons["mountain_lion"]) == ("🦃", "🐆")
    assert (icons["bighorn_rocky"], icons["bighorn_california"], icons["mountain_goat"]) == (
        "🐏",
        "🐏",
        "🐐",
    )
    assert "🐾" not in {s[2] for s in seed.SPECIES}  # every species gets an animal, not a track


def test_tag_types_match_design(seeded):
    sql = _design_section("## 5. Data model")
    comment = sql[sql.index("CREATE TABLE tag_types") : sql.index("species_id       TEXT")]
    design_ids = set(re.findall(r"'([a-z_]+)'", comment))
    rows = {r["id"]: r for r in seeded.execute("SELECT * FROM tag_types")}
    assert set(rows) == design_ids
    assert {k for k, r in rows.items() if r["requires_zone"]} == {
        "elk_zone_a",
        "elk_zone_b",
        "sandhill_crane",
    }
    assert {k for k, r in rows.items() if r["requires_hunt_no"]} == {"controlled"}
    assert rows["controlled"]["species_id"] is None
    assert rows["hunting_license"]["species_id"] is None


def test_seed_is_idempotent(seeded):
    before = {t: _count(seeded, t) for t in ("units", "species", "tag_types", "zone_units")}
    seed.seed(seeded, year=2026)
    seed.seed(seeded, year=2026)
    assert {t: _count(seeded, t) for t in before} == before


def test_seed_keeps_user_zone_edits(seeded):
    seeded.execute("DELETE FROM zone_units WHERE license_year = 2026 AND unit_id = '1'")
    seeded.execute("UPDATE species SET label = 'Muley' WHERE id = 'deer'")
    seed.seed(seeded, year=2026)
    assert _count(seeded, "zone_units", "WHERE license_year = 2026") == 98
    assert seeded.execute("SELECT label FROM species WHERE id = 'deer'").fetchone()[0] == "Muley"
    seed.seed(seeded, year=2027)  # a new license year gets its own GIS defaults
    assert _count(seeded, "zone_units", "WHERE license_year = 2027") == 99


# ───────── FK & constraint enforcement ─────────
def _season(conn) -> int:
    doc = conn.execute(
        "INSERT INTO source_documents (license_year, year_end, kind, status)"
        " VALUES (2026, 2026, 'big_game', 'needs_review')"
    ).lastrowid
    return conn.execute(
        "INSERT INTO seasons (document_id, status, species_id, tag_type_id, hunt_class, weapon,"
        " sex, start_date, end_date) VALUES (?, 'staged', 'deer', 'deer_regular', 'general',"
        " 'any', 'antlered', '2026-10-10', '2026-10-24')",
        (doc,),
    ).lastrowid


def test_fk_rejects_unknown_unit(seeded):
    sid = _season(seeded)
    seeded.execute("INSERT INTO season_units (season_id, unit_id) VALUES (?, '39')", (sid,))
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
        seeded.execute("INSERT INTO season_units (season_id, unit_id) VALUES (?, '999Z')", (sid,))


def test_fk_rejects_unknown_species_and_cascades(seeded):
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
        seeded.execute(
            "INSERT INTO zone_units (license_year, zone, unit_id) VALUES (2026, 'X', 'nope')"
        )
    sid = _season(seeded)
    seeded.execute("INSERT INTO season_units (season_id, unit_id) VALUES (?, '39')", (sid,))
    seeded.execute("DELETE FROM source_documents")  # ON DELETE CASCADE → seasons → season_units
    assert _count(seeded, "seasons") == 0
    assert _count(seeded, "season_units") == 0


def test_check_constraints(seeded):
    with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
        seeded.execute("INSERT INTO profile (id) VALUES (2)")
    with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
        seeded.execute("INSERT INTO my_permits VALUES (2026, 'nonresident')")


# ───────── helpers ─────────
def test_query_helpers_and_tx(seeded):
    with db.use(seeded):
        assert len(db.q("SELECT id FROM units")) >= 100
        assert db.q1("SELECT label FROM species WHERE id = ?", "elk")["label"] == "Elk"
        assert db.q1("SELECT 1 FROM units WHERE id = ?", "nope") is None
        with db.tx():
            db.exec("INSERT INTO my_permits VALUES (?, ?)", 2026, "archery")
        with pytest.raises(sqlite3.IntegrityError), db.tx():
            db.exec("INSERT INTO my_permits VALUES (?, ?)", 2026, "muzzleloader")
            db.exec("INSERT INTO my_permits VALUES (?, ?)", 2026, "bogus")  # CHECK fails
        permits = [r[0] for r in db.q("SELECT permit FROM my_permits")]
        assert permits == ["archery"]  # muzzleloader rolled back with the failed tx


def test_nested_tx_uses_savepoint(seeded):
    with db.use(seeded), db.tx():
        db.exec("INSERT INTO my_permits VALUES (2026, 'archery')")
        with pytest.raises(RuntimeError), db.tx():
            db.exec("INSERT INTO my_permits VALUES (2026, 'wmb')")
            raise RuntimeError("inner fails")
    assert [r[0] for r in seeded.execute("SELECT permit FROM my_permits")] == ["archery"]


def test_helpers_need_a_connection():
    with pytest.raises(RuntimeError, match="no current DB connection"):
        db.q("SELECT 1")


# ───────── app wiring ─────────
def test_first_run_creates_db_and_rerun_does_not_duplicate(tmp_path):
    path = tmp_path / "sub" / "planner.db"
    assert not path.exists()
    app = create_app(db_path=path)
    assert path.exists()
    with app.app_context():
        counts = {t: db.q1(f"SELECT COUNT(*) FROM {t}")[0] for t in ("units", "tag_types")}
        assert counts["units"] >= 100 and counts["tag_types"] >= 11
    create_app(db_path=path)  # second start
    with app.app_context():
        assert {t: db.q1(f"SELECT COUNT(*) FROM {t}")[0] for t in counts} == counts
        assert db.q1("SELECT COUNT(*) FROM schema_version")[0] == 1


# ───────── RESTRICTIONS (§5.1) ─────────
def test_restrictions_match_design_5_1():
    text = _design_section("### 5.1 Restriction codes")
    vocab_line = text.split("\n")[1]
    codes = re.findall(r"`([a-z_]+)`", vocab_line)
    assert list(RESTRICTIONS) == codes
    informational = {
        "cwd_testing",
        "motorized_rule",
        "second_tag_prohibited",
        "antlered_only",
        "antlerless_only",
        "two_point_only",
        "spike_only",
        "brow_tined",
    }
    for code, r in RESTRICTIONS.items():
        assert r["label"]
        expected = "informational" if code in informational else "narrowing"
        assert r["severity"] == expected, code
