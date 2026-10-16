"""T-17b: hunt-area descriptions and unit coverage (`planner/importer/hunt_areas.py`).

Part 1 of the ticket built `static/data/hunt_area_coverage/<year>.json` from the IDFG
GIS service; this half reads the brochure's two-column `CONTROLLED HUNT AREA
DESCRIPTIONS` pages, resolves every hunt area to its GMUs and records the four §8.7
invariants of §12 step 4.

The fixture is the same 14-page excerpt of the real 2026 brochure T-17a uses
(`tests/fixtures/bg2026_controlled.pdf`), which carries the deer, elk, pronghorn and
bear description pages, so the parser runs against the printed layout it must survive:
a gutter, entries that continue across columns and pages, an advert below the last
entry, and `—All` with no space after the dash (elk 60-1).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from planner import db, importer
from planner.importer import ParseContext, hunt_areas
from planner.importer.checks import AreaFacts, HuntAreaCoverage, SectionFacts, run_checks
from planner.importer.controlled import BIG_GAME
from planner.importer.extract import iter_pages
from planner.importer.hunt_areas import (
    AreaDescription,
    Coverage,
    columns_of,
    load_coverage,
    resolve_area,
    species_heading,
    unit_tokens,
)

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "bg2026_controlled.pdf"
UNITS = tuple(u["id"] for u in json.loads(Path("static/data/units_seed.json").read_text()))
COVERAGE_FILE = Path("static/data/hunt_area_coverage/2026.json")


def context(document_id: int = 1) -> ParseContext:
    return ParseContext(
        document_id=document_id,
        kind="big_game",
        license_year=2026,
        year_end=2026,
        units=UNITS,
    )


@pytest.fixture(scope="module")
def pages(tmp_path_factory) -> dict[int, object]:
    cache = tmp_path_factory.mktemp("cache")
    return {p.number: p for p in iter_pages(FIXTURE, cache_dir=cache)}


@pytest.fixture(scope="module")
def described(pages) -> dict[tuple[str, str], AreaDescription]:
    """Every entry the parser reads out of the fixture, keyed (species, area)."""
    ctx = context()
    hunt_areas.begin(ctx)
    state = hunt_areas.state_for(ctx)
    for number in sorted(pages):
        hunt_areas.read_page(pages[number], ctx, BIG_GAME)
    return dict(state.descriptions)


# ───────── unit tokens ─────────


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            "All of Units 1, 2, 3, 4, 4A, 5, 6, 7 and 9.",
            ("1", "2", "3", "4", "4A", "5", "6", "7", "9"),
        ),
        ("All of Unit 43 and 48.", ("43", "48")),
        ("That portion of Unit 1 within Boundary County.", ("1",)),
        ("Private land within Unit 50.", ("50",)),
        ("All of Units 58, 59, and 59A", ("58", "59", "59A")),
        # Highways, refuges and phone numbers are never units.
        ("That portion of Unit 53 east of U.S. Highway 93, see page 97, (208) 467-9278.", ("53",)),
    ],
)
def test_unit_tokens_reads_only_what_a_description_assigns(text, expected) -> None:
    assert unit_tokens(text) == expected


def test_a_boundary_between_two_units_assigns_neither() -> None:
    """2026 elk 10A-1 walks the `Unit 8A/10A boundary`; the hunt is in 10A alone."""
    text = (
        "That portion of Unit 10A west of the Clearwater National Forest boundary, then "
        "north on Dent Bridge Road to the Unit 8A/10A boundary, then along the Unit "
        "8A/10A boundary to the Unit 10A/11A boundary."
    )
    assert unit_tokens(text) == ("10A",)


# ───────── reading the pages ─────────


def test_the_section_is_found_by_its_heading(pages) -> None:
    headings = {
        species_heading(page, BIG_GAME): number
        for number, page in pages.items()
        if species_heading(page, BIG_GAME)
    }
    assert set(headings) == {"deer", "elk", "pronghorn", "black_bear"}


def test_a_full_width_line_belongs_to_no_column(pages) -> None:
    """The heading, its preamble and the page footer run through the gutter."""
    page = next(p for p in pages.values() if species_heading(p, BIG_GAME) == "deer")
    columns = columns_of(page)
    assert len(columns) == 2 and all(columns)
    text = " ".join(w.text for column in columns for w in column)
    assert "CONTROLLED HUNT AREA DESCRIPTIONS" not in text
    assert "idfg.idaho.gov" not in text


def test_every_described_area_is_read_once_per_species(described) -> None:
    counts: dict[str, int] = {}
    for species_id, _ in described:
        counts[species_id] = counts.get(species_id, 0) + 1
    # The excerpt carries deer pp. 32-33 (not 34), elk 60-62, pronghorn 68, bear 74.
    assert counts == {"deer": 80, "elk": 107, "pronghorn": 40, "black_bear": 3}


def test_the_column_split_did_not_interleave_the_two_columns(described) -> None:
    """§12 T-17b acceptance criterion: deer `1-1X` reads as printed."""
    entry = described[("deer", "1-1X")]
    assert entry.text.startswith(
        "That portion of Unit 1 within Boundary County east of the Selkirk Mountains crest"
    )
    # …and the word hyphenated across two lines is rejoined ("Dis- ease").
    assert "Chronic Wasting Disease Transport Rules Apply" in entry.text


def test_an_entry_continues_across_a_page_boundary(described) -> None:
    """2026 elk 24-1 starts at the foot of p. 59 and ends on p. 60."""
    entry = described[("elk", "24-1")]
    assert entry.text.startswith("That portion of Unit 24")
    assert entry.text.rstrip().endswith("to the point of beginning.")


def test_an_entry_printed_without_a_space_after_the_dash_is_read(described) -> None:
    """2026 elk 60-1 prints `Hunt Area 60-1 —All of Units 60, 61, and 62A.`"""
    assert described[("elk", "60-1")].text == "All of Units 60, 61, and 62A."


def test_no_entry_swallows_the_page_footer_or_the_advert_below_it(described) -> None:
    for (species_id, area_id), entry in described.items():
        assert "idfg.idaho.gov" not in entry.text, f"{species_id} {area_id}"
        assert "Seasons & Rules" not in entry.text, f"{species_id} {area_id}"


def test_the_printed_page_is_read_from_the_uncropped_footer(described) -> None:
    """`hunt_areas.description_page` must be turnable-to: 31, not the PDF index."""
    assert described[("deer", "1-1")].printed_page == 31
    assert described[("elk", "1-1")].printed_page == 59


def test_a_description_knows_its_first_sentence_and_its_portion_wording() -> None:
    entry = AreaDescription(
        species_id="deer",
        area_id="38-1",
        page=5,
        printed_page=32,
        parts=["That portion of Unit 38 within the Refuge.", "Contact the Refuge for maps."],
    )
    assert entry.first_sentence == "That portion of Unit 38 within the Refuge."
    assert entry.portion_only is True
    assert AreaDescription("deer", "11", 1, 31, ["All of Unit 11."]).portion_only is False


# ───────── resolving an area to its units ─────────


def coverage(**areas: dict[str, object]) -> Coverage:
    return Coverage(license_year=2026, species={"deer": areas})


def test_a_polygon_resolves_full_and_partial_units() -> None:
    resolved = resolve_area(
        "deer",
        "1-1",
        AreaDescription("deer", "1-1", 1, 31, ["All of Units 1 and 2."]),
        coverage(**{"1-1": {"geometry_year": 2025, "units": {"1": 1.0, "2": 0.44}}}),
    )
    assert resolved.source == "gis" and resolved.geometry_year == 2025
    assert [(u.unit_id, u.coverage, u.coverage_frac) for u in resolved.units] == [
        ("1", "full", 1.0),
        ("2", "partial", 0.44),
    ]
    assert resolved.units[0].coverage_note == "All of Units 1 and 2. (see p. 31)"


def test_a_fraction_below_the_sliver_rule_is_still_a_partial_unit() -> None:
    """§12's 0.02 cut would delete the ticket's own `38-1` criterion (9 km² of unit 38)."""
    resolved = resolve_area(
        "deer",
        "38-1",
        None,
        coverage(**{"38-1": {"geometry_year": 2025, "units": {"38": 0.0017}}}),
    )
    assert [(u.unit_id, u.coverage, u.coverage_frac) for u in resolved.units] == [
        ("38", "partial", 0.0017)
    ]


def test_an_area_with_no_polygon_falls_back_to_its_description() -> None:
    resolved = resolve_area(
        "black_bear",
        "1-1",
        AreaDescription("black_bear", "1-1", 14, 73, ["That portion of Unit 1 north of X."]),
        Coverage(license_year=2026, species={}),
    )
    assert resolved.source == "description" and resolved.geometry_year is None
    assert [(u.unit_id, u.coverage) for u in resolved.units] == [("1", "partial")]


def test_a_plain_all_of_unit_description_resolves_full() -> None:
    resolved = resolve_area(
        "black_bear",
        "32A",
        AreaDescription("black_bear", "32A", 14, 73, ["All of Units 32A and 33."]),
        Coverage(license_year=2026, species={}),
    )
    assert [(u.unit_id, u.coverage) for u in resolved.units] == [("32A", "full"), ("33", "full")]


def test_an_area_with_neither_falls_back_to_the_unit_its_id_starts_with() -> None:
    resolved = resolve_area("deer", "36A-1X", None, Coverage(license_year=2026, species={}))
    assert resolved.source == "area_id"
    assert [(u.unit_id, u.coverage) for u in resolved.units] == [("36A", "partial")]


def test_a_missing_coverage_file_is_an_empty_one(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(hunt_areas, "COVERAGE_DIR", tmp_path)
    empty = load_coverage(1999)
    assert empty.species == {} and empty.has_species("deer") is False


def test_the_committed_2026_file_is_the_one_the_parser_reads() -> None:
    assert hunt_areas.coverage_path(2026) == COVERAGE_FILE.resolve()
    loaded = load_coverage(2026)
    assert loaded.has_species("deer") and loaded.area("deer", "11") == {
        "geometry_year": 2025,
        "units": {"11": 1.0},
    }


# ───────── the four §8.7 checks ─────────


def area(**kw: object) -> AreaFacts:
    base: dict[str, object] = {
        "area_id": "11",
        "page": 31,
        "described_units": ("11",),
        "resolved_units": ("11",),
        "fractions": {"11": 1.0},
        "partial_units": (),
        "source": "gis",
        "geometry_year": 2026,
        "has_description": True,
    }
    return AreaFacts(**{**base, **kw})  # type: ignore[arg-type]


def results(*areas: AreaFacts, has_geometry: bool = True, license_year: int = 2026) -> dict:
    facts = SectionFacts(
        section="hunt_areas_deer",
        hunt_area_coverage=HuntAreaCoverage(
            species_id="deer",
            license_year=license_year,
            has_geometry=has_geometry,
            coverage_file="static/data/hunt_area_coverage/2026.json",
            areas=areas,
        ),
    )
    return {r.name: r for r in run_checks(context(), "hunt_areas_deer", facts)}


def test_a_section_with_no_coverage_facts_records_nothing() -> None:
    assert run_checks(context(), "hunt_areas_deer", SectionFacts(section="hunt_areas_deer")) == []


def test_gis_area_match_fails_on_an_area_with_no_polygon() -> None:
    passing = results(area())["gis_area_match"]
    assert (passing.status, passing.actual) == ("pass", "1 matched")
    failing = results(area(), area(area_id="9-9", source="description"))["gis_area_match"]
    assert failing.status == "fail"
    assert failing.detail == [{"area": "9-9", "page": 31, "resolved_from": "description"}]


def test_gis_area_match_is_skipped_for_a_species_the_file_has_no_polygons_for() -> None:
    """Black bear is outside part 1's MVP build; T-25/T-26 extend it."""
    skipped = results(area(source="description"), has_geometry=False)["gis_area_match"]
    assert skipped.status == "skipped"
    assert "no black_bear polygons" not in skipped.actual  # the species comes from the facts
    assert "no deer polygons" in skipped.actual


