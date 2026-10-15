"""General deer season tables, DESIGN.md §12 T-11 (productionises `docs/spike_deer_parser.py`).

Scope: every **general** `Regular Deer Tag` and `White-tailed Deer Tag` table — Any
Weapon, Archery Only, Muzzleloader Only and Short Range Weapon. Controlled tables are
T-17a's and are skipped here.

Two independent extraction paths (§8.7 principle 3):

* **Path A — ruled tables.** `page.tables`, pdfplumber's `find_tables` output.
* **Path B — text layer.** `page.words`: lines are rebuilt from word positions, columns
  from the whitespace gutters under the `Unit(s) | Antlered | Antlerless | Notes`
  header, and rows from the vertical gaps between lines. It never looks at a ruled line.

Both paths produce the same `RowSeason` values, which are compared per unit group:
groups that agree are staged with `extract_agree = 1`, groups that disagree with 0 so a
human reviews them (§8.4). Path A alone is not enough to publish from: in the 2026
brochure its row geometry drops the vertically merged **Notes** cells entirely (every
notes cell on p. 12 comes back empty), so the notes text — and the restriction codes in
it — is read from path B.
"""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, field

from planner.dates import to_iso
from planner.importer import CheckResult, ParseContext, ParsedSeason, UnitRef, common, parser
from planner.importer.extract import Page, Table, Word

#: `import_checks.section` and `seasons.section` for everything this module parses.
SECTION = "deer_general"
#: Tag types this parser may emit (`Controlled` titles are T-17a's).
TAG_TYPES = frozenset({"deer_regular", "deer_whitetail"})
#: The header a general deer season table must have (§12 T-11), lower-cased.
HEADER_CELLS = ("unit(s)", "antlered", "antlerless", "notes")
#: Season columns and the `seasons.sex` each one produces.
SEX_COLUMNS: tuple[tuple[str, str], ...] = (("antlered", "antlered"), ("antlerless", "antlerless"))
#: Characters after a date range that may qualify it (§12 T-11). Clamped to the next range.
QUALIFIER_WINDOW = 60
#: Vertical gap (pt) between text lines that starts a new row band in path B.
#: Measured on the 2026 brochure: within a row ≤ 1.2 pt (lines overlap), between rows ≥ 6.3 pt.
ROW_GAP_PT = 4.0
#: Horizontal whitespace (pt) that separates two columns in path B.
COLUMN_GUTTER_PT = 6.0
#: Unit that is closed to hunting and therefore exempt from `general_coverage` (§8.7).
CLOSED_UNIT = "YNP"

_FOOTER_RE = re.compile(r"idfg\.idaho\.gov|Idaho Big Game \d{4} Seasons", re.IGNORECASE)
_NOTES_SECTION_RE = re.compile(r"^Notes:\s*$", re.IGNORECASE)
_TITLE_LINE_RE = re.compile(r"deer tag|seasons|tag is valid|permit required", re.IGNORECASE)
_PORTION_RE = re.compile(r"portion of unit(s)?\s*([0-9A-Z,\s]*)", re.IGNORECASE)
#: `See Note 3, Page 15` / `See Pages 104 - 106` — a pointer, not a condition itself.
_POINTER_RE = re.compile(
    r"\b(?:see\s+)?(?:notes?|pages?|pg)\.?\s*(?:\d+|and|[,\s-])*", re.IGNORECASE
)
#: `… may not be used in Units 10, 10A, and 12` — the units a matched qualifier applies to.
_UNIT_PHRASE_RE = re.compile(
    r"\bin (?:the )?units?\b(?:\s*(?:\d{1,2}[A-Z]?|and)[,\s]*)*", re.IGNORECASE
)
#: Words that carry no restriction of their own.
_NOISE_RE = re.compile(r"\battention\b[:,]?|\bonly\b|[\s,.;—-]+", re.IGNORECASE)
#: A numbered note this parser does not read ⇒ `see_notes` (D3).
_NOTE_REF_RE = re.compile(r"\bnotes?\s*\d", re.IGNORECASE)
#: A controlled hunt row (`1081 68A Unlimited …`) or a hunt-area description heading.
_CONTROLLED_ROW_RE = r"(?m)^\s*\d{{4}}\s+{unit}(?:-\d+[A-Z]*)?\b"
_CONTROLLED_AREA_RE = r"(?m)Hunt Area {unit}(?:-\d+[A-Z]*)?\b"


