"""Upland game bird seasons, DESIGN.md §5.2/§5.3 (§12 T-14 part b).

The Upland Game, Turkey & Furbearer brochure prints its bird seasons as **prose**, not
as tables: a species banner, then one or more area blocks ::

    FOREST GROUSE: DUSKY (BLUE), RUFFED, AND SPRUCE
    AREA 1
    Benewah, Bonner, … counties, and Adams and Idaho counties, except those
    portions in game management units 19A, 20A, 22, 23, and 26.
    Seasons
    2026 – 2027: August 30, 2026 through January 31, 2027
    2027 – 2028: August 30, 2027 through January 31, 2028
    Daily Bag Limit ……… 4 in the aggregate
    Possession Limit …… 12 in the aggregate

so there is nothing for `find_tables` to read. The two independent paths (§8.7
principle 3) are therefore

* **path A** — the page's text layer in reading order, filtered by *content* only;
* **path B** — word geometry: the body column, at body font size.

They matter here: page 10 prints a county map **over** the season column, and its
labels interleave into the reading-order text (`Seasons PERCE`, `… Franklin, BENEWAH`,
and a `Possession Limit` whose dotted leader lands on its own line). Path A rejects
them because the brochure prints county names in prose as Title Case and map labels in
caps; path B rejects them by position and font size. Neither rule could rescue the
other, which is what makes the agreement evidence.

Two more brochure facts drive this module:

* the vertical section tab reaches x1 ≤ 28.5 (even pages) / x0 ≥ 565 (odd pages) while
  the body column starts at x0 = 35.5, so the fixed 40 pt side crop cuts real text —
  everything here reads `page.full_text` / `page.full_words` (T-13's tab-only crop);
* the brochure covers **two** license years (`2026 – 2027` and `2027 – 2028`), and the
  document's year range is Jul 1 2026 – Jun 30 2028, so both years are staged.
"""

from __future__ import annotations

import datetime as dt
import json
import re
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, field, replace
from functools import lru_cache
from pathlib import Path
from typing import Any

from planner.importer import (
    ParseContext,
    ParsedSeason,
    ParserSpec,
    UnitRef,
    common,
    register,
)
from planner.importer.checks import DateCell, SectionFacts, TwoPath
from planner.importer.extract import Page, Word

__all__ = [
    "BirdSection",
    "SECTIONS",
    "blocks_of",
    "county_units",
    "lines_geometry",
    "lines_text",
    "resolve_definition",
]

#: `static/data/county_units.json` — the county × GMU overlay built by part (a).
COUNTY_FILE = Path(__file__).resolve().parents[2] / "static" / "data" / "county_units.json"

#: §5.2: a GMU at least this far inside the area's counties is `full`, …
FULL_COVERAGE = 0.98
#: … above this it is `partial`, and below it the overlap is border noise.
MIN_COVERAGE = 0.02


# ───────── the sections this parser claims ─────────


@dataclass(frozen=True)
class BirdSection:
    """One upland game bird section of the brochure (§5.3)."""

    species_id: str
    #: `import_checks.section` / `seasons.section`, and the golden file's name.
    section: str
    #: Full-line banner that opens the section's page.
    banner: re.Pattern[str]
    #: The species' share of a shared bag limit (`8 Chukar & 8 Gray Partridge`).
    bag_word: str | None = None
    #: The brochure prints no season: it is set later, in a separate brochure (§8.7).
    deferred: str | None = None
    #: Printed permit sentence a hunter must also hold, kept on every row's notes.
    permit_note: str | None = None
    #: §5.1 codes every row of the section carries.
    restrictions: tuple[str, ...] = ()


