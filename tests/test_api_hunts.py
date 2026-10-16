"""T-17c: `GET /api/hunts/<year>/<no>`, controlled tags in `/api/tags`, and the §7.2
controlled-hunt rules end to end through `/api/availability`.

The hunt numbers, units, areas and dates here are the real 2026 Big Game ones (hunt
#1002 = Unit 11, #1028 = area 38-1 inside Unit 38), so a failure reads like the PR's
acceptance criteria.
"""

from __future__ import annotations

import pytest

from planner import db


def _doc(conn, year: int = 2026, status: str = "published") -> int:
    return db.exec(
        "INSERT INTO source_documents (license_year, year_end, kind, title, status)"
        " VALUES (?, ?, 'big_game', ?, ?)",
        year,
        year,
        f"big_game {year}",
        status,
        conn=conn,
    ).lastrowid


def _season(conn, doc: int, units: list[tuple], **kw) -> int:
    """One season row; `units` are `(unit_id, coverage, coverage_frac, coverage_note)`."""
    row = {
        "status": "published",
        "species_id": "deer",
        "tag_type_id": "controlled",
        "hunt_class": "controlled",
        "weapon": "any",
        "sex": "antlered",
        "start_date": "2026-10-10",
        "end_date": "2026-11-03",
        "restrictions": "[]",
        "source_page": 22,
    }
    row.update(kw)
    cols = ", ".join(["document_id", *row])
    marks = ", ".join("?" * (len(row) + 1))
    sid = db.exec(
        f"INSERT INTO seasons ({cols}) VALUES ({marks})", doc, *row.values(), conn=conn
    ).lastrowid
    db.exec_many(
        "INSERT INTO season_units (season_id, unit_id, coverage, coverage_frac, coverage_note)"
        " VALUES (?, ?, ?, ?, ?)",
        [(sid, *_unit(u)) for u in units],
        conn=conn,
    )
    return sid


def _unit(u: tuple) -> tuple:
    """`('11',)` → `('11', 'full', None, None)`."""
    return (u[0], u[1] if len(u) > 1 else "full", *(u[2:] + (None, None))[:2])


def _tag(conn, tag_type: str = "controlled", year: int = 2026, **kw) -> int:
    return db.exec(
        "INSERT INTO my_tags (license_year, tag_type_id, zone, hunt_number, filled, created_at)"
        " VALUES (?, ?, ?, ?, ?, '2026-01-01')",
        year,
        tag_type,
        kw.get("zone"),
        kw.get("hunt_number"),
        kw.get("filled", 0),
        conn=conn,
    ).lastrowid


@pytest.fixture
def conn(app):
    c = db.connect(app.config["DB_PATH"])
    yield c
    c.close()


AREA_38_1 = "That portion of Unit 38 lying south of Interstate 84 and west of State Highway 51."


@pytest.fixture
def hunts(conn):
    """The three 2026 hunts the acceptance criteria use, plus a 2025 #1002."""
    doc = _doc(conn)
    ids = {
        "1002": _season(conn, doc, [("11", "full", 1.0, None)], hunt_number="1002", hunt_area="11"),
        "1028": _season(
            conn,
            doc,
            [("38", "partial", 0.41, f"{AREA_38_1[:60]}… — see p.33")],
            hunt_number="1028",
            hunt_area="38-1",
            end_date="2026-11-24",
            source_page=23,
        ),
        "1056": _season(
            conn,
            doc,
            [("39", "full", 1.0, None)],
            hunt_number="1056",
            hunt_area="39",
            weapon="archery",
            source_page=24,
        ),
    }
    db.exec(
        "INSERT INTO hunt_areas (license_year, species_id, area_id, description,"
        " description_page, document_id) VALUES (2026, 'deer', '38-1', ?, 33, ?)",
        AREA_38_1,
        doc,
        conn=conn,
    )
    old = _doc(conn, year=2025)
    ids["2025:1002"] = _season(
        conn, old, [("60", "full", 1.0, None)], hunt_number="1002", hunt_area="60"
    )
    return ids


def _avail(client, **params):
    params.setdefault("species", "deer")
    params.setdefault("start", "2026-10-17")
    params.setdefault("end", "2026-10-18")
    resp = client.get("/api/availability", query_string=params)
    assert resp.status_code == 200, resp.get_json()
    return resp.get_json()