# ───────── values both paths produce ─────────


@dataclass(frozen=True)
class RowSeason:
    """One season as read from a table cell, before notes and validation are applied."""

    tag_type_id: str
    weapon: str
    units: tuple[str, ...]
    sex: str
    start: dt.date
    end: dt.date
    codes: tuple[str, ...] = ()  # restriction codes from the date cell only
    unknown_units: tuple[str, ...] = ()
    leftovers: tuple[str, ...] = ()  # date/qualifier text nothing explained (D3)
    units_text: str = ""
    cell_text: str = ""
    notes_text: str = ""
    bbox: tuple[float, float, float, float] | None = None

    @property
    def group(self) -> tuple[str, str, tuple[str, ...]]:
        """The printed table row this season came from: tag, weapon and its unit list."""
        return (self.tag_type_id, self.weapon, self.units)

    @property
    def key(self) -> tuple[str, str, tuple[str, ...], str, str, str, tuple[str, ...]]:
        """What the two paths must agree on (§8.7): units, sex, dates, cell qualifiers."""
        return (
            self.tag_type_id,
            self.weapon,
            self.units,
            self.sex,
            to_iso(self.start),
            to_iso(self.end),
            self.codes,
        )


@dataclass(frozen=True)
class DeerRow:
    """One row of a general deer table: its unit cell plus the season/notes cells."""

    units_text: str
    cells: dict[str, str]
    bbox: tuple[float, float, float, float] | None = None


@dataclass(frozen=True)
class DeerTable:
    """A general deer season table on one page."""

    title: common.TitleInfo
    rows: list[DeerRow]
    date_cell_texts: list[str] = field(default_factory=list)


# ───────── path A: pdfplumber's ruled tables ─────────


def _cell(value: str | None) -> str:
    return re.sub(r"[ \t]+", " ", (value or "").replace("\n", " ")).strip()


def _header_columns(row: Sequence[str | None]) -> dict[int, str] | None:
    """Column index → column name, or None when `row` is not the `Unit(s) …` header."""
    names = [_cell(c).lower().rstrip(".") for c in row]
    if not names or not names[0].startswith("unit"):
        return None
    columns: dict[int, str] = {0: "units"}
    for i, name in enumerate(names[1:], 1):
        for wanted in HEADER_CELLS[1:]:
            if name.startswith(wanted):
                columns[i] = wanted
    if "antlered" not in columns.values() or "antlerless" not in columns.values():
        return None
    return columns


def tables_from_rules(tables: Iterable[Table], carried: common.TitleInfo | None) -> list[DeerTable]:
    """Path A: general deer tables read from pdfplumber's ruled-table output."""
    out: list[DeerTable] = []
    for raw in tables:
        rows = [r for r in raw if any(_cell(c) for c in r)]
        header_index = next((i for i, r in enumerate(rows) if _header_columns(r) is not None), None)
        if header_index is None:
            continue
        columns = _header_columns(rows[header_index]) or {}
        title_text = " ".join(_cell(c) for c in rows[0] if _cell(c)) if header_index else ""
        info = common.parse_title(title_text) if title_text else carried
        if info is None or not _is_general_deer(info):
            continue
        carried = info
        body: list[DeerRow] = []
        date_cells: list[str] = []
        for row in rows[header_index + 1 :]:
            cells = {name: _cell(row[i]) for i, name in columns.items() if i < len(row)}
            units_text = cells.pop("units", "")
            if not units_text and not any(cells.get(c) for c, _ in SEX_COLUMNS):
                continue
            date_cells += [cells.get(c, "") for c, _ in SEX_COLUMNS]
            body.append(DeerRow(units_text=units_text, cells=cells))
        out.append(DeerTable(title=info, rows=body, date_cell_texts=date_cells))
    return out


