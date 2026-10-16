"""Black bear, mountain lion and gray wolf general season tables, §12 T-13.

Scope: the **general** season tables of the 2026 Big Game brochure's bear, lion and
wolf sections — one section each (`black_bear_general`, `mountain_lion_general`,
`wolf_general`). Controlled bear hunts are T-17a's `controlled.py`, and so is every
pronghorn hunt: the brochure prints no general pronghorn season at all (p. 64: "All
pronghorn hunting, including archery seasons, is by controlled hunt").

The three tables share one shape — a units cell, one season column, zero or more
**method-window** columns, and a Notes cell::

    Units | Take Season   | Dogs Prohibited | Dog Training | Notes   (bear)
    Units | Take Season   | Dogs Prohibited | Notes                  (lion)
    Units | Season Dates  | Notes                                    (wolf)

so one module reads all three, as §12 T-13 allows.

Two independent extraction paths (§8.7 principle 3): path A is `page.full_tables`
(pdfplumber's ruled tables), path B rebuilds the same rows from `page.full_words`
alone. Both read the page's *least-cropped* view: the fixed 40 pt side crop cuts
through the units cell of the bear table (`60A` → `0A`, `68` → `8` — valid-looking and
silently wrong, so the two paths would have agreed on it) and drops the Notes column's
right rule. §8.2's `tab_margins` keeps that view available as `full_*`.

A method window (Dogs Prohibited, Dog Training) is **not** a season: it says how the
season may be hunted. Those cells never produce a row, so they stay out of
`date_token_accounting`; `method_window_accounting` checks them instead, and a dogs
window overlapping a season adds the informational `dogs_prohibited` code (§5.1).
"""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, field

from planner.dates import to_iso
from planner.importer import ParseContext, ParsedSeason, UnitRef, common, parser
from planner.importer.checks import DateCell, MethodWindow, SectionFacts, TwoPath
from planner.importer.extract import Page, Word
from planner.importer.textlayer import (
    TextLine,
    column_blocks,
    column_of,
    group_lines,
    join_words,
    split_bands,
)

#: Every season this module reads is a general season for an either-sex species.
HUNT_CLASS = "general"
SEX = "any"
#: Characters after a date range that may qualify it (as in `deer.py`).
QUALIFIER_WINDOW = 60


@dataclass(frozen=True)
class SectionSpec:
    """One species' table: how to recognise it and what its columns mean."""

    species_id: str
    tag_type_id: str
    section: str
    #: The species as printed in the table title (also what `matches` scans a page for).
    species_re: re.Pattern[str]
    #: Header words that name the season column, lower-cased.
    season_words: frozenset[str]
    #: Header words naming a method-window column → the window's name.
    method_words: dict[str, str] = field(default_factory=dict)


SPECS: tuple[SectionSpec, ...] = (
    SectionSpec(
        species_id="black_bear",
        tag_type_id="black_bear",
        section="black_bear_general",
        species_re=re.compile(r"black\s+bear", re.IGNORECASE),
        season_words=frozenset({"take"}),
        method_words={
            "dogs": "dogs_prohibited",
            "prohibited": "dogs_prohibited",
            "training": "dog_training",
        },  # fmt: skip
    ),
    SectionSpec(
        species_id="mountain_lion",
        tag_type_id="mountain_lion",
        section="mountain_lion_general",
        species_re=re.compile(r"mountain\s+lion", re.IGNORECASE),
        season_words=frozenset({"take"}),
        method_words={"dogs": "dogs_prohibited", "prohibited": "dogs_prohibited"},
    ),
    SectionSpec(
        species_id="wolf",
        tag_type_id="wolf",
        section="wolf_general",
        species_re=re.compile(r"wolf\s+hunting", re.IGNORECASE),
        season_words=frozenset({"dates"}),
    ),
)
SPEC_BY_SECTION: dict[str, SectionSpec] = {s.section: s for s in SPECS}

