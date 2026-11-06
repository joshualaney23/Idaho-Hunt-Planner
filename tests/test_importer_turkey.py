"""T-23: wild turkey general seasons and controlled hunts of the Upland brochure.

Two parsers, so two kinds of test:

* `turkey_general` is its own two-path parser (ruled cells vs. word geometry), tested
  against `tests/fixtures/make_regs_fixture.py::turkey_pages`, which reproduces the
  traps the real 2026 & 2027 brochure prints on its p. 22: a title printed inside each
  table's own ruled box, a full-width banner **between** two tables that names no sex
  (so it is not a table title, yet certainly not a row either), a sideways section tab
  whose zero-width words merge into printed lines, a unit cell wrapped over two lines,
  a Notes cell wrapped over three that carries the hunt's **weapon**, and `See note N`
  pointers into a numbered notes block.

* `controlled_turkey` reuses T-17a's `controlled.py` with the `UPLAND_TURKEY`
  `Brochure` (§12 T-23). The tests here pin the five ways the turkey tables differ from
  the Big Game ones — each a data field on the spec, so the proof that nothing else
  moved is the rest of the suite still passing.

The fixture also prints the row the real brochure does **not**: a unit list that defers
to the p. 23 maps (`See map on page 23`). Those maps are images and are deliberately not
a data source (§12 T-23), so that row must reach review with no units, never a guess —
and `page 23` must not be read as GMU 23.
"""

from __future__ import annotations

import datetime as dt
import json
import tempfile
from pathlib import Path

import pytest

from planner import db
from planner.importer import ParseContext, controlled, parsers_for, run_import, turkey
from planner.importer.checks import run_checks
from planner.importer.extract import iter_pages
from planner.importer.turkey import UPLAND_TURKEY

FIXTURES = Path(__file__).resolve().parent / "fixtures"
PDF = FIXTURES / "upland_turkey_sample.pdf"
UNITS = tuple(u["id"] for u in json.loads(Path("static/data/units_seed.json").read_text()))
#: The page of the fixture that prints the general-season tables, and the `see map` one.
GENERAL_PAGE, SEE_MAP_PAGE, CONTROLLED_PAGE = 2, 3, 4


def context(document_id: int = 1, **kw: object) -> ParseContext:
    base = {
        "document_id": document_id,
        "kind": "upland",
        "license_year": 2026,
        "year_end": 2027,
        "units": UNITS,
    }
    return ParseContext(**{**base, **kw})  # type: ignore[arg-type]


@pytest.fixture(scope="module")
def pages() -> dict[int, object]:
    return {p.number: p for p in iter_pages(PDF)}


@pytest.fixture(scope="module")
def imported() -> dict[str, object]:
    """The fixture imported once into a throwaway DB: rows, units, checks and errors."""
    tmp = Path(tempfile.mkdtemp())
    db.init_db(tmp / "t.db", 2026)
    conn = db.connect(tmp / "t.db")
    with db.use(conn):
        db.exec(
            "INSERT INTO source_documents (id, kind, license_year, year_end, stored_path,"
            " status, sha256) VALUES (1, 'upland', 2026, 2027, ?, 'uploaded', 'turkeyfx')",
            str(PDF),
        )
        result = run_import(1, pdf_path=PDF, data_dir=tmp)
        rows = [dict(r) for r in db.q("SELECT * FROM seasons ORDER BY section, start_date, id")]
        units: dict[int, list[tuple[str, str]]] = {}
        for r in db.q("SELECT season_id, unit_id, coverage FROM season_units"):
            units.setdefault(r["season_id"], []).append((r["unit_id"], r["coverage"]))
        checks = [dict(r) for r in db.q("SELECT * FROM import_checks")]
    return {"result": result, "rows": rows, "units": units, "checks": checks}


def rows_of(imported: dict[str, object], section: str) -> list[dict[str, object]]:
    return [r for r in imported["rows"] if r["section"] == section]  # type: ignore[union-attr]


def check(imported: dict[str, object], section: str, name: str) -> dict[str, object]:
    found = [
        c
        for c in imported["checks"]  # type: ignore[union-attr]
        if c["section"] == section and c["check_name"] == name
    ]
    assert found, f"no {name} check for {section}"
    return found[0]


