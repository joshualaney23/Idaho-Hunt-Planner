"""Regulation PDF import pipeline, DESIGN.md §8 (T-10).

    upload → store (sha256) → classify → extract pages → section router
          → species parsers → validate → staged rows → review (T-15) → publish

A parser claims pages of one brochure kind and yields `ParsedSeason`s::

    @parser(kind="big_game", match=lambda page: "Deer Tag" in page.text)
    def deer_general(page, ctx):
        yield ParsedSeason(species_id="deer", tag_type_id="deer_regular", ...)

`run_import(doc_id)` routes every page to the parsers that match it, validates each
parsed row against the DB and the document's license year, writes the survivors to
`seasons` (status `staged`) + `season_units`, and records every rejected row as a
failing `import_checks` row — never silently dropped, never guessed (D3, §8.7).
"""

from __future__ import annotations

import datetime as dt
import hashlib
import io
import json
from collections.abc import Callable, Iterable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from planner import db
from planner.dates import to_iso
from planner.importer import common
from planner.importer.classify import CLASSIFY_PAGES, Classification, ClassifyError
from planner.importer.classify import classify as classify_document
from planner.importer.extract import Page, cache_dir_for, iter_pages, page_texts

if TYPE_CHECKING:  # pragma: no cover - typing only (the checks package imports this one)
    from planner.importer.checks import SectionFacts

__all__ = [
    "PARSER_VERSION",
    "CheckResult",
    "Classification",
    "ClassifyError",
    "ImportResult",
    "ParsedSeason",
    "ParserSpec",
    "RowError",
    "classify_bytes",
    "classify_document",
    "parser",
    "parsers_for",
    "register",
    "run_import",
    "sha256_bytes",
    "stored_path",
    "use_parsers",
]

#: Stored on every document and bumped whenever parsing changes (§8.1).
PARSER_VERSION = "1.0.0"
#: Upload guards (§8.1).
MAX_PDF_BYTES = 50 * 1024 * 1024
PDF_MAGIC = b"%PDF-"


# ───────── parsed rows ─────────


@dataclass(frozen=True)
class UnitRef:
    """One unit a season applies to, with its coverage (§5 `season_units`)."""

    unit_id: str
    coverage: str = "full"
    coverage_note: str | None = None
    coverage_frac: float | None = None


@dataclass(frozen=True)
class ParsedSeason:
    """One season row a parser produced, before validation (maps to `seasons`, §5)."""

    species_id: str
    tag_type_id: str
    hunt_class: str
    weapon: str
    sex: str
    start: dt.date
    end: dt.date
    units: Sequence[str | UnitRef] = ()
    zone: str | None = None
    sub_species: str | None = None
    hunt_number: str | None = None
    hunt_area: str | None = None
    restrictions: Sequence[str] = ()
    bag_limit: str | None = None
    notes: str | None = None
    tag_quota: int | None = None
    source_page: int | None = None
    source_text: str | None = None
    source_bbox: Sequence[float] | None = None
    extract_agree: bool = False
    confidence: float = 1.0
    section: str = ""

    def unit_refs(self) -> list[UnitRef]:
        """`units` normalised to `UnitRef`s."""
        return [u if isinstance(u, UnitRef) else UnitRef(str(u)) for u in self.units]


@dataclass(frozen=True)
class RowError:
    """A parsed row that failed validation: staged as `fail`, never written (D3)."""

    section: str
    page: int | None
    reason: str
    detail: str
    source_text: str | None = None


@dataclass
class ImportResult:
    """What `run_import` did, mirrored into `source_documents` + `import_checks`."""

    document_id: int
    status: str
    pages: int = 0
    parsed: int = 0
    staged: int = 0
    errors: list[RowError] = field(default_factory=list)
    sections: list[str] = field(default_factory=list)
    parser_version: str = PARSER_VERSION
    error: str | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "document_id": self.document_id,
            "status": self.status,
            "pages": self.pages,
            "parsed": self.parsed,
            "staged": self.staged,
            "sections": self.sections,
            "parser_version": self.parser_version,
            "errors": [
                {
                    "section": e.section,
                    "page": e.page,
                    "reason": e.reason,
                    "detail": e.detail,
                    "source_text": e.source_text,
                }
                for e in self.errors
            ],
            "error": self.error,
        }