#: Header words that name the units column and the notes column.
UNITS_WORDS = frozenset({"units", "unit(s)"})
NOTES_WORDS = frozenset({"notes"})
#: Wolf prints one table per land class; only the private-land one restricts the row.
PRIVATE_LAND_RE = re.compile(r"private\s+land", re.IGNORECASE)
#: A trapping table is not a hunting season (D4: trapping-only take is out of scope).
TRAPPING_RE = re.compile(r"trapping", re.IGNORECASE)
#: A season table's title always says "Season(s)" beside the species.
SEASON_WORD_RE = re.compile(r"\bseasons?\b", re.IGNORECASE)
#: Page furniture that ends a path-B table.
_FOOTER_RE = re.compile(r"idfg\.idaho\.gov|Idaho Big Game \d{4} Seasons", re.IGNORECASE)
_NOTE_BLOCK_RE = re.compile(r"^(?:notes?|private land note)\s*:", re.IGNORECASE)


# ───────── values both paths produce ─────────


@dataclass(frozen=True)
class RowSeason:
    """One season read from a table row, before notes become restriction codes."""

    section: str
    units: tuple[str, ...]
    start: dt.date
    end: dt.date
    #: Set when the license year clipped the printed window (`Apr 15 - July 31`).
    clipped_from: str = ""
    codes: tuple[str, ...] = ()
    unknown_units: tuple[str, ...] = ()
    leftovers: tuple[str, ...] = ()
    private_land: bool = False
    units_text: str = ""
    cell_text: str = ""
    notes_text: str = ""
    methods: tuple[tuple[str, str], ...] = ()  # (window name, printed text)
    bbox: tuple[float, float, float, float] | None = None

    @property
    def key(self) -> tuple[str, tuple[str, ...], str, str, bool, tuple[str, ...]]:
        """What the two paths must agree on (§8.7): units, dates, land class, codes."""
        return (
            self.section,
            self.units,
            to_iso(self.start),
            to_iso(self.end),
            self.private_land,
            self.codes,
        )


@dataclass(frozen=True)
class SeasonRow:
    """One printed table row: its units cell, its named cells and where it sits."""

    units_text: str
    cells: dict[str, str]
    bbox: tuple[float, float, float, float] | None = None


@dataclass(frozen=True)
class SeasonTable:
    """One general season table of this module's shape."""

    spec: SectionSpec
    title: str
    rows: list[SeasonRow]

    @property
    def private_land(self) -> bool:
        return bool(PRIVATE_LAND_RE.search(self.title))


def _cell(value: str | None) -> str:
    return re.sub(r"[ \t]+", " ", (value or "").replace("\n", " ")).strip()


def spec_for_title(title: str) -> SectionSpec | None:
    """The species whose general season table carries this title, if any."""
    if TRAPPING_RE.search(title):
        return None
    if not SEASON_WORD_RE.search(title):
        return None
    info = common.parse_title(title)
    if info.hunt_class != HUNT_CLASS:  # a Controlled table belongs to T-17a
        return None
    # The title is matched on the species alone: a page-wide text line interleaves the
    # title's two printed lines ("Black Bear (Females Seasons with for young Fall 2026
    # are protected!)"), so the phrase "Black Bear Seasons" is not contiguous in path B.
    return next((s for s in SPECS if s.species_re.search(title)), None)


# ───────── path A: pdfplumber's ruled tables ─────────


def _columns_from_header(spec: SectionSpec, row: Sequence[str | None]) -> dict[int, str] | None:
    """Column index → role, or None when `row` is not this table's header row."""
    names = [_cell(c).lower().rstrip(".") for c in row]
    if not names or names[0] not in UNITS_WORDS:
        return None
    columns: dict[int, str] = {0: "units"}
    for index, name in enumerate(names[1:], 1):
        role = _role_for(spec, name.split())
        if role:
            columns[index] = role
    if "season" not in columns.values():
        return None
    return columns


def _role_for(spec: SectionSpec, words: Iterable[str]) -> str | None:
    """`units` / `season` / `notes` / a method-window name, from a header cell's words."""
    found: str | None = None
    for raw in words:
        word = raw.lower().strip(".,")
        if word in UNITS_WORDS:
            return "units"
        if word in spec.season_words:
            return "season"
        if word in NOTES_WORDS:
            found = found or "notes"
        if word in spec.method_words:
            found = found or spec.method_words[word]
    return found


