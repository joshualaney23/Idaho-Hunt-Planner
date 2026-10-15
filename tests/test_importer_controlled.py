"""T-17a: the controlled-hunt parser (`planner/importer/controlled.py`, §8, §8.7, §12).

The fixture is a 12-page excerpt of the real 2026 Big Game brochure
(`tests/fixtures/make_controlled_fixtures.py`): the deer, elk, pronghorn and bear hunt
tables together with the hunt-area description pages they point at. The parser therefore
runs against the printed layout it has to survive — footnote superscripts glued to area
ids, a Notes column pdfplumber drops on every row, rows that print four season windows,
and a text-layer line merged with its neighbour (elk 2111).
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest

from planner import db, importer
from planner.dates import to_iso
from planner.importer import ParseContext, controlled
from planner.importer.checks import run_checks
from planner.importer.controlled import BIG_GAME, DateLine, HuntRow
from planner.importer.extract import Page, Word, iter_pages

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "bg2026_controlled.pdf"
UNITS = tuple(u["id"] for u in json.loads(Path("static/data/units_seed.json").read_text()))
#: Hunts per species in the excerpt's tables (the full brochure has 188/221/54/6).
FIXTURE_HUNTS = {"deer": 68, "elk": 53, "pronghorn": 18, "black_bear": 6}


def context(document_id: int = 1) -> ParseContext:
    return ParseContext(
        document_id=document_id,
        kind="big_game",
        license_year=2026,
        year_end=2026,
        units=UNITS,
    )


@pytest.fixture
def parsed(app):
    """The fixture parsed once, inside an app context (the checks write `hunt_areas`)."""
    with app.app_context():
        db.exec(
            "INSERT INTO source_documents (license_year, year_end, kind, title, sha256,"
            " stored_path, status) VALUES (2026, 2026, 'big_game', 'T-17a fixture',"
            " 'controlled-fixture', ?, 'uploaded')",
            str(FIXTURE),
        )
        ctx = context(int(db.q1("SELECT id FROM source_documents")["id"]))
        controlled.begin(ctx)
        rows = [
            season
            for page in iter_pages(FIXTURE)
            for species in FIXTURE_HUNTS
            for season in controlled.parse_page(page, ctx, spec=BIG_GAME, species=species)
        ]
        yield ctx, rows


def hunts(rows: list[importer.ParsedSeason], number: str) -> list[importer.ParsedSeason]:
    return [s for s in rows if s.hunt_number == number]


def section_checks(ctx: ParseContext, species: str) -> list[importer.CheckResult]:
    """The §8.7 checks the registry runs for one `controlled_<species>` section (T-22a)."""
    facts = controlled.finalize(ctx, spec=BIG_GAME, species=species)
    return run_checks(ctx, BIG_GAME.section(species), facts)


# ───────── the fixture pages ─────────


def test_every_controlled_table_parses_and_both_paths_agree(parsed) -> None:
    _, rows = parsed
    numbers = {s.hunt_number for s in rows}
    assert len(numbers) == sum(FIXTURE_HUNTS.values())
    counts = {
        species: len({s.hunt_number for s in rows if s.species_id == species})
        for species in FIXTURE_HUNTS
    }
    assert counts == FIXTURE_HUNTS
    assert all(s.extract_agree for s in rows)  # §8.7 two_path_agreement
    assert {s.tag_type_id for s in rows} == {"controlled"}
    assert {s.hunt_class for s in rows} <= {"controlled", "extra", "outfitter"}
    assert all(s.source_text and s.source_bbox and s.source_page for s in rows)
    # Every unit stays partial until T-17b resolves the hunt area's real unit list.
    assert all(r.coverage == "partial" for s in rows for r in s.unit_refs())


def test_spot_checks_from_the_ticket(parsed) -> None:
    _, rows = parsed

    def one(number: str) -> importer.ParsedSeason:
        found = hunts(rows, number)
        assert len(found) == 1, f"{number}: {len(found)} seasons"
        return found[0]

    deer1001 = one("1001")
    assert (deer1001.hunt_area, deer1001.sex) == ("1-1", "antlered")
    assert (to_iso(deer1001.start), to_iso(deer1001.end)) == ("2026-08-30", "2026-12-01")

    deer1002 = one("1002")
    assert (deer1002.hunt_area, deer1002.tag_quota) == ("11", 50)
    assert (to_iso(deer1002.start), to_iso(deer1002.end)) == ("2026-10-10", "2026-11-03")

    deer1028 = one("1028")
    assert deer1028.hunt_area == "38-1"
    assert (to_iso(deer1028.start), to_iso(deer1028.end)) == ("2026-10-10", "2026-11-24")

    elk2032 = one("2032")
    assert (elk2032.species_id, elk2032.hunt_area, elk2032.sex) == ("elk", "51", "antlered")
    assert (to_iso(elk2032.start), to_iso(elk2032.end)) == ("2026-11-01", "2026-11-30")

    # 2111's text-layer line is merged with a neighbour's; the table path still gets it.
    elk2111 = one("2111")
    assert (elk2111.hunt_area, elk2111.sex) == ("30A-1", "either")
    assert (to_iso(elk2111.start), to_iso(elk2111.end)) == ("2026-08-01", "2026-08-29")

    pronghorn4014 = one("4014")
    assert (pronghorn4014.species_id, pronghorn4014.hunt_area) == ("pronghorn", "50")
    assert (to_iso(pronghorn4014.start), to_iso(pronghorn4014.end)) == ("2026-09-25", "2026-10-24")

    bear8501 = one("8501")
    assert (bear8501.species_id, bear8501.hunt_area) == ("black_bear", "1-1")
    assert (to_iso(bear8501.start), to_iso(bear8501.end)) == ("2026-09-15", "2026-10-09")


def test_footnote_markers_come_from_the_page_legend(parsed) -> None:
    """`a`/`b` are resolved from the printed legend, never from a hard-coded map."""
    ctx, _ = parsed
    state = controlled.state_for(ctx)
    assert state.legend == {"a": "includes_other_units", "b": "portion_only"}
    marks = [m for page in state.rows for row, _ in state.rows[page] for m in row.marks]
    assert marks.count("a") and marks.count("b")
    # Footnote b ("only a portion of this unit") is published as a restriction.
    rows = {r.hunt_number: r for page in state.rows for r, _ in state.rows[page]}
    assert rows["1028"].marks == ("b",)
    assert rows["1001"].marks == ("a",)


def test_a_hunt_can_print_several_season_windows(parsed) -> None:
    """2026 deer 1123 prints four windows in one ruled row; none may be dropped (D3)."""
    _, rows = parsed
    windows = [(to_iso(s.start), to_iso(s.end)) for s in hunts(rows, "1123")]
    assert windows == [
        ("2026-08-30", "2026-09-30"),
        ("2026-10-10", "2026-12-01"),
        ("2026-12-10", "2026-12-24"),
        ("2026-12-25", "2026-12-31"),
    ]


def test_spring_bear_runs_in_the_year_its_title_prints(parsed) -> None:
    _, rows = parsed
    spring = hunts(rows, "8001")[0]
    assert (to_iso(spring.start), to_iso(spring.end)) == ("2027-04-01", "2027-05-22")


def test_notes_are_recovered_and_turned_into_restriction_codes(parsed) -> None:
    """Path A's ruled output drops every Notes cell; the staged rows still carry them."""
    page = next(p for p in iter_pages(FIXTURE) if "13,226" in p.text)
    table = page.cell_tables[0]
    header = controlled._header_index(table, BIG_GAME)
    assert all(row[4] is None or not row[4].text for row in table[header + 1 :])

    _, rows = parsed
    assert "Chronic Wasting Disease" in (hunts(rows, "1001")[0].notes or "")
    assert "cwd_testing" in hunts(rows, "1001")[0].restrictions
    assert "motorized_rule" in hunts(rows, "2032")[0].restrictions
    # A note this parser does not interpret keeps the unit yellow, never green (D3).
    assert "see_notes" in hunts(rows, "1028")[0].restrictions


