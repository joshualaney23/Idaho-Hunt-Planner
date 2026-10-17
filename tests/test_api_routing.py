"""T-22g: per-section review routing, per-row approval and the publish override.

The §8.4 gate used to demand a hand approval for **every** staged row of a document.
These tests pin the rule that replaced it:

* a section is `auto_accepted` when nothing about it is uncertain, else it is sent to
  `review` with its reasons;
* being sent to review only obliges the *uncertain rows* of that section — a clean row
  publishes with its document;
* a human can still publish past the gate, with a written reason that is kept forever.

Rows are inserted directly, as `tests/test_api_publish_gate.py` does: routing is pure
API/DB logic and the importer's own path is covered by `tests/test_review_e2e.py`.
"""

from __future__ import annotations

import pytest

from planner import db
from tests.test_api_availability import _doc, _season

DEER = "deer_general"
ELK = "controlled_elk"


@pytest.fixture
def conn(app):
    c = db.connect(app.config["DB_PATH"])
    yield c
    c.close()


def _staged(conn, doc: int, units: list[str] | None = None, **kw) -> int:
    row = {"status": "staged", "section": DEER, "extract_agree": 1, "confidence": 1.0}
    row.update(kw)
    return _season(conn, doc, units if units is not None else ["39"], **row)


def _check(conn, doc: int, name: str, status: str = "pass", section: str = DEER) -> int:
    return db.exec(
        "INSERT INTO import_checks (document_id, section, check_name, status, run_at)"
        " VALUES (?, ?, ?, ?, '2026-10-16T09:00:00')",
        doc,
        section,
        name,
        status,
        conn=conn,
    ).lastrowid


def _sections(client, doc: int) -> dict[str, dict]:
    body = client.get(f"/api/documents/{doc}").get_json()
    return {s["section"]: s for s in body["sections"]}


def _routing(client, doc: int) -> dict:
    return client.get(f"/api/documents/{doc}").get_json()["routing"]


def _publish(client, doc: int, **body):
    resp = client.post(f"/api/documents/{doc}/publish", json=body or None)
    return resp.status_code, resp.get_json()


def _two_sections(conn):
    """A document with a clean deer section and a clean elk section."""
    doc = _doc(conn, year=2026, status="needs_review")
    deer = _staged(conn, doc, ["39"])
    elk = _staged(conn, doc, ["40"], section=ELK)
    _check(conn, doc, "two_path_agreement", "pass")
    _check(conn, doc, "golden_match", "skipped")
    _check(conn, doc, "two_path_agreement", "pass", section=ELK)
    return doc, deer, elk


# ───────── AC1: a clean document publishes with no row approved ─────────


def test_a_clean_document_publishes_with_no_row_approved(client, conn):
    doc, deer, elk = _two_sections(conn)
    body = client.get(f"/api/documents/{doc}").get_json()
    assert body["routing"] == {
        "auto_accepted": [ELK, DEER],
        "review": [],
        "rows_needing_review": 0,
    }
    assert all(s["routing"] == "auto_accepted" for s in body["sections"])
    assert body["publish"]["blocked"] is False
    assert body["publish"]["rows"] == {"approved": 0, "auto": 2, "unreviewed": 0}

    status, published = _publish(client, doc)
    assert status == 200
    assert published["published"] == sorted([deer, elk])
    assert published["auto_published"] == sorted([deer, elk])
    assert published["published_unreviewed"] == []
    assert published["document"]["publish_override"] is None
    assert [r["status"] for r in db.q("SELECT status FROM seasons", conn=conn)] == [
        "published",
        "published",
    ]
    # …and the map sees them.
    summary = client.get(
        "/api/availability?species=deer&start=2026-10-10&end=2026-10-24&include_untagged=1"
    ).get_json()
    assert {u for u, v in summary["units"].items() if v["status"] in ("open", "untagged")} >= {
        "39",
        "40",
    }


# ───────── AC2: each condition routes exactly its own section ─────────


@pytest.mark.parametrize(
    "flip, reason",
    [
        ("check", "check_blocking"),
        ("confidence", "low_confidence"),
        ("extract_agree", "two_path_disagreement"),
    ],
)
def test_each_condition_routes_only_its_own_section(client, conn, flip, reason):
    doc, deer, _elk = _two_sections(conn)
    if flip == "check":
        _check(conn, doc, "general_coverage", "fail")
    elif flip == "confidence":
        db.exec("UPDATE seasons SET confidence = 0.6 WHERE id = ?", deer, conn=conn)
    else:
        db.exec("UPDATE seasons SET extract_agree = 0 WHERE id = ?", deer, conn=conn)

    sections = _sections(client, doc)
    assert sections[DEER]["routing"] == "review"
    assert sections[DEER]["reasons"] == [reason]
    assert sections[ELK]["routing"] == "auto_accepted"
    assert _routing(client, doc)["review"] == [DEER]

    row_level = flip in ("confidence", "extract_agree")
    assert sections[DEER]["needs_review_rows"] == ([deer] if row_level else [])
    assert _routing(client, doc)["rows_needing_review"] == (1 if row_level else 0)

    status, body = _publish(client, doc)
    assert status == 409
    assert body["error"]["code"] == "publish_blocked"
    assert body["error"]["detail"]["rows"] == ([deer] if row_level else [])

    # Clearing exactly that condition unblocks the document.
    if flip == "check":
        cid = db.q1(
            "SELECT id FROM import_checks WHERE check_name = 'general_coverage'", conn=conn
        )["id"]
        client.post(f"/api/documents/{doc}/checks/{cid}/waive", json={"reason": "verified p.14"})
    else:
        client.put(f"/api/documents/{doc}/seasons/{deer}", json={"status": "approved"})
    assert _publish(client, doc)[0] == 200


