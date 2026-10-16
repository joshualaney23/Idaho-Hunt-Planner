"""Database backup, restore and reset (T-21, DESIGN.md §6).

No Flask here — `planner/api.py` wires the three endpoints onto these helpers.

Everything goes through :meth:`sqlite3.Connection.backup`, in both directions:

* **out** — it copies a *consistent* snapshot of a live database (WAL pages
  included) without asking the app to stop using it, so `GET /api/backup` can
  never hand out a half-written file;
* **in** — ``source.backup(live_conn)`` replaces the contents of the live
  connection in place. Open connections stay valid and there is no file swap
  that could leave `planner.db` truncated if the process dies mid-write.

A restore or a reset always keeps the database it replaces as
``planner.db.bak-<timestamp>`` beside the DB, so the one irreversible-looking
button in the UI isn't.
"""

from __future__ import annotations

import datetime as _dt
import shutil
import sqlite3
import tempfile
from pathlib import Path

from planner import db

#: Every SQLite file starts with this 16-byte header (file format spec §1.3).
SQLITE_MAGIC = b"SQLite format 3\x00"

#: Timestamp used by both the download name and the `.bak-<stamp>` sidecar.
STAMP_FMT = "%Y%m%dT%H%M%S"

#: Tables a file must have to be a planner database rather than some other SQLite DB.
CORE_TABLES = ("schema_version", "profile", "units", "species", "seasons", "source_documents")

#: Refuse to even open something that can't be a database (the upload is read into memory).
MAX_BACKUP_BYTES = 512 * 1024 * 1024


class RestoreError(ValueError):
    """An uploaded file is not a restorable planner database.

    Carries the API error `code` and `detail` so the endpoint stays a thin wrapper.
    """

    def __init__(self, code: str, message: str, detail: dict | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.detail = detail or {}


# ───────── naming ─────────
def stamp(now: _dt.datetime | None = None) -> str:
    """Timestamp for backup names: local time, filename-safe, sorts chronologically."""
    return (now or _dt.datetime.now()).strftime(STAMP_FMT)


def download_name(now: _dt.datetime | None = None) -> str:
    """Filename the browser saves a `GET /api/backup` under."""
    return f"planner-backup-{stamp(now)}.db"


def sidecar_path(db_path: str | Path, now: _dt.datetime | None = None) -> Path:
    """Where the replaced database is kept: `planner.db.bak-<timestamp>` beside it.

    Collisions within the same second get a `-2`, `-3`, … suffix rather than
    silently overwriting the previous backup.
    """
    path = Path(db_path)
    base = path.with_name(f"{path.name}.bak-{stamp(now)}")
    if not base.exists():
        return base
    for n in range(2, 1000):
        candidate = base.with_name(f"{base.name}-{n}")
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"too many backups at {base}")


# ───────── snapshots ─────────
def latest_version() -> int:
    """The schema version this build migrates to (`schema.sql` plus every migration)."""
    files = db.migration_files()
    return files[-1][0] if files else db.SCHEMA_VERSION


def snapshot_to(conn: sqlite3.Connection, dest: str | Path) -> Path:
    """Write a consistent copy of `conn`'s database to `dest`. Returns `dest`."""
    path = Path(dest)
    path.parent.mkdir(parents=True, exist_ok=True)
    target = sqlite3.connect(str(path))
    try:
        conn.backup(target)
    finally:
        target.close()
    return path


def snapshot_bytes(conn: sqlite3.Connection) -> bytes:
    """A consistent copy of `conn`'s database as bytes (what `GET /api/backup` sends)."""
    with tempfile.TemporaryDirectory(prefix="ihp-backup-") as tmp:
        return snapshot_to(conn, Path(tmp) / "planner.db").read_bytes()


def keep_copy(
    conn: sqlite3.Connection, db_path: str | Path, now: _dt.datetime | None = None
) -> Path:
    """Keep the current database as `planner.db.bak-<timestamp>`. Returns the path."""
    return snapshot_to(conn, sidecar_path(db_path, now))


