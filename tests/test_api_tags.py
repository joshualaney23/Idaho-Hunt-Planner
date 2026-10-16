"""T-09: My Tags, permits and profile API (DESIGN.md §6, §12 T-09).

CRUD round-trips, the §12 validation rules (`requires_zone` / `requires_hunt_no`) and
the copy-year rules (no controlled tags, filled status never carried over).
"""

from __future__ import annotations

import datetime as dt

import pytest

from planner import db, seed
from planner.api import PERMIT_CODES

YEAR = dt.date.today().year


def _json(resp):
    return resp.get_json()


def _post(client, **body):
    return client.post("/api/tags", json=body)


def _error(resp):
    return _json(resp)["error"]


@pytest.fixture
def zone(client) -> str:
    """A real elk zone for the current year (seeded from the GIS elk zones, T-04)."""
    zones = _json(client.get("/api/tags"))["zones"]
    assert zones, "seed should create zone_units for the current year"
    return zones[0]


# ---- GET /api/tags ---------------------------------------------------------


def test_list_is_empty_but_carries_form_metadata(client):
    body = _json(client.get("/api/tags"))
    assert body["year"] == YEAR and body["tags"] == []
    assert YEAR in body["years"] and YEAR - 1 in body["years"]
    assert body["years"] == sorted(body["years"], reverse=True)

    by_id = {t["id"]: t for t in body["tag_types"]}
    assert len(by_id) == 11  # pronghorn_general retired (003 migration)
    assert by_id["deer_regular"]["species_label"] == "Deer"
    assert by_id["deer_regular"]["icon"] == "🦌"
    assert by_id["elk_zone_a"]["requires_zone"] is True
    assert by_id["controlled"]["requires_hunt_no"] is True
    assert by_id["controlled"]["species_id"] is None
    assert len(body["zones"]) >= 20 and all(isinstance(z, str) for z in body["zones"])
    species = {s["id"]: s for s in body["species"]}
    assert len(species) == 35 and species["elk"]["label"] == "Elk"


def test_bad_year_is_rejected(client):
    assert _error(client.get("/api/tags?year=nope"))["detail"] == {"year": "not a number"}
    assert client.get("/api/tags?year=12").status_code == 400


# ---- CRUD round-trip -------------------------------------------------------


def test_crud_round_trip(client, zone):
    created = _post(client, tag_type_id="deer_regular", notes="  bought at Sportsman's  ")
    assert created.status_code == 201
    tag = _json(created)["tag"]
    assert tag["license_year"] == YEAR
    assert (tag["tag_type_label"], tag["species_id"], tag["icon"]) == (
        "Regular deer tag",
        "deer",
        "🦌",
    )
    assert tag["filled"] is False and tag["zone"] is None and tag["hunt_number"] is None
    assert tag["notes"] == "bought at Sportsman's", "free text is trimmed"

    listed = _json(client.get(f"/api/tags?year={YEAR}"))["tags"]
    assert [t["id"] for t in listed] == [tag["id"]]

    filled = _json(client.put(f"/api/tags/{tag['id']}", json={"filled": True}))["tag"]
    assert filled["filled"] is True and filled["notes"] == "bought at Sportsman's"

    elk = _json(_post(client, tag_type_id="elk_zone_a", zone=zone))["tag"]
    assert elk["zone"] == zone
    moved = _json(client.put(f"/api/tags/{elk['id']}", json={"tag_type_id": "elk_zone_b"}))["tag"]
    assert (moved["tag_type_id"], moved["zone"]) == ("elk_zone_b", zone)

    assert _json(client.delete(f"/api/tags/{tag['id']}")) == {"ok": True, "deleted": tag["id"]}
    assert [t["id"] for t in _json(client.get("/api/tags"))["tags"]] == [elk["id"]]
    assert client.delete(f"/api/tags/{tag['id']}").status_code == 404
    assert client.put(f"/api/tags/{tag['id']}", json={"filled": True}).status_code == 404


