"""T-14 (part b): the upland game bird parser and the §5.2 area → unit resolution.

The bird sections of the Upland brochure are **prose**, so both §8.7 extraction paths
read lines rather than cells: path A takes the page's text layer in reading order and
defends itself with content rules, path B reads the body column's word geometry. The
fixture (`tests/fixtures/make_regs_fixture.py::upland_bird_pages`) reproduces the
traps the real 2026 & 2027 brochure prints:

* a county list that wraps mid-clause (`… Kootenai, Latah,` / `Lewis, Nez Perce, …`)
  followed by an `except … game management units` clause, and a sibling
  `Remainder of the state` area;
* the sharp-tailed page's county **map**, printed over the season column: 2.7 pt caps
  labels on the body's own baselines, a 24 pt area number beside `Seasons`, and a
  `Possession Limit` whose dotted leader is pushed onto a line of its own;
* the chukar/gray partridge page, where one printed limit carries both species;
* the pheasant page's nonresident opener (dropped, D1) and its youth season;
* the sage-grouse page, which prints **no** season at all.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from planner import db
from planner.importer import ParseContext, run_import, upland
from planner.importer.extract import iter_pages

FIXTURES = Path(__file__).resolve().parent / "fixtures"
PDF = FIXTURES / "upland_birds_sample.pdf"
UNITS = tuple(u["id"] for u in json.loads(Path("static/data/units_seed.json").read_text()))

#: The counties printed in the real (and fixture) forest grouse AREA 1 definition.
AREA1_COUNTIES = (
    "Benewah",
    "Bonner",
    "Boundary",
    "Clearwater",
    "Kootenai",
    "Latah",
    "Lewis",
    "Nez Perce",
    "Shoshone",
    "Adams",
    "Idaho",
)
AREA1_DEFINITION = (
    "Benewah, Bonner, Boundary, Clearwater, Kootenai, Latah, Lewis, Nez Perce, and"
    " Shoshone counties, and Adams and Idaho counties, except those portions in game"
    " management units 19A, 20A, 22, 23, and 26."
)
#: What the §12 acceptance criterion asks for, computed from the committed overlay
#: independently of the parser (`sum of the counties' GMU fractions`).
EXPECTED_AREA1_FULL = {
    "1", "2", "3", "4", "4A", "5", "6", "7", "8", "8A", "9", "10", "10A", "11", "11A",
    "12", "13", "14", "15", "16", "16A", "17", "18", "19", "20",
}  # fmt: skip
EXPECTED_AREA1_PARTIAL = {"32", "32A"}
EXCEPT_UNITS = {"19A", "20A", "22", "23", "26"}


def context(**kw: object) -> ParseContext:
    base = {
        "document_id": 1,
        "kind": "upland",
        "license_year": 2026,
        "year_end": 2027,
        "units": UNITS,
    }
    return ParseContext(**{**base, **kw})  # type: ignore[arg-type]


@pytest.fixture(scope="module")
def imported() -> dict[str, object]:
    """Import the bird fixture into a throwaway DB and return its rows and checks."""
    tmp = Path(tempfile.mkdtemp())
    conn = db.connect(tmp / "t.db")
    db.init_db(tmp / "t.db", 2026)
    conn = db.connect(tmp / "t.db")
    with db.use(conn):
        db.exec(
            "INSERT INTO source_documents (id, kind, license_year, year_end,"
            " stored_path, status, sha256) VALUES (1, 'upland', 2026, 2027, ?,"
            " 'uploaded', 'uplandbirds')",
            str(PDF),
        )
        result = run_import(1, pdf_path=PDF, data_dir=tmp)
        rows = [dict(r) for r in db.q("SELECT * FROM seasons ORDER BY section, start_date")]
        units: dict[int, list[tuple[str, str, str | None]]] = {}
        for r in db.q("SELECT season_id, unit_id, coverage, coverage_note FROM season_units"):
            units.setdefault(r["season_id"], []).append(
                (r["unit_id"], r["coverage"], r["coverage_note"])
            )
        checks = [dict(r) for r in db.q("SELECT * FROM import_checks")]
    return {"result": result, "rows": rows, "units": units, "checks": checks}


def rows_of(imported: dict[str, object], section: str) -> list[dict[str, object]]:
    return [r for r in imported["rows"] if r["section"] == section]  # type: ignore[union-attr]


# ───────── §5.2 area → units ─────────


def test_county_clause_names_every_printed_county() -> None:
    groups = upland.county_groups(AREA1_DEFINITION)
    assert [c for g in groups for c in g.counties] == list(AREA1_COUNTIES)
    assert not any(g.qualified for g in groups)


def test_a_dropped_county_leaves_a_residue() -> None:
    """The silent failure this guards: both paths agree on the smaller area."""
    assert upland.county_clause_residue(AREA1_DEFINITION) == []
    assert upland.county_clause_residue("Benewah and Nowhere counties.") == ["Nowhere"]


def test_a_sub_county_boundary_marks_the_clause_qualified() -> None:
    text = (
        "Bingham, Bonneville, Clark, and Jefferson counties east of Interstate 15,"
        " Power County south of Interstate 86, Bannock and Teton counties."
    )
    groups = upland.county_groups(text)
    assert [g.qualified for g in groups] == [True, True, False]
    assert groups[0].counties == ("Bingham", "Bonneville", "Clark", "Jefferson")
    assert groups[2].counties == ("Bannock", "Teton")


def test_forest_grouse_area_1_resolves_to_its_counties_minus_the_except_units() -> None:
    area = upland.resolve_definition(AREA1_DEFINITION, area="1", all_units=UNITS)
    assert set(area.removed) == EXCEPT_UNITS
    assert not (area.full_units | area.partial_units) & EXCEPT_UNITS
    assert area.full_units == EXPECTED_AREA1_FULL
    assert area.partial_units == EXPECTED_AREA1_PARTIAL


def test_a_partial_unit_names_the_counties_it_lies_in() -> None:
    area = upland.resolve_definition(AREA1_DEFINITION, area="1", all_units=UNITS)
    notes = {r.unit_id: r.coverage_note for r in area.refs if r.coverage == "partial"}
    assert "Adams" in (notes["32A"] or "")
    assert all(r.coverage_frac is not None for r in area.refs if r.coverage == "partial")


def test_remainder_of_the_state_is_the_complement_and_keeps_partials_partial() -> None:
    area1 = upland.resolve_definition(AREA1_DEFINITION, area="1", all_units=UNITS)
    area2 = upland.resolve_definition(
        "Remainder of the state.", area="2", all_units=UNITS, siblings=[area1]
    )
    assert not area2.full_units & area1.full_units
    assert area1.partial_units <= area2.partial_units
    assert area1.full_units | area2.full_units | area2.partial_units == {
        u for u in UNITS if u != "YNP"
    }


def test_a_closed_remainder_resolves_to_no_units() -> None:
    area = upland.resolve_definition("Remainder of the state: CLOSED.", area="2", all_units=UNITS)
    assert area.closed and area.refs == ()


def test_statewide_is_every_unit_but_yellowstone() -> None:
    area = upland.resolve_definition("", area="statewide", all_units=UNITS)
    assert area.statewide
    assert "YNP" not in area.full_units
    assert len(area.full_units) == len(UNITS) - 1


# ───────── the two extraction paths ─────────


def pages() -> dict[int, object]:
    return {p.number: p for p in iter_pages(PDF)}


def test_both_paths_read_the_same_blocks_through_the_map_labels() -> None:
    page = pages()[5]  # the sharp-tailed page, with the county map over the column
    section = next(s for s in upland.SECTIONS if s.species_id == "sharptail")
    a = upland.blocks_of(upland.lines_text(page), section)
    b = upland.blocks_of(upland.lines_geometry(page), section)
    assert [x.area for x in a] == [x.area for x in b] == ["1", "2"]
    assert a[0].definition_text == b[0].definition_text
    assert a[0].daily == b[0].daily == "2"
    # the dotted leader printed on its own line is re-attached by both paths
    assert a[0].possession == b[0].possession == "6"
    assert "BENEWAH" not in a[0].definition_text


def test_path_a_without_its_caps_rule_would_read_a_map_label_as_a_county(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The content rule is load-bearing: BENEWAH is a map label, Benewah a county."""
    page = pages()[5]
    section = next(s for s in upland.SECTIONS if s.species_id == "sharptail")
    monkeypatch.setattr(upland, "_strip_caps", lambda text: text)
    blocks = upland.blocks_of(upland.lines_text(page), section)
    polluted = upland.resolve_definition(blocks[0].definition_text, area="1", all_units=UNITS)
    clean = upland.resolve_definition(AREA1_DEFINITION, area="1", all_units=UNITS)
    assert "BENEWAH" in blocks[0].definition_text
    # Benewah's units (the Panhandle) are not in the printed sharp-tailed area.
    assert polluted.full_units | polluted.partial_units >= clean.full_units & {"5", "6"}