# ───────── the printed title says what the rows are ─────────


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Spring Turkey - Male or Bearded Turkey", ("general", "male", "spring")),
        ("Spring Turkey - Youth Only - Male or Bearded Turkey", ("youth", "male", "spring")),
        ("Fall Turkey - Either Sex Turkey", ("general", "either", "fall")),
        ("Fall Turkey - Youth Only - Either Sex Turkey", ("youth", "either", "fall")),
    ],
)
def test_title_attributes_map_every_printed_variant(title: str, expected: tuple) -> None:
    assert turkey.title_attributes(title) == expected


@pytest.mark.parametrize(
    "title",
    [
        "",
        "2026 & 2027 Fall Turkey - General Seasons",  # the banner: names no sex
        "WILD TURKEY GENERAL SEASONS 2026 & 2027",
        "Spring Turkey",  # no sex printed
    ],
)
def test_an_unreadable_title_is_a_failure_not_a_default(title: str) -> None:
    """A table whose sex cannot be read fails rather than defaulting it (D3)."""
    assert turkey.title_attributes(title) is None


# ───────── the two extraction paths ─────────


def test_both_paths_read_the_same_rows_through_the_banner_and_the_tab(pages) -> None:
    """The banner and the sideways tab are the two one-sided losses of §8.7 principle 3.

    Path A (ruled cells) reads both tables regardless; path B only does so if the
    full-width banner ends the first table's body and the zero-width tab words are
    filtered out. A mismatch here is exactly the silent loss `two_path_agreement`
    exists to catch.
    """
    page = pages[GENERAL_PAGE]
    rows_a = turkey.rows_from_tables(page)
    rows_b = turkey.rows_from_text(page)
    assert len(rows_a) == len(rows_b) == 4
    ctx = context()
    keys_a = sorted(s.key for row in rows_a for s in turkey.row_seasons(row, ctx))
    keys_b = sorted(s.key for row in rows_b for s in turkey.row_seasons(row, ctx))
    assert keys_a == keys_b


def test_the_sideways_section_tab_is_filtered_out_of_path_b(pages) -> None:
    page = pages[GENERAL_PAGE]
    assert any(w.x1 == w.x0 for w in page.full_words), "fixture prints no rotated tab"
    assert all(w.x1 > w.x0 for w in turkey.upright_words(page))
    # Left in, the tab's letters merge into a printed line and the title reads backwards.
    assert "yekruT" in page.full_text
    assert not any("yekruT" in row.title for row in turkey.rows_from_text(page))


def test_a_cell_wrapped_over_several_lines_stays_in_its_own_row(pages) -> None:
    """The unit cell wraps over two lines, the Notes cell over three."""
    by_dates = {row.dates_text: row for row in turkey.rows_from_text(pages[GENERAL_PAGE])}
    youth = by_dates["April 8 - April 14"]
    assert "4A," in youth.units_text and "10A," in youth.units_text  # both printed lines
    archery = by_dates["November 1 - December 1"]
    assert archery.notes_text == "Archery only, Archery Permit Required"
    assert archery.units_text == "2 (Farragut State Park and WMA only)"


# ───────── rows → seasons ─────────


def test_one_printed_cell_creates_a_season_in_each_covered_year(imported) -> None:
    """The brochure covers 2026 **and** 2027 and prints one date cell for both."""
    rows = rows_of(imported, "turkey_general")
    windows = {(r["start_date"], r["end_date"]) for r in rows}
    assert ("2026-04-08", "2026-04-14") in windows
    assert ("2027-04-08", "2027-04-14") in windows
    assert len(rows) == 2 * len({r["source_text"] for r in rows})


def test_spring_dates_are_the_tables_own_calendar_year_not_license_year_plus_one(
    imported,
) -> None:
    """Turkey tags expire Dec 31 of the year of purchase, so April 2026 is 2026.

    §8.3's Jul→Jun license-year rule would put any pre-July month in `license_year + 1`
    and date the brochure's whole first spring a year late — every unit closed for it.
    """
    springs = sorted(
        r["start_date"] for r in rows_of(imported, "turkey_general") if r["start_date"][5:7] == "04"
    )
    assert springs and springs[0].startswith("2026-04")
    assert all(not s.startswith("2028") for s in springs)


