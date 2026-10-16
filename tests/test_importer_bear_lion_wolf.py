"""T-13: the black bear, mountain lion and wolf general-season parsers.

`planner/importer/bear_lion_wolf.py` reads the three tables of the 2026 Big Game
brochure that share one shape — `Units | <season column> | method windows | Notes` —
and the fixtures are one-page excerpts of that brochure
(`tests/fixtures/make_bear_lion_wolf_fixtures.py`), so these tests run against the
printed layout the parser has to survive:

* **bear p. 72** — the page the old fixed 40 pt side crop corrupted (`60A` read as
  `0A`, `68` as `8`, both valid-looking GMUs, so *both* extraction paths agreed on the
  wrong unit), plus `Dogs Prohibited` / `Dog Training` method columns that are dates
  but not seasons, and a fall-and-spring take season in one cell;
* **lion p. 76** — rows whose printed lines chain together page-wide, so path B has to
  band on the units column's own geometry;
* **wolf p. 81** — a `Statewide` private-land table and recurring windows printed
  inside a `July 2026 - June 2027` table (one token, two license-year seasons).

`tests/fixtures/bear_lion_wolf_expected/*.json` is hand-verified against the rendered
pages (transcribed from page images first, then compared with the parser), never
generated from the implementation it polices.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pdfplumber
import pytest

from planner import importer
from planner.dates import to_iso
from planner.importer import ParseContext, bear_lion_wolf as blw, common
from planner.importer.checks import run_checks
from planner.importer.extract import SIDE_CROP_PT, iter_pages, tab_margins

FIXTURES = Path(__file__).resolve().parent / "fixtures"
EXPECTED = FIXTURES / "bear_lion_wolf_expected"
UNITS = tuple(u["id"] for u in json.loads(Path("static/data/units_seed.json").read_text()))

#: fixture → (section, the rows that page prints)
PAGES: dict[str, tuple[str, int]] = {
    "bg2026_bear_p72": ("black_bear_general", 30),  # 15 unit rows × fall + spring
    "bg2026_lion_p76": ("mountain_lion_general", 18),
    "bg2026_wolf_p81": ("wolf_general", 9),  # 6 printed rows, 3 of them recurring
}


def context(document_id: int = 1) -> ParseContext:
    return ParseContext(
        document_id=document_id,
        kind="big_game",
        license_year=2026,
        year_end=2026,
        units=UNITS,
    )


def parse(name: str, ctx: ParseContext | None = None) -> list[importer.ParsedSeason]:
    """Parse one fixture page with every section this module registers."""
    ctx = ctx or context()
    for spec in blw.SPECS:
        blw._begin(spec.section)(ctx)
    rows: list[importer.ParsedSeason] = []
    for page in iter_pages(FIXTURES / f"{name}.pdf"):
        for spec in blw.SPECS:
            if blw._matches(spec)(page):
                rows += list(blw.parse_page_for(spec, page, ctx))
    return rows


def as_json(season: importer.ParsedSeason) -> dict[str, object]:
    return {
        "units": [r.unit_id for r in season.unit_refs()],
        "start": to_iso(season.start),
        "end": to_iso(season.end),
        "restrictions": list(season.restrictions),
    }


def windows(rows: list[importer.ParsedSeason], units: list[str]) -> list[tuple[str, str]]:
    """The season windows of the row printed for exactly `units`."""
    return [
        (to_iso(s.start), to_iso(s.end))
        for s in rows
        if [r.unit_id for r in s.unit_refs()] == units
    ]


# ───────── the fixture pages ─────────


@pytest.mark.parametrize("name", sorted(PAGES))
def test_fixture_page_matches_hand_verified_expectation(name: str) -> None:
    expected = json.loads((EXPECTED / f"{name}.json").read_text())
    rows = parse(name)
    assert [as_json(s) for s in rows] == expected
    assert len(rows) == PAGES[name][1]


@pytest.mark.parametrize("name", sorted(PAGES))
def test_rows_carry_the_general_season_shape(name: str) -> None:
    section, count = PAGES[name]
    spec = blw.SPEC_BY_SECTION[section]
    rows = parse(name)
    assert len(rows) == count
    assert {s.section for s in rows} == {section}
    assert {s.species_id for s in rows} == {spec.species_id}
    assert {s.tag_type_id for s in rows} == {spec.tag_type_id}
    assert {s.hunt_class for s in rows} == {"general"}
    assert {s.sex for s in rows} == {"any"}
    assert all(s.extract_agree for s in rows)  # §8.7 two_path_agreement
    assert all(s.confidence == 1.0 for s in rows)
    assert all(s.source_text and s.source_bbox for s in rows)
    assert all(s.unit_refs() for s in rows)


@pytest.mark.parametrize("name", sorted(PAGES))
def test_checks_report_the_section_invariants(name: str) -> None:
    section, _ = PAGES[name]
    ctx = context()
    parse(name, ctx)
    checks = {c.name: c for c in run_checks(ctx, section, blw._finalize(section)(ctx))}
    assert checks["two_path_agreement"].status == "pass"
    assert checks["date_token_accounting"].status == "pass"
    assert checks["unit_tokens_valid"].status == "pass"
    assert checks["no_duplicate_keys"].status == "pass"
    # Bear and lion print method columns; wolf has none, so that check reports nothing.
    if blw.SPEC_BY_SECTION[section].method_words:
        assert checks["method_window_accounting"].status == "pass"
    else:
        assert "method_window_accounting" not in checks


# ───────── what each species' table prints ─────────


def test_bear_prints_a_fall_and_a_spring_season_per_row() -> None:
    """One `Take Season` cell holds both windows; each becomes its own row."""
    rows = parse("bg2026_bear_p72")
    labels = [tuple(r.unit_id for r in s.unit_refs()) for s in rows]
    assert len(set(labels)) == 15  # 15 printed unit rows
    assert all(labels.count(label) == 2 for label in labels)
    # Units 29, 30, 30A, 36A, 37, 37A: Aug 30 – Oct 31 and Apr 15 – June 15 (p. 72).
    assert windows(rows, ["29", "30", "30A", "36A", "37", "37A"]) == [
        ("2026-08-30", "2026-10-31"),
        ("2027-04-15", "2027-06-15"),
    ]
    # A spring window that runs into the next license year is never invented: the one
    # printed for unit 33 ends June 30, inside the year.
    assert windows(rows, ["33"]) == [("2026-08-30", "2026-10-31"), ("2027-04-15", "2027-06-30")]


def test_bear_method_windows_are_not_seasons() -> None:
    """`Dogs Prohibited` / `Dog Training` are dates, but they open nothing (§8.7)."""
    ctx = context()
    rows = parse("bg2026_bear_p72", ctx)
    state = blw.state_for(ctx, "black_bear_general")
    assert len(state.method_windows) == 56
    assert {w.name for w in state.method_windows} == {"dogs_prohibited", "dog_training"}
    assert all(w.windows >= 1 and not w.leftovers for w in state.method_windows)
    # Unit 61's dog-training window (July 1 – July 31) is in no season row.
    assert ("2026-07-01", "2026-07-31") not in windows(rows, ["61"])
    # A dogs-prohibited window that overlaps a season is carried as a code instead.
    fall = [s for s in rows if [r.unit_id for r in s.unit_refs()] == ["61"]][0]
    assert "dogs_prohibited" in fall.restrictions


def test_bear_notes_survive_the_merged_notes_cells() -> None:
    rows = parse("bg2026_bear_p72")
    notes = {s.notes for s in rows if s.notes}
    assert "Motorized Hunting Rule Applies in Units 29 and 37A, See Pages 104 - 106" in notes
    assert any(n.startswith("Bait and dogs prohibited in Fremont County") for n in notes)
    # An unexplained condition keeps the row yellow (D3).
    unit33 = [s for s in rows if [r.unit_id for r in s.unit_refs()] == ["33"]]
    assert all("see_notes" in s.restrictions for s in unit33)
    assert all("portion_of_unit" in s.restrictions for s in unit33)


def test_lion_rows_are_banded_on_the_units_column() -> None:
    """Page-wide line grouping chains four printed rows into one band; these don't."""
    rows = parse("bg2026_lion_p76")
    labels = [tuple(r.unit_id for r in s.unit_refs()) for s in rows]
    assert len(labels) == len(set(labels)) == 18
    # Every lion season on this page is the whole license year.
    assert {(to_iso(s.start), to_iso(s.end)) for s in rows} == {("2026-07-01", "2027-06-30")}
    assert windows(rows, ["4", "4A", "6"]) == [("2026-07-01", "2027-06-30")]


