"""Prepare a section for human verification: page images + a checklist (§8.7, T-22c).

Golden data is only worth what the human verification behind it is worth, so before a
section can be signed off this script renders **every source page its rows cite** and
writes the checklist that page-by-page review is recorded in:

    python scripts/make_verification_checklist.py controlled_deer \\
        --pdf tests/fixtures/bg2026_controlled.pdf --year 2026

    data/verify/2026/controlled_deer/p001.png …   (never committed — `data/` is ignored)
    tests/golden/2026/VERIFICATION.md             (committed, every page unverified)

Nothing here marks anything verified: every row of the table ships empty, and a section
whose table already carries a signature is left alone unless `--force` says otherwise.
"""

from __future__ import annotations

import argparse
import re
import sys
import tempfile
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:  # runnable as `python scripts/make_verification_checklist.py`
    sys.path.insert(0, str(REPO))

from planner import db, golden  # noqa: E402

#: Where page images land. Git-ignored: they are a working aid, not data.
VERIFY_DIR = REPO / "data" / "verify"
#: Render scale — 2.6 makes the brochure's 6 pt footnote markers readable on screen.
SCALE = 2.6

HEADER = """# {year} golden data — verification log

Every row of every section below is verified **by hand against the source PDF**, one
page at a time (DESIGN.md §8.7). The parser's output is not evidence of itself: sign a
page only after reading it on the page image, and write down every correction you made.

Page images are rendered by `scripts/make_verification_checklist.py` into
`data/verify/{year}/<section>/` and are never committed.
"""

TABLE_HEAD = "| PDF page | Rows | Verified by | Date | Corrections made |\n|---|---:|---|---|---|\n"
BEGIN = "<!-- section: {section} -->"
END = "<!-- /section: {section} -->"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("section", help="section name, e.g. deer_general or controlled_deer")
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--pdf", help="parse this PDF into a throwaway database first")
    src.add_argument("--db", help="read an already-imported document from this database")
    p.add_argument("--document", type=int, help="document id (with --db; default: the newest)")
    p.add_argument("--pdf-for-render", help="the PDF to render when rows come from --db")
    p.add_argument("--year", type=int, default=2026, help="license year (default 2026)")
    p.add_argument("--kind", default="big_game", help="brochure kind for --pdf (default big_game)")
    p.add_argument("--golden-dir", help=f"override {golden.DEFAULT_GOLDEN_DIR}")
    p.add_argument("--data-dir", help="page-cache directory for --pdf (default: a temp dir)")
    p.add_argument("--out-dir", help=f"override {VERIFY_DIR}/<year>/<section>")
    p.add_argument("--no-render", action="store_true", help="write the checklist only")
    p.add_argument(
        "--force", action="store_true", help="rewrite the section even if a page is signed"
    )
    return p.parse_args(argv)


def render_pages(pdf: Path, pages: list[int], out_dir: Path) -> list[Path]:
    """Render each 1-based page to `pNNN.png`. Needs pypdfium2 (requirements-dev)."""
    try:
        import pypdfium2
    except ImportError as exc:  # pragma: no cover - environment, not logic
        raise SystemExit(
            "pypdfium2 is needed to render page images:"
            " pip install -r requirements-dev.txt, or pass --no-render"
        ) from exc
    out_dir.mkdir(parents=True, exist_ok=True)
    document = pypdfium2.PdfDocument(str(pdf))
    written: list[Path] = []
    try:
        for number in pages:
            if number < 1 or number > len(document):
                raise SystemExit(f"row cites page {number}, but {pdf} has {len(document)} pages")
            path = out_dir / f"p{number:03d}.png"
            document[number - 1].render(scale=SCALE).to_pil().save(path)
            written.append(path)
    finally:
        document.close()
    return written


def section_block(section: str, year: int, counts: Counter[int], source: str, images: Path) -> str:
    """The section's checklist, every page unverified."""
    rows = sum(counts.values())
    lines = [
        BEGIN.format(section=section),
        f"## `{section}`",
        "",
        f"Source: `{source}` · **{rows} rows** on {len(counts)} pages ·"
        f" images: `{images.relative_to(REPO) if images.is_relative_to(REPO) else images}/`",
        "",
        TABLE_HEAD.rstrip("\n"),
    ]
    for page in sorted(counts):
        lines.append(f"| p{page:03d} | {counts[page]} | | | |")
    lines.append(f"| **total** | **{rows}** | | | |")
    lines += ["", END.format(section=section)]
    return "\n".join(lines) + "\n"


