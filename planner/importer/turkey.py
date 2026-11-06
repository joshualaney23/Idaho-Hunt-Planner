"""Wild turkey seasons of the Upland brochure, §12 T-23.

Scope: the turkey sections of the Upland Game, Turkey & Furbearer brochure (2026 &
2027: printed pp. 18–25). Two kinds of table, so two kinds of parser:

* **General seasons** (printed p. 22) — `turkey_general`. Three ruled tables, each a
  title plus `Game Management Units | Season Dates | Notes`, read here by two
  independent paths.
* **Controlled hunts** (printed pp. 24–25) — `controlled_turkey`, read by `controlled.py`
  with the `UPLAND_TURKEY` `Brochure` below, exactly as §12 T-23 asks. The turkey tables
  differ from the Big Game ones in five ways, all expressed as data on the spec: the
  header prints `Hunt No` with no period, each area's description is printed **inside**
  its area cell, no section prints a tag total, the hunt numbers are `9xxx`, and the
  brochure covers two license years.

The p. 23 **general season maps are images** and are deliberately not a data source
(§12 T-23). A unit list that defers to one (`see map`) is staged as a failing row for
manual entry; the 2026 brochure prints no such row, so that path is proven by a test
fixture rather than by live data — said plainly in the PR instead of claimed as data.

Both brochure years are created from one printed date cell (§12 T-23: "the brochure
covers 2026 and 2027: create seasons for both years with each year's printed dates"),
the same way T-25 handles the two-year MSG brochure.

Extraction (§8.7 principle 3), for the general tables:

* **Path A — ruled tables.** `page.full_tables`.
* **Path B — text layer.** `page.full_words`, rows anchored on the units column's own
  word geometry, never on a ruled line.

Both read the page's *least-cropped* view (`full_*`): the fixed 40 pt side crop reaches
into the units column of the general tables and empties it — on printed p. 22 the
cropped view loses the units cell of 4 of the 12 rows (`null` where `1, 2 (except
Farragut State Park and WMA), …` is printed) — and the crop also hides the controlled
tables' `Hunt No` header. That is the T-13 failure mode, and the fix is the same: read
the wider view that `extract.tab_margins` already computes, and leave the shared default
alone so no other parser moves.
"""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field, replace

from planner.dates import to_iso
from planner.importer import ParseContext, ParsedSeason, UnitRef, common, parser
from planner.importer.checks import Coverage, DateCell, SectionFacts, TwoPath
from planner.importer.controlled import Brochure, register_brochure
from planner.importer.extract import Page, Word
from planner.importer.hunt_areas import register_brochure as register_area_brochure
from planner.importer.textlayer import (
    TextLine,
    column_blocks,
    column_of,
    group_lines,
    join_words,
    split_bands,
)

# ───────── the controlled-hunt side: one `Brochure` value (§12 T-23 reuses T-17a) ─────────

#: The turkey tables print `Hunt No`, with no period after `No`.
HUNT_PAGE_RE = re.compile(r"Hunt No\b", re.IGNORECASE)
#: `Male or Bearded Turkey` / `Either Sex Turkey` — this brochure's sex vocabulary.
#: `seasons.sex` is a closed set (`VALID_SEX`), so a male-or-bearded hunt is `male`.
SEX_TITLES: tuple[tuple[str, str], ...] = (
    ("either sex", "either"),
    ("male or bearded", "male"),
    ("bearded", "male"),
)
#: The Upland brochure's page footer, which carries its own printed page number.
FOOTER_RE = re.compile(
    r"idfg\.idaho\.gov\s*(?P<right>\d{1,3})\s*$"
    r"|^\s*(?P<left>\d{1,3})\s+Upland Game, Turkey",
    re.IGNORECASE | re.MULTILINE,
)
#: A turkey hunt area id as printed: `63A`, `50-1`, `36B-1`.
AREA_RE = re.compile(r"(?P<area>\d{1,2}[A-Z]?(?:-\d+)?[A-Z]*)(?P<mark>[a-z]?)")
#: The turkey tables print no footnote legend of their own, and no description pages.
LEGEND_RE = re.compile(r"(?!)")  # never matches
HEADING_RE = re.compile(r"(?!)")  # never matches: the descriptions are in the cells

