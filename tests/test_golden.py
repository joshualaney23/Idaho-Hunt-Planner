"""T-22c: the golden harness — dump, diff, `golden_match` and the verification checklist.

The whole loop runs here on the committed controlled-hunt excerpt
(`tests/fixtures/bg2026_controlled.pdf`, 14 real 2026 brochure pages):

    import → dump → tests/golden/2026/fixture_controlled_deer.json
           → `make_golden.py --check` (clean, and a readable diff when a row changes)
           → `golden_match` during the next import (pass / fail / skipped)
           → `make_verification_checklist.py` (every page unverified)

and once more on the real 125-page brochure when it is cached locally — skipped with an
explicit message when it is not, so CI and an offline machine still run everything else.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from planner import db, golden
from planner.importer import ParseContext
from planner.importer.checks import CheckContext, SectionFacts
from planner.importer.checks.golden import golden_match

REPO = Path(__file__).resolve().parent.parent
FIXTURE = REPO / "tests" / "fixtures" / "bg2026_controlled.pdf"
COMMITTED = REPO / "tests" / "golden" / "2026" / "fixture_controlled_deer.json"
VERIFICATION = REPO / "tests" / "golden" / "2026" / "VERIFICATION.md"
SECTION = "controlled_deer"
YEAR = 2026
#: What `make_controlled_fixtures.py`'s excerpt holds, per section.
FIXTURE_SECTIONS = (
    "controlled_deer",
    "controlled_elk",
    "controlled_pronghorn",
    "controlled_black_bear",
)


def _run(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    """Run one of the harness scripts the way a human would, from the repo root."""
    return subprocess.run(  # noqa: S603 - fixed argv, test-only
        [sys.executable, *args],
        cwd=REPO,
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": str(REPO), **(env or {})},
        check=False,
    )


@pytest.fixture(scope="module")
def imported(tmp_path_factory) -> tuple[Path, int]:
    """The fixture excerpt imported once into a throwaway DB (page cache shared)."""
    work = tmp_path_factory.mktemp("golden")
    doc_id, result = golden.import_pdf(
        work / "golden.db", FIXTURE, kind="big_game", year=YEAR, data_dir=work / "data"
    )
    assert result.status == "needs_review", result.error
    return work, doc_id


@pytest.fixture
def conn(imported):
    work, _doc_id = imported
    c = db.connect(work / "golden.db")
    yield c
    c.close()


# ───────── AC1: dump → checklist → diff → test, on the committed fixture ─────────


def test_the_dump_reproduces_the_committed_golden_file_byte_for_byte(imported, conn) -> None:
    _work, doc_id = imported
    rows = golden.canonical_rows(doc_id, SECTION, conn=conn)
    assert len(rows) == 81
    text = golden.dumps(golden.build(SECTION, YEAR, rows))
    assert text == COMMITTED.read_text(encoding="utf-8")


def test_the_canonical_form_is_stable_and_carries_no_volatile_field(imported, conn) -> None:
    """Row ids, the document, geometry and the parser's confidence stay out (§8.7)."""
    _work, doc_id = imported
    rows = golden.canonical_rows(doc_id, SECTION, conn=conn)
    assert rows == sorted(rows, key=golden.sort_key)
    assert all(set(r) <= {*golden.ROW_COLUMNS, "units", "partial_units"} for r in rows)
    forbidden = {"id", "document_id", "status", "source_text", "source_bbox", "confidence"}
    assert all(not forbidden & set(r) for r in rows)
    first = rows[0]
    assert (first["hunt_number"], first["source_page"]) == ("1001", 1)
    assert (first["start_date"], first["end_date"]) == ("2026-08-30", "2026-12-01")
    # T-17b resolves a controlled hunt's area to its real GMUs (deer area 1-1).
    assert first["units"] == ["1", "2", "3", "4", "4A", "5", "6", "7", "9"]
    assert first["tag_quota"] == 60


def test_make_golden_check_is_clean_against_the_committed_file(imported) -> None:
    work, doc_id = imported
    done = _run(
        "scripts/make_golden.py",
        SECTION,
        "--db",
        str(work / "golden.db"),
        "--document",
        str(doc_id),
        "--as",
        "fixture_controlled_deer",
        "--check",
    )
    assert done.returncode == 0, done.stdout + done.stderr
    assert "81 rows match" in done.stdout


# ───────── AC2: --check exits non-zero with a readable diff ─────────