def tables_from_rules(page: Page) -> list[SeasonTable]:
    """Path A: the general season tables of this page, from the ruled-table view."""
    out: list[SeasonTable] = []
    for raw in page.full_tables:
        rows = [r for r in raw if any(_cell(c) for c in r)]
        title = " ".join(_cell(c) for c in rows[0]) if rows else ""
        spec = spec_for_title(title)
        header_index = next(
            (
                i
                for i, r in enumerate(rows)
                if spec is not None and _columns_from_header(spec, r) is not None
            ),
            None,
        )
        if spec is None or header_index is None:
            continue
        columns = _columns_from_header(spec, rows[header_index]) or {}
        body = [
            SeasonRow(
                units_text=_cell(row[0]) if row else "",
                cells={
                    role: _cell(row[i])
                    for i, role in columns.items()
                    if role != "units" and i < len(row)
                },
            )
            for row in rows[header_index + 1 :]
        ]
        out.append(SeasonTable(spec=spec, title=title, rows=body))
    return out


# ───────── path B: the text layer's word positions ─────────


def _title_above(lines: Sequence[TextLine], header: int) -> tuple[SectionSpec | None, str]:
    """The table title printed above a header line, and the species it names."""
    kept: list[str] = []
    for line in reversed(lines[:header]):
        text = line.text.strip()
        if not text or _FOOTER_RE.search(text):
            break
        kept.append(text)
        if spec_for_title(" ".join(reversed(kept))) is not None:
            break
        if len(kept) >= 4:
            break
    title = " ".join(reversed(kept))
    return spec_for_title(title), title


def _header_lines(lines: Sequence[TextLine]) -> list[int]:
    """Indexes of the lines that carry a `Units … Season …` header."""
    out: list[int] = []
    for i, line in enumerate(lines):
        words = {w.text.lower().strip(".,") for w in line.words}
        if words & UNITS_WORDS and (words & {"season", "dates"}):
            out.append(i)
    return out


def _name_blocks(
    spec: SectionSpec, lines: Sequence[TextLine], blocks: Sequence[tuple[float, float]]
) -> dict[int, str]:
    """Block index → role, from the header words (which wrap over several lines)."""
    names: dict[int, str] = {}
    for line in lines:
        for word in line.words:
            role = _role_for(spec, [word.text])
            if role is None:
                continue
            index = column_of(word.cx, blocks)
            if index >= 0:
                names.setdefault(index, role)
    return names


def tables_from_text(page: Page) -> list[SeasonTable]:
    """Path B: the same tables rebuilt from word positions, never a ruled line."""
    lines = sorted(group_lines(page.full_words), key=lambda ln: ln.top)
    headers = _header_lines(lines)
    out: list[SeasonTable] = []
    for position, start in enumerate(headers):
        spec, title = _title_above(lines, start)
        if spec is None:
            continue
        stop = headers[position + 1] if position + 1 < len(headers) else len(lines)
        body = _body_lines(lines[start + 1 : stop])
        if not body:
            continue
        blocks = column_blocks([w for ln in [lines[start], *body] for w in ln.words])
        # The header wraps ("Dogs" / "Prohibited" sit on their own lines), so the names
        # are read from the header line and the two lines around it.
        header_area = lines[max(0, start - 1) : min(len(lines), start + 2)]
        names = _name_blocks(spec, header_area, blocks)
        if "season" not in names.values():
            continue
        rows: list[SeasonRow] = []
        # Rows are anchored on the **units cell's own word geometry**, never on
        # page-wide lines or whitespace bands: on the lion page a page-wide line
        # grouping chains four printed rows into one (§8.7 path B rule — anchor on
        # content). Each unit cell is one band of units-column words, and every other
        # word of the table goes to the nearest such band by vertical centre, which is
        # exactly how the cells are printed (vertically centred on their row).
        body_words = [w for ln in body for w in ln.words]
        unit_words = [w for w in body_words if names.get(column_of(w.cx, blocks)) == "units"]
        labels = split_bands(group_lines(unit_words))
        labels = [
            band
            for band in labels
            if common.parse_units(_band_text(band)).units
            or common.names_all_units(_band_text(band))
        ]
        if not labels:
            continue
        centres = [_band_centre(band) for band in labels]
        cells_per_row: list[dict[str, list[Word]]] = [{} for _ in labels]
        for word in body_words:
            role = names.get(column_of(word.cx, blocks))
            if not role:
                continue
            centre = (word.top + word.bottom) / 2
            owner = min(range(len(centres)), key=lambda i: abs(centres[i] - centre))
            cells_per_row[owner].setdefault(role, []).append(word)
        for index, label in enumerate(labels):
            cells = cells_per_row[index]
            text = {role: join_words(items) for role, items in cells.items()}
            boxes = [w for items in cells.values() for w in items] or [
                w for ln in label for w in ln.words
            ]
            rows.append(
                SeasonRow(
                    units_text=_band_text(label),
                    cells={k: v for k, v in text.items() if k != "units"},
                    bbox=(
                        min(w.x0 for w in boxes),
                        min(w.top for w in boxes),
                        max(w.x1 for w in boxes),
                        max(w.bottom for w in boxes),
                    ),
                )
            )
        out.append(SeasonTable(spec=spec, title=title, rows=rows))
    return out


