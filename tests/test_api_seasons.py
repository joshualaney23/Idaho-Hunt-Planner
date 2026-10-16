"""T-16: the Season Table API — `/api/seasons` CRUD and the CSV round-trip (§6).

Two acceptance criteria live here: **export → import → export is byte-identical**, and
**an invalid row reports its line number**. The rest guards the manual-document rules:
a hand-typed row publishes at once under a `manual` document and supersedes the
brochure row it corrects (§8.5), an imported CSV row waits in `staged` for review, and
a parsed row can never be edited behind the review screen's back (D3).
"""

from __future__ import annotations

import json

import pytest

from planner import db
from tests.test_api_availability import _doc, _season

NEW_ROW = {
    "license_year": 2026,
    "species_id": "deer",
    "tag_type_id": "deer_regular",
    "hunt_class": "general",
    "weapon": "archery",
    "sex": "either",
    "start_date": "2026-08-30",
    "end_date": "2026-09-30",
    "units": ["39", "4A"],
    "restrictions": ["antlerless_only"],
    "notes": "typed by hand",
    "source_page": 14,
}


@pytest.fixture
def conn(app):
    c = db.connect(app.config["DB_PATH"])
    yield c
    c.close()


@pytest.fixture
def published(conn):
    """Two published brochure rows: what the Season Table shows by default."""
    doc = _doc(conn)
    a = _season(conn, doc, ["39", "43"], source_page=12, notes="general deer")
    b = _season(
        conn,
        doc,
        ["4A"],
        species_id="elk",
        tag_type_id="controlled",
        hunt_class="controlled",
        hunt_number="1001",
        weapon="muzzleloader",
        sex="antlered",
        start_date="2026-11-01",
        end_date="2026-11-14",
        restrictions=json.dumps(["youth_only"]),
        source_page=40,
    )
    return doc, a, b


def _get(client, path: str):
    resp = client.get(path)
    return resp.status_code, resp.get_json()


# ───────── GET /api/seasons: the table ─────────


def test_lists_published_rows_with_labels_and_units(client, published) -> None:
    _, a, b = published
    code, body = _get(client, "/api/seasons")
    assert code == 200
    assert body["count"] == 2
    rows = {r["id"]: r for r in body["seasons"]}
    assert rows[a]["units"] == ["4A", "39"] or rows[a]["units"] == ["39", "43"]
    assert rows[a]["species_label"] == "Deer"
    assert rows[a]["license_year"] == 2026
    assert rows[a]["manual"] is False
    assert rows[b]["restrictions"] == ["youth_only"]
    assert rows[b]["tag_type_label"]


def test_filters_narrow_the_table(client, published) -> None:
    _, a, b = published
    assert _get(client, "/api/seasons?species=elk")[1]["count"] == 1
    assert _get(client, "/api/seasons?weapon=muzzleloader")[1]["seasons"][0]["id"] == b
    assert _get(client, "/api/seasons?tag_type=deer_regular")[1]["seasons"][0]["id"] == a
    assert _get(client, "/api/seasons?year=2025")[1]["count"] == 0
    assert _get(client, "/api/seasons?q=1001")[1]["seasons"][0]["id"] == b


def test_unit_search_is_a_prefix_so_typing_narrows(client, published) -> None:
    _, a, b = published
    # '4' matches unit 4A *and* unit 43 — a prefix search narrows, it doesn't jump.
    assert sorted(r["id"] for r in _get(client, "/api/seasons?unit=4")[1]["seasons"]) == sorted(
        [a, b]
    )
    assert [r["id"] for r in _get(client, "/api/seasons?unit=4a")[1]["seasons"]] == [b]
    assert [r["id"] for r in _get(client, "/api/seasons?unit=39")[1]["seasons"]] == [a]
    assert _get(client, "/api/seasons?unit=zz")[1]["count"] == 0


def test_status_filter_and_its_vocabulary(client, conn, published) -> None:
    doc, _, _ = published
    _season(conn, doc, ["43"], status="staged")
    assert _get(client, "/api/seasons")[1]["count"] == 2
    assert _get(client, "/api/seasons?status=staged")[1]["count"] == 1
    assert _get(client, "/api/seasons?status=all")[1]["count"] == 3
    code, body = _get(client, "/api/seasons?status=nonsense")
    assert code == 400 and body["error"]["code"] == "validation_failed"


# ───────── POST /api/seasons: the manual editor ─────────


