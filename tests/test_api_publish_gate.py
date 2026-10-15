"""T-22b: the §8.4 review gate — publish, waivers and per-row review.

Documents and rows are inserted directly (no PDF): the gate is pure API/DB logic, and
the importer's own path is covered by `tests/test_api_documents.py`.
"""

from __future__ import annotations

import pytest

from planner import db
from tests.test_api_availability import _doc, _season

SECTION = "deer_general"


@pytest.fixture
def conn(app):
    c = db.connect(app.config["DB_PATH"])
    yield c
    c.close()


def _staged(conn, doc: int, units: list[str] | None = None, **kw) -> int:
    row = {"status": "staged", "section": SECTION, "extract_agree": 1}
    row.update(kw)
    return _season(conn, doc, units if units is not None else ["39"], **row)


def _check(conn, doc: int, name: str, status: str = "pass", section: str = SECTION, **kw) -> int:
    return db.exec(
        "INSERT INTO import_checks (document_id, section, check_name, status, expected, actual,"
        " detail, run_at) VALUES (?, ?, ?, ?, ?, ?, ?, '2026-10-15T09:00:00')",
        doc,
        section,
        name,
        status,
        kw.get("expected"),
        kw.get("actual"),
        kw.get("detail"),
        conn=conn,
    ).lastrowid


def _review(client, doc: int, season_id: int, status: str):
    return client.put(f"/api/documents/{doc}/seasons/{season_id}", json={"status": status})


def _publish(client, doc: int):
    resp = client.post(f"/api/documents/{doc}/publish")
    return resp.status_code, resp.get_json()


def _needs_review(conn, year: int = 2026, **kw) -> int:
    return _doc(conn, year=year, status="needs_review", **kw)


# ───────── AC1: an unwaived fail blocks publishing ─────────


def test_publish_blocked_by_failing_check_names_every_check_and_row(client, conn):
    doc = _needs_review(conn)
    ok = _staged(conn, doc, ["39"])
    bad = _staged(conn, doc, ["40"], extract_agree=0)
    fail_id = _check(conn, doc, "two_path_agreement", "fail", expected="235", actual="234")
    warn_id = _check(conn, doc, "yoy_diff", "warn", section="controlled_deer")
    _check(conn, doc, "tag_checksum", "pass")
    _check(conn, doc, "golden_match", "skipped")

    status, body = _publish(client, doc)
    assert status == 409
    error = body["error"]
    assert error["code"] == "publish_blocked"
    blocking = error["detail"]["checks"]
    assert {c["check_name"] for c in blocking} == {"two_path_agreement", "yoy_diff"}
    assert {c["id"] for c in blocking} == {fail_id, warn_id}
    assert [c for c in blocking if c["id"] == fail_id][0]["expected"] == "235"
    # Every blocking row is named: both are still staged, and `bad` also disagrees.
    assert error["detail"]["rows"] == sorted([ok, bad])
    assert [r["id"] for r in error["detail"]["unreviewed_rows"]] == sorted([ok, bad])
    assert [r["id"] for r in error["detail"]["disagreeing_rows"]] == [bad]
    # A `pass` and a `skipped` check never block.
    assert all(c["check_name"] not in ("tag_checksum", "golden_match") for c in blocking)
    assert db.q1("SELECT status FROM source_documents WHERE id = ?", doc, conn=conn)[0] == (
        "needs_review"
    )

    # Reviewing every row is not enough while the check is unwaived.
    _review(client, doc, ok, "approved")
    _review(client, doc, bad, "approved")
    status, body = _publish(client, doc)
    assert status == 409
    assert body["error"]["detail"]["rows"] == []
    assert {c["id"] for c in body["error"]["detail"]["checks"]} == {fail_id, warn_id}


