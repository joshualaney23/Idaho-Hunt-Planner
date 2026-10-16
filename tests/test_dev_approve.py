"""`scripts/dev_approve.py`: the local-testing bulk approve.

It is a dev tool, but it writes to the same tables the §8.4 gate reads, so the two
things that keep it honest are worth asserting: it stamps `reviewed_by_user = 0` (the
database never claims a human read these rows), and its waivers are attributable
(`waived_by = dev_approve.py`). The publish itself goes through the real endpoint, so
the gate is not bypassed — only the clicking is.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from planner import db
from tests.test_api_publish_gate import _check, _needs_review, _staged

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "dev_approve.py"


def _load():
    spec = importlib.util.spec_from_file_location("dev_approve", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def dev_approve():
    return _load()


@pytest.fixture
def conn(app):
    c = db.connect(app.config["DB_PATH"])
    yield c
    c.close()


def _statuses(conn, doc: int) -> dict[str, int]:
    return {
        r["status"]: r["n"]
        for r in db.q(
            "SELECT status, COUNT(*) AS n FROM seasons WHERE document_id = ? GROUP BY status",
            doc,
            conn=conn,
        )
    }


def test_it_approves_every_staged_row_without_claiming_a_human_did(app, conn, dev_approve):
    doc = _needs_review(conn)
    for unit in ("39", "40", "41"):
        _staged(conn, doc, [unit])
    _check(conn, doc, "two_path_agreement", "pass")

    assert dev_approve._approve(conn, doc) == 3
    assert _statuses(conn, doc) == {"approved": 3}
    # The gate's record of human review stays false — that is the point of the stamp.
    rows = db.q("SELECT reviewed_by_user, reviewed_at FROM seasons", conn=conn)
    assert {r["reviewed_by_user"] for r in rows} == {0}
    assert all(r["reviewed_at"] for r in rows)


def test_waiving_marks_the_check_waived_and_names_the_script(app, conn, dev_approve):
    doc = _needs_review(conn)
    _staged(conn, doc, ["39"])
    _check(conn, doc, "general_coverage", "fail", expected="99", actual="88")
    _check(conn, doc, "yoy_diff", "warn")
    _check(conn, doc, "golden_match", "skipped")

    waived = dev_approve._waive(app, conn, doc)

    assert sorted(waived) == ["general_coverage", "yoy_diff"]
    rows = db.q(
        "SELECT check_name, waived_by_user, waived_by, waive_reason FROM import_checks", conn=conn
    )
    by_name = {r["check_name"]: r for r in rows}
    assert by_name["general_coverage"]["waived_by_user"] == 1
    assert by_name["general_coverage"]["waived_by"] == dev_approve.WAIVED_BY
    assert "not reviewed by a human" in by_name["general_coverage"]["waive_reason"]
    # A `skipped` check never blocked, so it is never waived (409 `nothing_to_waive`).
    assert by_name["golden_match"]["waived_by_user"] == 0


def test_approve_then_waive_then_publish_satisfies_the_real_gate(app, conn, dev_approve):
    doc = _needs_review(conn)
    for unit in ("39", "40"):
        _staged(conn, doc, [unit])
    _check(conn, doc, "general_coverage", "fail", expected="99", actual="88")

    # Blocked before: a fail plus staged rows.
    assert dev_approve._publish(app, doc)[0] == 0
    dev_approve._approve(conn, doc)
    dev_approve._waive(app, conn, doc)
    published, _note = dev_approve._publish(app, doc)

    assert published == 2
    assert _statuses(conn, doc) == {"published": 2}


def test_it_refuses_to_publish_while_a_check_still_blocks(app, conn, dev_approve):
    """Without `--publish` nothing is waived, so the gate still holds the document."""
    doc = _needs_review(conn)
    _staged(conn, doc, ["39"])
    _check(conn, doc, "general_coverage", "fail")

    dev_approve._approve(conn, doc)
    published, note = dev_approve._publish(app, doc)

    assert published == 0
    assert "publish_blocked" in note
