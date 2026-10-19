"""T-06: availability engine rules (DESIGN.md §7.2, §7.4, §4.4). Pure, no DB."""

from __future__ import annotations

from datetime import date
from itertools import count
from typing import Any

import pytest

from planner.engine import (
    DERIVED_REASONS,
    RESTRICTIONS,
    combine_status,
    evaluate,
    evaluate_multi,
    intersect_spans,
    is_narrowing,
    merge_spans,
    sort_reasons,
    union_days,
)

_ids = count(1)

SAT, SUN = "2026-10-17", "2026-10-18"  # a Sat–Sun inside the general deer season


def season(**kw: Any) -> dict[str, Any]:
    """A published 2026 general any-weapon deer season (Oct 10–24) unless overridden."""
    row = {
        "id": next(_ids),
        "document_id": 1,
        "license_year": 2026,
        "status": "published",
        "species_id": "deer",
        "tag_type_id": "deer_regular",
        "zone": None,
        "hunt_number": None,
        "hunt_class": "general",
        "weapon": "any",
        "sex": "either",
        "start_date": "2026-10-10",
        "end_date": "2026-10-24",
        "restrictions": "[]",
        "source_page": 12,
    }
    row.update(kw)
    return row


def tag(tag_type_id: str = "deer_regular", year: int = 2026, **kw: Any) -> dict[str, Any]:
    row = {
        "id": next(_ids),
        "license_year": year,
        "tag_type_id": tag_type_id,
        "zone": None,
        "hunt_number": None,
        "filled": 0,
    }
    row.update(kw)
    return row