def test_publish_succeeds_and_supersedes_older_rows(client, conn):
    old_doc = _doc(conn, status="published")
    old = _season(conn, old_doc, ["39"])  # published, priority 0
    other_unit = _season(conn, old_doc, ["45"])

    doc = _needs_review(conn)
    db.exec("UPDATE source_documents SET priority = 5 WHERE id = ?", doc, conn=conn)
    fresh = _staged(conn, doc, ["39"])
    _check(conn, doc, "tag_checksum", "pass")
    client.post(f"/api/documents/{doc}/approve-all")

    status, body = _publish(client, doc)
    assert status == 200
    assert body["published"] == [fresh]
    assert body["superseded"] == [old]
    assert body["document"]["status"] == "published"
    assert body["document"]["published_at"]
    rows = dict(
        db.q(
            "SELECT id, status FROM seasons WHERE id IN (?, ?, ?)",
            fresh,
            old,
            other_unit,
            conn=conn,
        )
    )
    assert rows == {fresh: "published", old: "superseded", other_unit: "published"}
    assert (
        db.q1("SELECT supersedes_id FROM seasons WHERE id = ?", old, conn=conn)["supersedes_id"]
        == fresh
    )
    # Publishing twice is a 409, not a silent no-op.
    assert _publish(client, doc)[0] == 409


# ───────── AC2: waivers need a reason and are kept forever ─────────


def test_waiver_requires_a_reason_and_unblocks_publish(client, conn):
    doc = _needs_review(conn)
    row = _staged(conn, doc)
    check_id = _check(conn, doc, "tag_checksum", "fail", expected="13226", actual="13225")

    empty = client.post(f"/api/documents/{doc}/checks/{check_id}/waive", json={"reason": "   "})
    assert empty.status_code == 400
    assert empty.get_json()["error"]["code"] == "missing_reason"
    assert client.post(f"/api/documents/{doc}/checks/{check_id}/waive", json={}).status_code == 400
    assert (
        db.q1("SELECT waived_by_user FROM import_checks WHERE id = ?", check_id, conn=conn)[0] == 0
    )

    reason = "Brochure p.12 misprints the header total; rows match the table (verified by hand)."
    waived = client.post(
        f"/api/documents/{doc}/checks/{check_id}/waive", json={"reason": reason, "by": "josh"}
    )
    assert waived.status_code == 200
    check = waived.get_json()["check"]
    assert check["waived"] is True
    assert check["blocks_publish"] is False
    assert check["waive_reason"] == reason
    assert check["waived_by"] == "josh"
    assert check["waived_at"]

    # The reason survives a re-read of the document, and the section is flagged for §8.7
    # display safety (the ⚠ "manually verified" badge).
    doc_body = client.get(f"/api/documents/{doc}").get_json()
    stored = [c for c in doc_body["checks"] if c["id"] == check_id][0]
    assert (stored["waive_reason"], stored["waived"], stored["waived_by"]) == (reason, True, "josh")
    section = [s for s in doc_body["sections"] if s["section"] == SECTION][0]
    assert section["waived"] is True
    assert section["waived_checks"] == ["tag_checksum"]
    assert section["clean"] is False
    assert doc_body["seasons"][0]["section"] == SECTION

    # Only the rows still block now; approve them and publishing goes through.
    assert _publish(client, doc)[1]["error"]["detail"]["checks"] == []
    _review(client, doc, row, "approved")
    assert _publish(client, doc)[0] == 200
    after = client.get(f"/api/documents/{doc}").get_json()
    assert [c for c in after["checks"] if c["id"] == check_id][0]["waive_reason"] == reason
    assert after["publish"]["blocked"] is False


def test_waiver_rejects_unknown_check_and_passing_check(client, conn):
    doc = _needs_review(conn)
    passing = _check(conn, doc, "tag_checksum", "pass")
    missing = client.post(f"/api/documents/{doc}/checks/9999/waive", json={"reason": "x"})
    assert missing.status_code == 404
    assert missing.get_json()["error"]["code"] == "unknown_check"
    nothing = client.post(f"/api/documents/{doc}/checks/{passing}/waive", json={"reason": "x"})
    assert nothing.status_code == 409
    assert nothing.get_json()["error"]["code"] == "nothing_to_waive"


# ───────── AC3: extract_agree = 0 rows, and what bulk approve may touch ─────────


