"""Availability engine, DESIGN.md §7 (T-06).

Pure functions over plain rows (mappings): no Flask, no SQL, so every rule is unit-testable.
`planner/api.py::availability` loads the rows and calls :func:`evaluate`.

Rows are mappings with the DB column names (§5):

* season: ``id, document_id, license_year`` (from its source document), ``species_id,
  tag_type_id, zone, hunt_number, hunt_class, weapon, sex, start_date, end_date,
  restrictions`` (JSON text or list), ``source_page``, and optionally ``status``
* season_unit: ``season_id, unit_id, coverage, coverage_note, coverage_frac``
* my_tag: ``id, license_year, tag_type_id, zone, hunt_number, filled``
* permit: a ``(license_year, permit)`` pair, or a mapping with those keys
* profile: ``{youth: 0|1}``
"""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from datetime import date
from typing import Any, Final, Literal, TypedDict

from planner.dates import days, parse_iso, to_iso

Severity = Literal["narrowing", "informational"]


class Restriction(TypedDict):
    label: str
    severity: Severity


# §5.1 closed vocabulary. `narrowing` codes make a unit *restricted* (yellow);
# `informational` codes show as badges but don't change the color. Mirrored to the
# frontend via `/api/meta`. Seasons carrying `youth_only` are hidden for adult profiles
# (§7.2 step 4). For a youth profile the code still narrows (yellow, §4.4).
# Animal-limit codes (antlered/antlerless/two-point/spike/brow-tined) limit which animal you
# may take, not where or when you may hunt, so they're informational (badge only).
RESTRICTIONS: Final[dict[str, Restriction]] = {
    "whitetail_only": {"label": "White-tailed deer only", "severity": "narrowing"},
    "mule_deer_only": {"label": "Mule deer only", "severity": "narrowing"},
    "antlered_only": {"label": "Antlered only", "severity": "informational"},
    "antlerless_only": {"label": "Antlerless only", "severity": "informational"},
    "two_point_only": {"label": "Two-point or less only", "severity": "informational"},
    "spike_only": {"label": "Spike bull only", "severity": "informational"},
    "brow_tined": {"label": "Brow-tined bull only", "severity": "informational"},
    "youth_only": {"label": "Youth only", "severity": "narrowing"},
    "private_land_only": {"label": "Private land only", "severity": "narrowing"},
    "portion_of_unit": {"label": "Portion of unit only", "severity": "narrowing"},
    "outfitter_only": {"label": "Outfitted hunters only", "severity": "narrowing"},
    "cwd_testing": {"label": "CWD testing required", "severity": "informational"},
    "motorized_rule": {"label": "Motorized hunting rule", "severity": "informational"},
    "second_tag_prohibited": {"label": "Second tag prohibited", "severity": "informational"},
    # §12 T-13 — the bear/lion/wolf tables' printed method rules. They say how you may
    # hunt (bait, dogs, calls, expanded methods) or warn about grizzlies; none of them
    # changes where or when the season is open, so all are informational (§5.1).
    "second_tag_allowed": {"label": "Second tag may be used", "severity": "informational"},
    "bait_prohibited": {"label": "Bait prohibited", "severity": "informational"},
    "dogs_prohibited": {"label": "Dogs prohibited", "severity": "informational"},
    "electronic_calls": {"label": "Electronic calls may be used", "severity": "informational"},
    "grizzly_caution": {"label": "Grizzly bears may be encountered", "severity": "informational"},
    "expanded_methods": {
        "label": "Expanded hunting methods allowed",
        "severity": "informational",
    },
    "general_methods": {"label": "General hunting rules apply", "severity": "informational"},
    # §12 T-14 — pheasant on an Upland Game Bird Permit Release Area (a WMA) needs
    # the `wmb` permit on top of the hunting licence; the season itself is unchanged.
    "wmb_permit_on_wma": {
        "label": "Upland Game Bird Permit required on WMAs",
        "severity": "informational",
    },
    "see_notes": {"label": "See notes", "severity": "narrowing"},
}

