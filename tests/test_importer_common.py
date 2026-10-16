"""T-10: `planner/importer/common.py` — dates, unit lists, qualifiers, titles (§8.3).

Pure string → value tests: no PDF, no DB. Every case is a string that really appears in
the 2026 IDFG brochures (or a deliberate malformation the parser must refuse).
"""

from __future__ import annotations

import datetime as dt

import pytest

from planner import engine
from planner.importer import common

D = dt.date
YEAR = 2026  # license year: Jul 1 2026 → Jun 30 2027

# (text, license_year, start, end) — the §12 acceptance list plus the cases the
# brochures actually print. Months before July belong to license_year + 1.
DATE_CASES: list[tuple[str, int, D, D]] = [
    ("Aug 30 - Sep 30", YEAR, D(2026, 8, 30), D(2026, 9, 30)),
    ("Oct 10 – Nov 3", YEAR, D(2026, 10, 10), D(2026, 11, 3)),  # en dash
    ("Oct 10 — Nov 3", YEAR, D(2026, 10, 10), D(2026, 11, 3)),  # em dash
    ("August 30, 2026 through January 31, 2027", YEAR, D(2026, 8, 30), D(2027, 1, 31)),
    ("Dec 25 - Dec 31", YEAR, D(2026, 12, 25), D(2026, 12, 31)),
    ("Nov 15 - Jan 15", YEAR, D(2026, 11, 15), D(2027, 1, 15)),  # crosses the year
    ("Nov 15 - Jan 15", 2025, D(2025, 11, 15), D(2026, 1, 15)),
    ("Oct 10 - 24", YEAR, D(2026, 10, 10), D(2026, 10, 24)),  # second month omitted
    ("Oct. 10 - Oct. 24", YEAR, D(2026, 10, 10), D(2026, 10, 24)),
    ("Oct 10-Oct 24", YEAR, D(2026, 10, 10), D(2026, 10, 24)),  # no spaces
    ("OCT 10 - OCT 24", YEAR, D(2026, 10, 10), D(2026, 10, 24)),
    ("Sept 1 - Sept 30", YEAR, D(2026, 9, 1), D(2026, 9, 30)),
    ("Sep 1 - Sep 30", YEAR, D(2026, 9, 1), D(2026, 9, 30)),
    ("September 1 - September 30", YEAR, D(2026, 9, 1), D(2026, 9, 30)),
    ("Aug 30 to Sep 30", YEAR, D(2026, 8, 30), D(2026, 9, 30)),
    ("Aug 30 thru Sep 30", YEAR, D(2026, 8, 30), D(2026, 9, 30)),
    ("Jan 1 - Jan 31", YEAR, D(2027, 1, 1), D(2027, 1, 31)),  # spring half of the year
    ("Mar 15 - May 31", YEAR, D(2027, 3, 15), D(2027, 5, 31)),  # spring bear runs in 2027
    ("Apr 15 - May 25", YEAR, D(2027, 4, 15), D(2027, 5, 25)),  # spring turkey
    ("Jun 30 - Jul 1", YEAR, D(2027, 6, 30), D(2027, 7, 1)),
    ("Jul 1 - Jun 30", YEAR, D(2026, 7, 1), D(2027, 6, 30)),  # year-round (predatory)
    ("Nov 1, 2026 - Dec 31, 2026", YEAR, D(2026, 11, 1), D(2026, 12, 31)),
    ("Dec 1 - Dec 15, 2026", YEAR, D(2026, 12, 1), D(2026, 12, 15)),
    ("Feb 29 - Mar 15", 2027, D(2028, 2, 29), D(2028, 3, 15)),  # leap day
    ("Oct 10 - Nov 3 (White-tailed deer only)", YEAR, D(2026, 10, 10), D(2026, 11, 3)),
    ("Season: Oct 7 - Oct 31, youth only", YEAR, D(2026, 10, 7), D(2026, 10, 31)),
    ("Aug 1 - Aug 31", YEAR, D(2026, 8, 1), D(2026, 8, 31)),
    ("May 1 - May 31", YEAR, D(2027, 5, 1), D(2027, 5, 31)),
    ("Sep 15 - Dec 31", YEAR, D(2026, 9, 15), D(2026, 12, 31)),
    ("Oct 1 - Jan 1", YEAR, D(2026, 10, 1), D(2027, 1, 1)),
]

NO_DATE_CASES = ["None", "", "Closed", "Open year-round", "see notes", "Feb 30 - Mar 1", "31 - 45"]


@pytest.mark.parametrize(("text", "year", "start", "end"), DATE_CASES)
def test_date_ranges(text: str, year: int, start: D, end: D) -> None:
    got = common.parse_date_range(text, year)
    assert got is not None, f"no range parsed from {text!r}"
    assert (got.start, got.end) == (start, end)


def test_at_least_25_date_cases() -> None:
    """§12 T-10 asks for at least 25 date-string cases."""
    assert len(DATE_CASES) >= 25


