"""Elk A/B zone invariants, DESIGN.md §8.7 (§12 T-12).

The zone pages carry three facts nothing else in the brochure does, so they get three
checks of their own. Each reads a field the elk parser measured and nothing else; a
section that measures none of them records none of these rows.

* `zone_unit_match` — the unit list printed in a zone header is the regulation. It must
  equal the GIS `Elk_Zone` membership in `zone_units`; a difference is a **fail** that
  names both lists (T-12's own acceptance criterion).
* `zone_tag_coverage` — every zone offers an A and a B tag unless the page says
  otherwise (`No B Tags in this Zone`, `Controlled Hunts Only`), and a tag that is
  offered has at least one season.
* `month_column_agreement` — an entry's dates must fall in the month columns its box
  spans. The columns are a cross-check only: entries are centred across two of them, so
  they can never be used as cell boundaries (that is what loses a season).
* `tag_quota_accounting` — every printed `… B Tag Quota Available` number was read.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from planner.importer.checks import CheckContext, CheckResult, check

if TYPE_CHECKING:  # pragma: no cover - typing only
    from planner.importer.elk import ZoneFacts


@check("elk_zones")
def zone_unit_match(c: CheckContext) -> CheckResult | None:
    """The printed zone → unit list equals GIS `Elk_Zone` membership (§12 T-12)."""
    zones: list[ZoneFacts] = list(c.facts.extra.get("zones") or ())
    if not zones:
        return None
    bad = [
        {
            "zone": z.zone,
            "page": z.page,
            "printed": list(z.printed),
            "gis": list(z.gis),
            "unknown_tokens": list(z.unknown),
        }
        for z in zones
        if not z.agrees
    ]
    corrections = list(c.facts.extra.get("zone_unit_corrections") or ())
    return CheckResult(
        name="zone_unit_match",
        status="pass" if not bad else "fail",
        expected=f"{len(zones)} zone headers match `zone_units`",
        actual=f"{len(zones) - len(bad)} agree, {len(bad)} differ"
        + (f", {len(corrections)} written to `zone_units`" if corrections else ""),
        detail={"differences": bad, "applied": corrections} if bad else None,
    )


@check("elk_zones")
def zone_tag_coverage(c: CheckContext) -> CheckResult | None:
    """Every zone has seasons for both tags, unless the page says it offers only one."""
    zones: list[ZoneFacts] = list(c.facts.extra.get("zones") or ())
    if not zones:
        return None
    missing: list[dict[str, Any]] = []
    for z in zones:
        gaps = [tag for tag in z.offered if tag not in z.tags]
        if gaps:
            missing.append(
                {
                    "zone": z.zone,
                    "page": z.page,
                    "missing": gaps,
                    "parsed": list(z.tags),
                    "seasons": z.seasons,
                }
            )
    offered = sum(len(z.offered) for z in zones)
    return CheckResult(
        name="zone_tag_coverage",
        status="pass" if not missing else "fail",
        expected=f"{offered} zone/tag combinations the brochure offers",
        actual=f"{offered - sum(len(m['missing']) for m in missing)} with seasons,"
        f" {len(missing)} zones short",
        detail=missing or None,
    )


@check("elk_zones")
def month_column_agreement(c: CheckContext) -> CheckResult | None:
    """A season's dates fall in the month columns its printed entry spans (§12 T-12a)."""
    mismatches = c.facts.extra.get("month_mismatches")
    if mismatches is None:
        return None
    found = list(mismatches)
    return CheckResult(
        name="month_column_agreement",
        status="pass" if not found else "fail",
        expected="every season's months are printed in the columns its entry spans",
        actual=f"{len(found)} seasons fall outside their month columns",
        detail=found or None,
    )


@check("elk_zones")
def tag_quota_accounting(c: CheckContext) -> CheckResult | None:
    """Every printed first-come-first-served tag quota was read onto a tag that exists."""
    quotas = c.facts.extra.get("tag_quotas")
    if quotas is None:
        return None
    found = list(quotas)
    if not found:
        return None
    orphans = [q for q in found if not q.get("offered")]
    return CheckResult(
        name="tag_quota_accounting",
        status="pass" if not orphans else "fail",
        expected=f"{len(found)} printed tag quotas belong to a tag the zone offers",
        actual=f"{len(found) - len(orphans)} attached, {len(orphans)} orphaned",
        detail=orphans or None,
    )