@dataclass(frozen=True)
class ParseContext:
    """What a parser knows about the document it is reading."""

    document_id: int
    kind: str
    license_year: int
    year_end: int
    units: tuple[str, ...] = ()
    title: str = ""


ParserFn = Callable[[Page, ParseContext], Iterable[ParsedSeason]]
MatchFn = Callable[[Page], bool]


@dataclass(frozen=True)
class CheckResult:
    """One §8.7 invariant a parser ran over the whole document."""

    name: str
    status: str  # 'pass' | 'fail' | 'warn'
    expected: str | None = None
    actual: str | None = None
    detail: Any = None


BeginFn = Callable[["ParseContext"], None]
#: `finalize` returns the facts its section measured; the §8.7 checks live in the
#: `planner.importer.checks` registry and read those facts (T-22a).
FinalizeFn = Callable[["ParseContext"], "SectionFacts | None"]


@dataclass(frozen=True)
class ParserSpec:
    """A registered page parser (§8.1 section router)."""

    name: str
    kind: str
    match: MatchFn
    fn: ParserFn
    section: str = ""
    #: Called once before the first page — a parser resets its per-document state here.
    begin: BeginFn | None = None
    #: Called once after the last page — returns the section's `SectionFacts` (§8.7).
    finalize: FinalizeFn | None = None


# ───────── registry ─────────

_REGISTRY: list[ParserSpec] = []


def register(spec: ParserSpec) -> ParserSpec:
    """Add a parser to the registry (last registration for a name wins)."""
    global _REGISTRY
    _REGISTRY = [s for s in _REGISTRY if s.name != spec.name] + [spec]
    return spec


def parser(
    *,
    kind: str,
    match: MatchFn,
    name: str | None = None,
    section: str | None = None,
    begin: BeginFn | None = None,
    finalize: FinalizeFn | None = None,
) -> Callable[[ParserFn], ParserFn]:
    """Register a page parser for one brochure `kind` (§8.1)::

    @parser(kind="big_game", match=lambda page: "Deer Tag" in page.text)
    def deer_general(page, ctx): ...

    `begin` runs once before the first page (reset per-document state) and `finalize`
    once after the last one, returning the `SectionFacts` the §8.7 check registry reads.
    """

    def deco(fn: ParserFn) -> ParserFn:
        parser_name = name or fn.__name__
        register(
            ParserSpec(
                name=parser_name,
                kind=kind,
                match=match,
                fn=fn,
                section=section or parser_name,
                begin=begin,
                finalize=finalize,
            )
        )
        return fn

    return deco


def parsers_for(kind: str) -> list[ParserSpec]:
    """Registered parsers that claim this brochure kind, in registration order."""
    return [s for s in _REGISTRY if s.kind == kind]


def registered() -> list[ParserSpec]:
    """Every registered parser."""
    return list(_REGISTRY)


@contextmanager
def use_parsers(*specs: ParserSpec) -> Iterator[list[ParserSpec]]:
    """Swap the registry for the block — test isolation, and `use_parsers()` clears it."""
    global _REGISTRY
    saved = _REGISTRY
    _REGISTRY = list(specs)
    try:
        yield _REGISTRY
    finally:
        _REGISTRY = saved


# ───────── stored files ─────────


def sha256_bytes(data: bytes) -> str:
    """Hex sha256 of an uploaded file — the document's identity (§8.1 dedupe)."""
    return hashlib.sha256(data).hexdigest()


def stored_path(regs_dir: str | Path, license_year: int, sha256: str) -> Path:
    """`data/regs/<year>/<sha256>.pdf` (§5 `source_documents.stored_path`)."""
    return Path(regs_dir) / str(license_year) / f"{sha256}.pdf"


