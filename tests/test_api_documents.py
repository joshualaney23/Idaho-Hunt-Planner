"""T-10: document upload, storage, import pipeline and PDF streaming (§8.1, §6).

Uses the committed fixture PDFs (`tests/fixtures/make_regs_fixture.py`) and parsers
registered only for the duration of a test, so the registry stays empty until T-11
ships the first real parser.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import importlib.util
import io
from collections.abc import Iterator
from pathlib import Path

import pytest

from planner import db, importer
from planner.importer import common

FIXTURES = Path(__file__).resolve().parent / "fixtures"
BIG_GAME = FIXTURES / "big_game_sample.pdf"
UPLAND = FIXTURES / "upland_sample.pdf"


def _builder():
    """The fixture generator, so a test can build a one-off PDF in memory."""
    spec = importlib.util.spec_from_file_location(
        "make_regs_fixture", FIXTURES / "make_regs_fixture.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def upload(client, path: Path | None = None, *, data: bytes | None = None, name=None, **form):
    payload = data if data is not None else (path or BIG_GAME).read_bytes()
    filename = name or (path or BIG_GAME).name
    return client.post(
        "/api/documents",
        data={"file": (io.BytesIO(payload), filename), **form},
        content_type="multipart/form-data",
    )


# ───────── test parsers ─────────


def noop_parser(page, ctx):
    """Claims every big-game page and parses nothing (the §12 acceptance case)."""
    return []


def deer_parser(page, ctx):
    """A miniature T-11: reads the fixture's `Unit(s) | Antlered | Antlerless` table."""
    title = common.parse_title(page.lines[0] if page.lines else "")
    for table in page.tables:
        header = [c or "" for c in table[0]]
        for row in table[1:]:
            units = common.parse_units(row[0] or "", ctx.units)
            for column, cell in zip(header[1:], row[1:], strict=False):
                if common.is_no_season(cell):
                    continue
                ranges = common.parse_date_ranges(cell or "", ctx.license_year)
                quals = common.qualifiers(cell or "")
                for r in ranges:
                    yield importer.ParsedSeason(
                        species_id=title.species_id or "deer",
                        tag_type_id=title.tag_type_id or "deer_regular",
                        hunt_class=title.hunt_class,
                        weapon=title.weapon,
                        sex=column.strip().lower(),
                        start=r.start,
                        end=r.end,
                        units=units.units,
                        restrictions=quals.codes,
                        source_text=cell,
                        extract_agree=True,
                        confidence=common.confidence(
                            unknown_units=len(units.unknown),
                            unparsed_date_text=len(common.leftover_date_text(cell or "", ranges)),
                            unmatched_qualifiers=len(quals.unmatched),
                            date_ranges=len(ranges),
                        ),
                    )


def bad_row_parser(page, ctx):
    """Yields one row that must never be staged: unknown unit, dates outside the year."""
    yield importer.ParsedSeason(
        species_id="deer",
        tag_type_id="deer_regular",
        hunt_class="general",
        weapon="any",
        sex="antlered",
        start=dt.date(2020, 10, 1),
        end=dt.date(2020, 10, 10),
        units=["999"],
        source_text="Units 999 Oct 1 - Oct 10",
    )


def spec(fn, *, kind="big_game", match=None, section=None) -> importer.ParserSpec:
    return importer.ParserSpec(
        name=fn.__name__,
        kind=kind,
        match=match or (lambda page: True),
        fn=fn,
        section=section or fn.__name__,
    )


@pytest.fixture
def noop(_registry=None) -> Iterator[None]:
    with importer.use_parsers(spec(noop_parser)):
        yield


# ───────── upload ─────────


def test_upload_with_a_noop_parser_needs_review(client, noop) -> None:
    """§12 T-10 acceptance: registered no-op parser + fixture PDF → `needs_review`."""
    res = upload(client)
    assert res.status_code == 201, res.get_json()
    body = res.get_json()
    doc, imported = body["document"], body["import"]
    assert doc["status"] == "needs_review"
    assert (doc["kind"], doc["license_year"], doc["year_end"]) == ("big_game", 2026, 2026)
    assert doc["sha256"] == hashlib.sha256(BIG_GAME.read_bytes()).hexdigest()
    assert doc["pages"] == 3
    assert doc["parser_version"] == importer.PARSER_VERSION
    assert doc["error"] is None
    assert (imported["pages"], imported["parsed"], imported["staged"]) == (3, 0, 0)
    assert imported["errors"] == []


