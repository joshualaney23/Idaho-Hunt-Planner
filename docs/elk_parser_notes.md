# Elk A/B zone pages — parser spike (T-12a)

Spike script: `docs/spike_elk_parser.py` (run it against the real 2026 Big Game
brochure; it imports nothing from `planner/` and ships no data). Everything below
is measured output from the 2026 brochure
(`data/regs/2026/feb85a9c…pdf`, 125 pages), not a guess.

```
.venv/bin/python docs/spike_elk_parser.py data/regs/2026/feb85a9c….pdf
.venv/bin/python docs/spike_elk_parser.py <pdf> --dump="Boise River"
```

## Headline result

| measurement | 2026 |
|---|---|
| elk zone pages (found by content, not page numbers) | physical **38–50** (13 pages) |
| zone blocks parsed | **28 / 28** (the brochure's own `ELK MANAGEMENT ZONES` index lists 28) |
| season rows (zone × tag × weapon/sex × date range) | **95** (A 62 · B 33) |
| date-token accounting | **95 of 106** printed tokens become seasons; the other **11 are the "Motorized Hunting Rule … August 30 - November 14" line** printed above the calendar — not a season |
| zone → unit list: page header vs index page vs GIS `elk_zone` | **28 / 28 agree** |
| words of the zone tables not assigned to any region | 17 (the page-level `ELK Notes:` legend on p.50) |

So the pages are fully machine-readable: no zone needs manual entry, and the
"Smoky – Bennett"-style spelling mapping table T-12's text anticipates is **not
needed for 2026** — the printed names already match GIS once punctuation and case
are ignored (`Smoky-Bennett`). Keep a normalisation function, not a mapping table,
and fail loudly when a printed name has no GIS zone.

## How to read these pages

The pages are landscape calendar artwork rotated 90° (`upright == False` for
96 % of their chars; `page.rotation` is **0**, so routing on `page.rotation` finds
nothing — route on the non-upright char share, as `extract.py` already does).

1. **Rotated frame.** Read text with `extract_text(line_dir_rotated="ltr",
   char_dir_rotated="btt")` and map every object into a rotated frame:
   `rx = page.height - bottom` (position along a printed line), `ry = x0`
   (printed line order, top → bottom). Rules, rects and words all map the same way.
2. **Never pass `extra_attrs=` to `extract_words()` on these pages.** Per-character
   size jitter then splits every single character into its own "word"
   (`P a n h a n d l e Z o n e`). Without it the words come out clean.
3. **Drop the margin strap first.** The vertical "Idaho Big Game 2026 Seasons &
   Rules … idfg.idaho.gov" runs down the left edge at `rx ≈ 20–30` and merges into
   the zone-title line, which turned three zone names into `Idaho Lemhi`,
   `Idaho Pioneer`, `Idaho Selway`. The table's own `rx` extent comes from the
   colored rects (below), so clip to it before grouping lines.
4. **The block skeleton is drawn, not inferred.** Each zone block is:
   * a **title band** rect, fill `(0.706, 0.727, 0.661)` — zone name + unit list,
     plus optional blue preamble lines (Motorized Hunting Rule, CWD transport);
   * a **month header strip** of rects, fill `(0.815, 0.782, 0.66)`, split into
     5 columns: a narrow stub (the `A Tag` / `B Tag` labels) + 4 month columns.
     The same fill is reused to shade a whole tag row, so take the column edges
     only from the rects whose `ry0` equals the strip's top;
   * **hairlines** (`page.lines`, `linewidth 0.5`) between rows.
5. **Row bands, then entries — never per-column cells.** Treat an `ry` where the
   hairlines cover ≥ 60 % of the table width as a band edge. Inside a band, cut
   each printed line at horizontal gaps > ~9 pt and cluster the segments by
   horizontal overlap; each cluster is one printed entry.
   **This is the trap that costs seasons:** an entry is routinely centred across
   two month columns, so a per-column cell model splits the token
   (`Oct 10` | `- Nov 3`) and loses the season silently. It cost 11 of 95 rows
   (every one of them a B-tag season) before the band model replaced it.
   The month column stays useful as a *cross-check* on the parsed dates, not as a
   boundary.
6. **Tag = nearest `A Tag` / `B Tag` label by vertical centre**, as in the
   controlled tables. A zone that offers one tag prints
   `No B Tags in this Zone — See Controlled Hunts`; that is a statement, not a
   season (2026: no A tag in Owyhee; no B tag in Lemhi, Beaverhead, Owyhee,
   Island Park, Snake River, Bannock). Two zones print
   `… Zone (Units …) — Controlled Hunts Only` and have no calendar at all
   (Hells Canyon, Owyhee).

## Vocabulary measured across all 28 zones

* **Weapon:** `Any weapon` 45 · `Archery only` 31 · `Muzzleloader only` 14 ·
  **`Short range weapons only`** (appears inside entries, e.g. Big Desert,
  Snake River, McCall — maps to §5.1 `short_range`).
* **Sex:** `antlerless only` · `any elk` · `antlered only` ·
  `brow-tined bulls only` · `spike or antlerless` · `spike only`.
  Match the **longest alternative first**: the spike's regex matched bare
  `antlerless` on Boise River's `antlerless or spike only` — a wrong, publishable
  value with no red flag. The sex vocabulary must be ordered and complete, and an
  unmatched sex phrase has to fail the row (D3).
* **Unit restriction inside an entry:** `Unit 39 only:` / `Units 21, 21A and 36B
  only:` / `Private land in Unit 32 only:` / `Portion of Unit 76 only` →
  `season_units` is the *intersection* of the zone's units with the entry's units,
  plus `private_land_only` / `portion_of_unit` restrictions.
* **One entry can carry two sexes, split by unit:** Middle Fork prints
  `Units 20A and 26: antlered only   Unit 27: brow-tined bulls only` under one
  date range. The row model must allow one printed window to produce several
  rows with different unit sets and sexes.
* **Note references:** `See Note A below` (per-block legend, letters restart per
  zone), `See Note 1/2/3/4, Page 49` (document-level legend on the last zone page),
  `See archers caution Page 36`. The trailing note line belongs to the entry
  printed above it — the spike currently drops it (known gap, see below).
* **Capped-tag quotas:** 20 zones print `Note: 1,088 B Tag Quota Available
  First-Come, First-Served. See Note 2, Page 49.` in a full-width sub-row
  (Lolo A 404 / B 1,088 · Dworshak 1,171 / 2,380 · Elk City 628 / 1,432 ·
  Selway 647 / 1,067 · Middle Fork 1,551 / 1,636 · Salmon B 2,507 ·
  Sawtooth 1,000 / 1,800 · Pioneer B 500 · Smoky-Bennett B 600 · South Hills B 500 ·
  Big Desert B 350 · Bear River B 550 · Diamond Creek 1,836 / 150).
  These are tag-quota facts, not seasons; they need a home in T-12b (a zone-level
  note on the rows, or a new column) and they are the only free checksum these
  pages offer.

## Checks this section can support (§8.7)

| check | how it is measured here | 2026 |
|---|---|---|
| `date_token_accounting` | every `Mon D - Mon D` token inside the zone tables is consumed by exactly one season | 95/95 after excluding the preamble |
| *(new)* motorized-rule accounting | the `Motorized Hunting Rule … , See Pages 104 - 106` preamble is a **method window**, not a season — account for it exactly like T-13's `method_window_accounting` or it shows up as 11 leftover tokens | 11 lines, 11 tokens |
| `zone_unit_match` (T-12's own AC) | printed header units == index-page units == GIS `elk_zone` membership | 28/28 |
| `two_path_agreement` | path A = this geometry model; path B = the plain rotated `extract_text()` line scan for `(Units …)` headers and date tokens per zone | to build in T-12b |
| `unit_tokens_valid` | header units and in-entry unit restrictions against `units` | 0 unknown tokens |

## What T-12b shipped (`planner/importer/elk.py`)

Section `elk_zones`, one parser, registered for `big_game`. Real 2026 brochure:
**28/28 zones · 95 printed windows · 98 staged rows · every §8.7 check `pass`, both
extraction paths agreeing 98/98.** (98 > 95 because Middle Fork's three windows each
split into two rows by unit, see gap 8 below.)

Five findings the spike had not seen, each of which silently lost or mis-tagged
seasons before it was fixed:

1. **A tag row is not a hairline band.** The brochure also rules a hairline *inside* a
   row, between its shelves of entries: Panhandle's A row is two bands (83–146 and
   146–214) with one `A Tag` label centred across both, while its B row is one band.
   So a band takes the label printed inside it and otherwise the nearest one — which is
   exactly why the label is centred on its row.
2. **Group printed lines by their centre, not by box overlap.** `textlayer.group_lines`
   is overlap-based (right for an upright table) and here chains the zone title into the
   preamble under it — "Idaho Motorized Hunting Rule Applies Lemhi in Units Zone 29,
   37A (Units and 51, …)", a title no regex can find, which loses the whole Lemhi block
   — and merges a row's three lines into one, scrambling entry segmentation.
3. **Entries must be clustered in two dimensions.** Horizontal overlap alone fuses two
   entries printed one under the other in the same month column (Panhandle's
   `Dec 2 - Dec 8` above `Dec 10 - Dec 16`), and the first entry's label then governs
   the second's dates. Use the **centre distance** between printed lines (~10.5 pt
   inside an entry, ≥ 20 pt between stacked entries); the white gap between their boxes
   is ~11 pt either way and cannot tell them apart.
