"""Date-range, unit-list, and qualifier parsing, DESIGN.md §8.3.

Everything here is deterministic and PDF-independent: parsers hand it strings taken
from a table cell or the text layer, and get back structured values plus an explicit
account of what could *not* be explained (D3 — never guess).

    >>> parse_date_range("Nov 15 - Jan 15", 2026)
    DateRange(start=datetime.date(2026, 11, 15), end=datetime.date(2027, 1, 15), ...)
"""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

# ───────── dates ─────────

MONTHS: dict[str, int] = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}  # fmt: skip

#: A license year runs Jul 1 → Jun 30, so months before July belong to `license_year + 1`.
SEASON_START_MONTH = 7

_MONTH = (
    r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?"
    r"|Aug(?:ust)?|Sept?(?:ember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
)
_SEP = r"(?:\s*[-–—]\s*|\s+(?:through|thru|to)\s+)"
_DATE_RANGE_RE = re.compile(
    rf"(?P<m1>{_MONTH})\.?\s+(?P<d1>\d{{1,2}})(?:\s*,\s*(?P<y1>\d{{4}}))?"
    rf"{_SEP}"
    rf"(?:(?P<m2>{_MONTH})\.?\s+)?(?P<d2>\d{{1,2}})(?:\s*,\s*(?P<y2>\d{{4}}))?",
    re.IGNORECASE,
)
#: A month name anywhere in a cell — used to spot date text the range parser left behind.
_MONTH_TOKEN_RE = re.compile(rf"\b{_MONTH}\b\.?", re.IGNORECASE)

#: Cell values that mean "no season in this column" (§8.3): no row is created.
NO_SEASON_VALUES = {"", "none", "no season", "closed", "n/a", "-", "--", "–"}


@dataclass(frozen=True)
class DateRange:
    """One parsed season window, with the exact source text it came from."""

    start: dt.date
    end: dt.date
    text: str
    span: tuple[int, int] = (0, 0)

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1


def _month(name: str) -> int:
    return MONTHS[name[:3].lower()]


def year_for_month(month: int, license_year: int) -> int:
    """Calendar year of `month` inside a license year (Jul 1 `license_year` → Jun 30 +1)."""
    return license_year if month >= SEASON_START_MONTH else license_year + 1


def parse_date_ranges(text: str, license_year: int) -> list[DateRange]:
    """Every `Mon D – Mon D` range in `text`, resolved to real dates.

    Years come from the document's license year unless the text prints one. A second
    month may be omitted (`Oct 10 - 24`). An end before the start rolls into the next
    year (`Nov 15 - Jan 15`).
    """
    out: list[DateRange] = []
    for m in _DATE_RANGE_RE.finditer(text or ""):
        m1 = _month(m.group("m1"))
        m2 = _month(m.group("m2")) if m.group("m2") else m1
        d1, d2 = int(m.group("d1")), int(m.group("d2"))
        y1 = int(m.group("y1")) if m.group("y1") else year_for_month(m1, license_year)
        if m.group("y2"):
            y2 = int(m.group("y2"))
        else:
            y2 = y1 + 1 if (m2, d2) < (m1, d1) else y1
        try:
            start, end = dt.date(y1, m1, d1), dt.date(y2, m2, d2)
        except ValueError:  # e.g. "Feb 30" — a misread cell, never a guess
            continue
        out.append(DateRange(start, end, m.group(0).strip(), m.span()))
    return out


def parse_date_range(text: str, license_year: int) -> DateRange | None:
    """The first range in `text`, or None."""
    ranges = parse_date_ranges(text, license_year)
    return ranges[0] if ranges else None


def leftover_date_text(text: str, ranges: Sequence[DateRange]) -> list[str]:
    """Month tokens in `text` that no parsed range consumed (§8.7 `date_token_accounting`)."""
    taken = [r.span for r in ranges]
    out = []
    for m in _MONTH_TOKEN_RE.finditer(text or ""):
        if not any(a <= m.start() < b for a, b in taken):
            out.append(m.group(0))
    return out


