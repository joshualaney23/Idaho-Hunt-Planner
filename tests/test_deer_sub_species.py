"""T-31: the White-tailed / Mule deer sub-species filters (engine §7.2 + `/api/availability`).

The matrix these tests encode is the 2026 Big Game brochure's own tag definition (p. 11):

    "Resident deer hunters may choose either a regular deer tag or a white-tailed deer
    tag. The regular deer tag is valid for any hunt listed under '2026 Regular Deer Tag
    Seasons' on pages 11 - 15 and may be used to take a mule deer or white-tailed deer
    during those seasons. … The white-tailed deer tag is valid for white-tailed deer
    only, for any hunt listed under '2026 White-tailed Deer Tag Seasons' on pages 17 - 20."

and the Regular table's own disclaimer (physical p. 12): "Regular Deer tag is valid for
mule deer or white-tailed deer unless otherwise noted."
"""

from __future__ import annotations

import pytest

from planner import db, engine

from .test_api_availability import _doc, _season, _tag

RANGE = {"start": "2026-10-17", "end": "2026-10-18"}


@pytest.fixture
def conn(app):
    c = db.connect(app.config["DB_PATH"])
    yield c
    c.close()


@pytest.fixture
def seeded(conn):
    """One published 2026 deer document covering every row of the matrix.

    * unit 1 — Regular tag, antlered Oct 10–31, "(White-tailed deer only)" (T-11 spot check)
    * unit 1 — White-tailed Deer Tag table season (its own tag type, `sub_species`)
    * unit 2 — Regular tag, mule-deer-only
    * unit 3 — Regular tag, any deer
    """
    doc = _doc(conn)
    ids = {
        "wt": _season(
            conn,
            doc,
            ["1"],
            start_date="2026-10-10",
            end_date="2026-10-31",
            sex="antlered",
            restrictions='["whitetail_only"]',
        ),
        "wt_tag": _season(
            conn,
            doc,
            ["1"],
            tag_type_id="deer_whitetail",
            sub_species="whitetail",
            start_date="2026-10-10",
            end_date="2026-10-31",
        ),
        "mule": _season(conn, doc, ["2"], restrictions='["mule_deer_only"]'),
        "any": _season(conn, doc, ["3"]),
    }
    return ids


def _units(client, **params):
    resp = client.get("/api/availability", query_string={**RANGE, **params})
    assert resp.status_code == 200, resp.get_json()
    body = resp.get_json()
    return {u: v["status"] for u, v in body["units"].items()}, body


# ───────── acceptance criteria (§12 T-31) ─────────
def test_regular_tag_sees_each_animal_under_its_own_filter(client, conn, seeded):
    _tag(conn)  # a Regular Deer tag, and nothing else

    wt, _ = _units(client, species="deer_whitetail")
    mule, _ = _units(client, species="deer_mule")
    both, _ = _units(client, species="deer")

    # The white-tailed-only unit is fully open under White-tailed deer, shut under Mule.
    assert (wt["1"], mule["1"]) == ("open", "closed")
    # …and the mule-deer-only unit the other way round.
    assert (wt["2"], mule["2"]) == ("closed", "open")
    # A season for any deer counts under both filters, and under plain Deer.
    assert wt["3"] == mule["3"] == both["3"] == "open"
    # Plain Deer is unchanged: each sub-species season is still only `restricted`.
    assert (both["1"], both["2"]) == ("restricted", "restricted")


def test_a_white_tailed_tag_never_reaches_mule_deer(client, conn, seeded):
    _tag(conn, "deer_whitetail")  # only a White-tailed Deer tag

    mule, body = _units(client, species="deer_mule")
    assert set(mule.values()) == {"closed"}
    assert body["summary"] == {"open": 0, "restricted": 0, "untagged": 0, "closed": 100}
    assert body["tags"] == "mine"

    wt, _ = _units(client, species="deer_whitetail")
    # Unit 1 is open on the White-tailed Deer Tag season — the same unit the Regular tag
    # reaches through the "(White-tailed deer only)" season, and with no narrowing reason.
    assert wt["1"] == "open"
    # The Regular-tag seasons stay shut: the brochure's tag definition, not the filter.
    assert wt["2"] == wt["3"] == "closed"


def test_plain_deer_is_unchanged_and_never_worse_than_a_sub_filter(client, conn, seeded):
    _tag(conn)
    deer, _ = _units(client, species="deer")
    # The T-06 behavior, byte for byte: sub-species codes narrow, they don't exclude.
    assert {u: s for u, s in deer.items() if s != "closed"} == {
        "1": "restricted",
        "2": "restricted",
        "3": "open",
    }
    # A sub-filter only ever *refines* Deer: it never opens a unit Deer calls closed.
    # (It can turn Deer's yellow into green — that is the point of the filter, and §12's
    # first acceptance criterion; see the PR's deviation note.)
    for sub in ("deer_whitetail", "deer_mule"):
        sub_statuses, _ = _units(client, species=sub)
        for uid, status in sub_statuses.items():
            if status != "closed":
                assert deer[uid] != "closed", (sub, uid)