def test_wolf_statewide_private_land_row_covers_every_unit() -> None:
    rows = parse("bg2026_wolf_p81")
    private = [s for s in rows if "private_land_only" in s.restrictions]
    assert len(private) == 1
    units = [r.unit_id for r in private[0].unit_refs()]
    assert len(units) == len([u for u in UNITS if u not in common.CLOSED_UNITS])
    assert "YNP" not in units  # Yellowstone is closed to hunting
    assert (to_iso(private[0].start), to_iso(private[0].end)) == ("2026-07-01", "2027-06-30")
    assert "expanded_methods" in private[0].restrictions


def test_wolf_recurring_window_yields_both_license_year_parts() -> None:
    """`Apr 1 - Nov 14` in a `July 2026 - June 2027` table recurs (one token, two rows)."""
    rows = parse("bg2026_wolf_p81")
    # Printed-cell order: the `Apr 1 - Nov 14` token yields both of its occurrences,
    # then the `Nov 15 - Mar 31` token follows.
    assert windows(rows, ["29", "32", "32A", "50", "51"]) == [
        ("2026-07-01", "2026-11-14"),  # the printed window, clipped at the year's start
        ("2027-04-01", "2027-06-30"),  # the same printed window, next occurrence
        ("2026-11-15", "2027-03-31"),
    ]
    clipped = [
        s
        for s in rows
        if [r.unit_id for r in s.unit_refs()] == ["29", "32", "32A", "50", "51"]
        and to_iso(s.start) == "2026-07-01"
    ]
    assert "see_notes" in clipped[0].restrictions  # a clipped window goes to review (D3)


