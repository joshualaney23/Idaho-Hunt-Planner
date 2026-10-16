"""T-06: `/api/availability`, `/api/meta`, `/api/units` against a real (temp) DB."""

from __future__ import annotations

import json
import random
import statistics
import time
from datetime import date, timedelta

import pytest

from planner import db
from planner.dates import default_range, to_iso
from planner.engine import RESTRICTIONS


def _doc(conn, year: int = 2026, status: str = "published", kind: str = "big_game") -> int:
    cur = db.exec(
        "INSERT INTO source_documents (license_year, year_end, kind, title, status)"
        " VALUES (?, ?, ?, ?, ?)",
        year,
        year,
        kind,
        f"{kind} {year}",
        status,
        conn=conn,
    )
    return cur.lastrowid


def _season(conn, doc: int, units: list[str], **kw) -> int:
    row = {
        "status": "published",
        "species_id": "deer",
        "tag_type_id": "deer_regular",
        "hunt_class": "general",
        "weapon": "any",
        "sex": "either",
        "start_date": "2026-10-10",
        "end_date": "2026-10-24",
        "restrictions": "[]",
        "source_page": 12,
    }
    row.update(kw)
    cols = ", ".join(["document_id", *row])
    marks = ", ".join("?" * (len(row) + 1))
    sid = db.exec(
        f"INSERT INTO seasons ({cols}) VALUES ({marks})", doc, *row.values(), conn=conn
    ).lastrowid
    db.exec_many(
        "INSERT INTO season_units (season_id, unit_id) VALUES (?, ?)",
        [(sid, u) for u in units],
        conn=conn,
    )
    return sid


def _tag(conn, tag_type: str = "deer_regular", year: int = 2026, **kw) -> int:
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


def _get(client, **params):
    resp = client.get("/api/availability", query_string=params)
    return resp.status_code, resp.get_json()


def test_availability_end_to_end(client, conn):
    doc = _doc(conn)
    open_id = _season(conn, doc, ["39", "40"])
    _season(conn, doc, ["41"], restrictions='["whitetail_only"]')
    _season(conn, doc, ["42"], weapon="archery")
    _season(conn, doc, ["43"], status="staged")  # never shown
    _season(conn, _doc(conn, status="needs_review"), ["44"], status="approved")
    _season(conn, doc, ["45"], tag_type_id="deer_whitetail")  # no WT tag held
    tag_id = _tag(conn)

    status, body = _get(client, species="deer", start="2026-10-17", end="2026-10-18")
    assert status == 200
    units = body["units"]
    assert len(units) == 100  # every unit is reported
    assert units["39"]["status"] == units["40"]["status"] == "open"
    assert units["39"]["days_open"] == 2
    assert units["41"] == {**units["41"], "status": "restricted", "reasons": ["whitetail_only"]}
    for uid in ("42", "43", "44", "45", "1"):
        assert units[uid]["status"] == "closed", uid
    s = units["39"]["seasons"][0]
    assert (s["id"], s["tag_ids"], s["document_id"], s["source_page"]) == (
        open_id,
        [tag_id],
        doc,
        12,
    )
    assert body["summary"] == {"open": 2, "restricted": 1, "untagged": 0, "closed": 97}
    assert body["range"] == {"start": "2026-10-17", "end": "2026-10-18", "days": 2}
    assert body["tags"] == "mine"

    _, body = _get(client, start="2026-10-17", end="2026-10-18", include_untagged=1)
    assert body["units"]["45"]["status"] == "untagged"
    assert body["units"]["42"]["reasons"] == ["needs_archery_permit"]
    assert body["units"]["43"]["status"] == "closed"

    db.exec("INSERT INTO my_permits VALUES (2026, 'archery')", conn=conn)
    _, body = _get(client, start="2026-10-17", end="2026-10-18")
    assert body["units"]["42"]["status"] == "open"
    _, body = _get(client, start="2026-10-17", end="2026-10-18", weapon="rifle")
    assert body["units"]["42"]["status"] == "closed"