#: The 2026 & 2027 Upland brochure's controlled turkey hunts — every one numbered 9xxx.
UPLAND_TURKEY = Brochure(
    kind="upland",
    species_by_prefix={"9": "turkey"},
    species_words=(("turkey", "turkey"),),
    header=("hunt no", "controlled hunt area descriptions", "season dates", "tags"),
    legend_re=LEGEND_RE,
    legend_meanings=(),
    mark_codes={},
    area_re=AREA_RE,
    footer_re=FOOTER_RE,
    heading_re=HEADING_RE,
    # One printed date cell covers both license years (§12 T-23), as in T-25's MSG.
    both_years=True,
    # Turkey tags expire on December 31 of the year of purchase (printed p. 22), so a
    # spring window belongs to its table's own calendar year, not to `license_year + 1`.
    calendar_years=True,
    sex_titles=SEX_TITLES,
    hunt_page_re=HUNT_PAGE_RE,
    # The rows interleave in the text layer, so a hunt number is not reliably at a line
    # start; `9xxx` is used by nothing else on these pages.
    numbers_anywhere=True,
    prints_tag_totals=False,
    area_descriptions_in_cell=True,
)

#: Registered here so `controlled_turkey` and `hunt_areas_turkey` exist for `kind=upland`.
CONTROLLED_PARSERS = register_brochure(UPLAND_TURKEY)
AREA_PARSERS = register_area_brochure(UPLAND_TURKEY)


# ───────── the general-season side ─────────

SECTION = "turkey_general"
SPECIES_ID = "turkey"
TAG_TYPE_ID = "turkey_general"
#: Printed title phrase → `seasons.hunt_class`. A youth table is its own hunt class.
YOUTH_RE = re.compile(r"youth", re.IGNORECASE)
#: `Spring Turkey - Male or Bearded Turkey` / `Fall Turkey - Either Sex Turkey`.
TITLE_RE = re.compile(r"\b(?P<season>spring|fall)\s+turkey\b", re.IGNORECASE)
#: Header labels of a general season table.
UNITS_WORDS = frozenset({"units", "unit(s)"})
NOTES_WORDS = frozenset({"notes"})
#: `Archery only, Archery Permit Required` — the Notes column of these tables can carry
#: the hunt's **weapon**, not just a note, so it is read for one (§12 T-23).
ARCHERY_RE = re.compile(r"\barchery\s+only\b", re.IGNORECASE)
#: A row that defers its unit list to the p. 23 maps, which are images (§12 T-23).
SEE_MAP_RE = re.compile(r"\bsee\s+(?:the\s+)?maps?\b", re.IGNORECASE)
#: Page furniture that ends a path-B table.
FOOTER_LINE_RE = re.compile(r"idfg\.idaho\.gov|Upland Game, Turkey & Furbearer", re.IGNORECASE)
#: The numbered notes block printed under the tables.
NOTE_ITEM_RE = re.compile(r"^(\d+)\.\s+(.*)$")
#: `See note 1` / `See notes 1 and 2` — a pointer into that block.
NOTE_REF_RE = re.compile(r"see\s+notes?\s+([\d,\s]+(?:and\s+\d+)?)", re.IGNORECASE)


@dataclass(frozen=True)
class GeneralRow:
    """One printed general-season row, before it becomes seasons."""

    units_text: str
    dates_text: str
    notes_text: str
    title: str
    bbox: tuple[float, float, float, float] | None = None


@dataclass(frozen=True)
class GeneralSeason:
    """One season read from a general-season row."""

    start: dt.date
    end: dt.date
    units: tuple[str, ...]
    hunt_class: str
    weapon: str
    sex: str
    codes: tuple[str, ...]
    units_text: str
    dates_text: str
    notes_text: str
    title: str
    unknown_units: tuple[str, ...] = ()
    leftovers: tuple[str, ...] = ()
    see_map: bool = False
    bbox: tuple[float, float, float, float] | None = None

    @property
    def key(self) -> tuple[str, ...]:
        """What the two extraction paths must agree on (§8.7 principle 3)."""
        return (
            to_iso(self.start),
            to_iso(self.end),
            ",".join(self.units),
            self.hunt_class,
            self.weapon,
            self.sex,
            ",".join(self.codes),
        )


