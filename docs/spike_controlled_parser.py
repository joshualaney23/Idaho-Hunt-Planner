"""Spike for T-17a / T-22: parse 2026 IDFG controlled-hunt tables and run accuracy invariants.

Usage:
    curl -LO https://idfg.idaho.gov/sites/default/files/seasons-rules-big-game-2026.pdf
    pip install pdfplumber
    python docs/spike_controlled_parser.py seasons-rules-big-game-2026.pdf [ch25.json]

ch25.json (optional) = IDFG GIS Controlled Hunts layer attributes, from
  https://gisportal-idfg.idaho.gov/hosting/rest/services/Hunting/MapServer/4/query
  ?where=Year%3D2025&outFields=BigGame,HuntArea&returnGeometry=false&f=json&resultRecordCount=2000

Not production code. Proves: superscript filtering, footnote flags, title carry-forward,
tag-total checksum, hunt-number completeness, GIS hunt-area matching. See DESIGN.md §2, §8.7.
"""
import collections
import json
import re
import sys

import pdfplumber

SUPERSCRIPT_MAX_PT = 7.0  # body text is 10 pt; footnote markers a/b are 5.8 pt
SPECIES = {"1": "deer", "2": "elk", "4": "pronghorn", "8": "bear"}
DATE = re.compile(r"^([A-Z][a-z]{2})\s+(\d{1,2})\s*-\s*([A-Z][a-z]{2})\s+(\d{1,2})$")
TOTAL = re.compile(r"\(([\d,]+) Tags")


def pages_for(pdf):
    """Pages whose text contains a controlled-hunt table header (no hard-coded page numbers)."""
    for i, p in enumerate(pdf.pages):
        if "Hunt No." in (p.extract_text() or ""):
            yield i + 1, p


def footnote_markers(page, bbox):
    """Superscript chars (a/b) inside one table cell's bbox, read from the UNFILTERED page.
    pdfplumber crops keep absolute coordinates, so a cell bbox from the filtered/cropped
    page can be applied to the original page directly. Pad vertically: superscripts sit
    above the baseline and can poke out of the cell's top edge."""
    x0, top, x1, bottom = bbox
    return sorted({c["text"] for c in page.chars
                   if c["size"] < SUPERSCRIPT_MAX_PT and c["text"] in "ab"
                   and x0 - 1 <= c["x0"] <= x1 + 1 and top - 4 <= c["top"] <= bottom})


def main(path, gis_path=None):
    pdf = pdfplumber.open(path)
    out, title, totals = [], "", {}
    for pn, page in pages_for(pdf):
        body = page.filter(lambda o: o.get("object_type") != "char"
                           or o.get("size", 10) >= SUPERSCRIPT_MAX_PT)
        body = body.crop((40, 0, page.width - 40, page.height))
        for t in body.find_tables():
            rows = t.extract()
            if not rows:
                continue
            first = (rows[0][0] or "")
            if not first.startswith("Hunt No"):
                title = " ".join(first.split("\n")[:3])
                if m := TOTAL.search(title):
                    totals[title] = int(m.group(1).replace(",", ""))
            for r, cells in zip(rows, t.rows):
                hn = (r[0] or "").strip()
                if not re.fullmatch(r"\d{4}", hn):
                    continue
                area_raw = (r[1] or "").replace("\n", " ")
                area = re.sub(r"\s*\(See pg \d+\)", "", area_raw).strip()
                see_pg = re.search(r"See pg (\d+)", area_raw)
                marks = footnote_markers(page, cells.cells[1]) if cells.cells[1] else []
                date_txt = re.sub(r"\s*-\s*", " - ", (r[3] or "").strip())
                m = DATE.match(date_txt)
                out.append(dict(
                    page=pn, title=title, hunt_number=hn, species=SPECIES.get(hn[0], "?"),
                    hunt_area=area, includes_other_units="a" in marks, portion_only="b" in marks,
                    description_page=int(see_pg.group(1)) if see_pg else None,
                    tags=(r[2] or "").strip(), dates_raw=r[3], date_ok=bool(m),
                    notes=(r[4] or "").replace("\n", " ").strip(),
                ))

    # second, independent extraction path: raw text layer (superscripts kept inline, e.g. "1-1a")
    raw = {}
    for _, page in pages_for(pdf):
        for m in re.finditer(r"\b([1248]\d{3})\s+(\d{1,2}[A-C]?(?:-\d+X?)?)([ab]?)\b", page.extract_text() or ""):
            raw[m.group(1)] = (m.group(2), m.group(3))
    parsed = {o["hunt_number"]: o for o in out}
    print("hunt numbers in text but not parsed:", sorted(set(raw) - set(parsed)) or "none")
    disagree = []
    for hn, o in parsed.items():
        if hn not in raw:
            continue  # text layer merged the line with a neighbour; table path is authoritative
        area, mark = raw[hn]
        mine = "a" if o["includes_other_units"] else "b" if o["portion_only"] else ""
        if area.upper() != o["hunt_area"].upper() or mark != mine:
            disagree.append((hn, f"text={area}{mark}", f"table={o['hunt_area']}{mine}"))
    print(f"table vs text disagreements: {len(disagree)}", disagree[:15])

    # ---------- invariants ----------
    print(f"controlled hunts parsed: {len(out)}", dict(collections.Counter(o["species"] for o in out)))
    dup = [k for k, v in collections.Counter(o["hunt_number"] for o in out).items() if v > 1]
    print("duplicate hunt numbers:", dup or "none")
    bad = [o for o in out if not o["date_ok"]]
    print("unparsed date cells:", len(bad), [(o["hunt_number"], o["dates_raw"]) for o in bad])
    print("footnotes: a(includes other units)=%d  b(portion only)=%d" % (
        sum(o["includes_other_units"] for o in out), sum(o["portion_only"] for o in out)))
    # hunt-number gaps inside each species block
    for sp in sorted({o["hunt_number"][0] for o in out}):
        n = sorted(int(o["hunt_number"]) for o in out if o["hunt_number"][0] == sp)
        gaps = sorted(set(range(n[0], n[-1] + 1)) - set(n))
        print(f"species {sp}: {n[0]}..{n[-1]}  gaps={gaps[:15]}{'…' if len(gaps) > 15 else ''}")
    # tag checksum vs table headers ("13,226 Tags Plus Unlimited Tags")
    for t, total in totals.items():
        sp = "1" if "Deer" in t else "2" if "Elk" in t else "4" if "Pronghorn" in t else "?"
        s = sum(int(o["tags"].replace(",", "")) for o in out
                if o["hunt_number"][0] == sp and o["tags"].replace(",", "").isdigit())
        print(f"checksum {t[:45]!r}: header={total} parsed_sum={s} {'OK' if s == total else 'MISMATCH'}")
    if gis_path:
        gis = collections.defaultdict(set)
        for a in (f["attributes"] for f in json.load(open(gis_path))["features"]):
            gis[a["BigGame"].lower()].add(a["HuntArea"].strip().upper())
        miss = [o for o in out if o["hunt_area"].upper() not in gis.get(o["species"], set())]
        print(f"GIS hunt-area match: {len(out) - len(miss)}/{len(out)}",
              [(o["hunt_number"], o["hunt_area"]) for o in miss][:20])
    json.dump(out, open("controlled_2026.json", "w"), indent=1)


if __name__ == "__main__":
    main(*sys.argv[1:])
