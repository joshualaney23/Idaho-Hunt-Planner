"""Golden datasets: dump, diff and compare (DESIGN.md §8.7, §12 T-22c).

A *golden file* is the hand-verified truth for one section of one license year, as
`tests/golden/<year>/<section>.json`. The parser must reproduce it with **zero diffs**:
the `golden_match` check (`planner/importer/checks/golden.py`) runs on every import, and
`tests/test_golden.py` re-parses the source PDF and asserts an exact match.

This module owns the three pieces the harness shares:

* **canonical form** — `canonical_rows()` reduces a document's rows for one section to
  plain JSON, stably sorted, with everything volatile (row ids, the document, geometry,
  the parser's own confidence) left out, so a diff only ever shows real data changes.
* **diffing** — `diff_text()` for humans (a unified diff of the canonical JSON) and
  `row_diffs()` for the check's `detail` (missing / unexpected / changed rows).
* **a reproducible import** — `import_pdf()` parses a PDF into a throwaway DB so the
  dump, the test and the check all read rows produced the same way.

Golden files are never written by an agent's judgement: the rows come from the parser,
and a human verifies them page by page against the PDF (`VERIFICATION.md`, T-22d–f).
"""

from __future__ import annotations

import difflib
import hashlib
import json
import os
import urllib.request
from pathlib import Path
from typing import Any

from planner import db
from planner.importer.common import natural_key

__all__ = [
    "DEFAULT_GOLDEN_DIR",
    "GOLDEN_DIR_ENV",
    "PDF_CACHE_ENV",
    "SOURCES_PATH",
    "build",
    "canonical_rows",
    "cached_pdf",
    "diff_text",
    "dumps",
    "golden_dir",
    "golden_path",
    "identity",
    "import_pdf",
    "load",
    "row_diffs",
    "sections_with_golden",
    "sources",
]

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_GOLDEN_DIR = REPO_ROOT / "tests" / "golden"
#: Point the harness at another tree (tests use a tmp dir; never set in production).
GOLDEN_DIR_ENV = "IHP_GOLDEN_DIR"
#: Where a downloaded source brochure is cached, keyed by the hash of its URL.
PDF_CACHE_ENV = "IHP_PDF_CACHE"
DEFAULT_PDF_CACHE = REPO_ROOT / "data" / "pdf_cache"
#: The source PDFs a golden year is built from: name → URL (committed; CI caches by it).
SOURCES_PATH = DEFAULT_GOLDEN_DIR / "sources.json"

#: Schema of a dumped file, bumped when the canonical row form itself changes.
FORMAT_VERSION = 1


# ───────── where the files live ─────────


def golden_dir(directory: str | Path | None = None) -> Path:
    """`tests/golden`, or the override (`directory`, else `$IHP_GOLDEN_DIR`)."""
    if directory is not None:
        return Path(directory)
    env = os.environ.get(GOLDEN_DIR_ENV)
    return Path(env) if env else DEFAULT_GOLDEN_DIR


def golden_path(year: int, name: str, directory: str | Path | None = None) -> Path:
    """`<golden dir>/<year>/<name>.json` — one file per section per license year."""
    return golden_dir(directory) / str(year) / f"{name}.json"


def sections_with_golden(year: int, directory: str | Path | None = None) -> list[str]:
    """Section names that have a committed golden file for this year, sorted."""
    folder = golden_dir(directory) / str(year)
    if not folder.is_dir():
        return []
    return sorted(p.stem for p in folder.glob("*.json") if p.name != SOURCES_PATH.name)


def sources(path: str | Path | None = None) -> dict[str, dict[str, Any]]:
    """The committed source-PDF table (`tests/golden/sources.json`)."""
    file = Path(path) if path is not None else SOURCES_PATH
    if not file.is_file():
        return {}
    data = json.loads(file.read_text(encoding="utf-8"))
    return data.get("documents", data)


