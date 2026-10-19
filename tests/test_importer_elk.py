"""T-12: the elk A/B zone parser (rotated calendar pages).

`planner/importer/elk.py` reads the 13 rotated zone pages of the 2026 Big Game
brochure. The fixtures are one-page excerpts of it
(`tests/fixtures/make_elk_fixtures.py`), chosen for the traps they carry:

* **p. 38** — Panhandle + Palouse: a tag row made of two hairline bands (so a band is
  not a row), an entry stacked under another in the same month column, and one date
  cell printed word for word in both the A and the B row;
* **p. 41** — Middle Fork + Salmon: one printed window carrying **two sexes split by
  unit**, and the `Note: 1,551 A Tag Quota Available…` sub-row that sits *between* the
  A and B rows and reads exactly like a note legend;
* **p. 42** — Weiser River + McCall: a `Short range weapons only on Payette River and
  Montour WMAs` note printed *under* a window — a weapon phrase that is not a hunt
  label;
* **p. 43** — Lemhi + Beaverhead: zones with no B tag, a zone title the margin strap
  interleaves with ("Idaho Motorized Hunting Rule Applies Lemhi in Units Zone 29, …"),
  and a B-tag hunt printed directly under a tall A row;
* **p. 46** — Boise River + Smoky-Bennett: `antlerless or spike only` (the phrase a
  shortest-match sex regex reads as plain `antlerless`) and an `Oct 1 - Dec 31` window
  centred on the November column;
* **p. 50** — Diamond Creek and the document-level `ELK Notes:` legend page.

`tests/fixtures/elk_expected/*.json` is hand-verified against the rendered pages (the
page image is transcribed first and compared with the parser, never the other way
round): Panhandle, Middle Fork and Boise River were read line by line off the page.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from planner import db, importer
from planner.dates import to_iso
from planner.importer import ParseContext, elk
from planner.importer.checks import run_checks
from planner.importer.extract import iter_pages

FIXTURES = Path(__file__).resolve().parent / "fixtures"
EXPECTED = FIXTURES / "elk_expected"
UNITS = tuple(u["id"] for u in json.loads(Path("static/data/units_seed.json").read_text()))

#: fixture → (zones printed on it, rows the parser must stage)
PAGES: dict[str, tuple[tuple[str, ...], int]] = {
    "bg2026_elk_p38": (("Panhandle", "Palouse"), 15),
    "bg2026_elk_p41": (("Middle Fork", "Salmon"), 10),
    "bg2026_elk_p42": (("Weiser River", "McCall"), 11),
    "bg2026_elk_p43": (("Lemhi", "Beaverhead"), 8),
    "bg2026_elk_p46": (("Boise River", "Smoky-Bennett"), 6),
    "bg2026_elk_p50": (("Diamond Creek",), 3),
}


@pytest.fixture
def ctx(tmp_path):  # type: ignore[no-untyped-def]
    """A parse context over a seeded database: the zone → unit map lives in `zone_units`."""
    path = tmp_path / "elk.db"
    db.init_db(path, 2026)
    conn = db.connect(path)
    with db.use(conn):
        yield ParseContext(
            document_id=1, kind="big_game", license_year=2026, year_end=2026, units=UNITS
        )


def parse(name: str, ctx: ParseContext) -> list[importer.ParsedSeason]:
    """Parse one fixture page the way `run_import` would."""
    elk.begin(ctx)
    rows: list[importer.ParsedSeason] = []
    for page in iter_pages(FIXTURES / f"{name}.pdf"):
        if elk.matches(page):
            rows += list(elk.parse_page(page, ctx))
    return rows


def as_json(season: importer.ParsedSeason) -> dict[str, object]:
    return {
        "zone": season.zone,
        "tag": season.tag_type_id,
        "weapon": season.weapon,
        "sex": season.sex,
        "start": to_iso(season.start),
        "end": to_iso(season.end),
        "units": [u.unit_id for u in season.unit_refs()],
        "restrictions": list(season.restrictions),
        "tag_quota": season.tag_quota,
    }


# ───────── the fixture pages ─────────


@pytest.mark.parametrize("name", sorted(PAGES))
def test_fixture_page_matches_hand_verified_expectation(name: str, ctx: ParseContext) -> None:
    expected = json.loads((EXPECTED / f"{name}.json").read_text())
    rows = parse(name, ctx)
    assert [as_json(s) for s in rows] == expected
    assert len(rows) == PAGES[name][1]


@pytest.mark.parametrize("name", sorted(PAGES))
def test_rows_carry_the_zone_season_shape(name: str, ctx: ParseContext) -> None:
    zones, count = PAGES[name]
    rows = parse(name, ctx)
    assert len(rows) == count
    assert {s.section for s in rows} == {"elk_zones"}
    assert {s.species_id for s in rows} == {"elk"}
    assert {s.tag_type_id for s in rows} <= {"elk_zone_a", "elk_zone_b"}
    assert {s.hunt_class for s in rows} == {"general"}
    assert {s.zone for s in rows} <= set(zones)
    assert all(s.sex in ("antlered", "antlerless", "either") for s in rows)
    assert all(s.extract_agree for s in rows)  # §8.7 two_path_agreement
    assert all(s.confidence == 1.0 for s in rows)
    assert all(s.source_text and s.source_bbox and s.unit_refs() for s in rows)


@pytest.mark.parametrize("name", sorted(PAGES))
def test_checks_report_the_section_invariants(name: str, ctx: ParseContext) -> None:
    parse(name, ctx)
    checks = {c.name: c for c in run_checks(ctx, elk.SECTION, elk.finalize(ctx))}
    for check in (
        "two_path_agreement",
        "date_token_accounting",
        "unit_tokens_valid",
        "no_duplicate_keys",
        "zone_unit_match",
        "zone_tag_coverage",
        "month_column_agreement",
    ):
        assert checks[check].status == "pass", (check, checks[check].actual, checks[check].detail)


# ───────── what the pages print ─────────


def test_a_window_whose_sexes_are_split_by_unit_becomes_one_row_each(ctx: ParseContext) -> None:
    """Middle Fork prints `Units 20A and 26: antlered only  Unit 27: brow-tined bulls only`.

    Each clause qualifies **only its own units**: the brow-tine restriction is unit 27's
    alone, and publishing it on 20A and 26 would show two units as brow-tined-bull hunts
    the brochure opens to any antlered elk (reported on the 2026 verification sheet).
    """
    rows = [s for s in parse("bg2026_elk_p41", ctx) if s.zone == "Middle Fork"]
    october = [s for s in rows if to_iso(s.start) == "2026-10-01"]
    assert [
        ([u.unit_id for u in s.unit_refs()], s.sex, sorted(s.restrictions)) for s in october
    ] == [
        (["20A", "26"], "antlered", ["antlered_only"]),
        (["27"], "antlered", ["brow_tined"]),
    ]
    # Every Middle Fork window prints the same two clauses, and none of the six rows may
    # carry the other clause's animal limit.
    for season in rows:
        units = {u.unit_id for u in season.unit_refs()}
        if units == {"27"}:
            assert "antlered_only" not in season.restrictions
        else:
            assert "brow_tined" not in season.restrictions
    # One printed window, two rows — and the date token is still consumed exactly once.
    checks = {c.name: c for c in run_checks(ctx, elk.SECTION, elk.finalize(ctx))}
    assert checks["date_token_accounting"].status == "pass"


def test_a_weapon_note_under_a_window_is_not_the_next_hunts_label(ctx: ParseContext) -> None:
    """Weiser River prints `Short range weapons only on Payette River and Montour WMAs`.

    That line sits *under* a window and names two WMAs inside unit 32, so it qualifies
    the hunt above it — the hunt itself stays `Any weapon` and goes to review
    (`see_notes`, §5.1). Read as a label it did two wrong things at once: its own row
    lost the note, and the next hunt printed in the same entry (`Any weapon — antlered
    only  Oct 25 - Nov 3`) was published as a short-range-only season.
    """
    rows = [s for s in parse("bg2026_elk_p42", ctx) if s.zone == "Weiser River"]
    october = next(s for s in rows if to_iso(s.start) == "2026-10-01")
    assert october.weapon == "any"
    assert [u.unit_id for u in october.unit_refs()] == ["32"]
    assert sorted(october.restrictions) == ["antlerless_only", "see_notes"]
    assert "Payette River and Montour WMAs" in (october.notes or "")
    for start in ("2026-10-25", "2026-11-16"):
        following = next(s for s in rows if to_iso(s.start) == start)
        assert following.weapon == "any"
        assert "see_notes" in following.restrictions


def test_the_sex_vocabulary_matches_the_longest_phrase(ctx: ParseContext) -> None:
    """`antlerless or spike only` is not `antlerless` (§12 T-12a: a wrong publishable value)."""
    rows = [s for s in parse("bg2026_elk_p46", ctx) if s.zone == "Boise River"]
    december = next(s for s in rows if to_iso(s.end) == "2026-12-31")
    assert december.sex == "either"
    assert {"antlerless_only", "spike_only"} <= set(december.restrictions)
    assert [u.unit_id for u in december.unit_refs()] == ["38"]


def test_an_entry_restricted_to_part_of_the_zone_keeps_only_those_units(ctx: ParseContext) -> None:
    """`Units 4, 7 and 9 only` narrows the season to the zone's intersection with them."""
    rows = [s for s in parse("bg2026_elk_p38", ctx) if s.zone == "Panhandle"]
    november = next(s for s in rows if to_iso(s.start) == "2026-11-15")
    assert [u.unit_id for u in november.unit_refs()] == ["4", "7", "9"]
    archery = next(s for s in rows if to_iso(s.start) == "2026-08-30")
    zone_units = ["1", "2", "3", "4", "4A", "5", "6", "7", "9"]
    assert [u.unit_id for u in archery.unit_refs()] == zone_units