def test_the_banner_is_only_read_as_a_full_line() -> None:
    """`SAGE-GROUSE SHARP-TAILED GROUSE` (a comparison table) is not a section."""
    section = next(s for s in upland.SECTIONS if s.species_id == "sharptail")
    assert not section.banner.fullmatch("SAGE-GROUSE SHARP-TAILED GROUSE")
    assert section.banner.fullmatch("SHARP-TAILED GROUSE")


# ───────── the import end to end ─────────


def test_every_bird_section_imports_with_both_paths_agreeing(imported) -> None:
    result = imported["result"]
    assert result.status == "needs_review"
    assert result.errors == []
    assert {r["section"] for r in imported["rows"]} == {
        "upland_forest_grouse",
        "upland_chukar",
        "upland_gray_partridge",
        "upland_sharptail",
        "upland_pheasant",
    }
    assert all(r["extract_agree"] == 1 for r in imported["rows"])
    assert all(r["tag_type_id"] == "hunting_license" for r in imported["rows"])


def test_no_check_blocks_publishing(imported) -> None:
    blocking = [c for c in imported["checks"] if c["status"] in ("fail", "warn")]
    assert blocking == []
    names = {(c["section"], c["check_name"]) for c in imported["checks"]}
    assert ("upland_forest_grouse", "two_path_agreement") in names
    assert ("upland_forest_grouse", "county_tokens_resolve") in names
    assert ("upland_forest_grouse", "date_token_accounting") in names
    assert ("upland_forest_grouse", "unit_tokens_valid") in names


