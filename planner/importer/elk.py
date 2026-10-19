"""Elk A/B zone season pages of the Big Game brochure, §12 T-12.

The zone pages are landscape **calendar artwork rotated 90°** — there is no ruled table
to extract at all. `extract.py` reads such a page in its own frame (`Page.rot_words`,
`rot_rects`, `rot_rules`: `x` runs along a printed line, `top`/`bottom` march down the
printed page), and this module turns it into one section, `elk_zones`.

The layout, as measured in T-12a (`docs/elk_parser_notes.md`):

    Panhandle Zone (Units 1, 2, 3, 4, 4A, 5, 6, 7, 9)      ← title band, fill 0.706…
    Chronic Wasting Disease Transport Rules apply …        ← preamble (not a season)
            | September | October | November | December |  ← month strip, fill 0.815…
    A Tag   | Archery only — antlered only                 ← row band, entries printed
            | Aug 30 - Sep 30                                side by side inside it
    B Tag   | …
    Note A  …                                              ← per-block legend

**Two independent extraction paths (§8.7 principle 3).** Path A reads the *drawn*
skeleton: the tan title band and month strip give the table's extent and its columns,
and the 0.5 pt hairlines give the row bands. Path B never looks at a rect or a rule: it
rebuilds the same blocks from word positions alone (`textlayer`'s line/band/column
helpers). Both then interpret an entry with the same code — what the paths disagree
about is the *geometry*, which is where these pages go wrong.

**Row bands, never per-column cells.** An entry is routinely centred across two month
columns, so cutting on a column edge splits `Oct 10 - Nov 3` in half and loses the
season silently — it cost 11 of 95 seasons in the spike, every one a B-tag row. The
month columns are kept as a *cross-check* on the parsed months (`month_mismatch`),
never as a boundary.
"""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, field, replace

from planner import db
from planner.dates import to_iso
from planner.importer import ParseContext, ParsedSeason, UnitRef, common, parser
from planner.importer.checks import DateCell, MethodWindow, SectionFacts, TwoPath
from planner.importer.extract import Page, Shape, Word
from planner.importer.textlayer import TextLine, join_words

#: The section every elk zone season is staged under.
SECTION = "elk_zones"
SPECIES = "elk"
HUNT_CLASS = "general"
#: `A Tag` / `B Tag` → `tag_types.id` (§5 `requires_zone`).
TAG_TYPES = {"A": "elk_zone_a", "B": "elk_zone_b"}

#: Fill colours of the drawn skeleton, measured on the 2026 pages.
TITLE_FILL = (0.706, 0.727, 0.661)
MONTH_FILL = (0.815, 0.782, 0.66)
#: A rule is a *band edge* when it covers this share of the table's width.
BAND_COVERAGE = 0.6
#: Points of white space that separate two entries printed side by side.
ENTRY_GAP_PT = 9.0
#: Most vertical space between two printed lines of the *same* entry.
ENTRY_LINE_GAP_PT = 15.0
#: A hairline is this thin across the printed line it separates.
RULE_THICKNESS_PT = 2.0

_ZONE_HEADER_RE = re.compile(
    r"(?P<name>[A-Z][A-Za-z.\-– ]+?)\s+Zone\s+\(Units?\s+(?P<units>[^)]*)\)(?P<tail>.*)$"
)
#: `No B Tags in this Zone — See Controlled Hunts`: a statement, never a season.
_NO_TAG_RE = re.compile(r"No\s+([AB])\s+Tags?\s+in\s+this\s+Zone", re.IGNORECASE)
#: A zone whose every hunt is controlled prints no calendar at all.
_CONTROLLED_ONLY_RE = re.compile(r"Controlled\s+Hunts?\s+Only", re.IGNORECASE)
_TAG_LABEL_RE = re.compile(r"^([AB])\s*Tag$")
#: `Note: 1,088 B Tag Quota Available First-Come, First-Served.` — a tag-quota fact.
_QUOTA_RE = re.compile(r"([\d,]+)\s+([AB])\s+Tag\s+Quota", re.IGNORECASE)
#: The preamble method window: it rules *how* a season may be hunted, not when it opens.
_MOTORIZED_RE = re.compile(r"Motorized\s+Hunting\s+Rule", re.IGNORECASE)
#: The document-level legend (`ELK Notes:` on the last zone page) and a block's own one.
_LEGEND_START_RE = re.compile(r"^(?:ELK\s+)?Notes?\s*:", re.IGNORECASE)
_BLOCK_NOTE_RE = re.compile(r"^Note\s+[A-Z0-9]+\b")
#: `See Note A below` / `See Note 3, Page 49` — a pointer this parser resolves.
_NOTE_REF_RE = re.compile(r"See\s+Note\s+([A-Z0-9]+)", re.IGNORECASE)

#: Printed weapon phrase → `seasons.weapon`. Longest alternative first.
WEAPON_PHRASES: tuple[tuple[str, str], ...] = (
    ("short range weapons only", "short_range"),
    ("short-range weapons only", "short_range"),
    ("muzzleloader only", "muzzleloader"),
    ("archery only", "archery"),
    ("shotgun only", "shotgun"),
    ("any weapon", "any"),
)
#: Printed sex phrase → (`seasons.sex`, restriction codes). **Longest match first**: a
#: shortest-match regex reads Boise River's `antlerless or spike only` as plain
#: `antlerless` — a wrong, publishable value with no red flag (§12 T-12a finding).
SEX_PHRASES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("antlerless or spike only", "either", ("antlerless_only", "spike_only")),
    ("spike or antlerless only", "either", ("antlerless_only", "spike_only")),
    ("spike or antlerless", "either", ("antlerless_only", "spike_only")),
    ("brow-tined bulls only", "antlered", ("brow_tined",)),
    ("brow tined bulls only", "antlered", ("brow_tined",)),
    ("brow-tined bull only", "antlered", ("brow_tined",)),
    ("spike elk only", "antlered", ("spike_only",)),
    ("spike only", "antlered", ("spike_only",)),
    ("antlerless only", "antlerless", ("antlerless_only",)),
    ("antlered only", "antlered", ("antlered_only",)),
    ("any elk", "either", ()),
)
#: An entry that names no sex at all cannot be published: `na` plus `see_notes` keeps
#: the unit yellow and the row in review (D3 — never guess what may be shot).
UNKNOWN_SEX = "na"