SECTIONS: tuple[BirdSection, ...] = (
    BirdSection("forest_grouse", "upland_forest_grouse", re.compile(r"FOREST GROUSE\b.*")),
    BirdSection("quail", "upland_quail", re.compile(r"CALIFORNIA AND BOBWHITE QUAIL\s*")),
    BirdSection(
        "chukar",
        "upland_chukar",
        re.compile(r"CHUKAR AND GRAY PARTRIDGE\s*"),
        bag_word="Chukar",
    ),
    BirdSection(
        "gray_partridge",
        "upland_gray_partridge",
        re.compile(r"CHUKAR AND GRAY PARTRIDGE\s*"),
        bag_word="Gray Partridge",
    ),
    BirdSection(
        "sage_grouse",
        "upland_sage_grouse",
        re.compile(r"SAGE-GROUSE\s*"),
        deferred=(
            "Sage-grouse seasons are set in July and published in a separate brochure,"
            " so this brochure prints none."
        ),
    ),
    BirdSection(
        "sharptail",
        "upland_sharptail",
        re.compile(r"SHARP-TAILED GROUSE\s*"),
        permit_note="Requires a sharp-tailed grouse permit validation.",
    ),
    BirdSection(
        "pheasant",
        "upland_pheasant",
        re.compile(r"PHEASANTS?\b.*"),
        permit_note=(
            "An Upland Game Bird Permit is required to hunt pheasants on the"
            " Upland Game Bird Permit Release Areas (WMAs)."
        ),
        restrictions=("wmb_permit_on_wma",),
    ),
)


# ───────── line model ─────────


@dataclass(frozen=True)
class Line:
    """One printed line, with the bbox path B knows and path A does not."""

    text: str
    bbox: tuple[float, float, float, float] | None = None


_AREA_RE = re.compile(r"^(?:\S{1,2}\s+)?AREA\s+(\d+)\b")
_STATEWIDE_RE = re.compile(r"^(?:\S{1,2}\s+)?STATEWIDE\b")
_SEASONS_RE = re.compile(r"^(?:\S{1,2}\s+)?Seasons\b")
_YOUTH_RE = re.compile(r"^(?:\S{1,2}\s+)?Youth Hunt Season\b")
_DAILY_RE = re.compile(r"^(?:\S{1,2}\s+)?Daily Bag Limit\b")
_POSS_RE = re.compile(r"^(?:\S{1,2}\s+)?Possession Limit\b")
#: The block ends where the species' natural-history column starts.
_STOP_RE = re.compile(r"^(Distribution and Habitat Use|Note:)")
#: `2026 – 2027: …`, `2026: …`, and the youth block's em-dash form `2026 — …`.
#: The youth block separates the year from its window with a dash, not a colon.
_YEAR_LINE_RE = re.compile(r"^(\d{4})(?:\s*[–—-]\s*(\d{4}))?\s*[:—–-]\s*(.+)$")
#: D1: the brochure prints the nonresident opener beside the resident one.
_RESIDENT_RE = re.compile(r"^Resident\s*:\s*(.+)$", re.IGNORECASE)
_NONRESIDENT_RE = re.compile(r"^Nonresident\s*:\s*(.+)$", re.IGNORECASE)
#: A dotted leader that lost its label (a map label split the printed line).
_ORPHAN_TAIL_RE = re.compile(r"^[.\s]*\d[\d,]*\s*(?:in the aggregate|roosters?\*?)?\s*$")
#: What a printed limit value may say — anything after it is a stray caption.
_LIMIT_VALUE_RE = re.compile(
    r"\d[\d,]*(?:\s*(?:&|and|in|the|aggregate|roosters?\*?|Chukar|Gray|Partridge|\d+))*",
    re.IGNORECASE,
)
_FOOTNOTE_RE = re.compile(r"^\*?\s*Daily bag limit\s*:", re.IGNORECASE)
#: The map labels printed over the sharp-tailed page, and nothing the prose needs.
_CAPS_RE = re.compile(r"\b[A-Z][A-Z’'-]{2,}\b")
#: …except these, which the season blocks really do print in caps.
_CAPS_KEEP = frozenset({"AREA", "STATEWIDE", "CLOSED", "NO", "SEASON", "SEASONS", "WMA", "WMAS"})
_EXCEPT_UNITS_RE = re.compile(
    r"except[^.]*?\b(?:game management\s+)?units?\s+"
    r"(?P<list>[0-9A-Z,\s]+?and\s+[0-9A-Z]+|[0-9A-Z,\s]+)",
    re.IGNORECASE,
)
_REMAINDER_RE = re.compile(r"remainder of the state", re.IGNORECASE)