def signed_pages(block: str) -> list[str]:
    """Page rows of an existing block whose *Verified by* cell is filled in."""
    out = []
    for line in block.splitlines():
        cells = [c.strip() for c in line.split("|")]
        # | page | rows | verified by | date | corrections | → 7 cells with the empty ends
        if len(cells) == 7 and cells[1].startswith("p") and cells[3]:
            out.append(cells[1])
    return out


def upsert(path: Path, section: str, block: str, year: int, force: bool) -> str:
    """Replace this section's block in VERIFICATION.md, keeping every other section."""
    text = path.read_text(encoding="utf-8") if path.is_file() else HEADER.format(year=year)
    pattern = re.compile(
        re.escape(BEGIN.format(section=section)) + r".*?" + re.escape(END.format(section=section)),
        re.S,
    )
    found = pattern.search(text)
    if found:
        signed = signed_pages(found.group(0))
        if signed and not force:
            raise SystemExit(
                f"{path} already records {len(signed)} verified page(s) for {section}"
                f" ({', '.join(signed)}); pass --force to discard that work"
            )
        return pattern.sub(lambda _m: block.rstrip("\n"), text)
    return text.rstrip("\n") + "\n\n" + block


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    with tempfile.TemporaryDirectory(prefix="ihp-verify-") as tmp:
        if args.db:
            conn = db.connect(args.db)
            try:
                doc_id = args.document
                if doc_id is None:
                    row = db.q1(
                        "SELECT id FROM source_documents WHERE license_year = ?"
                        " ORDER BY id DESC LIMIT 1",
                        args.year,
                        conn=conn,
                    )
                    if row is None:
                        raise SystemExit(f"no document for license year {args.year} in {args.db}")
                    doc_id = int(row["id"])
                rows = golden.canonical_rows(doc_id, args.section, conn=conn)
                stored = db.q1(
                    "SELECT stored_path FROM source_documents WHERE id = ?", doc_id, conn=conn
                )
                stored_path = str(stored["stored_path"] or "") if stored else ""
            finally:
                conn.close()
            pdf = Path(args.pdf_for_render or stored_path)
        else:
            pdf = Path(args.pdf)
            if not pdf.is_file():
                raise SystemExit(f"no such PDF: {pdf}")
            workdir = Path(tmp)
            doc_id, result = golden.import_pdf(
                workdir / "verify.db",
                pdf,
                kind=args.kind,
                year=args.year,
                data_dir=Path(args.data_dir) if args.data_dir else workdir / "data",
            )
            if result.status == "failed":
                raise SystemExit(f"import failed: {result.error}")
            conn = db.connect(workdir / "verify.db")
            try:
                rows = golden.canonical_rows(doc_id, args.section, conn=conn)
            finally:
                conn.close()

        if not rows:
            raise SystemExit(f"section {args.section!r} produced no rows — nothing to verify")
        counts = Counter(int(r["source_page"]) for r in rows if r.get("source_page"))
        if len(counts) != len({r.get("source_page") for r in rows}):
            raise SystemExit(f"{args.section}: a row has no source_page; it cannot be verified")

        images = Path(args.out_dir) if args.out_dir else VERIFY_DIR / str(args.year) / args.section
        if args.no_render:
            print(f"skipped rendering {len(counts)} pages (--no-render)")
        else:
            if not pdf.is_file():
                raise SystemExit("no PDF to render; pass --pdf-for-render or --no-render")
            written = render_pages(pdf, sorted(counts), images)
            print(f"rendered {len(written)} pages → {images}")

        md = golden.golden_dir(args.golden_dir) / str(args.year) / "VERIFICATION.md"
        md.parent.mkdir(parents=True, exist_ok=True)
        block = section_block(args.section, args.year, counts, str(pdf), images)
        md.write_text(upsert(md, args.section, block, args.year, args.force), encoding="utf-8")
    print(f"wrote {md}: {len(counts)} pages, {sum(counts.values())} rows, all unverified")
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI
    raise SystemExit(main())