def test_availability_explicit_tag_scope(client, conn):
    doc = _doc(conn)
    _season(conn, doc, ["39"])
    _season(
        conn, doc, ["11"], tag_type_id="controlled", hunt_class="controlled", hunt_number="1002"
    )
    regular = _tag(conn)
    controlled = _tag(conn, "controlled", hunt_number="1002")
    q = {"start": "2026-10-17", "end": "2026-10-18"}

    _, body = _get(client, **q, tags=str(controlled))
    assert body["tags"] == [controlled]
    assert [u for u, v in body["units"].items() if v["status"] != "closed"] == ["11"]
    _, body = _get(client, **q, tags=f"{regular},{controlled}")
    assert {u for u, v in body["units"].items() if v["status"] == "open"} == {"11", "39"}


def test_availability_filled_and_other_year_tags(client, conn):
    _season(conn, _doc(conn), ["39"])
    _tag(conn, filled=1)
    _tag(conn, year=2025)
    _, body = _get(client, start="2026-10-17", end="2026-10-18")
    assert body["units"]["39"]["status"] == "closed"


def test_availability_defaults(client, monkeypatch):
    status, body = _get(client)
    assert status == 200
    start, end = default_range(date.today())
    assert body["range"]["start"] == to_iso(start)
    assert body["range"]["end"] == to_iso(end)
    assert (body["species"], body["weapon"], body["include_untagged"]) == ("deer", "all", False)


@pytest.mark.parametrize(
    "params, code",
    [
        ({"species": "unicorn"}, "unknown_species"),
        ({"start": "2026-13-01", "end": "2026-12-01"}, "bad_date"),
        ({"start": "10/17/2026", "end": "2026-10-18"}, "bad_date"),
        ({"start": "2026-10-18", "end": "2026-10-17"}, "bad_range"),
        ({"weapon": "bazooka"}, "bad_weapon"),
        ({"include_untagged": "maybe"}, "bad_flag"),
        ({"tags": "1,x"}, "bad_tags"),
    ],
)
def test_availability_errors(client, params, code):
    status, body = _get(client, **params)
    assert status == 400
    assert body["error"]["code"] == code
    assert set(body["error"]) == {"code", "message", "detail"}


def test_meta(client, conn):
    _season(conn, _doc(conn), ["39"])
    _season(
        conn,
        _doc(conn, 2025, status="superseded"),
        ["39"],
        species_id="elk",
        tag_type_id="elk_zone_a",
        status="superseded",
    )
    resp = client.get("/api/meta")
    assert resp.status_code == 200
    m = resp.get_json()
    species = {s["id"]: s for s in m["species"]}
    assert len(species) == 35
    assert species["deer"]["published_seasons"] == 1
    assert species["elk"]["published_seasons"] == 0
    assert m["restrictions"] == json.loads(json.dumps(RESTRICTIONS))
    assert m["reasons"]["no_tag_next_year"]["severity"] == "narrowing"
    assert m["weapons"] == ["rifle", "archery", "muzzleloader", "short_range", "falconry"]
    assert {t["id"] for t in m["tag_types"]} >= {"deer_regular", "controlled", "elk_zone_a"}
    assert next(t for t in m["tag_types"] if t["id"] == "elk_zone_a")["requires_zone"] is True
    start, end = default_range(date.today())
    assert m["default_range"]["start"] == to_iso(start)
    assert m["default_range"]["end"] == to_iso(end)
    assert m["default_range"]["weekend_def"] == "fri_sun"
    assert 2026 in m["license_years"] and date.today().year in m["license_years"]
    assert [d["license_year"] for d in m["documents"]] == [2026]


def test_units(client):
    resp = client.get("/api/units")
    assert resp.status_code == 200
    units = resp.get_json()["units"]
    assert len(units) == 100
    assert set(units[0]) == {"id", "sort_key", "elk_zone", "region", "area_acres"}
    keys = [u["sort_key"] for u in units]
    assert keys == sorted(keys)
    assert next(u for u in units if u["id"] == "39")["elk_zone"]


# ───────── performance (§12 T-06 acceptance) ─────────
PERF_QUERY = {"species": "deer", "start": "2026-10-17", "end": "2026-10-18", "include_untagged": 1}