def _by_status(body: dict) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for unit, u in body["units"].items():
        out.setdefault(u["status"], []).append(unit)
    return out


# ───────── the lookup endpoint ─────────
def test_hunt_lookup_returns_the_card_the_dialog_shows(client, hunts):
    hunt = client.get("/api/hunts/2026/1002").get_json()["hunt"]
    assert hunt["species_id"] == "deer"
    assert hunt["species_label"] == "Deer"
    assert hunt["species_ids"] == ["deer"]
    assert hunt["hunt_number"] == "1002"
    assert hunt["hunt_class"] == "controlled"
    assert hunt["weapon"] == "any"
    assert hunt["sex"] == "antlered"
    assert hunt["hunt_area"] == "11"
    assert (hunt["start"], hunt["end"]) == ("2026-10-10", "2026-11-03")
    assert hunt["status"] == "published" and hunt["published"] is True
    assert hunt["units"] == [
        {"unit": "11", "coverage": "full", "coverage_frac": 1.0, "coverage_note": None}
    ]
    assert hunt["source_page"] == 22


def test_hunt_lookup_carries_the_verbatim_area_description_and_page(client, hunts):
    hunt = client.get("/api/hunts/2026/1028").get_json()["hunt"]
    assert hunt["hunt_area"] == "38-1"
    assert hunt["description"] == AREA_38_1
    assert hunt["description_page"] == 33
    assert hunt["units"][0]["unit"] == "38"
    assert hunt["units"][0]["coverage"] == "partial"


def test_one_hunt_number_can_print_several_windows(client, conn):
    doc = _doc(conn)
    _season(conn, doc, [("1", "full", 1.0, None)], hunt_number="1123", hunt_area="1")
    _season(
        conn,
        doc,
        [("1", "full", 1.0, None)],
        hunt_number="1123",
        hunt_area="1",
        start_date="2026-11-10",
        end_date="2026-11-18",
    )
    hunt = client.get("/api/hunts/2026/1123").get_json()["hunt"]
    assert [(w["start"], w["end"]) for w in hunt["windows"]] == [
        ("2026-10-10", "2026-11-03"),
        ("2026-11-10", "2026-11-18"),
    ]
    assert (hunt["start"], hunt["end"]) == ("2026-10-10", "2026-11-18")
    assert [u["unit"] for u in hunt["units"]] == ["1"]  # one row per unit, not per window


def test_unknown_and_rejected_hunt_numbers_are_404(client, conn, hunts):
    body = client.get("/api/hunts/2026/9999")
    assert body.status_code == 404
    assert body.get_json()["error"]["code"] == "unknown_hunt"
    assert body.get_json()["error"]["detail"]["reason"] == "not_found"

    doc = _doc(conn)
    _season(conn, doc, [("12", "full", 1.0, None)], hunt_number="1500", status="rejected")
    rejected = client.get("/api/hunts/2026/1500")
    assert rejected.status_code == 404
    assert rejected.get_json()["error"]["detail"]["reason"] == "rejected"


def test_a_staged_hunt_is_found_but_flagged(client, conn):
    doc = _doc(conn, status="needs_review")
    _season(conn, doc, [("11", "full", 1.0, None)], hunt_number="1002", status="staged")
    hunt = client.get("/api/hunts/2026/1002").get_json()["hunt"]
    assert hunt["status"] == "staged" and hunt["published"] is False


def test_a_hunt_number_is_scoped_to_its_license_year(client, hunts):
    assert client.get("/api/hunts/2026/1002").get_json()["hunt"]["units"][0]["unit"] == "11"
    assert client.get("/api/hunts/2025/1002").get_json()["hunt"]["units"][0]["unit"] == "60"


def test_a_malformed_hunt_number_is_a_400(client):
    resp = client.get("/api/hunts/2026/10-02")
    assert resp.status_code == 400
    assert resp.get_json()["error"]["code"] == "bad_hunt_number"


