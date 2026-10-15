"""Checks that only a controlled-hunt section can answer (DESIGN.md §8.7, §12 T-22a).

They are registered for every `controlled_*` section, so T-23/25/26 inherit them with
their brochure's facts and never re-implement them.
"""

from __future__ import annotations

from planner.importer.checks import CheckContext, CheckResult, check


@check("controlled_*")
def page_ref_resolves(c: CheckContext) -> CheckResult | None:
    """Every `(See pg N)` pointer resolves to the hunt area's description (§8.7).

    The printed page number is mapped to its physical page first — the brochure's own
    numbering never matches the PDF index.
    """
    total = c.facts.page_refs
    if total is None:
        return None
    unresolved = list(c.facts.page_refs_unresolved)
    return CheckResult(
        name="page_ref_resolves",
        status="pass" if not unresolved else "fail",
        expected=f"{total} '(See pg N)' pointers resolve to the area's description",
        actual=f"{total - len(unresolved)} resolve",
        detail=unresolved or None,
    )


@check("controlled_*")
def footnote_legend(c: CheckContext) -> CheckResult | None:
    """Every footnote marker printed on a row has a legend entry, and every table title
    maps to a tag/weapon/hunt class (§8.7: an unexplained marker is never dropped, D3)."""
    marks = c.facts.unknown_marks
    if marks is None:
        return None
    unknown = list(marks)
    titles = list(c.facts.unreadable_titles)
    return CheckResult(
        name="footnote_legend",
        status="pass" if not unknown and not titles else "fail",
        expected="every footnote marker has a legend entry and every title maps",
        actual=f"{len(unknown)} rows with an unknown marker, {len(titles)} unreadable titles",
        detail={"markers": unknown, "titles": titles} if unknown or titles else None,
    )


@check("controlled_*")
def hunt_areas_written(c: CheckContext) -> CheckResult | None:
    """What the section wrote to `hunt_areas`, and how many notes it attached."""
    written = c.facts.hunt_areas
    if written is None:
        return None
    return CheckResult(
        name="hunt_areas_written",
        status="pass",
        expected=f"{written.get('distinct', 0)} distinct hunt areas",
        actual=f"{written.get('areas', 0)} rows in hunt_areas,"
        f" {written.get('attached', 0)} seasons with an attached note",
    )
