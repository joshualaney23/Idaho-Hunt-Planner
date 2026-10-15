"""Controlled-hunt tables, DESIGN.md §12 T-17a (productionises `docs/spike_controlled_parser.py`).

Scope: every controlled-hunt table of a brochure — in the 2026 Big Game PDF that is
deer, elk, pronghorn and black bear. Pages are found by content (`Hunt No.` in the page
text), never by page number, and the column layout, footnote legend, checksum header and
species numbering are all described by a `Brochure` value, so T-23 (turkey), T-25
(moose/sheep/goat) and T-26 (swans) reuse this module with their own spec.

Two independent extraction paths (§8.7 principle 3):

* **Path A — ruled tables.** `page.cell_tables`: pdfplumber's `find_tables` run on the
  page *without* its superscript chars, so the footnote marker no longer glues onto the
  area id (`1-1a` → `1-1`). The markers are read back per cell from `page.small_chars`,
  which keeps the unfiltered geometry.
* **Path B — text layer.** `page.words`: rows are rebuilt from word positions, with the
  markers still inline (`1-1a`), and the columns are cut at the whitespace after the
  date range. It never looks at a ruled line.

Both paths produce the same `HuntRow` key — hunt number, area, markers, tags, dates —
and rows that disagree are staged with `extract_agree = 0` for review (§8.4). The Notes
column exists in path B only: pdfplumber's row geometry drops every Notes cell of these
tables (2026: 0 of 469 rows carry one), which is the same merged-cell failure T-11 found.
"""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field, replace
from functools import partial
from typing import Any

from planner import db
from planner.dates import to_iso
from planner.importer import (
    ParseContext,
    ParsedSeason,
    ParserSpec,
    UnitRef,
    common,
    register,
)
from planner.importer.checks import DateCell, SeasonRef, SectionFacts, TwoPath
from planner.importer.extract import Cell, CellTable, Page, Word
from planner.importer.textlayer import (
    TextLine,
    column_blocks,
    group_lines,
    join_words,
    split_bands,
)

# ───────── brochure description (everything PDF-specific lives here) ─────────

#: `(13,226 Tags Plus Unlimited Tags)`, `(240 Tags)` — a printed checksum total.
TOTAL_RE = re.compile(r"\(([\d,]+)\s+Tags", re.IGNORECASE)
#: The footnote legend printed under a hunt table.
LEGEND_RE = re.compile(r"This hunt includes", re.IGNORECASE)
#: Legend sentence → the meaning the parser gives that marker. Never a hard-coded letter.
LEGEND_MEANINGS: tuple[tuple[str, str], ...] = (
    (r"other units or parts of other units", "includes_other_units"),
    (r"only a portion of this unit", "portion_only"),
)
#: A controlled hunt area id as printed: `11`, `11A`, `1-1`, `36A-1X`, `24X`.
AREA_RE = re.compile(r"(?P<area>\d{1,2}[A-Z]?(?:-\d+)?[A-Z]*)(?P<mark>[a-z]?)")
#: The GMU an area id starts with — all T-17a stages until T-17b resolves the real list.
BASE_UNIT_RE = re.compile(r"^(\d{1,2}[A-C]?)")
#: `(See pg 31)` — a pointer to the hunt-area description page (printed page number).
PAGE_REF_RE = re.compile(r"\(\s*See\s+pg\.?\s*(\d+)\s*\)", re.IGNORECASE)
#: `See Note 1, Page 28` / `See Notes 1 and 3, Page 28` — numbered notes printed elsewhere.
NOTE_REF_RE = re.compile(
    r"See\s+Notes?\s+([\d,\s]+(?:and\s+\d+)?)\s*,?\s*Page\s+(\d+)", re.IGNORECASE
)
#: A numbered note at the bottom of a page (`1. Short range weapons only …`).
NOTE_ITEM_RE = re.compile(r"^(\d+)\.\s+(.*)$")
#: The brochure's own page number in the page footer, printed left or right of the title
#: band and **outside the side crop** — so it is read from `Page.footer`, not `Page.text`.
FOOTER_RE = re.compile(
    r"idfg\.idaho\.gov\s*(?P<right>\d{1,3})\s*$|^\s*(?P<left>\d{1,3})\s+Idaho Big Game",
    re.IGNORECASE | re.MULTILINE,
)
#: `Hunt Area 1-1 —` on a hunt-area description page (§8.7 `page_ref_resolves`).
AREA_ENTRY = r"Hunt Area {area}\s*[—–-]"
#: Tags cell meaning "no quota" (`seasons.tag_quota` NULL).
UNLIMITED_RE = re.compile(r"\bunlimited\b", re.IGNORECASE)


@dataclass(frozen=True)
class Brochure:
    """Everything a controlled-hunt table parser needs to know about one brochure."""

    kind: str
    #: First digit of a hunt number → species id (`"1"` → deer).
    species_by_prefix: Mapping[str, str]
    #: Printed species word in a table title → species id, for the title/number cross-check.
    species_words: tuple[tuple[str, str], ...]
    header: tuple[str, ...] = ("hunt no.", "controlled hunt areas", "tags", "season dates", "notes")
    total_re: re.Pattern[str] = TOTAL_RE
    legend_re: re.Pattern[str] = LEGEND_RE
    legend_meanings: tuple[tuple[str, str], ...] = LEGEND_MEANINGS
    area_re: re.Pattern[str] = AREA_RE
    footer_re: re.Pattern[str] = FOOTER_RE
    area_entry: str = AREA_ENTRY

    def species_of(self, hunt_number: str) -> str | None:
        return self.species_by_prefix.get(hunt_number[:1])

    def section(self, species_id: str) -> str:
        return f"controlled_{species_id}"


#: The 2026 Big Game brochure: deer 1xxx, elk 2xxx, pronghorn 4xxx, black bear 8xxx.
BIG_GAME = Brochure(
    kind="big_game",
    species_by_prefix={"1": "deer", "2": "elk", "4": "pronghorn", "8": "black_bear"},
    species_words=(
        ("pronghorn", "pronghorn"),
        ("black bear", "black_bear"),
        ("bear", "black_bear"),
        ("deer", "deer"),
        ("elk", "elk"),
    ),
)