def test_both_years_of_the_two_year_brochure_are_staged(imported) -> None:
    grouse = rows_of(imported, "upland_forest_grouse")
    assert {(r["hunt_area"], r["start_date"], r["end_date"]) for r in grouse} == {
        ("AREA 1", "2026-08-30", "2027-01-31"),
        ("AREA 1", "2027-08-30", "2028-01-31"),
        ("AREA 2", "2026-08-30", "2026-12-31"),
        ("AREA 2", "2027-08-30", "2027-12-31"),
    }


def test_the_staged_area_1_rows_carry_the_resolved_units(imported) -> None:
    row = next(r for r in rows_of(imported, "upland_forest_grouse") if r["hunt_area"] == "AREA 1")
    refs = imported["units"][row["id"]]
    assert {u for u, c, _ in refs if c == "full"} == EXPECTED_AREA1_FULL
    assert {u for u, c, _ in refs if c == "partial"} == EXPECTED_AREA1_PARTIAL
    assert all(note for _, coverage, note in refs if coverage == "partial")


def test_a_closed_area_stages_nothing(imported) -> None:
    sharptail = rows_of(imported, "upland_sharptail")
    assert {r["hunt_area"] for r in sharptail} == {"AREA 1"}


def test_a_shared_bag_limit_is_split_between_its_species(imported) -> None:
    chukar = rows_of(imported, "upland_chukar")[0]
    partridge = rows_of(imported, "upland_gray_partridge")[0]
    assert chukar["bag_limit"] == "Daily 8 Chukar; possession 24 Chukar"
    assert partridge["bag_limit"] == "Daily 8 Gray Partridge; possession 24 Gray Partridge"
    assert chukar["start_date"] == partridge["start_date"]


def test_pheasant_carries_the_wma_permit_code_and_a_youth_season(imported) -> None:
    rows = rows_of(imported, "upland_pheasant")
    assert all("wmb_permit_on_wma" in json.loads(r["restrictions"]) for r in rows)
    youth = [r for r in rows if r["hunt_class"] == "youth"]
    assert len(youth) == 1
    assert "youth_only" in json.loads(youth[0]["restrictions"])
    assert youth[0]["start_date"] == "2026-10-08"


def test_the_nonresident_opener_is_dropped_but_accounted_for(imported) -> None:
    """D1: residents only — and a date the parser drops still has to be explained."""
    general = [r for r in rows_of(imported, "upland_pheasant") if r["hunt_class"] == "general"]
    assert [r["start_date"] for r in general] == ["2026-10-15"]  # not the 20th
    check = next(c for c in imported["checks"] if c["check_name"] == "resident_only_windows")
    assert check["status"] == "pass"
    assert "1 paired, 0 orphaned" in check["actual"]


def test_sage_grouse_stages_nothing_and_says_why(imported) -> None:
    assert rows_of(imported, "upland_sage_grouse") == []
    check = next(c for c in imported["checks"] if c["check_name"] == "deferred_season")
    assert check["section"] == "upland_sage_grouse"
    assert check["status"] == "skipped"  # never blocks publishing (§12 T-22b)
    assert "set in July" in (check["expected"] or "")
