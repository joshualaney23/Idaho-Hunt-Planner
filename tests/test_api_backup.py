"""T-21: `GET /api/backup`, `POST /api/restore`, `POST /api/reset` (DESIGN.md §6).

The headline test is the ticket's acceptance criterion: back up, reset, restore, and
`/api/availability` answers byte-for-byte what it did before.
"""

from __future__ import annotations

import io
import sqlite3
from pathlib import Path

import pytest

from planner import backup, db

AVAIL = "/api/availability?species=deer&start=2026-10-17&end=2026-10-19&include_untagged=1"


def _doc(conn, year: int = 2026, status: str = "published") -> int:
    return db.exec(
        "INSERT INTO source_documents (license_year, year_end, kind, title, status)"
        " VALUES (?, ?, 'big_game', ?, ?)",
        year,
        year,
        f"big_game {year}",
        status,
        conn=conn,
    ).lastrowid


def _season(conn, doc: int, units: list[str], **kw) -> int:
    row = {
        "status": "published",
        "species_id": "deer",
        "tag_type_id": "deer_regular",
        "hunt_class": "general",
        "weapon": "any",
        "sex": "either",
        "start_date": "2026-10-10",
        "end_date": "2026-10-24",
        "restrictions": "[]",
        "source_page": 12,
    }
    row.update(kw)
    cols = ", ".join(["document_id", *row])
    marks = ", ".join("?" * (len(row) + 1))
    sid = db.exec(
        f"INSERT INTO seasons ({cols}) VALUES ({marks})", doc, *row.values(), conn=conn
    ).lastrowid
    db.exec_many(
        "INSERT INTO season_units (season_id, unit_id) VALUES (?, ?)",
        [(sid, u) for u in units],
        conn=conn,
    )
    return sid


@pytest.fixture
def conn(app):
    c = db.connect(app.config["DB_PATH"])
    yield c
    c.close()


@pytest.fixture
def seeded(client, conn):
    """A DB with published seasons, a tag, a permit and a non-default profile."""
    doc = _doc(conn)
    _season(conn, doc, ["39", "40"])
    _season(conn, doc, ["41"], restrictions='["whitetail_only"]')
    _season(conn, doc, ["42"], weapon="archery")
    db.exec(
        "INSERT INTO my_tags (license_year, tag_type_id, filled, created_at)"
        " VALUES (2026, 'deer_regular', 0, '2026-01-01')",
        conn=conn,
    )
    db.exec("INSERT INTO my_permits (license_year, permit) VALUES (2026, 'archery')", conn=conn)
    client.put("/api/profile", json={"youth": True, "weekend_def": "sat_sun"})
    return doc


def _bak_files(app) -> list[Path]:
    db_path = Path(app.config["DB_PATH"])
    return sorted(db_path.parent.glob(f"{db_path.name}.bak-*"))


# ───────── the acceptance criterion ─────────
def test_backup_reset_restore_returns_identical_availability(client, app, seeded):
    before = client.get(AVAIL).get_json()
    assert before["summary"]["open"] > 0, "the fixture must produce a non-trivial answer"
    before_tags = client.get("/api/tags?year=2026").get_json()
    before_profile = client.get("/api/profile").get_json()

    dump = client.get("/api/backup")
    assert dump.status_code == 200
    data = dump.data
    assert backup.looks_like_sqlite(data)

    reset = client.post("/api/reset", json={"confirm": "RESET"})
    assert reset.status_code == 200, reset.get_json()
    wiped = client.get(AVAIL).get_json()
    assert wiped["summary"]["open"] == 0
    assert wiped != before
    assert client.get("/api/tags?year=2026").get_json()["tags"] == []

    restored = client.post("/api/restore", data={"file": (io.BytesIO(data), "planner.db")})
    assert restored.status_code == 200, restored.get_json()
    assert restored.get_json()["restored"] is True

    after = client.get(AVAIL).get_json()
    assert after == before
    assert client.get("/api/tags?year=2026").get_json() == before_tags
    assert client.get("/api/profile").get_json() == before_profile
    # Both destructive steps kept the database they replaced.
    assert len(_bak_files(app)) == 2


