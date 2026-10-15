"""T-11: the general deer-season parser (`planner/importer/deer.py`, DESIGN.md §8, §8.7).

The fixtures are single-page excerpts of the real 2026 Big Game brochure
(`tests/fixtures/make_deer_fixtures.py`), so these tests run the parser against the
printed layout it has to survive: merged unit cells, vertically centred unit labels,
Notes cells the ruled-table path drops, and tables whose title is a page above.

`tests/fixtures/deer_expected/*.json` is hand-verified against the rendered PDF page
(T-11 acceptance): four pages, row for row.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest

from planner import db, importer
from planner.dates import to_iso
from planner.importer import ParseContext, common, deer
from planner.importer.checks import run_checks
from planner.importer.extract import Page, Word, iter_pages

FIXTURES = Path(__file__).resolve().parent / "fixtures"
EXPECTED = FIXTURES / "deer_expected"
#: Every general deer page of the 2026 brochure, as committed one-page fixtures.
DEER_PAGES = (12, 13, 14, 15, 16, 18, 19, 20, 21)
#: Pages with a hand-verified expectation file.
VERIFIED_PAGES = (12, 16, 19, 21)
UNITS = tuple(u["id"] for u in json.loads((Path("static/data/units_seed.json")).read_text()))


def context(document_id: int = 1) -> ParseContext:
    return ParseContext(
        document_id=document_id,
        kind="big_game",
        license_year=2026,
        year_end=2026,
        units=UNITS,
    )


def parse(pages: tuple[int, ...], ctx: ParseContext | None = None) -> list[importer.ParsedSeason]:
    """Parse one or more fixture pages in document order with a single context."""
    ctx = ctx or context()
    deer.begin(ctx)
    rows: list[importer.ParsedSeason] = []
    for number in pages:
        path = FIXTURES / f"bg2026_deer_p{number:02d}.pdf"
        for page in iter_pages(path):
            rows += list(deer.parse_page(page, ctx))
    return rows


def as_json(season: importer.ParsedSeason) -> dict[str, object]:
    refs = season.unit_refs()
    return {
        "tag_type_id": season.tag_type_id,
        "weapon": season.weapon,
        "sex": season.sex,
        "start": to_iso(season.start),
        "end": to_iso(season.end),
        "units": [r.unit_id for r in refs],
        "partial": [r.unit_id for r in refs if r.coverage == "partial"],
        "restrictions": list(season.restrictions),
        "notes": season.notes,
    }


# ───────── the fixture pages ─────────


@pytest.mark.parametrize("number", VERIFIED_PAGES)
def test_fixture_page_matches_hand_verified_expectation(number: int) -> None:
    expected = json.loads((EXPECTED / f"bg2026_deer_p{number:02d}.json").read_text())
    assert [as_json(s) for s in parse((number,))] == expected


def test_every_general_deer_page_parses_and_both_paths_agree() -> None:
    rows = parse(DEER_PAGES)
    assert len(rows) == 235  # the 2026 brochure's general deer seasons (§12 T-11: ≥ 230)
    assert all(s.extract_agree for s in rows)  # §8.7 two_path_agreement
    assert all(s.confidence == 1.0 for s in rows)
    assert {s.species_id for s in rows} == {"deer"}
    assert {s.hunt_class for s in rows} == {"general"}
    assert {s.tag_type_id for s in rows} == {"deer_regular", "deer_whitetail"}
    assert {s.weapon for s in rows} == {"any", "archery", "muzzleloader", "short_range"}
    assert all(s.source_text and s.source_bbox for s in rows)


def test_spot_checks_from_the_ticket() -> None:
    rows = parse(DEER_PAGES)

    def find(**kw):
        return [
            s
            for s in rows
            if all(
                (sorted(u.unit_id for u in s.unit_refs()) == sorted(v))
                if k == "units"
                else getattr(s, k) == v
                for k, v in kw.items()
            )
        ]

    # Unit 39 Regular any-weapon antlered: Oct 10 – Oct 31 (printed as the row "39, 43").
    unit39 = [
        s
        for s in rows
        if "39" in {u.unit_id for u in s.unit_refs()}
        and s.tag_type_id == "deer_regular"
        and s.weapon == "any"
        and s.sex == "antlered"
    ]
    assert [(to_iso(s.start), to_iso(s.end)) for s in unit39] == [("2026-10-10", "2026-10-31")]

    # Units 1, 2, 3, 4A, 5, 6 antlered Oct 10 – 31 carries whitetail_only.
    north = find(
        units=["1", "2", "3", "4A", "5", "6"],
        tag_type_id="deer_regular",
        weapon="any",
        sex="antlered",
    )
    oct10 = [s for s in north if s.start == dt.date(2026, 10, 10)]
    assert len(oct10) == 1
    assert "whitetail_only" in oct10[0].restrictions

    # Unit 22 antlered carries two_point_only.
    unit22 = find(units=["22"], tag_type_id="deer_regular", sex="antlered")
    assert unit22 and all("two_point_only" in s.restrictions for s in unit22)


def test_checks_report_the_section_invariants() -> None:
    ctx = context()
    parse(DEER_PAGES, ctx)
    checks = {c.name: c for c in run_checks(ctx, deer.SECTION, deer.finalize(ctx))}
    assert checks["two_path_agreement"].status == "pass"
    assert checks["date_token_accounting"].status == "pass"
    assert checks["unit_tokens_valid"].status == "pass"
    # The fixtures hold the general tables only, so the controlled-only units have no
    # confirming page text here and the coverage check fails loudly, as it should.
    coverage = checks["general_coverage"]
    assert coverage.status == "fail"
    assert coverage.detail["missing"] == [  # type: ignore[index]
        "11", "11A", "13", "14", "18", "44", "45", "52", "54", "63A", "68A",
    ]  # fmt: skip


def test_notes_column_is_recovered_from_the_text_path() -> None:
    """Path A drops the vertically merged Notes cells; the published rows keep them."""
    page = next(iter(iter_pages(FIXTURES / "bg2026_deer_p12.pdf")))
    ruled = deer.tables_from_rules(page.tables, None)
    assert all(not row.cells.get("notes") for table in ruled for row in table.rows)
    notes = {s.notes for s in parse((12,)) if s.notes}
    assert "Very limited access because of private property" in notes
    assert "Second deer tag may not be used in Units 10, 10A, and 12" in notes


def test_continuation_page_carries_the_table_title() -> None:
    """A table that starts at the `Unit(s)` header inherits the previous page's title."""
    header = [["Unit(s)", "Antlered", "Antlerless", "Notes"], ["39", "Oct 10 - Oct 31", "", ""]]
    carried = common.parse_title("2026 Regular Deer Tag General Archery Only Seasons")
    tables = deer.tables_from_rules([header], carried)
    assert len(tables) == 1
    assert tables[0].title.tag_type_id == "deer_regular"
    assert tables[0].title.weapon == "archery"
    assert deer.tables_from_rules([header], None) == []  # nothing to inherit: skipped