def test_unit_coverage_fails_and_shows_both_lists() -> None:
    assert results(area())["unit_coverage"].status == "pass"
    bad = results(
        area(
            area_id="63-1X",
            described_units=("63",),
            resolved_units=("63", "59A"),
            fractions={"63": 0.73, "59A": 0.04},
        )
    )["unit_coverage"]
    assert bad.status == "fail"
    assert bad.detail == [
        {
            "area": "63-1X",
            "page": 31,
            "described": ["63"],
            "gis": ["63", "59A"],
            "only_described": [],
            "only_gis": ["59A"],
            "fractions": {"63": 0.73, "59A": 0.04},
        }
    ]


def test_footnote_consistency_covers_the_three_printed_cases() -> None:
    both_fine = results(
        area(area_id="1-1", includes_other_units=True, resolved_units=("1", "2")),
        area(area_id="1-1X", portion_only=True, partial_units=("1",), resolved_units=("1",)),
        area(area_id="11"),
    )["footnote_consistency"]
    assert both_fine.status == "pass"

    no_footnote = results(area(area_id="36A-3", resolved_units=("36A", "50")))[
        "footnote_consistency"
    ]
    assert no_footnote.status == "fail"
    assert no_footnote.detail[0]["reason"] == (
        "no footnote, but the area is not exactly one full unit"
    )

    footnote_a = results(area(area_id="11", includes_other_units=True))["footnote_consistency"]
    assert footnote_a.status == "fail"

    footnote_b = results(area(area_id="11", portion_only=True))["footnote_consistency"]
    assert footnote_b.status == "fail"