def save_pdf(regs_dir: str | Path, license_year: int, sha256: str, data: bytes) -> Path:
    """Write an uploaded PDF to its content-addressed path and return it."""
    path = stored_path(regs_dir, license_year, sha256)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def looks_like_pdf(data: bytes) -> bool:
    """Magic-number check: the upload really starts with `%PDF-`."""
    return data[: len(PDF_MAGIC)] == PDF_MAGIC


def classify_bytes(
    data: bytes,
    *,
    kind: str | None = None,
    year: int | None = None,
    filename: str | None = None,
) -> Classification:
    """Classify an upload straight from memory, before it is stored (§8.1)."""
    texts = page_texts(io.BytesIO(data), CLASSIFY_PAGES)
    return classify_document(texts, kind=kind, year=year, filename=filename)


# ───────── validation (§8.1) ─────────

VALID_HUNT_CLASSES = {"general", "controlled", "extra", "youth", "outfitter", "other"}
VALID_WEAPONS = {"any", "archery", "muzzleloader", "short_range", "shotgun", "falconry"}
VALID_SEX = {"antlered", "antlerless", "either", "any", "male", "na"}


def validate(
    season: ParsedSeason,
    ctx: ParseContext,
    *,
    species: set[str],
    tag_types: set[str],
    units: set[str],
) -> list[str]:
    """Every reason this row may not be staged, as plain sentences (empty = valid)."""
    problems: list[str] = []
    if season.species_id not in species:
        problems.append(f"unknown species {season.species_id!r}")
    if season.tag_type_id not in tag_types:
        problems.append(f"unknown tag type {season.tag_type_id!r}")
    if season.hunt_class not in VALID_HUNT_CLASSES:
        problems.append(f"invalid hunt_class {season.hunt_class!r}")
    if season.weapon not in VALID_WEAPONS:
        problems.append(f"invalid weapon {season.weapon!r}")
    if season.sex not in VALID_SEX:
        problems.append(f"invalid sex {season.sex!r}")
    if season.end < season.start:
        problems.append(f"end {to_iso(season.end)} is before start {to_iso(season.start)}")
    for label, value in (("start", season.start), ("end", season.end)):
        if not common.in_license_year(value, ctx.license_year, ctx.year_end):
            problems.append(
                f"{label} {to_iso(value)} is outside the {ctx.license_year} license year"
                f" (Jul 1 {ctx.license_year} – Jun 30 {ctx.year_end + 1})"
            )
    refs = season.unit_refs()
    if not refs:
        problems.append("no units")
    unknown = [r.unit_id for r in refs if r.unit_id not in units]
    if unknown:
        problems.append(f"unknown units {', '.join(unknown)}")
    bad_coverage = [r.coverage for r in refs if r.coverage not in ("full", "partial")]
    if bad_coverage:
        problems.append(f"invalid coverage {', '.join(sorted(set(bad_coverage)))}")
    bad_codes = sorted(set(season.restrictions) - common.RESTRICTION_CODES)
    if bad_codes:
        problems.append(f"unknown restriction codes {', '.join(bad_codes)}")
    return problems


# ───────── the pipeline ─────────


def _now() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


def _insert_season(document_id: int, season: ParsedSeason) -> int:
    cur = db.exec(
        "INSERT INTO seasons (document_id, status, species_id, sub_species, tag_type_id,"
        " zone, hunt_number, hunt_area, hunt_class, weapon, sex, start_date, end_date,"
        " restrictions, bag_limit, notes, tag_quota, source_page, source_text, source_bbox,"
        " extract_agree, confidence, section)"
        " VALUES (?, 'staged', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        document_id,
        season.species_id,
        season.sub_species,
        season.tag_type_id,
        season.zone,
        season.hunt_number,
        season.hunt_area,
        season.hunt_class,
        season.weapon,
        season.sex,
        to_iso(season.start),
        to_iso(season.end),
        json.dumps(list(season.restrictions)),
        season.bag_limit,
        season.notes,
        season.tag_quota,
        season.source_page,
        season.source_text,
        json.dumps(list(season.source_bbox)) if season.source_bbox else None,
        1 if season.extract_agree else 0,
        float(season.confidence),
        season.section or None,
    )
    season_id = int(cur.lastrowid or 0)
    db.exec_many(
        "INSERT OR REPLACE INTO season_units (season_id, unit_id, coverage, coverage_note,"
        " coverage_frac) VALUES (?, ?, ?, ?, ?)",
        [
            (season_id, r.unit_id, r.coverage, r.coverage_note, r.coverage_frac)
            for r in season.unit_refs()
        ],
    )
    return season_id