def _strip_caps(text: str) -> str:
    """Drop ALL-CAPS map labels — path A's only defence, and a content rule."""
    out = _CAPS_RE.sub(lambda m: m.group(0) if m.group(0) in _CAPS_KEEP else " ", text)
    # Strip whitespace only: a county list wraps mid-clause (`… Kootenai, Latah,` /
    # `Lewis, Nez Perce, …`), and dropping the trailing comma breaks the run in two,
    # which silently costs the area every county printed before the line break.
    return re.sub(r"\s{2,}", " ", out).strip()


def _merge_orphan_leaders(lines: list[Line]) -> list[Line]:
    """Re-attach a dotted leader that was printed away from its `… Limit` label."""
    out: list[Line] = []
    pending: list[Line] = []
    for line in lines:
        if _ORPHAN_TAIL_RE.match(line.text):
            for i in range(len(out) - 1, -1, -1):
                if _is_limit(out[i].text) and not re.search(r"\d", out[i].text):
                    out[i] = Line(f"{out[i].text} {line.text.lstrip('. ')}", out[i].bbox)
                    break
            else:
                pending.append(line)
            continue
        if pending and _is_limit(line.text) and not re.search(r"\d", line.text):
            tail = pending.pop(0)
            line = Line(f"{line.text} {tail.text.lstrip('. ')}", line.bbox)
        out.append(line)
    return out


def _is_limit(text: str) -> bool:
    return bool(_DAILY_RE.match(text) or _POSS_RE.match(text))


def lines_text(page: Page) -> list[Line]:
    """Path A: the page's text layer in reading order, cleaned by content rules only."""
    raw = [ln.strip() for ln in page.full_text.splitlines() if ln.strip()]
    return _merge_orphan_leaders([Line(ln) for ln in raw])


#: The body column of a bird page, and the body font sizes printed in it.
COLUMN_X1 = 340.0
#: Body text is 10–11 pt and the species banner 14 pt; the county map printed over the
#: sharp-tailed page labels its counties at 2.7 pt and its areas at 24 pt.
BODY_MIN_H, BODY_TEXT_MAX_H, BODY_MAX_H = 8.0, 13.0, 16.0
LINE_GAP = 5.0


def lines_geometry(page: Page, column_x1: float = COLUMN_X1) -> list[Line]:
    """Path B: the body column read from word positions, with each line's bbox."""
    words = [
        w
        for w in page.full_words
        if BODY_MIN_H <= (w.bottom - w.top) <= BODY_MAX_H
        # The species banner is centred across the page; everything else this path
        # reads is in the body column.
        and (w.x1 <= column_x1 or (w.bottom - w.top) > BODY_TEXT_MAX_H)
    ]
    words.sort(key=lambda w: ((w.top + w.bottom) / 2, w.x0))
    rows: list[list[Word]] = []
    for w in words:
        centre = (w.top + w.bottom) / 2
        if rows and centre - (rows[-1][0].top + rows[-1][0].bottom) / 2 <= LINE_GAP:
            rows[-1].append(w)
        else:
            rows.append([w])
    out: list[Line] = []
    for row in rows:
        row.sort(key=lambda w: w.x0)
        bbox = (
            min(w.x0 for w in row),
            min(w.top for w in row),
            max(w.x1 for w in row),
            max(w.bottom for w in row),
        )
        out.append(Line(" ".join(w.text for w in row).strip(), bbox))
    return _merge_orphan_leaders(out)


# ───────── block structure ─────────