def test_footnote_b_accepts_a_land_status_hunt_the_polygon_cannot_express() -> None:
    """`Private land within Unit 50` is printed `b` while its polygon is the whole GMU."""
    land_status = results(
        area(area_id="50-1X", portion_only=True, portion_in_text=True, resolved_units=("50",))
    )["footnote_consistency"]
    assert land_status.status == "pass"


def test_geometry_year_warns_on_every_area_built_from_older_polygons() -> None:
    current = results(area())["geometry_year"]
    assert current.status == "pass"
    stale = results(area(geometry_year=2025), area(area_id="13", geometry_year=2025))[
        "geometry_year"
    ]
    assert stale.status == "warn"
    assert stale.actual == "2 of 2 areas use 2025 geometry"
    assert [d["area"] for d in stale.detail] == ["11", "13"]


def test_geometry_year_is_skipped_when_nothing_came_from_a_polygon() -> None:
    skipped = results(area(geometry_year=None, source="description"), has_geometry=False)
    assert skipped["geometry_year"].status == "skipped"


# ───────── end to end over the fixture ─────────


@pytest.fixture
def imported(app, tmp_path):
    with app.app_context():
        db.exec(
            "INSERT INTO source_documents (license_year, year_end, kind, title, sha256,"
            " stored_path, status) VALUES (2026, 2026, 'big_game', 'T-17b fixture',"
            " 'hunt-areas-fixture', ?, 'uploaded')",
            str(FIXTURE),
        )
        doc_id = int(db.q1("SELECT id FROM source_documents")["id"])
        result = importer.run_import(doc_id, data_dir=tmp_path)
        assert result.status == "needs_review"
        yield doc_id


