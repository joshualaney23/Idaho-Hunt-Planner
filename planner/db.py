"""Connection, migrations, and query helpers (T-04, DESIGN.md §5).

Usage::

    conn = connect("data/planner.db")
    migrate(conn)                       # schema.sql at version 1, then migrations/NNN_*.sql
    with use(conn):                     # make it the current connection for q/q1/exec/tx
        rows = q("SELECT id FROM units ORDER BY sort_key")
        with tx():
            exec("INSERT INTO my_permits VALUES (?, ?)", 2026, "archery")

Every helper also takes an explicit ``conn=`` keyword. Inside a Flask app context the
current connection is opened lazily on ``app.config["DB_PATH"]`` and closed on teardown
(see ``init_app``).

Connections run in autocommit mode (``isolation_level=None``): each statement commits on
its own unless it's inside ``tx()``, which issues ``BEGIN IMMEDIATE``/``COMMIT`` itself
(or a ``SAVEPOINT`` when nested).
"""

from __future__ import annotations

import itertools
import re
import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Any

PLANNER_DIR = Path(__file__).resolve().parent
SCHEMA_PATH = PLANNER_DIR / "schema.sql"
MIGRATIONS_DIR = PLANNER_DIR / "migrations"
SCHEMA_VERSION = 1  # the version schema.sql represents
_MIGRATION_RE = re.compile(r"^(\d{3})_[A-Za-z0-9_]+\.sql$")

_current: ContextVar[sqlite3.Connection | None] = ContextVar("planner_db", default=None)
_savepoints = itertools.count(1)


# ───────── connections ─────────
def connect(path: str | Path) -> sqlite3.Connection:
    """Open a DB with foreign keys on, WAL journaling and `sqlite3.Row` rows."""
    if str(path) != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


@contextmanager
def use(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Make `conn` the current connection for the helpers within the block."""
    token = _current.set(conn)
    try:
        yield conn
    finally:
        _current.reset(token)


def current() -> sqlite3.Connection:
    """The helpers' connection: the innermost `use()` block, else the Flask app context's."""
    conn = _current.get()
    if conn is not None:
        return conn
    from flask import current_app, g, has_app_context

    if has_app_context():
        if "db" not in g:
            g.db = connect(current_app.config["DB_PATH"])
        return g.db
    raise RuntimeError("no current DB connection: wrap the call in db.use(conn) or pass conn=")


def current_or_none() -> sqlite3.Connection | None:
    """`current()`, or None when nothing opened a database — for callers that can say
    nothing without one (the `golden_match` check reads the rows it compares)."""
    try:
        return current()
    except RuntimeError:
        return None


def _conn(conn: sqlite3.Connection | None) -> sqlite3.Connection:
    return conn if conn is not None else current()


# ───────── query helpers ─────────
def q(sql: str, *args: Any, conn: sqlite3.Connection | None = None) -> list[sqlite3.Row]:
    """All rows of a query."""
    return _conn(conn).execute(sql, args).fetchall()


def q1(sql: str, *args: Any, conn: sqlite3.Connection | None = None) -> sqlite3.Row | None:
    """First row of a query, or None."""
    return _conn(conn).execute(sql, args).fetchone()


def exec(sql: str, *args: Any, conn: sqlite3.Connection | None = None) -> sqlite3.Cursor:
    """Run one statement; returns the cursor (`lastrowid`, `rowcount`)."""
    return _conn(conn).execute(sql, args)


def exec_many(
    sql: str, rows: Sequence[Sequence[Any]], *, conn: sqlite3.Connection | None = None
) -> sqlite3.Cursor:
    """`executemany` over `rows`."""
    return _conn(conn).executemany(sql, rows)


@contextmanager
def tx(conn: sqlite3.Connection | None = None) -> Iterator[sqlite3.Connection]:
    """Transaction: commit on success, roll back on any exception. Nests via SAVEPOINT."""
    c = _conn(conn)
    if c.in_transaction:
        name = f"sp{next(_savepoints)}"
        c.execute(f"SAVEPOINT {name}")
        try:
            yield c
        except BaseException:
            c.execute(f"ROLLBACK TO {name}")
            c.execute(f"RELEASE {name}")
            raise
        c.execute(f"RELEASE {name}")
        return
    c.execute("BEGIN IMMEDIATE")
    try:
        yield c
    except BaseException:
        c.execute("ROLLBACK")
        raise
    c.execute("COMMIT")


# ───────── migrations ─────────
def schema_version(conn: sqlite3.Connection) -> int:
    """Current schema version, 0 for an empty DB."""
    has_table = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'schema_version'"
    ).fetchone()
    if not has_table:
        return 0
    row = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()
    return int(row[0] or 0)


def migration_files(directory: Path = MIGRATIONS_DIR) -> list[tuple[int, Path]]:
    """`(version, path)` for every `NNN_name.sql` in `directory`, in version order."""
    if not directory.is_dir():
        return []
    found = sorted(
        (int(m.group(1)), p) for p in directory.iterdir() if (m := _MIGRATION_RE.match(p.name))
    )
    versions = [v for v, _ in found]
    if len(set(versions)) != len(versions):
        raise RuntimeError(f"duplicate migration numbers in {directory}: {versions}")
    if any(v <= SCHEMA_VERSION for v in versions):
        raise RuntimeError(f"migration numbers must be > {SCHEMA_VERSION} (schema.sql's version)")
    return found


def _apply_script(conn: sqlite3.Connection, sql: str, version: int) -> None:
    """Run one schema script and record `version`, atomically.

    `executescript()` commits any open transaction first, so BEGIN/COMMIT live inside the
    script; on failure the transaction is rolled back and the version stays unchanged.
    """
    if version == SCHEMA_VERSION:
        bump = f"INSERT INTO schema_version (version) VALUES ({version});"
    else:
        bump = f"UPDATE schema_version SET version = {version};"
    try:
        conn.executescript(f"BEGIN;\n{sql}\n;\n{bump}\nCOMMIT;")
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise


def migrate(conn: sqlite3.Connection, migrations_dir: Path = MIGRATIONS_DIR) -> int:
    """Bring the DB to the latest version and return it. Safe to call on every start."""
    version = schema_version(conn)
    if version == 0:
        _apply_script(conn, SCHEMA_PATH.read_text(encoding="utf-8"), SCHEMA_VERSION)
        version = SCHEMA_VERSION
    for v, path in migration_files(migrations_dir):
        if v > version:
            _apply_script(conn, path.read_text(encoding="utf-8"), v)
            version = v
    return version


# ───────── app wiring ─────────
def init_db(path: str | Path, year: int | None = None) -> int:
    """Create/upgrade the DB at `path` and seed reference data (idempotent). Returns version."""
    from planner import seed

    conn = connect(path)
    try:
        version = migrate(conn)
        seed.seed(conn, year=year)
        return version
    finally:
        conn.close()


def init_app(app: Any) -> None:
    """Create + seed the DB when the app is built; close per-request connections on teardown."""
    init_db(app.config["DB_PATH"])

    @app.teardown_appcontext
    def _close_db(_exc: BaseException | None) -> None:
        from flask import g

        conn = g.pop("db", None)
        if conn is not None:
            conn.close()