def test_the_matching_code_stops_narrowing_but_other_reasons_remain(client, conn, seeded):
    _tag(conn)
    # A second white-tailed-only season on unit 4 that covers only half the range.
    doc = db.q1("SELECT id FROM source_documents", conn=conn)["id"]
    _season(
        conn,
        doc,
        ["4"],
        start_date="2026-10-10",
        end_date="2026-10-17",
        restrictions='["whitetail_only", "private_land_only"]',
    )

    _, body = _units(client, species="deer_whitetail")
    assert body["units"]["1"]["reasons"] == []  # whitetail_only is satisfied by the filter
    assert body["units"]["4"]["reasons"] == ["private_land_only", "partial_dates"]
    assert body["units"]["4"]["status"] == "restricted"
    # The season still carries the code, so the tooltip and badges can name it.
    assert "whitetail_only" in body["units"]["1"]["seasons"][0]["restrictions"]

    _, plain = _units(client, species="deer")
    assert plain["units"]["1"]["reasons"] == ["whitetail_only"]


def test_include_untagged_only_offers_this_sub_species(client, conn, seeded):
    # No tags at all: every season is `untagged` under plain Deer…
    _, plain = _units(client, species="deer", include_untagged=1)
    assert [u for u, v in plain["units"].items() if v["status"] != "closed"] == ["1", "2", "3"]

    # …but under a sub-filter the other animal's seasons are excluded outright (closed),
    # never listed as something you could hunt with the right tag.
    _, wt = _units(client, species="deer_whitetail", include_untagged=1)
    assert wt["units"]["2"]["status"] == "closed"
    assert wt["units"]["2"]["seasons"] == []
    assert wt["units"]["1"]["status"] == "untagged"
    assert {s["id"] for s in wt["units"]["1"]["seasons"]} == {seeded["wt"], seeded["wt_tag"]}

    _, mule = _units(client, species="deer_mule", include_untagged=1)
    assert mule["units"]["1"]["status"] == "closed"
    assert mule["units"]["1"]["seasons"] == []
    assert mule["units"]["2"]["status"] == "untagged"


def test_an_unknown_virtual_id_is_still_a_400(client):
    resp = client.get("/api/availability", query_string={"species": "deer_elk"})
    assert resp.status_code == 400
    assert resp.get_json()["error"]["code"] == "unknown_species"


def test_meta_lists_both_filters_beside_deer(client, conn, seeded):
    m = client.get("/api/meta").get_json()
    ids = [s["id"] for s in m["species"]]
    assert ids[:3] == ["deer", "deer_whitetail", "deer_mule"]
    by_id = {s["id"]: s for s in m["species"]}
    assert by_id["deer_whitetail"]["label"] == "White-tailed deer"
    assert by_id["deer_mule"]["label"] == "Mule deer"
    assert by_id["deer_mule"]["icon"] == by_id["deer"]["icon"] == "🦌"
    assert by_id["deer_whitetail"]["base"] == by_id["deer_mule"]["base"] == "deer"
    # 4 published deer seasons: any-deer + white-tailed ones count for the WT filter,
    # any-deer + the mule-deer-only one for Mule.
    assert by_id["deer"]["published_seasons"] == 4
    assert by_id["deer_whitetail"]["published_seasons"] == 3
    assert by_id["deer_mule"]["published_seasons"] == 2


def test_a_sub_filter_combines_with_another_species(client, conn, seeded):
    """T-27's list syntax keeps working: `species=deer_mule,elk` needs no new plumbing."""
    doc = db.q1("SELECT id FROM source_documents", conn=conn)["id"]
    _season(conn, doc, ["1", "2"], species_id="elk", tag_type_id="elk_zone_a", zone="Sawtooth")
    _tag(conn)
    _tag(conn, "elk_zone_a", zone="Sawtooth")

    statuses, body = _units(client, species="deer_mule,elk")
    assert body["species"] == ["deer_mule", "elk"]
    assert body["species_mode"] == "all"  # 2+ species default to "all"
    assert statuses["2"] == "open"  # the mule-deer-only season and the elk season
    # Unit 1's only deer seasons are white-tailed, so `all` closes it even though elk is open.
    assert statuses["1"] == "closed"
    assert body["units"]["1"]["per_species"]["elk"]["status"] == "open"
    assert body["units"]["1"]["per_species"]["deer_mule"]["status"] == "closed"


# ───────── engine-level rules (pure, no DB) ─────────
def _season_row(**kw):
    row = {
        "id": kw.pop("id", 1),
        "license_year": 2026,
        "status": "published",
        "species_id": "deer",
        "tag_type_id": "deer_regular",
        "hunt_class": "general",
        "weapon": "any",
        "start_date": "2026-10-10",
        "end_date": "2026-10-24",
        "restrictions": "[]",
    }
    row.update(kw)
    return row


@pytest.mark.parametrize(
    "season, animal",
    [
        (_season_row(), None),
        (_season_row(restrictions='["whitetail_only"]'), "whitetail"),
        (_season_row(restrictions='["mule_deer_only"]'), "mule_deer"),
        (_season_row(sub_species="whitetail"), "whitetail"),
        (_season_row(tag_type_id="deer_whitetail"), "whitetail"),
        # An elk season has no deer sub-species, whatever else it carries.
        (_season_row(species_id="elk", restrictions='["brow_tined"]'), None),
    ],
)
def test_season_sub_species_reads_code_column_and_tag_type(season, animal):
    assert engine.season_sub_species(season) == animal


def test_base_species_maps_filter_ids_back_to_their_species():
    assert engine.base_species("deer_mule") == "deer"
    assert engine.base_species("deer_whitetail") == "deer"
    assert engine.base_species("elk") == "elk"  # a real id is returned unchanged
    assert set(engine.SUB_SPECIES) == {"deer_whitetail", "deer_mule"}