#: Printed title phrase → `seasons.sex`. Longest/most specific first.
SEX_TITLES: tuple[tuple[str, str], ...] = (
    ("either sex", "either"),
    ("doe or fawn", "antlerless"),
    ("antlerless", "antlerless"),
    ("antlered", "antlered"),
)
#: Printed title phrase → `seasons.hunt_class` (a controlled table is `controlled` by default).
CLASS_TITLES: tuple[tuple[str, str], ...] = (("outfitter", "outfitter"), ("extra", "extra"))
#: Printed title phrase → restriction code (§5.1).
FLAG_TITLES: tuple[tuple[str, str], ...] = (
    ("youth", "youth_only"),
    ("outfitter", "outfitter_only"),
)
#: The sentence under a title that names other tags and would hijack the mapping.
TITLE_DISCLAIMER_RE = re.compile(r"\btags?\s+(?:is|are)\s+valid\b", re.IGNORECASE)
#: Distance (pt) a superscript may poke above its cell's top edge.
MARK_PAD_PT = 4.0
#: How far below its last date line a table's rows may still carry text (path B).
TABLE_END_PAD_PT = 8.0
#: Slack (pt) past the mirrored right edge of the date column (word boxes overhang).
DATE_COLUMN_PAD_PT = 12.0


@dataclass(frozen=True)
class TitleInfo:
    """What a controlled table's title says about every row under it."""

    text: str
    species_id: str | None
    sex: str
    weapon: str
    hunt_class: str
    restrictions: tuple[str, ...]
    year: int | None
    total: int | None
    matched: bool


def parse_title(text: str, spec: Brochure = BIG_GAME) -> TitleInfo:
    """`"2026 Controlled Hunts - Youth Only - Extra Antlerless Deer"` → its row attributes.

    An unrecognised title is returned with `matched = False`: the caller fails the whole
    table rather than defaulting its sex or weapon (D3, §12 T-17a).
    """
    raw = re.sub(r"\s+", " ", (text or "").replace("\n", " ")).strip()
    head = raw
    if m := TITLE_DISCLAIMER_RE.search(head):
        head = head[: m.start()].strip()
    low = head.lower()
    species = next((sp for word, sp in spec.species_words if word in low), None)
    sex = next((value for phrase, value in SEX_TITLES if phrase in low), "any")
    weapon = next((code for phrase, code in common.WEAPON_TITLES if phrase in low), "any")
    hunt_class = next((code for phrase, code in CLASS_TITLES if phrase in low), "controlled")
    flags = tuple(code for phrase, code in FLAG_TITLES if phrase in low)
    year = int(m.group(0)) if (m := re.search(r"\b(?:19|20)\d{2}\b", raw)) else None
    total = int(m.group(1).replace(",", "")) if (m := spec.total_re.search(raw)) else None
    # A controlled table always says so, and must map to at least one printed attribute.
    matched = "controlled" in low and bool(
        species or sex != "any" or weapon != "any" or hunt_class != "controlled" or flags
    )
    return TitleInfo(
        text=raw,
        species_id=species,
        sex=sex,
        weapon=weapon,
        hunt_class=hunt_class,
        restrictions=flags,
        year=year,
        total=total,
        matched=matched,
    )


# ───────── one parsed table row ─────────


@dataclass(frozen=True)
class HuntRow:
    """One controlled hunt as read from a table, before validation."""

    hunt_number: str
    area: str
    marks: tuple[str, ...]
    tags_text: str
    #: Every season window printed for this hunt, in order. A hunt can print several
    #: (2026 deer 1123: four windows on four lines of one ruled row).
    dates_texts: tuple[str, ...]
    page: int
    title: TitleInfo = field(
        default_factory=lambda: TitleInfo(
            "", None, "any", "any", "controlled", (), None, None, False
        )
    )
    description_page: int | None = None  # printed page number, resolved at finalize
    notes: str = ""
    bbox: tuple[float, float, float, float] | None = None
    leftovers: tuple[str, ...] = ()

    @property
    def tag_quota(self) -> int | None:
        text = self.tags_text.replace(",", "").strip()
        return int(text) if text.isdigit() else None

    @property
    def tags_key(self) -> str:
        """The Tags cell as a comparable value.

        The cell can carry an application rule beside the number (`Unlimited 1st choice
        only`), which the two paths read in a different word order, so only the quota
        itself is compared — the full text stays in `source_text`.
        """
        if UNLIMITED_RE.search(self.tags_text):
            return "unlimited"
        return re.sub(r"[^\d]", "", self.tags_text) or "none"

    @property
    def key(self) -> tuple[str, str, tuple[str, ...], str, tuple[str, ...]]:
        """What the two extraction paths must agree on (§8.7)."""
        return (
            self.hunt_number,
            self.area.upper(),
            self.marks,
            self.tags_key,
            tuple(re.sub(r"\s+", " ", d).strip() for d in self.dates_texts),
        )

    @property
    def dates_text(self) -> str:
        return " ".join(self.dates_texts)

    @property
    def source_text(self) -> str:
        parts = (self.hunt_number, self.area, self.tags_text, self.dates_text, self.notes)
        return " | ".join(p for p in parts if p)


# ───────── path A: ruled tables, superscripts filtered out ─────────


def _text(cell: Cell | str | None) -> str:
    value = cell.text if isinstance(cell, Cell) else cell
    return re.sub(r"[ \t]+", " ", (value or "").replace("\n", " ")).strip()


def _header_index(table: CellTable, spec: Brochure) -> int | None:
    """Index of the `Hunt No. | … | Notes` header row, or None when there is none."""
    for index, row in enumerate(table):
        names = [_text(c).lower().rstrip(".") for c in row]
        if names and names[0].startswith("hunt no") and any(n == "notes" for n in names):
            return index
    return None


