"""Cut the bear, lion and wolf season pages out of the 2026 brochure as fixtures (T-13).

The brochure itself is never committed (`data/` is git-ignored), so the parser tests run
on one-page excerpts of it: one bear page (the hardest one — units that the fixed side
crop used to mangle, merged Notes cells and a wrapped unit list), one lion page and the
wolf hunting page, which prints `Statewide` and a continuation row.

    python tests/fixtures/make_bear_lion_wolf_fixtures.py data/regs/seasons-rules-big-game-2026.pdf

Images are dropped, so the fixtures stay small and byte-stable.
"""

from __future__ import annotations

import sys
from pathlib import Path

from pypdf import PdfReader

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests.fixtures.make_deer_fixtures import write_page  # noqa: E402

#: 1-based PDF page → fixture name (§12 T-13). Bear p. 72 is the one the fixed 40 pt
#: crop corrupted (`60A` → `0A`); lion p. 76 and wolf p. 81 carry the other shapes.
PAGES = {72: "bg2026_bear_p72", 76: "bg2026_lion_p76", 81: "bg2026_wolf_p81"}
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
