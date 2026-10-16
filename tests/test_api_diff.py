"""T-15a: `GET /api/documents/<id>/diff` — the year-over-year diff behind §8.4.

Rows are inserted directly (no PDF): the diff is pure SQL + Python over stored rows.
"""

from __future__ import annotations

import pytest

from planner import db
from tests.test_api_availability import _doc, _season


@pytest.fixture
def conn(app):
    c = db.connect(app.config["DB_PATH"])
    yield c
    c.close()


def _prior(conn, units: list[str], **kw) -> int:
    """A published row in the 2025 document."""
    return _season(conn, kw.pop("doc"), units, status="published", **kw)


def _diff(client, doc: int):
    resp = client.get(f"/api/documents/{doc}/diff")
    return resp.status_code, resp.get_json()


def _by_status(body) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {"new": [], "changed": [], "same": []}
    for row in body["rows"]:
        out[row["status"]].append(row)
    return out


@pytest.fixture
def two_years(conn):
    """2025 published + 2026 staged: one changed, one same, one new, one removed."""
    old = _doc(conn, year=2025, status="published")
    new = _doc(conn, year=2026, status="needs_review")

    ids = {
        "old_moved": _prior(conn, ["39"], doc=old, start_date="2025-10-10", end_date="2025-10-24"),
        "old_same": _prior(conn, ["43"], doc=old, start_date="2025-09-15", end_date="2025-09-30"),
        "old_gone": _prior(conn, ["44"], doc=old, start_date="2025-11-01", end_date="2025-11-10"),
    }
    ids["moved"] = _season(
        conn, new, ["39"], status="staged", start_date="2026-10-15", end_date="2026-10-29"
    )
    ids["same"] = _season(
        conn, new, ["43"], status="staged", start_date="2025-09-15", end_date="2025-09-30"
    )
    ids["added"] = _season(
        conn, new, ["70", "71"], status="staged", start_date="2026-10-10", end_date="2026-10-24"
    )
    return {"old": old, "new": new, **ids}


# ───────── AC1: changed (old → new), new, removed and same on a two-year DB ─────────


def test_two_year_diff_tags_every_row_and_lists_removed(client, two_years):
    status, body = _diff(client, two_years["new"])
    assert status == 200
    assert body["document"]["license_year"] == 2026
    assert body["prior_license_year"] == 2025
    rows = _by_status(body)

    # The row whose dates moved: `changed`, carrying both the old and the new dates.
    (changed,) = rows["changed"]
    assert changed["season_id"] == two_years["moved"]
    assert changed["changes"]["start_date"] == {"from": "2025-10-10", "to": "2026-10-15"}
    assert changed["changes"]["end_date"] == {"from": "2025-10-24", "to": "2026-10-29"}
    assert set(changed["changes"]) == {"start_date", "end_date"}
    assert changed["prior"]["id"] == two_years["old_moved"]
    assert changed["prior"]["license_year"] == 2025

    # A unit set with no counterpart last year is `new`, with no prior row.
    (added,) = rows["new"]
    assert added["season_id"] == two_years["added"]
    assert added["prior"] is None and added["changes"] == {}
    assert added["row"]["units"] == ["70", "71"]

    # An untouched row is `same`.
    (same,) = rows["same"]
    assert same["season_id"] == two_years["same"]
    assert same["changes"] == {} and same["prior"]["id"] == two_years["old_same"]

    # A prior-year row with no counterpart is removed, not silently dropped.
    assert [r["id"] for r in body["removed"]] == [two_years["old_gone"]]
    assert body["removed"][0]["units"] == ["44"]
    assert body["removed"][0]["license_year"] == 2025


def test_restrictions_coverage_and_notes_also_make_a_row_changed(client, conn):
    old = _doc(conn, year=2025, status="published")
    new = _doc(conn, year=2026, status="needs_review")
    prior = _season(conn, old, ["39"], status="published", restrictions='["ar"]', notes="old note")
    db.exec("UPDATE season_units SET coverage = 'full' WHERE season_id = ?", prior, conn=conn)
    row = _season(conn, new, ["39"], status="staged", restrictions='["ar","nm"]', notes="new note")
    db.exec(
        "UPDATE season_units SET coverage = 'partial', coverage_note = 'Portion of 39'"
        " WHERE season_id = ?",
        row,
        conn=conn,
    )

    _, body = _diff(client, new)
    (changed,) = _by_status(body)["changed"]
    assert changed["changes"]["restrictions"] == {"from": ["ar"], "to": ["ar", "nm"]}
    assert changed["changes"]["notes"] == {"from": "old note", "to": "new note"}
    assert changed["changes"]["coverage"] == {
        "from": {"39": ["full", None]},
        "to": {"39": ["partial", "Portion of 39"]},
    }


# ───────── AC2: unit-set matching ignores order and duplicates ─────────


def test_unit_set_matching_ignores_order_and_duplicates(client, conn):
    old = _doc(conn, year=2025, status="published")
    new = _doc(conn, year=2026, status="needs_review")
    _season(conn, old, ["39", "40", "41"], status="published")
    row = _season(conn, new, ["41", "39", "40"], status="staged")
    # A duplicate unit row would break a list comparison; the set ignores it.
    db.exec(
        "INSERT OR REPLACE INTO season_units (season_id, unit_id) VALUES (?, '39')", row, conn=conn
    )

    _, body = _diff(client, new)
    assert body["summary"] == {"new": 0, "changed": 0, "same": 1, "removed": 0}
    (same,) = _by_status(body)["same"]
    assert same["row"]["units"] == ["39", "40", "41"]