def _record_check(document_id: int, section: str, name: str, status: str, **kw: Any) -> None:
    db.exec(
        "INSERT INTO import_checks (document_id, section, check_name, status, expected,"
        " actual, detail, run_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        document_id,
        section,
        name,
        status,
        kw.get("expected"),
        kw.get("actual"),
        json.dumps(kw["detail"]) if kw.get("detail") is not None else None,
        _now(),
    )


def _set_status(document_id: int, status: str, **cols: Any) -> None:
    sets = ", ".join(f"{c} = ?" for c in ("status", *cols))
    db.exec(
        f"UPDATE source_documents SET {sets} WHERE id = ?",
        status,
        *cols.values(),
        document_id,
    )


def run_import(
    doc_id: int,
    *,
    pdf_path: str | Path | None = None,
    data_dir: str | Path | None = None,
    conn: Any = None,
) -> ImportResult:
    """Parse a stored document into staged seasons (§8.1). Never raises on parse errors.

    The document ends as `needs_review` (even with zero rows: review decides) or, when
    the PDF itself can't be read, as `failed` with the error text.
    """
    ctxmgr = db.use(conn) if conn is not None else _null_context()
    with ctxmgr:
        doc = db.q1("SELECT * FROM source_documents WHERE id = ?", doc_id)
        if doc is None:
            raise LookupError(f"no source document {doc_id}")
        path = Path(pdf_path or doc["stored_path"] or "")
        result = ImportResult(document_id=doc_id, status="parsing")
        _set_status(doc_id, "parsing", parser_version=PARSER_VERSION, error=None)

        units = {r["id"] for r in db.q("SELECT id FROM units")}
        species = {r["id"] for r in db.q("SELECT id FROM species")}
        tag_types = {r["id"] for r in db.q("SELECT id FROM tag_types")}
        ctx = ParseContext(
            document_id=doc_id,
            kind=doc["kind"],
            license_year=int(doc["license_year"]),
            year_end=int(doc["year_end"]),
            units=tuple(sorted(units, key=common.natural_key)),
            title=doc["title"] or "",
        )
        specs = parsers_for(ctx.kind)

        try:
            if not path.is_file():
                raise FileNotFoundError(f"stored PDF is missing: {path}")
            cache = cache_dir_for(doc["sha256"] or f"doc{doc_id}", data_dir or _data_dir_of(path))
            rows: list[tuple[ParsedSeason, ParserSpec]] = []
            pages = 0
            for spec in specs:
                if spec.begin is not None:
                    spec.begin(ctx)
            for page in iter_pages(path, cache_dir=cache):
                pages += 1
                for spec in specs:
                    try:
                        if not spec.match(page):
                            continue
                        for season in spec.fn(page, ctx):
                            rows.append((_with_defaults(season, spec, page), spec))
                    except Exception as exc:  # one bad page never kills the import
                        result.errors.append(
                            RowError(
                                section=spec.section,
                                page=page.number,
                                reason="parser_error",
                                detail=f"{type(exc).__name__}: {exc}",
                            )
                        )
            result.pages = pages
            result.parsed = len(rows)
        except Exception as exc:
            result.status = "failed"
            result.error = f"{type(exc).__name__}: {exc}"
            _set_status(doc_id, "failed", error=result.error, imported_at=_now())
            return result

        sections: list[str] = []
        with db.tx():
            db.exec("DELETE FROM seasons WHERE document_id = ? AND status = 'staged'", doc_id)
            db.exec("DELETE FROM import_checks WHERE document_id = ?", doc_id)
            for season, spec in rows:
                problems = validate(season, ctx, species=species, tag_types=tag_types, units=units)
                if problems:
                    result.errors.append(
                        RowError(
                            section=spec.section,
                            page=season.source_page,
                            reason="invalid_row",
                            detail="; ".join(problems),
                            source_text=season.source_text,
                        )
                    )
                    continue
                _insert_season(doc_id, season)
                result.staged += 1
                if spec.section not in sections:
                    sections.append(spec.section)

            # Every section's `finalize` runs before any check does: a section can
            # rewrite another's staged rows (T-17b resolves the controlled hunts'
            # `season_units` from the hunt-area descriptions), and a check that reads
            # the database (`golden_match`) must see the finished rows.
            measured: list[tuple[ParserSpec, SectionFacts | None]] = []
            for spec in specs:
                bad = [e for e in result.errors if e.section == spec.section]
                staged = sum(1 for s, sp in rows if sp is spec) - len(bad)
                _record_check(
                    doc_id,
                    spec.section,
                    "row_validation",
                    "fail" if bad else "pass",
                    expected=str(staged + len(bad)),
                    actual=str(max(staged, 0)),
                    detail=[{"page": e.page, "reason": e.reason, "detail": e.detail} for e in bad]
                    or None,
                )
                measured.append((spec, _section_facts(spec, ctx, result)))

            for spec, facts in measured:
                for check in checks.run_checks(ctx, spec.section, facts):
                    _record_check(
                        doc_id,
                        spec.section,
                        check.name,
                        check.status,
                        expected=check.expected,
                        actual=check.actual,
                        detail=check.detail,
                    )
            result.sections = sections
            result.status = "needs_review"
            _set_status(
                doc_id,
                "needs_review",
                parser_version=PARSER_VERSION,
                imported_at=_now(),
                pages=result.pages,
                error=None,
            )
        return result


