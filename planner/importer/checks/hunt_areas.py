"""Hunt-area checks, DESIGN.md §8.7 and §12 T-17b step 4.

Registered for every `hunt_areas_*` section, so T-23/25/26 inherit them with their
brochure's facts. The section's parser (`planner.importer.hunt_areas`) measures the
facts; these four invariants read them and never touch the PDF or the database.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from planner.importer.checks import AreaFacts, CheckContext, CheckResult, check

#: An area resolves to `full` coverage of a GMU at or above this fraction (§12 step 3).
FULL_FRAC = 0.98


def _areas(c: CheckContext) -> Sequence[AreaFacts] | None:
    facts = c.facts.hunt_area_coverage
    return None if facts is None else facts.areas


@check("hunt_areas_*")
def gis_area_match(c: CheckContext) -> CheckResult | None:
    """Every printed hunt area has a polygon in the committed GIS coverage file (§8.7).

    A species the file was never built for (black bear in the MVP build) is `skipped`,
    not failed: there is nothing to compare, and the area still resolves from its
    printed description. A species that *is* in the file must match exactly.
    """
    facts = c.facts.hunt_area_coverage
    areas = _areas(c)
    if facts is None or areas is None:
        return None
    if not facts.has_geometry:
        return CheckResult(
            name="gis_area_match",
            status="skipped",
            expected=f"GIS polygons for {facts.species_id} in {facts.coverage_file}",
            actual=f"the file has no {facts.species_id} polygons —"
            f" {len(areas)} areas resolved from their printed descriptions",
        )
    missing = [
        {"area": a.area_id, "page": a.page, "resolved_from": a.source}
        for a in areas
        if not a.has_polygon
    ]
    return CheckResult(
        name="gis_area_match",
        status="pass" if not missing else "fail",
        expected=f"{len(areas)} hunt areas have a {facts.license_year} polygon",
        actual=f"{len(areas) - len(missing)} matched",
        detail=missing or None,
    )


@check("hunt_areas_*")
def unit_coverage(c: CheckContext) -> CheckResult | None:
    """The units a description prints are the units its polygon covers (§12 step 4).

    Both lists are shown for every difference, with the printed page to turn to. Areas
    with no polygon are not compared — `gis_area_match` already reports those.
    """
    areas = _areas(c)
    if areas is None:
        return None
    compared = [a for a in areas if a.has_polygon and a.has_description]
    if not compared:
        return CheckResult(
            name="unit_coverage",
            status="skipped",
            expected="described units == the polygon's units",
            actual="no area has both a description and a polygon",
        )
    differences: list[dict[str, Any]] = [
        {
            "area": a.area_id,
            "page": a.page,
            "described": list(a.described_units),
            "gis": list(a.resolved_units),
            "only_described": sorted(set(a.described_units) - set(a.resolved_units)),
            "only_gis": sorted(set(a.resolved_units) - set(a.described_units)),
            "fractions": dict(a.fractions),
        }
        for a in compared
        if set(a.described_units) != set(a.resolved_units)
    ]
    return CheckResult(
        name="unit_coverage",
        status="pass" if not differences else "fail",
        expected=f"{len(compared)} areas: the described units are the polygon's units",
        actual=f"{len(compared) - len(differences)} agree",
        detail=differences or None,
    )


@check("hunt_areas_*")
def footnote_consistency(c: CheckContext) -> CheckResult | None:
    """The printed footnote agrees with the units the area resolved to (§12 step 4).

    * footnote `b` (*only a portion of this unit*) ⇒ a `partial` unit, **or** a
      description that says so in words. A land-status hunt ("Private land within Unit
      50") is printed with `b` while its polygon is the whole GMU, so the wording is
      evidence in its own right.
    * footnote `a` (*includes other units*) ⇒ two or more units, or units other than the
      one the area id starts with.
    * no footnote ⇒ exactly one unit, covered in full ("All of Unit X").
    """
    areas = _areas(c)
    if areas is None:
        return None
    judged = [a for a in areas if a.resolved_units]
    problems: list[dict[str, Any]] = []
    for area in judged:
        base = area.area_id.split("-")[0]
        reason = None
        if area.portion_only:
            if not area.partial_units and not area.portion_in_text:
                reason = "footnote b, but every unit is covered in full"
        elif area.includes_other_units:
            if len(area.resolved_units) < 2 and set(area.resolved_units) == {base}:
                reason = "footnote a, but the area resolves to its own unit alone"
        elif len(area.resolved_units) != 1 or area.partial_units:
            reason = "no footnote, but the area is not exactly one full unit"
        if reason:
            problems.append(
                {
                    "area": area.area_id,
                    "page": area.page,
                    "reason": reason,
                    "units": list(area.resolved_units),
                    "fractions": dict(area.fractions),
                    "footnotes": "".join(
                        m
                        for m, on in (("a", area.includes_other_units), ("b", area.portion_only))
                        if on
                    ),
                }
            )
    return CheckResult(
        name="footnote_consistency",
        status="pass" if not problems else "fail",
        expected=f"{len(judged)} areas agree with their printed footnote",
        actual=f"{len(judged) - len(problems)} agree",
        detail=problems or None,
    )


@check("hunt_areas_*")
def geometry_year(c: CheckContext) -> CheckResult | None:
    """Areas resolved from polygons older than the license year are flagged (§12 step 4).

    IDFG publishes the controlled-hunt polygons a year behind, so this `warn` is the
    normal state of a fresh brochure: a human waives it with a reason (§8.4) after
    confirming the boundaries did not change.
    """
    facts = c.facts.hunt_area_coverage
    areas = _areas(c)
    if facts is None or areas is None:
        return None
    stale = [
        a for a in areas if a.geometry_year is not None and a.geometry_year < facts.license_year
    ]
    with_geometry = [a for a in areas if a.geometry_year is not None]
    if not with_geometry:
        return CheckResult(
            name="geometry_year",
            status="skipped",
            expected=f"{facts.license_year} polygons",
            actual="no area resolved from a polygon",
        )
    if not stale:
        return CheckResult(
            name="geometry_year",
            status="pass",
            expected=f"{facts.license_year} polygons",
            actual=f"{len(with_geometry)} areas use {facts.license_year} geometry",
        )
    years = sorted({a.geometry_year for a in stale if a.geometry_year is not None})
    return CheckResult(
        name="geometry_year",
        status="warn",
        expected=f"{facts.license_year} polygons",
        actual=f"{len(stale)} of {len(with_geometry)} areas use"
        f" {', '.join(str(y) for y in years)} geometry",
        detail=[
            {"area": a.area_id, "geometry_year": a.geometry_year, "page": a.page} for a in stale
        ],
    )