def build_perf_db(conn, n: int = 5000, *, worst_case: bool = False, seed: int = 6) -> int:
    """`n` published seasons with 1–12 units each; returns the season_units count.

    Default mix: deer/elk/pronghorn/bear over two license years, Aug 30–Dec dates, so the SQL
    filters do real work. ``worst_case``: every season is deer and overlaps the query range.
    """
    rng = random.Random(seed)
    unit_ids = [r["id"] for r in db.q("SELECT id FROM units", conn=conn)]
    docs = {2025: _doc(conn, 2025), 2026: _doc(conn, 2026)}
    weapons = ["any", "any", "archery", "muzzleloader", "short_range"]
    codes = ["[]", "[]", '["whitetail_only"]', '["antlerless_only"]', '["see_notes"]']
    species_tags = {
        "deer": ["deer_regular", "deer_whitetail", "controlled"],
        "elk": ["elk_zone_a", "elk_zone_b", "controlled"],
        "pronghorn": ["pronghorn_general", "controlled"],
        "black_bear": ["black_bear", "controlled"],
    }
    seasons, sus = [], []
    for i in range(1, n + 1):
        if worst_case:
            year, species = 2026, "deer"
            start = date(2026, 10, 1) + timedelta(days=rng.randint(0, 16))
            end = date(2026, 10, 17) + timedelta(days=rng.randint(0, 14))
        else:
            year = rng.choice([2025, 2026])
            species = rng.choices(list(species_tags), weights=[5, 3, 1, 1])[0]
            start = date(year, 8, 30) + timedelta(days=rng.randint(0, 100))
            end = start + timedelta(days=rng.randint(0, 45))
        tt = rng.choice(species_tags[species])
        hunt = str(1000 + i) if tt == "controlled" else None
        zone = rng.choice(["Lolo", "Selway", "McCall"]) if tt.startswith("elk_zone") else None
        seasons.append(
            (i, docs[year], "published", species, tt, zone, hunt,
             "controlled" if hunt else "general", rng.choice(weapons), "either",
             to_iso(start), to_iso(end), rng.choice(codes), 10)
        )  # fmt: skip
        for u in rng.sample(unit_ids, rng.randint(1, 12)):
            sus.append((i, u, rng.choice(["full", "full", "partial"])))
    with db.tx(conn):
        db.exec_many(
            "INSERT INTO seasons (id, document_id, status, species_id, tag_type_id, zone,"
            " hunt_number, hunt_class, weapon, sex, start_date, end_date, restrictions,"
            " source_page) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            seasons,
            conn=conn,
        )
        db.exec_many(
            "INSERT INTO season_units (season_id, unit_id, coverage) VALUES (?, ?, ?)",
            sus,
            conn=conn,
        )
        db.exec("INSERT INTO my_permits VALUES (2026, 'archery')", conn=conn)
        for i in range(1, n + 1, 50):
            _tag(conn, "controlled", hunt_number=str(1000 + i))
        _tag(conn)
        _tag(conn, "deer_whitetail")
    return len(sus)


def time_availability(client, runs: int = 7) -> list[float]:
    """Request + response time in ms (not the test's own JSON parsing of the body)."""
    timings = []
    for _ in range(runs):
        t0 = time.perf_counter()
        resp = client.get("/api/availability", query_string=PERF_QUERY)
        timings.append((time.perf_counter() - t0) * 1000)
        assert resp.status_code == 200
    return timings


@pytest.mark.parametrize("worst_case", [False, True], ids=["realistic", "all-overlap"])
def test_availability_perf_5000_seasons(client, conn, worst_case):
    """§12 T-06: under 150 ms on a 5,000-season DB (realistic mix).

    `all-overlap` is a regression guard, not the acceptance bar: every season is deer and
    overlaps the range, so all 5,000 reach the engine and the response is ≈9 MB of JSON
    (≈100–140 ms on an M-series Mac). Its looser bound catches algorithmic blowups
    without flaking on slower CI runners.
    """
    n_units = build_perf_db(conn, worst_case=worst_case)
    assert db.q1("SELECT COUNT(*) FROM seasons", conn=conn)[0] == 5000
    status, body = _get(client, **PERF_QUERY)  # warm-up (imports, page cache)
    assert status == 200
    kept = {s["id"] for u in body["units"].values() for s in u["seasons"]}
    entries = sum(len(u["seasons"]) for u in body["units"].values())
    assert len(kept) == 5000 if worst_case else len(kept) > 250  # real load reaches the engine

    timings = time_availability(client)
    median = statistics.median(timings)
    print(
        f"\n/api/availability: 5000 seasons / {n_units} season_units in the DB, "
        f"{len(kept)} seasons / {entries} unit entries returned: median {median:.1f} ms, "
        f"min {min(timings):.1f} ms, max {max(timings):.1f} ms"
    )
    limit = 500 if worst_case else 150
    assert median < limit, f"median {median:.1f} ms; {timings}"