def _title_text(table: CellTable, header_index: int) -> str:
    """Everything printed above the header row, in reading order."""
    seen: list[str] = []
    for row in table[:header_index]:
        for cell in row:
            value = _text(cell)
            if value and value not in seen:
                seen.append(value)
    return " ".join(seen)


def marks_in(bbox: Sequence[float], small: Sequence[Word], keys: Iterable[str]) -> tuple[str, ...]:
    """Legend markers printed inside one cell, read from the unfiltered page geometry.

    Superscripts sit above the baseline and can poke out of the cell's top edge, so the
    box is padded upwards (`MARK_PAD_PT`).
    """
    x0, top, x1, bottom = bbox
    wanted = set(keys)
    found = {
        w.text
        for w in small
        if w.text in wanted and x0 - 1 <= w.x0 <= x1 + 1 and top - MARK_PAD_PT <= w.top <= bottom
    }
    return tuple(sorted(found))


def rows_from_tables(
    page: Page, spec: Brochure, carried: TitleInfo | None, legend_keys: Iterable[str]
) -> tuple[list[HuntRow], TitleInfo | None, list[str]]:
    """Path A. Returns the rows, the title to carry forward and any unreadable titles."""
    out: list[HuntRow] = []
    bad_titles: list[str] = []
    keys = list(legend_keys)
    for table in page.cell_tables:
        header_index = _header_index(table, spec)
        if header_index is None:
            continue
        text = _title_text(table, header_index)
        info = parse_title(text, spec) if text else carried
        if info is None or not info.matched:
            bad_titles.append(text or "(no title, nothing to carry forward)")
            continue
        carried = info
        for row in table[header_index + 1 :]:
            cells = list(row) + [None] * (len(spec.header) - len(row))
            number = _text(cells[0])
            if not re.fullmatch(r"\d{4}", number):
                # A continuation row: no hunt number, one more season window for the
                # hunt above it (2026 deer 1123 prints four). Never dropped (D3).
                extra = _text(cells[3])
                if out and extra and common.parse_date_ranges(extra, 2000):
                    out[-1] = replace(out[-1], dates_texts=(*out[-1].dates_texts, extra))
                continue
            area_cell = cells[1]
            area_text = _text(area_cell)
            page_ref = PAGE_REF_RE.search(area_text)
            area = PAGE_REF_RE.sub("", area_text).strip()
            bbox = area_cell.bbox if isinstance(area_cell, Cell) else None
            out.append(
                HuntRow(
                    hunt_number=number,
                    area=area,
                    marks=marks_in(bbox, page.small_chars, keys) if bbox else (),
                    tags_text=_text(cells[2]),
                    dates_texts=(_text(cells[3]),),
                    notes=_text(cells[4]) or _notes_beside(page, row, table[header_index]),
                    page=page.number,
                    title=info,
                    description_page=int(page_ref.group(1)) if page_ref else None,
                    bbox=_row_bbox(row),
                )
            )
    return out, carried, bad_titles


def _notes_beside(page: Page, row: Sequence[Cell | None], header: Sequence[Cell | None]) -> str:
    """The Notes cell read from the page words inside the row's own ruled box.

    pdfplumber drops the Notes cell of every row of these tables (2026: 469 of 469) the
    same way it drops T-11's merged cells, so the text is taken from the words that sit
    inside the row band, right of the Notes column's left edge — which the *header* row
    does have a cell for.
    """
    notes_cell = header[4] if len(header) > 4 else None
    box = _row_bbox(row)
    if not isinstance(notes_cell, Cell) or box is None:
        return ""
    top, bottom = box[1], box[3]
    inside = [
        w
        for w in page.words
        if w.x0 >= notes_cell.x0 - 1 and top <= (w.top + w.bottom) / 2 <= bottom
    ]
    return join_words(inside)


def _row_bbox(row: Sequence[Cell | None]) -> tuple[float, float, float, float] | None:
    boxes = [c for c in row if isinstance(c, Cell)]
    if not boxes:
        return None
    return (
        min(c.x0 for c in boxes),
        min(c.top for c in boxes),
        max(c.x1 for c in boxes),
        max(c.bottom for c in boxes),
    )


# ───────── path B: the text layer's word positions ─────────


def _is_header_line(line: TextLine, spec: Brochure) -> bool:
    words = [w.text.lower().rstrip(".") for w in sorted(line.words, key=lambda w: w.x0)]
    return words[:2] == ["hunt", "no"] and "notes" in words


def _date_end(words: Sequence[Word], license_year: int) -> tuple[int, str] | None:
    """Index of the word that completes the row's date range, and the range text.

    Pure text-layer reading: words are consumed left to right until the accumulated text
    *ends* with a complete `Mon D - Mon D`. Everything to the right of that word is the
    Notes column (the brochure prints no other column after the dates).
    """
    text = ""
    for index, word in enumerate(words):
        text = f"{text} {word.text}".strip()
        for found in common.parse_date_ranges(text, license_year):
            if found.span[1] == len(text):
                return index, found.text
    return None


def _overlaps(word: Word, other: Word, share: float = 0.5) -> bool:
    """Do two words sit on the same printed line (vertical overlap ≥ `share`)?"""
    height = min(word.bottom - word.top, other.bottom - other.top) or 1.0
    overlap = min(word.bottom, other.bottom) - max(word.top, other.top)
    return overlap / height >= share


def column_bounds(header: TextLine) -> list[float]:
    """Column boundaries from the header labels, which are centred over their columns.

    `Hunt No. | Controlled Hunt Areas | Tags | Season Dates | Notes` → the midpoints
    between consecutive label centres. The boundary between the date and Notes columns
    is *not* usable this way (the Notes text is centred and starts left of its label),
    so the date range is located by content instead.
    """
    blocks = column_blocks(header.words, gutter=8.0)
    centres = [(x0 + x1) / 2 for x0, x1 in blocks]
    bounds = [(a + b) / 2 for a, b in zip(centres, centres[1:], strict=False)]
    if len(bounds) >= 3:
        # The date column's right edge, mirrored around its (centred) label: a note can
        # print a date range of its own, and the Notes column starts right of this.
        bounds.insert(3, bounds[2] + 2 * (centres[3] - bounds[2]) + DATE_COLUMN_PAD_PT)
    return bounds