@dataclass
class Block:
    """One `AREA n` / `STATEWIDE` block of a bird section."""

    area: str
    definition: list[str] = field(default_factory=list)
    #: `(printed line, hunt_class)` for every season line of the block.
    season_lines: list[tuple[str, str]] = field(default_factory=list)
    daily: str | None = None
    possession: str | None = None
    notes: list[str] = field(default_factory=list)
    bboxes: list[tuple[float, float, float, float]] = field(default_factory=list)

    @property
    def definition_text(self) -> str:
        return " ".join(self.definition).strip()

    @property
    def bbox(self) -> tuple[float, float, float, float] | None:
        if not self.bboxes:
            return None
        return (
            min(b[0] for b in self.bboxes),
            min(b[1] for b in self.bboxes),
            max(b[2] for b in self.bboxes),
            max(b[3] for b in self.bboxes),
        )


def _limit_value(text: str) -> str | None:
    value = re.sub(r"^(?:\S{1,2}\s+)?(Daily Bag Limit|Possession Limit)", "", text).strip(" .")
    value = _strip_caps(value)
    m = _LIMIT_VALUE_RE.search(value)
    return m.group(0).strip(" .,") if m else None


def blocks_of(lines: Sequence[Line], section: BirdSection) -> list[Block]:
    """The area blocks a bird section prints, from either path's lines."""
    out: list[Block] = []
    cur: Block | None = None
    mode = "definition"
    started = False
    for line in lines:
        text = line.text.strip()
        if not started:
            if section.banner.fullmatch(text):
                started = True
            continue
        if _STOP_RE.match(text):
            break
        if m := _AREA_RE.match(text):
            cur = Block(area=m.group(1))
            out.append(cur)
            mode = "definition"
            continue
        if _STATEWIDE_RE.match(text):
            cur = Block(area="statewide")
            out.append(cur)
            mode = "definition"
            continue
        if cur is None:
            continue
        if line.bbox is not None:
            cur.bboxes.append(line.bbox)
        if _SEASONS_RE.match(text):
            mode = "general"
            continue
        if _YOUTH_RE.match(text):
            mode = "youth"
            continue
        if _DAILY_RE.match(text):
            cur.daily = _limit_value(text)
            continue
        if _POSS_RE.match(text):
            cur.possession = _limit_value(text)
            continue
        if _FOOTNOTE_RE.match(_strip_caps(text)):
            cur.notes.append(_strip_caps(text).lstrip("*").strip())
            continue
        if mode == "definition":
            # A line that was *only* a map label is now empty: dropping it keeps the
            # joined definition byte-identical to path B's.
            if cleaned := _strip_caps(text):
                cur.definition.append(cleaned)
        elif _YEAR_LINE_RE.match(text) or _NONRESIDENT_RE.match(text):
            cur.season_lines.append((text, mode))
    return out


# ───────── §5.2 area → units ─────────


@lru_cache(maxsize=1)
def county_units(path: str | None = None) -> dict[str, dict[str, float]]:
    """The county × GMU overlay: `{county: {unit_id: fraction of the GMU}}` (part a)."""
    data = json.loads(Path(path or COUNTY_FILE).read_text(encoding="utf-8"))
    return {name: dict(units) for name, units in data["counties"].items()}


@lru_cache(maxsize=1)
def _county_pattern() -> re.Pattern[str]:
    names = sorted(county_units(), key=len, reverse=True)
    alt = "|".join(re.escape(n) for n in names)
    return re.compile(
        rf"(?P<names>(?:{alt})(?:\s*,\s*(?:and\s+)?(?:{alt})|\s+and\s+(?:{alt}))*)"
        rf"\s+[Cc]ount(?:y|ies)(?P<qualifier>[^,.;]*)",
        re.IGNORECASE,
    )


@dataclass(frozen=True)
class CountyGroup:
    """One `A, B and C counties <qualifier>` clause of an area definition."""

    counties: tuple[str, ...]
    qualifier: str

    @property
    def qualified(self) -> bool:
        """The clause names a boundary inside the counties, which no overlay can map."""
        return bool(self.qualifier.strip())


