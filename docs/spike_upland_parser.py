"""Spike: read the Upland brochure's upland game bird seasons (T-14 part b).

Run it against the real 2026 & 2027 brochure::

    .venv/bin/python docs/spike_upland_parser.py data/regs/2026/<sha>.pdf

It prints, per bird section, what each of the two independent extraction paths read
(path A: the page's text layer in reading order; path B: word geometry) and the
blocks/seasons they produced, so the disagreements are visible before any of this is
productionised in `planner/importer/upland.py`.

Findings that drove the parser (see docs/upland_parser_notes.md):

* the bird pages are **prose**, not tables: `AREA n` / `STATEWIDE` heading, a
  definition paragraph, a `Seasons` heading, one line per year of the two-year
  brochure, then `Daily Bag Limit` / `Possession Limit` dotted-leader lines;
* the vertical section tab sits at x1 <= 28.5 on even pages and x0 >= 565 on odd ones,
  but the body column starts at x0 = 35.5 — the fixed 40 pt side crop cuts it, so the
  parser must read `page.full_text` / `page.full_words` (T-13's tab-only crop);
* page 10 (sharp-tailed grouse) prints a county map **over** the text column: its
  labels interleave into the reading-order text. They are either tiny (2.7 pt) or huge
  (24 pt) and always ALL CAPS, which is what each path filters on - independently.
"""

from __future__ import annotations

import re
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from planner.importer import common  # noqa: E402
from planner.importer.extract import Page, Word, iter_pages  # noqa: E402

# ───────── section headings ─────────

HEADINGS: tuple[tuple[str, str], ...] = (
    (r"^FOREST GROUSE\b", "forest_grouse"),
    (r"^CALIFORNIA AND BOBWHITE QUAIL\b", "quail"),
    (r"^CHUKAR AND GRAY PARTRIDGE\b", "chukar+gray_partridge"),
    (r"^SAGE-GROUSE\b", "sage_grouse"),
    (r"^SHARP-TAILED GROUSE\b", "sharptail"),
    (r"^PHEASANTS?\b", "pheasant"),
)

AREA_RE = re.compile(r"^AREA\s+(\d+)\b")
STATEWIDE_RE = re.compile(r"^STATEWIDE\b")
SEASONS_RE = re.compile(r"^Seasons\b")
YOUTH_RE = re.compile(r"^Youth Hunt Season\b")
DAILY_RE = re.compile(r"^Daily Bag Limit\b")
POSS_RE = re.compile(r"^Possession Limit\b")
STOP_RE = re.compile(r"^(Distribution and Habitat Use|[A-Z][a-z].*©)")
YEAR_LINE_RE = re.compile(r"^(\d{4})(?:\s*[–—-]\s*(\d{4}))?\s*[:—–-]\s*(.+)$")
ORPHAN_TAIL_RE = re.compile(r"^[.\s]*[\d,]+\s*(?:in the aggregate|roosters?\*?)?\s*$")
UPPER_JUNK_RE = re.compile(r"\b[A-Z][A-Z’'-]{2,}\b")


@dataclass
class Block:
    species: str
    area: str
    definition: list[str] = field(default_factory=list)
    season_lines: list[str] = field(default_factory=list)
    youth_lines: list[str] = field(default_factory=list)
    daily: str | None = None
    possession: str | None = None
    notes: list[str] = field(default_factory=list)

    def text(self) -> str:
        return " ".join(self.definition)


# ───────── path A: the text layer in reading order ─────────


def _strip_map_labels(line: str) -> str:
    """Drop the ALL-CAPS county-map labels the sharp-tailed page prints over the text.

    Content rule only — path A never looks at a coordinate. Headings are matched
    before this runs, so `AREA 1` and `STATEWIDE` survive.
    """
    out = UPPER_JUNK_RE.sub(" ", line)
    return re.sub(r"\s{2,}", " ", out).strip(" ,")


def lines_path_a(page: Page) -> list[str]:
    raw = [ln.strip() for ln in page.full_text.splitlines() if ln.strip()]
    out: list[str] = []
    for ln in raw:
        if ORPHAN_TAIL_RE.match(ln) and out:
            # A dotted leader wrapped away from its label ("............6" printed on
            # its own line because a map label split the line): it continues the most
            # recent limit line that has no number yet.
            for i in range(len(out) - 1, -1, -1):
                if (DAILY_RE.match(out[i]) or POSS_RE.match(out[i])) and not re.search(
                    r"\d", out[i]
                ):
                    out[i] = f"{out[i]} {ln.lstrip('. ')}"
                    break
            continue
        out.append(ln)
    return out