def _band_text(lines: Sequence[TextLine]) -> str:
    """The text of one cell band, in reading order."""
    return join_words([w for ln in lines for w in ln.words])


def _centre(line: TextLine) -> float:
    return (line.top + line.bottom) / 2


def _band_centre(lines: Sequence[TextLine]) -> float:
    return (min(ln.top for ln in lines) + max(ln.bottom for ln in lines)) / 2


def _column_text(
    line: TextLine,
    blocks: Sequence[tuple[float, float]],
    names: dict[int, str],
    role: str,
) -> str:
    """The text one printed line contributes to one column."""
    words = [w for w in line.words if names.get(column_of(w.cx, blocks)) == role]
    return join_words(words) if words else ""


def _body_lines(lines: Sequence[TextLine]) -> list[TextLine]:
    """A table's body lines: stop at the page footer, a `Note:` block or the next title."""
    body: list[TextLine] = []
    for band in split_bands(lines):
        text = " ".join(ln.text for ln in band)
        if _FOOTER_RE.search(text) or _NOTE_BLOCK_RE.match(text.strip()):
            break
        has_dates = bool(common.parse_date_ranges(text, 2026))
        has_units = bool(common.parse_units(band[0].text).units) or common.names_all_units(
            band[0].text
        )
        if not has_dates and not has_units:
            break
        body += band
    return body


# ───────── rows → seasons ─────────


def _qualifier_window(cell: str, this: common.DateRange, following: common.DateRange | None) -> str:
    """Text qualifying one date range: up to 60 chars, never past the next range."""
    limit = following.span[0] if following is not None else len(cell)
    end = this.span[1]
    return cell[end : min(limit, end + QUALIFIER_WINDOW)]


@dataclass(frozen=True)
class CellSeasons:
    """One season cell and the seasons read from it (§8.7 date accounting)."""

    units_text: str
    text: str
    seasons: tuple[RowSeason, ...] = ()
    bbox: tuple[float, float, float, float] | None = None
    #: The table's windows recur yearly, so one token can yield two license-year parts.
    recurring: bool = False

    @property
    def leftovers(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(left for s in self.seasons for left in s.leftovers))


