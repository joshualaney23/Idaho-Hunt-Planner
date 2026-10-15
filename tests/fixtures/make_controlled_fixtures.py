"""Cut the controlled-hunt pages out of the 2026 Big Game brochure as a test fixture (T-17a).

The brochure itself is never committed (`data/` is git-ignored), so the parser tests run
on one multi-page excerpt of it. The pages are kept **in printed order**, which is what
the printed → physical page map is built from: the hunt tables reference their hunt-area
description pages by the brochure's own page number (`(See pg 31)`), and those pages are
part of the excerpt so `page_ref_resolves` can be exercised.

    python tests/fixtures/make_controlled_fixtures.py data/regs/seasons-rules-big-game-2026.pdf

Images are dropped, so the fixture stays small and byte-stable.
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

from pypdf import PdfReader, PdfWriter

#: 1-based PDF pages of the 2026 brochure, chosen for what each one proves:
#: 22/23 deer tables + the 13,226 checksum header + footnote legend, 27 the four-window
#: rows, 32/33 deer hunt-area descriptions, 52 elk antlered (2032), 54 elk (2111, whose
#: text-layer line merges with its neighbour), 60-62 elk descriptions, 65/68 pronghorn and
#: its descriptions, 73/74 both bear tables (fall + spring 2027) and their descriptions.
CONTROLLED_PAGES = (22, 23, 27, 32, 33, 52, 54, 60, 61, 62, 65, 68, 73, 74)
FIXTURE_DIR = Path(__file__).resolve().parent
FIXTURE = FIXTURE_DIR / "bg2026_controlled.pdf"


def main(source: str) -> int:
    reader = PdfReader(source)
    writer = PdfWriter()
    for number in CONTROLLED_PAGES:
        page = writer.add_page(reader.pages[number - 1])
        page.images  # noqa: B018 - populates the image list before it is replaced
        for image in list(page.images):
            try:
                image.replace(image.image.resize((1, 1)))
            except Exception:  # an image pdfplumber can't decode is left alone
                pass
    writer.compress_identical_objects()
    with FIXTURE.open("wb") as handle:
        writer.write(handle)
    digest = hashlib.sha256(FIXTURE.read_bytes()).hexdigest()
    print(
        f"{FIXTURE.name}  {len(CONTROLLED_PAGES)} pages"
        f"  {FIXTURE.stat().st_size / 1024:.0f} KB  {digest[:12]}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(
        main(sys.argv[1] if len(sys.argv) > 1 else "data/regs/seasons-rules-big-game-2026.pdf")
    )