def is_no_season(cell: str | None) -> bool:
    """True when a season cell means "no season" (`None`, blank, `Closed`)."""
    return (cell or "").strip().strip(".").lower() in NO_SEASON_VALUES


def in_license_year(d: dt.date, license_year: int, year_end: int | None = None) -> bool:
    """Is `d` inside Jul 1 `license_year` … Jun 30 (`year_end` or `license_year`) + 1?"""
    last = (year_end if year_end is not None else license_year) + 1
    return dt.date(license_year, 7, 1) <= d <= dt.date(last, 6, 30)


# ───────── unit lists ─────────

_UNIT_TOKEN = r"(?:YNP|\d{1,2}[A-Z]?)"
_RANGE_RE = re.compile(rf"\b({_UNIT_TOKEN})\s*[-–—]\s*({_UNIT_TOKEN})\b")
_TOKEN_RE = re.compile(rf"\b({_UNIT_TOKEN})\b")
_UNIT_NOISE_RE = re.compile(
    r"\b(?:units?|gmus?|and|in|the|of|all|only|portion|see|pg|page|p)\b\.?", re.IGNORECASE
)
#: `(See pg 31)` points at a description page — its number is never a unit.
_PAGE_REF_RE = re.compile(r"\(?\s*see\s+(?:pg|page)s?\.?\s*\d+\s*\)?", re.IGNORECASE)


@dataclass(frozen=True)
class UnitList:
    """Units named by a cell, plus the tokens that aren't real GMUs (§8.3)."""

    units: list[str] = field(default_factory=list)
    unknown: list[str] = field(default_factory=list)
    text: str = ""

    def __bool__(self) -> bool:
        return bool(self.units)


def natural_key(unit_id: str) -> tuple[int, str]:
    """Sort key matching the printed order: 4, 4A, 5, 10, 10A, …, YNP last."""
    m = re.fullmatch(r"(\d{1,2})([A-Z]?)", unit_id.upper())
    if not m:
        return (10**6, unit_id.upper())
    return (int(m.group(1)), m.group(2))


def _expand_range(a: str, b: str, known: Sequence[str] | None) -> list[str] | None:
    """Units from `a` to `b` inclusive, or None when the range can't be resolved."""
    a, b = a.upper(), b.upper()
    if known:
        order = list(known)
        if a in order and b in order:
            i, j = order.index(a), order.index(b)
            if i <= j:
                return order[i : j + 1]
        return None
    ka, kb = natural_key(a), natural_key(b)
    if ka >= kb or ka[0] == 10**6 or kb[0] == 10**6:
        return None
    if ka[0] == kb[0]:  # 21–21C: same number, letters in between
        letters = [""] + [chr(c) for c in range(ord("A"), ord("Z") + 1)]
        i, j = letters.index(ka[1]), letters.index(kb[1])
        return [f"{ka[0]}{s}" for s in letters[i : j + 1]]
    if ka[1] or kb[1]:  # letters across different numbers need the real unit list
        return None
    return [str(n) for n in range(ka[0], kb[0] + 1)]


def parse_units(text: str, known: Iterable[str] | None = None) -> UnitList:
    """Parse `1, 2, 3, 4A`, `Units 16A, 17, 19, and 20`, `21-21A` into unit ids.

    Every token is validated against `known` (the `units` table) when it is given;
    tokens that aren't real GMUs come back in `unknown` instead of being dropped.
    """
    raw = _PAGE_REF_RE.sub(" ", (text or "").replace("\n", " "))
    valid = {u.upper() for u in known} if known is not None else None
    order = sorted(valid, key=natural_key) if valid else None
    units: list[str] = []
    unknown: list[str] = []

    def add(unit: str) -> None:
        u = unit.upper()
        if valid is not None and u not in valid:
            if u not in unknown:
                unknown.append(u)
        elif u not in units:
            units.append(u)

    rest = raw
    for m in _RANGE_RE.finditer(raw):
        expanded = _expand_range(m.group(1), m.group(2), order)
        if expanded is None:
            if m.group(0).strip() not in unknown:
                unknown.append(m.group(0).strip())
        else:
            for u in expanded:
                add(u)
        rest = rest.replace(m.group(0), " ", 1)

    rest = _UNIT_NOISE_RE.sub(" ", rest)
    for m in _TOKEN_RE.finditer(rest):
        add(m.group(1))
    return UnitList(units=units, unknown=unknown, text=(text or "").strip())