# Reasons the engine derives from the query (never printed in a PDF). All of them narrow.
# `needs_*_permit` appears only on `untagged` seasons (include_untagged): the user holds the
# tag, and the season would count if they also held the permit.
DERIVED_REASONS: Final[dict[str, str]] = {
    "partial_dates": "Season doesn't cover the whole date range",
    "no_tag_next_year": "No tag for the next license year (Dec 31 → Jan 1)",
    "no_tag_prev_year": "No tag for the earlier license year",
    "needs_archery_permit": "Needs an Archery Permit",
    "needs_muzzleloader_permit": "Needs a Muzzleloader Permit",
    "needs_falconry_permit": "Needs a Falconry Permit",
}

WEAPONS: Final[tuple[str, ...]] = ("all", "rifle", "archery", "muzzleloader", "short_range")

# season.weapon → (query weapons that may see it, permit it requires), §7.2 step 4.
# §7.2 doesn't cover `shotgun`/`falconry`. They show only for weapon=all (D3: don't assume a
# rifle or bow hunter may use them), and falconry also needs the falconry permit.
_WEAPON_RULES: Final[dict[str, tuple[frozenset[str], str | None]]] = {
    "any": (frozenset(WEAPONS), None),
    "archery": (frozenset({"all", "archery"}), "archery"),
    "muzzleloader": (frozenset({"all", "muzzleloader"}), "muzzleloader"),
    "short_range": (frozenset({"all", "short_range"}), None),
    "shotgun": (frozenset({"all"}), None),
    "falconry": (frozenset({"all"}), "falconry"),
}

# tag_type → (requires_zone, requires_hunt_no). Mirrors seed.TAG_TYPES; the API passes the
# DB's tag_types instead.
_DEFAULT_TAG_RULES: Final[dict[str, tuple[bool, bool]]] = {
    "elk_zone_a": (True, False),
    "elk_zone_b": (True, False),
    "sandhill_crane": (True, False),
    "controlled": (False, True),
}

PARTIAL_COVERAGE_FRAC: Final = 0.98  # §7.2: a controlled hunt below this is `portion_of_unit`

_REASON_ORDER: Final[dict[str, int]] = {
    code: i for i, code in enumerate([*RESTRICTIONS, *DERIVED_REASONS])
}
_STATUS_RANK: Final[dict[str, int]] = {"open": 3, "restricted": 2, "untagged": 1, "closed": 0}


def is_narrowing(code: str) -> bool:
    """Narrowing codes change the color. Unknown codes narrow too (D3: never guess green)."""
    r = RESTRICTIONS.get(code)
    return r is None or r["severity"] == "narrowing"


def sort_reasons(codes: Iterable[str]) -> list[str]:
    """Deduped, in vocabulary order: §5.1 codes, then derived codes, then unknown ones."""
    return sorted(set(codes), key=lambda c: (_REASON_ORDER.get(c, len(_REASON_ORDER)), c))


def _as_date(v: Any) -> date:
    return v if isinstance(v, date) else parse_iso(v)


def _restrictions(season: Mapping[str, Any]) -> list[str]:
    raw = season.get("restrictions") or []
    codes = json.loads(raw) if isinstance(raw, str) else list(raw)
    if season.get("hunt_class") == "outfitter":
        codes.append("outfitter_only")  # §7.2: outfitter hunts always carry it
    if season.get("hunt_class") == "youth":
        codes.append("youth_only")
    return list(dict.fromkeys(codes))


def _year_spans(a: date, b: date) -> dict[int, tuple[date, date]]:
    """The inclusive range [a, b] split by calendar (= license) year."""
    return {y: (max(a, date(y, 1, 1)), min(b, date(y, 12, 31))) for y in range(a.year, b.year + 1)}


def _permit_set(permits: Iterable[Any]) -> set[tuple[int, str]]:
    out: set[tuple[int, str]] = set()
    for p in permits:
        if isinstance(p, Mapping):
            out.add((int(p["license_year"]), str(p["permit"])))
        else:
            year, name = p
            out.add((int(year), str(name)))
    return out


