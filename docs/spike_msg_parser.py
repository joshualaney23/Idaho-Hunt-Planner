"""Spike for T-25: parse IDFG Moose / Bighorn Sheep / Mountain Goat controlled hunts and run invariants.

Usage:
    curl -LO https://idfg.idaho.gov/sites/default/files/seasons-rules-moose-sheep-goat-2025-26.pdf
    pip install pdfplumber
    python docs/spike_msg_parser.py seasons-rules-moose-sheep-goat-2025-26.pdf [ch25.json]

ch25.json = GIS Controlled Hunts attributes (see spike_controlled_parser.py docstring).

Differences from the Big Game brochure that this proves out (DESIGN.md §2):
  * fake-bold text is printed 4x on some pages -> page.dedupe_chars() is mandatory
  * sheep/goat tables put TWO hunts per row (8 columns) -> locate every "Hunt No." column from the header
  * the California bighorn table is nested inside another table (Hunt No. in column 1, not 0)
  * the full-size '*' is PART of the hunt-area id (GIS stores '1-1*'); superscript a/b are footnotes
  * superscripts are ~6.4 pt but a 7.0 pt 'a' exists on the same page -> use a RELATIVE size threshold
  * sheep header total includes lottery + auction tags that have no table row
"""
import collections
import json
import re
import statistics
import sys

import pdfplumber

SPECIES = {"3": "Moose", "5": "Rocky Mtn Sheep", "7": "California Sheep", "6": "Goat"}  # GIS BigGame names


def body_page(page):
    page = page.dedupe_chars(tolerance=1)
    sizes = [c["size"] for c in page.chars if c["text"].strip()]
    body = statistics.median(sizes) if sizes else 10
    # relative threshold: superscripts are well under body size; keep anything >= 75 % of median
    return page.filter(lambda o: o.get("object_type") != "char" or o["size"] >= 0.75 * body)


def main(path, gis_path=None):
    pdf = pdfplumber.open(path)
    rows, title, totals = [], "", {}
    for pn, page in enumerate(pdf.pages, 1):
        if "Hunt No." not in (page.extract_text() or ""):
            continue
        q = body_page(page).crop((30, 0, page.width - 30, page.height))
        for tb in q.extract_tables():
            starts = None
            for r in tb:
                cells = [(x or "").strip() for x in r]
                joined = " ".join(c for c in cells if c)
                if m := re.search(r"(\d+) (?:Total )?Tags", joined):
                    if "Hunt No" not in joined:
                        title = joined.split("\n")[0]
                        totals[title] = int(m.group(1))
                hs = [k for k, c in enumerate(cells) if c.startswith("Hunt No")]
                if hs:
                    starts = hs
                    continue
                for k in starts or []:
                    if k + 3 < len(cells) and re.fullmatch(r"\d{4}", cells[k]):
                        rows.append(dict(page=pn, hunt_number=cells[k], species=SPECIES[cells[k][0]],
                                         hunt_area=cells[k + 1].replace("\n", ""), tags=cells[k + 2],
                                         dates=cells[k + 3]))

    print(f"hunts parsed: {len(rows)}", dict(collections.Counter(r["species"] for r in rows)))
    print("duplicates:", [k for k, v in collections.Counter(r["hunt_number"] for r in rows).items() if v > 1] or "none")
    bad = [r for r in rows if not re.fullmatch(r"[A-Z][a-z]{2} \d{1,2}\s*-\s*[A-Z][a-z]{2} \d{1,2}", r["dates"])]
    print("unparsed dates:", [(r["hunt_number"], r["dates"]) for r in bad] or "none")

    text_nums = set()
    for page in pdf.pages:
        t = page.dedupe_chars().extract_text() or ""
        if "Hunt No." not in t:  # only hunt-table pages: other pages contain addresses/phone numbers
            continue
        for m in re.finditer(r"(?<!\d)([3567]\d{3})\s+\d", t):
            text_nums.add(m.group(1))
    parsed = {r["hunt_number"] for r in rows}
    print("in text, not parsed:", sorted(text_nums - parsed) or "none")

    def tag_sum(prefixes):
        return sum(int(r["tags"]) for r in rows if r["hunt_number"][0] in prefixes and r["tags"].isdigit())
    for t, total in totals.items():
        print(f"header {t[:70]!r}: {total}")
    print("sums: moose", tag_sum("3"), "(headers 510 + 3) | sheep RM+Cal", tag_sum("57"),
          "(header 100 incl. lottery+auction tags) | goat", tag_sum("6"), "(header 40)")
    late = sum(int(r["tags"]) for r in rows if "5021" <= r["hunt_number"] < "6000" and r["tags"].isdigit())
    print("sub-header checks: RM late rams", late, "(header 16) | California", tag_sum("7"),
          "(header 7; corrections page zeroes 7001 and 7002, 1 tag each -> expect 5)")

    if gis_path:
        gis = collections.defaultdict(set)
        for a in (f["attributes"] for f in json.load(open(gis_path))["features"]):
            gis[a["BigGame"]].add(a["HuntArea"].strip().upper())
        miss = [(r["hunt_number"], r["hunt_area"]) for r in rows if r["hunt_area"].upper() not in gis[r["species"]]]
        print(f"GIS exact match (incl. '*'): {len(rows) - len(miss)}/{len(rows)}", miss or "")
    json.dump(rows, open("msg_hunts.json", "w"), indent=1)


if __name__ == "__main__":
    main(*sys.argv[1:])