def _is_general_deer(info: common.TitleInfo) -> bool:
    """Is this title a *general* Regular/White-tailed deer table (not a controlled one)?"""
    return info.tag_type_id in TAG_TYPES and info.hunt_class == "general"


# ───────── path B: the text layer's word positions ─────────


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


def assign_unit_labels(bands: Sequence[tuple[bool, float, float]]) -> list[int]:
    """Which unit label owns each row band — path B's answer to merged unit cells.

    A unit cell is printed **vertically centred** on its row, so a band with no unit
    text may belong to the label above it *or* below it (2026 p. 15: unit 2's three
    date bands surround its label). Bands are therefore cut into rows so that every
    label ends up as centred as possible in the row it labels, which is a small DP
    over the split points. `bands` is `(has_unit_text, top, bottom)` in page order.
    """
    labels = [i for i, (has_units, _, _) in enumerate(bands) if has_units]
    if not labels:
        return [-1] * len(bands)
    n = len(bands)

    def centre(first: int, last: int) -> float:
        return (bands[first][1] + bands[last][2]) / 2

    starts: list[list[int]] = [[0]]
    starts += [list(range(labels[k - 1] + 1, labels[k] + 1)) for k in range(1, len(labels))]
    starts += [[n]]
    # dp[start of row k] = (cost of rows 0..k-1, the row starts that got there)
    dp: dict[int, tuple[float, tuple[int, ...]]] = {0: (0.0, (0,))}
    for k, label in enumerate(labels):
        nxt: dict[int, tuple[float, tuple[int, ...]]] = {}
        for start, (cost, path) in dp.items():
            for end in starts[k + 1]:
                if end <= start:
                    continue
                total = cost + abs(centre(start, end - 1) - centre(label, label))
                if total < nxt.get(end, (float("inf"), ()))[0]:
                    nxt[end] = (total, (*path, end))
        dp = nxt
    _, cuts = min(dp.values(), key=lambda item: item[0])
    owners = [-1] * n
    for k, label in enumerate(labels):
        for i in range(cuts[k], cuts[k + 1]):
            owners[i] = label
    return owners


def _column_of(x: float, blocks: Sequence[tuple[float, float]]) -> int:
    for i, (x0, x1) in enumerate(blocks):
        if x0 <= x <= x1:
            return i
    return min(
        range(len(blocks)),
        key=lambda i: min(abs(x - blocks[i][0]), abs(x - blocks[i][1])),
        default=-1,
    )


def _name_blocks(header: TextLine, blocks: Sequence[tuple[float, float]]) -> dict[int, str] | None:
    """Block index → column name, by which header word's centre falls inside it."""
    names: dict[int, str] = {}
    for word in header.words:
        label = word.text.lower().rstrip(".")
        if label in HEADER_CELLS:
            index = _column_of(word.cx, blocks)
            if index >= 0:
                names[index] = label
    if "unit(s)" not in names.values() or "antlered" not in names.values():
        return None
    return {i: ("units" if n == "unit(s)" else n) for i, n in names.items()}


def _is_header_line(line: TextLine) -> bool:
    labels = [w.text.lower().rstrip(".") for w in sorted(line.words, key=lambda w: w.x0)]
    return labels[:3] == ["unit(s)", "antlered", "antlerless"]


def _join_words(items: Sequence[Word]) -> str:
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