def county_groups(text: str) -> list[CountyGroup]:
    """Every county clause printed in an area definition, in order."""
    known = {name.lower(): name for name in county_units()}
    out: list[CountyGroup] = []
    for m in _county_pattern().finditer(text):
        names = tuple(
            known[part.strip().lower()]
            # `, and Shoshone` must split to `Shoshone`, not to `and Shoshone`: a
            # name the index does not know is dropped, so the area silently loses its
            # last county (and with it the whole Panhandle).
            for part in re.split(r"\s*,\s*(?:and\s+)?|\s+and\s+", m.group("names"))
            if part.strip().lower() in known
        )
        if names:
            out.append(CountyGroup(names, m.group("qualifier").strip()))
    return out


#: A `… counties` clause, for the residue check below.
_COUNTY_CLAUSE_RE = re.compile(r"(?P<names>[^,.;]*?)\s*\b[Cc]ount(?:y|ies)\b")


def county_clause_residue(text: str) -> list[str]:
    """Words a `… counties` clause prints that resolved to no county (§8.7).

    A dropped county name is invisible: both paths read the same text, so they agree
    on the smaller area. The only way to notice is to subtract the counties that *did*
    resolve from the clause the brochure printed and insist nothing but separators is
    left over — which is how `, and Shoshone` splitting to `and Shoshone` was caught.
    """
    out: list[str] = []
    known = {name.lower() for name in county_units()}
    for m in _COUNTY_CLAUSE_RE.finditer(text):
        names = m.group("names")
        if not names.strip():
            continue
        residue = names
        for part in sorted(known, key=len, reverse=True):
            residue = re.sub(rf"(?i)\b{re.escape(part)}\b", " ", residue)
        for word in re.split(r"[,\s]+", residue):
            word = word.strip(" ,.")
            # Only a capitalised leftover can be a county the parser dropped; the
            # clause's prose ("those portions of", "east of Interstate 15") is not.
            if word and word[0].isupper() and word.lower() not in {"and", "county", "counties"}:
                out.append(word)
    return out


def except_units(text: str, known: Sequence[str]) -> tuple[list[str], list[str]]:
    """Units an `except those portions in game management units …` clause removes."""
    m = _EXCEPT_UNITS_RE.search(text)
    if not m:
        return [], []
    parsed = common.parse_units(m.group("list"), known)
    return list(parsed.units), list(parsed.unknown)


@dataclass(frozen=True)
class AreaUnits:
    """What one area definition resolved to (§5.2)."""

    refs: tuple[UnitRef, ...]
    counties: tuple[str, ...]
    qualified: bool
    removed: tuple[str, ...] = ()
    unknown_tokens: tuple[str, ...] = ()
    statewide: bool = False
    remainder: bool = False
    closed: bool = False

    @property
    def full_units(self) -> frozenset[str]:
        return frozenset(r.unit_id for r in self.refs if r.coverage == "full")

    @property
    def partial_units(self) -> frozenset[str]:
        return frozenset(r.unit_id for r in self.refs if r.coverage == "partial")