# ───────── path B: word geometry ─────────

BODY_MIN_H, BODY_MAX_H = 8.0, 13.0


def lines_path_b(page: Page, column_x1: float = 310.0) -> list[str]:
    words = [
        w
        for w in page.full_words
        if w.x1 <= column_x1 and BODY_MIN_H <= (w.bottom - w.top) <= BODY_MAX_H
    ]
    rows: dict[int, list[Word]] = defaultdict(list)
    for w in words:
        rows[round((w.top + w.bottom) / 2 / 4)].append(w)
    out: list[str] = []
    for key in sorted(rows):
        g = sorted(rows[key], key=lambda w: w.x0)
        out.append(" ".join(w.text for w in g).strip())
    merged: list[str] = []
    for ln in out:
        if ORPHAN_TAIL_RE.match(ln) and merged:
            for i in range(len(merged) - 1, -1, -1):
                if (DAILY_RE.match(merged[i]) or POSS_RE.match(merged[i])) and not re.search(
                    r"\d", merged[i]
                ):
                    merged[i] = f"{merged[i]} {ln.lstrip('. ')}"
                    break
            continue
        merged.append(ln)
    return merged


# ───────── shared block structure ─────────


def species_of(lines: list[str]) -> str | None:
    head = " ".join(lines[:2])
    for pat, species in HEADINGS:
        if re.match(pat, head) or any(re.match(pat, ln) for ln in lines[:3]):
            return species
    return None


def blocks(lines: list[str], species: str) -> list[Block]:
    out: list[Block] = []
    cur: Block | None = None
    mode = "definition"
    for ln in lines[1:]:
        if STOP_RE.match(ln):
            break
        if m := AREA_RE.match(ln):
            cur = Block(species, m.group(1))
            out.append(cur)
            mode = "definition"
            continue
        if STATEWIDE_RE.match(ln):
            cur = Block(species, "statewide")
            out.append(cur)
            mode = "definition"
            continue
        if cur is None:
            continue
        if SEASONS_RE.match(ln):
            mode = "seasons"
            continue
        if YOUTH_RE.match(ln):
            mode = "youth"
            continue
        if DAILY_RE.match(ln):
            cur.daily = _limit_value(ln)
            continue
        if POSS_RE.match(ln):
            cur.possession = _limit_value(ln)
            continue
        if mode == "definition":
            cur.definition.append(ln)
        elif mode == "seasons":
            cur.season_lines.append(ln)
        elif mode == "youth":
            cur.youth_lines.append(ln)
    return out


def _limit_value(line: str) -> str:
    value = re.sub(r"^(Daily Bag Limit|Possession Limit)", "", line)
    value = value.strip(" .")
    value = _strip_map_labels(value)
    return re.sub(r"\s{2,}", " ", value).strip(" .,")


def seasons_of(block: Block, license_year: int) -> list[tuple[str, list[common.DateRange]]]:
    out = []
    for ln in [*block.season_lines, *block.youth_lines]:
        m = YEAR_LINE_RE.match(ln)
        if not m:
            out.append((ln, []))
            continue
        ranges = common.parse_date_ranges(m.group(3), license_year)
        out.append((ln, ranges))
    return out


# ───────── driver ─────────


def main(path: str) -> None:
    for page in iter_pages(path):
        lines_a = lines_path_a(page)
        species = species_of(lines_a)
        if species is None:
            continue
        lines_b = lines_path_b(page)
        print(f"\n═══ page {page.number}  species={species}")
        for label, lines in (("A", lines_a), ("B", lines_b)):
            bs = blocks(lines, species)
            print(f"  path {label}: {len(bs)} blocks")
            for b in bs:
                print(f"    AREA {b.area}: {b.text()[:120]!r}")
                print(f"      daily={b.daily!r} possession={b.possession!r}")
                for line, ranges in seasons_of(b, 2026):
                    got = ", ".join(f"{r.start}→{r.end}" for r in ranges)
                    print(f"      {line[:70]!r:<74} {got}")


if __name__ == "__main__":
    main(sys.argv[1])