def test_make_golden_check_exits_non_zero_with_a_readable_diff(imported, tmp_path) -> None:
    """One edited date must show up as a diff the reviewer can read, and exit 1."""
    work, doc_id = imported
    dir_ = tmp_path / "golden"
    (dir_ / str(YEAR)).mkdir(parents=True)
    data = json.loads(COMMITTED.read_text(encoding="utf-8"))
    data["rows"][0]["end_date"] = "2026-12-31"  # the brochure says Dec 1
    target = dir_ / str(YEAR) / "fixture_controlled_deer.json"
    target.write_text(golden.dumps(data), encoding="utf-8")

    done = _run(
        "scripts/make_golden.py",
        SECTION,
        "--db",
        str(work / "golden.db"),
        "--document",
        str(doc_id),
        "--as",
        "fixture_controlled_deer",
        "--golden-dir",
        str(dir_),
        "--check",
    )
    assert done.returncode == 1
    assert '-      "end_date": "2026-12-31"' in done.stdout
    assert '+      "end_date": "2026-12-01"' in done.stdout
    # `end_date` is part of a row's identity, so a moved date reads as two rows.
    assert "2 rows differ (missing, unexpected)" in done.stderr
    assert target.read_text(encoding="utf-8") == golden.dumps(data), "--check never writes"


def test_a_changed_row_reads_as_missing_plus_unexpected_a_changed_note_as_changed() -> None:
    """`row_diffs` splits identity changes from field changes (the check's detail)."""
    rows = json.loads(COMMITTED.read_text(encoding="utf-8"))["rows"]
    moved = [{**rows[0], "end_date": "2026-12-31"}, *rows[1:]]
    kinds = [d["kind"] for d in golden.row_diffs(rows, moved)]
    assert sorted(kinds) == ["missing", "unexpected"]

    renoted = [{**rows[0], "notes": "something else"}, *rows[1:]]
    diffs = golden.row_diffs(rows, renoted)
    assert [d["kind"] for d in diffs] == ["changed"]
    assert set(diffs[0]["fields"]) == {"notes"}
    assert diffs[0]["row"].startswith("deer 1001 2026-08-30–2026-12-01")
    assert golden.row_diffs(rows, rows) == []


# ───────── AC3: `golden_match` in the registry ─────────


def _context(doc_id: int, section: str = SECTION) -> CheckContext:
    return CheckContext(
        parse=ParseContext(document_id=doc_id, kind="big_game", license_year=YEAR, year_end=YEAR),
        section=section,
        facts=SectionFacts(section=section),
    )


def test_golden_match_passes_fails_and_skips(imported, conn, tmp_path, monkeypatch) -> None:
    work, doc_id = imported
    dir_ = tmp_path / "golden"
    (dir_ / str(YEAR)).mkdir(parents=True)
    monkeypatch.setenv(golden.GOLDEN_DIR_ENV, str(dir_))
    with db.use(conn):
        # No committed file for the section: never a fail, and never a publish blocker.
        skipped = golden_match(_context(doc_id))
        assert skipped is not None and skipped.status == "skipped"
        assert "no golden file yet" in (skipped.actual or "")
        # A section that parsed nothing says nothing at all (no general deer tables
        # are in this excerpt, so `deer_general` staged no rows).
        assert golden_match(_context(doc_id, "deer_general")) is None

        target = dir_ / str(YEAR) / f"{SECTION}.json"
        target.write_text(COMMITTED.read_text(encoding="utf-8"), encoding="utf-8")
        passed = golden_match(_context(doc_id))
        assert passed is not None
        assert (passed.status, passed.detail) == ("pass", None)
        assert passed.actual == "81 parsed, 0 differ"

        data = json.loads(target.read_text(encoding="utf-8"))
        data["rows"][3]["notes"] = "not what the page says"
        data["rows"].pop()
        target.write_text(golden.dumps(data), encoding="utf-8")
        failed = golden_match(_context(doc_id))
        assert failed is not None and failed.status == "fail"
        assert sorted({d["kind"] for d in failed.detail}) == ["changed", "unexpected"]


def test_golden_match_is_recorded_for_every_section_of_a_real_import(tmp_path, monkeypatch) -> None:
    """End to end: the check lands in `import_checks`, pass next to skipped."""
    dir_ = tmp_path / "golden"
    (dir_ / str(YEAR)).mkdir(parents=True)
    (dir_ / str(YEAR) / f"{SECTION}.json").write_text(
        COMMITTED.read_text(encoding="utf-8"), encoding="utf-8"
    )
    monkeypatch.setenv(golden.GOLDEN_DIR_ENV, str(dir_))
    doc_id, result = golden.import_pdf(
        tmp_path / "run.db", FIXTURE, kind="big_game", year=YEAR, data_dir=tmp_path / "data"
    )
    assert result.status == "needs_review"
    conn = db.connect(tmp_path / "run.db")
    try:
        rows = {
            r["section"]: r["status"]
            for r in db.q(
                "SELECT section, status FROM import_checks WHERE document_id = ?"
                " AND check_name = 'golden_match'",
                doc_id,
                conn=conn,
            )
        }
    finally:
        conn.close()
    assert rows[SECTION] == "pass"
    assert all(rows[s] == "skipped" for s in FIXTURE_SECTIONS if s != SECTION)
    assert "deer_general" not in rows  # no rows, no golden file, nothing to say


# ───────── AC4: nothing ships verified ─────────