# ───────── backup ─────────
def test_backup_is_a_consistent_openable_snapshot(client, app, seeded, tmp_path):
    resp = client.get("/api/backup")
    assert resp.mimetype == "application/vnd.sqlite3"
    disposition = resp.headers["Content-Disposition"]
    assert "attachment" in disposition and "planner-backup-" in disposition
    assert resp.headers["Content-Length"] == str(len(resp.data))

    copy = tmp_path / "dump.db"
    copy.write_bytes(resp.data)
    assert backup.inspect(copy) == backup.latest_version()
    conn = sqlite3.connect(copy)
    try:
        assert conn.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        assert conn.execute("SELECT COUNT(*) FROM seasons").fetchone()[0] == 3
        # The snapshot carries no WAL sidecar: everything is in the one file.
        assert conn.execute("SELECT COUNT(*) FROM units").fetchone()[0] > 0
    finally:
        conn.close()
    assert not copy.with_name(copy.name + "-wal").exists()


def test_download_name_is_timestamped_and_sorts_chronologically():
    import datetime as dt

    early = dt.datetime(2026, 10, 16, 9, 5, 0)
    late = dt.datetime(2026, 10, 16, 9, 5, 1)
    names = [backup.download_name(late), backup.download_name(early)]
    assert sorted(names) == [backup.download_name(early), backup.download_name(late)]
    assert backup.download_name(early) == "planner-backup-20261016T090500.db"


# ───────── restore validation ─────────
def test_restore_rejects_a_non_sqlite_file(client, seeded):
    before = client.get(AVAIL).get_json()
    resp = client.post(
        "/api/restore", data=b"not a database", content_type="application/octet-stream"
    )
    assert resp.status_code == 415
    assert resp.get_json()["error"]["code"] == "invalid_backup"
    assert client.get(AVAIL).get_json() == before  # nothing touched


def test_restore_rejects_some_other_sqlite_database(client, seeded, tmp_path):
    other = tmp_path / "other.db"
    conn = sqlite3.connect(other)
    conn.execute("CREATE TABLE notes (id INTEGER)")
    conn.commit()
    conn.close()
    resp = client.post("/api/restore", data={"file": (io.BytesIO(other.read_bytes()), "other.db")})
    assert resp.status_code == 422
    body = resp.get_json()["error"]
    assert body["code"] == "invalid_backup"
    assert "schema_version" in body["detail"]["missing_tables"]
    assert client.get(AVAIL).get_json()["summary"]["open"] > 0


def test_restore_rejects_a_newer_schema_version(client, app, seeded, tmp_path):
    newer = tmp_path / "newer.db"
    backup.snapshot_to(db.connect(app.config["DB_PATH"]), newer)
    conn = sqlite3.connect(newer)
    conn.execute("UPDATE schema_version SET version = ?", (backup.latest_version() + 5,))
    conn.commit()
    conn.close()
    resp = client.post("/api/restore", data={"file": (io.BytesIO(newer.read_bytes()), "n.db")})
    assert resp.status_code == 409
    err = resp.get_json()["error"]
    assert err["code"] == "incompatible_backup"
    assert err["detail"]["supported"] == backup.latest_version()


def test_restore_without_a_file_is_a_400(client):
    resp = client.post("/api/restore", data={})
    assert resp.status_code == 400
    assert resp.get_json()["error"]["code"] == "missing_file"


def test_restore_migrates_an_older_backup_forward(client, app, tmp_path):
    """A backup written at schema v1 is accepted and brought up to date."""
    old = tmp_path / "old.db"
    conn = db.connect(old)
    db.migrate(conn)
    from planner import seed

    seed.seed(conn)
    conn.execute("UPDATE schema_version SET version = 1")
    # Undo everything migrations 002, 004 and 005 added, so the file really is a v1 database.
    conn.execute("DROP TABLE fetch_log")
    conn.execute("ALTER TABLE source_documents DROP COLUMN replaces_id")
    conn.execute("ALTER TABLE seasons DROP COLUMN section")
    conn.execute("ALTER TABLE seasons DROP COLUMN reviewed_at")
    conn.execute("ALTER TABLE import_checks DROP COLUMN waived_by")
    conn.execute("ALTER TABLE import_checks DROP COLUMN waived_at")
    conn.execute("ALTER TABLE seasons DROP COLUMN published_unreviewed")
    conn.execute("ALTER TABLE seasons DROP COLUMN edited_at")
    for column in ("reason", "by", "at", "detail"):
        conn.execute(f"ALTER TABLE source_documents DROP COLUMN publish_override_{column}")
    conn.commit()
    conn.close()
    assert backup.inspect(old) == 1

    resp = client.post("/api/restore", data={"file": (io.BytesIO(old.read_bytes()), "old.db")})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["restored_from"] == 1
    assert body["schema_version"] == backup.latest_version()
    # The migrated column is back, so the review gate still works.
    assert client.get("/api/seasons?status=all").status_code == 200


