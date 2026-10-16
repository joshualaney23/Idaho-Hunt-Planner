#!/usr/bin/env python3
"""Local testing shortcut: approve (and optionally publish) a document's staged rows.

**This is not a review.** It exists for the reimport loop — `ihp --clean`, upload the
brochure again, and you need published rows to exercise the map, the season table or the
availability engine. Clicking Approve 695 times there proves nothing, because nobody
reads 695 rows they already read last time.

So that the database never lies about it, every row this script approves is stamped
``reviewed_by_user = 0``: the §8.4 gate's own record of "a human looked at this" stays
false, and anything that audits it (a golden run, a QA report) can tell machine-approved
rows from reviewed ones. Waivers it writes name the script in ``waived_by``.

It is a script, deliberately: it is not reachable from the API, the UI, or any route the
app serves, so it cannot become the production path by accident. Publishing real data a
hunter will read still goes through the review screen.

Usage (from the repo, with the venv active)::

    python3 scripts/dev_approve.py --list            # what's in the DB
    python3 scripts/dev_approve.py                   # approve the newest document
    python3 scripts/dev_approve.py --publish         # approve, waive blockers, publish
    python3 scripts/dev_approve.py --all --publish   # every unpublished document
    python3 scripts/dev_approve.py --doc 3 --db /tmp/t.db
"""

from __future__ import annotations

import argparse
import datetime as dt
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from planner import db  # noqa: E402
from planner.app import DEFAULT_DB_PATH, create_app  # noqa: E402

#: Stamped on every waiver this script writes, so a waived check is never mistaken for a
#: human's judgement call.
WAIVED_BY = "dev_approve.py"
WAIVE_REASON = "local testing: waived by scripts/dev_approve.py, not reviewed by a human"


def _now() -> str:
    return dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat()


def _documents(conn: sqlite3.Connection, doc_id: int | None, every: bool) -> list[sqlite3.Row]:
    """The documents to act on: one named, every unpublished one, or the newest."""
    rows = db.q(
        "SELECT id, kind, license_year, status FROM source_documents ORDER BY id", conn=conn
    )
    if doc_id is not None:
        chosen = [r for r in rows if r["id"] == doc_id]
        if not chosen:
            sys.exit(f"dev_approve: no document {doc_id} (have {[r['id'] for r in rows]})")
        return chosen
    if every:
        return [r for r in rows if r["status"] != "published"]
    return rows[-1:]


def _approve(conn: sqlite3.Connection, doc_id: int) -> int:
    """Flip every staged row to approved. `reviewed_by_user` stays 0 — see the module docstring."""
    cur = conn.execute(
        "UPDATE seasons SET status = 'approved', reviewed_by_user = 0, reviewed_at = ?"
        " WHERE document_id = ? AND status = 'staged'",
        (_now(), doc_id),
    )
    return cur.rowcount


def _waive(app, conn: sqlite3.Connection, doc_id: int) -> list[str]:
    """Waive every check that blocks publishing, through the real waive endpoint.

    A waiver is `waived_by_user = 1` plus the reason — writing only `waive_reason` by hand
    leaves the gate blocking, because `_check_dict` reads the flag, not the text.
    """
    blocking = db.q(
        "SELECT id, check_name FROM import_checks WHERE document_id = ?"
        " AND status IN ('fail', 'warn') AND waived_by_user = 0",
        doc_id,
        conn=conn,
    )
    client = app.test_client()
    waived: list[str] = []
    for check in blocking:
        res = client.post(
            f"/api/documents/{doc_id}/checks/{check['id']}/waive",
            json={"reason": WAIVE_REASON, "by": WAIVED_BY},
        )
        if res.status_code == 200:
            waived.append(check["check_name"])
        else:
            print(f"    ! could not waive {check['check_name']}: {res.get_json()}")
    return waived


def _publish(app, doc_id: int) -> tuple[int, str]:
    """Publish through the real API, so the §8.4 gate and §8.5 supersession are the real ones."""
    res = app.test_client().post(f"/api/documents/{doc_id}/publish")
    body = res.get_json() or {}
    if res.status_code != 200:
        return 0, f"blocked ({body.get('error', res.status_code)}): {body.get('detail', body)}"
    return len(
        body.get("published", [])
    ), f"superseded {len(body.get('superseded', []))} older row(s)"


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="dev_approve.py",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--db", type=Path, default=DEFAULT_DB_PATH, help="DB path (default data/planner.db)"
    )
    parser.add_argument("--doc", type=int, help="document id (default: the newest)")
    parser.add_argument("--all", action="store_true", help="every unpublished document")
    parser.add_argument(
        "--publish", action="store_true", help="also waive blocking checks and publish"
    )
    parser.add_argument(
        "--list", action="store_true", help="list documents and row counts, change nothing"
    )
    args = parser.parse_args(argv)

    if not args.db.exists():
        sys.exit(f"dev_approve: no database at {args.db} — import a document first")

    print(f"dev_approve: {args.db}  (machine approval, not a human review)")
    app = create_app(args.db)
    conn = db.connect(args.db)
    try:
        for doc in _documents(conn, args.doc, args.all or args.list):
            counts = {
                r["status"]: r["n"]
                for r in db.q(
                    "SELECT status, COUNT(*) AS n FROM seasons WHERE document_id = ?"
                    " GROUP BY status",
                    doc["id"],
                    conn=conn,
                )
            }
            head = (
                f"  doc {doc['id']} {doc['kind']} {doc['license_year']} [{doc['status']}] {counts}"
            )
            if args.list:
                print(head)
                continue
            print(head)
            print(f"    approved {_approve(conn, doc['id'])} staged row(s)")
            if args.publish:
                waived = _waive(app, conn, doc["id"])
                if waived:
                    print(f"    waived {len(waived)} blocking check(s): {', '.join(waived)}")
                published, note = _publish(app, doc["id"])
                print(f"    published {published} row(s) — {note}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