def test_controlled_tables_are_left_to_t17a() -> None:
    title = "2026 Controlled Deer Hunts (13,226 Tags) Antlered Deer"
    rows = [["Unit(s)", "Antlered", "Antlerless", "Notes"], ["39", "Oct 10 - Oct 31", "", ""]]
    assert deer.tables_from_rules([[[title, "", "", ""], *rows]], None) == []


# ───────── pure helpers ─────────


def word(text: str, x0: float, top: float, width: float = 20, height: float = 10) -> Word:
    return Word(text=text, x0=x0, top=top, x1=x0 + width, bottom=top + height)


def test_split_bands_cuts_rows_at_vertical_whitespace() -> None:
    lines = deer.group_lines(
        [word("Oct", 10, 100), word("(White-tailed", 10, 111), word("Nov", 10, 130)]
    )
    bands = deer.split_bands(lines)
    assert [len(b) for b in bands] == [2, 1]


def test_assign_unit_labels_centres_each_label_in_its_row() -> None:
    # p. 15: the label of the second row sits between two of its own date bands.
    bands = [
        (True, 98.0, 145.0),
        (False, 152.0, 172.0),
        (True, 179.0, 199.0),
        (False, 206.0, 226.0),
    ]
    assert deer.assign_unit_labels(bands) == [0, 2, 2, 2]
    # A label on the first line of its row keeps the bands that follow it.
    assert deer.assign_unit_labels([(True, 10.0, 20.0), (False, 26.0, 36.0)]) == [0, 0]
    assert deer.assign_unit_labels([(False, 10.0, 20.0)]) == [-1]


def test_qualifiers_attach_only_to_the_date_range_they_follow() -> None:
    """The 60-char window never runs into the next range (§12 T-11)."""
    cell = (
        "Oct 10 - Oct 31 (White-tailed deer only) Nov 1 - Nov 14 Nov 15 - Dec 1 (2-point deer only)"
    )
    table = deer.DeerTable(
        title=common.parse_title("2026 Regular Deer Tag General Any Weapon Seasons"),
        rows=[deer.DeerRow(units_text="39", cells={"antlered": cell})],
    )
    seasons = deer.table_seasons(table, context())
    assert [(to_iso(s.start), s.codes) for s in seasons] == [
        ("2026-10-10", ("whitetail_only",)),
        ("2026-11-01", ()),
        ("2026-11-15", ("two_point_only",)),
    ]


def test_none_cells_make_no_season() -> None:
    table = deer.DeerTable(
        title=common.parse_title("2026 Regular Deer Tag General Any Weapon Seasons"),
        rows=[deer.DeerRow(units_text="39", cells={"antlered": "None", "antlerless": ""})],
    )
    assert deer.table_seasons(table, context()) == []


