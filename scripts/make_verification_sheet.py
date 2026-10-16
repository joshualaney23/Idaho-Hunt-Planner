"""Render the page-by-page review sheet a human verifies a golden section with (§8.7).

`make_verification_checklist.py` renders the page images and writes the log that gets
signed; this script writes the thing you actually read while signing it — every parsed
row of a section, grouped under the image of the page it was read from, in page order:

    python scripts/make_verification_sheet.py deer_general --year 2026
    open data/verify/2026/deer_general/review.html

The sheet is a working aid, never evidence: it is written into the git-ignored
`data/verify/` tree, it marks nothing verified, and a row it shows is only as good as
the page image beside it. Corrections go into the golden file and into the
*Corrections made* cell of `tests/golden/<year>/VERIFICATION.md`.
"""

from __future__ import annotations

import argparse
import html
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:  # runnable as `python scripts/make_verification_sheet.py`
    sys.path.insert(0, str(REPO))

from planner import golden  # noqa: E402

VERIFY_DIR = REPO / "data" / "verify"

#: Columns of the row table, in the order a brochure row reads.
COLUMNS = (
    ("units", "Unit(s)"),
    ("tag_type_id", "Tag"),
    ("hunt_number", "Hunt no."),
    ("weapon", "Weapon"),
    ("sex", "Sex"),
    ("dates", "Season dates"),
    ("bag_limit", "Bag"),
    ("tag_quota", "Tags"),
    ("restrictions", "Restrictions"),
    ("notes", "Notes"),
)

STYLE = """
body { font: 14px/1.45 -apple-system, system-ui, sans-serif; margin: 0 auto; max-width: 1600px;
       padding: 1.5rem; }
h1 { margin: 0 0 .25rem; } p.lede { margin: 0 0 2rem; opacity: .75; }
section { border-top: 2px solid currentColor; padding-top: 1rem; margin-bottom: 3rem; }
.page { display: grid; grid-template-columns: minmax(380px, 46%) 1fr; gap: 1.5rem;
        align-items: start; }
.page img { width: 100%; border: 1px solid currentColor; position: sticky; top: 1rem; }
table { border-collapse: collapse; width: 100%; font-size: 12.5px; }
th, td { border: 1px solid currentColor; padding: 3px 6px; text-align: left;
         vertical-align: top; }
th { position: sticky; top: 0; }
td.n { text-align: right; opacity: .6; }
td.notes { max-width: 26ch; }
td.units { font-weight: 600; white-space: normal; max-width: 18ch; }
.partial { opacity: .7; font-style: italic; }
@media print { .page img { position: static; } }
"""


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("section", help="section name, e.g. deer_general")
    p.add_argument("--year", type=int, default=2026, help="license year (default 2026)")
    p.add_argument("--golden-dir", help=f"override {golden.DEFAULT_GOLDEN_DIR}")
    p.add_argument("--images", help="page-image directory (default: beside the output)")
    p.add_argument("--out", help=f"override {VERIFY_DIR}/<year>/<section>/review.html")
    return p.parse_args(argv)


def cell(row: dict[str, Any], key: str) -> str:
    """One table cell's text, in the brochure's own vocabulary."""
    if key == "dates":
        return f"{row['start_date']} – {row['end_date']}"
    if key == "units":
        units = row.get("units") or []
        partial = row.get("partial_units") or {}
        return ", ".join(f"{u}*" if u in partial else str(u) for u in units)
    value = row.get(key)
    if value in (None, "", [], {}):
        return ""
    if isinstance(value, list):
        return ", ".join(str(v) for v in value)
    return str(value)


def page_table(rows: list[dict[str, Any]]) -> str:
    head = "".join(f"<th>{html.escape(label)}</th>" for _key, label in COLUMNS)
    body = []
    for n, row in enumerate(rows, start=1):
        cells = "".join(
            f'<td class="{key}">{html.escape(cell(row, key))}</td>' for key, _label in COLUMNS
        )
        body.append(f'<tr><td class="n">{n}</td>{cells}</tr>')
    return f"<table><thead><tr><th></th>{head}</tr></thead><tbody>{''.join(body)}</tbody></table>"


def sheet(section: str, year: int, rows: list[dict[str, Any]], images: Path) -> str:
    by_page: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_page[int(row["source_page"])].append(row)
    sort_key = ("units", "start_date", "weapon", "sex")
    parts = [
        "<!doctype html><html lang=en><head><meta charset=utf-8>",
        f"<title>{html.escape(section)} {year} — verification sheet</title>",
        f"<style>{STYLE}</style></head><body>",
        f"<h1>{html.escape(section)} · {year} — verification sheet</h1>",
        f"<p class=lede>{len(rows)} parsed rows on {len(by_page)} pages. Read each row against"
        " the page image beside it. A unit marked <code>*</code> is partial coverage."
        " Nothing here is verified: sign pages in"
        " <code>tests/golden/{year}/VERIFICATION.md</code> and correct wrong rows in the"
        " golden file.</p>".replace("{year}", str(year)),
    ]
    for page in sorted(by_page):
        page_rows = sorted(by_page[page], key=lambda r: tuple(str(r.get(k)) for k in sort_key))
        image = images / f"p{page:03d}.png"
        src = html.escape(image.name if image.is_file() else "")
        img = (
            f'<a href="{src}"><img src="{src}" alt="PDF page {page}"></a>'
            if src
            else f"<p>no image rendered for page {page}</p>"
        )
        parts.append(
            f"<section><h2>PDF page {page} · {len(page_rows)} rows</h2>"
            f'<div class="page">{img}<div>{page_table(page_rows)}</div></div></section>'
        )
    parts.append("</body></html>")
    return "\n".join(parts)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    path = golden.golden_path(args.year, args.section, args.golden_dir)
    if not path.is_file():
        raise SystemExit(f"no golden file at {path}: run scripts/make_golden.py first")
    data = golden.load(path)
    out = Path(args.out) if args.out else VERIFY_DIR / str(args.year) / args.section / "review.html"
    images = Path(args.images) if args.images else out.parent
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(sheet(args.section, args.year, data["rows"], images), encoding="utf-8")
    print(f"wrote {out}: {data['row_count']} rows, images from {images}")
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI
    raise SystemExit(main())