def title_attributes(title: str) -> tuple[str, str, str] | None:
    """`(hunt_class, sex, season word)` of a general table title, or None.

    An unreadable title fails the table rather than defaulting its sex (D3).
    """
    text = re.sub(r"\s+", " ", title or "").strip()
    match = TITLE_RE.search(text)
    if match is None:
        return None
    low = text.lower()
    sex = next((value for phrase, value in SEX_TITLES if phrase in low), None)
    if sex is None:
        return None
    hunt_class = "youth" if YOUTH_RE.search(text) else "general"
    return hunt_class, sex, match.group("season").lower()


# ───────── path A: pdfplumber's ruled tables ─────────


def _cell(value: str | None) -> str:
    return re.sub(r"[ \t]+", " ", (value or "").replace("\n", " ")).strip()


def _columns_from_header(row: Sequence[str | None]) -> dict[int, str] | None:
    """Column index → role, or None when `row` is not a general table's header."""
    names = [_cell(c).lower().rstrip(".") for c in row]
    columns: dict[int, str] = {}
    for index, name in enumerate(names):
        words = {w.strip(".,") for w in name.split()}
        if words & UNITS_WORDS or "units" in name:
            columns.setdefault(index, "units")
        elif "season dates" in name or "dates" in words:
            columns.setdefault(index, "dates")
        elif words & NOTES_WORDS:
            columns.setdefault(index, "notes")
    if "units" not in columns.values() or "dates" not in columns.values():
        return None
    return columns


def rows_from_tables(page: Page) -> list[GeneralRow]:
    """Path A: every general-season row of this page, from the ruled-table view."""
    out: list[GeneralRow] = []
    for raw in page.full_tables:
        rows = [r for r in raw if any(_cell(c) for c in r)]
        if not rows:
            continue
        title = " ".join(_cell(c) for c in rows[0])
        header_index = next(
            (i for i, r in enumerate(rows) if _columns_from_header(r) is not None), None
        )
        if header_index is None or title_attributes(title) is None:
            continue
        columns = _columns_from_header(rows[header_index]) or {}
        roles = {role: index for index, role in columns.items()}
        for row in rows[header_index + 1 :]:
            cells = list(row)
            values = {
                role: (_cell(cells[index]) if index < len(cells) else "")
                for role, index in roles.items()
            }

            def value(role: str, values: dict[str, str] = values) -> str:
                return values.get(role, "")

            units_text, dates_text = value("units"), value("dates")
            if not units_text and not dates_text:
                continue
            out.append(
                GeneralRow(
                    units_text=units_text,
                    dates_text=dates_text,
                    notes_text=value("notes"),
                    title=title,
                )
            )
    return out


# ───────── path B: the text layer's word positions ─────────


def _header_lines(lines: Sequence[TextLine]) -> list[int]:
    """Indexes of the `Game Management Units … Season Dates …` header lines."""
    out: list[int] = []
    for index, line in enumerate(lines):
        words = {w.text.lower().strip(".,") for w in line.words}
        if words & UNITS_WORDS and "dates" in words:
            out.append(index)
    return out


def _title_above(lines: Sequence[TextLine], header: int) -> str:
    """The table title printed directly above a header line."""
    kept: list[str] = []
    for line in reversed(lines[:header]):
        text = line.text.strip()
        if not text or FOOTER_LINE_RE.search(text):
            break
        kept.append(text)
        if title_attributes(" ".join(reversed(kept))) is not None:
            break
        if len(kept) >= 3:
            break
    return " ".join(reversed(kept))


def _band_text(lines: Sequence[TextLine]) -> str:
    return join_words([w for ln in lines for w in ln.words])


def _band_centre(lines: Sequence[TextLine]) -> float:
    return (min(ln.top for ln in lines) + max(ln.bottom for ln in lines)) / 2