def _tag_rules(tag_types: Iterable[Mapping[str, Any]] | None) -> dict[str, tuple[bool, bool]]:
    if tag_types is None:
        return dict(_DEFAULT_TAG_RULES)
    return {t["id"]: (bool(t["requires_zone"]), bool(t["requires_hunt_no"])) for t in tag_types}


def _tag_matches(
    tag: Mapping[str, Any], season: Mapping[str, Any], rules: Mapping[str, tuple[bool, bool]]
) -> bool:
    """§7.2 tag_ok, minus the license-year check (the caller does that per day-year)."""
    if tag["tag_type_id"] != season["tag_type_id"]:
        return False
    needs_zone, needs_hunt = rules.get(season["tag_type_id"], (False, False))
    if needs_zone and (tag.get("zone") or None) != (season.get("zone") or None):
        return False
    if needs_hunt:
        if (tag.get("hunt_number") or None) != (season.get("hunt_number") or None):
            return False
        # Hunt numbers are reused across years: a 2025 #1001 never matches 2026's #1001.
        if int(tag["license_year"]) != int(season["license_year"]):
            return False
        # §7.2 step 2 ("controlled tags whose hunt's species matches") needs no check here:
        # a controlled tag has no species of its own, and `evaluate` only ever offers it
        # seasons of the queried species, so the hunt it matches *is* that species' hunt.
    return True


def _is_partial(su: Mapping[str, Any]) -> bool:
    frac = su.get("coverage_frac")
    return su.get("coverage") == "partial" or (frac is not None and frac < PARTIAL_COVERAGE_FRAC)


def merge_spans(spans: Iterable[tuple[date, date]]) -> list[tuple[date, date]]:
    """The union of inclusive date spans, sorted and non-overlapping.

    Overlapping and touching spans merge; spans a day apart stay separate, which changes
    no day count (:func:`days` is inclusive on both ends).
    """
    out: list[tuple[date, date]] = []
    for a, b in sorted(spans):
        if out and a <= out[-1][1]:
            if b > out[-1][1]:
                out[-1] = (out[-1][0], b)
        else:
            out.append((a, b))
    return out


def intersect_spans(
    a: Iterable[tuple[date, date]], b: Iterable[tuple[date, date]]
) -> list[tuple[date, date]]:
    """The inclusive spans covered by **both** span lists (each merged first).

    This is what `species_mode=all` counts: the days on which every selected species is
    open. Intersecting the spans is exact, where `min()` of the per-species day counts is
    not — two species open for one day each may be open on *different* days (§12 T-27).
    """
    out: list[tuple[date, date]] = []
    xs, ys = merge_spans(a), merge_spans(b)
    i = j = 0
    while i < len(xs) and j < len(ys):
        lo, hi = max(xs[i][0], ys[j][0]), min(xs[i][1], ys[j][1])
        if lo <= hi:
            out.append((lo, hi))
        if xs[i][1] < ys[j][1]:
            i += 1
        else:
            j += 1
    return out


def union_days(spans: Iterable[tuple[date, date]]) -> int:
    """Days covered by the union of inclusive date spans."""
    return sum(days(*s) for s in merge_spans(spans))