def table_cells(table: SeasonTable, ctx: ParseContext) -> list[CellSeasons]:
    """Every season cell of one table with the seasons it produced, units carried down."""
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
        cell = row.cells.get("season", "")
        notes_text = row.cells.get("notes", "")
        methods = tuple(
            (name, row.cells[name]) for name in row.cells if name not in ("season", "notes")
        )
        if (not units and not unknown) or common.is_no_season(cell):
            out.append(CellSeasons(units_text, cell, (), row.bbox))
            continue
        ranges = common.parse_date_ranges(cell, ctx.license_year)
        leftovers = list(common.leftover_date_text(cell, ranges))
        recurring = covers_license_year(table.title, ctx)
        seasons: list[RowSeason] = []
        for i, found in enumerate(ranges):
            window = _qualifier_window(cell, found, ranges[i + 1] if i + 1 < len(ranges) else None)
            quals = common.qualifiers(window)
            parts = windows_in_license_year(found, ctx, recurring=recurring)
            if not parts:  # printed entirely outside this license year (D3)
                leftovers.append(found.text)
                continue
            for start, end, clipped in parts:
                codes = list(quals.codes)
                if clipped and "see_notes" not in codes:
                    codes.append("see_notes")
                for name, text in methods:
                    if name == "dogs_prohibited" and _overlaps_window(found, text, ctx):
                        if "dogs_prohibited" not in codes:
                            codes.append("dogs_prohibited")
                seasons.append(
                    RowSeason(
                        section=table.spec.section,
                        units=units,
                        start=start,
                        end=end,
                        clipped_from=found.text if clipped else "",
                        codes=tuple(codes),
                        unknown_units=unknown,
                        leftovers=tuple(leftovers + list(quals.unmatched)),
                        private_land=table.private_land,
                        units_text=units_text,
                        cell_text=cell,
                        notes_text=notes_text,
                        methods=methods,
                        bbox=row.bbox,
                    )
                )
        out.append(CellSeasons(units_text, cell, tuple(seasons), row.bbox, recurring))
    return out


#: A title that names the license year's own span (`July 2026 - June 2027`) belongs to a
#: table whose windows **recur every year**, so each one applies to this licence year
#: wherever it falls inside it (the wolf and lion tables are printed that way). A title
#: naming explicit seasons instead ("Seasons for Fall 2026 and Spring 2027") states one
#: occurrence each, and nothing may be read into the year before it.
def covers_license_year(title: str, ctx: ParseContext) -> bool:
    """Does this table's title say it covers the whole license year (July X – June X+1)?"""
    text = re.sub(r"\s+", " ", title or "")
    return bool(
        re.search(
            rf"july\s+{ctx.license_year}\s*[-–—]\s*june\s+{ctx.year_end + 1}", text, re.IGNORECASE
        )
    )


def windows_in_license_year(
    found: common.DateRange, ctx: ParseContext, *, recurring: bool
) -> list[tuple[dt.date, dt.date, bool]]:
    """The parts of a printed window that fall inside the license year.

    `(start, end, clipped)` per part. A window is anchored by `common.parse_date_ranges`
    and clipped to Jul 1 … Jun 30: the result is always a **subset** of what is printed,
    so the map can never show a unit open when it is not, and a clipped row carries
    `see_notes` so it stays yellow and goes to review (D3).

    In a `recurring` table (`July 2026 - June 2027`) the same window also applies in the
    year before the anchored one — `Apr 1 - Nov 14` means Jul 1 – Nov 14 2026 *and*
    Apr 1 – Jun 30 2027 — so both parts are kept rather than silently losing one.
    """
    first = dt.date(ctx.license_year, 7, 1)
    last = dt.date(ctx.year_end + 1, 6, 30)
    candidates = [(found.start, found.end)]
    if recurring:
        candidates.append(
            (_shift_year(found.start, -1), _shift_year(found.end, -1)),
        )
    out: list[tuple[dt.date, dt.date, bool]] = []
    for start, end in candidates:
        cut_start, cut_end = max(start, first), min(end, last)
        if cut_end < cut_start:
            continue
        out.append((cut_start, cut_end, (cut_start, cut_end) != (start, end)))
    return sorted(out)


def _shift_year(day: dt.date, years: int) -> dt.date:
    try:
        return day.replace(year=day.year + years)
    except ValueError:  # Feb 29
        return day.replace(year=day.year + years, day=day.day - 1)


def _overlaps_window(season: common.DateRange, text: str, ctx: ParseContext) -> bool:
    """Does any window printed in `text` overlap this season (dogs prohibited when so)?"""
    return any(
        w.start <= season.end and season.start <= w.end
        for w in common.parse_date_ranges(text, ctx.license_year)
    )


