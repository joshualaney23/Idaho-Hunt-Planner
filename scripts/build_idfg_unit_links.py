"""Build `static/data/idfg_units.json`: GMU id → IDFG Hunt Planner unit page id (T-08).

The IDFG Hunt Planner's unit pages use internal ids, not unit numbers
(`unit.aspx?ID=5` is Unit 4A, `ID=78` is Unit 61), so the Unit Brief can't build the
link from the unit id alone. This script reads the `<h1>Unit 4A</h1>` heading of
every `unit.aspx?ID=N` page and writes the mapping. Rerun it only if IDFG renumbers
its pages; `--check` exits 1 when the committed file is stale.

    python scripts/build_idfg_unit_links.py           # fetch + rewrite the JSON
    python scripts/build_idfg_unit_links.py --check   # compare with the committed file
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "static" / "data" / "idfg_units.json"
SEED = REPO / "static" / "data" / "units_seed.json"
PAGE = "https://idfg.idaho.gov/ifwis/huntplanner/unit.aspx?ID={}"
H1 = re.compile(r"<h1[^>]*>\s*Unit\s+([0-9]+[A-Z]?)\s*</h1>", re.I)
MAX_ID = 140  # the ids are dense (1..100 in 2026); stop after a long run of misses
MISS_RUN = 25


def fetch(page_id: int) -> str | None:
    req = urllib.request.Request(
        PAGE.format(page_id), headers={"User-Agent": "Mozilla/5.0 (ihp unit-link builder)"}
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        html = r.read().decode("utf-8", "replace")
    m = H1.search(html)
    return m.group(1).upper() if m else None


def build() -> dict[str, int]:
    out: dict[str, int] = {}
    misses = 0
    for page_id in range(1, MAX_ID + 1):
        unit = fetch(page_id)
        time.sleep(0.2)  # be polite
        if unit is None:
            misses += 1
            if misses >= MISS_RUN:
                break
            continue
        misses = 0
        if unit in out:
            raise SystemExit(f"unit {unit} appears twice (ids {out[unit]} and {page_id})")
        out[unit] = page_id
    return out


def check_coverage(links: dict[str, int]) -> list[str]:
    """Every huntable unit in units_seed.json (all but Yellowstone) must have a page."""
    units = [u["id"] for u in json.loads(SEED.read_text())]
    return [u for u in units if u != "YNP" and u not in links]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Build static/data/idfg_units.json")
    ap.add_argument("--check", action="store_true", help="exit 1 if the committed file is stale")
    args = ap.parse_args(argv)
    links = build()
    missing = check_coverage(links)
    if missing:
        print(f"no IDFG unit page for: {', '.join(missing)}", file=sys.stderr)
        return 1
    body = json.dumps(dict(sorted(links.items(), key=lambda kv: kv[1])), indent=0) + "\n"
    if args.check:
        stale = not OUT.exists() or OUT.read_text() != body
        print("stale" if stale else "up to date")
        return 1 if stale else 0
    OUT.write_text(body)
    print(f"wrote {OUT.relative_to(REPO)}: {len(links)} units")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
