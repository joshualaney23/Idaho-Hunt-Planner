"""Accuracy invariants, DESIGN.md §8.7 (T-22a).

The registry owns the **checks**; a parser owns only the **facts** its section measured.
A parser's `finalize` hook returns one `SectionFacts`; `run_import` then runs every
check whose `section_glob` matches the section and writes all of them to
`import_checks`. A check that finds no facts for its invariant returns `None` and is
not recorded, so a parser opts into an invariant by filling in the field it reads —
it never re-implements the check (§12 T-22a).

    @check("controlled_*")
    def page_ref_resolves(c: CheckContext) -> CheckResult | None: ...

Checks are pure: they read `CheckContext` and return a `CheckResult`. Anything that
writes to the database stays in the parser's `finalize`.
"""

from __future__ import annotations

import datetime as dt
import fnmatch
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any

from planner.importer import CheckResult

if TYPE_CHECKING:  # pragma: no cover - typing only (the parse context lives one level up)
    from planner.importer import ParseContext

__all__ = [
    "AreaFacts",
    "CheckContext",
    "CheckResult",
    "CheckSpec",
    "Coverage",
    "DateCell",
    "HuntAreaCoverage",
    "MethodWindow",
    "SeasonRef",
    "SectionFacts",
    "TwoPath",
    "check",
    "checks_for",
    "registered",
    "run_checks",
    "use_checks",
]


# ───────── the facts a section hands to its checks ─────────


@dataclass(frozen=True)
class TwoPath:
    """§8.7 principle 3: what the two independent extraction paths compared."""

    #: Rows both paths produced a verdict on.
    compared: int
    #: Rows whose key fields differ between the paths.
    disagreeing: int
    #: One entry per disagreement, as it is stored in `import_checks.detail`.
    disagreements: Sequence[Any] = ()
    #: Text-layer lines a neighbour swallowed (path B only), reported but not fatal.
    merged_lines: Sequence[str] = ()


@dataclass(frozen=True)
class DateCell:
    """One printed date cell and the seasons the parser made from it (§8.7 accounting).

    `texts` are the cell's printed strings; `seasons` is how many parsed seasons the
    parser produced from them. The invariant is that every `Mon D - Mon D` token in the
    cell is consumed by **exactly one** season, so the two counts must be equal and no
    date text may be left over.
    """

    page: int
    texts: tuple[str, ...]
    seasons: int
    #: Hunt number or unit list — what names this cell on the page.
    label: str = ""
    #: Date/qualifier text nothing explained (D3: never silently dropped).
    leftovers: tuple[str, ...] = ()
    #: The row's bbox, the same one staged as `seasons.source_bbox`.
    bbox: tuple[float, float, float, float] | None = None
    #: This cell must yield at least one season (a controlled hunt always prints one);
    #: an empty deer cell (`No Season`) legitimately yields none.
    must_produce: bool = False
    #: The window is printed once but **recurs every year** (the wolf table's
    #: `Apr 1 - Nov 14` inside a `July 2026 - June 2027` table), so one printed token
    #: legitimately produces more than one license-year season (§12 T-13). The
    #: invariant then is that every token produced *at least* one.
    recurring: bool = False


@dataclass(frozen=True)
class MethodWindow:
    """A date window that rules a *method*, not a season (§12 T-13).

    The bear and lion tables print `Dogs Prohibited` and `Dog Training` windows beside
    the take season. They never produce a season row, so `date_token_accounting` must
    not see them — but every date printed in the brochure still has to be accounted
    for (§8.7 principle 4), which is what `method_window_accounting` does with these.
    """

    page: int
    #: `dogs_prohibited` / `dog_training` — which column it came from.
    name: str
    text: str
    #: Date ranges the parser read out of `text`.
    windows: int
    #: Unit list of the row the window belongs to.
    label: str = ""
    #: Date text in the cell nothing explained (D3).
    leftovers: tuple[str, ...] = ()


@dataclass(frozen=True)
class Coverage:
    """Which GMUs a general section covered, and the text that explains the rest."""

    covered: frozenset[str]
    all_units: tuple[str, ...]
    #: Units that are never huntable (YNP) and so need no season.
    excluded: frozenset[str] = frozenset()
    #: Page texts a controlled-only unit may be confirmed in.
    texts: tuple[str, ...] = ()


@dataclass(frozen=True)
class AreaFacts:
    """One controlled hunt area, as the §12 T-17b checks read it.

    `described_units` are the GMU tokens printed in the area's own description;
    `resolved_units` are the ones it was resolved to (the GIS polygon's units when it
    has one, else the described ones), and `fractions` is the share of each GMU that
    lies inside the polygon.
    """

    area_id: str
    #: Printed page of the description (what a reviewer turns to), `None` when undescribed.
    page: int | None = None
    described_units: tuple[str, ...] = ()
    resolved_units: tuple[str, ...] = ()
    fractions: Mapping[str, float] = field(default_factory=dict)
    partial_units: tuple[str, ...] = ()
    #: `gis` | `description` | `area_id` — what the units were resolved from.
    source: str = "area_id"
    geometry_year: int | None = None
    has_description: bool = False
    #: The description's own wording restricts the hunt to part of a unit.
    portion_in_text: bool = False
    #: Footnote `a` printed on the hunt row.
    includes_other_units: bool = False
    #: Footnote `b` printed on the hunt row.
    portion_only: bool = False
    #: Staged seasons whose `season_units` this area resolved.
    seasons: int = 0

    @property
    def has_polygon(self) -> bool:
        return self.source == "gis"