def evaluate(
    seasons: Iterable[Mapping[str, Any]],
    season_units: Iterable[Mapping[str, Any]],
    my_tags: Iterable[Mapping[str, Any]],
    permits: Iterable[Any],
    profile: Mapping[str, Any] | None,
    species: str,
    start: date | str,
    end: date | str,
    weapon: str = "all",
    include_untagged: bool = False,
    *,
    tag_types: Iterable[Mapping[str, Any]] | None = None,
    unit_ids: Sequence[str] | None = None,
    with_spans: bool = False,
) -> dict[str, Any]:
    """Unit statuses for one query (§7.2), in the §6 `/api/availability` shape.

    ``my_tags`` is the tag scope (all the user's tags, or the explicit ids). Filled tags are
    dropped here. ``unit_ids`` lists every unit to report (units with no season are
    ``closed``); without it, only units with at least one kept season appear.

    ``with_spans`` adds each unit's merged open-day spans as ``open_spans`` (a list of
    ``[start_iso, end_iso]`` pairs). Only :func:`evaluate_multi` asks for them — it needs
    the day *sets*, not their counts, to intersect them — and it strips them again, so the
    §6 payload is unchanged by default.
    """
    start_d, end_d = _as_date(start), _as_date(end)
    if end_d < start_d:
        raise ValueError(f"end {end_d} is before start {start_d}")
    if weapon not in WEAPONS:
        raise ValueError(f"unknown weapon {weapon!r} (expected one of {WEAPONS})")
    youth = bool((profile or {}).get("youth"))
    held_permits = _permit_set(permits)
    rules = _tag_rules(tag_types)
    range_years = set(_year_spans(start_d, end_d))
    # Candidate tags by (tag type, hunt number); the hunt number is None unless the type needs it.
    tags_by_key: dict[tuple[str, Any], list[Mapping[str, Any]]] = defaultdict(list)
    for t in my_tags:
        if not t.get("filled") and int(t["license_year"]) in range_years:
            needs_hunt = rules.get(t["tag_type_id"], (False, False))[1]
            key = (t["tag_type_id"], (t.get("hunt_number") or None) if needs_hunt else None)
            tags_by_key[key].append(t)

    units_by_season: dict[Any, list[Mapping[str, Any]]] = defaultdict(list)
    for su in season_units:
        units_by_season[su["season_id"]].append(su)

    per_unit: dict[str, list[dict[str, Any]]] = defaultdict(list)
    spans_by_unit: dict[str, list[tuple[date, date]]] = defaultdict(list)

    for s in seasons:
        if s.get("status", "published") != "published" or s["species_id"] != species:
            continue
        s_start, s_end = _as_date(s["start_date"]), _as_date(s["end_date"])
        o_start, o_end = max(s_start, start_d), min(s_end, end_d)
        if o_end < o_start:
            continue  # no overlap with the range

        allowed, permit = _WEAPON_RULES.get(s["weapon"], (frozenset({"all"}), None))
        if weapon not in allowed:
            continue  # the weapon filter is a hard exclusion, even with include_untagged
        codes = _restrictions(s)
        if "youth_only" in codes and not youth:
            continue  # §7.2: youth-only seasons are hidden for adults

        # §7.4: each overlapping day counts only if a tag (and permit) of that day's year holds.
        tt = s["tag_type_id"]
        key = (tt, (s.get("hunt_number") or None) if rules.get(tt, (False, False))[1] else None)
        matching = [t for t in tags_by_key.get(key, ()) if _tag_matches(t, s, rules)]
        year_spans = _year_spans(o_start, o_end)
        covered: list[int] = []
        unlocking: list[Mapping[str, Any]] = []
        missing_permit = False
        for y in year_spans:
            year_tags = [t for t in matching if int(t["license_year"]) == y]
            if not year_tags:
                continue
            if permit and (y, permit) not in held_permits:
                missing_permit = True
                continue
            covered.append(y)
            unlocking.extend(year_tags)
        tag_ok = bool(covered)
        # Outfitter hunts stay hidden unless the exact hunt number is held (§7.2).
        if not tag_ok and (not include_untagged or "outfitter_only" in codes):
            continue

        reasons = [c for c in codes if is_narrowing(c)]
        if "whitetail_only" in reasons and tag_ok:
            if any(t["tag_type_id"] == "deer_whitetail" for t in unlocking):
                reasons.remove("whitetail_only")  # the tag is already WT-only: not narrowing
        if s_start > start_d or s_end < end_d:
            reasons.append("partial_dates")
        if tag_ok:
            first = min(covered)
            reasons += [
                "no_tag_next_year" if y > first else "no_tag_prev_year"
                for y in year_spans
                if y not in covered
            ]
            spans = [year_spans[y] for y in covered]
        else:
            spans = []
            if missing_permit:
                reasons.append(f"needs_{permit}_permit")

        base = {
            "id": s["id"],
            "status": "untagged",
            "reasons": [],
            "tag_ids": sorted({t["id"] for t in unlocking}),
            "tag_type": s["tag_type_id"],
            "zone": s.get("zone"),
            "hunt_number": s.get("hunt_number"),
            "hunt_class": s.get("hunt_class"),
            "weapon": s["weapon"],
            "sex": s.get("sex"),
            "start": to_iso(s_start),
            "end": to_iso(s_end),
            "restrictions": codes,
            "coverage": "full",
            "coverage_note": None,
            "source_page": s.get("source_page"),
            "document_id": s.get("document_id"),
            "license_year": s.get("license_year"),
        }
        # Only coverage varies by unit: build the full/partial variants once per season.
        variants: dict[bool, dict[str, Any]] = {}
        for partial in (False, True):
            r = sort_reasons([*reasons, "portion_of_unit"] if partial else reasons)
            status = ("restricted" if r else "open") if tag_ok else "untagged"
            variants[partial] = {
                **base,
                "status": status,
                "reasons": r,
                "coverage": "partial" if partial else "full",
            }

        for su in units_by_season.get(s["id"], ()):
            entry = variants[_is_partial(su)]
            note = su.get("coverage_note")
            per_unit[su["unit_id"]].append({**entry, "coverage_note": note} if note else entry)
            if spans:
                spans_by_unit[su["unit_id"]].extend(spans)

    report_ids = list(unit_ids) if unit_ids is not None else []
    known = set(report_ids)
    report_ids += sorted(u for u in per_unit if u not in known)

    units: dict[str, dict[str, Any]] = {}
    summary = {"open": 0, "restricted": 0, "untagged": 0, "closed": 0}
    for uid in report_ids:
        entries = sorted(per_unit.get(uid, []), key=lambda e: (e["start"], str(e["id"])))
        status = max((e["status"] for e in entries), key=_STATUS_RANK.__getitem__, default="closed")
        # Reasons: the deduped union across the seasons that reach the unit's best status.
        reasons = sort_reasons(c for e in entries if e["status"] == status for c in e["reasons"])
        spans = merge_spans(spans_by_unit.get(uid, []))
        units[uid] = {
            "status": status,
            "reasons": reasons,
            "days_open": sum(days(*s) for s in spans),
            "seasons": entries,
        }
        if with_spans:
            units[uid]["open_spans"] = [[to_iso(a), to_iso(b)] for a, b in spans]
        summary[status] += 1

    return {
        "range": {"start": to_iso(start_d), "end": to_iso(end_d), "days": days(start_d, end_d)},
        "species": species,
        "weapon": weapon,
        "include_untagged": bool(include_untagged),
        "units": units,
        "summary": summary,
    }