def _section_facts(
    spec: ParserSpec, ctx: ParseContext, result: ImportResult
) -> SectionFacts | None:
    """The facts a section measured — a crash while collecting them fails loudly (D3)."""
    if spec.finalize is None:
        return None
    try:
        return spec.finalize(ctx)
    except Exception as exc:  # a broken parser never kills the import, it fails loudly
        result.errors.append(
            RowError(
                section=spec.section,
                page=None,
                reason="check_error",
                detail=f"{type(exc).__name__}: {exc}",
            )
        )
        _record_check(
            ctx.document_id,
            spec.section,
            "checks_ran",
            "fail",
            expected="checks run",
            actual=f"{type(exc).__name__}: {exc}",
        )
        return None


def _data_dir_of(pdf_path: Path) -> Path:
    """`data/` for a stored `data/regs/<year>/<sha>.pdf` (fallback: the file's folder)."""
    parents = pdf_path.resolve().parents
    return parents[2] if len(parents) >= 3 else pdf_path.parent


def _with_defaults(season: ParsedSeason, spec: ParserSpec, page: Page) -> ParsedSeason:
    """Fill in the page and section a parser didn't bother to set."""
    changes: dict[str, Any] = {}
    if season.source_page is None:
        changes["source_page"] = page.number
    if not season.section:
        changes["section"] = spec.section
    return replace(season, **changes) if changes else season


@contextmanager
def _null_context() -> Iterator[None]:
    yield None


# Importing the check registry and the parser modules registers them (§8.1 section
# router, §8.7 checks). Kept last so the API above is fully defined when they import it.
from planner.importer import (  # noqa: E402,F401  (side effect)
    bear_lion_wolf,
    checks,
    controlled,
    deer,
    elk,
    hunt_areas,
)
from planner.importer.checks import SectionFacts  # noqa: E402,F401  (re-exported for parsers)
