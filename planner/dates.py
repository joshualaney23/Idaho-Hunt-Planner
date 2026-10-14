"""Weekend default and date-overlap math, DESIGN.md §7.3 (T-05).

Twin of ``static/js/dates.js``. Both are tested against the same fixture,
``tests/fixtures/dates_cases.json``, so any change here must be made there too.

All ranges are inclusive ``(start, end)`` pairs of :class:`datetime.date`, the same
convention as the database (§5: ISO dates, inclusive).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import date, timedelta
from typing import Any

WEEKEND_DEFS = ("fri_sun", "sat_sun")
DEFAULT_WEEKEND_DEF = "fri_sun"  # D5

DateRange = tuple[date, date]

_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
_FIRST_DAY = {"fri_sun": 4, "sat_sun": 5}  # date.weekday(): Mon=0 … Sun=6
_SUN = 6


def parse_iso(s: str) -> date:
    """Parse ``YYYY-MM-DD`` into a date. Raises ValueError on anything else."""
    if not isinstance(s, str) or len(s) != 10:
        raise ValueError(f"not an ISO date: {s!r}")
    return date.fromisoformat(s)


def to_iso(d: date) -> str:
    return d.isoformat()


def fmt_short(d: date) -> str:
    """``Oct 17``. Locale-independent, matching ``fmtShort`` in dates.js."""
    return f"{_MONTHS[d.month - 1]} {d.day}"


def add_days(d: date, n: int) -> date:
    return d + timedelta(days=n)


def days(a: date, b: date) -> int:
    """Number of days in the inclusive range [a, b]; 0 if b is before a."""
    return max(0, (b - a).days + 1)


def overlap(a1: date, a2: date, b1: date, b2: date) -> int:
    """Number of days shared by the inclusive ranges [a1, a2] and [b1, b2]."""
    return days(max(a1, b1), min(a2, b2))


def default_range(today: date, weekend_def: str = DEFAULT_WEEKEND_DEF) -> DateRange:
    """The coming weekend per §7.3.

    ``fri_sun``: Fri/Sat/Sun → [today, Sunday]; otherwise next Fri–Sun.
    ``sat_sun``: Sat → [today, Sunday]; Sun → [today, today]; otherwise next Sat–Sun.
    """
    if weekend_def not in _FIRST_DAY:
        raise ValueError(f"unknown weekend_def: {weekend_def!r} (expected one of {WEEKEND_DEFS})")
    first = _FIRST_DAY[weekend_def]
    wd = today.weekday()
    sunday = add_days(today, _SUN - wd)
    if wd >= first:
        return today, sunday
    return add_days(today, first - wd), sunday


# --- presets (§7.3) -------------------------------------------------------------------


def this_weekend(today: date, weekend_def: str = DEFAULT_WEEKEND_DEF) -> DateRange:
    return default_range(today, weekend_def)


def next_weekend(today: date, weekend_def: str = DEFAULT_WEEKEND_DEF) -> DateRange:
    """The full weekend after this one (this weekend's Sunday + 7 days).

    Equal to "this weekend + 7 days" except when today is already inside the weekend:
    on a Saturday with ``fri_sun`` it returns next Fri–Sun, not just next Sat–Sun.
    """
    _, sunday = default_range(today, weekend_def)
    return default_range(add_days(sunday, 1), weekend_def)


def today_range(today: date) -> DateRange:
    return today, today


def next_7(today: date) -> DateRange:
    """Today plus the following six days (7 days inclusive)."""
    return today, add_days(today, 6)


def _season_start(season: Mapping[str, Any]) -> date:
    raw = season.get("start", season.get("start_date"))
    return raw if isinstance(raw, date) else parse_iso(raw)


def opening_day(seasons: Iterable[Mapping[str, Any]], today: date) -> DateRange | None:
    """[d, d] for the earliest season start on or after today, or None.

    ``seasons`` are mappings with ``start`` (API shape, §6) or ``start_date`` (DB row),
    as an ISO string or a date. The caller passes only the seasons for the user's tags.
    """
    starts = [s for s in map(_season_start, seasons) if s >= today]
    if not starts:
        return None
    first = min(starts)
    return first, first


def presets(
    today: date,
    weekend_def: str = DEFAULT_WEEKEND_DEF,
    seasons: Iterable[Mapping[str, Any]] = (),
) -> dict[str, DateRange | None]:
    """All five §7.3 presets, keyed the same way as ``presets()`` in dates.js."""
    return {
        "thisWeekend": this_weekend(today, weekend_def),
        "nextWeekend": next_weekend(today, weekend_def),
        "today": today_range(today),
        "next7": next_7(today),
        "openingDay": opening_day(seasons, today),
    }
