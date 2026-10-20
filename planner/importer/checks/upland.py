"""The upland game bird invariants, DESIGN.md §8.7 (§12 T-14).

Four things can go wrong in a prose section that no shared check can see:

* a **county name the parser dropped** — both extraction paths read the same printed
  text, so they agree on the smaller area and `two_path_agreement` stays green;
* an **area that resolved to no unit at all**, which looks like a quiet "no season";
* the **nonresident opener** the brochure prints beside the resident one, which D1
  drops — a dropped date still has to be accounted for (§8.7 principle 4);
* a section whose seasons the brochure **does not print at all** (sage-grouse is set
  in July, in a separate brochure), which must be stated, not inferred from silence.
"""

from __future__ import annotations

from typing import Any

from planner.importer.checks import CheckContext, CheckResult, check


@check("upland_*")
def county_tokens_resolve(c: CheckContext) -> CheckResult | None:
    """Every county an area definition prints resolved to a county of the overlay."""
    residue: list[dict[str, Any]] = list(c.facts.extra.get("county_residue") or ())
    areas = list(c.facts.extra.get("areas") or ())
    if not areas:
        return None
    counties = sum(len(a.get("counties") or ()) for a in areas)
    return CheckResult(
        name="county_tokens_resolve",
        status="pass" if not residue else "fail",
        expected=f"{counties} county names resolve, nothing left over",
        actual=f"{counties} resolved, {len(residue)} unresolved",
        detail=residue or None,
    )


@check("upland_*")
def area_units_resolved(c: CheckContext) -> CheckResult | None:
    """Every printed area resolved to at least one unit, unless it is printed CLOSED."""
    areas = list(c.facts.extra.get("areas") or ())
    if not areas:
        return None
    empty = [a for a in areas if not a.get("closed") and not a.get("units")]
    return CheckResult(
        name="area_units_resolved",
        status="pass" if not empty else "fail",
        expected=f"{len(areas)} areas resolve to units",
        actual=f"{len(areas) - len(empty)} resolved, {len(empty)} empty",
        detail=empty or None,
    )


@check("upland_*")
def resident_only_windows(c: CheckContext) -> CheckResult | None:
    """Each nonresident opener D1 dropped has the resident window it was printed with."""
    dropped: list[dict[str, Any]] = list(c.facts.extra.get("nonresident_windows") or ())
    if not dropped:
        return None
    staged = {(a, year, hunt_class) for a, year, hunt_class in c.facts.extra.get("windows") or ()}
    orphans = [
        d for d in dropped if (d.get("area"), d.get("year"), d.get("hunt_class")) not in staged
    ]
    return CheckResult(
        name="resident_only_windows",
        status="pass" if not orphans else "fail",
        expected=f"{len(dropped)} nonresident windows each have a resident window (D1)",
        actual=f"{len(dropped) - len(orphans)} paired, {len(orphans)} orphaned",
        detail={"dropped": dropped, "orphans": orphans} if dropped else None,
    )


@check("upland_*")
def deferred_season(c: CheckContext) -> CheckResult | None:
    """A section the brochure deliberately prints no season for says so (D3)."""
    info = c.facts.extra.get("deferred_season")
    if not info:
        return None
    rows = int(info.get("rows") or 0)
    # The brochure prints no season, so staging one would be an invention; staging
    # none is correct and must not block publishing — hence `skipped`, not `warn`
    # (§12 T-22b: `warn` blocks until a human waives it).
    return CheckResult(
        name="deferred_season",
        status="skipped" if rows == 0 else "fail",
        expected="no seasons: " + str(info.get("reason")),
        actual=f"{rows} rows staged from pages {info.get('pages')}",
        detail=info,
    )