def cached_pdf(url: str, *, cache_dir: str | Path | None = None, download: bool = False) -> Path:
    """The cached copy of `url`, keyed by the hash of the URL itself.

    Nothing is fetched unless `download=True`: the golden test must *skip* when the
    brochure isn't cached, never silently pull 30 MB in CI.
    """
    folder = Path(cache_dir or os.environ.get(PDF_CACHE_ENV) or DEFAULT_PDF_CACHE)
    path = folder / f"{hashlib.sha256(url.encode()).hexdigest()[:16]}.pdf"
    if download and not path.is_file():
        folder.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(url, timeout=120) as response:  # noqa: S310 - committed URL
            data = response.read()
        path.write_bytes(data)
    return path


# ───────── the canonical row form ─────────

#: `seasons` columns a golden row keeps, in the order they are written. Left out on
#: purpose: `id`/`document_id` (volatile), `status` (review state, not data),
#: `source_text`/`source_bbox` (page geometry — it changes with the extractor, not with
#: the regulations), `confidence`/`extract_agree` (the parser's opinion of itself).
ROW_COLUMNS = (
    "species_id",
    "sub_species",
    "tag_type_id",
    "zone",
    "hunt_number",
    "hunt_area",
    "hunt_class",
    "weapon",
    "sex",
    "start_date",
    "end_date",
    "restrictions",
    "bag_limit",
    "notes",
    "tag_quota",
    "source_page",
)

#: What makes two rows *the same row* when diffing: everything a human reads off the
#: page to find the line again. Fields outside this key are the ones a diff reports as
#: *changed* rather than as a missing/unexpected row.
IDENTITY_FIELDS = (
    "species_id",
    "tag_type_id",
    "hunt_class",
    "hunt_number",
    "zone",
    "weapon",
    "sex",
    "start_date",
    "end_date",
    "units",
)


def _units_of(season_id: int, conn: Any = None) -> tuple[list[str], dict[str, Any]]:
    """`(units, partial)` — unit ids sorted naturally, plus the non-full coverage only."""
    rows = db.q(
        "SELECT unit_id, coverage, coverage_note, coverage_frac FROM season_units"
        " WHERE season_id = ?",
        season_id,
        conn=conn,
    )
    units = sorted((r["unit_id"] for r in rows), key=natural_key)
    partial = {
        r["unit_id"]: {
            "coverage": r["coverage"],
            "note": r["coverage_note"],
            "frac": r["coverage_frac"],
        }
        for r in rows
        if r["coverage"] != "full"
    }
    return units, dict(sorted(partial.items(), key=lambda kv: natural_key(kv[0])))


def _row(record: Any, conn: Any = None) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for column in ROW_COLUMNS:
        value = record[column]
        if column == "restrictions":
            value = sorted(json.loads(value or "[]"))
        out[column] = value
    units, partial = _units_of(int(record["id"]), conn=conn)
    out["units"] = units
    if partial:
        out["partial_units"] = partial
    return out


def identity(row: dict[str, Any]) -> tuple[Any, ...]:
    """The tuple that makes a row findable on the page (see `IDENTITY_FIELDS`)."""
    return tuple(tuple(row.get(f) or ()) if f == "units" else row.get(f) for f in IDENTITY_FIELDS)


def sort_key(row: dict[str, Any]) -> tuple[Any, ...]:
    """A total order over rows that reads like the brochure: species, hunt, dates, units."""
    return (
        str(row.get("species_id") or ""),
        str(row.get("tag_type_id") or ""),
        natural_key(str(row.get("hunt_number") or "")),
        str(row.get("start_date") or ""),
        str(row.get("end_date") or ""),
        str(row.get("weapon") or ""),
        str(row.get("sex") or ""),
        str(row.get("zone") or ""),
        tuple(natural_key(u) for u in row.get("units") or ()),
        int(row.get("source_page") or 0),
    )


def canonical_rows(
    document_id: int,
    section: str,
    *,
    conn: Any = None,
    statuses: tuple[str, ...] = ("staged", "approved", "published"),
) -> list[dict[str, Any]]:
    """Every row of one section of one document, in canonical, stably sorted form.

    `rejected` and `superseded` rows are history, not data, so they are left out.
    """
    marks = ", ".join("?" for _ in statuses)
    records = db.q(
        f"SELECT * FROM seasons WHERE document_id = ? AND section = ? AND status IN ({marks})",
        document_id,
        section,
        *statuses,
        conn=conn,
    )
    return sorted((_row(r, conn=conn) for r in records), key=sort_key)