@pytest.mark.parametrize("text", NO_DATE_CASES)
def test_no_date_parsed(text: str) -> None:
    assert common.parse_date_ranges(text, YEAR) == []


def test_two_ranges_in_one_cell() -> None:
    ranges = common.parse_date_ranges("Oct 1 - Oct 7 and Oct 20 - Oct 31", YEAR)
    assert [(r.start, r.end) for r in ranges] == [
        (D(2026, 10, 1), D(2026, 10, 7)),
        (D(2026, 10, 20), D(2026, 10, 31)),
    ]
    assert ranges[0].text == "Oct 1 - Oct 7"


def test_days_and_source_text() -> None:
    r = common.parse_date_range("Aug 30 - Sep 30", YEAR)
    assert r is not None and r.days == 32 and r.text == "Aug 30 - Sep 30"


def test_leftover_date_text_flags_unconsumed_months() -> None:
    cell = "Oct 10 - Oct 24; see November supplement"
    ranges = common.parse_date_ranges(cell, YEAR)
    assert len(ranges) == 1
    assert common.leftover_date_text(cell, ranges) == ["November"]
    assert common.leftover_date_text("Oct 10 - Oct 24", ranges) == []


@pytest.mark.parametrize(
    ("cell", "expected"),
    [("None", True), ("", True), (None, True), ("Closed", True), ("Oct 10 - Oct 24", False)],
)
def test_is_no_season(cell: str | None, expected: bool) -> None:
    assert common.is_no_season(cell) is expected


def test_in_license_year() -> None:
    assert common.in_license_year(D(2026, 7, 1), 2026)
    assert common.in_license_year(D(2027, 6, 30), 2026)
    assert not common.in_license_year(D(2026, 6, 30), 2026)
    assert not common.in_license_year(D(2027, 7, 1), 2026)
    # two-year upland brochure: 2026 → 2027, so it runs to Jun 30 2028
    assert common.in_license_year(D(2028, 6, 30), 2026, 2027)


# ───────── unit lists ─────────

UNITS = [
    "1", "2", "3", "4", "4A", "5", "6", "7", "10", "10A", "16A", "17", "19", "20",
    "21", "21A", "28", "31", "39", "60", "60A", "61", "68A", "YNP",
]  # fmt: skip

UNIT_CASES: list[tuple[str, list[str], list[str]]] = [
    ("1, 2, 3, 4A, 5, 6", ["1", "2", "3", "4A", "5", "6"], []),
    ("Units 16A, 17, 19, and 20", ["16A", "17", "19", "20"], []),
    ("21-21A", ["21", "21A"], []),
    ("Unit 39", ["39"], []),
    ("1,2,3", ["1", "2", "3"], []),
    ("4A", ["4A"], []),
    ("All of Unit 6 and 7", ["6", "7"], []),
    ("YNP", ["YNP"], []),
    ("60, 60A\n61", ["60", "60A", "61"], []),
    ("1, 2, 99", ["1", "2"], ["99"]),
    ("Units 10-10A, 17", ["10", "10A", "17"], []),
    ("See pg 31", [], []),  # a page pointer is not a unit list
    ("4-5", ["4", "4A", "5"], []),  # the range walks the real unit order
    ("In a portion of Unit 28", ["28"], []),
]


@pytest.mark.parametrize(("text", "units", "unknown"), UNIT_CASES)
def test_parse_units(text: str, units: list[str], unknown: list[str]) -> None:
    got = common.parse_units(text, UNITS)
    assert got.units == units
    assert got.unknown == unknown


def test_at_least_10_unit_cases() -> None:
    """§12 T-10 asks for at least 10 unit-list cases."""
    assert len(UNIT_CASES) >= 10


def test_parse_units_without_a_known_list() -> None:
    assert common.parse_units("10-13").units == ["10", "11", "12", "13"]
    assert common.parse_units("21-21C").units == ["21", "21A", "21B", "21C"]
    assert common.parse_units("Units 1, 2, 99").units == ["1", "2", "99"]


def test_unresolvable_range_is_reported_not_guessed() -> None:
    got = common.parse_units("70-75", UNITS)
    assert got.units == []
    assert got.unknown == ["70-75"]


def test_natural_key_orders_like_the_brochure() -> None:
    assert sorted(["10A", "4", "10", "4A", "5", "YNP"], key=common.natural_key) == [
        "4",
        "4A",
        "5",
        "10",
        "10A",
        "YNP",
    ]


# ───────── qualifiers ─────────