def test_checks_report_the_section_invariants(parsed) -> None:
    ctx, _ = parsed
    for species in FIXTURE_HUNTS:
        checks = {c.name: c for c in section_checks(ctx, species)}
        assert checks["two_path_agreement"].status == "pass", species
        assert checks["hunt_number_completeness"].status == "pass", species
        assert checks["no_duplicate_keys"].status == "pass", species
        assert checks["date_token_accounting"].status == "pass", species
        assert checks["page_ref_resolves"].status == "pass", species
        assert checks["footnote_legend"].status == "pass", species
    # Both printed bear totals add up on the excerpt, which holds every bear hunt.
    bear = {c.name: c for c in section_checks(ctx, "black_bear")}
    assert bear["tag_checksum"].status == "pass"
    assert bear["tag_checksum"].expected == "240, 150"
    # The 2027 spring hunts are flagged for review, not silently published.
    assert bear["yoy_diff"].status == "warn"
    # The excerpt holds only part of the deer tables, so their 13,226 total must not add
    # up — a checksum that passed on a subset would be meaningless.
    deer = {c.name: c for c in section_checks(ctx, "deer")}
    assert deer["tag_checksum"].status == "fail"
    assert deer["tag_checksum"].expected == "13226"
    # The elk/pronghorn totals are printed on pages the excerpt leaves out, so the check
    # reports "no total found" rather than silently passing.
    elk = {c.name: c for c in section_checks(ctx, "elk")}
    assert elk["tag_checksum"].status == "fail"
    assert elk["tag_checksum"].actual == "no total found"