def test_a_review_section_only_obliges_its_uncertain_rows(client, conn):
    """The fix Josh asked for: one bad row must not drag its whole section in."""
    doc, _deer, _elk = _two_sections(conn)
    clean = [_staged(conn, doc, ["41"]) for _ in range(5)]
    shaky = _staged(conn, doc, ["42"], confidence=0.5)

    sections = _sections(client, doc)
    assert sections[DEER]["routing"] == "review"
    assert sections[DEER]["rows"] == 7  # the whole section is routed…
    assert sections[DEER]["needs_review_rows"] == [shaky]  # …but one row needs a human
    assert sections[DEER]["unreviewed_rows"] == [shaky]

    status, body = _publish(client, doc)
    assert status == 409
    assert body["error"]["detail"]["rows"] == [shaky]

    client.put(f"/api/documents/{doc}/seasons/{shaky}", json={"status": "approved"})
    status, published = _publish(client, doc)
    assert status == 200
    assert set(clean) <= set(published["auto_published"])
    assert shaky in published["published"] and shaky not in published["auto_published"]


# ───────── AC3: a waived check never auto-accepts its section ─────────


def test_a_waived_check_routes_its_section_to_review_forever(client, conn):
    doc, _deer, _elk = _two_sections(conn)
    cid = _check(conn, doc, "tag_checksum", "warn")
    assert _sections(client, doc)[DEER]["reasons"] == ["check_blocking"]

    waived = client.post(
        f"/api/documents/{doc}/checks/{cid}/waive",
        json={"reason": "header total misprinted; rows match the table", "by": "josh"},
    )
    assert waived.status_code == 200
    section = _sections(client, doc)[DEER]
    assert section["routing"] == "review"  # a waiver is a human override, not a pass
    assert section["reasons"] == ["check_waived"]
    assert section["waived"] is True
    assert _sections(client, doc)[ELK]["routing"] == "auto_accepted"
    # Nothing about the rows changed, so publishing still needs no approval.
    assert _publish(client, doc)[0] == 200


def test_editing_a_row_routes_its_section_back_to_review(client, conn):
    doc, deer, _elk = _two_sections(conn)
    assert _sections(client, doc)[DEER]["routing"] == "auto_accepted"

    edited = client.put(f"/api/documents/{doc}/seasons/{deer}", json={"sex": "antlered"})
    assert edited.status_code == 200
    section = _sections(client, doc)[DEER]
    assert section["routing"] == "review"
    assert section["reasons"] == ["edited"]
    assert section["needs_review_rows"] == [deer]
    assert _publish(client, doc)[0] == 409

    # Deciding on the edit clears it; the untouched elk section never moved.
    client.put(f"/api/documents/{doc}/seasons/{deer}", json={"status": "approved"})
    assert _sections(client, doc)[DEER]["routing"] == "auto_accepted"
    assert _publish(client, doc)[0] == 200


# ───────── the publish override (Josh's bypass button) ─────────


def test_override_publishes_past_the_gate_and_is_recorded_forever(client, conn):
    doc, deer, elk = _two_sections(conn)
    db.exec("UPDATE seasons SET extract_agree = 0 WHERE id = ?", deer, conn=conn)
    _check(conn, doc, "general_coverage", "fail")

    blocked = _publish(client, doc)
    assert blocked[0] == 409
    assert blocked[1]["error"]["detail"]["override_available"] is True

    assert _publish(client, doc, override=True)[0] == 400
    empty = _publish(client, doc, override={"reason": "   "})
    assert empty[0] == 400
    assert empty[1]["error"]["code"] == "missing_reason"
    assert db.q1("SELECT status FROM source_documents WHERE id = ?", doc, conn=conn)[0] == (
        "needs_review"
    )

    reason = "Season opener is tomorrow; I checked pp.12-21 against the brochure myself."
    status, body = _publish(client, doc, override={"reason": reason, "by": "josh"})
    assert status == 200
    assert body["published"] == sorted([deer, elk])
    assert body["published_unreviewed"] == [deer]
    assert body["auto_published"] == [elk]

    override = body["document"]["publish_override"]
    assert (override["reason"], override["by"]) == (reason, "josh")
    assert override["at"]
    assert [c["check_name"] for c in override["bypassed"]["checks"]] == ["general_coverage"]
    assert override["bypassed"]["rows"] == [deer]
    assert override["bypassed"]["published_unreviewed"] == [deer]

    # The flag rides on the row itself, so the app can say which seasons nobody confirmed.
    flags = dict(db.q("SELECT id, published_unreviewed FROM seasons", conn=conn))
    assert flags == {deer: 1, elk: 0}
    # …and it survives a re-read of the document.
    again = client.get(f"/api/documents/{doc}").get_json()["document"]
    assert again["publish_override"]["reason"] == reason


def test_an_override_on_an_unblocked_document_records_nothing(client, conn):
    doc, _deer, _elk = _two_sections(conn)
    status, body = _publish(client, doc, override={"reason": "not needed"})
    assert status == 200
    assert body["document"]["publish_override"] is None
    assert body["published_unreviewed"] == []