def test_a_changed_unit_set_is_new_plus_removed_not_changed(client, conn):
    old = _doc(conn, year=2025, status="published")
    new = _doc(conn, year=2026, status="needs_review")
    gone = _season(conn, old, ["39", "40"], status="published")
    added = _season(conn, new, ["39", "40", "41"], status="staged")

    _, body = _diff(client, new)
    assert [r["season_id"] for r in _by_status(body)["new"]] == [added]
    assert [r["id"] for r in body["removed"]] == [gone]


def test_several_windows_under_one_hunt_number_pair_window_by_window(client, conn):
    """T-17a: one hunt number prints several windows, so a match key is not unique."""
    old = _doc(conn, year=2025, status="published")
    new = _doc(conn, year=2026, status="needs_review")
    key = {"tag_type_id": "controlled", "hunt_class": "controlled", "hunt_number": "1123"}
    _season(
        conn, old, ["39"], status="published", start_date="2025-10-10", end_date="2025-10-14", **key
    )
    _season(
        conn, old, ["39"], status="published", start_date="2025-11-01", end_date="2025-11-05", **key
    )
    kept = _season(
        conn, new, ["39"], status="staged", start_date="2025-11-01", end_date="2025-11-05", **key
    )
    moved = _season(
        conn, new, ["39"], status="staged", start_date="2026-10-12", end_date="2026-10-16", **key
    )

    _, body = _diff(client, new)
    rows = _by_status(body)
    # The untouched window stays `same` even though its sibling moved.
    assert [r["season_id"] for r in rows["same"]] == [kept]
    assert [r["season_id"] for r in rows["changed"]] == [moved]
    assert body["removed"] == []


# ───────── AC3: earliest year is all `new`; unknown document is a 404 ─────────


def test_earliest_year_returns_all_new_and_no_removed(client, conn):
    doc = _doc(conn, year=2026, status="needs_review")
    rows = [
        _season(conn, doc, ["39"], status="staged"),
        _season(conn, doc, ["40"], status="staged"),
    ]
    # A later published year must not count as "prior".
    later = _doc(conn, year=2027, status="published")
    _season(conn, later, ["39"], status="published")

    status, body = _diff(client, doc)
    assert status == 200
    assert body["prior_license_year"] is None
    assert [r["season_id"] for r in body["rows"]] == sorted(rows)
    assert {r["status"] for r in body["rows"]} == {"new"}
    assert body["removed"] == []
    assert body["summary"] == {"new": 2, "changed": 0, "same": 0, "removed": 0}


def test_unknown_document_is_404(client):
    status, body = _diff(client, 999)
    assert status == 404
    assert body["error"]["code"] == "unknown_document"


def test_prior_year_is_the_most_recent_one_with_published_rows(client, conn):
    """2024 published, 2025 staged-only, 2026 under review → the prior year is 2024."""
    y2024 = _doc(conn, year=2024, status="published")
    _season(conn, y2024, ["39"], status="published", start_date="2024-10-10", end_date="2024-10-24")
    y2025 = _doc(conn, year=2025, status="needs_review")
    _season(conn, y2025, ["39"], status="staged")
    new = _doc(conn, year=2026, status="needs_review")
    _season(conn, new, ["39"], status="staged", start_date="2026-10-10", end_date="2026-10-24")

    _, body = _diff(client, new)
    assert body["prior_license_year"] == 2024
    (changed,) = _by_status(body)["changed"]
    assert changed["prior"]["start_date"] == "2024-10-10"


def test_rejected_and_superseded_rows_are_left_out(client, conn):
    old = _doc(conn, year=2025, status="published")
    new = _doc(conn, year=2026, status="needs_review")
    _season(conn, old, ["39"], status="published")
    _season(conn, old, ["44"], status="superseded")  # not a prior-year baseline row
    kept = _season(conn, new, ["39"], status="approved")
    _season(conn, new, ["70"], status="rejected")  # thrown away, never diffed

    _, body = _diff(client, new)
    assert [r["season_id"] for r in body["rows"]] == [kept]
    assert body["removed"] == []


# ───────── AC4: the summary equals the row tallies, and nothing is written ─────────


def test_summary_counts_equal_the_row_tallies(client, two_years):
    _, body = _diff(client, two_years["new"])
    rows = _by_status(body)
    assert body["summary"] == {
        "new": len(rows["new"]),
        "changed": len(rows["changed"]),
        "same": len(rows["same"]),
        "removed": len(body["removed"]),
    }
    assert body["summary"] == {"new": 1, "changed": 1, "same": 1, "removed": 1}
    assert len(body["rows"]) == 3


def test_diff_writes_nothing(client, conn, two_years):
    before = [
        tuple(r)
        for r in db.q(
            "SELECT id, status, start_date, end_date, reviewed_by_user FROM seasons ORDER BY id",
            conn=conn,
        )
    ]
    doc_before = [tuple(r) for r in db.q("SELECT id, status FROM source_documents", conn=conn)]

    assert _diff(client, two_years["new"])[0] == 200
    assert _diff(client, two_years["old"])[0] == 200

    after = [
        tuple(r)
        for r in db.q(
            "SELECT id, status, start_date, end_date, reviewed_by_user FROM seasons ORDER BY id",
            conn=conn,
        )
    ]
    assert after == before
    assert [tuple(r) for r in db.q("SELECT id, status FROM source_documents", conn=conn)] == (
        doc_before
    )