# ───────── validation ─────────
def looks_like_sqlite(data: bytes) -> bool:
    """True when `data` starts with the SQLite file header."""
    return data[: len(SQLITE_MAGIC)] == SQLITE_MAGIC


def inspect(path: str | Path) -> int:
    """Validate a candidate backup file and return its schema version.

    Raises :class:`RestoreError` when the file is not SQLite, is corrupt, is some
    other application's database, or was written by a newer planner than this one.
    """
    file = Path(path)
    if not file.is_file() or file.stat().st_size == 0:
        raise RestoreError("invalid_backup", "the uploaded file is empty")
    if not looks_like_sqlite(file.read_bytes()[: len(SQLITE_MAGIC)]):
        raise RestoreError("invalid_backup", "that file is not a SQLite database")

    conn = sqlite3.connect(f"file:{file}?mode=ro", uri=True)
    try:
        try:
            check = conn.execute("PRAGMA quick_check").fetchone()[0]
        except sqlite3.DatabaseError as exc:
            raise RestoreError("invalid_backup", f"the database is unreadable: {exc}") from None
        if check != "ok":
            raise RestoreError("invalid_backup", "the database is corrupt", {"quick_check": check})

        names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        missing = [t for t in CORE_TABLES if t not in names]
        if missing:
            raise RestoreError(
                "invalid_backup",
                "that database is not a planner backup",
                {"missing_tables": missing},
            )
        version = db.schema_version(conn)
    finally:
        conn.close()

    newest = latest_version()
    if version <= 0:
        raise RestoreError("invalid_backup", "the backup has no schema version")
    if version > newest:
        raise RestoreError(
            "incompatible_backup",
            f"the backup is schema version {version}; this planner understands {newest}."
            " Update the planner and try again.",
            {"schema_version": version, "supported": newest},
        )
    return version


# ───────── restore / reset ─────────
def copy_into(source: str | Path, conn: sqlite3.Connection) -> None:
    """Replace the contents of `conn`'s database with the database at `source`."""
    src = sqlite3.connect(f"file:{Path(source)}?mode=ro", uri=True)
    try:
        src.backup(conn)
    finally:
        src.close()


def restore(conn: sqlite3.Connection, source: str | Path, db_path: str | Path) -> dict:
    """Validate `source`, keep the current DB aside, then restore into `conn`.

    An older backup is migrated forward afterwards, so restoring last season's file
    into a newer build works. Returns `{backup, schema_version, restored_from}`.
    """
    from_version = inspect(source)
    kept = keep_copy(conn, db_path)
    copy_into(source, conn)
    version = db.migrate(conn)
    return {"backup": str(kept), "restored_from": from_version, "schema_version": version}


def reset(conn: sqlite3.Connection, db_path: str | Path, year: int | None = None) -> dict:
    """Keep the current DB aside, then replace it with a freshly seeded empty one.

    Reference data (units, species, tag types) is re-seeded; tags, permits, profile,
    documents and seasons are gone. Returns `{backup, schema_version}`.
    """
    from planner import seed

    kept = keep_copy(conn, db_path)
    with tempfile.TemporaryDirectory(prefix="ihp-reset-") as tmp:
        fresh_path = Path(tmp) / "fresh.db"
        fresh = db.connect(fresh_path)
        try:
            version = db.migrate(fresh)
            seed.seed(fresh, year=year)
        finally:
            fresh.close()
        copy_into(fresh_path, conn)
    return {"backup": str(kept), "schema_version": version}


def clear_data_dir(data_dir: str | Path) -> list[str]:
    """Delete the files a reset orphans: stored PDFs and the page cache (§8.1/§8.2).

    The database rows that pointed at them are gone, so leaving them behind would
    only make the next upload of the same PDF look like a duplicate. Best effort —
    a file the OS won't let us remove is not worth failing a reset over.
    """
    removed: list[str] = []
    for name in ("regs", "cache", "pdf_cache"):
        target = Path(data_dir) / name
        if not target.exists():
            continue
        try:
            shutil.rmtree(target)
        except OSError:
            continue
        removed.append(name)
    return removed
