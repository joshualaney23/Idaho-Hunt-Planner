"""Cut the elk A/B zone pages out of the 2026 Big Game brochure as fixtures (T-12).

The zone pages are rotated calendar **artwork**: no ruled table, a tan title band and
month strip, hairlines between rows, and entries centred across two month columns. The
brochure is never committed (`data/` is git-ignored), so the parser tests run on
one-page excerpts of it that keep that exact layout.

    python tests/fixtures/make_elk_fixtures.py data/regs/seasons-rules-big-game-2026.pdf

The pages were chosen for the traps they carry (§12 T-12a findings):

* **p. 38** — Panhandle + Palouse: nine and six seasons, `See archers caution Page 36`
  and a per-block `Note A` legend;
* **p. 41** — Middle Fork + Salmon: one printed window carrying **two** sexes split by
  unit (`Units 20A and 26: antlered only  Unit 27: brow-tined bulls only`) and a capped
  B-tag quota sub-row;
* **p. 43** — Lemhi + Beaverhead: zones with **no B tag** (`No B Tags in this Zone`) and
  in-entry unit restrictions under two windows of the same weapon;
* **p. 46** — Boise River + Smoky-Bennett: `antlerless or spike only` (the phrase a
  shortest-match sex regex silently reads as `antlerless`) and an `Oct 1 - Dec 31`
  window spanning three month columns;
* **p. 50** — Diamond Creek: the document-level `ELK Notes:` legend (`Notes 1-4`) every
  other page's `See Note N, Page 49` points at.

Images are dropped, so the fixtures stay small and byte-stable.
"""

from __future__ import annotations

import sys
from pathlib import Path

from pypdf import PdfReader

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests.fixtures.make_deer_fixtures import write_page  # noqa: E402

#: 1-based PDF page → fixture name.
PAGES = {38: "bg2026_elk_p38", 41: "bg2026_elk_p41", 43: "bg2026_elk_p43",
         46: "bg2026_elk_p46", 50: "bg2026_elk_p50"}  # fmt: skip
FIXTURE_DIR = Path(__file__).resolve().parent


def main(source: str) -> int:
    reader = PdfReader(source)
    for number, name in PAGES.items():
        path = FIXTURE_DIR / f"{name}.pdf"
        digest = write_page(reader, number, path)
        print(f"{path.name}  {path.stat().st_size / 1024:6.0f} KB  {digest[:12]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(
        main(sys.argv[1] if len(sys.argv) > 1 else "data/regs/seasons-rules-big-game-2026.pdf")
    )