def test_upload_stores_the_pdf_by_sha256(client, app, noop) -> None:
    doc = upload(client).get_json()["document"]
    data_dir = Path(app.config["DATA_DIR"])
    stored = data_dir / "regs" / "2026" / f"{doc['sha256']}.pdf"
    assert stored.is_file()
    assert stored.read_bytes() == BIG_GAME.read_bytes()
    assert (data_dir / "cache" / doc["sha256"] / "p1.json").is_file()


def test_uploading_the_same_file_twice_returns_409(client, noop) -> None:
    """§12 T-10 acceptance: the second upload of a file is refused."""
    first = upload(client)
    assert first.status_code == 201
    again = upload(client, name="renamed-copy.pdf")
    assert again.status_code == 409
    error = again.get_json()["error"]
    assert error["code"] == "duplicate_document"
    assert error["detail"]["document_id"] == first.get_json()["document"]["id"]
    assert len(client.get("/api/documents").get_json()["documents"]) == 1


def test_upload_rejects_non_pdf_and_missing_file(client, noop) -> None:
    res = upload(client, data=b"GIF89a not a pdf", name="sneaky.pdf")
    assert res.status_code == 415
    assert res.get_json()["error"]["code"] == "not_a_pdf"

    empty = client.post("/api/documents", data={}, content_type="multipart/form-data")
    assert empty.status_code == 400
    assert empty.get_json()["error"]["code"] == "missing_file"


def test_upload_rejects_an_unclassifiable_pdf(client, noop) -> None:
    mk = _builder()
    pdf = mk.pdf_bytes([[mk.text_op(100, 650, 18, "Idaho Fishing Seasons & Rules 2026")]])
    res = upload(client, data=pdf, name="fishing.pdf")
    assert res.status_code == 422
    assert res.get_json()["error"]["code"] == "unclassified"

    # …unless the form says what it is (§8.1 manual override)
    ok = upload(client, data=pdf, name="fishing.pdf", kind="big_game", year="2024")
    assert ok.status_code == 201
    doc = ok.get_json()["document"]
    assert (doc["kind"], doc["license_year"], doc["year_end"]) == ("big_game", 2024, 2024)


def test_upland_brochure_covers_two_years(client) -> None:
    with importer.use_parsers():
        doc = upload(client, UPLAND).get_json()["document"]
    assert (doc["kind"], doc["license_year"], doc["year_end"]) == ("upland", 2026, 2027)
    assert doc["status"] == "needs_review"


# ───────── parsing into staged rows ─────────


def test_a_real_parser_stages_rows_with_units_and_restrictions(client, app) -> None:
    with importer.use_parsers(spec(deer_parser, match=lambda page: "Deer Tag" in page.text)):
        body = upload(client).get_json()
    assert body["import"]["staged"] == 5
    assert body["document"]["seasons"] == {"staged": 5}

    detail = client.get(f"/api/documents/{body['document']['id']}").get_json()
    rows = detail["seasons"]
    assert all(r["status"] == "staged" and r["source_page"] == 2 for r in rows)
    assert [r["sex"] for r in rows] == [
        "antlered",
        "antlered",
        "antlerless",
        "antlered",
        "antlerless",
    ]
    first = rows[0]
    assert first["units"] == ["1", "2", "3", "4A"]
    assert (first["start_date"], first["end_date"]) == ("2026-10-10", "2026-10-24")
    assert (first["tag_type_id"], first["weapon"], first["hunt_class"]) == (
        "deer_regular",
        "any",
        "general",
    )
    assert first["extract_agree"] == 1 and first["confidence"] == 1.0

    whitetail = next(r for r in rows if r["restrictions"])
    assert whitetail["restrictions"] == ["whitetail_only"]
    assert (whitetail["start_date"], whitetail["end_date"]) == ("2026-11-15", "2027-01-15")
    assert next(r for r in rows if "youth_only" in r["restrictions"])["units"] == [
        "21",
        "21A",
        "28",
    ]

    checks = detail["checks"]
    assert [(c["check_name"], c["status"]) for c in checks] == [("row_validation", "pass")]


