"""Moose, bighorn sheep & mountain goat brochure, DESIGN.md §12 T-25.

Every hunt in the MSG brochure is controlled, so this module is a `Brochure` profile
for the shared T-17a parser (`planner.importer.controlled`) plus the three things that
brochure does differently (`docs/spike_msg_parser.py` proved all of them):

* **Two hunt records per printed row.** The sheep and goat tables print `Hunt No. |
  Hunt Area | Tags | Season Dates` twice side by side, the moose tables print one group
  with a Notes column, and the California table is nested inside another table (its
  `Hunt No.` is column 1). Path A reads every group the header row starts; path B
  (`rows_from_text` below) anchors on the hunt-number words themselves, so it never
  looks at a ruled line or at the header.
* **Two license years in one brochure.** The title reads `2025 & 2026 … Controlled
  Hunts` and the printed window applies to both years, so each date token produces two
  seasons (`Brochure.both_years`).
* **A corrections section.** Pages 2–3 print `<SPECIES> – HUNT NO. NNNN (AREA X)` items
  that change a hunt's tag count; they are read here, matched to the parsed rows and
  reported by `corrections_applied`. A hunt corrected down to 0 tags keeps its row
  (history) but is staged `closed_hunt`, never shown as open.

The brochure's side crop is 0 (`SIDE_CROP_BY_KIND`): its tables' outer rule is printed
*under* the rotated section tab, so any crop wide enough to hide the tab also deletes
the rule, and `find_tables` then drops every second hunt-number cell (19 of 28 sheep
hunts on p. 19). Uncropped, the tab becomes a one-column table of its own, which the
header-driven reader ignores.
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Sequence
from dataclasses import replace
from functools import partial
from typing import Any

from planner.importer import (
    ParseContext,
    ParsedSeason,
    ParserSpec,
    controlled,
    hunt_areas,
    register,
)
from planner.importer.checks import SectionFacts
from planner.importer.controlled import Brochure, DocState, HuntRow, TitleInfo
from planner.importer.extract import Page, Word
from planner.importer.textlayer import join_words

#: `- 510 Tags`, `100 Total Tags Including …`, `16 Tags` — MSG prints its totals without
#: the Big Game brochure's parentheses.
TOTAL_RE = re.compile(r"([\d,]+)\s+(?:Total\s+)?Tags\b", re.IGNORECASE)
#: A total that covers tags with no table row: `100 Total Tags Including Special
#: Lottery and Auction Tag`. The named tags are one each (§8.7: name them explicitly).
UMBRELLA_RE = re.compile(r"([\d,]+)\s+Total Tags Including\s+(?P<extras>[^.]+)", re.IGNORECASE)
#: The named extra tags inside that phrase (`Special Lottery and Auction Tag`).
EXTRA_TAG_RE = re.compile(r"\b(Lottery|Auction|Super Hunt)\b", re.IGNORECASE)
#: A hunt-table title: MSG prints `… Controlled Hunts` above every table but one, the
#: two-hunt `Antlered Moose Archery ONLY Hunt` on p. 11 (5 of the 510 moose tags).
TITLE_MARKER_RE = re.compile(r"controlled hunts?|archery only hunt", re.IGNORECASE)
#: The legend lines printed under an MSG hunt table.
LEGEND_RE = re.compile(
    r"See controlled hunt area descriptions|This wilderness hunt area", re.IGNORECASE
)
#: Legend sentence → the meaning this brochure gives that marker.
LEGEND_MEANINGS: tuple[tuple[str, str], ...] = (
    (r"portion of this unit, includes other units", "portion_or_other_units"),
    (r"wilderness hunt area encompasses extreme terrain", "wilderness_terrain"),
)
#: A hunt area id as MSG prints it: `11`, `19A`, `1-1`, `27-1`, `17L`, `20AL`, with the
#: full-size `*` **part of the id** (GIS stores `20A*`) and a superscript letter after it.
AREA_RE = re.compile(r"(?P<area>\d{1,2}[A-Z]*(?:-\d+)?[A-Z]*\*?)(?P<mark>[a-z]?)")
#: The MSG page footer, which carries the brochure's own page number (T-17a's map).
FOOTER_RE = re.compile(
    r"idfg\.idaho\.gov\s*(?P<right>\d{1,3})\s*$|^\s*(?P<left>\d{1,3})\s+Moose, Bighorn Sheep",
    re.IGNORECASE | re.MULTILINE,
)
#: `Hunt Area 1-1 —` on a description page (the id is printed without its `*`).
AREA_ENTRY = r"Hunt Areas?\s+(?:[\w-]+\s+and\s+)?{area}\b"
#: `MOOSE HUNT AREA DESCRIPTIONS` — MSG prints no `CONTROLLED` in the heading, and the
#: sheep heading names both bighorn species over two printed lines.
HEADING_RE = re.compile(r"^(?P<species>[A-Z][A-Z &]*?)\s+HUNT AREA DESCRIPTIONS", re.M | re.S)
#: `ROCKY MOUNTAIN BIGHORN SHEEP – HUNT NO. 5007 (AREA 26)` — one corrections item.
CORRECTION_RE = re.compile(
    r"^(?P<species>[A-Z][A-Z ]+?)\s*[–—-]\s*HUNT NO\.\s*(?P<hunt>\d{4})"
    r"(?:\s*\(AREA\s*(?P<area>[^)]+)\))?",
    re.MULTILINE,
)
#: What a correction did to the hunt's tag count, as the item's own prose says it.
ZEROED_RE = re.compile(r"\beliminated the only tag\b|\beliminated the\b.{0,20}\btags?\b", re.I)
REDUCED_RE = re.compile(r"\breduction of (?P<count>one|two|three|\d+)\s*\(?(?P<digits>\d*)", re.I)
_WORD_NUMBERS = {"one": 1, "two": 2, "three": 3, "four": 4}
#: Horizontal distance (pt) within which two hunt-number words share a printed column.
COLUMN_TOLERANCE_PT = 25.0

#: The 2025 & 2026 MSG brochure: moose 3xxx, Rocky Mountain bighorn 5xxx, California
#: bighorn 7xxx, mountain goat 6xxx (§12 T-25, confirmed against every table title).
MSG = Brochure(
    kind="moose_sheep_goat",
    species_by_prefix={
        "3": "moose",
        "5": "bighorn_rocky",
        "7": "bighorn_california",
        "6": "mountain_goat",
    },
    species_words=(
        ("california bighorn", "bighorn_california"),
        ("california", "bighorn_california"),
        ("rocky mountain bighorn", "bighorn_rocky"),
        ("rocky mountain", "bighorn_rocky"),
        ("mountain goat", "mountain_goat"),
        ("goat", "mountain_goat"),
        ("moose", "moose"),
    ),
    header=("hunt no.", "controlled hunt area", "tags", "season dates"),
    title_marker=TITLE_MARKER_RE,
    total_re=TOTAL_RE,
    legend_re=LEGEND_RE,
    legend_meanings=LEGEND_MEANINGS,
    # `*` says the area is a portion of its unit **or** includes other units — which of
    # the two is answered by the resolved polygon (T-17b), never guessed here — and the
    # wilderness marker only tells the hunter what the terrain is like (§5.1).
    mark_codes={"wilderness_terrain": "wilderness_terrain"},
    area_re=AREA_RE,
    footer_re=FOOTER_RE,
    area_entry=AREA_ENTRY,
    heading_re=HEADING_RE,
    both_years=True,
    read_text=lambda page, spec, carried, year: rows_from_text(page, spec, carried, year),
)


# ───────── path B: the hunt-number words and their own printed line ─────────


def _columns(hunts: Sequence[Word]) -> list[float]:
    """The x of each hunt-number column, clustered from the numbers themselves.

    Path B never reads the header: MSG prints it over two lines (`Controlled` above
    `Hunt Area`) and twice across the page, so the records are located by the only
    thing that marks one — a four-digit hunt number printed at the left of its row.
    """
    out: list[float] = []
    for x in sorted(w.x0 for w in hunts):
        if not out or x - out[-1] > COLUMN_TOLERANCE_PT:
            out.append(x)
    return out


def rows_from_text(
    page: Page, spec: Brochure, carried: TitleInfo | None, license_year: int
) -> tuple[list[HuntRow], TitleInfo | None]:
    """Path B (§8.7 principle 3): the same rows from word positions and one regex.

    Every hunt number is the anchor of its own record: the words that overlap it
    vertically and sit between it and the next hunt-number column are the record's
    area, tags, dates and notes, read with a regex that knows nothing of the ruled
    table. A record whose text does not parse is left out — path A's row then has no
    peer and is staged for review (`two_path_agreement`).
    """
    hunts = sorted(
        (w for w in page.words if re.fullmatch(r"\d{4}", w.text) and spec.species_of(w.text)),
        key=lambda w: (w.x0, w.top),
    )
    if not hunts:
        return [], carried
    columns = _columns(hunts)
    out: list[HuntRow] = []
    for hunt in sorted(hunts, key=lambda w: (_column_of(w, columns), w.top)):
        limit = next((x for x in columns if x > hunt.x1), float("inf"))
        cells = [
            w
            for w in page.words
            if w is not hunt and hunt.x1 <= w.x0 and w.x1 <= limit and _same_line(w, hunt)
        ]
        row = _row_from(hunt.text, join_words(sorted(cells, key=lambda w: w.x0)), page, spec)
        if row is not None:
            out.append(row)
    return out, carried


def _column_of(word: Word, columns: Sequence[float]) -> int:
    return min(range(len(columns)), key=lambda i: abs(columns[i] - word.x0))


def _same_line(word: Word, anchor: Word) -> bool:
    """Do the two words sit on the same printed line (half their height overlaps)?"""
    height = min(word.bottom - word.top, anchor.bottom - anchor.top) or 1.0
    overlap = min(word.bottom, anchor.bottom) - max(word.top, anchor.top)
    return overlap / height >= 0.5


#: `1-1* 10 Sep 15 - Dec 1 Chronic Wasting Disease …` — one record, right of its number.
ROW_RE = re.compile(
    r"^(?P<area>\S+)\s+(?P<tags>\d{1,3}|Unlimited)\s+"
    r"(?P<dates>[A-Z][a-z]{2}\.?\s*\d{1,2}\s*[-–]\s*[A-Z][a-z]{2}\.?\s*\d{1,2})"
    r"(?P<notes>.*)$",
    re.IGNORECASE,
)


def _row_from(number: str, text: str, page: Page, spec: Brochure) -> HuntRow | None:
    match = ROW_RE.match(text.strip())
    if match is None:
        return None
    area = spec.area_re.match(match.group("area"))
    return HuntRow(
        hunt_number=number,
        area=area.group("area") if area else match.group("area"),
        marks=(area.group("mark"),) if area and area.group("mark") else (),
        tags_text=match.group("tags"),
        dates_texts=(match.group("dates"),),
        notes=match.group("notes").strip(),
        page=page.number,
    )


# ───────── the corrections pages (§12 T-25) ─────────


def corrections(state: DocState, spec: Brochure) -> list[dict[str, Any]]:
    """Every `<SPECIES> – HUNT NO. NNNN (AREA X)` item printed in the brochure.

    The item's own prose says what changed (a tag eliminated, a reduction of one), and
    `applied_to` then checks the parsed row against it: the published tables are the
    corrected ones, so a hunt whose only tag was eliminated must read 0.
    """
    out: list[dict[str, Any]] = []
    for page_number, text in sorted(state.page_text.items()):
        if "CORRECTION" not in text.upper():
            continue
        for match in CORRECTION_RE.finditer(text):
            body = text[match.end() : match.end() + 400]
            out.append(
                {
                    "page": page_number,
                    "species": spec.species_in(match.group("species")),
                    "hunt": match.group("hunt"),
                    "area": (match.group("area") or "").strip() or None,
                    "text": re.sub(r"\s+", " ", body.split("The Updated Information")[0]).strip(),
                }
            )
    return out


def applied_to(item: dict[str, Any], rows: Sequence[HuntRow]) -> dict[str, Any]:
    """One correction item judged against the parsed rows it names."""
    found = [r for r in rows if r.hunt_number == item["hunt"]]
    quota = found[0].tag_quota if found else None
    zeroed = bool(ZEROED_RE.search(item["text"]))
    reduction = _reduction(item["text"])
    agrees = bool(found) and (quota == 0 if zeroed else True)
    return {
        **item,
        "matched": bool(found),
        "parsed_tags": quota,
        "zeroed": zeroed,
        "reduced_by": reduction,
        "agrees": agrees,
    }


def _reduction(text: str) -> int | None:
    match = REDUCED_RE.search(text)
    if match is None:
        return None
    digits = match.group("digits")
    if digits:
        return int(digits)
    word = match.group("count").lower()
    return _WORD_NUMBERS.get(word, int(word) if word.isdigit() else None)


def tags_removed_by(items: Sequence[dict[str, Any]], rows: Sequence[HuntRow]) -> int:
    """Tags a correction took out of the printed header total, for `tag_checksum`.

    Only a hunt the correction **zeroed** is counted: the 2025 brochure reprints its
    sub-header totals with every tag reduction already in them (the Rocky Mountain
    5007 reduction is inside the printed 77), but a hunt whose only tag was eliminated
    still reads 0 under a header that counts it — that is the California 7 → 5 gap
    §12 T-25 names. A reduction the header did *not* absorb therefore leaves
    `tag_checksum` failing on purpose, for a human to read (D3).
    """
    numbers = {r.hunt_number for r in rows}
    return sum(1 for i in items if i["hunt"] in numbers and i.get("zeroed"))


# ───────── tag totals: scope totals, the sheep umbrella, corrections ─────────


def tag_totals(
    state: DocState,
    spec: Brochure,
    species: str,
    items: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    """One entry per printed total, with everything that explains its gap (§8.7).

    MSG prints a *scope* total above each table (`- 510 Tags`, `16 Tags`) and, for
    sheep, an *umbrella* total above both species (`100 Total Tags Including Special
    Lottery and Auction Tag`) whose extra tags have no table row at all.
    """
    scopes = controlled.checksum_scopes(state, spec, species)
    out: list[dict[str, Any]] = []
    for title, amount, rows in scopes:
        removed = tags_removed_by(items, rows)
        entry: dict[str, Any] = {
            "title": title,
            "header": amount,
            "parsed": sum(r.tag_quota or 0 for r in rows),
            "unlimited": sum(1 for r in rows if r.tag_quota is None),
            "rows": len(rows),
        }
        if removed:
            entry["accounted"] = removed
            entry["explained"] = [
                f"correction p.{i['page']}: hunt {i['hunt']} ({i['area']})"
                for i in items
                if i["hunt"] in {r.hunt_number for r in rows}
                and (i.get("zeroed") or i.get("reduced_by"))
            ]
        out.append(entry)
    umbrella = _umbrella(state, spec, species)
    if umbrella is not None:
        out.append(umbrella)
    return out


def _umbrella(state: DocState, spec: Brochure, species: str) -> dict[str, Any] | None:
    """The `N Total Tags Including …` total, summed over every species it covers.

    The sheep umbrella is printed once, above the Rocky Mountain table, but covers the
    California hunts two pages later as well, so it is reported on the section whose
    title prints it and summed over **every** species it names.
    """
    for _page, title, _amount in state.totals:
        match = UMBRELLA_RE.search(title)
        if match is None or spec.species_in(title) != species:
            continue
        extras = sorted(set(EXTRA_TAG_RE.findall(match.group("extras"))))
        covered = [
            row
            for other in sorted(_umbrella_species(spec, title))
            for row in all_rows(state, spec, other)
        ]
        return {
            "title": re.sub(r"\s+", " ", title).strip(),
            "header": int(match.group(1).replace(",", "")),
            "parsed": sum(r.tag_quota or 0 for r in covered),
            "unlimited": sum(1 for r in covered if r.tag_quota is None),
            "rows": len(covered),
            "accounted": len(extras),
            "explained": [f"{name} tag (no table row)" for name in extras],
        }
    return None


def _umbrella_species(spec: Brochure, title: str) -> set[str]:
    """Every species the umbrella title covers (`Bighorn Sheep` → both bighorns)."""
    low = title.lower()
    if "bighorn sheep" in low:
        return {"bighorn_rocky", "bighorn_california"}
    found = spec.species_in(title)
    return {found} if found else set()


# ───────── staged rows, checks and registration ─────────

#: A hunt a correction closed: kept for history, never shown as open (§12 T-25).
CLOSED_NOTE = "Hunt closed: 0 tags for this license year"


def all_rows(state: DocState, spec: Brochure, species: str) -> list[HuntRow]:
    """Every parsed row of one species, in printed order."""
    return [row for row, _ in controlled.rows_for(state, spec, species)]


def parse_msg_page(
    page: Page, ctx: ParseContext, *, spec: Brochure, species: str
) -> Iterator[ParsedSeason]:
    """One species' hunts from a page, with a 0-tag hunt staged as closed."""
    for season in controlled.parse_page(page, ctx, spec=spec, species=species):
        if season.tag_quota == 0:
            codes = [*season.restrictions]
            if "see_notes" not in codes:
                codes.append("see_notes")
            yield replace(
                season,
                restrictions=codes,
                hunt_class="other",
                notes=f"{CLOSED_NOTE}. {season.notes or ''}".strip(),
            )
        else:
            yield season