def test_unfilled_tags_sort_before_filled(client):
    a = _json(_post(client, tag_type_id="deer_regular"))["tag"]
    b = _json(_post(client, tag_type_id="wolf"))["tag"]
    client.put(f"/api/tags/{a['id']}", json={"filled": True})
    assert [t["id"] for t in _json(client.get("/api/tags"))["tags"]] == [b["id"], a["id"]]


def test_tags_are_scoped_to_their_license_year(client):
    _post(client, tag_type_id="deer_regular", license_year=YEAR - 1)
    assert _json(client.get(f"/api/tags?year={YEAR}"))["tags"] == []
    assert len(_json(client.get(f"/api/tags?year={YEAR - 1}"))["tags"]) == 1


# ---- validation (shown inline by the UI) -----------------------------------


def test_zone_tag_needs_a_zone_that_exists_this_year(client, zone):
    missing = _error(_post(client, tag_type_id="elk_zone_a"))
    assert missing["code"] == "validation_failed"
    assert missing["detail"] == {"zone": "required for this tag type"}

    wrong = _error(_post(client, tag_type_id="elk_zone_a", zone="Atlantis"))
    assert wrong["detail"] == {"zone": f"no units in zone 'Atlantis' for {YEAR}"}

    # Same zone, a year with no zone_units rows: still rejected.
    stale = _error(_post(client, tag_type_id="elk_zone_a", zone=zone, license_year=1999))
    assert stale["detail"] == {"zone": f"no units in zone '{zone}' for 1999"}


def test_controlled_tag_needs_a_hunt_number(client):
    assert _error(_post(client, tag_type_id="controlled"))["detail"] == {
        "hunt_number": "required for this tag type"
    }
    assert _error(_post(client, tag_type_id="controlled", hunt_number="10-02"))["detail"] == {
        "hunt_number": "hunt numbers are digits (e.g. 1002)"
    }
    tag = _json(_post(client, tag_type_id="controlled", hunt_number=" 1002 "))["tag"]
    assert (tag["hunt_number"], tag["species_id"], tag["icon"]) == ("1002", None, "🎟️")


def test_unused_fields_are_cleared(client, zone):
    tag = _json(_post(client, tag_type_id="deer_regular", zone=zone, hunt_number="1002"))["tag"]
    assert tag["zone"] is None and tag["hunt_number"] is None


def test_unknown_and_missing_tag_type(client):
    assert _error(_post(client, tag_type_id="unicorn"))["detail"] == {
        "tag_type_id": "unknown tag type"
    }
    assert _error(_post(client))["detail"] == {"tag_type_id": "required"}


def test_bad_payloads(client):
    assert _error(_post(client, tag_type_id="deer_regular", filled="maybe"))["detail"] == {
        "filled": "not a boolean"
    }
    tag = _json(_post(client, tag_type_id="deer_regular"))["tag"]
    assert client.put(f"/api/tags/{tag['id']}", json={"colour": "red"}).status_code == 400
    assert client.post("/api/tags", data="not json").status_code == 400


@pytest.fixture
def prev_zones(app):
    """Give last year the same GIS elk zones, so last year can hold an elk A/B tag."""
    conn = db.connect(app.config["DB_PATH"])
    try:
        assert seed.seed_zone_units(conn, YEAR - 1) > 0
    finally:
        conn.close()


# ---- copy year -------------------------------------------------------------


def test_copy_year_skips_controlled_and_resets_filled(client, zone, prev_zones):
    prev = YEAR - 1
    deer = _json(_post(client, tag_type_id="deer_regular", license_year=prev, filled=True))["tag"]
    _post(client, tag_type_id="elk_zone_a", license_year=prev, zone=zone)
    _post(client, tag_type_id="controlled", license_year=prev, hunt_number="1002")
    assert deer["filled"] is True

    body = _json(client.post(f"/api/tags/copy?from={prev}&to={YEAR}"))
    assert body["copied"] == 2
    assert body["skipped"] == {"controlled": 1, "existing": 0, "invalid": 0}
    copied = {t["tag_type_id"]: t for t in body["tags"]}
    assert set(copied) == {"deer_regular", "elk_zone_a"}
    assert copied["deer_regular"]["filled"] is False, "filled status never carries over"
    assert copied["elk_zone_a"]["zone"] == zone
    assert all(t["license_year"] == YEAR for t in body["tags"])
    assert len(_json(client.get(f"/api/tags?year={prev}"))["tags"]) == 3, "source is untouched"

    again = _json(client.post(f"/api/tags/copy?from={prev}&to={YEAR}"))
    assert (again["copied"], again["skipped"]["existing"]) == (0, 2), "copy is idempotent"