def resolve_definition(
    definition: str,
    *,
    area: str,
    all_units: Sequence[str],
    siblings: Sequence[AreaUnits] = (),
) -> AreaUnits:
    """Resolve one area definition to `season_units` rows, per §5.2.

    County lists become units by overlap (≥ 98 % `full`, 2–98 % `partial`); an
    `except … units X` clause removes those units outright; `Remainder of the state`
    takes everything the sibling areas did not cover in full, keeping a unit that was
    `partial` there `partial` here.
    """
    text = definition.strip()
    low = text.lower()
    # Yellowstone is closed to hunting, so it is never part of "statewide" or of the
    # "remainder of the state" (§8.3 `CLOSED_UNITS`, the same rule the deer parser uses).
    known = [u for u in all_units if u not in common.CLOSED_UNITS]

    if _REMAINDER_RE.search(low):
        if re.search(r"\bclosed\b", low):
            return AreaUnits((), (), False, remainder=True, closed=True)
        taken = frozenset().union(*[s.full_units for s in siblings]) if siblings else frozenset()
        partial = (
            frozenset().union(*[s.partial_units for s in siblings]) if siblings else frozenset()
        )
        refs = tuple(
            UnitRef(
                unit,
                coverage="partial" if unit in partial else "full",
                coverage_note=(
                    "Partly outside this area: the rest of the unit lies in the"
                    " neighbouring upland area"
                    if unit in partial
                    else None
                ),
            )
            for unit in known
            if unit not in taken
        )
        return AreaUnits(refs, (), False, remainder=True)

    if not county_groups(text) and re.search(r"\bstatewide\b", low) or area == "statewide":
        return AreaUnits(tuple(UnitRef(u) for u in known), (), False, statewide=True)

    groups = county_groups(text)
    removed, unknown = except_units(text, known)
    fractions: dict[str, float] = {}
    contributors: dict[str, list[str]] = {}
    qualified_units: set[str] = set()
    counties: list[str] = []
    index = county_units()
    for group in groups:
        for name in group.counties:
            counties.append(name)
            for unit, frac in index.get(name, {}).items():
                fractions[unit] = min(1.0, fractions.get(unit, 0.0) + float(frac))
                contributors.setdefault(unit, []).append(name)
                if group.qualified:
                    qualified_units.add(unit)
    out_refs: list[UnitRef] = []
    for unit in known:
        frac = fractions.get(unit, 0.0)
        if unit in removed or frac < MIN_COVERAGE:
            continue
        full = frac >= FULL_COVERAGE and unit not in qualified_units
        note = None
        if not full:
            named_list = list(dict.fromkeys(contributors.get(unit, ())))
            named = ", ".join(named_list)
            word = "county" if len(named_list) == 1 else "counties"
            note = f"Only the part of this unit in {named} {word}"
            if unit in qualified_units:
                note += ", and only the portion the area description names"
        out_refs.append(
            UnitRef(
                unit,
                coverage="full" if full else "partial",
                coverage_note=note,
                coverage_frac=None if full else round(frac, 4),
            )
        )
    return AreaUnits(
        refs=tuple(out_refs),
        counties=tuple(dict.fromkeys(counties)),
        qualified=any(g.qualified for g in groups),
        removed=tuple(removed),
        unknown_tokens=tuple(unknown),
    )


# ───────── seasons out of a block ─────────


@dataclass(frozen=True)
class Window:
    """One printed season window of a block."""

    start: dt.date
    end: dt.date
    hunt_class: str
    text: str
    year_label: str


def _parse_year_line(
    line: str, license_year: int, hunt_class: str
) -> tuple[list[Window], list[str], list[str]]:
    """Windows, nonresident-only windows and leftover date text of one printed line."""
    m = _YEAR_LINE_RE.match(line)
    if not m:
        if nm := _NONRESIDENT_RE.match(line.strip()):
            return [], [nm.group(1).strip()], []
        return [], [], common.leftover_date_text(line, [])
    label, body = m.group(1), m.group(3).strip()
    nonresident: list[str] = []
    if rm := _RESIDENT_RE.match(body):
        body = rm.group(1).strip()
    ranges = common.parse_date_ranges(body, license_year)
    windows = [Window(r.start, r.end, hunt_class, r.text, label) for r in ranges]
    return windows, nonresident, common.leftover_date_text(body, ranges)


def _bag_limit(block: Block, section: BirdSection) -> str | None:
    """`Daily 4 in the aggregate; possession 12 in the aggregate`, per species."""
    daily, possession = block.daily, block.possession
    if section.bag_word:
        daily = _species_share(daily, section.bag_word)
        possession = _species_share(possession, section.bag_word)
    parts = []
    if daily:
        parts.append(f"Daily {daily}")
    if possession:
        parts.append(f"possession {possession}")
    return "; ".join(parts) or None


