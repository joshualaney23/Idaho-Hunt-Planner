"""Detect document kind and year from text (T-10, DESIGN.md §8.1).

`classify(texts)` reads the first few pages of a regulation PDF and returns the
`source_documents` fields that identify it: `kind`, `license_year`, `year_end` and a
title. It never guesses: when nothing matches it raises `ClassifyError`, and the upload
endpoint then requires the manual `kind`/`year` form fields.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

#: `source_documents.kind` vocabulary (§5). `manual` is for hand-entered seasons.
KINDS: tuple[str, ...] = (
    "big_game",
    "upland",
    "moose_sheep_goat",
    "migratory",
    "supplemental",
    "manual",
)
#: Pages read when classifying (§8.1: page 1–3 text).
CLASSIFY_PAGES = 3
#: Supplements override the base brochure (§8.5).
PRIORITY = {"supplemental": 10, "manual": 20}

_YEAR = r"(?:19|20)\d{2}"
#: "2026-2027", "2026–27", "2025-26" → (start, end).
_YEAR_RANGE_RE = re.compile(rf"\b({_YEAR})\s*[-–—/]\s*((?:19|20)?\d{{2}})\b")
_YEAR_RE = re.compile(rf"\b({_YEAR})\b")

#: kind → (patterns that must all match, at least one of which names the document).
_KIND_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("supplemental", (r"supplemental\s+proclamation",)),
    (
        "moose_sheep_goat",
        (r"moose[, ].{0,40}(?:bighorn\s+)?sheep.{0,40}(?:mountain\s+)?goat",),
    ),
    ("upland", (r"upland\s+game.{0,40}turkey", r"upland\s+game\s+(?:bird|animal|seasons)")),
    ("migratory", (r"migratory\s+(?:game\s+)?bird",)),
    ("big_game", (r"big\s+game\s+seasons?\s*(?:&|and)\s*rules", r"big\s+game")),
)
_KIND_RES: tuple[tuple[str, tuple[re.Pattern[str], ...]], ...] = tuple(
    (kind, tuple(re.compile(p, re.IGNORECASE | re.DOTALL) for p in pats))
    for kind, pats in _KIND_PATTERNS
)


class ClassifyError(ValueError):
    """The document's kind or year couldn't be read from its first pages."""

    def __init__(self, message: str, *, sample: str = "") -> None:
        super().__init__(message)
        self.sample = sample


@dataclass(frozen=True)
class Classification:
    """What the first pages say this document is."""

    kind: str
    license_year: int
    year_end: int
    title: str
    priority: int = 0
    manual_kind: bool = False
    manual_year: bool = False


def _text_of(pages: Sequence[Any]) -> str:  # noqa: ANN401 - Page or plain str
    out = []
    for p in pages[:CLASSIFY_PAGES]:
        out.append(p if isinstance(p, str) else getattr(p, "text", "") or "")
    return "\n".join(out)


def _years(text: str) -> tuple[int, int] | None:
    """License year and last covered year, from a `YYYY–YY` range or a single year."""
    if m := _YEAR_RANGE_RE.search(text):
        start = int(m.group(1))
        tail = m.group(2)
        end = int(tail) if len(tail) == 4 else int(str(start)[:2] + tail.zfill(2))
        if start <= end <= start + 1:
            return start, end
    if m := _YEAR_RE.search(text):
        year = int(m.group(1))
        return year, year
    return None


def detect_kind(text: str) -> str | None:
    """The `source_documents.kind` named by this text, or None."""
    for kind, patterns in _KIND_RES:
        if any(p.search(text) for p in patterns):
            return kind
    return None


def title_of(text: str) -> str:
    """The first substantial line of the cover page, used as the document title."""
    for line in text.splitlines():
        cleaned = re.sub(r"\s+", " ", line).strip()
        if len(cleaned) >= 8 and re.search(r"[A-Za-z]{4}", cleaned):
            return cleaned[:120]
    return ""


def classify(
    pages: Sequence[Any],
    *,
    kind: str | None = None,
    year: int | None = None,
    filename: str | None = None,
) -> Classification:
    """Classify a document from its first pages, with manual overrides winning.

    `pages` may be `Page` objects or plain page texts. `kind`/`year` come from the
    upload form (§8.1 "manual override via form fields").
    """
    if kind is not None and kind not in KINDS:
        raise ClassifyError(f"unknown kind {kind!r}: expected one of {', '.join(KINDS)}")
    text = _text_of(pages)
    haystack = f"{text}\n{filename or ''}"
    found_kind = kind or detect_kind(haystack)
    if found_kind is None:
        raise ClassifyError(
            "could not tell which brochure this is; pass kind explicitly",
            sample=text[:400],
        )

    years = (year, year) if year is not None else _years(haystack)
    if years is None:
        raise ClassifyError(
            f"could not find a license year in the first {CLASSIFY_PAGES} pages;"
            " pass year explicitly",
            sample=text[:400],
        )
    license_year, year_end = years
    if found_kind in ("big_game", "supplemental", "manual") and year is None:
        year_end = license_year  # single-year brochures, even when a range is printed
    return Classification(
        kind=found_kind,
        license_year=license_year,
        year_end=max(year_end, license_year),
        title=title_of(text) or (filename or ""),
        priority=PRIORITY.get(found_kind, 0),
        manual_kind=kind is not None,
        manual_year=year is not None,
    )