def test_add_season_publishes_under_an_auto_created_manual_document(client, conn) -> None:
    code, body = client.post("/api/seasons", json=NEW_ROW), None
    body = code.get_json()
    assert code.status_code == 201, body
    season = body["season"]
    assert season["status"] == "published"
    assert season["manual"] is True
    assert season["units"] == ["4A", "39"]
    assert season["restrictions"] == ["antlerless_only"]
    doc = body["document"]
    assert (doc["kind"], doc["license_year"], doc["status"]) == ("manual", 2026, "published")
    assert doc["sha256"] is None  # a manual document has no PDF
    # A second row reuses the same document.
    again = client.post("/api/seasons", json={**NEW_ROW, "units": ["43"]}).get_json()
    assert again["document"]["id"] == doc["id"]
    assert db.q1("SELECT COUNT(*) AS n FROM source_documents", conn=conn)["n"] == 1


def test_manual_rows_show_on_the_map_query(client, published) -> None:
    """A manual row is published, so availability picks it up at once (§7)."""
    client.post("/api/seasons", json={**NEW_ROW, "weapon": "any", "units": ["33"]})
    code, body = _get(
        client,
        "/api/availability?species=deer&start=2026-09-05&end=2026-09-06&include_untagged=1",
    )
    assert code == 200
    assert body["units"]["33"]["status"] in ("open", "restricted", "untagged")
    assert body["units"]["33"]["seasons"]


def test_a_manual_row_supersedes_the_brochure_row_it_corrects(client, conn, published) -> None:
    _, a, _ = published
    body = client.post(
        "/api/seasons",
        json={**NEW_ROW, "weapon": "any", "sex": "either", "units": ["39"], "restrictions": []},
    ).get_json()
    assert a in body["superseded"]
    assert db.q1("SELECT status FROM seasons WHERE id = ?", a, conn=conn)["status"] == "superseded"


@pytest.mark.parametrize(
    ("patch", "field"),
    [
        ({"species_id": "unicorn"}, "species_id"),
        ({"tag_type_id": "nope"}, "tag_type_id"),
        ({"weapon": "bazooka"}, "weapon"),
        ({"sex": "maybe"}, "sex"),
        ({"hunt_class": "whatever"}, "hunt_class"),
        ({"units": ["99Z"]}, "units"),
        ({"units": []}, "units"),
        ({"restrictions": ["no_such_code"]}, "restrictions"),
        ({"start_date": "2026-13-01"}, "start_date"),
        ({"end_date": "2026-01-01"}, "end_date"),
        ({"source_page": 0}, "source_page"),
    ],
)
def test_bad_values_are_rejected_with_the_field_name(client, conn, patch, field) -> None:
    resp = client.post("/api/seasons", json={**NEW_ROW, **patch})
    assert resp.status_code in (400, 404), resp.get_json()
    error = resp.get_json()["error"]
    assert field in json.dumps(error)  # the field is always named, in detail or message
    assert not db.q("SELECT id FROM seasons", conn=conn)


def test_missing_required_fields_are_named(client) -> None:
    resp = client.post("/api/seasons", json={"species_id": "deer"})
    body = resp.get_json()
    assert resp.status_code == 400
    assert set(body["error"]["detail"]) >= {"license_year", "tag_type_id", "units", "start_date"}


# ───────── PUT / DELETE: manual rows only ─────────


def test_edit_and_delete_a_manual_row(client, conn) -> None:
    sid = client.post("/api/seasons", json=NEW_ROW).get_json()["season"]["id"]
    code = client.put(f"/api/seasons/{sid}", json={"units": ["43"], "notes": "fixed"})
    assert code.status_code == 200
    season = code.get_json()["season"]
    assert season["units"] == ["43"] and season["notes"] == "fixed"
    assert not db.q(
        "SELECT 1 FROM season_units WHERE season_id = ? AND unit_id = '39'", sid, conn=conn
    )
    assert client.delete(f"/api/seasons/{sid}").status_code == 200
    assert not db.q("SELECT id FROM seasons WHERE id = ?", sid, conn=conn)
    assert not db.q("SELECT 1 FROM season_units WHERE season_id = ?", sid, conn=conn)


def test_editing_the_year_moves_the_row_to_that_years_manual_document(client) -> None:
    sid = client.post("/api/seasons", json=NEW_ROW).get_json()["season"]["id"]
    body = client.put(
        f"/api/seasons/{sid}",
        json={"license_year": 2027, "start_date": "2027-09-01", "end_date": "2027-09-30"},
    ).get_json()
    assert body["season"]["license_year"] == 2027
    docs = client.get("/api/documents").get_json()["documents"]
    assert sorted(d["license_year"] for d in docs if d["kind"] == "manual") == [2026, 2027]


def test_a_parsed_row_cannot_be_edited_outside_the_review_screen(client, published) -> None:
    _, a, _ = published
    for call in (
        client.put(f"/api/seasons/{a}", json={"notes": "sneaky"}),
        client.delete(f"/api/seasons/{a}"),
    ):
        assert call.status_code == 409
        assert call.get_json()["error"]["code"] == "not_manual"
    assert client.put("/api/seasons/9999", json={"notes": "x"}).status_code == 404