#: In-entry unit restrictions, e.g. `Unit 39 only:`, `Units 21, 21A and 36B only:`,
#: `On or within 1 mile of private land in the following Units: 1, 2, 3 and 4A only`.
_UNIT_PHRASE_RE = re.compile(
    r"(?:in\s+the\s+following\s+)?Units?\s*:?\s*((?:\d{1,2}[A-Z]?)(?:\s*(?:,|and|&)\s*\d{1,2}[A-Z]?)*)",
    re.IGNORECASE,
)
_PRIVATE_LAND_RE = re.compile(r"private\s+(?:land|agricultural|irrigated)", re.IGNORECASE)
#: `Units 20A and 26: antlered only` — a sex that applies to part of the zone only. One
#: printed window can carry several of them (Middle Fork prints two), and each is its
#: own season: the units it names and the sex it grants differ (§12 T-12).
_UNIT_SEX_RE = re.compile(
    r"\bUnits?\s+((?:\d{1,2}[A-Z]?)(?:\s*(?:,|and|&)\s*\d{1,2}[A-Z]?)*)\s*:\s*"
    r"(" + "|".join(re.escape(phrase) for phrase, _, _ in SEX_PHRASES) + r")",
    re.IGNORECASE,
)
_PORTION_RE = re.compile(r"portion\s+of\s+(?:the\s+)?unit", re.IGNORECASE)


# ───────── the printed page, in blocks and entries ─────────


@dataclass(frozen=True)
class Entry:
    """One printed entry: a weapon/sex label, its date line(s) and any note under it."""

    tag: str
    lines: tuple[str, ...]
    #: Month columns the entry's box overlaps — a cross-check, never a boundary.
    months: tuple[str, ...] = ()
    #: The entry's box in the *rotated* frame (converted when it is staged).
    box: tuple[float, float, float, float] | None = None

    @property
    def text(self) -> str:
        return " | ".join(self.lines)


@dataclass(frozen=True)
class ZoneBlock:
    """One zone's block of the page: its header, preamble, entries and legend."""

    page: int
    zone: str
    header_units: str
    tail: str
    entries: tuple[Entry, ...] = ()
    preamble: tuple[str, ...] = ()
    footnotes: tuple[str, ...] = ()
    no_tags: frozenset[str] = frozenset()
    #: Words inside the block's table that no region claimed (D3: nothing is dropped).
    unassigned: tuple[str, ...] = ()
    #: `A`/`B` → the printed first-come-first-served quota of that tag.
    quotas: tuple[tuple[str, int], ...] = ()

    @property
    def controlled_only(self) -> bool:
        """A zone with no calendar: every hunt in it is a controlled hunt."""
        return bool(_CONTROLLED_ONLY_RE.search(self.tail))

    def offers(self, tag: str) -> bool:
        """Does the brochure offer this tag in this zone at all?"""
        return not self.controlled_only and tag not in self.no_tags


def normalise_zone(name: str) -> str:
    """Zone name reduced to what spelling differences cannot change.

    The printed names already match the GIS `Elk_Zone` values for 2026 once punctuation
    and case are ignored (`Smoky-Bennett` vs. `Smoky - Bennett`), so this is a
    normalisation, not the mapping table §12 anticipated — and a printed name that
    normalises to no GIS zone fails loudly rather than being guessed into one.
    """
    return re.sub(r"[^a-z]", "", (name or "").lower())


def _fill_is(shape: Shape, fill: tuple[float, ...]) -> bool:
    return shape.fill is not None and tuple(round(v, 3) for v in shape.fill) == fill


def _centre(box: tuple[float, float, float, float]) -> float:
    return (box[1] + box[3]) / 2


def _word_box(words: Sequence[Word]) -> tuple[float, float, float, float]:
    return (
        min(w.x0 for w in words),
        min(w.top for w in words),
        max(w.x1 for w in words),
        max(w.bottom for w in words),
    )


def native_bbox(page: Page, box: tuple[float, float, float, float]) -> list[float]:
    """A rotated-frame box back in page coordinates, for `seasons.source_bbox` (§8.6)."""
    x0, top, x1, bottom = box
    return [top, page.height - x1, bottom, page.height - x0]


#: Two words belong to the same printed line when their centres are this close.
LINE_TOLERANCE_PT = 2.0


def _lines_of(words: Iterable[Word], tolerance: float = LINE_TOLERANCE_PT) -> list[TextLine]:
    """Words → printed lines, grouped by their **centre**, not by box overlap.

    `textlayer.group_lines` groups on overlap, which is right for an upright table but
    chains these pages' printed lines together: the zone title is set in a larger font
    whose box overlaps the preamble under it ("Idaho Motorized Hunting Rule Applies
    Lemhi in Units Zone 29, 37A (Units and 51, …)" — a title no regex can find), and a
    row band's three lines merge into one, which scrambles the entry segmentation.
    """
    out: list[list[Word]] = []
    centres: list[float] = []
    for word in sorted(words, key=lambda w: ((w.top + w.bottom) / 2, w.x0)):
        centre = (word.top + word.bottom) / 2
        if out and abs(centre - centres[-1]) <= tolerance:
            out[-1].append(word)
        else:
            out.append([word])
            centres.append(centre)
    return [TextLine(sorted(group, key=lambda w: w.x0)) for group in out]