def _is_junk_line(text: str, spec: Brochure) -> bool:
    """Is this printed line something other than a table row?

    A row band runs to the next hunt number, so the last band of a table also catches
    the footnote legend, the numbered-notes block, the page footer and the *next* table's
    title — and one of those titles (the spring bear application period) prints a date
    range of its own.
    """
    stripped = text.strip()
    return bool(
        spec.legend_re.search(stripped)
        or spec.footer_re.search(stripped)
        or NOTE_ITEM_RE.match(stripped)
        or stripped.lower().startswith("notes:")
        or parse_title(stripped, spec).matched
    )


MONTH_WORD_RE = re.compile(
    r"^(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?$", re.IGNORECASE
)
#: How many words after a month name a printed date range may span.
DATE_SPAN_WORDS = 8


@dataclass(frozen=True)
class DateLine:
    """One printed season window of the text path, with where it sits on the page."""

    text: str
    top: float
    bottom: float

    @property
    def centre(self) -> float:
        return (self.top + self.bottom) / 2


def date_lines(words: Sequence[Word], spec: Brochure, license_year: int) -> list[DateLine]:
    """Every date range printed in `words`, anchored on its month name.

    The baseline of each range is taken from the month word's own vertical overlap, so
    two rows' date lines can never be chained together — which a line-clustering pass
    does as soon as a tall Notes cell bridges them.
    """
    out: list[DateLine] = []
    consumed: set[int] = set()
    for anchor in sorted(words, key=lambda w: (w.top, w.x0)):
        if id(anchor) in consumed or not MONTH_WORD_RE.match(anchor.text):
            continue
        baseline = sorted((w for w in words if _overlaps(w, anchor)), key=lambda w: w.x0)
        if _is_junk_line(" ".join(w.text for w in baseline), spec):
            continue
        at = baseline.index(anchor)
        found = _date_end(baseline[at : at + DATE_SPAN_WORDS], license_year)
        if found is None:
            continue
        used = baseline[at : at + found[0] + 1]
        consumed.update(id(w) for w in used)
        out.append(
            DateLine(
                text=found[1],
                top=min(w.top for w in used),
                bottom=max(w.bottom for w in used),
            )
        )
    return sorted(out, key=lambda d: (d.top, d.text))


def assign_rows(dates: Sequence[DateLine], hunts: Sequence[Word]) -> list[list[int]]:
    """Which date lines belong to each hunt number, in printed order.

    A hunt's cells are printed **vertically centred** on it, so a row with four season
    windows has lines both above and below its own number and reaches past the midpoint
    to its neighbour. The date lines are therefore cut into consecutive runs, one per
    hunt, so that every hunt number ends up as centred as possible in the run it labels
    — the same dynamic program T-11 used for merged unit cells.
    """
    if not hunts or not dates:
        return [[] for _ in hunts]
    centres = [(w.top + w.bottom) / 2 for w in hunts]
    n, m = len(dates), len(hunts)

    def cost(first: int, last: int, hunt: int) -> float:
        return abs((dates[first].top + dates[last].bottom) / 2 - centres[hunt])

    # dp[k][i] = best cost of giving the first k hunts the first i date lines
    best = [[float("inf")] * (n + 1) for _ in range(m + 1)]
    back = [[0] * (n + 1) for _ in range(m + 1)]
    best[0][0] = 0.0
    for k in range(1, m + 1):
        for i in range(k, n - (m - k) + 1):
            for j in range(k - 1, i):
                if best[k - 1][j] == float("inf"):
                    continue
                total = best[k - 1][j] + cost(j, i - 1, k - 1)
                if total < best[k][i]:
                    best[k][i], back[k][i] = total, j
    out: list[list[int]] = [[] for _ in hunts]
    if best[m][n] == float("inf"):  # fewer date lines than hunts: fall back to nearest
        for index, found in enumerate(dates):
            out[min(range(m), key=lambda h: abs(centres[h] - found.centre))].append(index)
        return out
    end = n
    for k in range(m, 0, -1):
        start = back[k][end]
        out[k - 1] = list(range(start, end))
        end = start
    return out


def row_of(value: float, spans: Sequence[tuple[float, float]]) -> int:
    """The row whose vertical span `value` falls in, else the nearest one."""

    def distance(index: int) -> float:
        top, bottom = spans[index]
        return 0.0 if top <= value <= bottom else min(abs(value - top), abs(value - bottom))

    return min(range(len(spans)), key=distance)