def test_a_fall_window_crossing_new_year_keeps_its_end_in_the_next_year(imported) -> None:
    rows = rows_of(imported, "turkey_general")
    crossing = [r for r in rows if r["start_date"] == "2026-08-30"]
    assert crossing and crossing[0]["end_date"] == "2027-01-31"


def test_a_youth_table_is_its_own_hunt_class_and_carries_youth_only(imported) -> None:
    youth = [r for r in rows_of(imported, "turkey_general") if r["hunt_class"] == "youth"]
    assert youth
    assert all("youth_only" in json.loads(r["restrictions"]) for r in youth)
    assert all(r["sex"] == "male" for r in youth)


def test_archery_only_in_the_notes_column_is_the_hunts_weapon(imported) -> None:
    """`Archery only, Archery Permit Required` is a weapon, not just a note (§12 T-23)."""
    archery = [r for r in rows_of(imported, "turkey_general") if r["weapon"] == "archery"]
    assert archery
    assert all("Archery only" in (r["notes"] or "") for r in archery)
    assert {r["start_date"] for r in archery} == {"2026-11-01", "2027-11-01"}


def test_a_see_note_pointer_leaves_the_condition_unexplained(imported) -> None:
    """`See note 1` points at a note; the condition itself stays unread, so `see_notes`."""
    pointed = [r for r in rows_of(imported, "turkey_general") if "See note 1" in (r["notes"] or "")]
    assert pointed
    youth_with_qualifier = next(r for r in pointed if "except Farragut" in r["source_text"])
    assert "see_notes" in json.loads(youth_with_qualifier["restrictions"])


# ───────── the p. 23 maps are not a data source ─────────


def test_a_row_deferring_to_the_page_23_maps_is_never_guessed(pages) -> None:
    """`See map on page 23` must not read `23` as GMU 23 — a valid-looking wrong unit."""
    rows = turkey.rows_from_tables(pages[SEE_MAP_PAGE])
    assert [r.units_text for r in rows] == ["See map on page 23"]
    seasons = turkey.row_seasons(rows[0], context())
    assert seasons and all(s.see_map for s in seasons)
    assert all(s.units == () and s.unknown_units == () for s in seasons)


def test_a_see_map_row_reaches_review_as_a_failing_row_never_published(imported) -> None:
    result = imported["result"]
    reasons = [(e.section, e.reason) for e in result.errors]  # type: ignore[union-attr]
    assert ("turkey_general", "invalid_row") in reasons
    assert all("no units" in (e.detail or "") for e in result.errors)  # type: ignore[union-attr]
    # It is counted, so `row_validation` fails and publishing stays blocked (§8.4).
    validation = check(imported, "turkey_general", "row_validation")
    assert validation["status"] == "fail"
    assert validation["actual"] != validation["expected"]


def test_a_see_map_row_is_staged_with_zero_confidence_when_it_has_units(pages) -> None:
    """Confidence 0 and `see_notes`, so review sorts it first (§8.6)."""
    row = turkey.rows_from_tables(pages[SEE_MAP_PAGE])[0]
    season = turkey.row_seasons(row, context())[0]
    parsed = turkey.to_parsed(season, SEE_MAP_PAGE, agree=True)
    assert parsed.confidence == 0.0
    assert "see_notes" in parsed.restrictions


# ───────── the §8.7 checks ─────────


def test_the_general_section_reports_its_invariants(imported) -> None:
    statuses = {
        c["check_name"]: c["status"]
        for c in imported["checks"]  # type: ignore[union-attr]
        if c["section"] == "turkey_general"
    }
    assert statuses["two_path_agreement"] == "pass"
    assert statuses["date_token_accounting"] == "pass"
    assert statuses["no_duplicate_keys"] == "pass"
    assert statuses["unit_tokens_valid"] == "pass"
    assert statuses["general_coverage"] == "pass"
    # No golden file is committed until a human signs it page by page (§8.7).
    assert statuses["golden_match"] == "skipped"


def test_coverage_is_measured_against_the_units_the_tables_name(imported) -> None:
    """Turkey is not statewide: the rest of the state is controlled-hunt only (§12 T-23).

    Measuring `general_coverage` against every GMU would fail on a correct parse, which
    is the kind of always-failing check that gets waived wholesale.
    """
    coverage = check(imported, "turkey_general", "general_coverage")
    assert coverage["status"] == "pass"
    assert "unexplained" in str(coverage["actual"])
    assert "0 unexplained" in str(coverage["actual"])