QUALIFIER_CASES: list[tuple[str, str]] = [
    ("(White-tailed deer only)", "whitetail_only"),
    ("(Mule deer only)", "mule_deer_only"),
    ("(Antlered only)", "antlered_only"),
    ("(Antlerless only)", "antlerless_only"),
    ("(2-point deer only)", "two_point_only"),
    ("(Spike only)", "spike_only"),
    ("(Brow-tined bull)", "brow_tined"),
    ("(Youth hunt only)", "youth_only"),
    ("Private land only", "private_land_only"),
    ("In a Portion of Unit", "portion_of_unit"),
    ("(Outfitter allocated only)", "outfitter_only"),
    ("Chronic Wasting Disease Testing Required", "cwd_testing"),
    ("Motorized Hunting Rule Applies", "motorized_rule"),
    ("Second deer tag may not be used", "second_tag_prohibited"),
    # §12 T-13 — printed in the bear, lion and wolf tables' Notes cells.
    ("Second bear tag may be used", "second_tag_allowed"),
    ("Bait prohibited", "bait_prohibited"),
    ("Bait and dogs prohibited", "dogs_prohibited"),
    ("Electronic calls may be used", "electronic_calls"),
    ("Caution: grizzly bears may be encountered", "grizzly_caution"),
    ("Expanded hunting methods allowed", "expanded_methods"),
    ("General hunting rules apply", "general_methods"),
]


@pytest.mark.parametrize(("text", "code"), QUALIFIER_CASES)
def test_qualifier_codes(text: str, code: str) -> None:
    result = common.qualifiers(text)
    assert code in result.codes
    assert result.unmatched == []


def test_every_qualifier_code_is_covered_and_valid() -> None:
    """Every code this module can emit has a case above, and is a §5.1 code."""
    covered = {code for _, code in QUALIFIER_CASES} | {"see_notes"}
    assert covered == common.RESTRICTION_CODES
    assert common.RESTRICTION_CODES <= set(engine.RESTRICTIONS)


def test_two_qualifiers_in_one_cell() -> None:
    codes = common.qualifiers("Oct 10 - Nov 3 (White-tailed deer only) Private land only").codes
    assert codes == ["whitetail_only", "private_land_only"]


def test_unexplained_parenthetical_becomes_see_notes() -> None:
    """D3: unexplained conditions stay yellow instead of being dropped."""
    result = common.qualifiers("Oct 10 - Oct 24 (Shotgun with slugs prohibited)")
    assert result.unmatched == ["Shotgun with slugs prohibited"]
    assert result.codes == ["see_notes"]


def test_page_pointers_and_dates_are_not_qualifiers() -> None:
    assert common.qualifiers("1-1 (See pg 31)").codes == []
    assert common.qualifiers("Elk Zone (Oct 10 - Oct 24)").codes == []
    assert common.qualifiers("(510 Tags)").codes == []


# ───────── titles ─────────


@pytest.mark.parametrize(
    ("title", "tag_type", "weapon", "hunt_class"),
    [
        (
            "2026 Regular Deer Tag / General Archery Only Seasons – Archery Permit Required",
            "deer_regular",
            "archery",
            "general",
        ),
        ("2026 Regular Deer Tag / General Any Weapon Seasons", "deer_regular", "any", "general"),
        (
            "2026 White-tailed Deer Tag / General Short-Range Weapon Seasons",
            "deer_whitetail",
            "short_range",
            "general",
        ),
        (
            "2026 Regular Deer Tag / General Muzzleloader Seasons",
            "deer_regular",
            "muzzleloader",
            "general",
        ),
        ("2026 Controlled Deer Hunts", "controlled", "any", "controlled"),
        ("2026 Pronghorn General Seasons", "pronghorn_general", "any", "general"),
        ("2026 Black Bear General Seasons", "black_bear", "any", "general"),
        ("2026 Wild Turkey General Seasons", "turkey_general", "any", "general"),
        ("2026 Elk Zone A Any Weapon Seasons", "elk_zone_a", "any", "general"),
        ("2026 Youth Only Deer Hunts", "deer_regular", "any", "youth"),
    ],
)
def test_parse_title(title: str, tag_type: str, weapon: str, hunt_class: str) -> None:
    info = common.parse_title(title)
    assert (info.tag_type_id, info.weapon, info.hunt_class) == (tag_type, weapon, hunt_class)
    assert info.year == 2026
    assert info.matched


def test_unmatched_title() -> None:
    info = common.parse_title("Hunt Area Descriptions")
    assert not info.matched and info.tag_type_id is None


# ───────── confidence ─────────


def test_confidence_penalties() -> None:
    assert common.confidence() == 1.0
    assert common.confidence(unknown_units=1) == pytest.approx(0.8)
    assert common.confidence(unparsed_date_text=1) == pytest.approx(0.8)
    assert common.confidence(unmatched_qualifiers=1) == pytest.approx(0.8)
    assert common.confidence(date_ranges=4) == pytest.approx(0.8)
    assert common.confidence(date_ranges=3) == 1.0
    assert common.confidence(unknown_units=2, unmatched_qualifiers=1) == pytest.approx(0.4)
    assert common.confidence(unknown_units=99) == 0.0