# ───────── the adaptive side crop (§8.2) ─────────


def test_adaptive_crop_keeps_the_units_the_fixed_crop_cut() -> None:
    """The 40 pt crop sliced the bear table's units cell; `tab_margins` doesn't."""
    path = FIXTURES / "bg2026_bear_p72.pdf"
    with pdfplumber.open(path) as pdf:
        page = pdf.pages[0]
        left, right = tab_margins(page)
        fixed = page.crop(
            (SIDE_CROP_PT, 0, float(page.width) - SIDE_CROP_PT, float(page.height))
        ).extract_text()
    assert left == 0.0  # no tab on this page's left edge, so nothing is cropped there
    assert 0.0 < right < SIDE_CROP_PT  # the tab reaches ~35 pt in
    extracted = next(iter(iter_pages(path)))
    assert "60A, 63, 63A" in extracted.full_text
    assert "60A, 63, 63A" not in fixed  # the fixed crop read `0A, 63, 63A`
    # The parser reads the wide view, so no unit token is lost or invented.
    rows = parse("bg2026_bear_p72")
    assert ["60A", "63", "63A"] in [[r.unit_id for r in s.unit_refs()] for s in rows]
    assert all(r.unit_id in UNITS for s in rows for r in s.unit_refs())


def test_the_fixed_crop_view_is_unchanged_for_older_parsers() -> None:
    """`tables`/`words`/`text` stay on the fixed crop; only `full_*` sees more (§8.2)."""
    page = next(iter(iter_pages(FIXTURES / "bg2026_bear_p72.pdf")))
    assert page.wide_text and page.wide_text != page.text
    assert page.full_text == page.wide_text
    assert page.full_tables == page.wide_tables
    assert "60A, 63, 63A" not in page.text