4. **The capped-tag quota sub-row reads exactly like a note legend.**
   `Note: 1,551 A Tag Quota Available First-Come, First-Served.` is printed *between*
   the A and B rows, so treating it as the legend start cuts the block off above its B
   row and silently drops every B-tag season of that zone. It is also an `A`+`Tag` word
   pair mid-page, which breaks a naive tag-label scan.
5. **The calendar ends where its drawn skeleton ends.** Below it sit the block's note
   legend, the footer and the sideways `ELK` tab; without that bound they read as
   B-tag entries. In path B the same role is played by the month strip's own *words*
   (the margin strap prints a word on that line too: "Big August/September October …").

### The two paths

* **Path A** reads the drawn artwork: title band and month strip (fills
  `(0.706,0.727,0.661)` / `(0.815,0.782,0.66)`) for the table's extent and columns,
  0.5 pt hairlines for the row bands.
* **Path B** never touches a rect or a rule: the month strip is found by its words, rows
  are the sets of entries whose vertical spans overlap, and each row takes the tag label
  that sits inside its span. (Nearest-label-by-centre is **wrong** for a tall row:
  McCall's A row is 108 pt deep, so its lowest entry sits closer to the B label.)

Both then interpret an entry with the same code — what the paths disagree about is the
*geometry*, which is where these pages go wrong.

## Known gaps, as closed in T-12b

1. ✅ **Trailing note lines are no longer dropped.** An entry's lines are grouped
   around the date line they belong to, and a note printed *under* a date
   (`See Note A below`, `On or within 1 mile of private … Note 3, Page 49`) qualifies
   the window above it.
2. ✅ **The sex vocabulary is ordered longest-first** and complete, so
   `antlerless or spike only` is `either` + `antlerless_only` + `spike_only`, never
   plain `antlerless`. A phrase outside it stages `sex = na` + `see_notes` and reports
   the entry text, which fails the section's date accounting (D3: never publish a guess).
3. ✅ **`Short range weapons only` is in the weapon vocabulary** (§5.1 `short_range`);
   2026 uses it in Big Desert, Snake River and McCall.
4. ✅ **The month columns are a cross-check** (`month_column_agreement`), compared
   against every month the window runs through — not just its ends, or Boise River's
   `Oct 1 - Dec 31` under the November column reads as a mismatch.
5. ⬜ **The page-level `ELK Notes:` legend (p. 50, Notes 1–4) is still not resolved.**
   A row that points at it carries `see_notes`, so it stays yellow and goes to review
   rather than being published as if nothing qualified it. Resolving the legend text
   onto rows is a separate, safe improvement; leaving it unresolved never loses
   information, it only costs a review.
6. ✅ **Capped-tag quotas have a home**: `seasons.tag_quota`, written onto every row of
   that zone and tag, with `tag_quota_accounting` asserting each printed number landed
   on a tag the zone offers (2026: 20 quotas).
7. ✅ **`season_units` comes from `zone_units`** (seeded from GIS in T-04), narrowed
   by any in-entry unit restriction. The printed header agreed with GIS for all 28
   zones in 2026, so the "brochure rewrites `zone_units`" path stays cold on real data
   and is covered by a test that deletes a unit from the seed instead.
8. ✅ **One window, two sexes split by unit** (new in T-12b): Middle Fork prints
   `Units 20A and 26: antlered only   Unit 27: brow-tined bulls only` under one date
   range, and it becomes **one row per clause** — merging them would publish brow-tined
   bulls as plain antlered elk in two units and antlered elk as brow-tined in a third.
   `date_token_accounting` therefore counts *windows*, not rows.

## Hand verification (against the page images)

Pages were rendered with `pypdfium2` at scale 3 and rotated −90° for reading. The
T-12b rows below were transcribed off the page image **first** and then compared with
the parser, never the other way round.

| zone | page | T-12a (spike) | T-12b (shipped parser) |
|---|---|---|---|
| Panhandle | 38 | 9 rows match; trailing note lines missing (gap 1) | 9 rows match, notes attached. The `Dec 10 - Dec 16` entry is an **A**-tag season: it sits between the `A Tag` label and the only full-width rule before the `B Tag` label. A vision read of the page put it in the B row — the drawn geometry is the arbiter, not an eyeball. |
| Middle Fork | 41 | — | 1 A window + 2 B windows, each splitting into two rows by unit (20A, 26 antlered · 27 brow-tined); quotas 1,551 / 1,636 read; `Sep 15 - Sep 30` confirmed digit by digit. |
| Lemhi | 43 | 4 rows match (gap 1) | 4 rows match, `No B Tags in this Zone` recorded as a statement, and Beaverhead's B-tag `Unit 30 only` hunt — printed directly under a tall A row — lands in the B row. |
| Boise River | 46 | 4 rows match; found gaps 2 and 4 | 4 rows match, including `antlerless or spike only` → `either` + `antlerless_only` + `spike_only`, and `Oct 1 - Dec 31` under the November column. |
