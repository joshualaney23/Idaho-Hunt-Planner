"""Dump a section's parsed rows to its golden file, or diff against it (§8.7, T-22c).

A golden file is the hand-verified truth for one section of one license year. This
script produces it in a canonical, stably sorted form, and `--check` turns the same
dump into a regression gate: it prints a unified diff and exits 1 instead of writing.

    # dump the controlled deer rows of the committed fixture excerpt
    python scripts/make_golden.py controlled_deer --pdf tests/fixtures/bg2026_controlled.pdf \\
        --year 2026 --kind big_game --as fixture_controlled_deer

    # the real brochure, into tests/golden/2026/controlled_deer.json
    python scripts/make_golden.py controlled_deer --pdf data/regs/seasons-rules-big-game-2026.pdf
    python scripts/make_golden.py controlled_deer --pdf data/regs/...pdf --check   # 1 on a diff

    # a document already imported into the app's own database
    python scripts/make_golden.py deer_general --db data/planner.db --document 3

Dumping does **not** verify anything: a page counts as verified only when a human has
read it against the PDF and signed the row in `tests/golden/<year>/VERIFICATION.md`
(`scripts/make_verification_checklist.py`).
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:  # runnable as `python scripts/make_golden.py`
    sys.path.insert(0, str(REPO))

from planner import db, golden  # noqa: E402


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("section", help="section name, e.g. deer_general or controlled_deer")
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--pdf", help="parse this PDF into a throwaway database first")
    src.add_argument("--db", help="read an already-imported document from this database")
    src.add_argument("--url", help="parse the cached download of this URL (fetched once)")
    p.add_argument("--document", type=int, help="document id (with --db; default: the newest)")
    p.add_argument("--year", type=int, default=2026, help="license year (default 2026)")
    p.add_argument("--kind", default="big_game", help="brochure kind for --pdf (default big_game)")
    p.add_argument("--as", dest="name", help="file stem (default: the section name)")
    p.add_argument("--golden-dir", help=f"override {golden.DEFAULT_GOLDEN_DIR}")
    p.add_argument("--data-dir", help="page-cache directory for --pdf (default: a temp dir)")
    p.add_argument(
        "--check",
        action="store_true",
        help="print the diff against the committed file and exit 1; write nothing",
    )
    return p.parse_args(argv)


def rows_from_pdf(pdf: Path, args: argparse.Namespace, workdir: Path) -> list[dict]:
    """Import `pdf` into a throwaway DB and return the section's canonical rows."""
    data_dir = Path(args.data_dir) if args.data_dir else workdir / "data"
    doc_id, result = golden.import_pdf(
        workdir / "golden.db", pdf, kind=args.kind, year=args.year, data_dir=data_dir
    )
    if result.status == "failed":
        raise SystemExit(f"import failed: {result.error}")
    print(
        f"imported {pdf} → {result.pages} pages, {result.staged} rows staged"
        f" in {len(result.sections)} sections",
        file=sys.stderr,
    )
    conn = db.connect(workdir / "golden.db")
    try:
        return golden.canonical_rows(doc_id, args.section, conn=conn)
    finally:
        conn.close()


def rows_from_db(args: argparse.Namespace) -> list[dict]:
    """Read an already-imported document out of an existing database."""
    conn = db.connect(args.db)
    try:
        doc_id = args.document
        if doc_id is None:
            row = db.q1(
                "SELECT id FROM source_documents WHERE license_year = ? ORDER BY id DESC LIMIT 1",
                args.year,
                conn=conn,
            )
            if row is None:
                raise SystemExit(f"no document for license year {args.year} in {args.db}")
            doc_id = int(row["id"])
        return golden.canonical_rows(doc_id, args.section, conn=conn)
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    name = args.name or args.section
    path = golden.golden_path(args.year, name, args.golden_dir)

    with tempfile.TemporaryDirectory(prefix="ihp-golden-") as tmp:
        if args.db:
            rows = rows_from_db(args)
        else:
            pdf = Path(args.pdf) if args.pdf else golden.cached_pdf(args.url, download=True)
            if not pdf.is_file():
                raise SystemExit(f"no such PDF: {pdf}")
            rows = rows_from_pdf(pdf, args, Path(tmp))

    if not rows:
        raise SystemExit(f"section {args.section!r} produced no rows — nothing to dump")
    text = golden.dumps(golden.build(args.section, args.year, rows))

    if args.check:
        if not path.is_file():
            print(f"no golden file at {path}", file=sys.stderr)
            return 1
        committed = path.read_text(encoding="utf-8")
        if committed == text:
            print(f"{path}: {len(rows)} rows match")
            return 0
        diff = golden.diff_text(
            committed, text, from_name=f"{path} (golden)", to_name=f"{args.section} (parsed)"
        )
        print(diff, end="")
        changes = golden.row_diffs(golden.load(path).get("rows") or [], rows)
        print(
            f"\n{path}: {len(changes)} rows differ "
            f"({', '.join(sorted({c['kind'] for c in changes})) or 'formatting only'})",
            file=sys.stderr,
        )
        return 1

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    print(f"wrote {path}: {len(rows)} rows")
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI
    raise SystemExit(main())
