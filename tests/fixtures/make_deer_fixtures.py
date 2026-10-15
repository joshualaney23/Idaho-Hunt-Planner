"""Cut the general deer pages out of the 2026 Big Game brochure as test fixtures (T-11).

The brochure itself is never committed (`data/` is git-ignored), so the parser tests run
on one-page excerpts of it. Each page keeps its real printed layout — ruled table, side
tab, merged Notes cells — which is what the parser has to cope with.

    python tests/fixtures/make_deer_fixtures.py data/regs/seasons-rules-big-game-2026.pdf

Images are dropped (the deer photos on p. 12 are a megabyte each and no parser reads
them), so the fixtures stay small and byte-stable.
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

from pypdf import PdfReader, PdfWriter

#: 1-based PDF page numbers of the general deer tables in the 2026 brochure (§12 T-11).
DEER_PAGES = (12, 13, 14, 15, 16, 18, 19, 20, 21)
FIXTURE_DIR = Path(__file__).resolve().parent


def write_page(reader: PdfReader, number: int, path: Path) -> str:
    """Write one page of `reader` to `path` without its images; return its sha256."""
    writer = PdfWriter()
    page = writer.add_page(reader.pages[number - 1])
    page.images  # noqa: B018 - populates the image list before it is replaced
    for image in list(page.images):
        try:
            image.replace(image.image.resize((1, 1)))
        except Exception:  # an image pdfplumber can't decode is left alone
            pass
    writer.compress_identical_objects()
    with path.open("wb") as handle:
        writer.write(handle)
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main(source: str) -> int:
    reader = PdfReader(source)
    for number in DEER_PAGES:
        path = FIXTURE_DIR / f"bg2026_deer_p{number:02d}.pdf"
        digest = write_page(reader, number, path)
        print(f"{path.name}  {path.stat().st_size / 1024:6.0f} KB  {digest[:12]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(
        main(sys.argv[1] if len(sys.argv) > 1 else "data/regs/seasons-rules-big-game-2026.pdf")
    )