def test_invalid_rows_are_never_staged_and_are_reported(client) -> None:
    """D3: a row that fails validation is staged as a failing check, not written."""
    with importer.use_parsers(spec(bad_row_parser)):
        body = upload(client).get_json()
    assert body["import"]["staged"] == 0
    assert body["document"]["status"] == "needs_review"
    errors = body["import"]["errors"]
    assert len(errors) == 3  # one per page, all the same row
    assert errors[0]["reason"] == "invalid_row"
    assert "unknown units 999" in errors[0]["detail"]
    assert "outside the 2026 license year" in errors[0]["detail"]

    detail = client.get(f"/api/documents/{body['document']['id']}").get_json()
    assert detail["seasons"] == []
    (check,) = detail["checks"]
    assert (check["check_name"], check["status"]) == ("row_validation", "fail")
    assert len(check["detail"]) == 3


def test_a_parser_that_raises_doesnt_kill_the_import(client) -> None:
    def boom(page, ctx):
        raise ValueError("bad cell")

    with importer.use_parsers(spec(boom)):
        body = upload(client).get_json()
    assert body["document"]["status"] == "needs_review"
    assert [e["reason"] for e in body["import"]["errors"]] == ["parser_error"] * 3
    assert "ValueError: bad cell" in body["import"]["errors"][0]["detail"]


def test_run_import_fails_loudly_when_the_pdf_is_gone(app, tmp_path) -> None:
    with app.app_context(), importer.use_parsers(spec(noop_parser)):
        cur = db.exec(
            "INSERT INTO source_documents (license_year, year_end, kind, title, sha256,"
            " stored_path, status) VALUES (2026, 2026, 'big_game', 'gone', 'deadbeef', ?,"
            " 'uploaded')",
            str(tmp_path / "missing.pdf"),
        )
        doc_id = int(cur.lastrowid or 0)
        result = importer.run_import(doc_id, data_dir=tmp_path)
        assert result.status == "failed"
        assert "stored PDF is missing" in (result.error or "")
        row = db.q1("SELECT status, error FROM source_documents WHERE id = ?", doc_id)
        assert row["status"] == "failed" and "missing" in row["error"]


def test_reimport_replaces_the_previous_staged_rows(client, app) -> None:
    with importer.use_parsers(spec(deer_parser, match=lambda page: "Deer Tag" in page.text)):
        doc = upload(client).get_json()["document"]
        with app.app_context():
            first = importer.run_import(doc["id"], data_dir=Path(app.config["DATA_DIR"]))
            second = importer.run_import(doc["id"], data_dir=Path(app.config["DATA_DIR"]))
    assert first.staged == second.staged == 5
    rows = client.get(f"/api/documents/{doc['id']}").get_json()["seasons"]
    assert len(rows) == 5  # not 10


# ───────── parser registry ─────────


def test_parser_decorator_registers_for_one_kind() -> None:
    with importer.use_parsers():

        @importer.parser(kind="big_game", match=lambda page: "Deer Tag" in page.text)
        def deer_tables(page, ctx):
            return []

        @importer.parser(kind="upland", match=lambda page: True, name="turkey")
        def turkey_tables(page, ctx):
            return []

        assert [s.name for s in importer.parsers_for("big_game")] == ["deer_tables"]
        assert [s.name for s in importer.parsers_for("upland")] == ["turkey"]
        assert importer.parsers_for("migratory") == []
        assert len(importer.registered()) == 2
    assert importer.registered() == []  # the registry is empty again outside the block


# ───────── streaming the PDF ─────────


def test_pdf_is_served_inline(client, noop) -> None:
    doc = upload(client).get_json()["document"]
    res = client.get(f"/api/documents/{doc['id']}/pdf")
    assert res.status_code == 200
    assert res.mimetype == "application/pdf"
    assert res.headers["Content-Disposition"].startswith("inline")
    assert res.data == BIG_GAME.read_bytes()
    assert doc["pdf_url"] == f"/api/documents/{doc['id']}/pdf"


def test_pdf_errors(client, app, noop) -> None:
    missing = client.get("/api/documents/999/pdf")
    assert missing.status_code == 404
    assert missing.get_json()["error"]["code"] == "unknown_document"

    doc = upload(client).get_json()["document"]
    Path(app.config["DATA_DIR"], "regs", "2026", f"{doc['sha256']}.pdf").unlink()
    gone = client.get(f"/api/documents/{doc['id']}/pdf")
    assert gone.status_code == 410
    assert gone.get_json()["error"]["code"] == "missing_file"


def test_document_list_and_detail_404(client, noop) -> None:
    assert client.get("/api/documents").get_json()["documents"] == []
    upload(client)
    (doc,) = client.get("/api/documents").get_json()["documents"]
    assert doc["title"].startswith("2026 IDAHO")
    assert client.get("/api/documents/999").status_code == 404
