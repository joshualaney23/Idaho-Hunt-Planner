"""T-22a: the §8.7 check registry (`planner/importer/checks/`).

Three layers are covered here:

* the **registry** — glob matching, a check with no facts, a check that crashes;
* every **shared invariant**, on hand-built facts: the passing case and the failing one;
* the **corrupted fixtures** — the real 2026 brochure pages with one defect introduced
  (a dropped table row, superscript filtering turned off, a season silently skipped),
  which is what proves a check would catch a parser regression (D3, §8.7).
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import replace
from pathlib import Path

import pytest

from planner import db
from planner.importer import ParseContext, controlled, deer, extract
from planner.importer.checks import (
    CheckContext,
    CheckResult,
    CheckSpec,
    Coverage,
    DateCell,
    SeasonRef,
    SectionFacts,
    TwoPath,
    check,
    checks_for,
    register_check,
    registered,
    run_checks,
    use_checks,
)
from planner.importer.controlled import BIG_GAME
from planner.importer.extract import iter_pages

FIXTURES = Path(__file__).resolve().parent / "fixtures"
CONTROLLED = FIXTURES / "bg2026_controlled.pdf"
UNITS = tuple(u["id"] for u in json.loads(Path("static/data/units_seed.json").read_text()))
#: Every invariant §8.7 lists that this ticket's registry owns.
EXPECTED_CHECKS = {
    "two_path_agreement",
    "hunt_number_completeness",
    "no_duplicate_keys",
    "date_token_accounting",
    "unit_tokens_valid",
    "tag_checksum",
    "general_coverage",
    "yoy_diff",
    "corrections_applied",
    "page_ref_resolves",
    "footnote_legend",
    "hunt_areas_written",
}


def context(units: tuple[str, ...] = ("1", "2", "3", "YNP")) -> ParseContext:
    return ParseContext(
        document_id=1, kind="big_game", license_year=2026, year_end=2026, units=units
    )


def facts(**kw: object) -> SectionFacts:
    return SectionFacts(section="unit_test", **kw)  # type: ignore[arg-type]


def run(section: str, f: SectionFacts, ctx: ParseContext | None = None) -> dict[str, CheckResult]:
    return {c.name: c for c in run_checks(ctx or context(), section, f)}


# ───────── the registry ─────────


def test_every_invariant_of_8_7_is_registered() -> None:
    assert {c.name for c in registered()} >= EXPECTED_CHECKS


def test_the_glob_decides_which_sections_a_check_claims() -> None:
    with use_checks():
        check("controlled_*")(lambda c: None)  # type: ignore[arg-type,return-value]
        check()(lambda c: None)  # type: ignore[arg-type,return-value]
        names = [(s.name, s.section_glob) for s in checks_for("controlled_deer")]
        assert len(names) == 2
        assert [s.name for s in checks_for("deer_general")] == ["<lambda>"]


def test_a_name_override_becomes_the_recorded_check_name() -> None:
    with use_checks():

        @check("*", name="renamed")
        def whatever(c: CheckContext) -> CheckResult:
            return CheckResult(name="ignored", status="pass")

        (result,) = run_checks(context(), "deer_general", facts())
        assert result.name == "renamed"


def test_a_check_with_no_facts_records_nothing() -> None:
    """A parser opts into an invariant by filling in the field it reads (§12 T-22a)."""
    assert run_checks(context(), "controlled_deer", facts()) == []


def test_a_section_with_no_facts_at_all_records_nothing() -> None:
    assert run_checks(context(), "controlled_deer", None) == []


def test_a_crashing_check_fails_loudly_and_the_others_still_run() -> None:
    with use_checks():

        @check()
        def explodes(c: CheckContext) -> CheckResult:
            raise ValueError("boom")

        register_check(CheckSpec("fine", "*", lambda c: CheckResult(name="fine", status="pass")))
        results = {c.name: c for c in run_checks(context(), "deer_general", facts())}
        assert results["explodes"].status == "fail"
        assert "ValueError: boom" in (results["explodes"].actual or "")
        assert results["fine"].status == "pass"


def test_a_controlled_only_check_does_not_run_for_a_general_section() -> None:
    general = run("deer_general", facts(page_refs=1, page_refs_unresolved=[{"area": "1-1"}]))
    assert "page_ref_resolves" not in general
    controlled_section = run(
        "controlled_deer", facts(page_refs=1, page_refs_unresolved=[{"area": "1-1"}])
    )
    assert controlled_section["page_ref_resolves"].status == "fail"


# ───────── one passing and one failing case per invariant ─────────


def test_two_path_agreement() -> None:
    ok = run("deer_general", facts(two_path=TwoPath(compared=12, disagreeing=0)))
    assert (ok["two_path_agreement"].status, ok["two_path_agreement"].expected) == (
        "pass",
        "12 rows agree",
    )
    bad = run(
        "deer_general",
        facts(
            two_path=TwoPath(
                compared=12,
                disagreeing=1,
                disagreements=[{"hunt": "1001", "tables": ["1-1"], "text": ["1-1a"]}],
                merged_lines=["2111"],
            )
        ),
    )
    assert bad["two_path_agreement"].status == "fail"
    assert bad["two_path_agreement"].actual == "11 agree, 1 text lines merged with a neighbour"
    assert bad["two_path_agreement"].detail["text_line_merged"] == ["2111"]  # type: ignore[index]


def test_hunt_number_completeness() -> None:
    ok = run(
        "controlled_deer",
        facts(hunt_numbers_in_text=["1001", "1002"], hunt_numbers_parsed=["1001", "1002"]),
    )
    assert ok["hunt_number_completeness"].status == "pass"
    bad = run(
        "controlled_deer",
        facts(hunt_numbers_in_text=["1001", "1002"], hunt_numbers_parsed=["1001"]),
    )
    assert bad["hunt_number_completeness"].status == "fail"
    assert bad["hunt_number_completeness"].detail == {"missing": ["1002"]}


def test_no_duplicate_keys() -> None:
    rows = [("1001", "2026-08-30", "2026-12-01"), ("1002", "2026-08-30", "2026-12-01")]
    assert run("controlled_deer", facts(keys=rows))["no_duplicate_keys"].status == "pass"
    bad = run("controlled_deer", facts(keys=rows + rows[:1], key_label="(hunt number, start, end)"))
    assert bad["no_duplicate_keys"].status == "fail"
    assert bad["no_duplicate_keys"].detail == ["1001-2026-08-30-2026-12-01"]
    assert bad["no_duplicate_keys"].expected == (
        "every (hunt number, start, end) unique per document"
    )


def test_date_token_accounting_counts_every_printed_token() -> None:
    cell = DateCell(page=12, texts=("Oct 10 - Oct 31",), seasons=1, label="1, 2, 3")
    assert run("deer_general", facts(date_cells=[cell]))["date_token_accounting"].status == "pass"

    dropped = run("deer_general", facts(date_cells=[replace_cell(cell, seasons=0)]))
    assert dropped["date_token_accounting"].status == "fail"
    assert dropped["date_token_accounting"].detail[0]["tokens"] == 1  # type: ignore[index]

    twice = run("deer_general", facts(date_cells=[replace_cell(cell, seasons=2)]))
    assert twice["date_token_accounting"].status == "fail"  # consumed by more than one season

    leftover = run("deer_general", facts(date_cells=[replace_cell(cell, leftovers=("and Nov 2",))]))
    assert leftover["date_token_accounting"].status == "fail"  # D3: never silently dropped

    empty = DateCell(page=11, texts=(), seasons=0, label="1001")
    assert run("controlled_deer", facts(date_cells=[empty]))["date_token_accounting"].status == (
        "pass"
    )
    must = replace_cell(empty, must_produce=True)
    assert run("controlled_deer", facts(date_cells=[must]))["date_token_accounting"].status == (
        "fail"
    )


def replace_cell(cell: DateCell, **kw: object) -> DateCell:
    return replace(cell, **kw)  # type: ignore[arg-type]


def test_unit_tokens_valid() -> None:
    assert run("deer_general", facts(unknown_units=[]))["unit_tokens_valid"].status == "pass"
    bad = run("deer_general", facts(unknown_units=[(12, "99Z")]))
    assert bad["unit_tokens_valid"].status == "fail"
    assert bad["unit_tokens_valid"].detail == [{"page": 12, "token": "99Z"}]


def test_tag_checksum() -> None:
    total = {"title": "Black Bear", "header": 240, "parsed": 240, "unlimited": 0, "rows": 4}
    assert run("controlled_black_bear", facts(tag_totals=[total]))["tag_checksum"].status == "pass"
    bad = run("controlled_black_bear", facts(tag_totals=[{**total, "parsed": 230}]))
    assert (bad["tag_checksum"].status, bad["tag_checksum"].expected) == ("fail", "240")
    none = run("controlled_black_bear", facts(tag_totals=[]))
    assert (none["tag_checksum"].status, none["tag_checksum"].actual) == ("fail", "no total found")


def test_general_coverage_accepts_a_unit_the_text_confirms_as_controlled_only() -> None:
    cov = Coverage(
        covered=frozenset({"1", "2"}),
        all_units=("1", "2", "3", "YNP"),
        excluded=frozenset({"YNP"}),
        texts=("1081 3 Unlimited Oct 10 - Oct 31",),
    )
    ok = run("deer_general", facts(coverage=cov))["general_coverage"]
    assert ok.status == "pass"
    assert ok.detail == {"controlled_only": ["3"], "missing": []}
    assert ok.expected == "3 units covered or controlled-only"

    bad = run("deer_general", facts(coverage=Coverage(**{**cov.__dict__, "texts": ()})))
    assert bad["general_coverage"].status == "fail"
    assert bad["general_coverage"].detail == {"controlled_only": [], "missing": ["3"]}  # type: ignore[comparison-overlap]


def test_yoy_diff_only_ever_warns() -> None:
    inside = SeasonRef(label="1001", title="Deer", start=dt.date(2026, 10, 10))
    outside = SeasonRef(label="1002", title="Deer", start=dt.date(2025, 10, 10))
    assert run("controlled_deer", facts(first_windows=[inside]))["yoy_diff"].status == "pass"
    warned = run("controlled_deer", facts(first_windows=[inside, outside]))["yoy_diff"]
    assert warned.status == "warn"
    assert warned.detail == [{"label": "1002", "title": "Deer", "start": "2025-10-10"}]


def test_corrections_applied_reports_the_pages_that_need_review() -> None:
    clean = run("controlled_deer", facts(corrections_pages=[]))["corrections_applied"]
    assert (clean.status, clean.actual) == ("pass", "no corrections page in this document")
    found = run("controlled_deer", facts(corrections_pages=[3]))["corrections_applied"]
    assert found.detail == [3] and "need review" in (found.actual or "")


def test_page_ref_resolves_and_footnote_legend() -> None:
    ok = run("controlled_deer", facts(page_refs=2, unknown_marks=[]))
    assert ok["page_ref_resolves"].status == "pass"
    assert ok["footnote_legend"].status == "pass"
    bad = run(
        "controlled_deer",
        facts(
            page_refs=2,
            page_refs_unresolved=[{"area": "1-1", "printed_page": 31, "physical": None}],
            unknown_marks=[{"hunt": "1001", "mark": "z"}],
            unreadable_titles=[{"page": 11, "text": "???"}],
        ),
    )
    assert bad["page_ref_resolves"].status == "fail"
    assert bad["page_ref_resolves"].actual == "1 resolve"
    assert bad["footnote_legend"].status == "fail"


def test_hunt_areas_written_reports_what_the_section_wrote() -> None:
    written = run("controlled_deer", facts(hunt_areas={"distinct": 3, "areas": 3, "attached": 2}))[
        "hunt_areas_written"
    ]
    assert (written.status, written.expected) == ("pass", "3 distinct hunt areas")
    assert written.actual == "3 rows in hunt_areas, 2 seasons with an attached note"


# ───────── corrupted fixtures: the real pages with one defect ─────────


def deer_page(number: int = 12) -> extract.Page:
    return next(iter(iter_pages(FIXTURES / f"bg2026_deer_p{number}.pdf")))


def corrupt_cells(page: extract.Page, column: int, old: str, new: str) -> extract.Page:
    """The same page with one ruled-table column rewritten (path A only)."""
    tables = [
        [
            [
                (cell.replace(old, new) if i == column and isinstance(cell, str) else cell)
                for i, cell in enumerate(row)
            ]
            for row in table
        ]
        for table in page.tables
    ]
    return replace(page, tables=tables)


def deer_facts(pages: list[extract.Page]) -> SectionFacts | None:
    ctx = context(UNITS)
    deer.begin(ctx)
    for page in pages:
        list(deer.parse_page(page, ctx))
    return deer.finalize(ctx)


def deer_checks(pages: list[extract.Page]) -> dict[str, CheckResult]:
    return {c.name: c for c in run_checks(context(UNITS), deer.SECTION, deer_facts(pages))}


def test_the_clean_deer_page_passes_every_check_it_can() -> None:
    results = deer_checks([deer_page()])
    assert results["two_path_agreement"].status == "pass"
    assert results["date_token_accounting"].status == "pass"
    assert results["unit_tokens_valid"].status == "pass"


def test_an_unknown_unit_token_fails_unit_tokens_valid() -> None:
    page = corrupt_cells(deer_page(), 0, "1, 2, 3, 4A, 5, 6", "1, 2, 3, 4A, 5, 6, 99Z")
    results = deer_checks([page])
    assert results["unit_tokens_valid"].status == "fail"
    assert results["unit_tokens_valid"].detail[0]["token"] == "99Z"  # type: ignore[index]


def test_a_silently_skipped_row_fails_date_token_accounting() -> None:
    """A unit cell the table path loses: the row's dates are then consumed by no season
    at all, which has to surface as a failure rather than as a shorter table (D3)."""
    page = corrupt_cells(deer_page(), 0, "1, 2, 3, 4A, 5, 6", "")
    results = deer_checks([page])
    assert results["date_token_accounting"].status == "fail"
    dropped = [d for d in results["date_token_accounting"].detail if d["tokens"]]  # type: ignore[union-attr]
    assert dropped and all(d["seasons"] == 0 for d in dropped)


def test_unexplained_qualifier_text_fails_date_token_accounting() -> None:
    page = corrupt_cells(deer_page(), 1, "Oct 10 - Oct 31", "Oct 10 - Oct 31 (every third Tuesday)")
    results = deer_checks([page])
    assert results["date_token_accounting"].status == "fail"
    assert "every third Tuesday" in str(results["date_token_accounting"].detail)


def test_a_date_only_one_path_reads_fails_two_path_agreement() -> None:
    page = corrupt_cells(deer_page(), 1, "Oct 10 - Oct 31", "Oct 11 - Oct 31")
    results = deer_checks([page])
    assert results["two_path_agreement"].status == "fail"
    assert results["two_path_agreement"].detail["disagreements"]  # type: ignore[index]


# ───────── corrupted fixtures: the controlled tables ─────────


@pytest.fixture
def controlled_state(app):
    """The controlled fixture parsed once into parser state, inside an app context."""
    with app.app_context():
        db.exec(
            "INSERT INTO source_documents (license_year, year_end, kind, title, sha256,"
            " stored_path, status) VALUES (2026, 2026, 'big_game', 'T-22a fixture',"
            " 'checks-fixture', ?, 'uploaded')",
            str(CONTROLLED),
        )
        ctx = context(UNITS)
        controlled.begin(ctx)
        for page in iter_pages(CONTROLLED):
            for species in ("deer", "elk", "pronghorn", "black_bear"):
                list(controlled.parse_page(page, ctx, spec=BIG_GAME, species=species))
        yield ctx


def controlled_checks(ctx: ParseContext, species: str) -> dict[str, CheckResult]:
    facts_ = controlled.finalize(ctx, spec=BIG_GAME, species=species)
    return {c.name: c for c in run_checks(ctx, BIG_GAME.section(species), facts_)}


def test_dropping_one_table_row_fails_completeness_and_the_checksum(controlled_state) -> None:
    """The §8.7 example: a row the table path loses must never pass unnoticed."""
    ctx = controlled_state
    before = controlled_checks(ctx, "black_bear")
    assert before["hunt_number_completeness"].status == "pass"
    assert before["tag_checksum"].status == "pass"

    state = controlled.state_for(ctx)
    dropped = None
    for page, pairs in state.rows.items():
        for pair in list(pairs):
            if pair[0].hunt_number.startswith("8") and pair[0].tag_quota:
                dropped = pair[0].hunt_number
                state.rows[page] = [p for p in pairs if p is not pair]
                break
        if dropped:
            break
    assert dropped, "the fixture must hold at least one bear hunt with a tag quota"

    after = controlled_checks(ctx, "black_bear")
    assert after["hunt_number_completeness"].status == "fail"
    assert after["hunt_number_completeness"].detail == {"missing": [dropped]}
    assert after["tag_checksum"].status == "fail"


def test_without_superscript_filtering_the_two_paths_disagree(app, monkeypatch) -> None:
    """Path A reads the ruled tables from the page *without* its footnote markers. Turn
    that off and the `a` printed on hunt 1001's area is glued to it, so the two paths
    read different areas and the row must be flagged (§8.7 principle 3)."""
    monkeypatch.setattr(extract, "without_superscripts", lambda page, size=None: page)
    with app.app_context():
        db.exec(
            "INSERT INTO source_documents (license_year, year_end, kind, title, sha256,"
            " stored_path, status) VALUES (2026, 2026, 'big_game', 'T-22a fixture',"
            " 'checks-superscripts', ?, 'uploaded')",
            str(CONTROLLED),
        )
        doc_id = int(db.q1("SELECT id FROM source_documents")["id"])
        ctx = replace(context(UNITS), document_id=doc_id)
        controlled.begin(ctx)
        for page in iter_pages(CONTROLLED):
            list(controlled.parse_page(page, ctx, spec=BIG_GAME, species="deer"))
        results = controlled_checks(ctx, "deer")
    assert results["two_path_agreement"].status == "fail"
    hunts = {str(d["hunt"]) for d in results["two_path_agreement"].detail["disagreements"]}  # type: ignore[index]
    assert "1001" in hunts


def test_a_repeated_hunt_row_fails_no_duplicate_keys(controlled_state) -> None:
    ctx = controlled_state
    assert controlled_checks(ctx, "pronghorn")["no_duplicate_keys"].status == "pass"
    state = controlled.state_for(ctx)
    page, pairs = next(
        (p, rows) for p, rows in state.rows.items() if any(r.hunt_number[0] == "4" for r, _ in rows)
    )
    twin = next(pair for pair in pairs if pair[0].hunt_number[0] == "4")
    state.rows[page] = list(pairs) + [twin]
    duplicated = controlled_checks(ctx, "pronghorn")["no_duplicate_keys"]
    assert duplicated.status == "fail"
    assert duplicated.detail and duplicated.detail[0].startswith(twin[0].hunt_number)  # type: ignore[index]


def test_a_page_pointer_to_nowhere_fails_page_ref_resolves(controlled_state) -> None:
    ctx = controlled_state
    assert controlled_checks(ctx, "deer")["page_ref_resolves"].status == "pass"
    state = controlled.state_for(ctx)
    for page, pairs in state.rows.items():
        state.rows[page] = [
            (replace(row, description_page=999) if row.description_page else row, agree)
            for row, agree in pairs
        ]
    broken = controlled_checks(ctx, "deer")["page_ref_resolves"]
    assert broken.status == "fail"
    assert broken.detail and broken.detail[0]["printed_page"] == 999  # type: ignore[index]


def test_a_corrections_page_is_always_reported(controlled_state) -> None:
    ctx = controlled_state
    assert "no corrections page" in (
        controlled_checks(ctx, "deer")["corrections_applied"].actual or ""
    )
    state = controlled.state_for(ctx)
    state.page_text[99] = "Corrections\nHunt 1001 is antlerless, not antlered."
    reported = controlled_checks(ctx, "deer")["corrections_applied"]
    assert reported.detail == [99] and "need review" in (reported.actual or "")


def test_the_fixture_warns_on_a_season_outside_the_license_year(controlled_state) -> None:
    """The bear tables print a spring season dated in the next calendar year (§8.7 warn)."""
    warned = controlled_checks(controlled_state, "black_bear")["yoy_diff"]
    assert warned.status == "warn"
    assert warned.detail and all(d["start"][:4] != "2026" for d in warned.detail)  # type: ignore[index]


def test_a_marker_the_legend_does_not_explain_fails_footnote_legend(app, monkeypatch) -> None:
    """A superscript the legend has no entry for is never dropped: it fails the check
    and the row stays unpublishable (D3, §8.7)."""
    real = controlled.marks_in
    monkeypatch.setattr(
        controlled,
        "marks_in",
        lambda bbox, small, keys: (*real(bbox, small, keys), "z"),
    )
    with app.app_context():
        db.exec(
            "INSERT INTO source_documents (license_year, year_end, kind, title, sha256,"
            " stored_path, status) VALUES (2026, 2026, 'big_game', 'T-22a fixture',"
            " 'checks-legend', ?, 'uploaded')",
            str(CONTROLLED),
        )
        doc_id = int(db.q1("SELECT id FROM source_documents")["id"])
        ctx = replace(context(UNITS), document_id=doc_id)
        controlled.begin(ctx)
        for page in iter_pages(CONTROLLED):
            list(controlled.parse_page(page, ctx, spec=BIG_GAME, species="deer"))
        results = controlled_checks(ctx, "deer")
    assert results["footnote_legend"].status == "fail"
    assert results["footnote_legend"].detail["markers"]  # type: ignore[index]