def _entries_in_band(
    words: Sequence[Word], tag: str, months: Sequence[tuple[float, float, str]]
) -> list[Entry]:
    """Split a row of the calendar into the entries printed side by side inside it.

    Each printed line is cut at horizontal gaps, then the segments are clustered in
    **both** directions: they must overlap horizontally *and* sit on adjacent printed
    lines. The vertical half matters as much as the horizontal one — two entries printed
    one under the other in the same month column (Panhandle's `Dec 2 - Dec 8` above
    `Dec 10 - Dec 16`) share an x range, and clustering on overlap alone fuses them into
    one entry whose first label then governs the other's dates. Measured on the 2026
    pages: lines inside an entry are ~10.5 pt apart, stacked entries ≥ 20 pt.
    """
    segments: list[list[Word]] = []
    for line in _lines_of(words):
        current: list[Word] = []
        for word in sorted(line.words, key=lambda w: w.x0):
            if current and word.x0 - current[-1].x1 > ENTRY_GAP_PT:
                segments.append(current)
                current = []
            current.append(word)
        if current:
            segments.append(current)

    boxes = [_word_box(segment) for segment in segments]

    def joined(a: int, b: int) -> bool:
        ax0, atop, ax1, abottom = boxes[a]
        bx0, btop, bx1, bbottom = boxes[b]
        overlap = min(ax1, bx1) - max(ax0, bx0)
        if overlap <= 0.4 * min(ax1 - ax0, bx1 - bx0):
            return False
        # Line *centres*, not the gap between boxes: consecutive lines of one entry are
        # ~10.5 pt apart centre to centre while two stacked entries are ≥ 20 pt, but the
        # white space between their boxes is only ~11 pt in both cases — measuring the
        # gap fuses the last line of one row with the first line of the next (p. 43
        # reads Beaverhead's B-tag hunt as an A-tag one).
        return abs((atop + abottom) / 2 - (btop + bbottom) / 2) <= ENTRY_LINE_GAP_PT

    groups: list[list[int]] = []
    seen: set[int] = set()
    for index in range(len(segments)):
        if index in seen:
            continue
        group = [index]
        seen.add(index)
        queue = [index]
        while queue:
            current_index = queue.pop()
            for other in range(len(segments)):
                if other not in seen and joined(current_index, other):
                    seen.add(other)
                    group.append(other)
                    queue.append(other)
        groups.append(group)

    out: list[Entry] = []
    for group in groups:
        items = [w for index in group for w in segments[index]]
        box = _word_box(items)
        out.append(
            Entry(
                tag=tag,
                lines=tuple(ln.text for ln in _lines_of(items)),
                months=tuple(
                    name for x0, x1, name in months if x1 > box[0] and x0 < box[2] and name
                ),
                box=box,
            )
        )
    return sorted(out, key=lambda e: (e.box or (0,))[0])


def _band_tag(labels: Sequence[tuple[str, float]], start: float, stop: float) -> str:
    """The tag label printed inside this row band, else the nearest one.

    A tag row is **not** one band: the brochure rules a hairline between the shelves of
    entries inside a row too, so Panhandle's A row is two bands (83–146 and 146–214)
    with its label centred across both, while its B row is one (214–277). The band that
    holds the label gives its own tag; a band above or below it belongs to the nearest
    label, which is why the label is printed centred on its row in the first place.
    """
    inside = [tag for tag, centre in labels if start <= centre < stop]
    if inside:
        return inside[0]
    return _tag_of((start + stop) / 2, labels, "?")


def _tag_of(centre: float, labels: Sequence[tuple[str, float]], fallback: str) -> str:
    """The tag label nearest this entry by vertical centre (as in the controlled tables)."""
    if not labels:
        return fallback
    return min(labels, key=lambda item: abs(item[1] - centre))[0]


# ───────── path A: the drawn skeleton (rects and hairlines) ─────────


def blocks_from_skeleton(page: Page) -> list[ZoneBlock]:
    """Path A: zone blocks read from the printed artwork — title band, month strip, rules."""
    title_rects = [r for r in page.rot_rects if _fill_is(r, TITLE_FILL)]
    month_rects = [r for r in page.rot_rects if _fill_is(r, MONTH_FILL)]
    if not title_rects and not month_rects:
        return []
    table = title_rects + month_rects
    x0, x1 = min(r.x0 for r in table), max(r.x1 for r in table)
    # The vertical "Idaho Big Game 2026 Seasons & Rules" strap and the sideways `ELK`
    # section tab are printed outside the table and merge into the zone title line
    # otherwise ("Idaho Lemhi Zone"), so the table's own extent clips them away.
    body = [w for w in page.rot_words if x0 - 2 <= (w.x0 + w.x1) / 2 <= x1 + 2]
    rules = [
        r
        for r in page.rot_rules
        if (r.bottom - r.top) < RULE_THICKNESS_PT and r.x1 > x0 and r.x0 < x1
    ]
    bounds = _block_bounds(body)
    return [
        _read_block(page, body, month_rects, rules, start, stop, header)
        for header, start, stop in bounds
    ]


def _block_bounds(body: Sequence[Word]) -> list[tuple[TextLine, float, float]]:
    """Each zone header line on the page and the vertical span its block owns."""
    lines = _lines_of(body)
    found = [ln for ln in lines if _ZONE_HEADER_RE.search(ln.text) and len(ln.words) >= 3]
    if not found:
        return []
    end = max(ln.bottom for ln in lines) + 1
    spans: list[tuple[TextLine, float, float]] = []
    for index, line in enumerate(found):
        start = line.top - 6
        stop = found[index + 1].top - 6 if index + 1 < len(found) else end
        spans.append((line, start, stop))
    return spans