def test_copy_year_skips_a_zone_that_vanished(client, app, zone, prev_zones):
    prev = YEAR - 1
    _post(client, tag_type_id="elk_zone_a", license_year=prev, zone=zone)
    conn = db.connect(app.config["DB_PATH"])
    try:
        db.exec("DELETE FROM zone_units WHERE license_year = ? AND zone = ?", YEAR, zone, conn=conn)
    finally:
        conn.close()

    body = _json(client.post(f"/api/tags/copy?from={prev}&to={YEAR}"))
    assert (body["copied"], body["skipped"]["invalid"], body["tags"]) == (0, 1, [])


def test_copy_year_argument_errors(client):
    assert client.post("/api/tags/copy").status_code == 400
    assert client.post(f"/api/tags/copy?from={YEAR}&to={YEAR}").status_code == 400
    empty = _json(client.post(f"/api/tags/copy?from=2000&to={YEAR}"))
    assert empty["copied"] == 0 and empty["tags"] == []


# ---- permits ---------------------------------------------------------------


def test_permits_round_trip(client):
    body = _json(client.get("/api/permits"))
    assert body == {
        "year": YEAR,
        "permits": [],
        "available": body["available"],
    }
    assert [p["code"] for p in body["available"]] == list(PERMIT_CODES)

    saved = _json(client.put("/api/permits", json={"permits": ["hip", "archery", "archery"]}))
    assert saved["permits"] == ["archery", "hip"], "deduped, in vocabulary order"
    assert _json(client.get(f"/api/permits?year={YEAR}"))["permits"] == ["archery", "hip"]

    assert _json(client.put("/api/permits", json={"permits": []}))["permits"] == []
    assert _json(client.get("/api/permits"))["permits"] == []


def test_permits_are_per_year_and_validated(client):
    client.put(f"/api/permits?year={YEAR - 1}", json={"permits": ["muzzleloader"]})
    assert _json(client.get("/api/permits"))["permits"] == []
    assert _json(client.get(f"/api/permits?year={YEAR - 1}"))["permits"] == ["muzzleloader"]

    bad = _error(client.put("/api/permits", json={"permits": ["crossbow"]}))
    assert bad["code"] == "validation_failed" and bad["detail"] == {"permits": ["crossbow"]}
    assert client.put("/api/permits", json={"permits": "archery"}).status_code == 400


# ---- profile ---------------------------------------------------------------


def test_profile_defaults_and_update(client):
    assert _json(client.get("/api/profile"))["profile"] == {
        "youth": 0,
        "weekend_def": "fri_sun",
        "default_species": "deer",
    }
    saved = _json(client.put("/api/profile", json={"youth": True, "weekend_def": "sat_sun"}))[
        "profile"
    ]
    assert saved == {"youth": 1, "weekend_def": "sat_sun", "default_species": "deer"}
    assert _json(client.get("/api/profile"))["profile"] == saved
    assert _json(client.put("/api/profile", json={"default_species": "elk"}))["profile"] == {
        "youth": 1,
        "weekend_def": "sat_sun",
        "default_species": "elk",
    }


def test_profile_validation_and_no_residency_field(client):
    assert _error(client.put("/api/profile", json={"weekend_def": "mon_tue"}))["detail"] == {
        "weekend_def": "unknown weekend definition"
    }
    assert _error(client.put("/api/profile", json={"default_species": "dragon"}))["detail"] == {
        "default_species": "unknown species"
    }
    # D1: residents only — the profile has no residency field to set.
    resident = client.put("/api/profile", json={"resident": False})
    assert resident.status_code == 400 and _error(resident)["detail"] == ["resident"]
    assert _json(client.get("/api/profile"))["profile"]["weekend_def"] == "fri_sun"