def test_a_tag_row_may_span_several_hairline_bands(ctx: ParseContext) -> None:
    """Panhandle's A row is two bands; its lowest entry is still an A-tag season."""
    rows = [s for s in parse("bg2026_elk_p38", ctx) if s.zone == "Panhandle"]
    december = next(s for s in rows if to_iso(s.start) == "2026-12-10")
    assert december.tag_type_id == "elk_zone_a"
    assert sum(1 for s in rows if s.tag_type_id == "elk_zone_a") == 6
    assert sum(1 for s in rows if s.tag_type_id == "elk_zone_b") == 3


def test_a_zone_that_offers_one_tag_is_not_a_missing_row(ctx: ParseContext) -> None:
    """`No B Tags in this Zone — See Controlled Hunts` is a statement, not a season."""
    parse("bg2026_elk_p43", ctx)
    facts = elk.finalize(ctx)
    assert facts is not None
    lemhi = next(z for z in facts.extra["zones"] if z.zone == "Lemhi")
    assert lemhi.offered == ("A",)
    assert lemhi.tags == ("A",)
    checks = {c.name: c for c in run_checks(ctx, elk.SECTION, facts)}
    assert checks["zone_tag_coverage"].status == "pass"


def test_the_motorized_rule_preamble_is_a_method_window_not_a_season(ctx: ParseContext) -> None:
    """`Motorized Hunting Rule Applies …, August 30 - November 14` opens no season (§8.7)."""
    parse("bg2026_elk_p43", ctx)
    facts = elk.finalize(ctx)
    assert facts is not None
    windows = facts.extra["method_windows"]
    assert windows and all(w.name == "motorized_rule" and w.windows == 1 for w in windows)
    checks = {c.name: c for c in run_checks(ctx, elk.SECTION, facts)}
    assert checks["method_window_accounting"].status == "pass"
    # …and the token it carries is not counted as a season anywhere.
    assert checks["date_token_accounting"].status == "pass"