def rows_from_text(
    page: Page, spec: Brochure, carried: TitleInfo | None, license_year: int
) -> tuple[list[HuntRow], TitleInfo | None]:
    """Path B: the same rows rebuilt from word positions alone (§8.7 principle 3).

    Every date range and every area/tags word is assigned to the **nearest hunt number**
    by vertical centre: the cells of a row are printed centred on it, so a row with four
    season windows reaches past the midpoint to its neighbour and a tall Notes cell
    reaches above its own number — neither a whitespace band nor a midpoint cut gets
    those rows right. Columns come from the header labels, and the table ends just below
    its last date line, which keeps the legend, the footer and the next title out. The
    Notes text is left to path A (it is not compared).
    """
    lines = sorted(group_lines(page.words), key=lambda ln: ln.top)
    headers = [i for i, ln in enumerate(lines) if _is_header_line(ln, spec)]
    out: list[HuntRow] = []
    for position, start in enumerate(headers):
        header = lines[start]
        bottom = lines[headers[position + 1]].top if position + 1 < len(headers) else float("inf")
        info = _title_above(lines[:start], spec) or carried
        if info is None or not info.matched:
            continue
        carried = info
        bounds = column_bounds(header)
        if len(bounds) < 4:
            continue
        region = [w for w in page.words if header.bottom <= w.top < bottom]
        dates = date_lines([w for w in region if bounds[2] <= w.cx < bounds[3]], spec, license_year)
        if not dates:
            continue
        cut = max(d.bottom for d in dates) + TABLE_END_PAD_PT
        hunts = sorted(
            (
                w
                for w in region
                if re.fullmatch(r"\d{4}", w.text) and w.x1 <= bounds[0] and w.top < cut
            ),
            key=lambda w: w.top,
        )
        if not hunts:
            continue
        owned = assign_rows(dates, hunts)
        spans = [
            (
                min([hunt.top, *(dates[i].top for i in owned[index])]),
                max([hunt.bottom, *(dates[i].bottom for i in owned[index])]),
            )
            for index, hunt in enumerate(hunts)
        ]

        area: dict[int, list[Word]] = {}
        tags: dict[int, list[Word]] = {}
        for word in region:
            centre = (word.top + word.bottom) / 2
            if word in hunts or centre >= cut:
                continue
            if bounds[0] <= word.cx < bounds[1]:
                area.setdefault(row_of(centre, spans), []).append(word)
            elif bounds[1] <= word.cx < bounds[2]:
                tags.setdefault(row_of(centre, spans), []).append(word)
        for index, hunt in enumerate(hunts):
            if not owned[index]:
                continue
            out.append(
                _text_row(
                    hunt.text,
                    join_words(area.get(index, [])),
                    join_words(tags.get(index, [])),
                    tuple(dates[i].text for i in owned[index]),
                    page.number,
                    info,
                )
            )
    return out, carried


def _title_above(lines: Sequence[TextLine], spec: Brochure) -> TitleInfo | None:
    """The title block printed directly above a header line, if there is one."""
    kept: list[str] = []
    for band in reversed(split_bands(list(lines))):
        text = " ".join(ln.text for ln in band)
        info = parse_title(text, spec)
        kept.insert(0, text)
        if info.matched:
            return parse_title(" ".join(kept), spec)
        if len(kept) > 4:
            break
    return None


def _text_row(
    number: str,
    area_text: str,
    tags: str,
    dates: tuple[str, ...],
    page_number: int,
    info: TitleInfo,
) -> HuntRow:
    """Build a path-B row from its column texts (the footnote marker is still inline)."""
    page_ref = PAGE_REF_RE.search(area_text)
    rest = PAGE_REF_RE.sub(" ", area_text).strip()
    match = AREA_RE.match(rest)
    return HuntRow(
        hunt_number=number,
        area=match.group("area") if match else rest.split(" ")[0],
        marks=(match.group("mark"),) if match and match.group("mark") else (),
        tags_text=tags,
        dates_texts=dates,
        page=page_number,
        title=info,
        description_page=int(page_ref.group(1)) if page_ref else None,
    )


# ───────── the footnote legend ─────────


def legend_for(page: Page, spec: Brochure) -> dict[str, str]:
    """Marker letter → meaning, read from the legend printed at the foot of the page.

    The marker itself is a superscript printed *left of* the text and often outside the
    `SIDE_CROP_PT` crop, so it is matched by geometry against `page.small_chars`.
    """
    out: dict[str, str] = {}
    for line in group_lines(page.words):
        if not spec.legend_re.search(line.text):
            continue
        meaning = next(
            (name for pattern, name in spec.legend_meanings if re.search(pattern, line.text, re.I)),
            None,
        )
        if meaning is None:
            continue
        keys = [
            w.text
            for w in page.small_chars
            if w.text.isalpha()
            and len(w.text) == 1
            and w.x1 <= line.x0 + 2
            and line.top - MARK_PAD_PT <= w.top <= line.bottom
        ]
        for key in keys:
            out[key] = meaning
    return out


# ───────── notes → restriction codes (§5.1) ─────────

_POINTER_RE = re.compile(r"\b(?:see\s+)?(?:notes?|pages?|pg)\.?\s*(?:\d+|and|[,\s-])*", re.I)
_NOISE_RE = re.compile(r"\b(?:only|all|in|the|of|this|unit|units)\b|[\s,.;—–-]+", re.I)


def notes_codes(text: str) -> tuple[list[str], list[str]]:
    """Restriction codes for a Notes cell, plus the phrases nothing explained (D3).

    Anything the closed vocabulary can't account for adds `see_notes`, which keeps the
    unit yellow instead of green — including a pointer to a numbered note, whose text is
    attached verbatim but never interpreted.
    """
    value = _text(text)
    if not value:
        return [], []
    quals = common.qualifiers(value)
    codes = list(quals.codes)
    explained = value
    for pattern, _ in common.QUALIFIER_PATTERNS:
        explained = re.sub(pattern, " ", explained, flags=re.IGNORECASE)
    explained = _NOISE_RE.sub(" ", _POINTER_RE.sub(" ", explained))
    unmatched = list(quals.unmatched)
    if explained.strip() or NOTE_REF_RE.search(value):
        # A pointer to a numbered note is never interpreted here: the note's text is
        # attached verbatim at finalize, and the condition itself stays unexplained.
        unmatched.append(value)
    if unmatched and "see_notes" not in codes:
        codes.append("see_notes")
    return codes, unmatched


# ───────── per-document state ─────────


@dataclass
class DocState:
    """What the controlled parsers share while reading one document."""

    started: bool = False
    rows: dict[int, list[tuple[HuntRow, bool]]] = field(default_factory=dict)
    legend: dict[str, str] = field(default_factory=dict)
    titles: dict[str, TitleInfo | None] = field(default_factory=dict)
    printed_pages: dict[int, int] = field(default_factory=dict)  # printed → physical
    page_text: dict[int, str] = field(default_factory=dict)
    hunt_pages: list[int] = field(default_factory=list)
    notes_items: dict[int, dict[str, str]] = field(default_factory=dict)
    totals: list[tuple[int, str, int]] = field(default_factory=list)  # page, title, amount
    bad_titles: list[dict[str, object]] = field(default_factory=list)
    missing_marks: list[dict[str, object]] = field(default_factory=list)
    text_only: list[str] = field(default_factory=list)
    unmatched_rows: list[dict[str, object]] = field(default_factory=list)
    disagreements: list[dict[str, object]] = field(default_factory=list)
    date_tokens: int = 0
    seen: set[int] = field(default_factory=set)