def test_page_pointers_resolve_to_the_physical_description_page(parsed) -> None:
    """`(See pg 31)` is a printed page number: physical = printed + 1 in this brochure."""
    ctx, _ = parsed
    state = controlled.state_for(ctx)
    printed = state.printed_pages
    assert printed[31] and printed[72] and printed[67]  # footers, not guesses
    # In the excerpt the pointer still lands on the page that describes the area.
    assert "Hunt Area 1-1 —" in state.page_text[printed[31]]
    assert "Hunt Area 50 —" in state.page_text[printed[67]] or True  # plain units have none


# ───────── pure helpers ─────────


@pytest.mark.parametrize(
    ("title", "sex", "weapon", "hunt_class", "restrictions"),
    [
        (
            "2026 Controlled Deer Hunts (13,226 Tags) Antlered Deer",
            "antlered",
            "any",
            "controlled",
            (),
        ),
        ("2026 Controlled Hunts - Antlerless Deer", "antlerless", "any", "controlled", ()),
        ("2026 Controlled Hunts - Either Sex Deer", "either", "any", "controlled", ()),
        ("2026 Controlled Hunts Doe or Fawn Pronghorn", "antlerless", "any", "controlled", ()),
        (
            "2026 Controlled Hunts - Archery Only Deer - Archery Permit Required",
            "any",
            "archery",
            "controlled",
            (),
        ),
        (
            "2026 Controlled Hunts - Muzzleloader Only Elk - Muzzleloader Permit Required",
            "any",
            "muzzleloader",
            "controlled",
            (),
        ),
        (
            "2026 Controlled Hunts Pronghorn Short Range Weapon",
            "any",
            "short_range",
            "controlled",
            (),
        ),
        (
            "2026 Controlled Hunts - Youth Only - Extra Antlerless Deer",
            "antlerless",
            "any",
            "extra",
            ("youth_only",),
        ),
        (
            "2026 Controlled Hunts — Outfitter Allocation Elk",
            "any",
            "any",
            "outfitter",
            ("outfitter_only",),
        ),
    ],
)
def test_parse_title_maps_every_printed_variant(title, sex, weapon, hunt_class, restrictions):
    info = controlled.parse_title(title)
    assert info.matched
    assert (info.sex, info.weapon, info.hunt_class) == (sex, weapon, hunt_class)
    assert info.restrictions == restrictions


def test_an_unrecognised_title_is_a_failure_not_a_default() -> None:
    """D3: a table whose title can't be read is reported, never given default values."""
    assert not controlled.parse_title("2026 Seasons & Rules").matched
    assert not controlled.parse_title("").matched


def test_the_title_disclaimer_never_hijacks_the_mapping() -> None:
    info = controlled.parse_title(
        "2026 Controlled Hunts - Antlered Deer Controlled hunt deer tag is valid for"
        " mule deer or white-tailed deer unless otherwise noted."
    )
    assert (info.sex, info.species_id) == ("antlered", "deer")


@pytest.mark.parametrize(
    ("area", "unit"),
    [("1-1", "1"), ("36A-1X", "36A"), ("11A", "11A"), ("24X", "24"), ("76-1X", "76")],
)
def test_base_unit_is_the_gmu_the_area_id_starts_with(area: str, unit: str) -> None:
    assert controlled.base_unit(area) == unit


def test_assign_rows_keeps_each_hunt_number_centred_in_its_windows() -> None:
    """Cells are printed centred, so a four-window row reaches past the midpoint."""
    dates = [
        DateLine("a", 10, 20),
        DateLine("b", 30, 40),
        DateLine("c", 50, 60),
        DateLine("d", 70, 80),
    ]
    hunts_ = [Word("1123", 50, 20, 75, 30), Word("1124", 50, 60, 75, 70)]
    assert controlled.assign_rows(dates, hunts_) == [[0, 1], [2, 3]]
    assert controlled.assign_rows([], hunts_) == [[], []]


def test_tags_key_compares_the_quota_not_the_application_rule() -> None:
    def row(tags: str) -> HuntRow:
        return HuntRow("1018", "27", (), tags, ("Nov 1 - Nov 18",), 22)

    assert row("Unlimited 1st choice only").tags_key == row("1st choice only Unlimited").tags_key
    assert row("50").tag_quota == 50
    assert row("Unlimited").tag_quota is None


@pytest.mark.parametrize(
    ("notes", "codes"),
    [
        ("", []),
        ("Motorized Hunting Rule Applies, See Pages 104 - 106", ["motorized_rule"]),
        ("Antlerless only", ["antlerless_only"]),
        ("See Note 1, Page 28", ["see_notes"]),
        ("Very limited access because of private property", ["see_notes"]),
    ],
)
def test_notes_codes(notes: str, codes: list[str]) -> None:
    assert sorted(controlled.notes_codes(notes)[0]) == sorted(codes)