def _read_block(
    page: Page,
    body: Sequence[Word],
    month_rects: Sequence[Shape],
    rules: Sequence[Shape],
    start: float,
    stop: float,
    header: TextLine,
) -> ZoneBlock:
    """One zone block of path A, with every word of it accounted for."""
    match = _ZONE_HEADER_RE.search(header.text)
    assert match is not None  # `_block_bounds` only yields header lines
    mine = [w for w in body if start <= _centre((w.x0, w.top, w.x1, w.bottom)) < stop]
    strip = [r for r in month_rects if start <= _centre((r.x0, r.top, r.x1, r.bottom)) < stop]
    base = ZoneBlock(
        page=page.number,
        zone=match.group("name").strip(),
        header_units=match.group("units").strip(),
        tail=match.group("tail").strip(),
    )
    if not strip:
        # A zone with no calendar at all (Hells Canyon, Owyhee): prose only.
        prose = tuple(ln.text for ln in _lines_of(mine))
        return replace(
            base,
            preamble=prose[1:],
            no_tags=_no_tags(prose),
            unassigned=tuple(w.text for w in mine if common.parse_date_ranges(w.text, 2026)),
        )

    top = min(r.top for r in strip)
    # The month strip's fill is reused to shade a whole tag row, so the column edges
    # come only from the rects whose top edge is the strip's own.
    headers = sorted(((r.x0, r.x1) for r in strip if abs(r.top - top) < 2), key=lambda c: c[0])
    if not headers:
        return replace(base, preamble=tuple(ln.text for ln in _lines_of(mine)))
    body_top = max(r.bottom for r in strip if abs(r.top - top) < 2)
    label_x1 = headers[0][1]

    claimed: set[int] = set()

    def claim(words: Iterable[Word]) -> None:
        claimed.update(id(w) for w in words)

    above = [w for w in mine if _centre((w.x0, w.top, w.x1, w.bottom)) < top]
    claim(above)
    preamble = tuple(ln.text for ln in _lines_of(above))[1:]  # minus the header line

    months: list[tuple[float, float, str]] = []
    for x0, x1 in headers:
        cells = [
            w
            for w in mine
            if top <= _centre((w.x0, w.top, w.x1, w.bottom)) <= body_top
            and x0 <= (w.x0 + w.x1) / 2 <= x1
        ]
        claim(cells)
        months.append((x0, x1, join_words(cells)))

    labels: list[tuple[str, float]] = []
    legend_top = stop
    footnotes: list[str] = []
    stub = [
        w
        for w in mine
        if _centre((w.x0, w.top, w.x1, w.bottom)) > body_top and (w.x0 + w.x1) / 2 <= label_x1
    ]
    for line in _lines_of(stub):
        text = line.text
        if _TAG_LABEL_RE.match(text):
            labels.append((text[0], _centre((line.x0, line.top, line.x1, line.bottom))))
            claim(line.words)
        elif _is_legend_start(text):
            legend_top = min(legend_top, line.top - 2)
            claim(line.words)
            footnotes.append(text)

    # The calendar ends where its drawn skeleton ends. Below it come the block's note
    # legend, the page footer and the sideways `ELK` section tab — never a season, so
    # the bands stop there (Panhandle's `Note A` paragraph and Palouse's tab both read
    # as a B-tag entry otherwise).
    table_bottom = max(
        [r.bottom for r in strip]
        + [
            r.top
            for r in rules
            if r.top > body_top
            and (r.x1 - r.x0) >= BAND_COVERAGE * (headers[-1][1] - headers[1][0])
        ]
    )
    legend_top = min(legend_top, table_bottom + 1)

    entries: list[Entry] = []
    for band_start, band_stop in _band_edges(rules, headers, body_top, legend_top):
        band = [
            w
            for w in mine
            if band_start < _centre((w.x0, w.top, w.x1, w.bottom)) < band_stop
            and headers[1][0] - 2 <= (w.x0 + w.x1) / 2 <= headers[-1][1] + 2
        ]
        if not band:
            continue
        claim(band)
        # The band *is* the tag row — it is cut out by the hairlines the brochure drew —
        # so every entry in it carries that row's label. Re-deriving the tag from an
        # entry's own centre instead reads a tall row's lowest entry as the next row's
        # tag (p. 43: Lemhi's archery season, in a zone that has no B tag at all).
        tag = _band_tag(labels, band_start, band_stop)
        entries += [replace(entry, tag=tag) for entry in _entries_in_band(band, tag, months)]

    tail = [
        w
        for w in mine
        if _centre((w.x0, w.top, w.x1, w.bottom)) >= legend_top and (w.x0 + w.x1) / 2 > label_x1
    ]
    claim(tail)
    footnotes += [ln.text for ln in _lines_of(tail)]

    rest = [w for w in mine if id(w) not in claimed]
    for line in _lines_of(rest):
        if _NO_TAG_RE.search(line.text):
            claim(line.words)
    unassigned = tuple(w.text for w in mine if id(w) not in claimed)
    texts = (*preamble, *(e.text for e in entries), *footnotes, *(w for w in unassigned))
    return replace(
        base,
        entries=tuple(e for e in entries if not _NO_TAG_RE.search(e.text)),
        preamble=preamble,
        footnotes=tuple(footnotes),
        no_tags=_no_tags(texts),
        unassigned=unassigned,
        quotas=_quotas(texts),
    )


def _band_edges(
    rules: Sequence[Shape],
    headers: Sequence[tuple[float, float]],
    body_top: float,
    legend_top: float,
) -> list[tuple[float, float]]:
    """Row bands: a hairline covering most of the table's width is a band edge."""
    x0, x1 = headers[1][0], headers[-1][1]
    width = x1 - x0
    cover: dict[float, float] = {}
    for rule in rules:
        if body_top - 2 <= rule.top < legend_top:
            overlap = min(rule.x1, x1) - max(rule.x0, x0)
            if overlap > 0:
                cover[round(rule.top, 1)] = cover.get(round(rule.top, 1), 0.0) + overlap
    edges = sorted(
        {round(body_top, 1), round(legend_top, 1)}
        | {y for y, covered in cover.items() if covered >= BAND_COVERAGE * width}
    )
    return list(zip(edges, edges[1:], strict=False))


def _is_legend_start(text: str) -> bool:
    """Does this line open a note legend (and not a tag quota)?

    `Note: 1,551 A Tag Quota Available First-Come, First-Served.` is printed in a
    full-width sub-row **between** the A and B rows and reads exactly like a legend
    heading. Treating it as one cuts the block off above its B row, which silently loses
    every B-tag season of that zone — so a quota line is never a legend.
    """
    if _QUOTA_RE.search(text):
        return False
    return bool(_BLOCK_NOTE_RE.match(text) or _LEGEND_START_RE.match(text))


def _no_tags(texts: Iterable[str]) -> frozenset[str]:
    """The tags a block says it does not offer (`No B Tags in this Zone`)."""
    found = {m.group(1).upper() for text in texts for m in _NO_TAG_RE.finditer(text)}
    return frozenset(found)


def _quotas(texts: Iterable[str]) -> tuple[tuple[str, int], ...]:
    """The printed first-come-first-served tag quotas of a block."""
    out: dict[str, int] = {}
    for text in texts:
        for m in _QUOTA_RE.finditer(text):
            out[m.group(2).upper()] = int(m.group(1).replace(",", ""))
    return tuple(sorted(out.items()))


# ───────── path B: word positions only, no rect and no rule ─────────


#: Month names as the header strip prints them — how path B finds the strip without
#: ever looking at its coloured rect.
_MONTH_NAMES = frozenset(
    {
        "january", "february", "march", "april", "may", "june",
        "july", "august", "september", "october", "november", "december",
    }
)  # fmt: skip


def _is_month_header(line: TextLine) -> bool:
    """A printed line that is the month strip: two or more month names and nothing else."""
    words = [w.text.strip("/,").lower() for w in line.words]
    months = [w for part in words for w in part.split("/") if w in _MONTH_NAMES]
    return len(months) >= 2 and len(months) >= len(words) - 1