def run(
    seasons: list[dict[str, Any]],
    tags: list[dict[str, Any]],
    *,
    units: dict[int, list[str]] | None = None,
    coverage: dict[tuple[int, str], dict[str, Any]] | None = None,
    permits: list[tuple[int, str]] = (),  # type: ignore[assignment]
    youth: bool = False,
    species: str = "deer",
    start: str = SAT,
    end: str = SUN,
    weapon: str = "all",
    include_untagged: bool = False,
    unit_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Evaluate; every season applies to unit '39' unless `units` says otherwise."""
    units = units or {}
    coverage = coverage or {}
    sus = []
    for s in seasons:
        for u in units.get(s["id"], ["39"]):
            su = {"season_id": s["id"], "unit_id": u, "coverage": "full", "coverage_note": None}
            su.update(coverage.get((s["id"], u), {}))
            sus.append(su)
    return evaluate(
        seasons,
        sus,
        tags,
        list(permits),
        {"youth": int(youth)},
        species,
        start,
        end,
        weapon,
        include_untagged,
        unit_ids=unit_ids,
    )


def unit(result: dict[str, Any], uid: str = "39") -> dict[str, Any]:
    return result["units"].get(uid, {"status": "closed", "reasons": [], "seasons": []})


# ───────── acceptance criteria (§12 T-06) ─────────
def test_full_range_open():
    r = run([season()], [tag()])
    u = unit(r)
    assert u["status"] == "open"
    assert u["reasons"] == []
    assert u["days_open"] == 2
    assert r["range"] == {"start": SAT, "end": SUN, "days": 2}
    assert r["summary"]["open"] == 1


def test_partial_dates_restricted():
    # Season ends Saturday; the range runs through Sunday.
    u = unit(run([season(end_date=SAT)], [tag()]))
    assert u["status"] == "restricted"
    assert u["reasons"] == ["partial_dates"]
    assert u["days_open"] == 1


def test_partial_dates_when_season_opens_mid_range():
    u = unit(run([season(start_date=SUN)], [tag()]))
    assert (u["status"], u["reasons"], u["days_open"]) == ("restricted", ["partial_dates"], 1)


def test_antlerless_only_is_informational():
    # §5.1 (T-04 decision): animal-limit codes are badge-only; they don't change the color.
    u = unit(run([season(restrictions='["antlerless_only"]', sex="antlerless")], [tag()]))
    assert u["status"] == "open"
    assert u["reasons"] == []
    assert u["seasons"][0]["restrictions"] == ["antlerless_only"]


@pytest.mark.parametrize("code", ["antlered_only", "two_point_only", "spike_only", "brow_tined"])
def test_other_animal_limits_informational(code):
    assert unit(run([season(restrictions=[code])], [tag()]))["status"] == "open"


@pytest.mark.parametrize(
    "code", ["mule_deer_only", "private_land_only", "see_notes", "some_new_code"]
)
def test_narrowing_and_unknown_codes_restrict(code):
    u = unit(run([season(restrictions=[code])], [tag()]))
    assert (u["status"], u["reasons"]) == ("restricted", [code])


def test_whitetail_only_restricted_on_regular_tag():
    u = unit(run([season(restrictions='["whitetail_only"]')], [tag("deer_regular")]))
    assert (u["status"], u["reasons"]) == ("restricted", ["whitetail_only"])


def test_whitetail_only_open_on_whitetail_tag():
    s = season(tag_type_id="deer_whitetail", restrictions='["whitetail_only"]')
    u = unit(run([s], [tag("deer_whitetail")]))
    assert (u["status"], u["reasons"]) == ("open", [])
    assert u["seasons"][0]["restrictions"] == ["whitetail_only"]  # the badge stays


def test_archery_season_hidden_without_permit():
    s = season(weapon="archery")
    assert unit(run([s], [tag()]))["status"] == "closed"
    assert unit(run([s], [tag()], permits=[(2026, "archery")]))["status"] == "open"
    # A permit for another year doesn't count.
    assert unit(run([s], [tag()], permits=[(2025, "archery")]))["status"] == "closed"


def test_muzzleloader_season_needs_muzzleloader_permit():
    s = season(weapon="muzzleloader")
    assert unit(run([s], [tag()], permits=[(2026, "archery")]))["status"] == "closed"
    assert unit(run([s], [tag()], permits=[(2026, "muzzleloader")]))["status"] == "open"


def test_missing_permit_shows_as_untagged_with_reason():
    u = unit(run([season(weapon="archery")], [tag()], include_untagged=True))
    assert (u["status"], u["reasons"]) == ("untagged", ["needs_archery_permit"])


def test_weapon_rifle_excludes_archery_and_muzzleloader():
    any_s, bow, ml = season(), season(weapon="archery"), season(weapon="muzzleloader")
    permits = [(2026, "archery"), (2026, "muzzleloader")]
    r = run([any_s, bow, ml], [tag()], permits=permits, weapon="rifle")
    assert [s["id"] for s in unit(r)["seasons"]] == [any_s["id"]]
    # weapon=archery sees the bow season and any-weapon seasons (a bow is a legal weapon).
    r = run([any_s, bow, ml], [tag()], permits=permits, weapon="archery")
    assert {s["id"] for s in unit(r)["seasons"]} == {any_s["id"], bow["id"]}
    r = run([any_s, bow, ml], [tag()], permits=permits, weapon="all")
    assert len(unit(r)["seasons"]) == 3


def test_weapon_filter_excludes_even_with_include_untagged():
    r = run([season(weapon="archery")], [], weapon="rifle", include_untagged=True)
    assert unit(r)["status"] == "closed"


def test_short_range_only_for_all_or_short_range():
    s = season(weapon="short_range")
    assert unit(run([s], [tag()], weapon="rifle"))["status"] == "closed"
    assert unit(run([s], [tag()], weapon="short_range"))["status"] == "open"


def _elk(zone: str) -> dict[str, Any]:
    return season(species_id="elk", tag_type_id="elk_zone_a", zone=zone)


def test_elk_a_tag_wrong_zone_closed():
    t = tag("elk_zone_a", zone="Lolo")
    assert unit(run([_elk("Selway")], [t], species="elk"))["status"] == "closed"
    assert unit(run([_elk("Lolo")], [t], species="elk"))["status"] == "open"
    # A B tag in the right zone doesn't unlock an A season either.
    tb = tag("elk_zone_b", zone="Lolo")
    assert unit(run([_elk("Lolo")], [tb], species="elk"))["status"] == "closed"


def test_filled_tag_excluded():
    assert unit(run([season()], [tag(filled=1)]))["status"] == "closed"
    u = unit(run([season()], [tag(filled=1)], include_untagged=True))
    assert u["status"] == "untagged"


def test_include_untagged():
    assert unit(run([season()], []))["status"] == "closed"
    u = unit(run([season()], [], include_untagged=True))
    assert (u["status"], u["reasons"], u["days_open"]) == ("untagged", [], 0)
    assert u["seasons"][0]["status"] == "untagged"
    assert u["seasons"][0]["tag_ids"] == []


def test_youth_only_hidden_for_adults():
    s = season(restrictions='["youth_only"]')
    assert unit(run([s], [tag()]))["status"] == "closed"
    assert unit(run([s], [tag()], include_untagged=True))["status"] == "closed"
    # Youth profile: shown, and still yellow (§4.4).
    u = unit(run([s], [tag()], youth=True))
    assert (u["status"], u["reasons"]) == ("restricted", ["youth_only"])


def test_youth_hunt_class_implies_youth_only():
    s = season(hunt_class="youth")
    assert unit(run([s], [tag()]))["status"] == "closed"
    assert unit(run([s], [tag()], youth=True))["reasons"] == ["youth_only"]


def _upland(**kw: Any) -> dict[str, Any]:
    # Chukar runs into the next calendar year from a 2-year brochure.
    base = dict(
        species_id="chukar",
        tag_type_id="hunting_license",
        weapon="any",
        sex="na",
        start_date="2026-09-19",
        end_date="2027-01-31",
    )
    base.update(kw)
    return season(**base)


def test_dec31_jan1_crossover_without_next_year_tag():
    r = run(
        [_upland()],
        [tag("hunting_license", 2026)],
        species="chukar",
        start="2026-12-31",
        end="2027-01-01",
    )
    u = unit(r)
    assert (u["status"], u["reasons"], u["days_open"]) == ("restricted", ["no_tag_next_year"], 1)


def test_dec31_jan1_crossover_with_both_years_open():
    tags = [tag("hunting_license", 2026), tag("hunting_license", 2027)]
    r = run([_upland()], tags, species="chukar", start="2026-12-31", end="2027-01-01")
    u = unit(r)
    assert (u["status"], u["reasons"], u["days_open"]) == ("open", [], 2)
    assert u["seasons"][0]["tag_ids"] == sorted(t["id"] for t in tags)


def test_crossover_with_only_next_year_tag():
    r = run(
        [_upland()],
        [tag("hunting_license", 2027)],
        species="chukar",
        start="2026-12-31",
        end="2027-01-01",
    )
    assert unit(r)["reasons"] == ["no_tag_prev_year"]


def test_crossover_permit_checked_per_year():
    s = _upland(weapon="archery")
    tags = [tag("hunting_license", 2026), tag("hunting_license", 2027)]
    r = run(
        [s],
        tags,
        permits=[(2026, "archery")],
        species="chukar",
        start="2026-12-31",
        end="2027-01-01",
    )
    assert unit(r)["reasons"] == ["no_tag_next_year"]


# ───────── controlled hunts (§7.2 D2) ─────────
def _controlled(hunt: str = "1002", year: int = 2026, **kw: Any) -> dict[str, Any]:
    base: dict[str, Any] = dict(
        tag_type_id="controlled", hunt_number=hunt, hunt_class="controlled", license_year=year
    )
    return season(**{**base, **kw})


def test_controlled_tag_matches_hunt_number_and_year():
    s = _controlled("1002")
    assert unit(run([s], [tag("controlled", hunt_number="1002")]))["status"] == "open"
    assert unit(run([s], [tag("controlled", hunt_number="1003")]))["status"] == "closed"


def test_controlled_tag_from_other_year_never_matches():
    # A 2025 #1002 tag must not unlock 2026's #1002, even for a range in 2025's tag year.
    s = _controlled("1002", year=2026)
    t = tag("controlled", 2025, hunt_number="1002")
    assert unit(run([s], [t]))["status"] == "closed"
    s25 = _controlled("1002", year=2025, start_date="2025-10-10", end_date="2025-10-24")
    t26 = tag("controlled", 2026, hunt_number="1002")
    assert unit(run([s25], [t26], start="2025-10-17", end="2025-10-18"))["status"] == "closed"


def test_partial_unit_coverage_restricted():
    s = _controlled("1028")
    cov = {(s["id"], "38"): {"coverage": "partial", "coverage_note": "Portion of Unit 38 — p.40"}}
    r = run([s], [tag("controlled", hunt_number="1028")], units={s["id"]: ["38"]}, coverage=cov)
    u = unit(r, "38")
    assert (u["status"], u["reasons"]) == ("restricted", ["portion_of_unit"])
    assert u["seasons"][0]["coverage"] == "partial"
    assert u["seasons"][0]["coverage_note"] == "Portion of Unit 38 — p.40"


def test_coverage_frac_below_threshold_is_partial():
    s = _controlled("1028")
    t = tag("controlled", hunt_number="1028")
    low = {(s["id"], "39"): {"coverage": "full", "coverage_frac": 0.97}}
    high = {(s["id"], "39"): {"coverage": "full", "coverage_frac": 0.99}}
    assert unit(run([s], [t], coverage=low))["reasons"] == ["portion_of_unit"]
    assert unit(run([s], [t], coverage=high))["status"] == "open"


def test_outfitter_hidden_unless_hunt_held():
    s = _controlled("1500", hunt_class="outfitter")
    assert unit(run([s], [], include_untagged=True))["status"] == "closed"
    u = unit(run([s], [tag("controlled", hunt_number="1500")]))
    assert (u["status"], u["reasons"]) == ("restricted", ["outfitter_only"])


def test_controlled_archery_needs_permit():
    s = _controlled("1010", weapon="archery")
    t = tag("controlled", hunt_number="1010")
    assert unit(run([s], [t]))["status"] == "closed"
    assert unit(run([s], [t], include_untagged=True))["reasons"] == ["needs_archery_permit"]


def test_controlled_and_general_union_labels_unlocking_tag():
    gen, ctl = season(), _controlled("1002")
    tg, tc = tag(), tag("controlled", hunt_number="1002")
    r = run([gen, ctl], [tg, tc], units={gen["id"]: ["39"], ctl["id"]: ["39", "11"]})
    by_id = {s["id"]: s for s in unit(r)["seasons"]}
    assert by_id[gen["id"]]["tag_ids"] == [tg["id"]]
    assert by_id[ctl["id"]]["tag_ids"] == [tc["id"]]
    assert unit(r, "11")["status"] == "open"


# ───────── unit aggregation ─────────
def test_best_status_wins_and_reasons_from_best_only():
    open_s = season()
    restricted = season(restrictions='["see_notes"]')
    u = unit(run([open_s, restricted], [tag()], include_untagged=True))
    assert (u["status"], u["reasons"]) == ("open", [])
    assert len(u["seasons"]) == 2


def test_restricted_reasons_deduped_union():
    a = season(end_date=SAT, restrictions='["see_notes"]')
    b = season(start_date=SUN, restrictions='["private_land_only", "see_notes"]')
    u = unit(run([a, b], [tag()]))
    assert u["status"] == "restricted"
    # §5.1 vocabulary order, then derived codes.
    assert u["reasons"] == ["private_land_only", "see_notes", "partial_dates"]
    assert u["days_open"] == 2  # Sat from a, Sun from b


def test_restricted_beats_untagged():
    tagged = season(end_date=SAT)
    untagged = season(tag_type_id="deer_whitetail")
    u = unit(run([tagged, untagged], [tag()], include_untagged=True))
    assert (u["status"], u["reasons"]) == ("restricted", ["partial_dates"])


def test_days_open_ignores_untagged_seasons():
    u = unit(
        run(
            [season(end_date=SAT), season(tag_type_id="deer_whitetail")],
            [tag()],
            include_untagged=True,
        )
    )
    assert u["days_open"] == 1


def test_unit_ids_reported_and_summarized():
    r = run([season()], [tag()], unit_ids=["1", "39", "YNP"])
    assert list(r["units"]) == ["1", "39", "YNP"]
    assert r["units"]["1"] == {"status": "closed", "reasons": [], "days_open": 0, "seasons": []}
    assert r["summary"] == {"open": 1, "restricted": 0, "untagged": 0, "closed": 2}


def test_filters_species_status_and_dates():
    seasons = [
        season(species_id="elk"),
        season(status="staged"),
        season(status="superseded"),
        season(start_date="2026-11-01", end_date="2026-11-10"),
    ]
    r = run(seasons, [tag()], include_untagged=True)
    assert r["units"] == {}


def test_season_entry_shape():
    s = season(restrictions='["cwd_testing"]', source_page=17, document_id=4)
    entry = unit(run([s], [tag()]))["seasons"][0]
    assert entry == {
        "id": s["id"],
        "status": "open",
        "reasons": [],
        "tag_ids": entry["tag_ids"],
        "tag_type": "deer_regular",
        "zone": None,
        "hunt_number": None,
        "hunt_class": "general",
        "weapon": "any",
        "sex": "either",
        "start": "2026-10-10",
        "end": "2026-10-24",
        "restrictions": ["cwd_testing"],
        "coverage": "full",
        "coverage_note": None,
        "source_page": 17,
        "document_id": 4,
        "license_year": 2026,
    }


def test_invalid_inputs():
    with pytest.raises(ValueError):
        run([season()], [tag()], start=SUN, end=SAT)
    with pytest.raises(ValueError):
        run([season()], [tag()], weapon="bazooka")


def test_helpers():
    assert all(is_narrowing(c) for c in DERIVED_REASONS)
    assert not is_narrowing("cwd_testing")
    assert is_narrowing("not_a_code")
    assert set(DERIVED_REASONS).isdisjoint(RESTRICTIONS)
    d = date
    assert union_days([]) == 0
    assert union_days([(d(2026, 1, 1), d(2026, 1, 3)), (d(2026, 1, 2), d(2026, 1, 5))]) == 5
    assert union_days([(d(2026, 1, 1), d(2026, 1, 1)), (d(2026, 1, 3), d(2026, 1, 3))]) == 2
    assert sort_reasons(["zzz", "see_notes", "partial_dates", "whitetail_only", "see_notes"]) == [
        "whitetail_only",
        "see_notes",
        "partial_dates",
        "zzz",
    ]


# ───────── T-27: span algebra and status combination ─────────
def test_merge_and_intersect_spans():
    d = date
    assert merge_spans([]) == [] and intersect_spans([], []) == []
    a = [(d(2026, 10, 10), d(2026, 10, 20)), (d(2026, 10, 1), d(2026, 10, 5))]
    assert merge_spans(a) == [(d(2026, 10, 1), d(2026, 10, 5)), (d(2026, 10, 10), d(2026, 10, 20))]
    # Overlapping spans merge; the merged list is what union_days counts.
    assert merge_spans([(d(2026, 1, 1), d(2026, 1, 3)), (d(2026, 1, 2), d(2026, 1, 9))]) == [
        (d(2026, 1, 1), d(2026, 1, 9))
    ]
    b = [(d(2026, 10, 3), d(2026, 10, 12))]
    assert intersect_spans(a, b) == [
        (d(2026, 10, 3), d(2026, 10, 5)),
        (d(2026, 10, 10), d(2026, 10, 12)),
    ]
    # Disjoint single days: one day each, nothing in common. `min()` of the counts says 1.
    one, two = [(d(2026, 10, 17), d(2026, 10, 17))], [(d(2026, 10, 18), d(2026, 10, 18))]
    assert union_days(one) == union_days(two) == 1
    assert intersect_spans(one, two) == []
    assert intersect_spans(one, one) == one
    # Intersection is commutative and idempotent on merged input.
    assert intersect_spans(b, a) == intersect_spans(a, b)


def test_combine_status_ranks():
    order = ["closed", "untagged", "restricted", "open"]
    for i, lo in enumerate(order):
        for hi in order[i:]:
            assert combine_status([lo, hi], "all") == lo
            assert combine_status([lo, hi], "any") == hi
    assert combine_status([], "all") == "closed"
    # One species behaves identically in both modes.
    for s in order:
        assert combine_status([s], "all") == combine_status([s], "any") == s


def run_multi(
    seasons: list[dict[str, Any]],
    tags: list[dict[str, Any]],
    species: list[str],
    *,
    mode: str = "any",
    units: dict[int, list[str]] | None = None,
) -> dict[str, Any]:
    units = units or {}
    sus = [
        {"season_id": s["id"], "unit_id": u, "coverage": "full", "coverage_note": None}
        for s in seasons
        for u in units.get(s["id"], ["39"])
    ]
    return evaluate_multi(
        seasons, sus, tags, [], {"youth": 0}, species, SAT, SUN, "all", False, mode
    )


def test_evaluate_multi_validates_its_arguments():
    with pytest.raises(ValueError, match="at least one"):
        run_multi([], [], [])
    with pytest.raises(ValueError, match="at most 4"):
        run_multi([], [], ["deer", "elk", "pronghorn", "black_bear", "wolf"])
    with pytest.raises(ValueError, match="species_mode"):
        run_multi([], [], ["deer"], mode="both")


def test_evaluate_multi_leaves_the_single_species_payload_alone():
    seasons, tags = [season()], [tag()]
    one = run_multi(seasons, tags, ["deer"])
    base = run(seasons, tags)
    assert one["species"] == ["deer"] and one["species_mode"] == "any"
    assert {k: v for k, v in one.items() if k not in ("species", "species_mode")} == {
        k: v for k, v in base.items() if k != "species"
    }
    assert "open_spans" not in one["units"]["39"]  # the internal day sets never leak


def test_evaluate_multi_combines_two_species():
    deer = season()
    elk = season(species_id="elk", tag_type_id="elk_zone_a", zone="Lolo")
    tags = [tag(), tag("elk_zone_a", zone="Lolo")]
    both = run_multi([deer, elk], tags, ["deer", "elk"], mode="all", units={elk["id"]: ["40"]})
    assert both["units"]["39"]["status"] == "closed"  # no elk season there
    assert both["units"]["39"]["per_species"] == {
        "deer": {"status": "open", "reasons": [], "days_open": 2},
        "elk": {"status": "closed", "reasons": [], "days_open": 0},
    }
    assert (
        run_multi([deer, elk], tags, ["deer", "elk"], units={elk["id"]: ["40"]})["units"]["39"][
            "status"
        ]
        == "open"
    )  # `any` takes the best