@pytest.mark.parametrize(
    ("notes", "codes", "partial"),
    [
        ("", [], []),
        ("Chronic Wasting Disease Testing Required, See Page 96", ["cwd_testing"], []),
        (
            "Chronic Wasting Disease Transport Rules Apply and Testing is Required, See Page 96",
            ["cwd_testing"],
            [],
        ),
        ("Motorized Hunting Rule Applies, See Pages 104 - 106", ["motorized_rule"], []),
        ("Second deer tag may not be used in Units 10, 10A, and 12", ["second_tag_prohibited"], []),
        ("Very limited access because of private property", ["see_notes"], []),
        ("See Note 3, Page 15", ["see_notes"], []),  # a note this parser does not read
        (
            "In a Portion of Unit 1, Chronic Wasting Disease Transport Rules Apply and"
            " Testing is Required, See Page 96",
            # which portion is unexplained, so the unit stays yellow (D3)
            ["cwd_testing", "portion_of_unit", "see_notes"],
            ["1"],
        ),
    ],
)
def test_notes_codes(notes: str, codes: list[str], partial: list[str]) -> None:
    found, partials = deer.notes_codes(notes, ["1", "2", "39"])
    assert sorted(found) == sorted(codes)
    assert sorted(partials) == sorted(partial)


def test_whitetail_tag_rows_carry_the_display_code_and_sub_species() -> None:
    row = deer.RowSeason(
        tag_type_id="deer_whitetail",
        weapon="any",
        units=("39",),
        sex="antlered",
        start=dt.date(2026, 10, 10),
        end=dt.date(2026, 10, 31),
    )
    season = deer.to_parsed(row, 18, agree=True)
    assert "whitetail_only" in season.restrictions
    assert season.sub_species == "whitetail"
    assert season.tag_type_id == "deer_whitetail"


def test_unknown_unit_token_is_staged_as_a_failing_row_never_dropped() -> None:
    """D3: a token that is not a GMU is kept so validation rejects the whole row."""
    table = deer.DeerTable(
        title=common.parse_title("2026 Regular Deer Tag General Any Weapon Seasons"),
        rows=[deer.DeerRow(units_text="39, 99Z", cells={"antlered": "Oct 10 - Oct 31"})],
    )
    ctx = context()
    season = deer.to_parsed(deer.table_seasons(table, ctx)[0], 13, agree=False)
    assert [r.unit_id for r in season.unit_refs()] == ["39", "99Z"]
    assert season.confidence < 1.0
    problems = importer.validate(
        season, ctx, species={"deer"}, tag_types={"deer_regular"}, units=set(UNITS)
    )
    assert any("unknown units 99Z" in p for p in problems)


def test_rotated_and_empty_pages_are_ignored() -> None:
    ctx = context()
    deer.begin(ctx)
    rotated = Page(number=1, text="2026 Regular Deer Tag Unit(s) Antlered", rotated=True)
    assert not deer.matches(rotated)
    assert list(deer.parse_page(Page(number=2, text="no tables here"), ctx)) == []


# ───────── wired into the pipeline ─────────


def test_parser_is_registered_for_big_game_with_both_hooks() -> None:
    spec = next(s for s in importer.parsers_for("big_game") if s.name == "deer_general")
    assert spec.section == "deer_general"
    assert spec.begin is not None and spec.finalize is not None


def test_run_import_stages_rows_and_records_the_checks(app, tmp_path) -> None:
    with app.app_context():
        db.exec(
            "INSERT INTO source_documents (license_year, year_end, kind, title, sha256,"
            " stored_path, status) VALUES (2026, 2026, 'big_game', 'T-11 fixture',"
            " 'deer-p13', ?, 'uploaded')",
            str(FIXTURES / "bg2026_deer_p13.pdf"),
        )
        doc_id = int(db.q1("SELECT id FROM source_documents")["id"])
        result = importer.run_import(doc_id, data_dir=tmp_path)
        assert result.status == "needs_review"
        assert result.staged == result.parsed > 0
        assert result.sections == ["deer_general"]

        rows = db.q("SELECT * FROM seasons WHERE document_id = ?", doc_id)
        assert all(r["status"] == "staged" and r["extract_agree"] == 1 for r in rows)
        units = db.q(
            "SELECT unit_id FROM season_units WHERE season_id IN"
            " (SELECT id FROM seasons WHERE document_id = ?)",
            doc_id,
        )
        assert {u["unit_id"] for u in units} <= set(UNITS)

        checks = {c["check_name"]: c for c in db.q("SELECT * FROM import_checks")}
        assert checks["two_path_agreement"]["status"] == "pass"
        assert checks["date_token_accounting"]["status"] == "pass"
        assert checks["unit_tokens_valid"]["status"] == "pass"
        assert checks["general_coverage"]["status"] == "fail"  # one page can't cover Idaho
        assert checks["row_validation"]["status"] == "pass"