def tables_from_text(page: Page, carried: common.TitleInfo | None) -> list[DeerTable]:
    """Path B: the same tables rebuilt from word positions alone (§8.7 principle 3)."""
    lines = sorted(group_lines(page.words), key=lambda ln: ln.top)
    header_indexes = [i for i, ln in enumerate(lines) if _is_header_line(ln)]
    out: list[DeerTable] = []
    for position, start in enumerate(header_indexes):
        header = lines[start]
        stop = header_indexes[position + 1] if position + 1 < len(header_indexes) else len(lines)
        body = _table_lines(lines[start + 1 : stop])
        info = _text_title(lines[:start]) or carried
        if info is None or not _is_general_deer(info):
            continue
        carried = info
        blocks = column_blocks([w for ln in [header, *body] for w in ln.words])
        names = _name_blocks(header, blocks)
        if names is None:
            continue
        rows: list[DeerRow] = []
        date_cells: list[str] = []
        cells_per_band: list[tuple[dict[str, str], tuple[float, float, float, float]]] = []
        for band in split_bands(body):
            cells: dict[str, list[Word]] = {}
            for line in band:
                for word in line.words:
                    name = names.get(_column_of(word.cx, blocks))
                    if name:
                        cells.setdefault(name, []).append(word)
            text = {name: _join_words(items) for name, items in cells.items()}
            bbox = (
                min(w.x0 for ln in band for w in ln.words),
                min(ln.top for ln in band),
                max(w.x1 for ln in band for w in ln.words),
                max(ln.bottom for ln in band),
            )
            cells_per_band.append((text, bbox))
        owners = assign_unit_labels(
            [(bool(t.get("units", "").strip()), b[1], b[3]) for t, b in cells_per_band]
        )
        for index, (text, bbox) in enumerate(cells_per_band):
            owner = owners[index]
            date_cells += [text.get(c, "") for c, _ in SEX_COLUMNS]
            rows.append(
                DeerRow(
                    units_text=cells_per_band[owner][0].get("units", "") if owner >= 0 else "",
                    cells={k: v for k, v in text.items() if k != "units"},
                    bbox=bbox,
                )
            )
        out.append(DeerTable(title=info, rows=rows, date_cell_texts=date_cells))
    return out


def _table_lines(lines: Sequence[TextLine]) -> list[TextLine]:
    """The body lines of one table: stop at its footer, a `Notes:` block or the next title."""
    body: list[TextLine] = []
    for band in split_bands(lines):
        text = " ".join(ln.text for ln in band)
        if _FOOTER_RE.search(text) or _NOTES_SECTION_RE.match(text.strip()):
            break
        has_dates = bool(common.parse_date_ranges(text, 2026))
        has_units = bool(common.parse_units(band[0].text).units)
        if not has_dates and not has_units:
            break
        body += band
    return body


def _text_title(before: Sequence[TextLine]) -> common.TitleInfo | None:
    """The title block printed directly above a header line, if there is one."""
    kept: list[str] = []
    for line in reversed(before):
        if not _TITLE_LINE_RE.search(line.text):
            break
        kept.append(line.text)
    if not kept:
        return None
    return common.parse_title(" ".join(reversed(kept)))


# ───────── cells → seasons ─────────


def _qualifier_window(cell: str, this: common.DateRange, following: common.DateRange | None) -> str:
    """The text that qualifies one date range: up to 60 chars, never past the next range."""
    end = this.span[1]
    limit = following.span[0] if following is not None else len(cell)
    return cell[end : min(limit, end + QUALIFIER_WINDOW)]