def blocks_from_words(page: Page) -> list[ZoneBlock]:
    """Path B: the same blocks rebuilt from the text layer's geometry alone.

    Nothing here reads a rect or a rule. The month strip is found by its *words*, the
    tag rows are anchored on the `A Tag` / `B Tag` labels — every other word goes to the
    nearest label by vertical centre, exactly the rule that fixed path B on the bear and
    lion tables — and entries are cut out of a row by the white space between them.
    That makes it an independent verdict on the geometry (§8.7 principle 3), which is
    where these pages go wrong.
    """
    lines = _lines_of(page.rot_words)
    headers = [ln for ln in lines if _ZONE_HEADER_RE.search(ln.text) and len(ln.words) >= 3]
    if not headers:
        return []
    out: list[ZoneBlock] = []
    end = max(ln.bottom for ln in lines) + 1
    for index, header in enumerate(headers):
        match = _ZONE_HEADER_RE.search(header.text)
        assert match is not None
        start = header.top - 6
        stop = headers[index + 1].top - 6 if index + 1 < len(headers) else end
        mine = [ln for ln in lines if start <= _centre((ln.x0, ln.top, ln.x1, ln.bottom)) < stop]
        texts = tuple(ln.text for ln in mine)
        block = ZoneBlock(
            page=page.number,
            zone=match.group("name").strip(),
            header_units=match.group("units").strip(),
            tail=match.group("tail").strip(),
            preamble=texts[1:],
            no_tags=_no_tags(texts),
            quotas=_quotas(texts),
        )
        strip = next((ln for ln in mine if _is_month_header(ln)), None)
        if strip is None:
            out.append(block)
            continue
        # Where the first month column begins — read from the month *words*, not from
        # the line's left edge: the vertical margin strap ("Idaho Big Game 2026 Seasons
        # & Rules") prints a word on that same printed line (p. 43: "Big
        # August/September October …"), which would put the column edge at the margin.
        strip_x0 = min(
            (
                w.x0
                for w in strip.words
                if any(part in _MONTH_NAMES for part in w.text.strip("/,").lower().split("/"))
            ),
            default=strip.x0,
        )
        # A label is an `A`/`B` word followed by `Tag` **in the stub column**, which the
        # month strip's own first word bounds. Taking any such pair instead matches the
        # quota sub-row ("Note: 1,551 A Tag Quota Available…") whose `Tag` sits mid-page
        # and would push the label column half across the table; requiring the pair to
        # *open* its line instead loses the label whenever the sideways `ELK` tab prints
        # a letter to the left of it (p. 40, p. 49), and a lost label mis-tags a row.
        labels: list[tuple[str, float]] = []
        label_x1 = 0.0
        for line in mine:
            words = sorted(line.words, key=lambda w: w.x0)
            for first, second in zip(words, words[1:], strict=False):
                if (
                    first.text.upper() in ("A", "B")
                    and second.text.lower() == "tag"
                    and second.x1 <= strip_x0 + 2
                ):
                    labels.append(
                        (
                            first.text.upper(),
                            _centre((first.x0, first.top, first.x1, first.bottom)),
                        )
                    )
                    label_x1 = max(label_x1, second.x1)
        if not labels:
            out.append(block)
            continue
        last_label = max(y for _, y in labels)
        legend = next(
            (ln.top for ln in mine if ln.top > last_label and _is_legend_start(ln.text)),
            stop,
        )
        body = [
            w
            for ln in mine
            if strip.bottom < _centre((ln.x0, ln.top, ln.x1, ln.bottom)) < legend
            for w in ln.words
            if w.x0 > label_x1
        ]
        # Entries are clustered out of the whole block first and only then given a tag:
        # splitting the words per tag beforehand cuts an entry in half whenever its last
        # line sits nearer the next tag's label than its own (Panhandle's `See archers
        # caution Page 36`), and that half then reads as a season of the wrong row.
        found = [
            entry
            for entry in _entries_in_band(body, "?", ())
            if common.parse_date_ranges(entry.text, 2026)
            and not _NO_TAG_RE.search(entry.text)
            and not _MOTORIZED_RE.search(entry.text)
            and not _QUOTA_RE.search(entry.text)
        ]
        out.append(replace(block, entries=tuple(_tag_rows(found, labels))))
    return out


def _tag_rows(entries: Sequence[Entry], labels: Sequence[tuple[str, float]]) -> list[Entry]:
    """Give every entry the tag of the printed row it shares with its neighbours.

    Path B has no hairlines to cut rows with, so a row is a set of entries whose
    vertical spans **overlap** — that is what "printed side by side" means — and the row
    takes the tag label that sits inside its span. Asking instead which label an entry's
    own centre is nearest to is wrong for a tall row: McCall's A row is 108 pt deep, so
    its lowest entry sits closer to the B label than to its own.
    """
    ordered = sorted(entries, key=lambda e: (e.box or (0.0, 0.0, 0.0, 0.0))[1])
    rows: list[list[int]] = []
    for index, entry in enumerate(ordered):
        box = entry.box or (0.0, 0.0, 0.0, 0.0)
        for row in rows:
            spans = [ordered[i].box or (0.0, 0.0, 0.0, 0.0) for i in row]
            if any(box[1] < span[3] and box[3] > span[1] for span in spans):
                row.append(index)
                break
        else:
            rows.append([index])
    out: list[Entry] = []
    for row in rows:
        boxes = [ordered[i].box or (0.0, 0.0, 0.0, 0.0) for i in row]
        top, bottom = min(b[1] for b in boxes), max(b[3] for b in boxes)
        tag = _band_tag(labels, top, bottom)
        out += [replace(ordered[i], tag=tag) for i in row]
    return out


# ───────── an entry → seasons ─────────


@dataclass(frozen=True)
class ZoneSeason:
    """One season window read from an entry, before it becomes a `ParsedSeason`."""

    zone: str
    tag: str
    weapon: str
    sex: str
    start: dt.date
    end: dt.date
    units: tuple[str, ...]
    codes: tuple[str, ...] = ()
    unknown_units: tuple[str, ...] = ()
    leftovers: tuple[str, ...] = ()
    qualifier: str = ""
    entry_text: str = ""
    months: tuple[str, ...] = ()
    box: tuple[float, float, float, float] | None = None

    @property
    def key(self) -> tuple[str, ...]:
        """What the two paths must agree on: zone, tag, weapon, sex, window, units."""
        return (
            normalise_zone(self.zone),
            self.tag,
            self.weapon,
            self.sex,
            to_iso(self.start),
            to_iso(self.end),
            ",".join(self.units),
        )