# ───────── the CSV round trip (acceptance criterion 1) ─────────


def test_export_import_export_is_byte_identical(client, published) -> None:
    first = client.get("/api/export.csv")
    assert first.status_code == 200
    assert first.mimetype == "text/csv"
    assert "attachment" in first.headers["Content-Disposition"]
    text = first.get_data(as_text=True)
    assert text.splitlines()[0].startswith("license_year,species,tag_type")

    imported = client.post(
        "/api/import.csv", data=text.encode(), content_type="text/csv"
    ).get_json()
    assert imported["imported"] == 2
    assert imported["status"] == "staged"

    second = client.get("/api/export.csv?status=staged").get_data(as_text=True)
    assert second == text


def test_round_trip_keeps_commas_quotes_and_multi_unit_rows(client, conn) -> None:
    doc = _doc(conn)
    _season(
        conn,
        doc,
        ["39", "4A", "43", "10"],
        notes='Portion of Unit 39, east of US-95 ("the gap")',
        restrictions=json.dumps(["portion_of_unit", "antlerless_only"]),
        zone="Sawtooth",
        hunt_number="1055",
    )
    text = client.get("/api/export.csv").get_data(as_text=True)
    assert (
        client.post("/api/import.csv", data=text.encode(), content_type="text/csv").status_code
        == 201
    )
    assert client.get("/api/export.csv?status=staged").get_data(as_text=True) == text
    row = client.get("/api/seasons?status=staged").get_json()["seasons"][0]
    assert row["units"] == ["4A", "10", "39", "43"]
    assert row["notes"] == 'Portion of Unit 39, east of US-95 ("the gap")'
    assert row["restrictions"] == ["antlerless_only", "portion_of_unit"]


def test_import_stages_rows_for_review_under_a_manual_document(client, published) -> None:
    text = client.get("/api/export.csv").get_data(as_text=True)
    body = client.post("/api/import.csv", data=text.encode(), content_type="text/csv").get_json()
    doc = body["documents"][0]
    assert doc["kind"] == "manual"
    assert doc["status"] == "needs_review"  # staged rows wait for the review screen
    assert doc["seasons"]["staged"] == 2
    detail = client.get(f"/api/documents/{doc['id']}").get_json()
    assert len(detail["seasons"]) == 2
    assert all(s["status"] == "staged" for s in detail["seasons"])


def test_import_accepts_a_multipart_upload(client, published) -> None:
    import io

    text = client.get("/api/export.csv").get_data(as_text=True)
    resp = client.post(
        "/api/import.csv",
        data={"file": (io.BytesIO(text.encode()), "seasons.csv")},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 201 and resp.get_json()["imported"] == 2


def test_the_shipped_template_imports_cleanly(client) -> None:
    from pathlib import Path

    template = Path(__file__).resolve().parent.parent / "docs" / "seasons_template.csv"
    resp = client.post("/api/import.csv", data=template.read_bytes(), content_type="text/csv")
    assert resp.status_code == 201, resp.get_json()
    assert resp.get_json()["imported"] == 3


# ───────── invalid rows report line numbers (acceptance criterion 2) ─────────


def test_invalid_rows_report_line_numbers_and_import_nothing(client, published) -> None:
    text = client.get("/api/export.csv").get_data(as_text=True)
    lines = text.split("\r\n")
    broken = "\r\n".join(
        [
            lines[0],
            lines[1],
            lines[1].replace("deer", "unicorn", 1),
            lines[2].replace("2026-11-01", "2026-13-01"),
            "2026,deer,deer_regular,,,general,any,either,2026-10-10,2026-10-24,99Z,,,",
        ]
    )
    resp = client.post("/api/import.csv", data=broken.encode(), content_type="text/csv")
    assert resp.status_code == 400
    error = resp.get_json()["error"]
    assert error["code"] == "invalid_csv"
    assert [(e["line"], e["field"]) for e in error["detail"]["errors"]] == [
        (3, "species"),
        (4, "start_date"),
        (5, "units"),
    ]
    assert error["detail"]["lines"] == [3, 4, 5]
    assert error["detail"]["rows_ok"] == 1
    # Strict: not even the good row landed.
    assert client.get("/api/seasons?status=staged").get_json()["count"] == 0


def test_an_empty_or_headerless_file_is_rejected(client) -> None:
    empty = client.post("/api/import.csv", data=b"", content_type="text/csv")
    assert empty.status_code == 400 and empty.get_json()["error"]["code"] == "invalid_csv"
    bad = client.post("/api/import.csv", data=b"a,b\n1,2\n", content_type="text/csv")
    detail = bad.get_json()["error"]["detail"]
    assert detail["errors"][0]["line"] == 1 and detail["errors"][0]["field"] == "header"