# ───────── pure helpers ─────────


@pytest.mark.parametrize(
    ("title", "section"),
    [
        ("Black Bear Seasons for Fall 2026 and Spring 2027", "black_bear_general"),
        ("2026 Mountain Lion Seasons", "mountain_lion_general"),
        ("Public Land - Wolf Hunting Seasons July 2026 - June 2027", "wolf_general"),
        ("Wolf Trapping Seasons July 2026 - June 2027", None),  # D4: trapping is out of scope
        ("2026 Controlled Black Bear Hunts (300 Tags)", None),  # T-17a owns controlled
        ("Black Bear Hunt Area Descriptions", None),  # not a season table
    ],
)
def test_spec_for_title_picks_the_species_table_only(title: str, section: str | None) -> None:
    spec = blw.spec_for_title(title)
    assert (spec.section if spec else None) == section


def test_covers_license_year_reads_the_title_span() -> None:
    ctx = context()
    assert blw.covers_license_year("Public Land - Wolf Hunting Seasons July 2026 - June 2027", ctx)
    assert not blw.covers_license_year("Black Bear Seasons for Fall 2026 and Spring 2027", ctx)


def test_windows_in_license_year_clips_and_recurs() -> None:
    ctx = context()
    spring = common.DateRange(start=dt.date(2027, 4, 15), end=dt.date(2027, 7, 31), text="")
    # Spring bear runs past June 30: clip to the license year and flag it.
    assert blw.windows_in_license_year(spring, ctx, recurring=False) == [
        (dt.date(2027, 4, 15), dt.date(2027, 6, 30), True)
    ]
    # The wolf table's recurring window applies twice inside one license year.
    wolf = common.DateRange(start=dt.date(2027, 4, 1), end=dt.date(2027, 11, 14), text="")
    assert blw.windows_in_license_year(wolf, ctx, recurring=True) == [
        (dt.date(2026, 7, 1), dt.date(2026, 11, 14), True),
        (dt.date(2027, 4, 1), dt.date(2027, 6, 30), True),
    ]
    # A window wholly inside the year is kept whole and unflagged.
    inside = common.DateRange(start=dt.date(2026, 8, 30), end=dt.date(2026, 10, 31), text="")
    assert blw.windows_in_license_year(inside, ctx, recurring=False) == [
        (dt.date(2026, 8, 30), dt.date(2026, 10, 31), False)
    ]


@pytest.mark.parametrize(
    ("notes", "codes"),
    [
        ("", []),
        ("Motorized Hunting Rule Applies, See Pages 104 - 106", ["motorized_rule"]),
        ("Bait and dogs prohibited", ["bait_prohibited", "dogs_prohibited"]),
        ("Second bear tag may be used", ["second_tag_allowed"]),
        ("Caution: grizzly bears may be encountered", ["grizzly_caution"]),
        ("Expanded hunting methods allowed, See pages 77 - 78", ["expanded_methods"]),
        (
            "General hunting rules apply (Expanded hunting methods not allowed)",
            ["general_methods"],
        ),
        ("See Note 1, Page 76", ["see_notes"]),  # a note this parser does not read
        ("Bait prohibited, Take season limited to on or within 1 mile of private lands",
         ["bait_prohibited", "see_notes"]),  # fmt: skip
    ],
)
def test_notes_codes_explain_or_flag_every_notes_cell(notes: str, codes: list[str]) -> None:
    assert blw.notes_codes(notes) == codes


def test_statewide_units_expand_and_except_takes_back() -> None:
    known = ("1", "2", "3", "4A", "YNP")
    allunits = common.parse_units("Statewide", known)
    assert allunits.all_units and allunits.units == ["1", "2", "3", "4A"]
    some = common.parse_units("All units except 2 and 4A", known)
    assert some.units == ["1", "3"] and some.excluded == ["2", "4A"]
    # Without a unit list the state cannot be expanded — reported, never guessed (D3).
    blind = common.parse_units("Statewide", None)
    assert blind.all_units and blind.units == []