def _weapon_in(text: str) -> str | None:
    low = text.lower()
    return next((code for phrase, code in WEAPON_PHRASES if phrase in low), None)


def _sex_in(text: str) -> tuple[str, tuple[str, ...]] | None:
    low = text.lower()
    return next(
        ((sex, codes) for phrase, sex, codes in SEX_PHRASES if phrase in low),
        None,
    )


def _unit_sex_clauses(
    text: str, ctx: ParseContext
) -> list[tuple[common.UnitList, tuple[str, tuple[str, ...]]]]:
    """`Units 20A and 26: antlered only  Unit 27: brow-tined bulls only` → one per clause."""
    out: list[tuple[common.UnitList, tuple[str, tuple[str, ...]]]] = []
    for match in _UNIT_SEX_RE.finditer(text or ""):
        units = common.parse_units(match.group(1), ctx.units)
        sex = _sex_in(match.group(2))
        if sex is not None and (units.units or units.unknown):
            out.append((units, sex))
    return out


def _units_in(text: str, zone_units: Sequence[str], ctx: ParseContext) -> common.UnitList | None:
    """The units an entry line restricts the season to, if it names any."""
    if not _UNIT_PHRASE_RE.search(text):
        return None
    parsed = common.parse_units(text, ctx.units)
    if not parsed.units and not parsed.unknown:
        return None
    return parsed


@dataclass
class _Group:
    """One weapon/sex label with the date lines it governs and the notes under them."""

    before: list[str] = field(default_factory=list)
    dated: list[str] = field(default_factory=list)
    after: list[str] = field(default_factory=list)


def _group_lines(entry: Entry, license_year: int) -> list[_Group]:
    """An entry's printed lines grouped around the date lines they belong to.

    A note printed *under* a date (`See Note A below`, `On or within 1 mile of private
    …, See Note 3, Page 49`) qualifies the window above it, so it is kept with that
    group rather than dropped (§12 T-12a gap 1).
    """
    groups: list[_Group] = [_Group()]
    for line in entry.lines:
        dated = bool(common.parse_date_ranges(line, license_year))
        current = groups[-1]
        if not dated and current.dated and (_weapon_in(line) or _sex_in(line)):
            groups.append(_Group(before=[line]))
        elif dated:
            current.dated.append(line)
        elif current.dated:
            current.after.append(line)
        else:
            current.before.append(line)
    return [g for g in groups if g.dated]


def entry_seasons(
    entry: Entry, block: ZoneBlock, zone_units: Sequence[str], ctx: ParseContext
) -> list[ZoneSeason]:
    """Every season one printed entry opens, with what qualifies it.

    One entry can carry **two sexes split by unit** (Middle Fork prints `Units 20A and
    26: antlered only` beside `Unit 27: brow-tined bulls only` under one window), so a
    date line's own head text overrides the entry's label.
    """
    out: list[ZoneSeason] = []
    weapon = "any"
    weapon_seen = False
    sex: tuple[str, tuple[str, ...]] | None = None
    entry_units: common.UnitList | None = None
    for group in _group_lines(entry, ctx.license_year):
        head_text = " ".join(group.before)
        if found := _weapon_in(head_text):
            weapon, weapon_seen = found, True
        if found_sex := _sex_in(head_text):
            sex = found_sex
        if found_units := _units_in(head_text, zone_units, ctx):
            entry_units = found_units
        note = " ".join(group.after)
        for line in group.dated:
            ranges = common.parse_date_ranges(line, ctx.license_year)
            leftovers = list(common.leftover_date_text(line, ranges))
            for index, found_range in enumerate(ranges):
                following = ranges[index + 1] if index + 1 < len(ranges) else None
                head = line[: found_range.span[0]]
                tail = line[found_range.span[1] : (following.span[0] if following else len(line))]
                line_weapon = _weapon_in(head) or (
                    weapon if weapon_seen else _weapon_in(entry.text)
                )
                line_sex = _sex_in(head) or _sex_in(tail) or sex
                line_units = _units_in(head, zone_units, ctx) or entry_units
                qualifier = " ".join(part for part in (head_text, head, tail, note) if part.strip())
                # `Units 20A and 26: antlered only   Unit 27: brow-tined bulls only` is
                # one printed window granting two different hunts, so it becomes one row
                # per clause — merging them would publish brow-tined bulls as plain
                # antlered elk in two units, and antlered elk as brow-tined in a third.
                clauses = _unit_sex_clauses(f"{head_text} {head}", ctx)
                for clause_units, clause_sex in clauses or [(line_units, line_sex)]:  # type: ignore[list-item]
                    out.append(
                        _season(
                            entry=entry,
                            block=block,
                            zone_units=zone_units,
                            weapon=line_weapon or "any",
                            sex=clause_sex,
                            units=clause_units,
                            found=found_range,
                            qualifier=qualifier,
                            leftovers=tuple(leftovers),
                        )
                    )
    return out


def _season(
    *,
    entry: Entry,
    block: ZoneBlock,
    zone_units: Sequence[str],
    weapon: str,
    sex: tuple[str, tuple[str, ...]] | None,
    units: common.UnitList | None,
    found: common.DateRange,
    qualifier: str,
    leftovers: tuple[str, ...],
) -> ZoneSeason:
    """One `ZoneSeason`, with its units narrowed and its qualifiers mapped to §5.1 codes."""
    codes: list[str] = list(sex[1] if sex else ())
    quals = common.qualifiers(qualifier)
    for code in quals.codes:
        if code not in codes:
            codes.append(code)
    season_units = tuple(zone_units)
    unknown: tuple[str, ...] = ()
    if units is not None:
        # The entry restricts the season to part of the zone: the season covers the
        # *intersection*, never a unit the zone itself does not hold.
        season_units = tuple(u for u in zone_units if u in set(units.units))
        unknown = tuple(units.unknown)
    if _PRIVATE_LAND_RE.search(qualifier) and "private_land_only" not in codes:
        codes.append("private_land_only")
    if _PORTION_RE.search(qualifier) and "portion_of_unit" not in codes:
        codes.append("portion_of_unit")
    if _NOTE_REF_RE.search(qualifier) and "see_notes" not in codes:
        # A note this parser does not resolve keeps the row yellow (D3).
        codes.append("see_notes")
    if sex is None and "see_notes" not in codes:
        codes.append("see_notes")
    return ZoneSeason(
        zone=block.zone,
        tag=entry.tag,
        weapon=weapon,
        sex=sex[0] if sex else UNKNOWN_SEX,
        start=found.start,
        end=found.end,
        units=season_units,
        codes=tuple(codes),
        unknown_units=unknown,
        # An entry whose sex phrase is not in the vocabulary is never published as a
        # guess: the unexplained text is reported, which fails the section's checks.
        leftovers=leftovers + ((entry.text,) if sex is None else ()),
        qualifier=qualifier.strip(),
        entry_text=entry.text,
        months=entry.months,
        box=entry.box,
    )