def table_seasons(table: DeerTable, ctx: ParseContext) -> list[RowSeason]:
    """Every season in one table, carrying the unit list down continuation rows (§12 T-11)."""
    info = table.title
    tag_type = info.tag_type_id or ""
    out: list[RowSeason] = []
    units: tuple[str, ...] = ()
    unknown: tuple[str, ...] = ()
    units_text = ""
    for row in table.rows:
        if row.units_text.strip():
            parsed = common.parse_units(row.units_text, ctx.units)
            units = tuple(parsed.units)
            unknown = tuple(parsed.unknown)
            units_text = row.units_text
        if not units and not unknown:
            continue
        notes_text = _cell(row.cells.get("notes", ""))
        for column, sex in SEX_COLUMNS:
            cell = row.cells.get(column, "")
            if common.is_no_season(cell):
                continue
            ranges = common.parse_date_ranges(cell, ctx.license_year)
            leftovers = list(common.leftover_date_text(cell, ranges))
            for i, found in enumerate(ranges):
                window = _qualifier_window(
                    cell, found, ranges[i + 1] if i + 1 < len(ranges) else None
                )
                quals = common.qualifiers(window)
                out.append(
                    RowSeason(
                        tag_type_id=tag_type,
                        weapon=info.weapon,
                        units=units,
                        sex=sex,
                        start=found.start,
                        end=found.end,
                        codes=tuple(quals.codes),
                        unknown_units=unknown,
                        leftovers=tuple(leftovers + list(quals.unmatched)),
                        units_text=units_text,
                        cell_text=cell,
                        notes_text=notes_text,
                        bbox=row.bbox,
                    )
                )
    return out


def notes_codes(notes: str, units: Sequence[str]) -> tuple[list[str], dict[str, str]]:
    """Restriction codes and per-unit partial coverage read from a Notes cell.

    A pointer to a numbered note (`See Note 3, Page 15`) always adds `see_notes`: the
    note itself is not parsed, so the condition stays unexplained and the unit stays
    yellow rather than green (D3, §5.1). So does any text the closed vocabulary can't
    account for; a bare page pointer next to a qualifier it explains does not.
    """
    text = _cell(notes)
    if not text:
        return [], {}
    quals = common.qualifiers(text)
    codes = list(quals.codes)
    explained = text
    for pattern, _ in common.QUALIFIER_PATTERNS:
        explained = re.sub(pattern, " ", explained, flags=re.IGNORECASE)
    explained = _POINTER_RE.sub(" ", explained)
    explained = _UNIT_PHRASE_RE.sub(" ", explained)
    explained = _NOISE_RE.sub(" ", explained)
    unexplained = bool(explained.strip()) or bool(_NOTE_REF_RE.search(text))
    if unexplained and "see_notes" not in codes:
        codes.append("see_notes")
    partial: dict[str, str] = {}
    if match := _PORTION_RE.search(text):
        named = common.parse_units(match.group(2) or "", units).units
        for unit in named or units:
            partial[unit] = text
        if "portion_of_unit" not in codes:
            codes.append("portion_of_unit")
    return codes, partial


def to_parsed(row: RowSeason, page_number: int, *, agree: bool) -> ParsedSeason:
    """A compared `RowSeason` as the `ParsedSeason` the pipeline stages."""
    codes = list(row.codes)
    note_codes, partial = notes_codes(row.notes_text, row.units)
    for code in note_codes:
        if code not in codes:
            codes.append(code)
    # The White-tailed Deer tag is itself WT-only: the code is carried for display, and
    # `engine.RESTRICTIONS` drops it again for a holder of that tag (§5.1, §7.2).
    if row.tag_type_id == "deer_whitetail" and "whitetail_only" not in codes:
        codes.append("whitetail_only")
    refs = [
        UnitRef(
            unit_id=unit,
            coverage="partial" if unit in partial else "full",
            coverage_note=partial.get(unit),
        )
        for unit in row.units
    ]
    # An unreadable unit token is never dropped: it is staged as a failing row (D3).
    refs += [UnitRef(unit_id=token) for token in row.unknown_units]
    source = " | ".join(part for part in (row.units_text, row.cell_text, row.notes_text) if part)
    return ParsedSeason(
        species_id="deer",
        tag_type_id=row.tag_type_id,
        hunt_class="general",
        weapon=row.weapon,
        sex=row.sex,
        start=row.start,
        end=row.end,
        units=refs,
        sub_species="whitetail" if row.tag_type_id == "deer_whitetail" else None,
        restrictions=codes,
        notes=row.notes_text or None,
        source_page=page_number,
        source_text=source,
        source_bbox=list(row.bbox) if row.bbox else None,
        extract_agree=agree,
        confidence=common.confidence(
            unknown_units=len(row.unknown_units),
            unparsed_date_text=len(row.leftovers),
            unmatched_qualifiers=0,
            date_ranges=row.cell_text.count("-"),
        ),
        section=SECTION,
    )