# ───────── reset ─────────
def test_reset_requires_the_typed_confirmation(client, app, seeded):
    for body in ({}, {"confirm": ""}, {"confirm": "reset"}, {"confirm": "yes"}):
        resp = client.post("/api/reset", json=body)
        assert resp.status_code == 400, body
        assert resp.get_json()["error"]["code"] == "confirmation_required"
    assert client.get(AVAIL).get_json()["summary"]["open"] > 0
    assert _bak_files(app) == []  # nothing was backed up, because nothing was deleted


def test_reset_clears_user_data_but_keeps_reference_data(client, app, seeded):
    data_dir = Path(app.config["DATA_DIR"])
    (data_dir / "regs" / "2026").mkdir(parents=True, exist_ok=True)
    (data_dir / "regs" / "2026" / "x.pdf").write_bytes(b"%PDF-1.4\n")
    (data_dir / "cache").mkdir(parents=True, exist_ok=True)

    resp = client.post("/api/reset", json={"confirm": "RESET"})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["reset"] is True
    assert sorted(body["removed"]) == ["cache", "regs"]
    assert not (data_dir / "regs").exists()
    assert Path(body["backup"]).is_file()

    assert client.get("/api/tags?year=2026").get_json()["tags"] == []
    assert client.get("/api/permits?year=2026").get_json()["permits"] == []
    assert client.get("/api/documents").get_json()["documents"] == []
    # Seeded reference data survives, so the map still draws.
    assert len(client.get("/api/units").get_json()["units"]) > 0
    assert client.get("/api/meta").get_json()["species"]
    # And the profile is back to its defaults (§6 PROFILE_DEFAULTS).
    assert client.get("/api/profile").get_json()["profile"]["weekend_def"] == "fri_sun"


def test_reset_backup_is_restorable(client, app, seeded):
    before = client.get(AVAIL).get_json()
    kept = Path(client.post("/api/reset", json={"confirm": "RESET"}).get_json()["backup"])
    resp = client.post("/api/restore", data={"file": (io.BytesIO(kept.read_bytes()), "k.db")})
    assert resp.status_code == 200
    assert client.get(AVAIL).get_json() == before


# ───────── module-level helpers ─────────
def test_sidecar_path_never_overwrites_an_existing_backup(tmp_path):
    db_path = tmp_path / "planner.db"
    db_path.write_bytes(b"")
    first = backup.sidecar_path(db_path)
    assert first.name.startswith("planner.db.bak-")
    first.write_bytes(b"")
    second = backup.sidecar_path(db_path)
    assert second != first and second.name == f"{first.name}-2"


def test_inspect_rejects_an_empty_or_corrupt_file(tmp_path):
    empty = tmp_path / "empty.db"
    empty.write_bytes(b"")
    with pytest.raises(backup.RestoreError, match="empty"):
        backup.inspect(empty)

    corrupt = tmp_path / "corrupt.db"
    corrupt.write_bytes(backup.SQLITE_MAGIC + b"\x00" * 2048)
    with pytest.raises(backup.RestoreError):
        backup.inspect(corrupt)


def test_snapshot_of_a_live_connection_includes_uncommitted_wal_pages(app, tmp_path):
    """The WAL trap: a plain file copy would miss rows that are only in `-wal`."""
    live = db.connect(app.config["DB_PATH"])
    try:
        live.execute("INSERT INTO my_permits (license_year, permit) VALUES (2031, 'archery')")
        out = backup.snapshot_to(live, tmp_path / "snap.db")
    finally:
        live.close()
    conn = sqlite3.connect(out)
    try:
        assert (
            conn.execute("SELECT COUNT(*) FROM my_permits WHERE license_year = 2031").fetchone()[0]
            == 1
        )
    finally:
        conn.close()