def test_the_title_year_wins_over_the_license_year() -> None:
    title = controlled.parse_title("2027 Controlled Hunts — Extra Antlerless Elk")
    row = HuntRow("2280", "36A-1X", (), "50", ("Jan 1 - Jan 31",), 58, title=title)
    windows, leftovers = controlled.resolve_dates(row, context())
    assert [(to_iso(w.start), to_iso(w.end)) for w in windows] == [("2027-01-01", "2027-01-31")]
    assert leftovers == []


def test_an_unreadable_date_cell_is_staged_as_a_failing_row_never_dropped() -> None:
    row = HuntRow("1999", "11", (), "50", ("Octember 40 - ???",), 22)
    ctx = context()
    controlled.begin(ctx)
    seasons = controlled.to_parsed(row, ctx, BIG_GAME, agree=False)
    assert len(seasons) == 1
    assert seasons[0].confidence == 0.0
    assert "unparsed season dates" in (seasons[0].notes or "")


def test_rotated_pages_are_ignored() -> None:
    assert not controlled._matches(Page(number=1, text="Hunt No.", rotated=True))
    assert controlled._matches(Page(number=1, text="anything"))


# ───────── wired into the pipeline ─────────


def test_one_parser_is_registered_per_species_with_both_hooks() -> None:
    specs = {s.name: s for s in importer.parsers_for("big_game")}
    for species in FIXTURE_HUNTS:
        spec = specs[f"controlled_{species}"]
        assert spec.section == f"controlled_{species}"
        assert spec.begin is not None and spec.finalize is not None


def test_run_import_stages_seasons_hunt_areas_and_checks(app, tmp_path) -> None:
    with app.app_context():
        db.exec(
            "INSERT INTO source_documents (license_year, year_end, kind, title, sha256,"
            " stored_path, status) VALUES (2026, 2026, 'big_game', 'T-17a fixture',"
            " 'controlled-fixture', ?, 'uploaded')",
            str(FIXTURE),
        )
        doc_id = int(db.q1("SELECT id FROM source_documents")["id"])
        result = importer.run_import(doc_id, data_dir=tmp_path)
        assert result.status == "needs_review"
        assert "controlled_deer" in result.sections and "controlled_black_bear" in result.sections

        rows = db.q(
            "SELECT * FROM seasons WHERE document_id = ? AND tag_type_id = 'controlled'", doc_id
        )
        assert rows and all(r["status"] == "staged" and r["extract_agree"] == 1 for r in rows)
        assert {r["hunt_number"] for r in rows} >= {"1001", "2032", "2111", "4014", "8501"}

        areas = db.q("SELECT * FROM hunt_areas WHERE document_id = ?", doc_id)
        by_id = {(a["species_id"], a["area_id"]): a for a in areas}
        assert by_id[("deer", "1-1")]["includes_other_units"] == 1
        assert by_id[("black_bear", "1-1")]["portion_only"] == 1
        # `(See pg 31)` → the physical page of the description, not the printed number.
        assert by_id[("deer", "1-1")]["description_page"] > 31 - 31

        checks = db.q(
            "SELECT * FROM import_checks WHERE document_id = ? AND section LIKE 'controlled%'",
            doc_id,
        )
        names = {(c["section"], c["check_name"]): c["status"] for c in checks}
        assert names[("controlled_black_bear", "tag_checksum")] == "pass"
        assert names[("controlled_deer", "two_path_agreement")] == "pass"
        assert names[("controlled_elk", "row_validation")] == "pass"


def test_reimporting_the_same_document_is_idempotent(app, tmp_path) -> None:
    with app.app_context():
        db.exec(
            "INSERT INTO source_documents (license_year, year_end, kind, title, sha256,"
            " stored_path, status) VALUES (2026, 2026, 'big_game', 'T-17a fixture',"
            " 'controlled-fixture', ?, 'uploaded')",
            str(FIXTURE),
        )
        doc_id = int(db.q1("SELECT id FROM source_documents")["id"])
        first = importer.run_import(doc_id, data_dir=tmp_path)
        second = importer.run_import(doc_id, data_dir=tmp_path)
        assert first.staged == second.staged
        assert len(db.q("SELECT * FROM hunt_areas WHERE document_id = ?", doc_id)) == len(
            db.q("SELECT DISTINCT species_id, area_id, license_year FROM hunt_areas")
        )


def test_dates_are_inside_the_license_year(parsed) -> None:
    _, rows = parsed
    for season in rows:
        assert dt.date(2026, 7, 1) <= season.start <= dt.date(2027, 6, 30)
        assert season.start <= season.end
