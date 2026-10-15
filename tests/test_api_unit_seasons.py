"""T-08: `GET /api/units/<unit>/seasons` (the Unit Brief) and the IDFG unit-page links."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from planner import db
from tests.test_api_availability import _doc, _season, _tag

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture
def conn(app):
    c = db.connect(app.config["DB_PATH"])
    yield c
    c.close()


def _brief(client, unit: str, **params):
    resp = client.get(f"/api/units/{unit}/seasons", query_string=params)
    return resp.status_code, resp.get_json()


def test_unit_seasons_lists_published_seasons_for_the_year(client, conn):
    doc = _doc(conn)
    archery = _season(
        conn, doc, ["39"], weapon="archery", start_date="2026-08-30", end_date="2026-09-30"
    )
    rifle = _season(
        conn,
        doc,
        ["39", "40"],
        restrictions='["whitetail_only"]',
        notes="Verbatim footnote from the PDF.",
        bag_limit="1 deer",
        source_page=13,
    )
    elk = _season(conn, doc, ["39"], species_id="elk", tag_type_id="elk_zone_a", zone="Boise River")
    _season(conn, doc, ["39"], status="staged")  # never shown
    _season(conn, doc, ["40"])  # other unit
    _season(conn, _doc(conn, 2025), ["39"], start_date="2025-10-10", end_date="2025-10-24")
    db.exec(
        "UPDATE season_units SET coverage = 'partial', coverage_note = 'Portion of Unit 39'"
        " WHERE season_id = ? AND unit_id = '39'",
        rifle,
        conn=conn,
    )
    _tag(conn)  # deer_regular, 2026

    status, body = _brief(client, "39", year=2026)
    assert status == 200
    assert body["year"] == 2026
    assert body["held_species"] == ["deer"]
    assert body["unit"]["id"] == "39"
    assert body["unit"]["idfg_url"] == "https://idfg.idaho.gov/ifwis/huntplanner/unit.aspx?ID=53"
    # Species in seed order (deer before elk), then by start date.
    assert [s["id"] for s in body["seasons"]] == [archery, rifle, elk]
    s = body["seasons"][1]
    assert s["restrictions"] == ["whitetail_only"]
    assert (s["notes"], s["bag_limit"], s["source_page"], s["document_id"]) == (
        "Verbatim footnote from the PDF.",
        "1 deer",
        13,
        doc,
    )
    assert (s["coverage"], s["coverage_note"]) == ("partial", "Portion of Unit 39")
    assert (s["start"], s["end"], s["species_label"], s["tag_type_label"]) == (
        "2026-10-10",
        "2026-10-24",
        "Deer",
        "Regular deer tag",
    )
    assert body["seasons"][2]["zone"] == "Boise River"

    status, body = _brief(client, "39", year=2025)
    assert status == 200 and len(body["seasons"]) == 1 and body["held_species"] == []


def test_two_year_documents_count_for_both_years(client, conn):
    doc = db.exec(
        "INSERT INTO source_documents (license_year, year_end, kind, title, status)"
        " VALUES (2026, 2027, 'upland', 'Upland 2026-27', 'published')",
        conn=conn,
    ).lastrowid
    _season(conn, doc, ["1"], species_id="forest_grouse", tag_type_id="hunting_license")
    for year in (2026, 2027):
        assert len(_brief(client, "1", year=year)[1]["seasons"]) == 1, year
    assert _brief(client, "1", year=2028)[1]["seasons"] == []


def test_unit_seasons_errors(client):
    status, body = _brief(client, "999")
    assert status == 404 and body["error"]["code"] == "unknown_unit"
    status, body = _brief(client, "39", year="soon")
    assert status == 400 and body["error"]["code"]
    status, body = _brief(client, "YNP")
    assert status == 200 and body["seasons"] == [] and body["unit"]["idfg_url"] is None


def test_idfg_links_cover_every_huntable_unit():
    """`scripts/build_idfg_unit_links.py` output: one page id per unit, ids unique."""
    links = json.loads((REPO / "static" / "data" / "idfg_units.json").read_text())
    units = [
        u["id"] for u in json.loads((REPO / "static" / "data" / "units_seed.json").read_text())
    ]
    assert sorted(links) == sorted(u for u in units if u != "YNP")
    assert len(set(links.values())) == len(links)
    # Spot checks read off the live pages' <h1> headings.
    assert (links["1"], links["4A"], links["5"], links["61"], links["70"]) == (1, 5, 6, 78, 91)