def upright_words(page: Page) -> list[Word]:
    """The page's words with the rotated section tab removed (§12 T-23).

    The least-cropped view keeps the sideways section tab ("Turkey General Seasons"
    printed up the page edge), and those words wreck path B twice over: they merge into
    a printed line, so the Fall table's title reads `yekruT Fall Turkey - Either Sex
    Turkey`, and they bridge the gap between two tables, so the middle table's body
    chains into its neighbour and its rows vanish — silently, since path A still reads
    them. A sideways word has **zero width** (`x0 == x1`) and a height several times the
    body text's, which is the signature used here; cropping to `tab_left`/`tab_right`
    instead would also cut the real units column, which is why the wide view exists.
    """
    return [w for w in page.full_words if w.x1 > w.x0]


def rows_from_text(page: Page) -> list[GeneralRow]:
    """Path B: the same rows rebuilt from word positions, never a ruled line.

    Rows are anchored on the **units column's own word geometry** (the T-13 rule): a
    page-wide line grouping chains a wrapped 4-line unit list into its neighbour's row,
    and these tables wrap nearly every unit cell. So the units-column words are banded
    first, bands that parse as units are kept as the row labels, and every other word
    goes to the nearest label by vertical centre — which is how the cells are printed.
    """
    lines = sorted(group_lines(upright_words(page)), key=lambda ln: ln.top)
    headers = _header_lines(lines)
    out: list[GeneralRow] = []
    for position, start in enumerate(headers):
        title = _title_above(lines, start)
        if title_attributes(title) is None:
            continue
        stop = headers[position + 1] if position + 1 < len(headers) else len(lines)
        body = _body_lines(lines[start + 1 : stop])
        if not body:
            continue
        blocks = column_blocks([w for ln in [lines[start], *body] for w in ln.words])
        names = _name_blocks(lines[start], blocks)
        if "units" not in names.values() or "dates" not in names.values():
            continue
        body_words = [w for ln in body for w in ln.words]
        unit_words = [w for w in body_words if names.get(column_of(w.cx, blocks)) == "units"]
        labels = [
            band
            for band in split_bands(group_lines(unit_words))
            if common.parse_units(_band_text(band)).units
            or common.names_all_units(_band_text(band))
            or SEE_MAP_RE.search(_band_text(band))
        ]
        if not labels:
            continue
        centres = [_band_centre(band) for band in labels]
        cells: list[dict[str, list[Word]]] = [{} for _ in labels]
        for word in body_words:
            role = names.get(column_of(word.cx, blocks))
            if not role:
                continue
            centre = (word.top + word.bottom) / 2
            owner = min(range(len(centres)), key=lambda i: abs(centres[i] - centre))
            cells[owner].setdefault(role, []).append(word)
        for index, label in enumerate(labels):
            texts = {role: join_words(items) for role, items in cells[index].items()}
            boxes = [w for items in cells[index].values() for w in items] or [
                w for ln in label for w in ln.words
            ]
            out.append(
                GeneralRow(
                    units_text=_band_text(label),
                    dates_text=texts.get("dates", ""),
                    notes_text=texts.get("notes", ""),
                    title=title,
                    bbox=(
                        min(w.x0 for w in boxes),
                        min(w.top for w in boxes),
                        max(w.x1 for w in boxes),
                        max(w.bottom for w in boxes),
                    ),
                )
            )
    return out


def _name_blocks(header: TextLine, blocks: Sequence[tuple[float, float]]) -> dict[int, str]:
    """Block index → role, from the header's own words."""
    names: dict[int, str] = {}
    for word in header.words:
        text = word.text.lower().strip(".,")
        role = None
        if text in UNITS_WORDS:
            role = "units"
        elif text == "dates":
            role = "dates"
        elif text in NOTES_WORDS:
            role = "notes"
        if role is None:
            continue
        index = column_of(word.cx, blocks)
        if index >= 0:
            names.setdefault(index, role)
    return names


