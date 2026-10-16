"""Year-over-year season diff (DESIGN.md §8.4, T-15a).

Pure functions over row dicts: no SQL, no writes, no parsing. `planner.api` reads the
document's own rows and the most recent earlier published license year, hands both to
`diff_rows`, and serves the result as `GET /api/documents/<id>/diff`.

A row matches its prior-year counterpart on identity — species, tag type, zone, hunt
number, weapon, sex and the **set** of units (order- and duplicate-independent). What
can change inside a match is dates, restrictions, coverage and notes; anything that
differs there makes the row `changed` and the prior values come back with it.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

# Identity: two rows describe the same season when all of these are equal.
MATCH_FIELDS: tuple[str, ...] = (
    "species_id",
    "tag_type_id",
    "zone",
    "hunt_number",
    "weapon",
    "sex",
)

# What a match is compared on. `units` is identity, but per-unit coverage is not.
COMPARE_FIELDS: tuple[str, ...] = ("start_date", "end_date", "restrictions", "coverage", "notes")

# Fields echoed for a prior-year row (so the UI can show old → new, and list `removed`).
ROW_FIELDS: tuple[str, ...] = (
    "id",
    "document_id",
    "license_year",
    "status",
    *MATCH_FIELDS,
    "hunt_area",
    "hunt_class",
    "start_date",
    "end_date",
    "restrictions",
    "notes",
    "units",
    "coverage",
    "source_page",
)


def unit_set(row: dict[str, Any]) -> tuple[str, ...]:
    """The row's units as a sorted, de-duplicated tuple — the order-independent set."""
    return tuple(sorted({str(u) for u in row.get("units") or ()}))


def match_key(row: dict[str, Any]) -> tuple[Any, ...]:
    """Identity key: the §6 match fields plus the unit set."""
    return (*(row.get(f) for f in MATCH_FIELDS), unit_set(row))


def _restrictions(row: dict[str, Any]) -> list[str]:
    """Restriction codes, compared as a set: `['nm','ar']` == `['ar','nm','ar']`."""
    return sorted({str(code) for code in row.get("restrictions") or ()})


def _coverage(row: dict[str, Any]) -> dict[str, Any]:
    """Per-unit coverage: `{unit: [coverage, coverage_note]}` for the row's units."""
    out: dict[str, Any] = {}
    for unit in row.get("coverage") or {}:
        value = (row["coverage"] or {})[unit]
        if isinstance(value, (list, tuple)):
            out[str(unit)] = [value[0], value[1] if len(value) > 1 else None]
        else:
            out[str(unit)] = [value, None]
    return out


def compare_values(row: dict[str, Any]) -> dict[str, Any]:
    """The comparable payload of a row — what decides `changed` vs. `same`."""
    return {
        "start_date": row.get("start_date"),
        "end_date": row.get("end_date"),
        "restrictions": _restrictions(row),
        "coverage": _coverage(row),
        "notes": row.get("notes"),
    }


def row_dict(row: dict[str, Any]) -> dict[str, Any]:
    """A row trimmed to the fields the diff returns, with normalized list/dict fields."""
    out = {f: row.get(f) for f in ROW_FIELDS}
    out["units"] = list(unit_set(row))
    out["restrictions"] = _restrictions(row)
    out["coverage"] = _coverage(row)
    return out


def _sort_key(row: dict[str, Any]) -> tuple[Any, ...]:
    """Deterministic order inside one match key (dates first, id as the tiebreaker)."""
    return (
        str(row.get("start_date") or ""),
        str(row.get("end_date") or ""),
        int(row.get("id") or 0),
    )


def _group(rows: Iterable[dict[str, Any]]) -> dict[tuple[Any, ...], list[dict[str, Any]]]:
    groups: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
    for row in rows:
        groups.setdefault(match_key(row), []).append(row)
    for group in groups.values():
        group.sort(key=_sort_key)
    return groups


def _changes(current: dict[str, Any], prior: dict[str, Any]) -> dict[str, Any]:
    """Field-by-field old → new for the fields that actually differ."""
    now, before = compare_values(current), compare_values(prior)
    return {f: {"from": before[f], "to": now[f]} for f in COMPARE_FIELDS if before[f] != now[f]}


def _pair(
    current: list[dict[str, Any]], prior: list[dict[str, Any]]
) -> tuple[list[tuple[dict[str, Any], dict[str, Any]]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Pair up rows sharing one identity key.

    One hunt number can print several season windows (T-17a), so a key is not unique.
    Identical rows pair first — an untouched window is never reported as changed just
    because a sibling window moved — then the leftovers pair in date order.
    """
    pairs: list[tuple[dict[str, Any], dict[str, Any]]] = []
    left, right = list(current), list(prior)

    for row in list(left):
        values = compare_values(row)
        for other in right:
            if compare_values(other) == values:
                pairs.append((row, other))
                left.remove(row)
                right.remove(other)
                break

    while left and right:
        pairs.append((left.pop(0), right.pop(0)))
    return pairs, left, right


def diff_rows(current: Iterable[dict[str, Any]], prior: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Tag every current row `new` / `changed` / `same` and list prior-only rows.

    Returns `{rows, removed, summary}`; `summary` counts always equal the row tallies.
    With no prior rows every row is `new` and `removed` is empty.
    """
    current_groups, prior_groups = _group(current), _group(prior)

    rows: list[dict[str, Any]] = []
    removed: list[dict[str, Any]] = []

    for key, group in current_groups.items():
        pairs, unmatched, leftover_prior = _pair(group, prior_groups.get(key, []))
        for row, prior_row in pairs:
            changes = _changes(row, prior_row)
            rows.append(
                {
                    "season_id": row.get("id"),
                    "status": "changed" if changes else "same",
                    "changes": changes,
                    "prior": row_dict(prior_row),
                    "row": row_dict(row),
                }
            )
        for row in unmatched:
            rows.append(
                {
                    "season_id": row.get("id"),
                    "status": "new",
                    "changes": {},
                    "prior": None,
                    "row": row_dict(row),
                }
            )
        removed.extend(leftover_prior)

    for key, group in prior_groups.items():
        if key not in current_groups:
            removed.extend(group)

    rows.sort(key=lambda r: int(r["season_id"] or 0))
    removed.sort(key=lambda r: int(r.get("id") or 0))

    summary = {
        "new": sum(1 for r in rows if r["status"] == "new"),
        "changed": sum(1 for r in rows if r["status"] == "changed"),
        "same": sum(1 for r in rows if r["status"] == "same"),
        "removed": len(removed),
    }
    return {"rows": rows, "removed": [row_dict(r) for r in removed], "summary": summary}