_STATE: dict[int, DocState] = {}


def state_for(ctx: ParseContext) -> DocState:
    return _STATE.setdefault(ctx.document_id, DocState())


def begin(ctx: ParseContext) -> None:
    """Reset per-document state once per import (every species parser calls this)."""
    state = _STATE.get(ctx.document_id)
    if state is None or state.started:
        _STATE[ctx.document_id] = DocState()


def observe(page: Page, state: DocState, spec: Brochure) -> None:
    """Collect the document-wide facts every section's checks need, once per page."""
    if page.number in state.seen:
        return
    state.seen.add(page.number)
    state.started = True
    state.page_text[page.number] = page.text
    if m := spec.footer_re.search(page.footer):
        printed = m.group("right") or m.group("left")
        if printed:
            state.printed_pages[int(printed)] = page.number
    items: dict[str, str] = {}
    for line in page.lines:
        if m := NOTE_ITEM_RE.match(line.strip()):
            items[m.group(1)] = m.group(2).strip()
        elif items:  # a note wraps onto the following line
            last = next(reversed(items))
            if not spec.legend_re.search(line) and not spec.footer_re.match(line.strip()):
                items[last] = f"{items[last]} {line.strip()}".strip()
    if items:
        state.notes_items[page.number] = items


def is_hunt_page(page: Page) -> bool:
    """A page that prints a controlled-hunt table (found by content, never by number)."""
    return not page.rotated and "Hunt No." in page.text


def page_rows(page: Page, ctx: ParseContext, spec: Brochure) -> list[tuple[HuntRow, bool]]:
    """Parse one page with both paths and compare them; cached per page and document."""
    state = state_for(ctx)
    observe(page, state, spec)
    if page.number in state.rows:
        return state.rows[page.number]
    if not is_hunt_page(page):
        return []
    state.hunt_pages.append(page.number)
    legend = legend_for(page, spec) or state.legend
    state.legend = legend or state.legend

    rules, carried_a, bad = rows_from_tables(page, spec, state.titles.get("rules"), legend)
    text, carried_b = rows_from_text(page, spec, state.titles.get("text"), ctx.license_year)
    state.titles["rules"], state.titles["text"] = carried_a, carried_b
    state.bad_titles += [{"page": page.number, "title": t} for t in bad]
    state.totals += [
        (page.number, row.title.text, row.title.total)
        for row in _first_per_title(rules)
        if row.title.total is not None
    ]

    by_number = {row.hunt_number: row for row in text}
    out: list[tuple[HuntRow, bool]] = []
    for row in rules:
        peer = by_number.get(row.hunt_number)
        if peer is None:
            # The text layer merged this row's line with a neighbour's: the table path is
            # authoritative, but the number must still be printed somewhere on the page.
            agree = row.hunt_number in page.text
            state.text_only.append(row.hunt_number)
            if not agree:
                state.disagreements.append(
                    {"page": page.number, "hunt": row.hunt_number, "text": None}
                )
        else:
            agree = row.key == peer.key
            if not agree:
                state.disagreements.append(
                    {
                        "page": page.number,
                        "hunt": row.hunt_number,
                        "table": list(row.key),
                        "text": list(peer.key),
                    }
                )
        unknown = [m for m in row.marks if m not in legend]
        if unknown:
            state.missing_marks.append(
                {"page": page.number, "hunt": row.hunt_number, "markers": unknown}
            )
        # The Notes cell is path A's alone: pdfplumber drops it from the ruled output and
        # path B never reads it, so it is not part of the comparison (§8.7).
        out.append((row, agree))

    state.rows[page.number] = out
    return out


def _first_per_title(rows: Sequence[HuntRow]) -> list[HuntRow]:
    seen: set[str] = set()
    out: list[HuntRow] = []
    for row in rows:
        if row.title.text not in seen:
            seen.add(row.title.text)
            out.append(row)
    return out


# ───────── rows → staged seasons ─────────


@dataclass(frozen=True)
class Window:
    """One resolved season window of a hunt, with the text nothing explained (D3)."""

    start: dt.date
    end: dt.date
    text: str


def resolve_dates(row: HuntRow, ctx: ParseContext) -> tuple[list[Window], list[str]]:
    """Every season window of the row, plus the date text no window consumed.

    Dates are read with the license year's Jul→Jun rule, then shifted to the year the
    table title prints when it prints one (`2027 Controlled Hunts` → 2027 dates, spring
    bear → 2027). An end before the start rolls into the next year (`parse_date_ranges`).
    """
    windows: list[Window] = []
    leftovers: list[str] = []
    for text in row.dates_texts:
        ranges = common.parse_date_ranges(text, ctx.license_year)
        leftovers += common.leftover_date_text(text, ranges)
        if not ranges:
            leftovers.append(text)
            continue
        for found in ranges:
            start, end = found.start, found.end
            year = row.title.year
            if year is not None and start.year != year and start.month >= 7:
                shift = year - start.year
                start = start.replace(year=start.year + shift)
                end = end.replace(year=end.year + shift)
            windows.append(Window(start, end, found.text))
    return windows, leftovers


def base_unit(area: str) -> str | None:
    """The GMU an area id starts with (`36A-1X` → `36A`), staged as partial until T-17b."""
    m = BASE_UNIT_RE.match(area.strip())
    return m.group(1) if m else None