# ───────── per-document state and the §8.7 checks ─────────


@dataclass
class DeerState:
    """What the parser carries between pages of one document."""

    title_rules: common.TitleInfo | None = None
    title_text: common.TitleInfo | None = None
    rows: int = 0
    pages: list[int] = field(default_factory=list)
    covered: set[str] = field(default_factory=set)
    unknown_units: list[tuple[int, str]] = field(default_factory=list)
    disagreements: list[dict[str, object]] = field(default_factory=list)
    compared: int = 0
    tokens: int = 0
    consumed: int = 0
    leftovers: list[dict[str, object]] = field(default_factory=list)
    controlled_text: list[str] = field(default_factory=list)


_STATE: dict[int, DeerState] = {}


def state_for(ctx: ParseContext) -> DeerState:
    """The parser's state for one document (created on first use)."""
    return _STATE.setdefault(ctx.document_id, DeerState())


def begin(ctx: ParseContext) -> None:
    """Reset per-document state — `run_import` calls this before the first page."""
    _STATE[ctx.document_id] = DeerState()


def matches(page: Page) -> bool:
    """Pages worth looking at: any un-rotated page naming a deer tag or a hunt table."""
    return not page.rotated and ("Deer" in page.text or "Hunt No." in page.text)


def _count_tokens(texts: Iterable[str], license_year: int) -> int:
    return sum(len(common.parse_date_ranges(text, license_year)) for text in texts)


def parse_page(page: Page, ctx: ParseContext) -> Iterator[ParsedSeason]:
    """Parse one page with both paths, compare them, and yield the seasons to stage."""
    state = state_for(ctx)
    if "Hunt No." in page.text and "Controlled" in page.text and "Deer" in page.text:
        state.controlled_text.append(page.text)

    rules = tables_from_rules(page.tables, state.title_rules)
    text = tables_from_text(page, state.title_text)
    if rules:
        state.title_rules = rules[-1].title
    if text:
        state.title_text = text[-1].title
    if not rules and not text:
        return
    state.pages.append(page.number)

    rows_a = [s for table in rules for s in table_seasons(table, ctx)]
    rows_b = [s for table in text for s in table_seasons(table, ctx)]
    by_group_b: dict[tuple[str, str, tuple[str, ...]], list[RowSeason]] = {}
    for row in rows_b:
        by_group_b.setdefault(row.group, []).append(row)
    # Notes live in path B only (path A's row geometry drops the merged Notes cells) and
    # are printed per date row, so they are keyed by the exact season they sit beside.
    notes_by_key = {r.key: (r.notes_text, r.bbox) for r in rows_b}

    # §8.7 date_token_accounting: tokens printed in the date columns vs seasons produced.
    state.tokens += _count_tokens(
        (c for table in rules for c in table.date_cell_texts), ctx.license_year
    )
    state.consumed += len(rows_a)
    for row in rows_a:
        if row.leftovers:
            state.leftovers.append(
                {"page": page.number, "units": row.units_text, "text": list(row.leftovers)}
            )

    for group, rows in _grouped(rows_a).items():
        peers = by_group_b.get(group, [])
        agree = {r.key for r in rows} == {r.key for r in peers} and len(rows) == len(peers)
        state.compared += len(rows)
        if not agree:
            state.disagreements.append(
                {
                    "page": page.number,
                    "units": list(group[2]),
                    "weapon": group[1],
                    "tag_type": group[0],
                    "tables": [_describe(r) for r in rows],
                    "text": [_describe(r) for r in peers],
                }
            )
        notes = next((r.notes_text for r in peers if r.notes_text), "")
        bbox = peers[0].bbox if peers else None
        for row in rows:
            state.rows += 1
            state.covered.update(row.units if row.tag_type_id == "deer_regular" else ())
            state.unknown_units += [(page.number, u) for u in row.unknown_units]
            own_notes, own_bbox = notes_by_key.get(row.key, (notes, bbox))
            merged = RowSeason(
                **{**row.__dict__, "notes_text": own_notes, "bbox": own_bbox or bbox}
            )
            yield to_parsed(merged, page.number, agree=agree)


