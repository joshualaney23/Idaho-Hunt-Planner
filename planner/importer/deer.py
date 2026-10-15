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
from planner.importer import ParseContext, ParsedSeason, UnitRef, common, parser
from planner.importer.checks import Coverage, DateCell, SectionFacts, TwoPath
from planner.importer.checks.shared import controlled_only
from planner.importer.extract import Page, Table, Word
from planner.importer.textlayer import (
    COLUMN_GUTTER_PT,
    ROW_GAP_PT,
    TextLine,
    column_blocks,
    column_of,
    group_lines,
    join_words,
    split_bands,
)

__all__ = [
    "COLUMN_GUTTER_PT",
    "controlled_only",
    "ROW_GAP_PT",
    "TextLine",
    "column_blocks",
    "column_of",
    "group_lines",
    "join_words",
    "split_bands",
]

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


def _name_blocks(header: TextLine, blocks: Sequence[tuple[float, float]]) -> dict[int, str] | None:
    """Block index → column name, by which header word's centre falls inside it."""
    names: dict[int, str] = {}
    for word in header.words:
        label = word.text.lower().rstrip(".")
        if label in HEADER_CELLS:
            index = column_of(word.cx, blocks)
            if index >= 0:
                names[index] = label
    if "unit(s)" not in names.values() or "antlered" not in names.values():
        return None
    return {i: ("units" if n == "unit(s)" else n) for i, n in names.items()}


def _is_header_line(line: TextLine) -> bool:
    labels = [w.text.lower().rstrip(".") for w in sorted(line.words, key=lambda w: w.x0)]
    return labels[:3] == ["unit(s)", "antlered", "antlerless"]


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
                    name = names.get(column_of(word.cx, blocks))
                    if name:
                        cells.setdefault(name, []).append(word)
            text = {name: join_words(items) for name, items in cells.items()}
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


@dataclass(frozen=True)
class CellSeasons:
    """One date cell of a table and the seasons it produced (§8.7 accounting)."""

    units_text: str
    column: str
    text: str
    seasons: tuple[RowSeason, ...] = ()
    bbox: tuple[float, float, float, float] | None = None

    @property
    def leftovers(self) -> tuple[str, ...]:
        """Date/qualifier text in this cell that nothing explained (D3)."""
        return tuple(dict.fromkeys(left for s in self.seasons for left in s.leftovers))


def table_cells(table: DeerTable, ctx: ParseContext) -> list[CellSeasons]:
    """Every date cell of one table with the seasons read from it, carrying the unit
    list down continuation rows (§12 T-11).

    A cell the parser produced nothing from is still returned, so `date_token_accounting`
    sees a dropped date instead of never hearing about the cell (D3).
    """
    info = table.title
    tag_type = info.tag_type_id or ""
    out: list[CellSeasons] = []
    units: tuple[str, ...] = ()
    unknown: tuple[str, ...] = ()
    units_text = ""
    for row in table.rows:
        if row.units_text.strip():
            parsed = common.parse_units(row.units_text, ctx.units)
            units = tuple(parsed.units)
            unknown = tuple(parsed.unknown)
            units_text = row.units_text
        notes_text = _cell(row.cells.get("notes", ""))
        for column, sex in SEX_COLUMNS:
            cell = row.cells.get(column, "")
            if (not units and not unknown) or common.is_no_season(cell):
                out.append(CellSeasons(units_text, column, cell, (), row.bbox))
                continue
            ranges = common.parse_date_ranges(cell, ctx.license_year)
            leftovers = list(common.leftover_date_text(cell, ranges))
            seasons: list[RowSeason] = []
            for i, found in enumerate(ranges):
                window = _qualifier_window(
                    cell, found, ranges[i + 1] if i + 1 < len(ranges) else None
                )
                quals = common.qualifiers(window)
                seasons.append(
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
            out.append(CellSeasons(units_text, column, cell, tuple(seasons), row.bbox))
    return out


def table_seasons(table: DeerTable, ctx: ParseContext) -> list[RowSeason]:
    """Every season in one table (the cells of `table_cells`, flattened)."""
    return [season for cell in table_cells(table, ctx) for season in cell.seasons]


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
    date_cells: list[DateCell] = field(default_factory=list)
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

    cells_a = [cell for table in rules for cell in table_cells(table, ctx)]
    rows_a = [s for cell in cells_a for s in cell.seasons]
    rows_b = [s for table in text for s in table_seasons(table, ctx)]
    by_group_b: dict[tuple[str, str, tuple[str, ...]], list[RowSeason]] = {}
    for row in rows_b:
        by_group_b.setdefault(row.group, []).append(row)
    # Notes live in path B only (path A's row geometry drops the merged Notes cells) and
    # are printed per date row, so they are keyed by the exact season they sit beside.
    notes_by_key = {r.key: (r.notes_text, r.bbox) for r in rows_b}

    # §8.7 date_token_accounting: every date cell printed in the table, with the
    # seasons it produced. The registry's check does the counting (T-22a).
    state.date_cells += [
        DateCell(
            page=page.number,
            texts=(cell.text,),
            seasons=len(cell.seasons),
            label=f"{cell.units_text} {cell.column}".strip(),
            leftovers=cell.leftovers,
            bbox=cell.bbox,
        )
        for cell in cells_a
    ]

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


def finalize(ctx: ParseContext) -> SectionFacts | None:
    """What `deer_general` measured — the §8.7 checks themselves live in the registry."""
    state = state_for(ctx)
    return SectionFacts(
        section=SECTION,
        two_path=TwoPath(
            compared=state.compared,
            disagreeing=sum(len(d["tables"]) for d in state.disagreements),  # type: ignore[arg-type]
            disagreements=state.disagreements,
        ),
        date_cells=state.date_cells,
        unknown_units=state.unknown_units,
        coverage=Coverage(
            covered=frozenset(state.covered),
            all_units=tuple(ctx.units),
            excluded=frozenset({CLOSED_UNIT}),
            texts=tuple(state.controlled_text),
        ),
    )


deer_general = parser(
    kind="big_game",
    match=matches,
    name=SECTION,
    section=SECTION,
    begin=begin,
    finalize=finalize,
)(parse_page)