def _body_lines(lines: Sequence[TextLine]) -> list[TextLine]:
    """A table's body lines: stop at the footer, the numbered notes or the next title.

    The stop test is `TITLE_RE` — any `Spring Turkey` / `Fall Turkey` line — not the
    stricter `title_attributes`, because the banner that opens the next section
    (`2026 & 2027 Fall Turkey - General Seasons`, then `For turkey season limits, see
    page 20.`) names no sex and so is not a table title, yet it is certainly not a row.
    Letting it into the body silently cost the whole middle table: the banner spans the
    full page width, so `column_blocks` fused the date column into the units column,
    the header then matched no `dates` column, and the table was skipped — path A still
    read its 2 rows, which is exactly the one-sided loss `two_path_agreement` exists to
    catch (§12 T-23).
    """
    body: list[TextLine] = []
    for band in split_bands(list(lines)):
        text = " ".join(ln.text for ln in band)
        stripped = text.strip()
        if (
            FOOTER_LINE_RE.search(text)
            or stripped.lower().startswith("notes:")
            or NOTE_ITEM_RE.match(stripped)
            or TITLE_RE.search(stripped)
        ):
            break
        body += band
    return body


# ───────── rows → seasons ─────────


def _notes_codes(notes: str) -> tuple[list[str], list[str]]:
    """Restriction codes for a Notes cell, plus what nothing explained (D3).

    `See note N` is a pointer, not a condition: the note's text is attached verbatim,
    and the condition itself stays unexplained, so the row keeps `see_notes`.
    """
    text = _cell(notes)
    if not text:
        return [], []
    quals = common.qualifiers(text)
    codes = list(quals.codes)
    explained = text
    for pattern, _ in common.QUALIFIER_PATTERNS:
        explained = re.sub(pattern, " ", explained, flags=re.IGNORECASE)
    explained = NOTE_REF_RE.sub(" ", explained)
    explained = ARCHERY_RE.sub(" ", explained)  # a weapon, read as `weapon` not a code
    explained = re.sub(r"\barchery\s+permit\s+required\b", " ", explained, flags=re.IGNORECASE)
    explained = re.sub(r"[\s,.;()—–-]+", " ", explained)
    unmatched = list(quals.unmatched)
    if explained.strip():
        unmatched.append(text)
    if unmatched and "see_notes" not in codes:
        codes.append("see_notes")
    return codes, unmatched


def row_seasons(row: GeneralRow, ctx: ParseContext) -> list[GeneralSeason]:
    """Every season one printed row produces, one per window per license year.

    The brochure covers two license years and prints one date cell for both (§12 T-23),
    so each printed window recurs in `year_end` as well — one printed token, two
    seasons, exactly as T-25 does for the MSG brochure.
    """
    attributes = title_attributes(row.title)
    if attributes is None:
        return []
    hunt_class, sex, _season = attributes
    parsed = common.parse_units(row.units_text, ctx.units)
    codes, unmatched = _notes_codes(row.notes_text)
    quals = common.qualifiers(row.units_text)
    for code in quals.codes:
        if code not in codes:
            codes.append(code)
    unmatched += list(quals.unmatched)
    if hunt_class == "youth" and "youth_only" not in codes:
        codes.append("youth_only")
    weapon = "archery" if ARCHERY_RE.search(row.notes_text) else "any"
    see_map = bool(SEE_MAP_RE.search(row.units_text))
    if see_map:
        # `See map on page 23` names a **page**, and `parse_units` reads `23` as GMU 23
        # — a valid-looking unit nothing on the page says is in the hunt. The p. 23
        # maps are images and are not a data source (§12 T-23), so nothing is read out
        # of such a cell: the row carries no units, which makes the pipeline stage it
        # as a failing row for manual unit entry (D3: never guess).
        parsed = replace(parsed, units=[], unknown=[])
    ranges = common.parse_date_ranges(row.dates_text, ctx.license_year)
    leftovers = list(common.leftover_date_text(row.dates_text, ranges))
    both = ctx.year_end > ctx.license_year
    out: list[GeneralSeason] = []
    for found in ranges:
        start, end = _calendar_year(found, ctx)
        windows = [(start, end)]
        if both:
            windows.append((_add_year(start), _add_year(end)))
        for start, end in windows:
            out.append(
                GeneralSeason(
                    start=start,
                    end=end,
                    units=tuple(parsed.units),
                    hunt_class=hunt_class,
                    weapon=weapon,
                    sex=sex,
                    codes=tuple(dict.fromkeys(codes)),
                    units_text=row.units_text,
                    dates_text=row.dates_text,
                    notes_text=row.notes_text,
                    title=row.title,
                    unknown_units=tuple(parsed.unknown),
                    leftovers=tuple(dict.fromkeys(leftovers + unmatched)),
                    see_map=see_map,
                    bbox=row.bbox,
                )
            )
    return out