def _grouped(rows: Sequence[RowSeason]) -> dict[tuple[str, str, tuple[str, ...]], list[RowSeason]]:
    out: dict[tuple[str, str, tuple[str, ...]], list[RowSeason]] = {}
    for row in rows:
        out.setdefault(row.group, []).append(row)
    return out


def _describe(row: RowSeason) -> str:
    return f"{row.sex} {to_iso(row.start)}–{to_iso(row.end)} {'+'.join(row.codes)}".strip()


def controlled_only(unit: str, texts: Sequence[str]) -> bool:
    """Does a controlled deer table or hunt-area description name this unit (§8.7)?"""
    row = re.compile(_CONTROLLED_ROW_RE.format(unit=re.escape(unit)))
    area = re.compile(_CONTROLLED_AREA_RE.format(unit=re.escape(unit)))
    return any(row.search(text) or area.search(text) for text in texts)


def finalize(ctx: ParseContext) -> list[CheckResult]:
    """The §8.7 invariants for `deer_general`, run once per document."""
    state = state_for(ctx)
    checks = [
        CheckResult(
            name="two_path_agreement",
            status="pass" if not state.disagreements else "fail",
            expected=f"{state.compared} rows agree",
            actual=f"{state.compared - sum(len(d['tables']) for d in state.disagreements)} agree",  # type: ignore[arg-type]
            detail=state.disagreements or None,
        ),
        CheckResult(
            name="date_token_accounting",
            status="pass" if state.tokens == state.consumed and not state.leftovers else "fail",
            expected=f"{state.tokens} date tokens consumed",
            actual=f"{state.consumed} seasons, {len(state.leftovers)} cells with leftover text",
            detail=state.leftovers or None,
        ),
        CheckResult(
            name="unit_tokens_valid",
            status="pass" if not state.unknown_units else "fail",
            expected="every unit token is a GMU in `units`",
            actual=f"{len(state.unknown_units)} unknown tokens",
            detail=[{"page": p, "token": t} for p, t in state.unknown_units] or None,
        ),
    ]
    missing = [
        unit
        for unit in sorted(set(ctx.units) - state.covered - {CLOSED_UNIT}, key=common.natural_key)
        if not controlled_only(unit, state.controlled_text)
    ]
    controlled = sorted(
        set(ctx.units) - state.covered - {CLOSED_UNIT} - set(missing), key=common.natural_key
    )
    checks.append(
        CheckResult(
            name="general_coverage",
            status="pass" if not missing else "fail",
            expected=f"{len(ctx.units) - 1} units covered or controlled-only",
            actual=f"{len(state.covered)} with a general Regular deer season,"
            f" {len(controlled)} controlled-only, {len(missing)} unexplained",
            detail={"controlled_only": controlled, "missing": missing}
            if controlled or missing
            else None,
        )
    )
    return checks


deer_general = parser(
    kind="big_game",
    match=matches,
    name=SECTION,
    section=SECTION,
    begin=begin,
    finalize=finalize,
)(parse_page)