def _block(text: str, section: str) -> str:
    """One section's slice of the log, markers included."""
    start = text.index(f"<!-- section: {section} -->")
    return text[start : text.index(f"<!-- /section: {section} -->", start)]


def test_the_fixture_section_of_the_committed_log_has_no_signed_page() -> None:
    """Nothing the harness dumps ships verified: this excerpt is a harness fixture,
    not hand-verified data (the hand-verified sections are signed by T-22d–f)."""
    text = VERIFICATION.read_text(encoding="utf-8")
    from scripts.make_verification_checklist import signed_pages

    assert signed_pages(_block(text, SECTION)) == []
    assert "| p001 | 27 | | | |" in text


def test_every_committed_golden_section_is_signed_page_by_page() -> None:
    """A golden file may only be committed once its log names a human and a date
    for every page it cites (§8.7, T-22d)."""
    text = VERIFICATION.read_text(encoding="utf-8")
    from scripts.make_verification_checklist import signed_pages

    for section in golden.sections_with_golden(YEAR):
        if section.startswith("fixture_"):
            continue
        rows = json.loads(golden.golden_path(YEAR, section).read_text(encoding="utf-8"))["rows"]
        pages = sorted({f"p{int(r['source_page']):03d}" for r in rows})
        block = _block(text, section)
        assert signed_pages(block) == pages, section
        for line in block.splitlines():
            cells = [c.strip() for c in line.split("|")]
            if len(cells) == 7 and cells[1] in pages:
                assert cells[3] and cells[4], f"{section} {cells[1]} has no verifier or date"
                assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", cells[4]), cells


def test_the_checklist_script_writes_an_unverified_skeleton(tmp_path) -> None:
    dir_ = tmp_path / "golden"
    done = _run(
        "scripts/make_verification_checklist.py",
        SECTION,
        "--pdf",
        str(FIXTURE),
        "--year",
        str(YEAR),
        "--golden-dir",
        str(dir_),
        "--data-dir",
        str(tmp_path / "data"),
        "--no-render",
    )
    assert done.returncode == 0, done.stdout + done.stderr
    assert "all unverified" in done.stdout
    written = (dir_ / str(YEAR) / "VERIFICATION.md").read_text(encoding="utf-8")
    from scripts.make_verification_checklist import signed_pages

    assert signed_pages(written) == []
    assert written.count("| | |") == 4  # three pages plus the total row, every cell empty


def test_the_checklist_refuses_to_discard_a_human_signature(tmp_path) -> None:
    from scripts.make_verification_checklist import upsert

    md = tmp_path / "VERIFICATION.md"
    block = (
        "<!-- section: controlled_deer -->\n## `controlled_deer`\n\n"
        "| PDF page | Rows | Verified by | Date | Corrections made |\n|---|---:|---|---|---|\n"
        "| p001 | 27 | josh | 2026-10-16 | fixed hunt 1003's end date |\n"
        "<!-- /section: controlled_deer -->\n"
    )
    md.write_text(block, encoding="utf-8")
    with pytest.raises(SystemExit, match="already records 1 verified page"):
        upsert(md, SECTION, "new block", YEAR, force=False)
    assert "josh" in md.read_text(encoding="utf-8")


# ───────── AC: the real brochure, when it is cached ─────────


def _real_brochure() -> tuple[Path, dict] | None:
    source = golden.sources().get("big_game_2026")
    if not source:
        return None
    path = golden.cached_pdf(source["url"])
    return (path, source) if path.is_file() else None


@pytest.mark.skipif(
    _real_brochure() is None,
    reason=(
        "the 2026 Big Game brochure is not cached: run"
        " `python scripts/make_golden.py <section> --url $(jq -r .documents.big_game_2026.url"
        " tests/golden/sources.json)` once with a network connection"
    ),
)
def test_the_real_brochure_still_matches_every_committed_golden_section(tmp_path) -> None:
    """Re-parse the real PDF: every committed golden section must match exactly."""
    found = _real_brochure()
    assert found is not None
    pdf, source = found
    sections = [s for s in golden.sections_with_golden(YEAR) if not s.startswith("fixture_")]
    if not sections:
        pytest.skip("no golden section is committed for 2026 yet (T-22d–f verify them by hand)")
    doc_id, result = golden.import_pdf(
        tmp_path / "real.db",
        pdf,
        kind=source["kind"],
        year=source["license_year"],
        year_end=source["year_end"],
        data_dir=golden.DEFAULT_PDF_CACHE / "pages",
    )
    assert result.status == "needs_review", result.error
    conn = db.connect(tmp_path / "real.db")
    try:
        for section in sections:
            rows = golden.canonical_rows(doc_id, section, conn=conn)
            want = golden.load(golden.golden_path(YEAR, section))
            assert golden.row_diffs(want["rows"], rows) == [], section
            assert len(rows) == want["row_count"], section
    finally:
        conn.close()