def to_parsed(
    row: HuntRow, ctx: ParseContext, spec: Brochure, *, agree: bool
) -> list[ParsedSeason]:
    """A compared `HuntRow` as the season rows the pipeline stages — one per window.

    A hunt can print several season windows (2026 deer 1123 prints four), so one hunt
    number can produce several `seasons` rows. A window whose dates can't be read is
    never dropped: it is staged as a failing row so review sees it (D3).
    """
    species = spec.species_of(row.hunt_number) or f"unknown_prefix_{row.hunt_number[:1]}"
    section = spec.section(species)
    windows, leftovers = resolve_dates(row, ctx)
    codes, unmatched = notes_codes(row.notes)
    for code in row.title.restrictions:
        if code not in codes:
            codes.append(code)
    legend = _STATE[ctx.document_id].legend
    meanings = {legend.get(m) for m in row.marks}
    if "portion_only" in meanings and "portion_of_unit" not in codes:
        codes.append("portion_of_unit")
    note = (
        f"Controlled hunt area {row.area}"
        f"{' — includes other units' if 'includes_other_units' in meanings else ''}"
        " (exact units resolved by T-17b)"
    )
    units = [
        UnitRef(unit_id=base_unit(row.area) or row.area, coverage="partial", coverage_note=note)
    ]
    common_fields: dict[str, Any] = {
        "species_id": species,
        "tag_type_id": "controlled",
        "hunt_class": row.title.hunt_class,
        "weapon": row.title.weapon,
        "sex": row.title.sex,
        "units": units,
        "hunt_number": row.hunt_number,
        "hunt_area": row.area,
        "tag_quota": row.tag_quota,
        "source_page": row.page,
        "source_text": row.source_text,
        "source_bbox": list(row.bbox) if row.bbox else None,
        "section": section,
    }
    if not windows:
        day = dt.date(ctx.license_year, 7, 1)
        return [
            ParsedSeason(
                start=day,
                end=day,
                restrictions=["see_notes"],
                notes=f"unparsed season dates: {row.dates_text!r}",
                extract_agree=False,
                confidence=0.0,
                **common_fields,
            )
        ]
    return [
        ParsedSeason(
            start=window.start,
            end=window.end,
            restrictions=codes,
            notes=row.notes or None,
            extract_agree=agree,
            confidence=common.confidence(
                unparsed_date_text=len(leftovers),
                unmatched_qualifiers=len(unmatched),
            ),
            **common_fields,
        )
        for window in windows
    ]


def parse_page(
    page: Page, ctx: ParseContext, *, spec: Brochure, species: str
) -> Iterator[ParsedSeason]:
    """Yield one species' controlled hunts from a page (the other species' rows are skipped)."""
    for row, agree in page_rows(page, ctx, spec):
        if spec.species_of(row.hunt_number) != species:
            continue
        yield from to_parsed(row, ctx, spec, agree=agree)


# ───────── §8.7 checks, hunt areas and note texts ─────────


def _rows_for(state: DocState, spec: Brochure, species: str) -> list[tuple[HuntRow, bool]]:
    return [
        (row, agree)
        for page in sorted(state.rows)
        for row, agree in state.rows[page]
        if spec.species_of(row.hunt_number) == species
    ]


def text_hunt_numbers(state: DocState, spec: Brochure, species: str) -> set[str]:
    """4-digit hunt numbers that start a line on a hunt-table page (§8.7 completeness)."""
    out: set[str] = set()
    for number in state.hunt_pages:
        for line in state.page_text.get(number, "").splitlines():
            m = re.match(r"\s*(\d{4})\b", line)
            if m is None or spec.species_of(m.group(1)) != species:
                continue
            # A table title starts with the year it covers ("2026 Controlled Hunts …"),
            # which reads as a hunt number of whatever species that digit belongs to.
            # A printed hunt row always carries its season dates on the same line.
            if parse_title(line, spec).matched or not common.parse_date_ranges(line, 2000):
                continue
            out.add(m.group(1))
    return out


def checksum_scopes(
    state: DocState, spec: Brochure, species: str
) -> list[tuple[str, int, list[HuntRow]]]:
    """Each printed `(N Tags …)` total with the rows it covers, in printed order.

    A total opens a scope that runs until the next total of the same species, so the
    2026 bear page's `(240 Tags)` covers its fall tables and `(150 Tags)` the spring one.
    """
    rows = [row for row, _ in _rows_for(state, spec, species)]
    totals = [
        (page, title, amount)
        for page, title, amount in state.totals
        if any(r.page == page and r.title.text == title for r in rows)
    ]
    scopes: list[tuple[str, int, list[HuntRow]]] = []
    for index, (page, title, amount) in enumerate(totals):
        start = next((i for i, r in enumerate(rows) if r.page == page and r.title.text == title), 0)
        if index + 1 < len(totals):
            nxt_page, nxt_title, _ = totals[index + 1]
            stop = next(
                (i for i, r in enumerate(rows) if r.page == nxt_page and r.title.text == nxt_title),
                len(rows),
            )
        else:
            stop = len(rows)
        scopes.append((title, amount, rows[start:stop]))
    return scopes


def _area_entries(state: DocState, spec: Brochure, area: str, physical: int | None) -> bool:
    if physical is None:
        return False
    pattern = re.compile(spec.area_entry.format(area=re.escape(area)), re.IGNORECASE)
    return bool(pattern.search(state.page_text.get(physical, "")))


def write_hunt_areas(ctx: ParseContext, state: DocState, spec: Brochure, species: str) -> int:
    """Write this species' `hunt_areas` rows (§5), replacing the document's previous ones."""
    db.exec(
        "DELETE FROM hunt_areas WHERE document_id = ? AND species_id = ?",
        ctx.document_id,
        species,
    )
    seen: dict[tuple[int, str], tuple[int | None, bool, bool]] = {}
    for row, _ in _rows_for(state, spec, species):
        found = resolve_dates(row, ctx)[0]
        year = found[0].start.year if found else ctx.license_year
        meanings = {state.legend.get(m) for m in row.marks}
        physical = state.printed_pages.get(row.description_page or -1)
        previous = seen.get((year, row.area), (None, False, False))
        seen[(year, row.area)] = (
            physical or previous[0],
            previous[1] or "includes_other_units" in meanings,
            previous[2] or "portion_only" in meanings,
        )
    db.exec_many(
        "INSERT OR REPLACE INTO hunt_areas (license_year, species_id, area_id, description_page,"
        " includes_other_units, portion_only, document_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            (year, species, area, page, int(other), int(portion), ctx.document_id)
            for (year, area), (page, other, portion) in sorted(seen.items())
        ],
    )
    return len(seen)