def test_printed_tag_quotas_land_on_the_rows_of_that_tag(ctx: ParseContext) -> None:
    rows = parse("bg2026_elk_p41", ctx)
    quotas = {(s.zone, s.tag_type_id, s.tag_quota) for s in rows}
    assert ("Middle Fork", "elk_zone_a", 1551) in quotas
    assert ("Middle Fork", "elk_zone_b", 1636) in quotas
    assert ("Salmon", "elk_zone_b", 2507) in quotas


def test_the_zone_header_units_are_checked_against_zone_units(ctx: ParseContext) -> None:
    parse("bg2026_elk_p38", ctx)
    facts = elk.finalize(ctx)
    assert facts is not None
    panhandle = next(z for z in facts.extra["zones"] if z.zone == "Panhandle")
    assert panhandle.printed == ("1", "2", "3", "4", "4A", "5", "6", "7", "9")
    assert panhandle.printed == panhandle.gis
    checks = {c.name: c for c in run_checks(ctx, elk.SECTION, facts)}
    assert checks["zone_unit_match"].status == "pass"


def test_a_printed_zone_membership_that_differs_from_gis_wins_and_is_reported(
    ctx: ParseContext,
) -> None:
    """§12 T-12: the brochure is the regulation, so it rewrites `zone_units` — loudly."""
    one_unit = "license_year = 2026 AND zone = 'Panhandle' AND unit_id = '9'"
    db.exec(f"DELETE FROM zone_units WHERE {one_unit}")
    parse("bg2026_elk_p38", ctx)
    facts = elk.finalize(ctx)
    assert facts is not None
    corrections = facts.extra["zone_unit_corrections"]
    assert [c["zone"] for c in corrections] == ["Panhandle"]
    assert "9" in corrections[0]["printed"] and "9" not in corrections[0]["gis"]
    assert db.q1(f"SELECT 1 FROM zone_units WHERE {one_unit}")
    checks = {c.name: c for c in run_checks(ctx, elk.SECTION, facts)}
    assert checks["zone_unit_match"].status == "fail"


def test_an_unreadable_sex_phrase_never_publishes_a_guess(ctx: ParseContext) -> None:
    """A phrase outside the vocabulary stages `na` + `see_notes` and fails the section."""
    entry = elk.Entry(tag="A", lines=("Any weapon — two-antlered bulls", "Oct 1 - Oct 31"))
    block = elk.ZoneBlock(page=1, zone="Panhandle", header_units="1, 2", tail="")
    seasons = elk.entry_seasons(entry, block, ["1", "2"], ctx)
    assert [s.sex for s in seasons] == [elk.UNKNOWN_SEX]
    assert "see_notes" in seasons[0].codes
    assert seasons[0].leftovers  # reported, which makes `date_token_accounting` fail