def finalize(ctx: ParseContext, *, spec: Brochure, species: str) -> SectionFacts | None:
    """The shared controlled-hunt facts plus MSG's corrections and tag totals."""
    facts = controlled.finalize(ctx, spec=spec, species=species)
    if facts is None:
        return None
    state = controlled.state_for(ctx)
    every_row = [
        row
        for other in dict.fromkeys(spec.species_by_prefix.values())
        for row in all_rows(state, spec, other)
    ]
    items = [applied_to(item, every_row) for item in corrections(state, spec)]
    mine = [i for i in items if i["species"] == species]
    return replace(
        facts,
        tag_totals=tag_totals(state, spec, species, mine),
        corrections=mine or None,
    )


def register_brochure(spec: Brochure) -> list[ParserSpec]:
    """One `controlled_<species>` parser per MSG species, plus its hunt-area sections."""
    out: list[ParserSpec] = []
    for species in dict.fromkeys(spec.species_by_prefix.values()):
        section = spec.section(species)
        out.append(
            register(
                ParserSpec(
                    name=section,
                    kind=spec.kind,
                    match=controlled.matches_upright,
                    fn=partial(parse_msg_page, spec=spec, species=species),
                    section=section,
                    begin=controlled.begin,
                    finalize=partial(finalize, spec=spec, species=species),
                )
            )
        )
    return out + hunt_areas.register_brochure(spec)


PARSERS = register_brochure(MSG)
