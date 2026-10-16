"""T-15c: the review screen's flow end to end, on real 2026 brochure pages.

Everything here goes through the HTTP API the review screen calls, on the nine committed
general-deer pages of the 2026 Big Game brochure (`tests/fixtures/bg2026_deer_p*.pdf`):
upload → §8.4 gate → waiver → per-row review → publish → `/api/availability`.

Why a waiver is part of the happy path: the fixtures are *page excerpts*, so the
`general_coverage` check correctly reports units it can't explain (their seasons are in
the controlled tables, which these pages don't contain). That failure is real, and
waiving it with a typed reason is exactly what §8.4 asks a human to do — which makes this
the most faithful end-to-end the repo can run without the uncommittable brochure.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from pypdf import PdfReader, PdfWriter

from planner import db

FIXTURES = Path(__file__).resolve().parent / "fixtures"
#: The general-deer pages of the 2026 brochure, as cut by `make_deer_fixtures.py`.
DEER_PAGES = (12, 13, 14, 15, 16, 18, 19, 20, 21)
#: What T-11 verified those pages hold.
EXPECTED_ROWS = 235


def _deer_brochure() -> bytes:
    """The nine fixture pages stitched back into one document, as a user would upload."""
    writer = PdfWriter()
    for number in DEER_PAGES:
        writer.add_page(PdfReader(str(FIXTURES / f"bg2026_deer_p{number}.pdf")).pages[0])
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


@pytest.fixture
def conn(app):
    c = db.connect(app.config["DB_PATH"])
    yield c
    c.close()


@pytest.fixture(scope="module")
def brochure() -> bytes:
    return _deer_brochure()


@pytest.fixture
def imported(client, brochure):
    """Upload the fixture brochure and return `(doc_id, detail)` after the import."""
    res = client.post(
        "/api/documents",
        # The classifier reads a full brochure's cover; a nine-page excerpt has none, so
        # the §6 kind/year override stands in for it (the parsers are the real subject).
        data={"file": (io.BytesIO(brochure), "deer-2026.pdf"), "kind": "big_game", "year": "2026"},
        content_type="multipart/form-data",
    )
    assert res.status_code == 201, res.get_json()
    doc_id = res.get_json()["document"]["id"]
    return doc_id, client.get(f"/api/documents/{doc_id}").get_json()


def _ids(detail) -> list[int]:
    return [s["id"] for s in detail["seasons"]]


def _coverage_check(detail):
    return next(c for c in detail["checks"] if c["check_name"] == "general_coverage")


def _section(detail, name: str = "deer_general"):
    """One section summary. The controlled parsers register a section per species even
    when these pages hold none of their tables, so never index `sections` by position."""
    return next(s for s in detail["sections"] if s["section"] == name)


def _approve_every_row(client, doc_id: int, detail) -> None:
    """§8.4: a section with a waived check is never bulk-approved — one row at a time."""
    for sid in _ids(detail):
        res = client.put(f"/api/documents/{doc_id}/seasons/{sid}", json={"status": "approved"})
        assert res.status_code == 200, res.get_json()


# ───────── the import itself ─────────


def test_the_fixture_brochure_stages_every_deer_row(imported) -> None:
    doc_id, detail = imported
    assert detail["document"]["status"] == "needs_review"
    assert detail["document"]["seasons"] == {"staged": EXPECTED_ROWS}
    assert len(detail["seasons"]) == EXPECTED_ROWS
    assert {s["section"] for s in detail["seasons"]} == {"deer_general"}
    assert all(s["source_page"] in range(1, len(DEER_PAGES) + 1) for s in detail["seasons"])


def test_the_accuracy_panel_has_a_row_for_every_check(imported) -> None:
    """AC: the Accuracy panel renders every `import_checks` row, grouped per section."""
    _doc_id, detail = imported
    deer = {c["check_name"] for c in detail["checks"] if c["section"] == "deer_general"}
    assert sorted(deer) == [
        "date_token_accounting",
        "general_coverage",
        # `skipped`: no 2026 golden file is committed for this section yet (T-22d–f).
        "golden_match",
        "row_validation",
        "two_path_agreement",
        "unit_tokens_valid",
    ]
    assert {c["status"] for c in detail["checks"]} == {"pass", "fail", "skipped"}
    assert _coverage_check(detail)["status"] == "fail"
    # Every check belongs to a section the panel can group it under, and every section of
    # the panel is present — including the controlled ones that found nothing here.
    assert all(c["section"] for c in detail["checks"])
    assert {s["section"] for s in detail["sections"]} == {c["section"] for c in detail["checks"]}
    assert _section(detail)["blocking_checks"] == ["general_coverage"]
    assert _section(detail)["clean"] is False


# ───────── AC: the gate blocks publishing ─────────


def test_publish_is_blocked_while_staged_rows_and_a_failing_check_remain(client, imported) -> None:
    doc_id, detail = imported
    assert detail["publish"]["blocked"] is True
    res = client.post(f"/api/documents/{doc_id}/publish")
    assert res.status_code == 409
    err = res.get_json()["error"]
    assert err["code"] == "publish_blocked"
    assert [c["check_name"] for c in err["detail"]["checks"]] == ["general_coverage"]
    assert err["detail"]["rows"] == sorted(_ids(detail))  # every staged row is named
    assert len(err["detail"]["unreviewed_rows"]) == EXPECTED_ROWS


def test_approving_every_row_is_not_enough_while_the_check_fails(client, imported) -> None:
    """The three gate conditions are independent: no staged rows left, still blocked."""
    doc_id, detail = imported
    _approve_every_row(client, doc_id, detail)
    res = client.post(f"/api/documents/{doc_id}/publish")
    assert res.status_code == 409
    detail_err = res.get_json()["error"]["detail"]
    assert detail_err["rows"] == []
    assert [c["check_name"] for c in detail_err["checks"]] == ["general_coverage"]


# ───────── AC: waiving needs a typed reason, stored and shown ─────────


def test_waiving_requires_a_reason_and_keeps_it(client, imported) -> None:
    doc_id, detail = imported
    cid = _coverage_check(detail)["id"]

    empty = client.post(f"/api/documents/{doc_id}/checks/{cid}/waive", json={"reason": "   "})
    assert empty.status_code == 400
    assert empty.get_json()["error"]["code"] == "missing_reason"

    reason = "Fixture is a nine-page excerpt; the 11 unexplained units are controlled-only."
    ok = client.post(
        f"/api/documents/{doc_id}/checks/{cid}/waive", json={"reason": reason, "by": "josh"}
    )
    assert ok.status_code == 200
    check = ok.get_json()["check"]
    assert (check["waived"], check["waive_reason"], check["waived_by"]) == (True, reason, "josh")
    assert check["waived_at"] and check["blocks_publish"] is False

    # …and it is still there on the next read, for the panel to display.
    after = client.get(f"/api/documents/{doc_id}").get_json()
    stored = _coverage_check(after)
    assert (stored["status"], stored["waived"], stored["waive_reason"]) == ("fail", True, reason)
    assert _section(after)["waived"] is True  # §8.7 display safety badge
    assert _section(after)["waived_checks"] == ["general_coverage"]
    assert _section(after)["clean"] is False  # a waiver is an override, not a pass


def test_bulk_approve_refuses_a_waived_section(client, imported) -> None:
    doc_id, detail = imported
    cid = _coverage_check(detail)["id"]
    client.post(f"/api/documents/{doc_id}/checks/{cid}/waive", json={"reason": "excerpt"})
    body = client.post(f"/api/documents/{doc_id}/approve-all").get_json()
    assert body["approved"] == []
    assert len(body["skipped"]) == EXPECTED_ROWS
    assert {s["reason"] for s in body["skipped"]} == {"section_has_failing_check"}


# ───────── AC: publish, then the map lights up ─────────


@pytest.fixture
def published(client, imported):
    """The full §8.4 flow: waive, review every row, publish."""
    doc_id, detail = imported
    cid = _coverage_check(detail)["id"]
    client.post(
        f"/api/documents/{doc_id}/checks/{cid}/waive",
        json={"reason": "nine-page excerpt: the unexplained units are controlled-only"},
    )
    _approve_every_row(client, doc_id, detail)
    res = client.post(f"/api/documents/{doc_id}/publish")
    assert res.status_code == 200, res.get_json()
    return doc_id, detail, res.get_json()


def test_publish_then_availability_returns_open_units(client, conn, published) -> None:
    """AC1 end to end: upload the 2026 deer fixture, review, publish, see open units."""
    doc_id, _detail, result = published
    assert len(result["published"]) == EXPECTED_ROWS
    doc = client.get(f"/api/documents/{doc_id}").get_json()["document"]
    assert doc["status"] == "published"
    assert doc["seasons"] == {"published": EXPECTED_ROWS}

    # A resident holding the 2026 Regular Deer tag (§5 `my_tags`).
    db.exec(
        "INSERT INTO my_tags (license_year, tag_type_id, filled, created_at)"
        " VALUES (2026, 'deer_regular', 0, '2026-01-01')",
        conn=conn,
    )
    # Oct 10 2026 is the general rifle opener printed in these very pages.
    res = client.get(
        "/api/availability",
        query_string={"species": "deer", "start": "2026-10-10", "end": "2026-10-10"},
    )
    assert res.status_code == 200
    body = res.get_json()
    open_units = sorted(u for u, v in body["units"].items() if v["status"] == "open")
    assert body["summary"] == {"open": 53, "restricted": 27, "untagged": 0, "closed": 20}
    assert len(open_units) == 53
    # Unit 1's Regular season that day is the white-tailed one, so it is yellow, not green.
    assert body["units"]["1"]["status"] == "restricted"
    assert "whitetail_only" in body["units"]["1"]["reasons"]
    # Every open unit traces back to a row of the document we just published.
    for unit in open_units[:5]:
        seasons = client.get(f"/api/units/{unit}/seasons").get_json()["seasons"]
        assert any(s["source_page"] for s in seasons)


def test_publishing_a_supplement_supersedes_the_matching_rows(client, conn, published) -> None:
    """AC2 (§8.5): a higher-priority document replaces the rows it matches."""
    doc_id, _detail, _result = published
    base = db.q1(
        "SELECT s.* FROM seasons s JOIN season_units su ON su.season_id = s.id"
        " WHERE s.document_id = ? AND s.status = 'published' AND s.tag_type_id = 'deer_regular'"
        " ORDER BY s.id LIMIT 1",
        doc_id,
        conn=conn,
    )
    units = [
        r["unit_id"]
        for r in db.q("SELECT unit_id FROM season_units WHERE season_id = ?", base["id"], conn=conn)
    ]

    supplement = db.exec(
        "INSERT INTO source_documents (license_year, year_end, kind, title, status, priority)"
        " VALUES (2026, 2026, 'supplemental', 'Deer season correction', 'needs_review', 10)",
        conn=conn,
    ).lastrowid
    cols = (
        "document_id, status, section, species_id, tag_type_id, hunt_class, weapon, sex,"
        " start_date, end_date, restrictions, source_page, extract_agree"
    )
    new_id = db.exec(
        f"INSERT INTO seasons ({cols}) VALUES (?, 'staged', 'deer_general', ?, ?, ?, ?, ?,"
        " '2026-11-01', '2026-11-09', '[]', 1, 1)",
        supplement,
        base["species_id"],
        base["tag_type_id"],
        base["hunt_class"],
        base["weapon"],
        base["sex"],
        conn=conn,
    ).lastrowid
    db.exec_many(
        "INSERT INTO season_units (season_id, unit_id) VALUES (?, ?)",
        [(new_id, u) for u in units],
        conn=conn,
    )

    res = client.put(f"/api/documents/{supplement}/seasons/{new_id}", json={"status": "approved"})
    assert res.status_code == 200 and res.get_json()["publish"]["blocked"] is False
    pub = client.post(f"/api/documents/{supplement}/publish")
    assert pub.status_code == 200, pub.get_json()
    superseded = pub.get_json()["superseded"]
    # Every published row the supplement matches on §8.5's identity — not just the one we
    # copied — is replaced, and nothing else is touched.
    assert base["id"] in superseded
    for sid in superseded:
        row = db.q1("SELECT * FROM seasons WHERE id = ?", sid, conn=conn)
        assert (row["status"], row["supersedes_id"]) == ("superseded", new_id)
    still_published = db.q1(
        "SELECT COUNT(*) AS n FROM seasons WHERE document_id = ? AND status = 'published'",
        doc_id,
        conn=conn,
    )["n"]
    assert still_published == EXPECTED_ROWS - len(superseded)

    # The supplement's own window is the one that opens the map now.
    db.exec(
        "INSERT INTO my_tags (license_year, tag_type_id, filled, created_at)"
        " VALUES (2026, 'deer_regular', 0, '2026-01-01')",
        conn=conn,
    )
    res = client.get(
        "/api/availability",
        query_string={"species": "deer", "start": "2026-11-02", "end": "2026-11-02"},
    )
    assert res.status_code == 200
    statuses = res.get_json()["units"]
    assert all(statuses[u]["status"] == "open" for u in units if u in statuses)