def _species_share(value: str | None, word: str) -> str | None:
    """`8 Chukar & 8 Gray Partridge` → this species' half of a shared limit."""
    if not value:
        return None
    for part in re.split(r"\s*&\s*", value):
        if word.lower() in part.lower():
            return part.strip()
    return value


# ───────── per-document state ─────────


@dataclass
class _State:
    section: BirdSection
    pages: list[int] = field(default_factory=list)
    date_cells: list[DateCell] = field(default_factory=list)
    keys: list[tuple[str, ...]] = field(default_factory=list)
    unknown_units: list[tuple[int, str]] = field(default_factory=list)
    compared: int = 0
    disagreements: list[dict[str, Any]] = field(default_factory=list)
    nonresident: list[dict[str, Any]] = field(default_factory=list)
    areas: list[dict[str, Any]] = field(default_factory=list)
    #: `(area, printed year, hunt class)` for every window this section staged.
    windows: list[tuple[str, str, str]] = field(default_factory=list)
    #: Capitalised words of a county clause that resolved to no county.
    county_residue: list[dict[str, Any]] = field(default_factory=list)
    rows: int = 0


_STATE: dict[str, _State] = {}


def _state(section: BirdSection) -> _State:
    return _STATE.setdefault(section.section, _State(section))


def _row_key(row: ParsedSeason) -> tuple[Any, ...]:
    return (
        row.hunt_area or "",
        row.hunt_class,
        row.start.isoformat(),
        row.end.isoformat(),
        tuple(sorted(r.unit_id for r in row.unit_refs())),
        tuple(sorted((r.unit_id, r.coverage) for r in row.unit_refs())),
        row.bag_limit or "",
    )


def _rows_for(
    page: Page,
    ctx: ParseContext,
    section: BirdSection,
    lines: Sequence[Line],
    *,
    record: bool,
) -> list[ParsedSeason]:
    """Every season a path's lines produce for this section's page."""
    state = _state(section)
    blocks = blocks_of(lines, section)
    resolved: list[AreaUnits] = []
    for block in blocks:
        text = block.definition_text
        area = resolve_definition(
            text, area=block.area, all_units=ctx.units, siblings=list(resolved)
        )
        resolved.append(area)
    rows: list[ParsedSeason] = []
    for block, area in zip(blocks, resolved, strict=True):
        if record:
            state.areas.append(
                {
                    "area": block.area,
                    "definition": block.definition_text,
                    "counties": list(area.counties),
                    "units": len(area.refs),
                    "partial": sorted(area.partial_units, key=common.natural_key),
                    "removed": list(area.removed),
                    "closed": area.closed,
                }
            )
            state.unknown_units.extend((page.number, t) for t in area.unknown_tokens)
            state.county_residue.extend(
                {"page": page.number, "area": block.area, "word": w, "definition": text}
                for w in county_clause_residue(text)
            )
        bag = _bag_limit(block, section)
        last_label = ""
        for line, hunt_class in block.season_lines:
            windows, nonresident, leftovers = _parse_year_line(line, ctx.license_year, hunt_class)
            if windows:
                last_label = windows[0].year_label
            if record:
                state.windows.extend((block.area, w.year_label, w.hunt_class) for w in windows)
                if nonresident:
                    state.nonresident.extend(
                        {
                            "page": page.number,
                            "area": block.area,
                            "year": last_label,
                            "hunt_class": hunt_class,
                            "text": t,
                        }
                        for t in nonresident
                    )
                else:
                    state.date_cells.append(
                        DateCell(
                            page=page.number,
                            texts=(line,),
                            seasons=len(windows),
                            label=f"AREA {block.area}",
                            leftovers=tuple(leftovers),
                            bbox=block.bbox,
                            must_produce=not area.closed,
                        )
                    )
            if area.closed or not area.refs:
                continue
            for window in windows:
                restrictions = list(section.restrictions)
                if window.hunt_class == "youth":
                    restrictions.append("youth_only")
                if area.qualified:
                    restrictions.append("see_notes")
                notes = [*block.notes]
                if section.permit_note:
                    notes.insert(0, section.permit_note)
                if area.qualified:
                    notes.append(f"Area description: {block.definition_text}")
                rows.append(
                    ParsedSeason(
                        species_id=section.species_id,
                        tag_type_id="hunting_license",
                        hunt_class=window.hunt_class,
                        weapon="any",
                        sex="any",
                        start=window.start,
                        end=window.end,
                        units=area.refs,
                        hunt_area=f"AREA {block.area}"
                        if block.area != "statewide"
                        else "Statewide",
                        restrictions=tuple(dict.fromkeys(restrictions)),
                        bag_limit=bag,
                        notes=" ".join(notes) or None,
                        source_page=page.number,
                        source_text=line,
                        source_bbox=block.bbox,
                        section=section.section,
                    )
                )
    return rows