def test_disagreeing_row_blocks_until_reviewed_individually(client, conn):
    doc = _needs_review(conn)
    agree = _staged(conn, doc, ["39"])
    disagree = _staged(conn, doc, ["40"], extract_agree=0)
    _check(conn, doc, "tag_checksum", "pass")

    bulk = client.post(f"/api/documents/{doc}/approve-all").get_json()
    assert bulk["approved"] == [agree]
    assert bulk["skipped"] == [
        {"id": disagree, "section": SECTION, "reason": "extract_disagreement"}
    ]
    assert bulk["publish"]["blocked"] is True

    status, body = _publish(client, doc)
    assert status == 409
    assert body["error"]["detail"]["rows"] == [disagree]
    assert [r["id"] for r in body["error"]["detail"]["disagreeing_rows"]] == [disagree]

    reviewed = _review(client, doc, disagree, "approved")
    assert reviewed.status_code == 200
    assert reviewed.get_json()["season"]["reviewed_by_user"] == 1
    assert reviewed.get_json()["season"]["reviewed_at"]
    assert reviewed.get_json()["publish"]["blocked"] is False
    assert _publish(client, doc)[0] == 200
    assert db.q1("SELECT status FROM seasons WHERE id = ?", disagree, conn=conn)[0] == "published"


def test_bulk_approve_never_covers_a_row_that_fails_a_check(client, conn):
    doc = _needs_review(conn)
    bad_section = _staged(conn, doc, ["39"], section="controlled_deer")
    good_section = _staged(conn, doc, ["40"])
    _check(conn, doc, "two_path_agreement", "fail", section="controlled_deer")
    _check(conn, doc, "two_path_agreement", "pass")

    body = client.post(f"/api/documents/{doc}/approve-all").get_json()
    assert body["approved"] == [good_section]
    assert body["clean_sections"] == [SECTION]
    assert body["skipped"] == [
        {"id": bad_section, "section": "controlled_deer", "reason": "section_has_failing_check"}
    ]

    # A waiver is a human override, not a pass: the section stays out of bulk approve.
    check_id = db.q1(
        "SELECT id FROM import_checks WHERE document_id = ? AND section = 'controlled_deer'",
        doc,
        conn=conn,
    )[0]
    client.post(
        f"/api/documents/{doc}/checks/{check_id}/waive", json={"reason": "corrections page p.3"}
    )
    again = client.post(f"/api/documents/{doc}/approve-all").get_json()
    assert again["approved"] == []
    assert again["clean_sections"] == [SECTION]
    assert db.q1("SELECT status FROM seasons WHERE id = ?", bad_section, conn=conn)[0] == "staged"

    # Rejecting it is a review too, so publishing is no longer blocked by that row.
    assert _review(client, doc, bad_section, "rejected").status_code == 200
    status, _ = _publish(client, doc)
    assert status == 200
    assert db.q1("SELECT status FROM seasons WHERE id = ?", bad_section, conn=conn)[0] == "rejected"


# ───────── plumbing ─────────


def test_review_endpoint_validates_status_and_refuses_published_rows(client, conn):
    doc = _needs_review(conn)
    row = _staged(conn, doc)
    bad = _review(client, doc, row, "approve")
    assert bad.status_code == 400
    assert bad.get_json()["error"]["code"] == "validation_failed"
    assert _review(client, doc, 9999, "approved").status_code == 404
    assert client.put("/api/documents/9999/seasons/1", json={"status": "approved"}).status_code == (
        404
    )
    db.exec("UPDATE seasons SET status = 'published' WHERE id = ?", row, conn=conn)
    locked = _review(client, doc, row, "rejected")
    assert locked.status_code == 409
    assert locked.get_json()["error"]["code"] == "already_published"


def test_checks_endpoint_reports_sections_and_gate_state(client, conn):
    doc = _needs_review(conn)
    _staged(conn, doc)
    _check(conn, doc, "tag_checksum", "pass")
    _check(conn, doc, "yoy_diff", "warn", section="controlled_deer")

    body = client.get(f"/api/documents/{doc}/checks").get_json()
    assert [c["check_name"] for c in body["checks"]] == ["yoy_diff", "tag_checksum"]
    assert {s["section"]: s["clean"] for s in body["sections"]} == {
        "controlled_deer": False,
        SECTION: True,
    }
    assert body["publish"]["blocked"] is True
    assert client.get("/api/documents/9999/checks").status_code == 404