def _calendar_year(found: common.DateRange, ctx: ParseContext) -> tuple[dt.date, dt.date]:
    """A printed window dated by **calendar** year rather than the Jul→Jun license year.

    A turkey tag "expires on December 31 the year of purchase" (printed p. 22) and the
    tables are titled `2026 & 2027 Spring Turkey - General Seasons`, so the spring
    windows belong to `license_year` itself. §8.3's Jul→Jun rule puts any month before
    July into `license_year + 1`, which would date the 2026 spring season to 2027 and
    show the unit closed for the whole of the brochure's first year (§12 T-23).
    """
    start, end = found.start, found.end
    if start.year == ctx.license_year:
        return start, end
    shift = ctx.license_year - start.year
    return (
        start.replace(year=start.year + shift),
        end.replace(year=end.year + shift),
    )


def _add_year(day: dt.date) -> dt.date:
    """The same calendar day one year later (Feb 29 → Feb 28, as `dates.py` does)."""
    try:
        return day.replace(year=day.year + 1)
    except ValueError:
        return day.replace(year=day.year + 1, day=day.day - 1)


def to_parsed(season: GeneralSeason, page_number: int, *, agree: bool) -> ParsedSeason:
    """A compared `GeneralSeason` as the `ParsedSeason` the pipeline stages.

    A row whose units defer to the p. 23 **maps** (images, never a data source — §12
    T-23) is staged with confidence 0 and `see_notes` so review catches it for manual
    unit entry; it is never dropped and never published as if it were read (D3).
    """
    codes = list(season.codes)
    if season.see_map and "see_notes" not in codes:
        codes.append("see_notes")
    refs = [UnitRef(unit_id=unit) for unit in season.units]
    # An unreadable unit token is never dropped: it is staged as a failing row (D3).
    refs += [UnitRef(unit_id=token) for token in season.unknown_units]
    source = " | ".join(
        part
        for part in (season.title, season.units_text, season.dates_text, season.notes_text)
        if part.strip()
    )
    confidence = (
        0.0
        if season.see_map
        else common.confidence(
            unknown_units=len(season.unknown_units),
            unparsed_date_text=len(season.leftovers),
        )
    )
    return ParsedSeason(
        species_id=SPECIES_ID,
        tag_type_id=TAG_TYPE_ID,
        hunt_class=season.hunt_class,
        weapon=season.weapon,
        sex=season.sex,
        start=season.start,
        end=season.end,
        units=refs,
        restrictions=codes,
        notes=season.notes_text or None,
        source_page=page_number,
        source_text=source,
        source_bbox=list(season.bbox) if season.bbox else None,
        extract_agree=agree,
        confidence=confidence,
        section=SECTION,
    )


# ───────── per-document state and the §8.7 facts ─────────


@dataclass
class DocState:
    """What the general-season section carries across the pages of one document."""

    pages: list[int] = field(default_factory=list)
    rows: int = 0
    compared: int = 0
    disagreements: list[dict[str, object]] = field(default_factory=list)
    date_cells: list[DateCell] = field(default_factory=list)
    keys: list[tuple[str, ...]] = field(default_factory=list)
    unknown_units: list[tuple[int, str]] = field(default_factory=list)
    covered: set[str] = field(default_factory=set)
    texts: list[str] = field(default_factory=list)
    see_map: list[dict[str, object]] = field(default_factory=list)
    notes_items: dict[str, str] = field(default_factory=dict)


_STATE: dict[int, DocState] = {}


def state_for(ctx: ParseContext) -> DocState:
    return _STATE.setdefault(ctx.document_id, DocState())