# ───────── qualifiers → restriction codes (§5.1) ─────────

#: Printed qualifier → restriction code. Order matters: the first match wins per phrase.
QUALIFIER_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"white[\s-]?tail(?:ed)?\s+deer\s+only", "whitetail_only"),
    (r"mule\s+deer\s+only", "mule_deer_only"),
    (r"antlerless\s+(?:deer\s+|elk\s+)?only", "antlerless_only"),
    (r"antlered\s+(?:deer\s+|elk\s+)?only", "antlered_only"),
    (r"(?:2|two)[\s-]?point\s+(?:deer\s+)?only", "two_point_only"),
    (r"spike\s+(?:elk\s+)?only", "spike_only"),
    (r"brow[\s-]?tined", "brow_tined"),
    (r"youth\s+(?:hunt\s+|hunters?\s+)?only", "youth_only"),
    (r"private\s+land\s+only", "private_land_only"),
    (r"(?:in\s+a\s+)?portion\s+of\s+(?:the\s+)?unit", "portion_of_unit"),
    (r"outfitt(?:er|ed)[\s-]?(?:allocated\s+)?only", "outfitter_only"),
    (r"chronic\s+wasting\s+disease\s+testing\s+required", "cwd_testing"),
    (r"\bcwd\s+testing\s+required", "cwd_testing"),
    (r"motorized\s+hunting\s+rule\s+applies", "motorized_rule"),
    (r"second\s+deer\s+tag\s+may\s+not\s+be\s+used", "second_tag_prohibited"),
)
_QUALIFIER_RES: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (re.compile(p, re.IGNORECASE), code) for p, code in QUALIFIER_PATTERNS
)
#: Restriction codes this module can produce (`see_notes` is the catch-all).
RESTRICTION_CODES: frozenset[str] = frozenset(
    {code for _, code in QUALIFIER_PATTERNS} | {"see_notes"}
)
_PAREN_RE = re.compile(r"\(([^)]*)\)")
#: Parentheticals that are never a restriction (page pointers, tag counts).
_IGNORED_PAREN_RE = re.compile(
    r"^\s*(?:see\s+pg\.?\s*\d+|see\s+page\s*\d+|[\d,]+\s+tags?.*|continued)\s*$", re.IGNORECASE
)


@dataclass(frozen=True)
class Qualifiers:
    """Restriction codes found in a cell, plus text that matched nothing (D3)."""

    codes: list[str] = field(default_factory=list)
    unmatched: list[str] = field(default_factory=list)


def qualifiers(text: str) -> Qualifiers:
    """Map printed qualifiers to §5.1 restriction codes.

    An unexplained parenthetical is never dropped: it is reported in `unmatched` and
    adds `see_notes`, which keeps the unit yellow rather than green (D3).
    """
    raw = (text or "").replace("\n", " ")
    codes: list[str] = []
    matched_spans: list[tuple[int, int]] = []
    for rx, code in _QUALIFIER_RES:
        for m in rx.finditer(raw):
            matched_spans.append(m.span())
            if code not in codes:
                codes.append(code)
    unmatched: list[str] = []
    for m in _PAREN_RE.finditer(raw):
        inner = m.group(1).strip()
        if not inner or _IGNORED_PAREN_RE.match(inner):
            continue
        if parse_date_ranges(inner, 2000):  # a date in parentheses is not a qualifier
            continue
        if any(a >= m.start() and b <= m.end() for a, b in matched_spans):
            continue
        unmatched.append(inner)
    if unmatched and "see_notes" not in codes:
        codes.append("see_notes")
    return Qualifiers(codes=codes, unmatched=unmatched)


# ───────── table title → tag type / weapon / hunt class ─────────