# ───────── zone → units ─────────


def gis_zone_units(license_year: int) -> dict[str, list[str]]:
    """`zone_units` for a year, keyed by the normalised zone name (T-04 seeds it from GIS)."""
    out: dict[str, list[str]] = {}
    for row in db.q(
        "SELECT zone, unit_id FROM zone_units WHERE license_year = ? ORDER BY zone", license_year
    ):
        out.setdefault(normalise_zone(row["zone"]), []).append(row["unit_id"])
    return {zone: sorted(units, key=common.natural_key) for zone, units in out.items()}


def zone_name_for(normalised: str, license_year: int) -> str | None:
    """The `zone_units` spelling of a normalised zone name."""
    for row in db.q("SELECT DISTINCT zone FROM zone_units WHERE license_year = ?", license_year):
        if normalise_zone(row["zone"]) == normalised:
            return str(row["zone"])
    return None


@dataclass(frozen=True)
class ZoneFacts:
    """One zone's printed unit list beside the GIS one (§12 T-12's own invariant)."""

    zone: str
    page: int
    printed: tuple[str, ...]
    gis: tuple[str, ...]
    unknown: tuple[str, ...] = ()
    seasons: int = 0
    tags: tuple[str, ...] = ()
    offered: tuple[str, ...] = ()
    quotas: tuple[tuple[str, int], ...] = ()

    @property
    def agrees(self) -> bool:
        return self.printed == self.gis and not self.unknown


# ───────── per-document state ─────────


@dataclass
class SectionState:
    """What the elk zone section carries across the pages of one document."""

    pages: list[int] = field(default_factory=list)
    zones: list[ZoneFacts] = field(default_factory=list)
    date_cells: list[DateCell] = field(default_factory=list)
    method_windows: list[MethodWindow] = field(default_factory=list)
    keys: list[tuple[str, ...]] = field(default_factory=list)
    unknown_units: list[tuple[int, str]] = field(default_factory=list)
    disagreements: list[dict[str, object]] = field(default_factory=list)
    compared: int = 0
    month_mismatches: list[dict[str, object]] = field(default_factory=list)
    #: `(zone, tag, quota, rows)` — every printed quota and the rows it was written to.
    quotas: list[dict[str, object]] = field(default_factory=list)


_STATE: dict[int, SectionState] = {}


def state_for(ctx: ParseContext) -> SectionState:
    """This document's section state (created on first use)."""
    return _STATE.setdefault(ctx.document_id, SectionState())


def begin(ctx: ParseContext) -> None:
    """Reset the section's per-document state (§8.1 `begin` hook)."""
    _STATE[ctx.document_id] = SectionState()


def matches(page: Page) -> bool:
    """A rotated page that prints at least one `… Zone (Units …)` header."""
    if not page.rotated or not page.rot_words:
        return False
    return any(_ZONE_HEADER_RE.search(ln.text) for ln in _lines_of(page.rot_words))


# ───────── staging ─────────


def to_parsed(page: Page, season: ZoneSeason, *, agree: bool, quota: int | None) -> ParsedSeason:
    """A compared `ZoneSeason` as the row the pipeline stages."""
    refs = [UnitRef(unit_id=unit) for unit in season.units]
    refs += [UnitRef(unit_id=token) for token in season.unknown_units]
    return ParsedSeason(
        species_id=SPECIES,
        tag_type_id=TAG_TYPES[season.tag] if season.tag in TAG_TYPES else "elk_zone_a",
        hunt_class=HUNT_CLASS,
        weapon=season.weapon,
        sex=season.sex,
        start=season.start,
        end=season.end,
        units=refs,
        zone=season.zone,
        restrictions=season.codes,
        notes=season.qualifier or None,
        tag_quota=quota,
        source_page=page.number,
        source_text=season.entry_text,
        source_bbox=native_bbox(page, season.box) if season.box else None,
        extract_agree=agree,
        confidence=common.confidence(
            unknown_units=len(season.unknown_units),
            unparsed_date_text=len(season.leftovers),
            unmatched_qualifiers=0,
            date_ranges=1,
        ),
        section=SECTION,
    )


def parse_page(page: Page, ctx: ParseContext) -> Iterator[ParsedSeason]:
    """Parse one zone page with both paths, compare them, and yield the staged seasons."""
    state = state_for(ctx)
    blocks_a = blocks_from_skeleton(page)
    blocks_b = {normalise_zone(b.zone): b for b in blocks_from_words(page)}
    if not blocks_a:
        return
    state.pages.append(page.number)
    zone_units = gis_zone_units(ctx.license_year)

    for block in blocks_a:
        key = normalise_zone(block.zone)
        units = zone_units.get(key, [])
        printed = common.parse_units(block.header_units, ctx.units)
        state.unknown_units += [(page.number, token) for token in printed.unknown]

        per_entry = [
            (entry, entry_seasons(entry, block, units or printed.units, ctx))
            for entry in block.entries
        ]
        seasons = [season for _, found in per_entry for season in found]
        twin = blocks_b.get(key)
        keys_b = (
            {
                season.key
                for entry in twin.entries
                for season in entry_seasons(entry, twin, units or printed.units, ctx)
            }
            if twin is not None
            else set()
        )
        quotas = dict(block.quotas)
        for season in seasons:
            agree = season.key in keys_b
            state.compared += 1
            if not agree:
                state.disagreements.append(
                    {
                        "page": page.number,
                        "zone": block.zone,
                        "skeleton": list(season.key),
                        "words": sorted("/".join(k) for k in keys_b if k[1] == season.tag),
                    }
                )
            state.keys.append(season.key)
            state.unknown_units += [(page.number, u) for u in season.unknown_units]
            _check_months(state, page, season)
            yield to_parsed(page, season, agree=agree, quota=quotas.get(season.tag))

        _record_zone(state, page, block, printed, units, seasons)
        _record_dates(state, page, block, per_entry)

    _record_quotas(state, blocks_a, zone_units)