# ───────── multi-species intersection (§12 T-27) ─────────
#: How many species one query may select. The UI's chip row caps at the same number.
MAX_SPECIES: Final = 4
SPECIES_MODES: Final[tuple[str, ...]] = ("any", "all")


def default_species_mode(species: Sequence[str]) -> str:
    """The mode a selection uses when the caller names none.

    A multi-species selection means "open to **every** one of them" (`all`); one species
    behaves the same in either mode, so it stays on the historical `any`.
    """
    return "all" if len(species) > 1 else "any"


def combine_status(statuses: Iterable[str], mode: str) -> str:
    """Combine per-species statuses on one unit (§12 T-27).

    Rank `closed < untagged < restricted < open`. `all` takes the **minimum** — the unit is
    only as good as the worst selected species — and `any` the maximum, which is exactly
    what a single species already does, so one species behaves identically in both modes.
    """
    ranked = list(statuses)
    if not ranked:
        return "closed"
    pick = min if mode == "all" else max
    return pick(ranked, key=_STATUS_RANK.__getitem__)


def evaluate_multi(
    seasons: Iterable[Mapping[str, Any]],
    season_units: Iterable[Mapping[str, Any]],
    my_tags: Iterable[Mapping[str, Any]],
    permits: Iterable[Any],
    profile: Mapping[str, Any] | None,
    species: Sequence[str],
    start: date | str,
    end: date | str,
    weapon: str = "all",
    include_untagged: bool = False,
    mode: str = "any",
    *,
    tag_types: Iterable[Mapping[str, Any]] | None = None,
    unit_ids: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Availability for several species at once, combined per unit (§12 T-27).

    :func:`evaluate` stays single-species and pure; this runs it once per species over the
    **rows already loaded** (the caller issues one SQL query per table with
    ``species_id IN (…)``, never one query per species) and combines the results.

    With one species the payload is T-06's, plus ``species`` as a one-element list and the
    ``species_mode`` echo. With more, every unit gains ``per_species`` and every season
    entry a ``species`` id, so the map tooltip can group and attribute them.
    """
    ids = list(dict.fromkeys(species))
    if not ids:
        raise ValueError("species must name at least one species")
    if len(ids) > MAX_SPECIES:
        raise ValueError(f"at most {MAX_SPECIES} species (got {len(ids)})")
    if mode not in SPECIES_MODES:
        raise ValueError(f"unknown species_mode {mode!r} (expected one of {SPECIES_MODES})")

    rows = list(seasons)
    sus = list(season_units)
    tags = list(my_tags)
    held = list(permits)
    types = None if tag_types is None else list(tag_types)
    per = {
        sp: evaluate(
            rows,
            sus,
            tags,
            held,
            profile,
            sp,
            start,
            end,
            weapon,
            include_untagged,
            tag_types=types,
            unit_ids=unit_ids,
            with_spans=True,
        )
        for sp in ids
    }

    first = per[ids[0]]
    if len(ids) == 1:
        for u in first["units"].values():
            u.pop("open_spans", None)
        return {**first, "species": ids, "species_mode": mode}

    # Units in the first species' order (every call saw the same `unit_ids`), then any
    # unit only a later species reached (possible when `unit_ids` is None).
    uids = list(first["units"])
    seen = set(uids)
    for sp in ids[1:]:
        uids += [u for u in per[sp]["units"] if u not in seen and not seen.add(u)]

    units: dict[str, dict[str, Any]] = {}
    summary = {"open": 0, "restricted": 0, "untagged": 0, "closed": 0}
    closed: dict[str, Any] = {"status": "closed", "reasons": [], "days_open": 0, "seasons": []}
    for uid in uids:
        mine = {sp: per[sp]["units"].get(uid, closed) for sp in ids}
        status = combine_status((u["status"] for u in mine.values()), mode)
        # Each code is prefixed with the species it came from (`elk:partial_dates`) so the
        # tooltip can attribute it; the unprefixed codes stay inside `per_species`.
        reasons = [f"{sp}:{c}" for sp in ids for c in mine[sp]["reasons"]]

        spans = [[tuple(map(_as_date, s)) for s in u.get("open_spans", [])] for u in mine.values()]
        if mode == "all":
            # The days on which *every* selected species is open — the intersection of the
            # per-species open-day sets, never `min()` of their counts (§12 T-27).
            combined = spans[0]
            for other in spans[1:]:
                combined = intersect_spans(combined, other)
        else:
            combined = merge_spans([s for group in spans for s in group])

        units[uid] = {
            "status": status,
            "reasons": reasons,
            "days_open": sum(days(*s) for s in combined),
            "seasons": sorted(
                ({**e, "species": sp} for sp in ids for e in mine[sp]["seasons"]),
                key=lambda e: (e["start"], e["species"], str(e["id"])),
            ),
            "per_species": {
                sp: {
                    "status": u["status"],
                    "reasons": u["reasons"],
                    "days_open": u["days_open"],
                }
                for sp, u in mine.items()
            },
        }
        summary[status] += 1

    return {
        "range": first["range"],
        "species": ids,
        "species_mode": mode,
        "weapon": first["weapon"],
        "include_untagged": first["include_untagged"],
        "units": units,
        "summary": summary,
    }