#: Printed tag name → (`tag_types.id`, `species.id`). Longest phrase first.
TAG_TITLES: tuple[tuple[str, str, str], ...] = (
    ("white-tailed deer", "deer_whitetail", "deer"),
    ("whitetail deer", "deer_whitetail", "deer"),
    ("regular deer", "deer_regular", "deer"),
    ("zone a elk", "elk_zone_a", "elk"),
    ("zone b elk", "elk_zone_b", "elk"),
    ("elk zone a", "elk_zone_a", "elk"),
    ("elk zone b", "elk_zone_b", "elk"),
    ("pronghorn", "pronghorn_general", "pronghorn"),
    ("black bear", "black_bear", "black_bear"),
    ("mountain lion", "mountain_lion", "mountain_lion"),
    ("gray wolf", "wolf", "wolf"),
    ("wolf", "wolf", "wolf"),
    ("wild turkey", "turkey_general", "turkey"),
    ("turkey", "turkey_general", "turkey"),
    ("deer", "deer_regular", "deer"),
    ("elk", "elk_zone_a", "elk"),
)
#: Printed weapon phrase → `seasons.weapon`.
WEAPON_TITLES: tuple[tuple[str, str], ...] = (
    ("any weapon", "any"),
    ("short-range", "short_range"),
    ("short range", "short_range"),
    ("muzzleloader", "muzzleloader"),
    ("muzzle-loader", "muzzleloader"),
    ("archery", "archery"),
    ("shotgun", "shotgun"),
    ("falconry", "falconry"),
)
#: Printed phrase → `seasons.hunt_class`.
HUNT_CLASSES: tuple[tuple[str, str], ...] = (
    ("controlled", "controlled"),
    ("extra", "extra"),
    ("youth", "youth"),
    ("outfitter", "outfitter"),
    ("depredation", "other"),
)


@dataclass(frozen=True)
class TitleInfo:
    """`(tag_type, weapon, hunt_class)` read from a season table's title (§8.3)."""

    tag_type_id: str | None
    species_id: str | None
    weapon: str
    hunt_class: str
    year: int | None
    title: str
    matched: bool


def parse_title(title: str) -> TitleInfo:
    """`"2026 Regular Deer Tag / General Archery Only Seasons"` → deer_regular/archery/general."""
    raw = re.sub(r"\s+", " ", (title or "").replace("\n", " ")).strip()
    low = raw.lower()
    tag_type = species = None
    for phrase, tag, sp in TAG_TITLES:
        if phrase in low:
            tag_type, species = tag, sp
            break
    weapon = "any"
    for phrase, code in WEAPON_TITLES:
        if phrase in low:
            weapon = code
            break
    hunt_class = "general"
    for phrase, code in HUNT_CLASSES:
        if phrase in low:
            hunt_class = code
            break
    if hunt_class == "controlled":
        tag_type = "controlled"
    year = None
    if m := re.search(r"\b(?:19|20)\d{2}\b", raw):
        year = int(m.group(0))
    return TitleInfo(
        tag_type_id=tag_type,
        species_id=species,
        weapon=weapon,
        hunt_class=hunt_class,
        year=year,
        title=raw,
        matched=tag_type is not None,
    )


# ───────── confidence (§8.3) ─────────

#: Each unexplained item costs this much confidence.
CONFIDENCE_PENALTY = 0.2
#: A cell holding more than this many date ranges is suspicious.
MAX_DATE_RANGES_PER_CELL = 3


def confidence(
    *,
    unknown_units: int = 0,
    unparsed_date_text: int = 0,
    unmatched_qualifiers: int = 0,
    date_ranges: int = 0,
) -> float:
    """Parser self-score: 1.0 minus 0.2 per unexplained item, clamped to [0, 1] (§8.3).

    Penalised: each unknown unit token, each leftover date token, each unmatched
    qualifier, and a cell spanning more than `MAX_DATE_RANGES_PER_CELL` ranges.
    """
    penalties = unknown_units + unparsed_date_text + unmatched_qualifiers
    if date_ranges > MAX_DATE_RANGES_PER_CELL:
        penalties += 1
    return max(0.0, min(1.0, 1.0 - CONFIDENCE_PENALTY * penalties))