def test_a_general_row_carries_its_source_page_and_a_box_to_highlight(imported) -> None:
    """Path A's cells have no geometry, so the box comes from the path-B twin (§8.6)."""
    rows = [r for r in rows_of(imported, "turkey_general") if r["source_page"] == GENERAL_PAGE]
    assert rows
    assert all(r["source_bbox"] for r in rows)
    assert all(len(json.loads(r["source_bbox"])) == 4 for r in rows)


# ───────── the controlled side: one `Brochure` value ─────────


def test_the_turkey_brochure_prints_hunt_no_without_a_period(pages) -> None:
    """The Big Game marker (`Hunt No.`) does not match these pages at all."""
    page = pages[CONTROLLED_PAGE]
    assert "Hunt No" in page.full_text and "Hunt No." not in page.full_text
    assert UPLAND_TURKEY.hunt_page_re.search(page.full_text)
    assert not controlled.HUNT_PAGE_RE.search(page.full_text)


def test_every_turkey_hunt_number_maps_to_turkey(pages) -> None:
    assert UPLAND_TURKEY.species_of("9001") == "turkey"
    assert UPLAND_TURKEY.species_of("1001") is None  # a Big Game deer number


def test_the_turkey_sex_vocabulary_maps_onto_the_closed_set() -> None:
    """`seasons.sex` has no `bearded` value, so a male-or-bearded hunt is `male`."""
    # The year banner alone names no sex, so it is not a readable title on its own —
    # the title is everything printed above the header row, joined (D3).
    assert not controlled.parse_title("2026 & 2027 Spring Controlled Hunts", UPLAND_TURKEY).matched
    youth = controlled.parse_title(
        "2026 & 2027 Spring Controlled Hunts Youth Only - Male or Bearded Turkey",
        UPLAND_TURKEY,
    )
    assert (youth.sex, youth.hunt_class) == ("male", "controlled")
    assert "youth_only" in youth.restrictions
    either = controlled.parse_title(
        "2026 & 2027 Fall Controlled Hunts Either Sex Turkey", UPLAND_TURKEY
    )
    assert either.sex == "either"


def test_this_brochure_prints_no_footnote_legend(pages) -> None:
    """So `footnote_consistency` must report `skipped`, not fail 11 correct areas."""
    assert not UPLAND_TURKEY.prints_footnotes
    assert UPLAND_TURKEY.legend_meanings == ()


def test_this_brochure_prints_no_tag_total_so_there_is_no_checksum() -> None:
    assert UPLAND_TURKEY.prints_tag_totals is False


def test_the_area_description_is_printed_inside_the_area_cell() -> None:
    """`50-1: All of Units 54 and 55` — one cell, split at the colon (§12 T-23)."""
    assert UPLAND_TURKEY.area_descriptions_in_cell is True
    area, description = controlled.split_area_cell(
        "50-1: All of Unit 50, 51, 58, 59 and 59A", UPLAND_TURKEY
    )
    assert area == "50-1"
    assert description == "All of Unit 50, 51, 58, 59 and 59A"


def test_turkey_seasons_are_dated_by_calendar_year() -> None:
    assert UPLAND_TURKEY.calendar_years is True
    assert UPLAND_TURKEY.both_years is True


def test_both_turkey_parsers_are_registered_for_the_upland_brochure() -> None:
    names = {spec.name for spec in parsers_for("upland")}
    assert "turkey_general" in names
    assert "controlled_turkey" in names
    assert "hunt_areas_turkey" in names


def test_the_general_parser_ignores_rotated_pages() -> None:
    from planner.importer.extract import Page

    assert not turkey.matches(Page(number=1, text="Fall Turkey General Seasons", rotated=True))


def test_a_general_section_with_no_rows_measures_nothing() -> None:
    """`finalize` returns None, so the registry records no check (T-22a's rule)."""
    ctx = context(document_id=999)
    turkey.begin(ctx)
    assert turkey.finalize(ctx) is None
    assert run_checks(ctx, turkey.SECTION, None) == []


def test_add_year_keeps_february_29_inside_february() -> None:
    assert turkey._add_year(dt.date(2028, 2, 29)) == dt.date(2029, 2, 28)
    assert turkey._add_year(dt.date(2026, 4, 8)) == dt.date(2027, 4, 8)
