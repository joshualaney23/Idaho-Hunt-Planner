"""Text-layer geometry shared by the season parsers, DESIGN.md §8.7 path B.

`find_tables()` is path A. Path B must reach the same rows **without** looking at a
single ruled line, so it rebuilds the printed layout from word positions alone:

* `group_lines` — words that sit on one printed line,
* `split_bands` — lines grouped into row bands by the vertical whitespace between them,
* `column_blocks` / `column_of` — columns found from the horizontal gutters,
* `join_words` — the words of one cell back into reading order.

T-11 (general deer) introduced these; T-17a (controlled hunts) reuses them, so they
live here rather than in either parser.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from planner.importer.extract import Word

#: Vertical gap (pt) between text lines that starts a new row band.
#: Measured on the 2026 brochure: within a row ≤ 1.2 pt (lines overlap), between rows ≥ 6.3 pt.
ROW_GAP_PT = 4.0
#: Horizontal whitespace (pt) that separates two columns.
COLUMN_GUTTER_PT = 6.0


@dataclass(frozen=True)
class TextLine:
    """Words that sit on one printed line, in reading order."""

    words: list[Word]

    @property
    def top(self) -> float:
        return min(w.top for w in self.words)

    @property
    def bottom(self) -> float:
        return max(w.bottom for w in self.words)

    @property
    def x0(self) -> float:
        return min(w.x0 for w in self.words)

    @property
    def x1(self) -> float:
        return max(w.x1 for w in self.words)

    @property
    def text(self) -> str:
        return " ".join(w.text for w in sorted(self.words, key=lambda w: w.x0))


def group_lines(words: Sequence[Word], tolerance: float = 2.0) -> list[TextLine]:
    """Words → printed lines, by vertical overlap of their boxes."""
    lines: list[list[Word]] = []
    bounds: list[tuple[float, float]] = []
    for word in sorted(words, key=lambda w: (w.top, w.x0)):
        for i, (top, bottom) in enumerate(bounds):
            if word.top < bottom - tolerance and word.bottom > top + tolerance:
                lines[i].append(word)
                bounds[i] = (min(top, word.top), max(bottom, word.bottom))
                break
        else:
            lines.append([word])
            bounds.append((word.top, word.bottom))
    return [TextLine(ws) for ws in lines]


def split_bands(lines: Sequence[TextLine], gap: float = ROW_GAP_PT) -> list[list[TextLine]]:
    """Lines → row bands, split wherever `gap` points of whitespace separate them.

    Unit cells are printed vertically centred, so a row's lines overlap each other while
    consecutive rows are always separated — this is path B's row geometry (no ruled lines).
    """
    bands: list[list[TextLine]] = []
    reach = 0.0
    for line in sorted(lines, key=lambda ln: ln.top):
        if not bands or line.top - reach >= gap:
            bands.append([line])
            reach = line.bottom
        else:
            bands[-1].append(line)
            reach = max(reach, line.bottom)
    return bands


def column_blocks(
    words: Sequence[Word], gutter: float = COLUMN_GUTTER_PT
) -> list[tuple[float, float]]:
    """Horizontal text blocks, i.e. the columns, found from the whitespace gutters."""
    blocks: list[tuple[float, float]] = []
    for word in sorted(words, key=lambda w: w.x0):
        if blocks and word.x0 - blocks[-1][1] < gutter:
            blocks[-1] = (blocks[-1][0], max(blocks[-1][1], word.x1))
        else:
            blocks.append((word.x0, word.x1))
    return blocks


def column_of(x: float, blocks: Sequence[tuple[float, float]]) -> int:
    """Index of the block `x` falls in, else the nearest one (-1 without blocks)."""
    for i, (x0, x1) in enumerate(blocks):
        if x0 <= x <= x1:
            return i
    return min(
        range(len(blocks)),
        key=lambda i: min(abs(x - blocks[i][0]), abs(x - blocks[i][1])),
        default=-1,
    )


def join_words(items: Sequence[Word]) -> str:
    """Words of one cell in reading order, re-joining words hyphenated across lines.

    Lines are re-clustered inside the cell: a page-wide line grouping chains words
    across columns, and a raw `top` sort scrambles a wrapped sentence (2026 p. 12:
    "Second deer tag may **not** be used…").
    """
    parts: list[str] = []
    for line in sorted(group_lines(items), key=lambda ln: ln.top):
        text = line.text
        if parts and len(parts[-1]) > 1 and parts[-1].endswith("-") and text[:1].islower():
            parts[-1] = parts[-1][:-1] + text
        else:
            parts.append(text)
    return " ".join(parts)