def build(section: str, year: int, rows: list[dict[str, Any]]) -> dict[str, Any]:
    """The full golden payload — the rows plus what identifies them."""
    return {
        "format": FORMAT_VERSION,
        "section": section,
        "license_year": year,
        "row_count": len(rows),
        "rows": rows,
    }


def dumps(payload: dict[str, Any]) -> str:
    """The on-disk text: one line per field, stable, newline-terminated."""
    return json.dumps(payload, indent=2, ensure_ascii=False) + "\n"


def load(path: str | Path) -> dict[str, Any]:
    """Read a golden file."""
    return json.loads(Path(path).read_text(encoding="utf-8"))


# ───────── diffing ─────────


def diff_text(expected: str, actual: str, *, from_name: str, to_name: str) -> str:
    """A unified diff of two canonical dumps — what `--check` prints."""
    return "".join(
        difflib.unified_diff(
            expected.splitlines(keepends=True),
            actual.splitlines(keepends=True),
            fromfile=from_name,
            tofile=to_name,
            n=3,
        )
    )


def _describe(row: dict[str, Any]) -> str:
    """How a row is named in a diff: hunt number or units, plus its window."""
    who = row.get("hunt_number") or ",".join(row.get("units") or ()) or "?"
    return (
        f"{row.get('species_id')} {who} {row.get('start_date')}–{row.get('end_date')}"
        f" (p.{row.get('source_page')})"
    )


def row_diffs(expected: list[dict[str, Any]], actual: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Row-level differences, as `golden_match` stores them in `import_checks.detail`.

    Rows are matched on `identity()`, so a changed date reads as two rows (one missing,
    one unexpected) while a changed note reads as one `changed` row with its fields.
    """
    by_id = {identity(r): r for r in expected}
    seen: set[tuple[Any, ...]] = set()
    out: list[dict[str, Any]] = []
    for row in actual:
        key = identity(row)
        seen.add(key)
        want = by_id.get(key)
        if want is None:
            out.append({"kind": "unexpected", "row": _describe(row), "fields": {}})
            continue
        fields = {
            f: {"golden": want.get(f), "parsed": row.get(f)}
            for f in set(want) | set(row)
            if want.get(f) != row.get(f)
        }
        if fields:
            out.append({"kind": "changed", "row": _describe(row), "fields": fields})
    for key, want in by_id.items():
        if key not in seen:
            out.append({"kind": "missing", "row": _describe(want), "fields": {}})
    return out


# ───────── a reproducible import ─────────


def import_pdf(
    db_path: str | Path,
    pdf: str | Path,
    *,
    kind: str,
    year: int,
    year_end: int | None = None,
    title: str | None = None,
    data_dir: str | Path | None = None,
) -> tuple[int, Any]:
    """Parse `pdf` into a fresh DB at `db_path` and return `(document_id, ImportResult)`.

    The same path the upload API takes, minus HTTP: the harness, the test and the check
    must all read rows produced by exactly one code path (§8.7 "the truth is the PDF").
    """
    # Imported here, not at module scope: the check registry imports this module while
    # `planner.importer` is still executing its own module body.
    from planner.importer import run_import, sha256_bytes

    pdf = Path(pdf)
    path = Path(db_path)
    if not path.exists():
        db.init_db(path, year)
    conn = db.connect(path)
    try:
        with db.use(conn):
            sha = sha256_bytes(pdf.read_bytes())
            doc_id = int(
                db.exec(
                    "INSERT INTO source_documents (license_year, year_end, kind, title,"
                    " filename, sha256, stored_path, status)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, 'uploaded')",
                    year,
                    year if year_end is None else year_end,
                    kind,
                    title or pdf.name,
                    pdf.name,
                    sha,
                    str(pdf.resolve()),
                ).lastrowid
                or 0
            )
            result = run_import(doc_id, pdf_path=pdf, data_dir=data_dir)
        return doc_id, result
    finally:
        conn.close()