def matches(page: Page) -> bool:
    """A page printing a general turkey season table (found by content, never number)."""
    if page.rotated:
        return False
    text = page.full_text
    return bool(TITLE_RE.search(text) and re.search(r"general\s+seasons?", text, re.IGNORECASE))


def parse_page(page: Page, ctx: ParseContext) -> Iterator[ParsedSeason]:
    """Parse one page with both paths, compare them, and yield the seasons to stage."""
    state = state_for(ctx)
    rows_a = rows_from_tables(page)
    rows_b = rows_from_text(page)
    if not rows_a and not rows_b:
        return
    state.pages.append(page.number)
    state.texts.append(page.full_text)
    for line in page.full_text.splitlines():
        if m := NOTE_ITEM_RE.match(line.strip()):
            state.notes_items.setdefault(m.group(1), m.group(2).strip())

    seasons_a = [s for row in rows_a for s in row_seasons(row, ctx)]
    seasons_b = [s for row in rows_b for s in row_seasons(row, ctx)]
    keys_b = {s.key for s in seasons_b}
    # Path A reads pdfplumber's ruled cells, which are plain strings with no geometry,
    # so a staged row would carry no `source_bbox` and review could not highlight it
    # (§8.6). Path B read the same row from word positions, so take the box from its
    # twin — it is only ever used to highlight, never compared (the T-13 rule).
    boxes_b = {s.key: s.bbox for s in seasons_b if s.bbox}

    for row in rows_a:
        windows = common.parse_date_ranges(row.dates_text, ctx.license_year)
        produced = [s for s in seasons_a if s.dates_text == row.dates_text]
        state.date_cells.append(
            DateCell(
                page=page.number,
                texts=(row.dates_text,),
                seasons=len(produced),
                label=row.units_text[:60],
                leftovers=tuple(common.leftover_date_text(row.dates_text, windows)),
                bbox=boxes_b.get(produced[0].key) if produced else None,
                must_produce=bool(common.parse_units(row.units_text, ctx.units).units),
                # One printed cell covers both license years (§12 T-23).
                recurring=ctx.year_end > ctx.license_year,
            )
        )

    for season in seasons_a:
        agree = season.key in keys_b
        if season.bbox is None and (box := boxes_b.get(season.key)):
            season = replace(season, bbox=box)
        state.compared += 1
        if not agree:
            state.disagreements.append(
                {
                    "page": page.number,
                    "title": season.title,
                    "table": list(season.key),
                    "text": [list(s.key) for s in seasons_b if s.units_text == season.units_text],
                }
            )
        state.rows += 1
        state.covered |= set(season.units)
        state.unknown_units += [(page.number, u) for u in season.unknown_units]
        state.keys.append(season.key)
        if season.see_map:
            state.see_map.append(
                {"page": page.number, "title": season.title, "units_text": season.units_text}
            )
        yield to_parsed(season, page.number, agree=agree)


def begin(ctx: ParseContext) -> None:
    _STATE[ctx.document_id] = DocState()


def finalize(ctx: ParseContext) -> SectionFacts | None:
    """What `turkey_general` measured, for the §8.7 check registry (T-22a)."""
    state = _STATE.get(ctx.document_id)
    if state is None or not state.rows:
        return None
    return SectionFacts(
        section=SECTION,
        two_path=TwoPath(
            compared=state.compared,
            disagreeing=len(state.disagreements),
            disagreements=state.disagreements,
        ),
        date_cells=state.date_cells,
        unknown_units=state.unknown_units,
        keys=state.keys,
        key_label="(season window, units, hunt class, weapon, sex, codes)",
        # Turkey is not statewide: the brochure prints general seasons for part of the
        # state and controlled hunts elsewhere, so `general_coverage` is measured
        # against the units these tables name, never against every GMU (§12 T-23).
        coverage=Coverage(
            covered=frozenset(state.covered),
            all_units=tuple(state.covered),
            excluded=frozenset(),
            texts=tuple(state.texts),
        ),
        extra={
            "see_map_rows": state.see_map,
            "notes": state.notes_items,
            "pages": state.pages,
        },
    )


parser(
    kind="upland",
    match=matches,
    name=SECTION,
    section=SECTION,
    begin=begin,
    finalize=finalize,
)(parse_page)
