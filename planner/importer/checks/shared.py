"""The invariants every section inherits (DESIGN.md §8.7, §12 T-22a).

One implementation per invariant, shared by every parser. A check returns `None` when
the section's facts say nothing about its invariant, so a parser opts in by filling in
the matching `SectionFacts` field.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any

from planner.importer import common
from planner.importer.checks import CheckContext, CheckResult, check

#: A controlled hunt row (`1081 68A Unlimited …`) or a hunt-area description heading —
#: how a unit with no general season is confirmed as controlled-only (§8.7).
_CONTROLLED_ROW_RE = r"(?m)^\s*\d{{4}}\s+{unit}(?:-\d+[A-Z]*)?\b"
_CONTROLLED_AREA_RE = r"(?m)Hunt Area {unit}(?:-\d+[A-Z]*)?\b"


@check()
def two_path_agreement(c: CheckContext) -> CheckResult | None:
    """§8.7 principle 3: the ruled-table path and the text-layer path agree per row."""
    facts = c.facts.two_path
    if facts is None:
        return None
    merged = list(facts.merged_lines)
    actual = f"{facts.compared - facts.disagreeing} agree"
    if merged:
        actual += f", {len(merged)} text lines merged with a neighbour"
    detail: dict[str, Any] = {
        "disagreements": list(facts.disagreements),
        "text_line_merged": merged,
    }
    return CheckResult(
        name="two_path_agreement",
        status="pass" if not facts.disagreements else "fail",
        expected=f"{facts.compared} rows agree",
        actual=actual,
        detail=detail if facts.disagreements or merged else None,
    )


@check()
def hunt_number_completeness(c: CheckContext) -> CheckResult | None:
    """Every hunt number that starts a line on a hunt-table page was parsed (§8.7).

    Gaps in the printed numbering are legitimate, so only the printed set is the truth.
    """
    in_text = c.facts.hunt_numbers_in_text
    if in_text is None:
        return None
    parsed = list(c.facts.hunt_numbers_parsed or ())
    missing = sorted(set(in_text) - set(parsed))
    return CheckResult(
        name="hunt_number_completeness",
        status="pass" if not missing else "fail",
        expected=f"{len(set(in_text))} hunt numbers start a line in the page text",
        actual=f"{len(parsed)} parsed, {len(missing)} missing",
        detail={"missing": missing} if missing else None,
    )


@check()
def no_duplicate_keys(c: CheckContext) -> CheckResult | None:
    """No two staged rows share a key. A hunt number can print several season windows
    (§12 T-17a), so the key is the window, not the number alone."""
    keys = c.facts.keys
    if keys is None:
        return None
    seen = list(keys)
    duplicates = sorted({"-".join(str(p) for p in k) for k in seen if seen.count(k) > 1})
    return CheckResult(
        name="no_duplicate_keys",
        status="pass" if not duplicates else "fail",
        expected=f"every {c.facts.key_label} unique per document",
        actual=f"{len(duplicates)} duplicates",
        detail=duplicates or None,
    )


@check()
def date_token_accounting(c: CheckContext) -> CheckResult | None:
    """Every `Mon D - Mon D` token printed in a date cell is consumed by **exactly one**
    parsed season, and no date text is left unexplained (§8.7, D3)."""
    cells = c.facts.date_cells
    if cells is None:
        return None
    tokens = 0
    seasons = 0
    unaccounted: list[dict[str, Any]] = []
    for cell in cells:
        found = sum(len(common.parse_date_ranges(t, c.license_year)) for t in cell.texts)
        tokens += found
        seasons += cell.seasons
        # A recurring window is printed once and applies twice inside one license year
        # (§12 T-13), so there the rule is "every token produced at least one season".
        short = cell.seasons < found if cell.recurring else cell.seasons != found
        if short or cell.leftovers or (cell.must_produce and not cell.seasons):
            unaccounted.append(
                {
                    "page": cell.page,
                    "label": cell.label,
                    "text": list(cell.texts),
                    "tokens": found,
                    "seasons": cell.seasons,
                    "leftover": list(cell.leftovers),
                    "bbox": list(cell.bbox) if cell.bbox else None,
                }
            )
    return CheckResult(
        name="date_token_accounting",
        # Per-cell accounting is the real invariant: a recurring window makes the
        # document-wide totals differ legitimately (§12 T-13).
        status="pass" if not unaccounted else "fail",
        expected=f"{tokens} date tokens consumed",
        actual=f"{seasons} seasons from {len(cells)} date cells, {len(unaccounted)} unaccounted",
        detail=unaccounted or None,
    )


@check()
def unit_tokens_valid(c: CheckContext) -> CheckResult | None:
    """Every unit token the parser read is a real GMU in `units` (§8.7)."""
    unknown = c.facts.unknown_units
    if unknown is None:
        return None
    return CheckResult(
        name="unit_tokens_valid",
        status="pass" if not unknown else "fail",
        expected="every unit token is a GMU in `units`",
        actual=f"{len(unknown)} unknown tokens",
        detail=[{"page": page, "token": token} for page, token in unknown] or None,
    )


@check()
def tag_checksum(c: CheckContext) -> CheckResult | None:
    """Σ the Tags column == every total printed in a section header or sub-header (§8.7)."""
    totals = c.facts.tag_totals
    if totals is None:
        return None
    summed = list(totals)
    bad = [s for s in summed if s["header"] != s["parsed"]]
    return CheckResult(
        name="tag_checksum",
        status="pass" if summed and not bad else "fail",
        expected=", ".join(f"{s['header']}" for s in summed) or "a printed tag total",
        actual=", ".join(f"{s['parsed']}" for s in summed) or "no total found",
        detail=summed or None,
    )


def controlled_only(unit: str, texts: Sequence[str]) -> bool:
    """Does a controlled hunt table or hunt-area description name this unit (§8.7)?"""
    row = re.compile(_CONTROLLED_ROW_RE.format(unit=re.escape(unit)))
    area = re.compile(_CONTROLLED_AREA_RE.format(unit=re.escape(unit)))
    return any(row.search(text) or area.search(text) for text in texts)


@check()
def general_coverage(c: CheckContext) -> CheckResult | None:
    """Every GMU has a general season, or is confirmed controlled-only in the text (§8.7)."""
    cov = c.facts.coverage
    if cov is None:
        return None
    units = set(cov.all_units) - set(cov.excluded)
    uncovered = sorted(units - set(cov.covered), key=common.natural_key)
    missing = [u for u in uncovered if not controlled_only(u, cov.texts)]
    controlled = [u for u in uncovered if u not in missing]
    return CheckResult(
        name="general_coverage",
        status="pass" if not missing else "fail",
        expected=f"{len(units)} units covered or controlled-only",
        actual=f"{len(cov.covered)} with a general season,"
        f" {len(controlled)} controlled-only, {len(missing)} unexplained",
        detail={"controlled_only": controlled, "missing": missing}
        if controlled or missing
        else None,
    )


@check()
def yoy_diff(c: CheckContext) -> CheckResult | None:
    """Warn on rows that need a year-over-year look — here, a season that starts in a
    calendar year other than the license year (§8.7 `yoy_diff` is a warning, never a block)."""
    windows = c.facts.first_windows
    if windows is None:
        return None
    other_year = [
        {"label": w.label, "title": w.title, "start": w.start.isoformat()}
        for w in windows
        if w.start.year != c.license_year
    ]
    return CheckResult(
        name="yoy_diff",
        status="warn" if other_year else "pass",
        expected=f"dates inside the {c.license_year} license year",
        actual=f"{len(other_year)} rows dated in another calendar year",
        detail=other_year or None,
    )


@check()
def corrections_applied(c: CheckContext) -> CheckResult | None:
    """Every item on the brochure's Corrections page is matched to the rows it changes.

    Matching the items themselves is T-22b's review gate; this records the pages that
    print one so no correction can pass unnoticed (§8.7).
    """
    pages = c.facts.corrections_pages
    if pages is None:
        return None
    found = list(pages)
    return CheckResult(
        name="corrections_applied",
        status="pass",
        expected="every item on the brochure's corrections page is matched",
        actual="no corrections page in this document"
        if not found
        else f"corrections on pages {found} need review",
        detail=found or None,
    )