def table_seasons(table: SeasonTable, ctx: ParseContext) -> list[RowSeason]:
    """Every season in one table."""
    return [season for cell in table_cells(table, ctx) for season in cell.seasons]


#: `See Note 1, Page 76` / `See Pages 104 - 106` — a pointer, not a condition itself.
_POINTER_RE = re.compile(
    r"\b(?:see\s+)?(?:notes?|pages?|pg)\.?\s*(?:\d+|and|[,\s-])*", re.IGNORECASE
)
#: `… in Units 29 and 37A` — the units a matched qualifier names.
_UNIT_PHRASE_RE = re.compile(
    r"\bin (?:the )?units?\b(?:\s*(?:\d{1,2}[A-Z]?|and)[,\s]*)*", re.IGNORECASE
)
#: Words that carry no restriction of their own.
_NOISE_RE = re.compile(r"\bapplies\b|\bonly\b|[\s,.;()—-]+", re.IGNORECASE)
#: A numbered note this parser does not read ⇒ `see_notes` (D3).
_NOTE_REF_RE = re.compile(r"\bnotes?\s*\d", re.IGNORECASE)


def notes_codes(notes: str) -> list[str]:
    """Restriction codes for a Notes cell; anything unexplained adds `see_notes` (D3).

    The same rule as `deer.notes_codes`: strip every phrase the §5.1 vocabulary
    explained, plus page pointers and the units a phrase names; whatever text is left
    is a condition nobody modelled, so the row stays yellow rather than green.
    """
    text = _cell(notes)
    if not text:
        return []
    quals = common.qualifiers(text)
    codes = list(quals.codes)
    explained = text
    for pattern, _ in common.QUALIFIER_PATTERNS:
        explained = re.sub(pattern, " ", explained, flags=re.IGNORECASE)
    explained = _POINTER_RE.sub(" ", explained)
    explained = _UNIT_PHRASE_RE.sub(" ", explained)
    explained = _NOISE_RE.sub(" ", explained)
    if (explained.strip() or _NOTE_REF_RE.search(text)) and "see_notes" not in codes:
        codes.append("see_notes")
    return codes


def to_parsed(row: RowSeason, page_number: int, *, agree: bool) -> ParsedSeason:
    """A compared `RowSeason` as the `ParsedSeason` the pipeline stages."""
    spec = SPEC_BY_SECTION[row.section]
    codes = list(row.codes)
    for code in notes_codes(row.notes_text):
        if code not in codes:
            codes.append(code)
    if row.private_land and "private_land_only" not in codes:
        codes.append("private_land_only")
    refs = [UnitRef(unit_id=unit) for unit in row.units]
    # An unreadable unit token is never dropped: it is staged as a failing row (D3).
    refs += [UnitRef(unit_id=token) for token in row.unknown_units]
    method_text = " | ".join(f"{name}: {text}" for name, text in row.methods if text)
    clipped = (
        f"printed {row.clipped_from}, clipped to this license year" if row.clipped_from else ""
    )
    source = " | ".join(
        part
        for part in (row.units_text, row.cell_text, method_text, row.notes_text, clipped)
        if part.strip()
    )
    return ParsedSeason(
        species_id=spec.species_id,
        tag_type_id=spec.tag_type_id,
        hunt_class=HUNT_CLASS,
        weapon="any",
        sex=SEX,
        start=row.start,
        end=row.end,
        units=refs,
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
            date_ranges=len(common.parse_date_ranges(row.cell_text, row.start.year)),
        ),
        section=row.section,
    )


# ───────── per-document state and the §8.7 facts ─────────


@dataclass
class SectionState:
    """What one section carries between the pages of a document."""

    rows: int = 0
    pages: list[int] = field(default_factory=list)
    unknown_units: list[tuple[int, str]] = field(default_factory=list)
    disagreements: list[dict[str, object]] = field(default_factory=list)
    compared: int = 0
    date_cells: list[DateCell] = field(default_factory=list)
    method_windows: list[MethodWindow] = field(default_factory=list)
    keys: list[tuple[str, ...]] = field(default_factory=list)


