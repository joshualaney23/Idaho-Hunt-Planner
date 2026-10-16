"""Hunt-area descriptions and unit coverage, DESIGN.md §12 T-17b (part 2 of 2).

T-17a stages every controlled hunt against the GMU its area id starts with (`36A-1X` →
`36A`, coverage `partial`) because the printed area id alone never says which units the
area really covers. This module resolves them:

* **Descriptions.** The brochure prints a `<SPECIES> CONTROLLED HUNT AREA DESCRIPTIONS`
  section (found by that heading, never by page number) whose entries start
  `Hunt Area <id> —`. The pages are **two-column**, so the words are split at the gutter
  (§8.2) and read left column then right, with an entry continuing across columns and
  pages. The verbatim text lands in `hunt_areas.description`.
* **Unit coverage.** `static/data/hunt_area_coverage/<year>.json` (part 1,
  `scripts/build_hunt_area_coverage.py`) says which fraction of each GMU lies inside the
  hunt-area polygon. Fractions ≥ `FULL_FRAC` are `full`, the rest `partial` with their
  `coverage_frac`, and every staged season of that area gets those `season_units` rows.
  An area with no polygon falls back to the unit tokens printed in its own description.
* **Checks.** `hunt_areas_<species>` sections record the four §8.7 invariants of §12
  step 4 (`gis_area_match`, `unit_coverage`, `footnote_consistency`, `geometry_year`).
  They live in `planner.importer.checks.hunt_areas`; this module only measures the facts.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path
from typing import Any

from planner import db
from planner.importer import (
    ParseContext,
    ParsedSeason,
    ParserSpec,
    UnitRef,
    register,
)
from planner.importer.checks import AreaFacts, HuntAreaCoverage, SectionFacts
from planner.importer.controlled import BIG_GAME, Brochure, base_unit
from planner.importer.extract import Page, Word
from planner.importer.textlayer import group_lines, join_words

#: Where part 1's committed build output lives (`static/`, never the git-ignored `data/`).
COVERAGE_DIR = Path(__file__).resolve().parents[2] / "static" / "data" / "hunt_area_coverage"

#: `DEER CONTROLLED HUNT AREA DESCRIPTIONS` — the section heading, per species.
HEADING_RE = re.compile(r"^(?P<species>[A-Z][A-Z ]*?)\s+CONTROLLED HUNT AREA DESCRIPTIONS", re.M)
#: `Hunt Area 36A-1X — All of Units 36A, 36B, 37 and 37A.` The separator is an em or en
#: dash (a plain hyphen is part of the area id, and 2026 elk 60-1 prints `—All` with no
#: space after it), and the id is matched by the same shape the hunt tables print.
ENTRY_RE = re.compile(r"^Hunt Area\s+(?P<area>\d{1,2}[A-Z]?(?:-\d+)?[A-Z]*)\s*[—–]\s*")
#: The page footer and the legend, printed inside a column and never part of an entry.
JUNK_RE = re.compile(r"Seasons & Rules|idfg\.idaho\.gov|See controlled hunt area", re.IGNORECASE)
#: `Unit 8A/10A boundary` names the line between two units, not an area's units.
BOUNDARY_PAIR_RE = re.compile(r"\bUnits?\s+\d{1,2}[A-Z]?\s*/\s*\d{1,2}[A-Z]?", re.IGNORECASE)
#: `All of Units 1, 2, 3, 4, 4A, 5, 6, 7 and 9` / `All of Unit 43 and 48`.
UNIT_PHRASE_RE = re.compile(
    r"\bUnits?\s+((?:\d{1,2}[A-Z]?)(?:\s*(?:,|and|,? and)\s*\d{1,2}[A-Z]?)*)", re.IGNORECASE
)
#: A unit token inside such a phrase.
UNIT_TOKEN_RE = re.compile(r"\d{1,2}[A-Z]?")
#: Wording that restricts a hunt to part of a unit — footnote `b`'s printed counterpart.
PORTION_RE = re.compile(
    r"\bthat portion\b|\bprivate land\b|\bprivate agricultural lands\b|\bexcept\b|"
    r"\bexcluding\b|\bis closed\b|\bwithin the\b",
    re.IGNORECASE,
)
#: Vertical whitespace inside a column that ends the entry being read (an advert, a
#: legend block). Entries are 10–14 pt apart, the 2026 deer page 34 advert is 230 pt away.
BREAK_GAP_PT = 20.0
#: Whitespace (pt) at the page's midpoint that separates the two columns. Measured on
#: the 2026 description pages: the gutter is ~16 pt, a word space inside a full-width
#: heading or footer line ~3 pt.
GUTTER_MARGIN_PT = 10.0

#: `season_units.coverage` = `full` at or above this fraction of the GMU, else `partial`.
FULL_FRAC = 0.98


# ───────── the committed coverage file ─────────


@dataclass(frozen=True)
class Coverage:
    """`static/data/hunt_area_coverage/<year>.json`, as the parser reads it."""

    license_year: int
    species: Mapping[str, Mapping[str, Any]]

    def has_species(self, species_id: str) -> bool:
        return species_id in self.species

    def area(self, species_id: str, area_id: str) -> Mapping[str, Any] | None:
        return self.species.get(species_id, {}).get(area_id)


def coverage_path(license_year: int) -> Path:
    return COVERAGE_DIR / f"{license_year}.json"


def load_coverage(license_year: int) -> Coverage:
    """Read the committed coverage file; a missing file is an empty one (checks report it).

    A year with no committed file is never an error here: `gis_area_match` records it as
    `skipped` and every area falls back to the units its description prints.
    """
    path = coverage_path(license_year)
    if not path.is_file():
        return Coverage(license_year=license_year, species={})
    payload = json.loads(path.read_text(encoding="utf-8"))
    return Coverage(
        license_year=int(payload.get("license_year", license_year)),
        species=payload.get("species") or {},
    )


# ───────── reading the description pages ─────────


@dataclass
class AreaDescription:
    """One `Hunt Area <id> — …` entry, verbatim.

    `parts` are the printed lines in reading order and stay open: an entry started at
    the foot of one column is still being appended to while the next column — on this
    page or the next — is read, so the text is joined on demand, never at creation.
    """

    species_id: str
    area_id: str
    page: int
    printed_page: int | None
    parts: list[str] = field(default_factory=list)

    @property
    def text(self) -> str:
        return _joined(self.parts)

    @property
    def units(self) -> tuple[str, ...]:
        return unit_tokens(self.text)

    @property
    def first_sentence(self) -> str:
        sentence = re.split(r"(?<=[.)])\s+", self.text.strip(), maxsplit=1)[0]
        return sentence.strip()

    @property
    def portion_only(self) -> bool:
        """The text itself says the hunt covers part of a unit (footnote `b`'s wording)."""
        return bool(PORTION_RE.search(self.text))


def unit_tokens(text: str) -> tuple[str, ...]:
    """The GMU tokens a description assigns, in printed order.

    Only tokens inside a `Unit`/`Units` phrase count — a description is prose full of
    road numbers and highways. `Unit 8A/10A boundary` names a boundary between two units
    and assigns neither, so those pairs are removed first.
    """
    out: list[str] = []
    for match in UNIT_PHRASE_RE.finditer(BOUNDARY_PAIR_RE.sub(" ", text)):
        for token in UNIT_TOKEN_RE.findall(match.group(1)):
            if token not in out:
                out.append(token)
    return tuple(out)


def columns_of(page: Page) -> list[list[Word]]:
    """The page's words as two columns, split at the gutter (§8.2).

    A printed line is cut at the whitespace that spans the page's midpoint. A line with
    no such whitespace runs **through** the gutter, so it is a full-width heading, the
    section's preamble or the page footer — never part of a description, which always
    stays inside its column — and its words belong to neither column.
    """
    if not page.words:
        return []
    mid = (min(w.x0 for w in page.words) + max(w.x1 for w in page.words)) / 2
    columns: list[list[Word]] = [[], []]
    for line in group_lines(page.words):
        words = sorted(line.words, key=lambda w: w.x0)
        left = [w for w in words if w.x1 <= mid]
        right = [w for w in words if w.x0 >= mid]
        if len(left) + len(right) != len(words):  # a word sits across the gutter
            continue
        if left and right:
            gutter = min(w.x0 for w in right) - max(w.x1 for w in left)
            if gutter < GUTTER_MARGIN_PT:  # text runs on through the midpoint
                continue
        columns[0] += left
        columns[1] += right
    return columns


def entries_on(
    page: Page, species_id: str, carried: AreaDescription | None
) -> tuple[list[AreaDescription], AreaDescription | None]:
    """Every entry printed on one page, continuing `carried` from the previous page.

    Returns the entries that start here plus the one still open, which the next column
    — on this page or the next — goes on appending to.
    """
    found: list[AreaDescription] = []
    current = carried
    printed = printed_page(page)
    for column in columns_of(page):
        previous_bottom: float | None = None
        for line in sorted(group_lines(column), key=lambda ln: ln.top):
            if previous_bottom is not None and line.top - previous_bottom > BREAK_GAP_PT:
                current = None
            previous_bottom = line.bottom
            text = join_words(line.words)
            if JUNK_RE.search(text):
                continue
            match = ENTRY_RE.match(text)
            if match is not None:
                current = AreaDescription(
                    species_id=species_id,
                    area_id=match.group("area"),
                    page=page.number,
                    printed_page=printed,
                    parts=[text[match.end() :]],
                )
                found.append(current)
            elif current is not None:
                current.parts.append(text)
    return found, current


def _joined(parts: Sequence[str]) -> str:
    """The lines of one entry as running text, re-joining words hyphenated across lines."""
    out = ""
    for part in parts:
        text = part.strip()
        if not text:
            continue
        if out.endswith("-") and text[:1].islower():
            out = out[:-1] + text
        elif out:
            out = f"{out} {text}"
        else:
            out = text
    return out.strip()


def printed_page(page: Page) -> int | None:
    """The brochure's own page number, read from the uncropped footer band."""
    match = BIG_GAME.footer_re.search(page.footer)
    if match is None:
        return None
    value = match.group("right") or match.group("left")
    return int(value) if value else None


def species_heading(page: Page, spec: Brochure) -> str | None:
    """The species whose description section starts on this page, if any."""
    match = HEADING_RE.search(page.text)
    if match is None:
        return None
    heading = match.group("species").strip().lower()
    for word, species_id in spec.species_words:
        if word in heading:
            return species_id
    return None


# ───────── per-document state ─────────


@dataclass
class DocState:
    """What the hunt-area parsers share while reading one document."""

    seen: set[int] = field(default_factory=set)
    species: str | None = None
    open_entry: AreaDescription | None = None
    descriptions: dict[tuple[str, str], AreaDescription] = field(default_factory=dict)
    pages: dict[str, list[int]] = field(default_factory=dict)


_STATE: dict[int, DocState] = {}


def state_for(ctx: ParseContext) -> DocState:
    return _STATE.setdefault(ctx.document_id, DocState())


def begin(ctx: ParseContext) -> None:
    """Reset per-document state once per import (every species parser calls this)."""
    _STATE[ctx.document_id] = DocState()


def read_page(page: Page, ctx: ParseContext, spec: Brochure) -> None:
    """Collect this page's description entries — once per page, for every species."""
    state = state_for(ctx)
    if page.number in state.seen or page.rotated:
        return
    state.seen.add(page.number)
    heading = species_heading(page, spec)
    if heading is not None:
        state.species = heading
        state.open_entry = None
    if state.species is None:
        return
    starts_entry = any(ENTRY_RE.match(line.strip()) for line in page.text.splitlines())
    if heading is None and not starts_entry:
        # Past the end of the section: an entry is never continued onto a page that
        # starts none of its own (2026: every continuation page prints further entries).
        state.species = None
        state.open_entry = None
        return
    entries, state.open_entry = entries_on(page, state.species, state.open_entry)
    if entries:
        state.pages.setdefault(state.species, []).append(page.number)
    for entry in entries:
        state.descriptions[(entry.species_id, entry.area_id)] = entry


def parse_page(
    page: Page, ctx: ParseContext, *, spec: Brochure, species: str
) -> Iterator[ParsedSeason]:
    """Read the page; description pages carry no season rows of their own."""
    read_page(page, ctx, spec)
    return iter(())


# ───────── resolving a hunt area to its units ─────────


@dataclass(frozen=True)
class ResolvedArea:
    """One hunt area's units, with where they came from (§8.7: the source is evidence)."""

    species_id: str
    area_id: str
    units: tuple[UnitRef, ...]
    fractions: Mapping[str, float]
    geometry_year: int | None
    source: str  # 'gis' | 'description' | 'area_id'


def _note(description: AreaDescription | None, area_id: str) -> str:
    """`season_units.coverage_note`: the first sentence of the description + its page."""
    if description is None:
        return f"Controlled hunt area {area_id}"
    page = description.printed_page or description.page
    return f"{description.first_sentence} (see p. {page})"


def resolve_area(
    species_id: str,
    area_id: str,
    description: AreaDescription | None,
    coverage: Coverage,
) -> ResolvedArea:
    """The units of one hunt area: GIS polygon first, else the printed description.

    Every fraction in the coverage file is a real overlap — the build script already
    dropped boundary slivers (part 1) — so each one becomes a `season_units` row:
    `full` at or above `FULL_FRAC`, `partial` with its fraction below it.
    """
    note = _note(description, area_id)
    entry = coverage.area(species_id, area_id)
    if entry and entry.get("units"):
        fractions = {str(u): float(f) for u, f in entry["units"].items()}
        units = tuple(
            UnitRef(
                unit_id=unit,
                coverage="full" if frac >= FULL_FRAC else "partial",
                coverage_note=note,
                coverage_frac=frac,
            )
            for unit, frac in sorted(fractions.items(), key=lambda kv: (-kv[1], kv[0]))
        )
        return ResolvedArea(
            species_id=species_id,
            area_id=area_id,
            units=units,
            fractions=fractions,
            geometry_year=entry.get("geometry_year"),
            source="gis",
        )
    if description is not None and description.units:
        # No polygon: the printed description is the only evidence, so its units are
        # staged `partial` whenever the text restricts the hunt to part of a unit.
        coverage_value = "partial" if description.portion_only else "full"
        return ResolvedArea(
            species_id=species_id,
            area_id=area_id,
            units=tuple(
                UnitRef(unit_id=unit, coverage=coverage_value, coverage_note=note)
                for unit in description.units
            ),
            fractions={},
            geometry_year=None,
            source="description",
        )
    unit = base_unit(area_id) or area_id
    return ResolvedArea(
        species_id=species_id,
        area_id=area_id,
        units=(UnitRef(unit_id=unit, coverage="partial", coverage_note=note),),
        fractions={},
        geometry_year=None,
        source="area_id",
    )


# ───────── writing the resolution back ─────────


def _staged_areas(ctx: ParseContext, species_id: str) -> dict[str, list[int]]:
    """Staged season ids of this document, per hunt area (controlled rows only)."""
    out: dict[str, list[int]] = {}
    for row in db.q(
        "SELECT id, hunt_area FROM seasons WHERE document_id = ? AND status = 'staged'"
        " AND species_id = ? AND hunt_area IS NOT NULL",
        ctx.document_id,
        species_id,
    ):
        out.setdefault(str(row["hunt_area"]), []).append(int(row["id"]))
    return out


def write_units(season_ids: Sequence[int], resolved: ResolvedArea, units: set[str]) -> int:
    """Replace the staged `season_units` of these seasons with the resolved ones (§5).

    A resolved unit that is not a GMU is never written (D3: never guess) — the row keeps
    the units T-17a staged and `unit_coverage` reports the difference.
    """
    refs = [ref for ref in resolved.units if ref.unit_id in units]
    if not refs or not season_ids:
        return 0
    marks = ",".join("?" * len(season_ids))
    db.exec(f"DELETE FROM season_units WHERE season_id IN ({marks})", *season_ids)
    db.exec_many(
        "INSERT OR REPLACE INTO season_units (season_id, unit_id, coverage, coverage_note,"
        " coverage_frac) VALUES (?, ?, ?, ?, ?)",
        [
            (season_id, ref.unit_id, ref.coverage, ref.coverage_note, ref.coverage_frac)
            for season_id in season_ids
            for ref in refs
        ],
    )
    return len(season_ids)


def write_descriptions(
    ctx: ParseContext, species_id: str, resolved: ResolvedArea, description: AreaDescription | None
) -> None:
    """Store the verbatim description and the geometry year on `hunt_areas` (§5)."""
    db.exec(
        "UPDATE hunt_areas SET description = ?, description_page = COALESCE(?, description_page),"
        " geometry_year = ? WHERE document_id = ? AND species_id = ? AND area_id = ?",
        description.text if description else None,
        description.page if description else None,
        resolved.geometry_year,
        ctx.document_id,
        species_id,
        resolved.area_id,
    )


# ───────── §8.7 facts ─────────


def finalize(ctx: ParseContext, *, spec: Brochure, species: str) -> SectionFacts | None:
    """Resolve this species' hunt areas, write them, and report what the checks read."""
    state = state_for(ctx)
    rows = db.q(
        "SELECT * FROM hunt_areas WHERE document_id = ? AND species_id = ?",
        ctx.document_id,
        species,
    )
    if not rows:
        return None
    # `hunt_areas` is keyed per license year, so an area printed in two years (spring
    # bear) arrives twice; the footnotes of all its rows are the area's footnotes.
    footnotes: dict[str, tuple[bool, bool]] = {}
    for row in rows:
        area_id = str(row["area_id"])
        other, portion = footnotes.get(area_id, (False, False))
        footnotes[area_id] = (
            other or bool(row["includes_other_units"]),
            portion or bool(row["portion_only"]),
        )
    coverage = load_coverage(ctx.license_year)
    known_units = set(ctx.units)
    staged = _staged_areas(ctx, species)
    facts: list[AreaFacts] = []
    for area_id, (includes_other_units, portion_only) in sorted(footnotes.items()):
        description = state.descriptions.get((species, area_id))
        resolved = resolve_area(species, area_id, description, coverage)
        write_descriptions(ctx, species, resolved, description)
        seasons = write_units(staged.get(area_id, []), resolved, known_units)
        facts.append(
            AreaFacts(
                area_id=area_id,
                page=(description.printed_page or description.page) if description else None,
                described_units=description.units if description else (),
                resolved_units=tuple(ref.unit_id for ref in resolved.units),
                fractions=dict(resolved.fractions),
                partial_units=tuple(
                    ref.unit_id for ref in resolved.units if ref.coverage == "partial"
                ),
                source=resolved.source,
                geometry_year=resolved.geometry_year,
                has_description=description is not None,
                portion_in_text=description.portion_only if description else False,
                includes_other_units=includes_other_units,
                portion_only=portion_only,
                seasons=seasons,
            )
        )
    return SectionFacts(
        section=section_of(spec, species),
        hunt_area_coverage=HuntAreaCoverage(
            species_id=species,
            license_year=ctx.license_year,
            has_geometry=coverage.has_species(species),
            coverage_file=str(coverage_path(ctx.license_year).relative_to(COVERAGE_DIR.parents[2])),
            areas=facts,
        ),
    )


# ───────── registration (one section per species) ─────────


def section_of(spec: Brochure, species_id: str) -> str:
    return f"hunt_areas_{species_id}"


def _matches(page: Page) -> bool:
    """Every upright page: the section is found by its heading, never by page number."""
    return not page.rotated


def register_brochure(spec: Brochure) -> list[ParserSpec]:
    """Register one `hunt_areas_<species>` parser per species of this brochure."""
    out: list[ParserSpec] = []
    for species in dict.fromkeys(spec.species_by_prefix.values()):
        section = section_of(spec, species)
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


PARSERS = register_brochure(BIG_GAME)