def units_of(hunt_number: str) -> list[tuple[str, str, float | None]]:
    season = db.q1("SELECT id FROM seasons WHERE hunt_number = ? LIMIT 1", hunt_number)
    assert season is not None, f"hunt {hunt_number} was not staged"
    return [
        (r["unit_id"], r["coverage"], r["coverage_frac"])
        for r in db.q(
            "SELECT * FROM season_units WHERE season_id = ? ORDER BY coverage_frac DESC, unit_id",
            season["id"],
        )
    ]


def test_every_staged_controlled_season_resolves_to_at_least_one_unit(app, imported) -> None:
    with app.app_context():
        unresolved = db.q(
            "SELECT hunt_number, hunt_area FROM seasons s WHERE s.hunt_number IS NOT NULL"
            " AND NOT EXISTS (SELECT 1 FROM season_units u WHERE u.season_id = s.id)"
        )
        assert unresolved == []


def test_acceptance_deer_1_1_covers_its_nine_units(app, imported) -> None:
    with app.app_context():
        assert units_of("1001") == [
            (u, "full", 1.0) for u in ("1", "2", "3", "4", "4A", "5", "6", "7", "9")
        ]


def test_acceptance_deer_11_is_exactly_unit_11_full(app, imported) -> None:
    with app.app_context():
        assert units_of("1002") == [("11", "full", 1.0)]