_STATE: dict[tuple[int, str], SectionState] = {}


def state_for(ctx: ParseContext, section: str) -> SectionState:
    """The state of one section of one document (created on first use)."""
    return _STATE.setdefault((ctx.document_id, section), SectionState())


def _begin(section: str):  # type: ignore[no-untyped-def]
    def begin(ctx: ParseContext) -> None:
        _STATE[(ctx.document_id, section)] = SectionState()

    return begin


def _matches(spec: SectionSpec):  # type: ignore[no-untyped-def]
    def matches(page: Page) -> bool:
        return not page.rotated and bool(spec.species_re.search(page.full_text))

    return matches


def _parse_page(spec: SectionSpec):  # type: ignore[no-untyped-def]
    def parse_page(page: Page, ctx: ParseContext) -> Iterator[ParsedSeason]:
        yield from parse_page_for(spec, page, ctx)

    return parse_page


def parse_page_for(spec: SectionSpec, page: Page, ctx: ParseContext) -> Iterator[ParsedSeason]:
    """Parse one page with both paths, compare them, and yield the seasons to stage."""
    state = state_for(ctx, spec.section)
    rules = [t for t in tables_from_rules(page) if t.spec is spec]
    text = [t for t in tables_from_text(page) if t.spec is spec]
    if not rules and not text:
        return
    state.pages.append(page.number)

    cells_a = [cell for table in rules for cell in table_cells(table, ctx)]
    rows_a = [season for cell in cells_a for season in cell.seasons]
    rows_b = [season for table in text for season in table_seasons(table, ctx)]

    state.date_cells += [
        DateCell(
            page=page.number,
            texts=(cell.text,),
            seasons=len(cell.seasons),
            label=cell.units_text,
            leftovers=cell.leftovers,
            bbox=cell.bbox,
            recurring=cell.recurring,
        )
        for cell in cells_a
    ]
    for row in rows_a:
        state.method_windows += [
            MethodWindow(
                page=page.number,
                name=name,
                text=text_value,
                windows=len(common.parse_date_ranges(text_value, ctx.license_year)),
                label=row.units_text,
                leftovers=tuple(
                    common.leftover_date_text(
                        text_value, common.parse_date_ranges(text_value, ctx.license_year)
                    )
                ),
            )
            for name, text_value in row.methods
            if text_value.strip() and not common.is_no_season(text_value)
        ]

    keys_b = {r.key for r in rows_b}
    for row in rows_a:
        agree = row.key in keys_b
        state.compared += 1
        if not agree:
            state.disagreements.append(
                {
                    "page": page.number,
                    "units": list(row.units),
                    "tables": [_describe(row)],
                    "text": [_describe(r) for r in rows_b if r.units == row.units],
                }
            )
        state.rows += 1
        state.unknown_units += [(page.number, u) for u in row.unknown_units]
        state.keys.append(
            (row.section, ",".join(row.units), to_iso(row.start), to_iso(row.end))
            + (("private",) if row.private_land else ())
        )
        yield to_parsed(row, page.number, agree=agree)


def _describe(row: RowSeason) -> str:
    return f"{to_iso(row.start)}–{to_iso(row.end)} {'+'.join(row.codes)}".strip()


def _finalize(section: str):  # type: ignore[no-untyped-def]
    def finalize(ctx: ParseContext) -> SectionFacts | None:
        state = state_for(ctx, section)
        return SectionFacts(
            section=section,
            two_path=TwoPath(
                compared=state.compared,
                disagreeing=len(state.disagreements),
                disagreements=state.disagreements,
            ),
            date_cells=state.date_cells,
            unknown_units=state.unknown_units,
            keys=state.keys,
            key_label="(units, season window, land class)",
            extra={"method_windows": state.method_windows},
        )

    return finalize


def register_all() -> None:
    """Register one parser per species (one section, one set of §8.7 checks each)."""
    for spec in SPECS:
        parser(
            kind="big_game",
            match=_matches(spec),
            name=spec.section,
            section=spec.section,
            begin=_begin(spec.section),
            finalize=_finalize(spec.section),
        )(_parse_page(spec))


register_all()