@dataclass(frozen=True)
class HuntAreaCoverage:
    """What one `hunt_areas_<species>` section resolved (§12 T-17b)."""

    species_id: str
    license_year: int
    #: The committed coverage file has polygons for this species.
    has_geometry: bool
    coverage_file: str
    areas: Sequence[AreaFacts] = ()


@dataclass(frozen=True)
class SeasonRef:
    """A parsed season, reduced to what the year-over-year check reads."""

    label: str
    title: str
    start: dt.date


@dataclass
class SectionFacts:
    """Everything one section measured while parsing a document.

    Every field defaults to `None` meaning *this section has nothing to say about that
    invariant*; the matching check then returns `None` and no row is written.
    """

    section: str
    #: `two_path_agreement`
    two_path: TwoPath | None = None
    #: `date_token_accounting`
    date_cells: Sequence[DateCell] | None = None
    #: `unit_tokens_valid` — `(page, token)` for every token that is not a GMU.
    unknown_units: Sequence[tuple[int, str]] | None = None
    #: `no_duplicate_keys` — the key every staged row must be unique on.
    keys: Sequence[tuple[str, ...]] | None = None
    #: How that key reads in the check's message.
    key_label: str = "key"
    #: `hunt_number_completeness`
    hunt_numbers_in_text: Sequence[str] | None = None
    hunt_numbers_parsed: Sequence[str] | None = None
    #: `tag_checksum` — one entry per printed total: `title/header/parsed/unlimited/rows`.
    tag_totals: Sequence[dict[str, Any]] | None = None
    #: `general_coverage`
    coverage: Coverage | None = None
    #: `yoy_diff` — the first window of every parsed hunt.
    first_windows: Sequence[SeasonRef] | None = None
    #: `page_ref_resolves` — `(See pg N)` pointers, and the ones that resolve to nothing.
    page_refs: int | None = None
    page_refs_unresolved: Sequence[Any] = ()
    #: `footnote_legend`
    unknown_marks: Sequence[Any] | None = None
    unreadable_titles: Sequence[Any] = ()
    #: `corrections_applied` — physical pages that print a "Corrections" heading.
    corrections_pages: Sequence[int] | None = None
    #: `hunt_areas_written` — what the parser wrote while finalizing.
    hunt_areas: dict[str, int] | None = None
    #: `gis_area_match` / `unit_coverage` / `footnote_consistency` / `geometry_year` (T-17b).
    hunt_area_coverage: HuntAreaCoverage | None = None
    #: Room for a section-specific check that has no shared field yet.
    extra: dict[str, Any] = field(default_factory=dict)


# ───────── the registry ─────────

CheckFn = Callable[["CheckContext"], "CheckResult | None"]


@dataclass(frozen=True)
class CheckSpec:
    """One registered §8.7 invariant."""

    name: str
    section_glob: str
    fn: CheckFn


@dataclass(frozen=True)
class CheckContext:
    """What a check reads: the document, its section, and that section's facts."""

    parse: ParseContext
    section: str
    facts: SectionFacts

    @property
    def license_year(self) -> int:
        return self.parse.license_year


_REGISTRY: list[CheckSpec] = []


def register_check(spec: CheckSpec) -> CheckSpec:
    """Add a check (a later registration of the same name and glob replaces it)."""
    global _REGISTRY
    _REGISTRY = [
        s for s in _REGISTRY if (s.name, s.section_glob) != (spec.name, spec.section_glob)
    ] + [spec]
    return spec


def check(section_glob: str = "*", name: str | None = None) -> Callable[[CheckFn], CheckFn]:
    """Register a §8.7 invariant for every section matching `section_glob`.

    The check's name is the function's name unless `name` says otherwise, and it is the
    `import_checks.check_name` the review gate (T-22b) reads.
    """

    def deco(fn: CheckFn) -> CheckFn:
        register_check(CheckSpec(name=name or fn.__name__, section_glob=section_glob, fn=fn))
        return fn

    return deco


def checks_for(section: str) -> list[CheckSpec]:
    """Every registered check that claims this section, in registration order."""
    return [s for s in _REGISTRY if fnmatch.fnmatchcase(section, s.section_glob)]


def registered() -> list[CheckSpec]:
    """Every registered check."""
    return list(_REGISTRY)


@contextmanager
def use_checks(*specs: CheckSpec) -> Iterator[list[CheckSpec]]:
    """Swap the registry for the block — test isolation, and `use_checks()` clears it."""
    global _REGISTRY
    saved = _REGISTRY
    _REGISTRY = list(specs)
    try:
        yield _REGISTRY
    finally:
        _REGISTRY = saved


def run_checks(parse: ParseContext, section: str, facts: SectionFacts | None) -> list[CheckResult]:
    """Run every check matching `section`. A check that crashes fails; the rest still run."""
    if facts is None:
        return []
    ctx = CheckContext(parse=parse, section=section, facts=facts)
    out: list[CheckResult] = []
    for spec in checks_for(section):
        try:
            result = spec.fn(ctx)
        except Exception as exc:  # a broken check never kills the import — it fails loudly
            result = CheckResult(
                name=spec.name,
                status="fail",
                expected="the check runs",
                actual=f"{type(exc).__name__}: {exc}",
            )
        if result is None:
            continue
        out.append(result if result.name == spec.name else replace(result, name=spec.name))
    return out


# Importing the check modules registers them. Kept last so the registry API above is
# fully defined when they import it.
from planner.importer.checks import (  # noqa: E402,F401  (side effect)
    controlled,
    golden,
    hunt_areas,
    method_windows,
    shared,
)