def _parse(page: Page, ctx: ParseContext, section: BirdSection) -> Iterator[ParsedSeason]:
    state = _state(section)
    state.pages.append(page.number)
    text_rows = _rows_for(page, ctx, section, lines_text(page), record=True)
    geom_lines = lines_geometry(page)
    geom_rows = _rows_for(page, ctx, section, geom_lines, record=False)
    geom_by_key = {_row_key(r): r for r in geom_rows}
    text_by_key = {_row_key(r): r for r in text_rows}
    state.compared += max(len(text_rows), len(geom_rows))
    for key in sorted(set(text_by_key) ^ set(geom_by_key), key=repr):
        state.disagreements.append(
            {
                "page": page.number,
                "path": "text" if key in text_by_key else "geometry",
                "row": list(key),
            }
        )
    for row in text_rows:
        twin = geom_by_key.get(_row_key(row))
        state.rows += 1
        state.keys.append(
            (row.hunt_area or "", row.hunt_class, row.start.isoformat(), row.end.isoformat())
        )
        if twin is None:
            yield row
        else:
            # Path A stages the row; path B's geometry supplies the bbox the review
            # screen highlights (§8.6) — path A has none to give.
            yield replace(row, extract_agree=True, source_bbox=twin.source_bbox or row.source_bbox)


def _begin(section: BirdSection) -> Any:
    def begin(ctx: ParseContext) -> None:
        _STATE[section.section] = _State(section)

    return begin


def _finalize(section: BirdSection) -> Any:
    def finalize(ctx: ParseContext) -> SectionFacts | None:
        state = _STATE.get(section.section)
        if state is None:
            return None
        extra: dict[str, Any] = {
            "areas": state.areas,
            "windows": [list(w) for w in state.windows],
            "county_residue": state.county_residue,
        }
        if state.nonresident:
            extra["nonresident_windows"] = state.nonresident
        if section.deferred:
            extra["deferred_season"] = {
                "reason": section.deferred,
                "pages": state.pages,
                "rows": state.rows,
            }
        if not state.pages:
            return None
        return SectionFacts(
            section=section.section,
            two_path=TwoPath(
                compared=state.compared,
                disagreeing=len(state.disagreements),
                disagreements=state.disagreements,
            ),
            date_cells=state.date_cells or None,
            unknown_units=state.unknown_units,
            keys=state.keys or None,
            key_label="area and window",
            extra=extra,
        )

    return finalize


def _make_parser(section: BirdSection) -> ParserSpec:
    def match(page: Page) -> bool:
        return any(section.banner.fullmatch(ln.strip()) for ln in page.full_text.splitlines())

    def fn(page: Page, ctx: ParseContext) -> Iterable[ParsedSeason]:
        return _parse(page, ctx, section)

    return register(
        ParserSpec(
            name=section.section,
            kind="upland",
            match=match,
            fn=fn,
            section=section.section,
            begin=_begin(section),
            finalize=_finalize(section),
        )
    )


PARSERS: tuple[ParserSpec, ...] = tuple(_make_parser(s) for s in SECTIONS)