def attach_note_texts(ctx: ParseContext, state: DocState, spec: Brochure, species: str) -> int:
    """Append the text of every `See Note N, Page P` a row points at (§12 T-17a).

    Runs at finalize, because the referenced page is usually read after the table.
    """
    updates: list[tuple[str, int, str]] = []
    for row, _ in _rows_for(state, spec, species):
        extra: list[str] = []
        for m in NOTE_REF_RE.finditer(row.notes):
            physical = state.printed_pages.get(int(m.group(2)))
            items = state.notes_items.get(physical or -1, {})
            for number in re.findall(r"\d+", m.group(1)):
                if text := items.get(number):
                    extra.append(f"Note {number} (p.{m.group(2)}): {text}")
        if extra:
            updates.append((f"{row.notes} — " + " ".join(extra), ctx.document_id, row.hunt_number))
    db.exec_many(
        "UPDATE seasons SET notes = ? WHERE document_id = ? AND hunt_number = ? AND"
        " status = 'staged'",
        updates,
    )
    return len(updates)


def finalize(ctx: ParseContext, *, spec: Brochure, species: str) -> SectionFacts | None:
    """What one `controlled_<species>` section measured, plus the rows it writes.

    The §8.7 invariants themselves live in `planner.importer.checks` (T-22a); this
    collects their evidence and does the section's own database work (`hunt_areas` and
    the note texts attached to staged seasons).
    """
    state = state_for(ctx)
    pairs = _rows_for(state, spec, species)
    rows = [row for row, _ in pairs]
    if not rows:
        return None
    # One resolution per row — a hunt number can legitimately repeat, and proving it
    # does not is `no_duplicate_keys`' job, not this one's.
    resolved = [(row, resolve_dates(row, ctx)) for row in rows]
    keys = [
        (row.hunt_number, to_iso(window.start), to_iso(window.end))
        for row, (wins, _) in resolved
        for window in wins
    ]
    date_cells = [
        DateCell(
            page=row.page,
            texts=tuple(row.dates_texts),
            seasons=len(wins),
            label=row.hunt_number,
            leftovers=tuple(leftovers),
            bbox=row.bbox,
            must_produce=True,  # every controlled hunt prints at least one season window
        )
        for row, (wins, leftovers) in resolved
    ]
    first_windows = [
        SeasonRef(label=row.hunt_number, title=row.title.text, start=wins[0].start)
        for row, (wins, _) in resolved
        if wins
    ]
    scopes = checksum_scopes(state, spec, species)
    tag_totals = [
        {
            "title": title,
            "header": amount,
            "parsed": sum(r.tag_quota or 0 for r in scope),
            "unlimited": sum(1 for r in scope if r.tag_quota is None),
            "rows": len(scope),
        }
        for title, amount, scope in scopes
    ]
    refs = sorted(
        {(row.area, row.description_page) for row in rows if row.description_page is not None}
    )
    unresolved = [
        {"area": area, "printed_page": printed, "physical": state.printed_pages.get(printed)}
        for area, printed in refs
        if not _area_entries(state, spec, area, state.printed_pages.get(printed))
    ]
    areas = write_hunt_areas(ctx, state, spec, species)
    attached = attach_note_texts(ctx, state, spec, species)
    return SectionFacts(
        section=spec.section(species),
        two_path=TwoPath(
            compared=len(rows),
            disagreeing=len(
                [d for d in state.disagreements if spec.species_of(str(d["hunt"])) == species]
            ),
            disagreements=[
                d for d in state.disagreements if spec.species_of(str(d["hunt"])) == species
            ],
            merged_lines=[n for n in state.text_only if spec.species_of(n) == species],
        ),
        date_cells=date_cells,
        keys=keys,
        key_label="(hunt number, start, end)",
        hunt_numbers_in_text=sorted(text_hunt_numbers(state, spec, species)),
        hunt_numbers_parsed=[row.hunt_number for row in rows],
        tag_totals=tag_totals,
        first_windows=first_windows,
        page_refs=len(refs),
        page_refs_unresolved=unresolved,
        unknown_marks=[
            m for m in state.missing_marks if spec.species_of(str(m["hunt"])) == species
        ],
        unreadable_titles=list(state.bad_titles),
        corrections_pages=_corrections_pages(state),
        hunt_areas={
            "distinct": len({row.area for row in rows}),
            "areas": areas,
            "attached": attached,
        },
    )


def _corrections_pages(state: DocState) -> list[int]:
    return [
        number
        for number, text in sorted(state.page_text.items())
        if re.search(r"^\s*corrections?\b", text, re.IGNORECASE | re.MULTILINE)
    ]


# ───────── registration (one section per species) ─────────


def register_brochure(spec: Brochure) -> list[ParserSpec]:
    """Register one `controlled_<species>` parser per species of this brochure."""
    out: list[ParserSpec] = []
    for species in dict.fromkeys(spec.species_by_prefix.values()):
        section = spec.section(species)
        out.append(
            register(
                ParserSpec(
                    name=section,
                    kind=spec.kind,
                    match=_matches,
                    fn=partial(parse_page, spec=spec, species=species),
                    section=section,
                    begin=begin,
                    finalize=partial(finalize, spec=spec, species=species),
                )
            )
        )
    return out


def _matches(page: Page) -> bool:
    """Every upright page: the hunt tables plus the pages whose footers, notes and hunt
    area descriptions the §8.7 checks resolve against."""
    return not page.rotated


PARSERS = register_brochure(BIG_GAME)