def _check_months(state: SectionState, page: Page, season: ZoneSeason) -> None:
    """The month columns an entry spans must contain the months its dates name (§12 T-12a)."""
    if not season.months:
        return
    printed = {m.lower()[:3] for name in season.months for m in name.split("/") if m}
    # Every month the window runs through, not just its ends: an entry centred on the
    # November column legitimately opens `Oct 1 - Dec 31` (§12 T-12a gap 4).
    parsed = set()
    month, year = season.start.month, season.start.year
    while (year, month) <= (season.end.year, season.end.month):
        parsed.add(dt.date(year, month, 1).strftime("%b").lower())
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    if printed and not (parsed & printed):
        state.month_mismatches.append(
            {
                "page": page.number,
                "zone": season.zone,
                "columns": list(season.months),
                "dates": f"{to_iso(season.start)}–{to_iso(season.end)}",
            }
        )


def _record_zone(
    state: SectionState,
    page: Page,
    block: ZoneBlock,
    printed: common.UnitList,
    gis: Sequence[str],
    seasons: Sequence[ZoneSeason],
) -> None:
    state.zones.append(
        ZoneFacts(
            zone=block.zone,
            page=page.number,
            printed=tuple(sorted(printed.units, key=common.natural_key)),
            gis=tuple(gis),
            unknown=tuple(printed.unknown),
            seasons=len(seasons),
            tags=tuple(sorted({s.tag for s in seasons})),
            offered=tuple(tag for tag in ("A", "B") if block.offers(tag)),
            quotas=block.quotas,
        )
    )


def _record_dates(
    state: SectionState,
    page: Page,
    block: ZoneBlock,
    per_entry: Sequence[tuple[Entry, Sequence[ZoneSeason]]],
) -> None:
    """Account for every date token printed in this block (§8.7 principle 4).

    Counted per printed entry, never per entry *text*: a zone routinely prints the same
    entry word for word in its A and B rows (Panhandle's December muzzleloader hunt),
    and keying on the text would credit one cell with the other's seasons.
    """
    for entry, seasons in per_entry:
        state.date_cells.append(
            DateCell(
                page=page.number,
                texts=entry.lines,
                # Windows, not rows: a window whose sexes are split by unit legitimately
                # stages several rows (Middle Fork), and the invariant is that every
                # printed token opened exactly one window — no more and none missing.
                seasons=len({(s.start, s.end) for s in seasons}),
                label=f"{block.zone} {entry.tag} Tag",
                leftovers=tuple(dict.fromkeys(left for s in seasons for left in s.leftovers)),
                bbox=entry.box,
                must_produce=bool(common.parse_date_ranges(entry.text, 2026)),
            )
        )
    # Dates printed outside an entry are method windows (the Motorized Hunting Rule
    # preamble) or they are unexplained, and an unexplained one has to be reported.
    for text in (*block.preamble, *block.footnotes, *block.unassigned):
        ranges = common.parse_date_ranges(text, 2026)
        if not ranges:
            continue
        if _MOTORIZED_RE.search(text):
            state.method_windows.append(
                MethodWindow(
                    page=page.number,
                    name="motorized_rule",
                    text=text,
                    windows=len(ranges),
                    label=block.zone,
                    leftovers=tuple(common.leftover_date_text(text, ranges)),
                )
            )
            continue
        state.date_cells.append(
            DateCell(
                page=page.number,
                texts=(text,),
                seasons=0,
                label=f"{block.zone} (outside any entry)",
                leftovers=tuple(r.text for r in ranges),
            )
        )


def _record_quotas(
    state: SectionState, blocks: Sequence[ZoneBlock], zone_units: dict[str, list[str]]
) -> None:
    for block in blocks:
        for tag, quota in block.quotas:
            state.quotas.append(
                {
                    "zone": block.zone,
                    "tag": tag,
                    "quota": quota,
                    "offered": block.offers(tag),
                }
            )


# ───────── finalize: the §8.7 facts, and `zone_units` for the year ─────────


def finalize(ctx: ParseContext) -> SectionFacts | None:
    """The section's measured facts, after writing any zone membership the PDF corrects."""
    state = state_for(ctx)
    if not state.pages:
        return None
    corrections = apply_zone_units(ctx, state.zones)
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
        key_label="(zone, tag, weapon, sex, window, units)",
        extra={
            "method_windows": state.method_windows,
            "zones": state.zones,
            "month_mismatches": state.month_mismatches,
            "zone_unit_corrections": corrections,
            "tag_quotas": state.quotas,
        },
    )


def apply_zone_units(ctx: ParseContext, zones: Sequence[ZoneFacts]) -> list[dict[str, object]]:
    """Write the brochure's own zone membership into `zone_units` where it differs.

    §12 T-12: the printed header is the regulation, so it wins over the GIS seed for
    that license year — and the difference is reported, never applied silently. For
    2026 every one of the 28 zones agrees, so this path stays cold.
    """
    out: list[dict[str, object]] = []
    for facts in zones:
        if facts.agrees or not facts.printed:
            continue
        name = zone_name_for(normalise_zone(facts.zone), ctx.license_year) or facts.zone
        db.exec(
            "DELETE FROM zone_units WHERE license_year = ? AND zone = ?", ctx.license_year, name
        )
        db.exec_many(
            "INSERT OR REPLACE INTO zone_units (license_year, zone, unit_id, coverage)"
            " VALUES (?, ?, ?, 'full')",
            [(ctx.license_year, name, unit) for unit in facts.printed],
        )
        out.append(
            {
                "zone": facts.zone,
                "page": facts.page,
                "printed": list(facts.printed),
                "gis": list(facts.gis),
            }
        )
    return out


parser(
    kind="big_game",
    match=matches,
    name=SECTION,
    section=SECTION,
    begin=begin,
    finalize=finalize,
)(parse_page)