# ───────── the tag card ─────────
def test_a_controlled_tag_carries_its_hunt(client, conn, hunts):
    _tag(conn, hunt_number="1002")
    tag = client.get("/api/tags?year=2026").get_json()["tags"][0]
    assert tag["hunt"]["species_label"] == "Deer"
    assert [u["unit"] for u in tag["hunt"]["units"]] == ["11"]
    assert (tag["hunt"]["start"], tag["hunt"]["end"]) == ("2026-10-10", "2026-11-03")
    # The species of a controlled tag comes from the hunt (§7.2), not from the tag type.
    assert (tag["species_id"], tag["icon"]) == ("deer", "🦌")


def test_a_tag_for_a_hunt_that_isnt_imported_yet_still_lists(client, conn):
    _tag(conn, hunt_number="1002")
    tag = client.get("/api/tags?year=2026").get_json()["tags"][0]
    assert tag["hunt"] is None
    assert tag["species_id"] is None


def test_a_general_tag_has_no_hunt_key(client, conn):
    _tag(conn, tag_type="deer_regular")
    tag = client.get("/api/tags?year=2026").get_json()["tags"][0]
    assert "hunt" not in tag


# ───────── §7.2 through /api/availability (the §12 acceptance criteria) ─────────
def test_only_the_controlled_hunts_unit_lights_up(client, conn, hunts):
    _tag(conn, hunt_number="1002")
    body = _avail(client)
    assert _by_status(body)["open"] == ["11"]
    assert body["summary"]["open"] == 1
    assert body["summary"]["restricted"] == 0
    assert body["units"]["11"]["seasons"][0]["hunt_number"] == "1002"


def test_a_partial_hunt_area_is_restricted_with_the_description_and_page(client, conn, hunts):
    _tag(conn, hunt_number="1028")
    body = _avail(client)
    assert _by_status(body) == {
        "restricted": ["38"],
        "closed": [u for u in body["units"] if u != "38"],
    }
    unit = body["units"]["38"]
    assert unit["reasons"] == ["portion_of_unit"]
    season = unit["seasons"][0]
    assert season["coverage"] == "partial"
    assert season["coverage_note"].startswith("That portion of Unit 38")
    assert "p.33" in season["coverage_note"]
    assert season["source_page"] == 23


def test_last_years_controlled_tag_lights_up_nothing(client, conn, hunts):
    _tag(conn, hunt_number="1002", year=2025)
    body = _avail(client)
    assert body["summary"] == {"open": 0, "restricted": 0, "untagged": 0, "closed": 100}


def test_controlled_and_general_tags_show_their_union_and_name_their_tag(client, conn, hunts):
    doc = _doc(conn)
    _season(
        conn,
        doc,
        [("39", "full", 1.0, None), ("40", "full", 1.0, None)],
        tag_type_id="deer_regular",
        hunt_class="general",
        hunt_number=None,
        hunt_area=None,
        sex="either",
        source_page=12,
    )
    controlled = _tag(conn, hunt_number="1002")
    regular = _tag(conn, tag_type="deer_regular")
    body = _avail(client)
    assert sorted(_by_status(body)["open"], key=int) == ["11", "39", "40"]
    assert body["units"]["11"]["seasons"][0]["tag_ids"] == [controlled]
    assert body["units"]["39"]["seasons"][0]["tag_ids"] == [regular]


def test_an_archery_hunt_needs_the_archery_permit(client, conn, hunts):
    _tag(conn, hunt_number="1056")
    assert _avail(client)["summary"]["open"] == 0
    shown = _avail(client, include_untagged=1)["units"]["39"]
    assert shown["status"] == "untagged"
    assert "needs_archery_permit" in shown["reasons"]

    db.exec(
        "INSERT INTO my_permits (license_year, permit) VALUES (2026, 'archery')",
        conn=conn,
    )
    assert _by_status(_avail(client))["open"] == ["39"]


def test_an_outfitter_hunt_stays_hidden_unless_its_number_is_held(client, conn):
    doc = _doc(conn)
    _season(
        conn,
        doc,
        [("45", "full", 1.0, None)],
        hunt_number="2001",
        hunt_area="45",
        hunt_class="outfitter",
    )
    assert _avail(client, include_untagged=1)["summary"]["untagged"] == 0
    _tag(conn, hunt_number="2001")
    held = _avail(client)["units"]["45"]
    assert held["status"] == "restricted"
    assert held["reasons"] == ["outfitter_only"]