def test_acceptance_deer_38_1_is_unit_38_partial(app, imported) -> None:
    with app.app_context():
        assert units_of("1028") == [("38", "partial", 0.0017)]


def test_acceptance_the_description_is_stored_verbatim(app, imported) -> None:
    with app.app_context():
        row = db.q1("SELECT * FROM hunt_areas WHERE species_id='deer' AND area_id='1-1X'")
        assert row["description"].startswith(
            "That portion of Unit 1 within Boundary County east of the Selkirk Mountains crest"
        )
        assert row["geometry_year"] == 2025


def test_an_area_with_no_polygon_is_resolved_from_its_description(app, imported) -> None:
    """Black bear has no GIS polygons in the MVP coverage build (hunt 8501, area 1-1)."""
    with app.app_context():
        assert units_of("8501") == [("1", "partial", None)]
        row = db.q1("SELECT * FROM hunt_areas WHERE species_id='black_bear' AND area_id='1-1'")
        assert row["description"].startswith("That portion of Unit 1")
        assert row["geometry_year"] is None


def test_the_checks_are_recorded_for_every_species_section(app, imported) -> None:
    with app.app_context():
        rows = db.q("SELECT * FROM import_checks WHERE section LIKE 'hunt_areas%'")
        statuses = {(r["section"], r["check_name"]): r["status"] for r in rows}
        for species in ("deer", "elk", "pronghorn", "black_bear"):
            section = f"hunt_areas_{species}"
            for name in (
                "gis_area_match",
                "unit_coverage",
                "footnote_consistency",
                "geometry_year",
            ):
                assert (section, name) in statuses
        assert statuses[("hunt_areas_deer", "gis_area_match")] == "pass"
        assert statuses[("hunt_areas_deer", "unit_coverage")] == "pass"
        assert statuses[("hunt_areas_deer", "footnote_consistency")] == "pass"
        # IDFG publishes the polygons a year behind, so a 2026 import always warns.
        assert statuses[("hunt_areas_deer", "geometry_year")] == "warn"
        assert statuses[("hunt_areas_black_bear", "gis_area_match")] == "skipped"


def test_reimporting_the_document_resolves_the_same_units(app, imported, tmp_path) -> None:
    with app.app_context():
        before = units_of("1001")
        importer.run_import(imported, data_dir=tmp_path)
        assert units_of("1001") == before
        assert (
            db.q1("SELECT COUNT(*) n FROM hunt_areas WHERE description IS NULL")["n"]
            < (db.q1("SELECT COUNT(*) n FROM hunt_areas")["n"])
        )
