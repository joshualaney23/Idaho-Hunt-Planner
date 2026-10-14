# Idaho Hunt Planner — Design Document

**Status:** Draft v2 · **Author:** Find A Hunt Planner (Hermes) · **Date:** 2026-10-14
**Repo:** https://github.com/joshualaney23/Idaho-Hunt-Planner
**Companion files:** `docs/mockup.html` (working UI mockup with real 2026 deer data), `docs/spike_deer_parser.py` (general-season parser proof of concept), `docs/spike_controlled_parser.py` (Big Game controlled hunts plus accuracy invariants), `docs/spike_msg_parser.py` (moose/sheep/goat controlled hunts plus invariants)

### 0. Decisions (from Josh)

| # | Decision | Effect on the design |
|---|---|---|
| D1 | **Residents only** for now. | No nonresident quotas, fees, or rules anywhere: no resident/nonresident setting, and nonresident-only text isn't parsed into restrictions. The Tags (quota) column is stored for checksums only and isn't shown in the UI. |
| D2 | **Controlled hunts are in core scope (MVP)**, not a later phase. Josh may hold a controlled tag. | The controlled-hunt parser, hunt-area descriptions, unit coverage, and the "add controlled tag by hunt number" flow are all Phase 1 (§10, T-17a/b/c). |
| D3 | **PDF parsing accuracy is the top priority.** Everything else depends on it. | New §8.7 accuracy strategy and ticket T-22: two independent extraction paths must agree, checksums and completeness checks run on every import, every section is checked against hand-verified golden data, and publishing is **blocked** while any check fails. No LLM-extracted data is ever published. |
| D4 | **Every animal that can be hunted under the Big Game and the Upland Game, Turkey & Furbearer regulations is in scope.** | See the species matrix in §5.3. Big Game PDF: deer (mule + white-tailed), elk, pronghorn, black bear, mountain lion, gray wolf. Upland PDF: forest grouse (dusky/ruffed/spruce), California & bobwhite quail, chukar, gray partridge, sage-grouse, sharp-tailed grouse, pheasant, wild turkey, cottontail, snowshoe hare, red squirrel, the furbearers that may be **hunted** (badger, bobcat, red fox), predatory/unprotected animals (year-round), and falconry seasons. **Out:** trapping-only furbearers (beaver, mink, muskrat, otter, marten). (Moose/sheep/goat and migratory birds were added by D7.) |
| D7 | **Moose, bighorn sheep, mountain goat and migratory birds are in scope too**, uploaded from their own brochures. | Two more source documents: **Moose, Bighorn Sheep & Mountain Goat** (2-year brochure, currently "2025 & 2026") and **Migratory Game Bird** (yearly, currently "2026–2027": ducks incl. mergansers, scaup sub-season, Wilson's snipe, American coot, Canada/white-fronted/light geese, swans (controlled), mourning & white-winged dove, American crow, sandhill crane). New tickets T-25 and T-26; species rows in §5.3. |
| D5 | **Friday counts as part of the default weekend.** | Default `weekend_def = 'fri_sun'` (§7.3). Sat–Sun stays available as a setting. |
| D6 | **No land ownership/access layers, ever.** Other apps already do that. | Removed from the roadmap. The map shows GMUs only. |

---

## 1. The problem

> "It's Thursday. I have an Idaho regular deer tag. Where can I legally hunt this weekend?"

Answering that today means reading 100+ pages of IDFG PDFs, cross-referencing unit tables, weapon seasons, permits, sex restrictions and dates. This project is a **small, local web app** that:

1. **Ingests the official IDFG regulation PDFs** once a year (plus mid-year supplements) into a local database.
2. **Stores the tags and permits I hold** for each license year.
3. **Shows an Idaho Game Management Unit (GMU) map** where units I can legally hunt in the chosen date range are **green**, restricted/partial ones are **yellow**, and the rest are grey.
4. **Defaults the date range to the coming weekend**, and lets me change it.

The look and feel follows Treeline Hunt Research IQ's **State Heat Map**: dark forest UI, gold accents, Bebas Neue headings, chip filters, a "Viewing …" breadcrumb, an SVG unit map with hover tooltips, and a sortable table beside it.

### 1.1 Goals
- Answers "where can I hunt X on dates Y with my tags" in under a second, offline.
- Reloading for a new year means **uploading the new PDF, reviewing what was parsed, and clicking Publish**. No code changes.
- Every season shown links back to its **source PDF page** so I can check it.
- Runs on a laptop with one command. No cloud, no accounts, no build step.

### 1.2 Non-goals (v1)
- Draw odds, harvest stats, unit scoring (that's what Treeline does).
- Land ownership / access layers (D6: use another app for that).
- Trapping seasons (D4).
- Mobile-first layout (it should work on a phone, but it's designed for desktop).
- Multi-user / hosted deployment.
- Nonresident hunters (D1).
- Legal advice. The app is a planning aid and always shows a "verify with official regs" disclaimer.

---

## 2. Research findings (these shaped the design)

I tested real sources before writing this. Summary:

| Finding | Evidence | Design consequence |
|---|---|---|
| IDFG publishes official GMU polygons as an ArcGIS REST layer with GeoJSON output: `gisportal-idfg.idaho.gov/hosting/rest/services/Hunting/MapServer/3`. **100 features**, fields `NAME` (unit, e.g. `4A`), `Elk_Zone` (28 zones), plus 2022 boundary updates. | Queried live; 71 KB simplified GeoJSON at `maxAllowableOffset=0.012`. | Map geometry and the **unit→elk zone** lookup come from this layer. Build once with a script, commit the output. |
| The same service also has **Elk Zones (1), Controlled Hunts (4), Closed areas (5–10), Wilderness (12)** layers. | `MapServer?f=json` | Phase 2 controlled-hunt boundaries and closures can come from GIS instead of PDF prose. |
| The **Big Game PDF** (`seasons-rules-big-game-YYYY.pdf`, 125 pp. in 2026) has deer general seasons in clean ruled tables: `Unit(s) | Antlered | Antlerless | Notes`. | `pdfplumber` extracts them. A 30-min spike parsed **235 deer season rows** (Regular + White-tailed tags; any-weapon/archery/muzzleloader/short-range). Every GMU with a general deer season was found; the 5 not found (44, 45, 52, 54, 68A) are controlled-only. | Deterministic parser with `pdfplumber` is viable for most tables. |
| Gotcha 1: a **vertical side tab** ("DEER / REGULAR") on each page breaks table detection on some pages. | Pages 13–14 returned zero tables until the page was **cropped 40 pt off each side**. | The extraction layer always crops page margins before finding tables. |
| Gotcha 2: **one unit row spans several sub-rows**. The unit cell is `None` on the continuation rows. | e.g. `1, 2, 3, 4A, 5, 6` has 3 antlered date ranges. | Carry the unit list forward while unit cell is empty. |
| Gotcha 3: dates carry **qualifiers in the same cell**: `(White-tailed deer only)`, `(Youth hunt only)`, `(2-point deer only)`, `(Private land only)`, `None`. | Seen throughout pp. 12–21. | Qualifiers become structured restriction flags, not free text. |
| Gotcha 4: **Elk zone season pages are graphical calendars rotated 90°**. `extract_tables()` returns reversed text. | p. 38: `'rebmetpeS'`, `'gaT\nA'`. | Elk needs a dedicated rotated-page parser. Text **is** recoverable (see the rotated-calendar row below). Manual entry/CSV is the fallback. |
| The **Upland brochure** is **2 years long** (2026–2027) and defines areas by **county lists with GMU exceptions**: e.g. Forest Grouse Area 1 = "Benewah, Bonner, … counties, and Adams and Idaho counties, **except** units 19A, 20A, 22, 23 and 26"; Area 2 = "Remainder of the state." | Upland PDF p. 7. | Area definitions need a **county ↔ GMU overlay** and a **partial-coverage** status (a unit can be partly in an area). |
| **Supplemental proclamations** are published mid-year (`…supplemental-proclamation-2026-2.pdf`). | IDFG big-game page. | A license year can have multiple source documents; later ones override earlier ones. |
| IDFG's online Hunt Planner (`fishandgame.idaho.gov/ifwis/huntplanner/hunt/<id>`) shows structured per-unit, per-season dates. | e.g. Unit 75 archery deer 8/30–9/30/2025. | Optional, manual-run **spot-check** only. See the internal-ID caveat below. |
| Idaho requires an **Archery Permit** for archery-only seasons and a **Muzzleloader Permit** for muzzleloader-only seasons. Elk **A/B zone tags** are valid only in their zone and only in that tag's seasons. Regular and White-tailed deer tags are separate. | Table titles: "General Archery Only Seasons – Archery Permit Required". | Tags and permits are both modeled. Eligibility = tag type + zone + permits + weapon. |
| **Controlled-hunt tables parse cleanly.** Header `Hunt No. \| Controlled Hunt Areas \| Tags \| Season Dates \| Notes`. 2026 sections: deer pp. 22–31, elk pp. 51–59, pronghorn pp. 64–67, bear p. 73. Titles give class/weapon ("Antlered", "Antlerless", "Either Sex", "Archery Only", "Muzzleloader Only", "Youth Only", "Extra Antlerless", "Outfitter Allocation"). | `docs/spike_controlled_parser.py`: **469 hunts** (deer 188, elk 221, pronghorn 54, bear 6), 0 unparsed date cells, 0 duplicate hunt numbers. | Deterministic table parser (T-17a). |
| **Superscript footnote markers are glued onto the hunt-area name** by naive extraction: `1-1a` is really Hunt Area `1-1` plus footnote **a** ("includes other units or parts of other units"), and `b` = "includes only a portion of this unit". Markers are 5.8 pt; body text is 10 pt. | `extract_words(extra_attrs=['size'])` on p. 22. The first footnote detector missed **92** markers; reading chars inside each cell's exact bbox fixed it (131 a / 77 b). It was caught **only because** the table path was compared with the raw-text path. | Extraction is **font-size aware** (§8.2). Footnotes become structured flags. Two-path agreement is mandatory (§8.7). |
| **Header tag totals are a free checksum.** "2026 Controlled Deer Hunts (13,226 Tags Plus Unlimited Tags)". | Parsed Tags column sums: deer **13,226**, elk **18,670**, pronghorn **2,040**. All exact. | Checksum invariant on every import. A mismatch blocks publishing (§8.7). |
| **Hunt numbers have real gaps** (elk 2214–2279, bear 8003–8500). These are numbering blocks, not missed rows. | None of the gap numbers appear anywhere in the PDF text. | The completeness check is "every hunt number printed on the page was parsed", **not** "no gaps". |
| IDFG GIS **Controlled Hunts layer** (MapServer/4, 2,162 features: `BigGame`, `HuntArea`, `Year`) has a polygon per hunt area. **Big game is published through 2025 only** (2026 has turkey only). | **469/469** 2026 hunt areas match a 2025 GIS `HuntArea` once superscripts are stripped. | Unit coverage for controlled hunts comes from **polygon × GMU overlay**, not prose parsing (T-17b). If the current year's polygons aren't published yet, use the latest year and flag `geometry_year < license_year` for review. |
| **Hunt-area description pages are two-column prose** (deer pp. 32–34, elk 60–62, pronghorn 68). Full-page `extract_text()` interleaves the columns. | p. 32 output mixed "Hunt Area 1-1" with text from the right column. | Detect the column gutter from word x-positions and extract each column separately. Descriptions are stored verbatim for display and to cross-check unit lists against the GIS overlay. |
| **Rotated elk zone calendars are readable.** pdfplumber `extract_text(line_dir_rotated='ltr', char_dir_rotated='btt')` returns proper text ("Panhandle Zone (Units 1, 2, 3, 4, 4A, 5, 6, 7, 9)", "Aug 30 - Sep 30 Archery only — any elk", "A Tag"). | p. 38 test. | Elk A/B parsing is automatable. Assigning each label to its zone and its A/B row needs geometry clustering (T-12). Manual entry stays as the fallback only. |
| **Printed page numbers ≠ PDF page indexes.** In-text references like "(See pg 31)" use the brochure's printed numbers, which are one less than the PDF page index in 2026. | p. 22 footer reads "idfg.idaho.gov 21"; the deer area descriptions referenced as "pg 31" are on PDF p. 32. | Store physical pages, map printed → physical from footers, and verify every reference resolves (`page_ref_resolves`). Page numbers elsewhere in this doc are **PDF page indexes**. |
| **Upland brochure scope** ("Upland Game, Turkey & Furbearer, 2026 & 2027", 55 pp.): upland birds, turkey (general + controlled), cottontail/snowshoe hare/red squirrel, furbearer **trapping and hunting** seasons by **region** ("\* Indicates species can also be hunted": badger, bobcat, red fox), and predatory/unprotected animals (year-round, defined in prose). Migratory birds are in a separate brochure. | Contents p. 6 and pp. 32, 36–38, 46 of the 2026 PDF. | Species matrix §5.3. Furbearer seasons need a **region → GMU** overlay. Predatory animals are modeled as statewide year-round seasons sourced from the rules prose. |
| IDFG GIS has **region polygons**: `WC_Regions/MapServer/0`, 8 features (Panhandle, Clearwater, Salmon, McCall SubRegion, Nampa SubRegion, Upper Snake, Magic Valley, Southeast). | Queried live. | McCall + Nampa = Southwest Region. `scripts/build_region_overlay.py` → `static/data/region_units.json` (T-24). |
| **Moose / Bighorn Sheep / Mountain Goat brochure** (`seasons-rules-moose-sheep-goat-2025-26.pdf`, 52 pp., two years). **All hunts are controlled.** Same `Hunt No.` layout, plus traps: (1) fake-bold text printed **4×** ("2222000022225555 MMMMoooouuuu…") on the goat page; (2) sheep/goat tables put **two hunts per row** (8 columns); (3) the California bighorn table is **nested** inside another table, so "Hunt No." is in column 1; (4) a **full-size `*`** is part of the area id, and GIS stores it the same way (`1-1*`), while some ids exist with and without it (`55` / `55*`); (5) superscript `a` here means "wilderness hunt area, extreme terrain", not "includes other units"; (6) superscripts are 6.4 pt but a 7.0 pt `a` exists on the same page. | `docs/spike_msg_parser.py`: **145 hunts** (moose 94, Rocky Mtn bighorn 28, California bighorn 4, goat 19), 0 duplicates, 0 unparsed dates, 0 hunt numbers in table-page text left unparsed, **145/145 exact GIS match**. Checksums: moose 513 = 510 + 3 ✓; goat 40 ✓; RM late rams 16 ✓; sheep 98 + lottery/auction tags = 100; California 5 vs. header 7, explained by the corrections page. The first run missed 7001–7004 (nested table) and was caught only by the completeness check. | `page.dedupe_chars()` always; locate **every** "Hunt No." column from the header row; match GIS area ids **exactly**, markers included; superscript threshold **relative to the page's median font size** (< 75 %); footnote meanings always from the page legend. |
| **Brochures contain "Corrections" pages** (Big Game p. 2, Upland p. 2, MSG pp. 2–3). Example: "California Bighorn Sheep – Hunt No. 7001 (Area 40): the Commission eliminated the only tag". The table already shows 0 tags (the online PDF is updated), but the header total still says 7. | MSG p. 3; spike sub-header checksum. | New `corrections` extractor: each correction item becomes a structured note on the affected hunt/season, and new check `corrections_applied` verifies that the table matches the correction. It also explains checksum gaps. A correction that can't be matched to a row is a `fail`. Hunts reduced to **0 tags are kept but marked `closed`** and never shown as open. |
| Completeness scans must be **limited to hunt-table pages**. | A full-document scan flagged "3316" (a street address) and "3529" (a phone number). | The `hunt_number_completeness` scope = pages containing a `Hunt No.` header. |
| **Migratory Game Bird brochure** (`migratorygame2026-2027_web.pdf`, 36 pp., one season Sep 2026 – Mar 2027). Ducks/geese: per-species pages with **AREA 1/2/3/4** blocks: full dates with years ("October 19, 2026 – January 31, 2027"; "September 1 – 15, 2026"), area definitions by **county lists**, "that portion of X County …" prose, and "all parts of the state NOT included in areas 2 and 3". **Scaup has its own sub-season** inside the duck season. Swans: controlled table (`Hunt No. \| Controlled Hunt Area \| Tags \| Season Dates \| Notes`, e.g. #9301 Area 1, Oct. 10 – Dec. 1). Sandhill crane: table `Hunt Area \| Season \| Tags` with carry-forward ("1-Bear Lake: September 1–30 (300), October 1–30 (150)"), areas defined by county ± exceptions. Dove/crow: statewide, plus extended falconry seasons. Requirements lists: **HIP permit**, **federal duck stamp**, nontoxic shot. Shooting-hour tables by time zone. Pages are multi-column with sidebars ("Areas Closed to Hunting… see pages 27–28") that interleave in plain text. | pp. 8–14, 16–17 of the 2026–27 PDF. | County overlay (reused from T-14) plus `partial` for "portion of" prose; new `sub_species` field on seasons; new date formats in `common.parse_dates` ("Oct. 10 – Dec. 1", "September 1–30", "September 1 – 15, 2026"); permits `hip`, `federal_duck_stamp`; sidebar-aware column cropping. |
| IDFG Hunt Planner pages use **internal IDs, not hunt numbers** (`/hunt/1002` is a 2003 bear season), and much of the data is old. | Fetched p. 1002. | Not a dependable cross-check source. Optional only (T-20). |

The spike output drives `docs/mockup.html`. For **Oct 17–18, 2026** with a Regular Deer tag and an archery permit, it shows **68 units open and 13 restricted**. This is spike output and hasn't been checked against the regs.

---

## 3. Architecture

### 3.1 Why not "just HTML"
A purely static page can't reliably parse PDF tables, and `localStorage` is fragile as a database. We still keep the UI as plain HTML/CSS/JS, but add a **tiny local Python server**:

```
┌───────────────────────── Browser (localhost:5050) ─────────────────────────┐
│  index.html + theme.css + vanilla ES modules (no framework, no build)      │
│  ├─ map.js        SVG GMU map (pre-projected paths)                        │
│  ├─ filters.js    species / tags / weapon / permits / date range           │
│  ├─ results.js    open-units table, unit brief drawer                      │
│  ├─ tags.js       My Tags & Permits                                        │
│  └─ review.js     Regulation import → review → publish                     │
└──────────────▲─────────────────────────────────────────────────────────────┘
               │ JSON over fetch()
┌──────────────┴──────────── Python 3.12 · Flask (single process) ───────────┐
│  api.py            REST endpoints                                          │
│  engine.py         availability engine (pure functions, unit-tested)       │
│  importer/         pdfplumber extraction + per-section parsers → staging   │
│  db.py + schema.sql SQLite (stdlib sqlite3), migrations by version number   │
└──────────────▲─────────────────────────────────────────────────────────────┘
               │
   data/planner.db     data/regs/<year>/<sha256>.pdf      (both git-ignored)
   static/data/gmu.json  (committed, built by scripts/build_gmu_geometry.py)
```

### 3.2 Technology choices

| Concern | Choice | Rationale |
|---|---|---|
| Server | **Flask 3** | One file to start, handles multipart upload, serves static files. |
| DB | **SQLite** via stdlib `sqlite3` | Zero-ops, single file, easy to back up (copy the file). No ORM; plain SQL in `db.py`. |
| PDF | **pdfplumber** | Proven in the spike: table extraction plus cropping plus char-level access for rotated pages. |
| Geo build | **shapely** + **pyproj** (build-time only, `requirements-dev.txt`) | Projection, simplification, county↔GMU overlay. Not needed at runtime. |
| Frontend | **Vanilla JS ES modules + SVG** | Matches Treeline's approach (they render the heat map with `createElementNS` paths). No bundler. |
| Fonts | Google Fonts: Bebas Neue, DM Sans, DM Mono | Same as reference. Fall back to system fonts when offline. |
| Tests | **pytest** (backend) and a tiny no-dependency JS test page or `node --test` for date utils | |
| Run | `python -m planner` → opens `http://localhost:5050` | |

### 3.3 Repository layout

```
Idaho-Hunt-Planner/
├── README.md
├── requirements.txt            # flask, pdfplumber
├── requirements-dev.txt        # pytest, shapely, pyproj, requests
├── planner/
│   ├── __main__.py             # `python -m planner` → run server, open browser
│   ├── app.py                  # Flask app factory, static + API blueprint
│   ├── api.py                  # REST endpoints (§6)
│   ├── db.py                   # connection, migrations, query helpers
│   ├── schema.sql              # canonical schema (§5)
│   ├── seed.py                 # species, tag types, weapon kinds, units
│   ├── engine.py               # availability engine (§7)
│   ├── dates.py                # weekend default, overlap math
│   └── importer/
│       ├── __init__.py         # run_import(pdf_path) orchestration
│       ├── extract.py          # pdfplumber wrapper: crop, tables, text, rotation
│       ├── classify.py         # detect doc kind + year from text
│       ├── common.py           # date-range, unit-list, qualifier parsing
│       ├── deer.py             # general deer (Regular + White-tailed)
│       ├── elk.py              # elk A/B zone seasons
│       ├── pronghorn.py
│       ├── bear_lion_wolf.py
│       ├── upland.py           # forest grouse, pheasant, quail, partridge, etc.
│       ├── controlled.py       # controlled-hunt tables (deer, elk, pronghorn, bear) — MVP
│       ├── hunt_areas.py       # 2-column hunt-area descriptions + GIS polygon coverage — MVP
│       ├── supplemental.py     # Phase 2
│       └── checks/             # accuracy invariants (§8.7): two-path agreement, checksums,
│           ├── __init__.py     #   completeness, token accounting, golden comparison
│           └── *.py
├── scripts/
│   ├── build_gmu_geometry.py   # IDFG GIS → static/data/gmu.json
│   ├── build_hunt_area_coverage.py # GIS MapServer/4 polygons × GMUs → data/hunt_area_coverage/<year>.json
│   ├── build_county_overlay.py # TIGER counties × GMUs → static/data/county_units.json
│   └── verify_golden.py        # compare an import against tests/golden/<year>/*.json
├── static/
│   ├── index.html
│   ├── css/theme.css           # tokens (§4)
│   ├── css/app.css
│   ├── js/{main,ui,router,api,state,map,filters,results,drawer,tags,review,seasons,dates}.js
│   └── data/{gmu.json,county_units.json}
├── data/                       # git-ignored: planner.db, regs/
├── tests/
│   ├── fixtures/               # small single-page PDFs cut from real brochures + expected JSON
│   ├── golden/2026/            # hand-verified full-section truth data (§8.7): deer_general.json,
│   │                           #   controlled_deer.json, controlled_elk.json, … (committed)
│   ├── test_dates.py  test_engine.py  test_common.py  test_checks.py
│   ├── test_deer_parser.py  test_controlled_parser.py  test_hunt_areas.py
│   ├── test_elk_parser.py  test_upland_parser.py  test_golden.py
│   └── test_api.py
└── docs/
    ├── DESIGN.md  mockup.html  spike_deer_parser.py  spike_controlled_parser.py
```

---

## 4. UI / UX specification

Reference: Treeline Hunt Research IQ. Working mockup: **`docs/mockup.html`** (open it in a browser; it's live and interactive).

### 4.1 Design tokens (taken from the Treeline stylesheet)

```css
:root{
  /* surfaces */ --bg:#0c0e0d; --bg2:#131613; --bg3:#1a1d1a; --bg4:#232723; --bg5:#2b302b;
  /* text */     --text:#e8ebd9; --text2:#9aa68f; --text3:#6a7460;
  /* brand */    --gold:#d4b15e; --gold2:#e8c878; --tl-green:#7a9a3e; --tl-green2:#a8c860;
  /* scale */    --sc1:#c44040; --sc2:#c47830; --sc3:#c4b840; --sc4:#8ab840; --sc5:#3aaa78;
  /* weapons */  --bow:#b8a038; --rifle:#4a7fa0;
  /* trend */    --up:#2d7a4a; --dn:#7a3030; --flat:#5a6a3a;
  /* semantic */ --open:var(--sc5); --restricted:var(--sc3); --closed:var(--bg4); --notag:transparent;
}
body{font-family:"DM Sans",sans-serif;background:var(--bg);color:var(--text);font-size:14px;line-height:1.6}
/* headings: "Bebas Neue", letter-spacing .06em; numerics & unit IDs: "DM Mono" */
```
- `--text3` on `--bg` is only 3.96:1, below WCAG AA (4.5:1) for small text, so the app uses `--text2` for small labels (filter labels, version, breadcrumb) where the mockup used `--text3`. Keep `--text3` for large or decorative text (T-03, Lighthouse a11y).
- `static/css/theme.css` is the only file with color literals. It also names the mockup's translucent tints (`--chip-on-bg`, `--badge-*`, `--map-label`, `--shadow`) and the font stacks with system fallbacks (`--font-body/display/mono`). `tests/test_static_shell.py` checks that the tokens match this block and that no other file has a literal color.

### 4.2 Layout (desktop ≥ 1100 px)

```
┌ disclaimer banner (bg3, gold "VERIFY" label, dismissible per session) ─────────────────────┐
│ IDAHO HUNT PLANNER  v0.x · regs 2026 (+1 supplement)            Where can I hunt this weekend? │
│ [🗺️ Where Can I Hunt] [🎟️ My Tags] [📅 Season Table] [📥 Regulations] [⚙️ Settings]           │
│ Viewing 🦌 Deer · Regular Deer · All legal weapons · Oct 17 – Oct 18                         │
│ Species [Deer][Elk][Pronghorn][Bear][Lion][Wolf] | [Grouse][Quail][Chukar][Partridge][Pheasant][Turkey]… [More ▾] │
│ Tags    [Regular Deer ✓][White-tailed Deer]  Weapon [All][Rifle][Archery][Muzzleloader]     │
│ Permits [🏹 Archery ✓][Muzzleloader]   Dates [2026-10-17]→[2026-10-18] [This wknd][Next wknd][Today][7 days] │
│ [ ] Also show seasons I don't hold a tag for (gold dashed outline)                          │
├──────────────────────────────┬─────────────────────────────────────────────────────────────┤
│  OPEN UNITS MAP (SVG)        │  68 OPEN · 13 RESTRICTED            [CSV] [Print]           │
│  hover → tooltip             │  Unit │ Status │ Seasons overlapping your dates │ Src       │
│  click → Unit Brief drawer   │  39   │ open   │ [any] antlered Oct 10–31        │ p.13      │
│  legend                      │  4    │ restr. │ [any] antlered Oct 10–31 [WT only] │ p.12   │
└──────────────────────────────┴─────────────────────────────────────────────────────────────┘
```
Below 1100 px the map and table stack vertically. Below 700 px chip rows scroll horizontally.

### 4.3 Map behavior
- **Fill colors**: `open` = `--open` (green), `restricted` = `--restricted` (yellow), `closed` = `--closed`. Units that are open for a tag the user doesn't hold, when the "show untagged" toggle is on: closed fill with a **2 px dashed `--gold` stroke**.
- **Labels**: unit ID centered on its pole of inaccessibility (precomputed), DM Mono 6.5 px. Dark text on colored fills, `--text3` on closed.
- **Hover**: highlight stroke `--gold2`. A tooltip shows unit, elk zone, and each overlapping season with a weapon badge (`any` blue, `archery` gold, `muzzleloader` orange), dates, restriction badges, and source page.
- **Click**: opens the **Unit Brief** drawer on the right (like Treeline's "📄 Unit Brief"):
  - Every season for that unit in the active license year, across all species the user holds tags for.
  - A horizontal **timeline** (Aug–Jan) with season bars and the selected date range shaded.
  - Notes and qualifiers verbatim from the PDF, with a link to `source PDF p.N` (opens the stored PDF at `#page=N`).
  - A link to the IDFG Hunt Planner unit page.
- **Zoom**: mouse-wheel and pinch zoom on the SVG viewBox, and a "↺ Reset view" button. (Nice-to-have; ticket T-08.)

### 4.4 Status semantics (the core rule)
For each unit, take the set **S** of published seasons that pass the tag, permit, weapon, species and date-overlap filters (§7):

| Status | Rule |
|---|---|
| **open** (green) | At least one season in S covers the **entire** selected range, the **full** unit, and has **no restriction flags** that narrow what a typical adult hunter with that tag can take. |
| **restricted** (yellow) | S is non-empty but every season has at least one of: partial date coverage, partial unit coverage, white-tailed only (on a Regular tag), antlerless only, 2-point only, private land only, youth only (when "youth" is enabled in settings), or "see notes" closures. The tooltip lists the reasons. |
| **closed** (grey) | S is empty. |
| **untagged** (dashed gold outline) | S is empty, but seasons would exist if tag ownership were ignored. Only shown when the toggle is on. |

### 4.5 Other tabs
- **🎟️ My Tags**: cards per license year. Add a tag by picking a tag type (Regular Deer, White-tailed Deer, Elk A, Elk B, **Controlled hunt**, Pronghorn, Black Bear, Lion, Wolf, Upland/Hunting license…), plus zone (elk) or hunt number (controlled). **Controlled:** type the 4-digit hunt number. The dialog looks it up in that year's published (or staged) controlled hunts and shows species, hunt area, dates, weapon, sex and the verbatim area description before saving. Unknown numbers are rejected. There's a permits panel (Archery, Muzzleloader), Settings for youth status, and a "Copy last year's tags" button (it **never** copies controlled tags, because hunt numbers are reused for different hunts each year).
- **📅 Season Table**: a filterable and sortable table of **all** published seasons (species, tag, unit/zone, weapon, sex, dates, restrictions, source page). Clicking a row highlights its units on the map. CSV export.
- **📥 Regulations**: an upload drop zone, a list of source documents by year (status: `parsing`, `needs review`, `published`, `superseded`), and the **Review screen** (§8.4) with its **Accuracy panel** (§8.7).
- **⚙️ Settings**: default species, weekend definition (Sat–Sun or Fri–Sun), youth flag, DB backup/restore, and "Reset all data". (No resident/nonresident setting. Residents only, D1.)

---

## 5. Data model (SQLite)

All dates are ISO `YYYY-MM-DD` strings, inclusive. `license_year` is the IDFG license year (calendar year). Upland seasons running Aug 30, 2026 to Jan 31, 2027 are stored with their real dates; the engine handles the year crossover (§7.4).

```sql
-- ───────── reference ─────────
CREATE TABLE schema_version (version INTEGER NOT NULL);

CREATE TABLE units (               -- seeded from static/data/gmu.json
  id          TEXT PRIMARY KEY,    -- '4A', '39', '68A', 'YNP'
  sort_key    INTEGER NOT NULL,    -- numeric part*10 + letter offset, for natural sort
  elk_zone    TEXT,                -- 'Panhandle' … from GIS Elk_Zone (overridable per year, see zone_units)
  region      TEXT,                -- IDFG region, optional
  area_acres  REAL
);

CREATE TABLE species (
  id    TEXT PRIMARY KEY,          -- full list + sources in §5.3: 'deer','elk','pronghorn','black_bear','mountain_lion','wolf',
                                   -- 'forest_grouse','quail','chukar','gray_partridge','sage_grouse','sharptail','pheasant','turkey',
                                   -- 'cottontail','snowshoe_hare','red_squirrel','badger','bobcat','red_fox','predatory_unprotected',
                                   -- 'moose','bighorn_rocky','bighorn_california','mountain_goat',
                                   -- 'duck','goose_canada','goose_whitefronted','goose_light','swan','snipe','coot',
                                   -- 'dove','crow','sandhill_crane'
  label TEXT NOT NULL, icon TEXT, grp TEXT NOT NULL   -- grp: 'big_game' | 'upland_bird' | 'turkey' | 'upland_animal' | 'furbearer' | 'predatory' | 'migratory'
);

CREATE TABLE tag_types (
  id               TEXT PRIMARY KEY, -- 'deer_regular','deer_whitetail','elk_zone_a','elk_zone_b','controlled',
                                     -- 'pronghorn_general','black_bear','mountain_lion','wolf','turkey_general',
                                     -- 'hunting_license' (upland birds/animals, furbearers, predatory, ducks/geese/snipe/coot/dove/crow: no species tag)
                                     -- 'sandhill_crane' (area-specific crane tag; requires_zone = 1, zone = crane hunt area)
                                     -- moose/sheep/goat/swan are controlled-only → 'controlled'
  species_id       TEXT REFERENCES species(id),   -- NULL for 'controlled' (species comes from hunt)
  label            TEXT NOT NULL,
  requires_zone    INTEGER NOT NULL DEFAULT 0,    -- elk A/B
  requires_hunt_no INTEGER NOT NULL DEFAULT 0     -- controlled hunts
);

CREATE TABLE zone_units (          -- elk zone membership per year (GIS default, editable)
  license_year INTEGER NOT NULL, zone TEXT NOT NULL, unit_id TEXT NOT NULL REFERENCES units(id),
  coverage TEXT NOT NULL DEFAULT 'full' CHECK (coverage IN ('full','partial')),
  PRIMARY KEY (license_year, zone, unit_id)
);

-- ───────── regulations ─────────
CREATE TABLE source_documents (
  id            INTEGER PRIMARY KEY,
  license_year  INTEGER NOT NULL,       -- first year covered
  year_end      INTEGER NOT NULL,       -- = license_year, or +1 for 2-yr upland brochures
  kind          TEXT NOT NULL,          -- 'big_game','upland','moose_sheep_goat','migratory','supplemental','manual'
  title         TEXT, filename TEXT, sha256 TEXT UNIQUE, pages INTEGER,
  stored_path   TEXT,                   -- data/regs/<year>/<sha256>.pdf
  priority      INTEGER NOT NULL DEFAULT 0,   -- supplements > base doc
  status        TEXT NOT NULL CHECK (status IN ('uploaded','parsing','needs_review','published','superseded','failed')),
  parser_version TEXT, imported_at TEXT, published_at TEXT, error TEXT
);

CREATE TABLE seasons (             -- staged AND published rows; status column separates them
  id             INTEGER PRIMARY KEY,
  document_id    INTEGER NOT NULL REFERENCES source_documents(id) ON DELETE CASCADE,
  status         TEXT NOT NULL CHECK (status IN ('staged','approved','rejected','published','superseded')),
  species_id     TEXT NOT NULL REFERENCES species(id),
  sub_species    TEXT,               -- a sub-season inside a species season, e.g. 'scaup' within 'duck' (shown as an informational badge when closed)
  tag_type_id    TEXT NOT NULL REFERENCES tag_types(id),
  zone           TEXT,               -- elk zone for A/B seasons
  hunt_number    TEXT,               -- controlled hunts ('1001'); unique per (license_year, document) — numbers are REUSED across years
  hunt_area      TEXT,               -- controlled hunt area id as printed WITHOUT footnote marker ('1-1', '36A-1X', '11A')
  hunt_class     TEXT NOT NULL CHECK (hunt_class IN ('general','controlled','extra','youth','outfitter','other')),
  weapon         TEXT NOT NULL CHECK (weapon IN ('any','archery','muzzleloader','short_range','shotgun','falconry')),
  sex            TEXT NOT NULL CHECK (sex IN ('antlered','antlerless','either','any','male','na')),
  start_date     TEXT NOT NULL, end_date TEXT NOT NULL,
  restrictions   TEXT NOT NULL DEFAULT '[]', -- JSON array of codes (§5.1)
  bag_limit      TEXT, notes TEXT,
  tag_quota      INTEGER,            -- controlled "Tags" column; NULL = unlimited. Used for checksums only, not shown (D1)
  source_page    INTEGER, source_text TEXT,  -- raw row/cell text for review
  source_bbox    TEXT,               -- JSON [x0,top,x1,bottom] of the table row on source_page (review highlight)
  extract_agree  INTEGER NOT NULL DEFAULT 0, -- 1 = table path and text path produced identical values (§8.7)
  confidence     REAL NOT NULL DEFAULT 1.0,  -- parser self-score 0..1
  supersedes_id  INTEGER REFERENCES seasons(id),
  reviewed_by_user INTEGER NOT NULL DEFAULT 0,
  CHECK (start_date <= end_date)
);
CREATE INDEX ix_seasons_lookup ON seasons(status, species_id, start_date, end_date);

CREATE TABLE season_units (        -- where a season applies
  season_id INTEGER NOT NULL REFERENCES seasons(id) ON DELETE CASCADE,
  unit_id   TEXT NOT NULL REFERENCES units(id),
  coverage  TEXT NOT NULL DEFAULT 'full' CHECK (coverage IN ('full','partial')),
  coverage_note TEXT,               -- e.g. 'Portion of Unit 1 — see p.13'
  coverage_frac REAL,               -- controlled hunts: fraction of the GMU's area inside the hunt-area polygon (T-17b)
  PRIMARY KEY (season_id, unit_id)
);

CREATE TABLE hunt_areas (           -- controlled hunt areas, per year & species (areas differ by species!)
  license_year     INTEGER NOT NULL,
  species_id       TEXT NOT NULL REFERENCES species(id),
  area_id          TEXT NOT NULL,   -- '1-1', '36A-1X', '11A' (no footnote marker)
  description      TEXT,            -- verbatim from the description pages (NULL for plain "All of Unit X" areas)
  description_page INTEGER,
  includes_other_units INTEGER NOT NULL DEFAULT 0,  -- footnote a
  portion_only     INTEGER NOT NULL DEFAULT 0,      -- footnote b
  geometry_year    INTEGER,         -- year of the GIS polygon used; < license_year ⇒ review flag
  document_id      INTEGER NOT NULL REFERENCES source_documents(id) ON DELETE CASCADE,
  PRIMARY KEY (license_year, species_id, area_id, document_id)
);

CREATE TABLE import_checks (        -- results of every accuracy invariant for a document (§8.7)
  id          INTEGER PRIMARY KEY,
  document_id INTEGER NOT NULL REFERENCES source_documents(id) ON DELETE CASCADE,
  section     TEXT NOT NULL,        -- 'deer_general', 'controlled_elk', …
  check_name  TEXT NOT NULL,        -- 'two_path_agreement','tag_checksum','hunt_number_completeness',
                                    -- 'date_token_accounting','unit_tokens_valid','golden_match','gis_area_match',
                                    -- 'unit_coverage','yoy_diff'
  status      TEXT NOT NULL CHECK (status IN ('pass','fail','warn','skipped')),
  expected    TEXT, actual TEXT,    -- e.g. '13226' / '13226'
  detail      TEXT,                 -- JSON list of offending rows/tokens with page numbers
  waived_by_user INTEGER NOT NULL DEFAULT 0, waive_reason TEXT,
  run_at      TEXT NOT NULL
);

-- ───────── user ─────────
CREATE TABLE profile (               -- single row, id = 1. Residents only (D1): no resident flag.
  id INTEGER PRIMARY KEY CHECK (id = 1),
  youth INTEGER NOT NULL DEFAULT 0,
  weekend_def TEXT NOT NULL DEFAULT 'fri_sun', default_species TEXT DEFAULT 'deer'
);

CREATE TABLE my_permits (
  license_year INTEGER NOT NULL, permit TEXT NOT NULL CHECK (permit IN ('archery','muzzleloader','hunting_license','wmb','falconry','hip','federal_duck_stamp')),
  PRIMARY KEY (license_year, permit)
);

CREATE TABLE my_tags (
  id INTEGER PRIMARY KEY,
  license_year INTEGER NOT NULL,
  tag_type_id  TEXT NOT NULL REFERENCES tag_types(id),
  zone TEXT, hunt_number TEXT, label TEXT, notes TEXT,
  filled INTEGER NOT NULL DEFAULT 0,      -- filled tags are excluded from availability
  created_at TEXT NOT NULL
);
```

### 5.1 Restriction codes (closed vocabulary)
`whitetail_only`, `mule_deer_only`, `antlered_only`, `antlerless_only`, `two_point_only`, `spike_only`, `brow_tined`, `youth_only`, `private_land_only`, `portion_of_unit`, `outfitter_only`, `cwd_testing`, `motorized_rule`, `second_tag_prohibited`, `see_notes`.
Each code has a `severity`: **narrowing** codes (`whitetail_only`, `mule_deer_only`, `youth_only` for adults, `private_land_only`, `portion_of_unit`, `outfitter_only`, and `see_notes`: unexplained conditions stay yellow, never green — D3) make a unit *restricted*. **Informational** codes (`cwd_testing`, `motorized_rule`, `second_tag_prohibited`, plus the animal-limit codes `antlered_only`, `antlerless_only`, `two_point_only`, `spike_only`, `brow_tined`: they limit which animal may be taken, but the unit is still open to hunt) show as badges but don't change color. The mapping lives in `planner/engine.py::RESTRICTIONS` and is mirrored to the frontend via `/api/meta`.

### 5.2 Upland areas
Upland seasons attach to **areas**, which are resolved into `season_units` rows with `full`/`partial` coverage at import time using `static/data/county_units.json` (precomputed percentage of each GMU inside each county). Rules:
- "Counties A, B, C" → units whose area is ≥ 98 % inside the union → `full`, 2–98 % → `partial`.
- "… except units X, Y" → remove X, Y.
- "Remainder of the state" → all units not `full` in the sibling areas. Units that were `partial` there are `partial` here too.

### 5.3 Species coverage matrix (D4)

Page numbers are **printed** brochure pages from each table of contents. Physical PDF index = printed + 1 in both 2026 brochures (verify per year with the footer map, §8.7 `page_ref_resolves`).

| Species (`species.id`) | Brochure · printed pp. | Area type → how it becomes `season_units` | Tag / license needed | Ticket |
|---|---|---|---|---|
| Deer: general Regular + White-tailed (`deer`) | Big Game · 11–15, 17–20 | GMU lists in tables | `deer_regular` / `deer_whitetail` | T-11 |
| Deer, controlled (`deer`) | Big Game · 21–33 | Hunt areas → GIS polygons (T-17b) | `controlled` + hunt # | T-17a/b |
| Elk, A/B zones (`elk`) | Big Game · 35–49 (rotated) | Elk zones → `zone_units` | `elk_zone_a/b` + zone | T-12 |
| Elk, controlled (`elk`) | Big Game · 50–61 | Hunt areas → GIS polygons | `controlled` | T-17a/b |
| Pronghorn general + controlled (`pronghorn`) | Big Game · 63–67 | GMU lists / hunt areas | `pronghorn_general` / `controlled` | T-13, T-17a |
| Black bear, spring/fall general + controlled (`black_bear`) | Big Game · 68–73 | GMU lists / hunt areas | `black_bear` / `controlled` | T-13, T-17a |
| Mountain lion (`mountain_lion`) | Big Game · ~74–76 | GMU lists, "all units except…" | `mountain_lion` | T-13 |
| Gray wolf, hunting only (`wolf`) | Big Game · ~77–83 (private vs. public land tables) | GMU lists; private/public land → `private_land_only` restriction | `wolf` | T-13 |
| Forest grouse, dusky/ruffed/spruce (`forest_grouse`) | Upland · 6 | Areas by **county lists ± units** → county overlay | `hunting_license` | T-14 |
| California & bobwhite quail (`quail`), chukar (`chukar`), gray partridge (`gray_partridge`) | Upland · 7–8 (statewide standardized in 2026) | Statewide or areas | `hunting_license` | T-14 |
| Sage-grouse (`sage_grouse`), sharp-tailed grouse (`sharptail`) | Upland · 9–10 | Areas (counties/units) | `hunting_license` (+ any permit the regs name) | T-14 |
| Pheasant (`pheasant`) | Upland · 11–13 | Areas; WMA rules → `wmb_permit_on_wma` | `hunting_license`; Upland Game Bird Permit on WMAs | T-14 |
| Wild turkey, spring/fall general + controlled (`turkey`) | Upland · 18–25 | General: GMU lists plus maps (p. 23). Controlled: hunt areas (GIS MapServer/4 has **2026 turkey** polygons) | `turkey_general` / `controlled` | T-23 |
| Cottontail (`cottontail`), snowshoe hare (`snowshoe_hare`) | Upland · 16 | Statewide/areas | `hunting_license` | T-24 |
| Red squirrel (`red_squirrel`) | Upland · 17 | Statewide/areas | `hunting_license` | T-24 |
| Badger, bobcat, red fox (hunting) (`badger`/`bobcat`/`red_fox`) | Upland · 31–33 ("\* Indicates species can also be hunted") | **IDFG regions** → region overlay (GIS `WC_Regions`) | `hunting_license` (bobcat: also harvest report, informational) | T-24 |
| Predatory & unprotected (coyote, raccoon, jackrabbit, skunk, weasel; marmot, porcupine, opossum, some ground squirrels…) (`predatory_unprotected`) | Upland · 35–37, 42, 45 (rules prose, not tables) | Statewide, year-round (Jul 1–Jun 30), with ground-squirrel exceptions from p. 42 | `hunting_license` | T-24 |
| Falconry seasons (`weapon='falconry'` on existing species) | Upland · 30 | As printed | `hunting_license` + falconry permit (new permit code) | T-24 |
| Moose, antlered + antlerless (`moose`) | MSG 2025 & 2026 · 6–16 (tables pp. 8–13 physical; area descriptions 17–18) | Controlled hunt areas → GIS MapServer/4 `Moose` polygons (ids include `*`) | `controlled` | T-25 |
| Rocky Mountain bighorn (`bighorn_rocky`), California bighorn (`bighorn_california`) | MSG · 17–22 (tables pp. 19–20 physical; descriptions p. 24) | GIS `Rocky Mtn Sheep` / `California Sheep` | `controlled` (plus lottery/auction tags, entered as a controlled hunt with a note) | T-25 |
| Mountain goat (`mountain_goat`) | MSG · 24–27 (table p. 26 physical, 4× fake-bold) | GIS `Goat` | `controlled` | T-25 |
| Ducks incl. mergansers (`duck`, scaup as `sub_species`), Wilson's snipe (`snipe`), American coot (`coot`) | Migratory 2026–27 · 8 | Areas 1–3 by county lists / "portion of" prose / "remainder" → county overlay | `hunting_license` + `hip` + `federal_duck_stamp` (ducks/geese/coot only, per the requirements list) | T-26 |
| Canada (`goose_canada`), white-fronted (`goose_whitefronted`), light geese (`goose_light`) | Migratory · 9–11 | Areas 1–4 by counties | `hunting_license` + `hip` + `federal_duck_stamp` | T-26 |
| Swan, tundra (`swan`) | Migratory · 12 | Controlled hunt areas (Notes column lists counties) | `controlled` | T-26 |
| Mourning & white-winged dove (`dove`), American crow (`crow`) | Migratory · 13 | Statewide; extended falconry season | `hunting_license` + `hip` (dove) | T-26 |
| Sandhill crane (`sandhill_crane`) | Migratory · 14 | Named crane hunt areas ("1-Bear Lake" … "6-Malad") by county ± exceptions | `sandhill_crane` tag for that area + `hip` | T-26 |

Species with no hunting season are never shown. That includes closed species (e.g. fisher, kit fox, lynx, Northern/Southern Idaho ground squirrels) and trapping-only furbearers.

---

## 6. API (JSON, localhost only)

| Method & path | Purpose |
|---|---|
| `GET /api/meta` | Species, tag types, weapons, restriction codes with severity, available license years, default date range (computed server-side using the local timezone). |
| `GET /api/units` | Unit metadata (id, elk zone, region). Geometry stays in the static `gmu.json`. |
| `GET /api/availability?species=deer&start=2026-10-17&end=2026-10-18&weapon=all&tags=mine\|<id,id>&include_untagged=0` | **Core query.** Returns `{range, units:{"39":{status,reasons:[],seasons:[…]}}, summary:{open,restricted,untagged}}`. |
| `GET /api/units/<unit>/seasons?year=2026` | All published seasons for the Unit Brief. |
| `GET /api/seasons?year&species&status=published` | Season Table. `POST`, `PUT /<id>`, `DELETE /<id>` for manual edits (creates or updates rows under a `manual` source document). |
| `GET /api/tags?year=2026` · `POST /api/tags` · `PUT /api/tags/<id>` · `DELETE /api/tags/<id>` · `POST /api/tags/copy?from=2025&to=2026` | My Tags. |
| `GET/PUT /api/permits?year=2026` · `GET/PUT /api/profile` | Permits and profile. |
| `POST /api/documents` (multipart `file`, optional `year`, `kind`) | Upload a PDF. Stores it, dedupes by sha256, and runs the import synchronously (it takes about 20 s, and the UI shows a spinner). Returns the document with counts. |
| `GET /api/documents` · `GET /api/documents/<id>` · `GET /api/documents/<id>/pdf` | List, detail with staged rows, and the raw PDF (served inline so `#page=N` works). |
| `PUT /api/documents/<id>/seasons/<sid>` | Edit, approve or reject a staged row. |
| `POST /api/documents/<id>/approve-all` | Bulk approve **only** rows with `extract_agree = 1` in sections where every check passes. |
| `GET /api/documents/<id>/checks` · `POST /api/documents/<id>/checks/<cid>/waive` (`{reason}`) | Accuracy results (§8.7) and the explicit waiver. |
| `GET /api/hunts/<year>/<hunt_number>` | Controlled-hunt lookup for the Add Tag dialog: species, area, description, dates, weapon, sex, units with coverage. |
| `POST /api/documents/<id>/publish` | Approved → published. Returns 409 if the §8.7 gate isn't satisfied. Marks overlapping rows from lower-priority docs as superseded (§8.5). |
| `DELETE /api/documents/<id>` | Remove a document and its seasons. |
| `GET /api/export.csv?year=` · `POST /api/import.csv` | Season CSV round-trip (manual fallback). |
| `GET /api/backup` · `POST /api/restore` | Download or upload `planner.db`. |

Errors use `{error: {code, message, detail}}` with HTTP 4xx/5xx. The server binds to `127.0.0.1` only.

---

## 7. Availability engine

`planner/engine.py`. Pure functions over rows. No Flask imports, so it's unit-testable.

### 7.1 Inputs
`species, start, end, weapon ∈ {all, rifle, archery, muzzleloader, short_range}, tag_scope (all my tags | explicit tag ids), include_untagged, profile, permits`.

### 7.2 Algorithm
```
1. license_years = years touched by [start, end]
2. my = unfilled my_tags in those years for this species (+ controlled tags whose hunt's species matches)
3. candidates = published seasons where species = S and start_date <= end and end_date >= start
4. for each season:
     tag_ok   = exists t in my with t.tag_type = season.tag_type
                 and (not requires_zone    or t.zone = season.zone)
                 and (not requires_hunt_no or t.hunt_number = season.hunt_number)
                 and t.license_year covers the overlapping days            (§7.4)
     weapon_ok:
       season.weapon = 'archery'      → user has archery permit AND weapon ∈ {all, archery}
       season.weapon = 'muzzleloader' → user has muzzleloader permit AND weapon ∈ {all, muzzleloader}
       season.weapon = 'short_range'  → weapon ∈ {all, short_range}
       season.weapon = 'any'          → always (any legal weapon, incl. bow/ML)
     youth_ok = 'youth_only' not in restrictions OR profile.youth
     keep if weapon_ok and youth_ok and (tag_ok or include_untagged)
5. for each kept season, for each season_unit:
     reasons = narrowing restrictions
             ∪ {'partial_dates'   if season does not cover all of [start,end]}
             ∪ {'portion_of_unit' if coverage = 'partial'}
             ∪ {'whitetail_only'  only counts as narrowing if user's tag isn't already WT-only}
6. unit status = open        if any tag_ok season has reasons = ∅
               = restricted  elif any tag_ok season
               = untagged    elif any season (include_untagged)
               = closed
7. days_open = count of days in [start,end] covered by the union of tag_ok seasons (for table sorting)
```

**Controlled-hunt specifics (D2):**
- A controlled tag matches **only** the season(s) with the same `hunt_number` **and** the same `license_year`. Hunt numbers are reused for different hunts in different years, so a 2025 tag `1001` must never match the 2026 hunt `1001`.
- A controlled tag's species comes from its hunt, so a deer controlled tag shows up under 🦌 Deer.
- The unit set comes from `season_units`, built by T-17b from the hunt-area polygon. A unit with `coverage_frac` < 0.98 is `partial` and shows as **restricted** with `portion_of_unit`, the verbatim hunt-area description, and its page link. It is never shown as plain green.
- Weapon and permit rules are the same as general seasons. "Archery Only" controlled hunts still require the Archery Permit.
- Seasons with `hunt_class = 'outfitter'` carry `outfitter_only` (narrowing) and are hidden unless the user holds that exact hunt number.
- When the user holds a controlled tag **and** a general tag for the same species, the map shows the union. Each season in the tooltip is labeled with the tag that unlocks it.

### 7.3 Default date range (`planner/dates.py` and `static/js/dates.js`, which must agree)
- `fri_sun` (**default**, D5): if today is Fri, Sat or Sun, return [today, Sunday]. Otherwise return the next Friday through Sunday.
- `sat_sun`: if today is Sat, return [today, tomorrow]. If today is Sun, return [today, today]. Otherwise return the next Saturday and Sunday.
- Uses the **browser's local date** on the frontend. The server's `/api/meta` value is only a fallback.
- Presets: *This weekend*, *Next weekend* (the full weekend after this one, i.e. this weekend's Sunday + 7 days; on a Saturday with `fri_sun` that is next Fri–Sun, not just next Sat–Sun), *Today*, *Next 7 days* (today + 6, inclusive), *Opening day* (the single day [d, d] where d is the earliest start among my tags' seasons on or after today; none if there isn't one).
- All ranges are inclusive. `days(a, b)` counts the days in [a, b] (0 if b < a); `overlap` counts the days two ranges share. The shared fixture `tests/fixtures/dates_cases.json` is the contract for both twins.

### 7.4 License-year crossover
Tags are valid for their calendar license year. If the range spans Dec 31 to Jan 1 (upland late season), each day is evaluated against the tags of that day's year. If the user has no next-year license, the unit is `restricted` with reason `no_tag_next_year`.

---

## 8. Regulation import pipeline

### 8.1 Flow
```
upload PDF ─► store (sha256) ─► classify(kind, year) ─► extract pages (cropped)
     ─► section router ─► species parsers ─► normalize & validate ─► staging rows (status='staged')
     ─► Review UI (diff vs prior year, confidence, page preview) ─► approve ─► publish
```

### 8.2 Extraction rules (`importer/extract.py`)
- Open with `pdfplumber`. For each page, **crop 40 pt off the left and right** (side tabs), then `extract_tables()` and `extract_text()`.
- **Font-size aware.** Superscript footnote markers are characters smaller than **75 % of the page's median body font size** (2026 Big Game: 5.8 pt vs 10 pt; MSG: 6.4 pt, with a legitimate 7.0 pt `a` elsewhere on the same page, so a fixed "< 7 pt" cutoff is unsafe). Remove them from the page with `page.filter(...)` before table extraction, then read them **separately from each cell's bbox on the unfiltered page** and store them as structured flags. **Marker meanings come from each page's footnote legend, never a global map**: Big Game `a` = "includes other units", `b` = "only a portion"; MSG `a` = "wilderness hunt area, extreme terrain", and a full-size `*` is part of the area id. pdfplumber crops keep absolute coordinates, so bboxes from the filtered page apply directly to the original.
- **Always `page.dedupe_chars(tolerance=1)` first.** Fake-bold headings are printed several times on top of each other (MSG goat page: "2222000022225555 MMMMoooouuuu…").
- **Locate columns from the header row, never by position.** Find every cell starting with `Hunt No.` and read the 4 cells to its right. This handles two-hunts-per-row tables (8 columns) and tables nested inside other tables (Hunt No. in column 1).
- **Two-column prose pages** (hunt-area descriptions): find the gutter as the widest vertical band with no word boxes in the middle 40 % of the page width, then extract each column separately.
- **Rotated pages** (elk zone calendars): `extract_text(line_dir_rotated='ltr', char_dir_rotated='btt')` for text, and `extract_words` with the same options for geometry.
- Detect rotated layouts. If more than 30 % of a page's chars have `upright == False`, set `page.rotated = True` and route the page to the rotated-layout handler (T-12).
- Cache per-page extraction as JSON in `data/cache/<sha256>/p<N>.json` so re-parsing after a parser fix is fast.

### 8.3 Common parsing (`importer/common.py`)
- **Date ranges**: `(Mon) D – (Mon) D` with `-`/`–`/`—`/"through". Full month names, and an optional year (`August 30, 2026 through January 31, 2027`). Infer the year from the document's license year. If the end is before the start, the end rolls into the next year.
- **Unit lists**: `1, 2, 3, 4A, 5, 6`, `Units 16A, 17, 19, and 20`, ranges like `21–21A`. Every token is validated against `units`. Unknown tokens become a warning on the row.
- **Qualifiers → restriction codes**: `(White-tailed deer only)`→`whitetail_only`, `(Youth hunt only)`→`youth_only`, `(2-point deer only)`→`two_point_only`, `Private land only`→`private_land_only`, `In a Portion of Unit`→`portion_of_unit`, `Chronic Wasting Disease Testing Required`→`cwd_testing`, `Motorized Hunting Rule Applies`→`motorized_rule`, `Second deer tag may not be used`→`second_tag_prohibited`. A cell reading `None` means no season (no row is created).
- **Table title → (tag_type, weapon, hunt_class)**: e.g. `"2026 Regular Deer Tag / General Archery Only Seasons – Archery Permit Required"` → (`deer_regular`, `archery`, `general`). Titles carry forward to continuation tables on later pages that start directly with the `Unit(s)` header.
- **Confidence** starts at 1.0 and loses 0.2 for each of: unknown unit token, unparsed date text left in a cell, qualifier text not matched, or a cell spanning more than 3 date ranges.

### 8.4 Review screen
- Rows grouped by species → table title → page. Columns: units (chips), tag, weapon, sex, dates, restriction badges, confidence (colored with `--sc1`…`--sc5`), source text.
- The right pane shows the **PDF page** (embedded `<iframe src="/api/documents/<id>/pdf#page=N">`) for the selected row.
- **Diff vs last published year**: each row is tagged `new`, `changed` (dates moved, with old→new shown) or `same`. There's also a "Removed since last year" list. The 2026 brochure's own "What's new" section is a great sanity check.
- Inline edit of any field, approve/reject per row, "approve all rows that pass every check" (there is **no** confidence-only bulk approve), keyboard nav (j/k/a/r).
- An **Accuracy panel** at the top lists every `import_checks` result per section (✓ pass / ✗ fail / ⚠ warn) with expected vs. actual and links to the offending rows and pages.
- The **Publish** button is disabled until (1) no rows remain `staged`, (2) every check is `pass`, or a `fail` has been explicitly **waived** with a typed reason (stored and shown on the document forever), and (3) every row with `extract_agree = 0` has been individually reviewed.

### 8.5 Supersession
When publishing doc D (license year Y, priority P): for every published season from another doc of the same year with lower priority, the same (species, tag_type, zone/hunt, weapon, sex) and an overlapping unit set, mark it `superseded` and set `supersedes_id`. Uploading a new year never touches prior years. Prior years stay queryable for "last year vs this year".

### 8.6 Fallbacks (the app must stay useful when a parser fails)
1. **Manual season editor** (Season Table → "+ Add season").
2. **CSV import** using the same columns as the export. A template is provided in `docs/seasons_template.csv`.
3. Optional (off by default): an **LLM-assisted extractor** that sends one page's text to a user-configured model and returns rows in the CSV schema. These rows go to staging with `confidence ≤ 0.6` and are **never** eligible for bulk approve. Each one must be hand-checked against the PDF page. Out of scope for v1 tickets; listed as a stretch goal.

### 8.7 Accuracy strategy (D3: the most important section in this document)

The app tells Josh where he can legally hunt, so a wrong date or unit is worse than no answer. Accuracy is enforced by **mechanical checks that block publishing**, not by hoping the parser is right. Every check writes a row to `import_checks`.

**Principles**
1. **Deterministic parsers only** produce publishable data. No LLM output is ever published without row-by-row human verification.
2. **Never guess.** If a cell can't be fully explained, the row is staged as `fail`. It is never silently dropped or "best-effort" filled.
3. **Two independent extraction paths must agree.** Path A is pdfplumber's ruled-table extraction (`find_tables`). Path B is the raw text layer (`extract_text` / word positions with superscripts kept inline), parsed with independent regexes. Every row's key fields (hunt number, area, footnote marks, dates, units, sex) are compared. Any disagreement makes that row `extract_agree = 0` and it must be reviewed by hand. *Evidence: this exact check caught 92 missed footnote markers in the spike (§2).*
4. **Every number printed in the PDF must be accounted for.**
5. **The truth is the PDF page.** Every row keeps `source_page`, `source_bbox` and `source_text`. Review shows the PDF page with the row highlighted.

**Invariants (run automatically after every import, per section)**

| Check | Rule | 2026 spike result |
|---|---|---|
| `two_path_agreement` | Table path == text path for every row's key fields. | Controlled: 0 disagreements / 469 |
| `tag_checksum` | Σ Tags column == every total printed in section headers and sub-headers ("(N Tags Plus Unlimited Tags)", "- 510 Tags", "16 Tags"). Totals that include tags with no table row (MSG "Including Special Lottery and Auction Tag") must name the extra tags explicitly in the check detail. | Deer 13,226 ✓ · Elk 18,670 ✓ · Pronghorn 2,040 ✓ · Moose 513 ✓ · Goat 40 ✓ · RM late rams 16 ✓ |
| `hunt_number_completeness` | The set of 4-digit hunt numbers that start a line in the text of **hunt-table pages** (pages with a `Hunt No.` header) == the set parsed. Gaps in numbering are legitimate. Scanning all pages produces false positives (addresses, phone numbers). | Big Game: 468 in text, 469 parsed (the extra, 2111, is a merged text line) ✓ · MSG: 0 missing, after the nested-table fix |
| `corrections_applied` | Every item on the brochure's "Corrections" page is matched to the affected rows, and the table agrees with the correction. A correction that can't be matched = fail. Checksum gaps are allowed only when a correction explains them. | MSG: 7001/7002 zeroed, explaining California 5 vs. header 7 |
| `no_duplicate_keys` | Hunt numbers unique per year. General seasons unique per (tag, weapon, sex, unit, dates). | 0 duplicates |
| `date_token_accounting` | Every date-range token (`Mon D - Mon D`) inside a season table on the page is consumed by **exactly one** parsed season. Leftover tokens = fail, listed with page and text. | Controlled: 0 unparsed date cells |
| `unit_tokens_valid` | Every unit token is a real GMU from `units`. Every hunt area resolves to ≥ 1 unit. | — |
| `gis_area_match` | Every controlled hunt area exists in the GIS Controlled Hunts layer for `license_year` (fall back to the latest year with a warning). Match **exactly**, including a full-size `*`. | Big Game 469/469 · MSG 145/145 (2025 GIS) |
| `general_coverage` | Every GMU (except `YNP`) has ≥ 1 general Regular deer season **or** appears in a controlled-only list that is confirmed in the page text. | 5 controlled-only: 44, 45, 52, 54, 68A |
| `golden_match` | For every section with a committed `tests/golden/<year>/<section>.json`, the import matches it **exactly**. | Golden files are built in T-22 |
| `yoy_diff` (warn) | Changes vs. last year's published data are listed for review. More than 25 % of rows changed in a section → warn. | — |

**Golden dataset (T-22).** For the first year (2026), every row of every MVP section (general deer, all controlled deer/elk/pronghorn/bear) is **verified by hand against the PDF**, one page at a time, and committed as JSON. The parser must reproduce it with zero diffs. In later years the previous year's golden file is not truth for the new PDF, but it serves as a regression fixture: re-parsing the 2026 PDF after any parser change must still match 2026 golden exactly. New years are protected by the invariants plus the review gate.

**Review gate.** Publishing requires every check to pass or be explicitly waived with a written reason, and every disagreeing row to be individually reviewed (§8.4). There's no bulk approve for rows that fail any check.

**Display safety.** If a section has any waived check, every season from that section shows a ⚠ "manually verified" badge in tooltips. The app always links each season to its PDF page.

---

## 9. Map geometry build

`scripts/build_gmu_geometry.py` (run manually, output committed):
1. GET `https://gisportal-idfg.idaho.gov/hosting/rest/services/Hunting/MapServer/3/query?where=1%3D1&outFields=NAME,ID,Elk_Zone&outSR=4326&f=geojson`.
2. Project to **EPSG:8826 (Idaho Transverse Mercator)** with pyproj. Simplify with shapely (tolerance about 300 m, preserving topology so shared borders don't gap).
3. Scale into a viewBox about 430 × 690 and emit `static/data/gmu.json`:
   `{"viewBox":"0 0 430 690","units":{"4A":{"d":"M…Z","label":[x,y],"zone":"Panhandle","acres":…}},"generated":"…","source":"IDFG GIS MapServer/3"}`.
   Label point = `shapely.ops.polylabel`.
4. Target size under 150 KB. Print a warning if any unit in the regs DB is missing from geometry.

`scripts/build_county_overlay.py`: download US Census cartographic-boundary counties (cb_*_us_county_500k, filter STATEFP=16), project, intersect with GMUs, and write `{"Ada":{"38":0.62,"39":0.04,…},…}` as fractions of each **unit's** area.

---

## 10. Phasing

| Phase | Outcome | Tickets |
|---|---|---|
| **0 – Foundations** | Repo runs, themed shell, map renders | T-01 … T-05 |
| **1 – MVP: Deer + all controlled hunts, accuracy-gated** | Upload the 2026 big-game PDF; general deer **and** every controlled deer/elk/pronghorn/bear hunt parse with every invariant passing and a 100 % golden match; review and publish; add a Regular Deer tag and/or a controlled tag by hunt number; see this weekend's green units | T-06 … T-11, T-15, T-16, **T-17a, T-17b, T-17c, T-22** |
| **2 – Elk A/B & other general big game** | Elk A/B zones, pronghorn, bear, lion, wolf, supplements | T-12, T-13, T-18 |
| **3 – Upland birds & turkey** | Forest grouse, quail, chukar, partridge, sage/sharp-tailed grouse, pheasant via county areas; turkey general + controlled | T-14, T-23 |
| **4 – Small game, furbearers, predatory, falconry** | Cottontail, snowshoe hare, red squirrel, badger/bobcat/red fox (hunting), predatory/unprotected, falconry | T-24 |
| **5 – Moose/sheep/goat & migratory birds** | Upload the MSG and Migratory brochures; all their hunts pass the accuracy gate | T-25, T-26 |
| **6 – Polish** | QA report, optional Hunt Planner spot-check, zoom, print, settings | T-19, T-20, T-21 |

Dependency graph: `T-01 → {T-02, T-04} ; T-02 → {T-03 → T-07, T-17b} ; T-04 → {T-05, T-06, T-09, T-10} ; T-05 → T-06 → T-07 → T-08 ; T-10 → {T-11, T-12, T-13, T-14, T-17a, T-18, T-22} ; T-17a → T-17b → T-17c ; T-09 → T-17c ; T-22 → T-15 (publish gate) ; T-11 → T-15 ; T-15 → T-16 ; T-14 → {T-23, T-24, T-26} ; T-17b → {T-23, T-25, T-26} ; T-19 spans all`.

---

## 11. Risks & open questions

| Risk | Mitigation |
|---|---|
| IDFG changes the PDF layout | Parsers are per-section and fixture-tested. The review screen catches drift. Manual/CSV fallback. Parser version is stored per document. |
| Elk calendar pages are hard to parse | Spike first (T-12a). Worst case is manual entry: about 28 zones × 2 tags × a few seasons is roughly 150 rows, entered once a year. |
| "Portion of unit" nuances | Shown as *restricted* with the verbatim note and page link. Never shown as plain green. |
| Wrong data means an illegal hunt | Accuracy gate (§8.7): two-path agreement, checksums, completeness, golden match. Permanent disclaimer, source page on every season, no auto-publish. |
| GIS hunt-area polygons lag a year behind the PDF (2026 big game not yet published) | Use the latest available year, flag `geometry_year < license_year`, and require review of each area whose description text changed vs. last year. If an area is new and has no polygon, fall back to the parsed description's unit list as `partial` with a warning. |
| Upland county / region overlays are approximate | Partial coverage → restricted plus a note naming the counties/regions. |
| Predatory/unprotected rules are prose, not tables | A small curated parser for the named species lists, plus a **mandatory** golden file. Any change in the prose year over year fails the check and must be reviewed. |
| **Open questions** | None outstanding. All answered (D1–D6). |

---

## 12. Tickets

Each ticket is self-contained for a coding agent. **Every agent reads `docs/AGENT_CONTEXT.md` first**, then the sections the ticket cites. Shared conventions:
- Python 3.12, `ruff` formatting, type hints, no ORM.
- Frontend: vanilla ES modules, no build step, no framework, CSS variables from `theme.css` only (no hard-coded colors).
- All tests via `pytest -q` from the repo root. A ticket isn't done until its tests pass and `python -m planner` still starts.
- Never commit `data/` (DB, PDFs, cache). Do commit `static/data/*.json` and `tests/fixtures/*`.
- Open one PR per ticket, titled `T-XX: <title>`.

---

### T-01 · Repository scaffold & dev environment
**Depends on:** none · **Size:** S
**Description:** Create the layout in §3.3 with empty or minimal modules. Add `requirements.txt` (`flask>=3`, `pdfplumber>=0.11`), `requirements-dev.txt` (`pytest`, `ruff`, `shapely>=2`, `pyproj`, `requests`), and a `.gitignore` covering `/data/` (anchored to the repo root so `static/data/` stays committed), `.venv/`, `__pycache__/`, `*.db`. Make `planner/__main__.py` start Flask on `127.0.0.1:5050` (port overridable via `PLANNER_PORT`), create `data/` if missing, and open the default browser unless `--no-browser` is passed. `app.py` exposes `create_app(db_path=None)` and serves `static/index.html` at `/`. Add `GET /api/health` → `{"ok":true,"version":…}`. README covers setup (`python -m venv .venv`, install, run) and the disclaimer. Add a GitHub Actions workflow running `ruff check` and `pytest` on 3.12.
**Acceptance criteria:**
- `python -m planner --no-browser` serves `/` (placeholder page) and `/api/health`.
- `pytest` passes with at least one smoke test using Flask's test client.
- CI is green.

---

### T-02 · GMU geometry build script
**Depends on:** T-01 · **Size:** M
**Description:** Implement `scripts/build_gmu_geometry.py` exactly per §9 steps 1–4. Use `requests` with a 30 s timeout and one retry. Preserve topology when simplifying (simplify the shared-boundary mesh, or use `shapely.coverage_simplify` if shapely ≥ 2.1, falling back to per-polygon `simplify(preserve_topology=True)`). Handle MultiPolygons. Emit numbers with 1 decimal. Include `YNP` if present, flagged `"closed": true`. Also write `static/data/units_seed.json`: `[{id, sort_key, elk_zone, area_acres}]`, where `sort_key` = int(numeric part)*10 + (0 for none, 1 for A, 2 for B, 3 for C). Commit the generated files.
**Acceptance criteria:**
- `static/data/gmu.json` has exactly the features returned by the service (100 as of 2026), file under 150 KB.
- Rendering all paths in a blank SVG shows no visible slivers or gaps between neighbors (attach a screenshot to the PR).
- The script is idempotent, and `--check` mode exits non-zero if the regenerated output differs from the committed file.

---

### T-03 · Theme & app shell (static UI)
**Depends on:** T-01 · **Size:** M
**Description:** Build `static/index.html`, `css/theme.css`, `css/app.css` and `js/main.js` to match §4.1–§4.2 and `docs/mockup.html`. Include: disclaimer banner (dismiss stored in `sessionStorage`), header with Bebas Neue title "IDAHO HUNT PLANNER" ("HUNT" in `--gold`), version and active-regs label, a tab bar with hash routing (`#/map`, `#/tags`, `#/seasons`, `#/regs`, `#/settings`), the "Viewing …" breadcrumb, the filter-bar container, and a two-column card layout with responsive stacking at 1100 px. Create reusable components in `js/ui.js`: `chipGroup({id, items, multi, value, onChange, disabled})`, `card(title)`, `badge(kind,text)`, `toast(msg, level)`. Fonts from Google with system fallback. All colors come from CSS variables.
**Acceptance criteria:**
- Visually matches the mockup (header, tabs, chips, cards, colors). Include a screenshot in the PR.
- Tab routing works with back/forward, and the empty tabs render placeholder cards.
- Lighthouse accessibility score ≥ 90: chips are `<button>` with `aria-pressed`, tabs use `role="tablist"`, and there's a visible focus ring in `--gold2`.

---

### T-04 · Database schema, migrations & seed
**Depends on:** T-01 (T-02 for unit seed data) · **Size:** M
**Description:** Implement `planner/schema.sql` exactly as §5 and `planner/db.py` with `connect(path)` (`PRAGMA foreign_keys=ON`, `journal_mode=WAL`, `row_factory=sqlite3.Row`), `migrate()` (applies `schema.sql` at version 1; future migrations live in `planner/migrations/NNN_*.sql` and are applied in order), and small helpers `q(sql, *args)`, `q1`, `exec`, `tx()` context manager. `planner/seed.py` inserts species (§5 list with icons 🦌 🫎 🐻 🐆 🐺 🦃 🐾 🐦; turkey 🦃, mountain lion 🐆), tag types (§5), units from `units_seed.json`, and default `zone_units` for the current year from GIS `Elk_Zone`. Seeding is idempotent (`INSERT OR IGNORE`). Also define `RESTRICTIONS` (code → {label, severity}) per §5.1 in `planner/engine.py` as a constant (engine logic comes in T-06).
**Acceptance criteria:**
- A fresh DB is created on first run. Re-running doesn't duplicate rows.
- Tests cover migrate on a temp DB, seed counts (≥ 100 units, ≥ 11 tag types), and FK enforcement (inserting a season_unit with an unknown unit fails).

---

### T-05 · Date utilities (Python + JS twins)
**Depends on:** T-01 · **Size:** S
**Description:** Implement §7.3 in `planner/dates.py` (`default_range(today, weekend_def) -> (date,date)`, `overlap(a1,a2,b1,b2) -> int days`, `days(a,b)`, `parse_iso`) and `static/js/dates.js` (same names, operating on local-time `Date` at noon to avoid DST bugs, plus `toISO`, `fmtShort` "Oct 17"). Include presets: thisWeekend, nextWeekend, today, next7, openingDay(seasons).
**Acceptance criteria:**
- Table-driven tests for all 7 weekdays × both weekend definitions, in Python and JS (`node --test static/js/dates.test.mjs`), using the **same fixture JSON** (`tests/fixtures/dates_cases.json`) so the two implementations can't diverge.
- Includes a DST-boundary case (first Sunday of November).

---

### T-06 · Availability engine + `/api/availability`
**Depends on:** T-04, T-05 · **Size:** L
**Description:** Implement §7 in `planner/engine.py` as pure functions: `evaluate(seasons, season_units, my_tags, permits, profile, species, start, end, weapon, include_untagged) -> dict`. Then add the API wrapper in `api.py` that loads rows with one SQL query per table (published only, date-overlap filter in SQL), calls `evaluate`, and returns the §6 shape. Each unit entry contains `status`, `reasons` (deduped union across seasons for the *best* status), `days_open`, and `seasons` (id, tag_type, zone, hunt_number, weapon, sex, start, end, restrictions, coverage, coverage_note, source_page, document_id). Also implement `GET /api/meta` and `GET /api/units`.
**Acceptance criteria:**
- Unit tests cover: full-range open; partial dates → restricted; antlerless-only → restricted; whitetail_only on a Regular tag → restricted, but **open** on a White-tailed tag; archery season hidden without an archery permit; weapon=rifle excludes archery/ML seasons; elk A tag in the wrong zone → closed; filled tag excluded; include_untagged → `untagged`; youth_only hidden for adults; Dec 31 → Jan 1 crossover with no next-year tag → restricted `no_tag_next_year`.
- `/api/availability` responds in under 150 ms on a DB with 5,000 seasons (add a perf test with synthetic data).

---

### T-07 · SVG map component + filter bar wiring
**Depends on:** T-02, T-03, T-06 · **Size:** L
**Description:** `static/js/map.js`: `createMap(svgEl, gmuJson)` returns `{setStatuses(map), onHover(cb), onClick(cb), highlight(unitIds)}`. Render one `<path>` per unit, then one `<text>` label per unit (§4.3). Colors are set via classes `.u-open/.u-restricted/.u-closed/.u-untagged` defined in `app.css` from tokens. `filters.js` builds the filter bar (§4.2) with `chipGroup`. Species chips are enabled only for species that have published seasons. Tag chips list the user's unfilled tags for that species (plus a "No tags yet → add one" link to `#/tags`). Weapon, permit chips (toggling a permit calls `PUT /api/permits`), date inputs and presets, and the include-untagged checkbox. `state.js` holds filter state, syncs it to the URL query string (shareable or bookmarkable views, e.g. `#/map?species=elk&start=…`), and debounces `GET /api/availability` (150 ms). The tooltip follows the mouse, is clamped to the viewport, and shows the content from §4.3. The legend shows counts per status.
**Acceptance criteria:**
- On first load with no URL params, the date range equals the coming weekend (T-05) and the species equals the profile default.
- Changing any filter updates the map and the breadcrumb within 300 ms. The URL reflects the state, and reloading restores it.
- Keyboard: Tab focuses units (`tabindex=0` on paths, `aria-label="Unit 39, open"`), and Enter opens the brief.
- Matches the mockup's look.

---

### T-08 · Results table, Unit Brief drawer, map zoom
**Depends on:** T-07 · **Size:** M
**Description:** `results.js`: a sortable table (Unit natural sort, Status, Days open, Weapons, Earliest start) of units with status ≠ closed, with season badges and restriction badges (labels from `/api/meta`) and a `p.N` link opening the PDF at that page. Hovering a row highlights the unit on the map. Includes CSV export and a print stylesheet (map + table on one landscape page, light theme). `drawer.js`: the Unit Brief from §4.3. It fetches `/api/units/<id>/seasons?year=`, renders a timeline (inline SVG, Aug 1 → Jan 31, one bar per season colored by weapon, selected range as a translucent `--gold` band, today as a line), the list of seasons with notes, and links (IDFG Hunt Planner search for the unit, source PDF). Map zoom: wheel and pinch on the viewBox, clamped to 1×–8×, plus a reset button.
**Acceptance criteria:**
- Sorting is stable and natural (4, 4A, 5, 10, 10A).
- The drawer closes on Esc and on outside click, and is focus-trapped.
- Print preview fits on one page.

---

### T-09 · My Tags & Permits (UI + API)
**Depends on:** T-04, T-03 · **Size:** M
**Description:** API per §6 (`/api/tags`, `/api/permits`, `/api/profile`, copy-year). Validation: `requires_zone` tag types must have a zone from `zone_units` for that year, and `requires_hunt_no` must have a hunt number. UI `#/tags`: a year selector (defaults to the current year), tag cards (species icon, label, zone/hunt #, filled toggle, delete), an "Add tag" dialog whose fields depend on tag type (zone dropdown for elk A/B; for controlled, a hunt-number field whose full lookup and preview is built in T-17c), a permits panel, a profile panel (youth, weekend definition, default species; **no resident/nonresident field**, D1), and a "Copy tags from <year-1>" button (copies unfilled tag types but not filled status, and **skips controlled tags** because hunt numbers are reused).
**Acceptance criteria:**
- CRUD round-trips with API tests, and validation errors show inline.
- Marking a tag filled removes its units from the map immediately (the shared state invalidates availability).

---

### T-10 · PDF ingest framework (upload, store, classify, extract, stage)
**Depends on:** T-04 · **Size:** L
**Description:** Implement §8.1–§8.3 infrastructure:
- `POST /api/documents`: accept a PDF (≤ 50 MB, `application/pdf` magic check), compute sha256 (an existing hash returns 409 with the existing doc id), save to `data/regs/<year>/<sha256>.pdf`, and create a `source_documents` row.
- `importer/classify.py`: detect `kind` and `license_year` from page 1–3 text. Big game: "Big Game … Seasons & Rules" + year. Upland: "Upland Game, Furbearer and Turkey" with a "YYYY–YYYY" range → year_end. Supplemental: "Supplemental Proclamation". Manual override via form fields.
- `importer/extract.py`: page iterator yielding `Page(number, text, tables, rotated)` with the 40 pt side crop, rotation detection, and the per-page JSON cache.
- `importer/common.py`: all of §8.3 (dates, unit lists, qualifiers → restriction codes, title → tag/weapon/class, confidence).
- `importer/__init__.py::run_import(doc_id)`: route pages to registered parsers (`@parser(kind="big_game", match=lambda page: "Deer Tag" in page.text)`), collect `ParsedSeason` dataclasses, validate (dates within Jul 1 of year to Jun 30 of year+1, units exist, end ≥ start), and write `seasons` (status='staged') and `season_units`. Set the document status to `needs_review` (or `failed` with the error). Store `parser_version`.
- `GET /api/documents/<id>/pdf` streams the file with `Content-Disposition: inline`.
**Acceptance criteria:**
- `common.py` has thorough unit tests: at least 25 date-string cases, including "Aug 30 - Sep 30", "Oct 10 – Nov 3", "August 30, 2026 through January 31, 2027", "Dec 25 - Dec 31", and "Nov 15 - Jan 15" crossing the year; at least 10 unit-list cases; and all qualifier codes.
- Uploading the same file twice returns 409.
- A registered no-op parser plus a fixture PDF produces a `needs_review` document.

---

### T-11 · Deer general-season parser
**Depends on:** T-10 · **Size:** M
**Description:** `importer/deer.py`, generalized from `docs/spike_deer_parser.py`. Scope: all "Regular Deer Tag" and "White-tailed Deer Tag" **general** tables (Any Weapon, Archery Only, Muzzleloader Only, Short Range). Skip "Controlled" tables (T-17a). Rules: crop sides; header row must be `Unit(s) | Antlered | Antlerless | Notes`; carry the title across pages; carry the unit list across continuation rows; each date range in the Antlered/Antlerless cells produces one season (`sex` = antlered/antlerless); qualifiers in the 60 chars after a date attach to that season; the Notes column parses to restriction codes plus `notes` text; "None" means no row. Map titles: Regular → `deer_regular`, White-tailed → `deer_whitetail` (implicitly add `whitetail_only` only for display, since the tag itself is WT-only). Set `source_page`, `source_text`.
**Fixtures:** cut pages 12–16 and 18–21 of the 2026 Big Game PDF into `tests/fixtures/bg2026_deer_pXX.pdf` (use `pypdf` to split) with hand-verified expected JSON for at least 4 pages.
**Acceptance criteria:**
- On the full 2026 PDF: ≥ 230 rows. Every GMU except {44, 45, 52, 54, 68A, YNP} has at least one Regular-tag general season. Fixture expectations match exactly.
- Spot checks: Unit 39 Regular any-weapon antlered **Oct 10–Oct 31**; Units 1, 2, 3, 4A, 5, 6 antlered Oct 10–31 carries `whitetail_only`; Unit 22 antlered carries `two_point_only`.
- Implements a **text-path twin** for these tables and the `two_path_agreement`, `date_token_accounting`, `unit_tokens_valid` and `general_coverage` checks from §8.7 for section `deer_general`. All pass on the 2026 PDF.
- **Matches `tests/golden/2026/deer_general.json` exactly** (produced in T-22).

---

### T-12 · Elk A/B zone parser (with spike)
**Depends on:** T-10 · **Size:** L
**Description:**
**T-12a (spike, time-box 4 h):** elk zone pages (2026 PDF pp. 37–50) are rotated calendar graphics. **Already known:** text is recoverable with `extract_text(line_dir_rotated='ltr', char_dir_rotated='btt')`, e.g. p. 38 gives "Panhandle Zone (Units 1, 2, 3, 4, 4A, 5, 6, 7, 9)", "Aug 30 - Sep 30 Archery only — any elk", "A Tag". The spike must work out how to assign each label to its zone block and A/B row: use `extract_words` with the same rotation options, then cluster by the rotated y-coordinate (zone bands) and by the horizontal rules or colored bars (`page.rects`, `page.lines`) separating the A and B rows. Write the findings in `docs/elk_parser_notes.md`.
**T-12b:** implement `importer/elk.py`. Output per zone × tag (A/B) × weapon window: `tag_type` = `elk_zone_a|elk_zone_b`, `zone` = the zone name normalized to the GIS `Elk_Zone` values (keep a mapping table for spelling differences, e.g. "Smoky – Bennett"), `sex` (`antlered`/`either`/`antlerless`), restrictions (`spike_only`, `brow_tined`, `portion_of_unit` with note). `season_units` = units of that zone from `zone_units` (also parse zone→unit lists from the PDF's zone table, and if it differs from GIS, update `zone_units` for that year and flag a warning).
**Acceptance criteria:**
- All 28 zones present for both A and B tags where the regs offer them. A zone with only one tag type is acceptable if the PDF says so.
- Fixture tests for at least 3 zones are hand-verified against the PDF.
- If the automated approach yields confidence < 0.8 for a zone, rows are still staged (flagged) rather than dropped.
- Accuracy: every date token on each zone page is consumed exactly once (`date_token_accounting`), and the zone→unit list printed in each zone header matches GIS `Elk_Zone` membership, with differences listed as `fail`. Hand-verified golden file `tests/golden/2026/elk_zones.json` for all zones (added to T-22's scope when this ticket starts).

---

### T-13 · Pronghorn, black bear, mountain lion, wolf parsers
**Depends on:** T-10 · **Size:** M
**Description:** One module each (or the shared `bear_lion_wolf.py`), following the T-11 pattern. Bear has spring and fall seasons, plus `restrictions` for baiting/hound notes as informational codes (add `bait_allowed`, `hounds_allowed` as informational). Lion and wolf seasons are often "All units except …" or long date spans with "statewide" text, so support `units: ALL` and `ALL EXCEPT [...]` in `common.parse_units`. Pronghorn general archery seasons are by unit list.
**Acceptance criteria:** fixture tests per species (≥ 1 page each); after import, each species has non-empty published-able rows; spot checks documented in the PR.

---

### T-14 · Upland bird parser + county overlay
**Depends on:** T-02, T-10 · **Size:** L
**Description:** (a) Implement `scripts/build_county_overlay.py` per §9 and commit `static/data/county_units.json`. (b) `importer/upland.py` for the Upland brochure's **upland game bird** sections (FOREST GROUSE, CALIFORNIA AND BOBWHITE QUAIL, CHUKAR AND GRAY PARTRIDGE, SAGE-GROUSE, SHARP-TAILED GROUSE, PHEASANT; turkey is T-23); per section, parse `AREA n` blocks: the definition text, then `Seasons` lines (one line per year of the 2-year brochure, e.g. `2026 – 2027: August 30, 2026 through January 31, 2027`), and bag/possession limits into `bag_limit`. Resolve area text to `season_units` via §5.2 (county lists, "except units …", "remainder of the state", and explicit unit lists). Tag type `hunting_license` (an Idaho hunting license is required; pheasant on WMAs requires the upland game bird permit, so add the `wmb` permit code as an informational restriction `wmb_permit_on_wma`).
**Acceptance criteria:** the 2026–27 Forest Grouse Area 1 resolves to units in the listed counties minus 19A, 20A, 22, 23, 26; Area 2 is the remainder; both years' seasons are created; partial units are marked `partial` with a note naming the counties. §8.7 checks (`two_path_agreement`, `date_token_accounting`, `unit_tokens_valid`) pass for every bird section, and each section matches a hand-verified `tests/golden/2026/upland_<species>.json`.

---

### T-15 · Regulations tab: upload & review & publish UI
**Depends on:** T-10, T-11 (for real data), T-22 (checks + publish gate), T-03 · **Size:** L
**Description:** `#/regs` per §8.4: a drag-and-drop upload card (shows sha/dup warnings, a spinner, then the result counts), a documents table by year with status badges, and a review screen at `#/regs/<docId>` with a grouped row list, confidence coloring (`--sc1`…`--sc5`), inline editors (unit chip editor with autocomplete from units, date inputs, selects for tag/weapon/sex, restriction multi-select), an embedded PDF iframe synced to the selected row's page, a diff vs prior published year (API: `GET /api/documents/<id>/diff`, implemented here, matching on species+tag+zone+weapon+sex+unit set), approve/reject per row, approve-all-passing (only rows with `extract_agree = 1` in sections whose checks all pass), keyboard shortcuts (j/k/a/r/e), and a Publish button (§8.5 supersession implemented in `api.py`/`db.py` here) with a confirmation modal summarizing counts.
**Acceptance criteria:** end-to-end test: upload the 2026 deer fixture, approve all, publish, and `/api/availability` returns open units. Publishing a supplement supersedes the matching rows. The UI prevents publishing while staged rows remain. **The Accuracy panel (§8.4, §8.7) renders every `import_checks` row. Publish is blocked (UI and API both return 409) while any check is `fail` and not waived, or any `extract_agree = 0` row is unreviewed. Waiving requires a typed reason, which is stored and displayed.**

---

### T-16 · Season Table, manual editor, CSV import/export
**Depends on:** T-06, T-15 · **Size:** M
**Description:** `#/seasons`: a filterable (year, species, tag, weapon, unit search, status) and sortable table of seasons; clicking a row highlights its units on the map (switch to the map tab with highlight). "+ Add season" opens the same editor as review; rows are saved under an auto-created `manual` source document for the year, published immediately but badged "manual". CSV export/import with columns `license_year,species,tag_type,zone,hunt_number,hunt_class,weapon,sex,start_date,end_date,units,restrictions,notes,source_page` (units and restrictions `;`-separated). Imported CSV rows go to staging under a `manual` doc for review. Ship `docs/seasons_template.csv` with 3 example rows.
**Acceptance criteria:** CSV round-trip is lossless (export → import → export is identical); invalid rows report line numbers.

---

### T-17a · Controlled-hunt table parser (MVP)
**Depends on:** T-10 · **Size:** L
**Description:** `importer/controlled.py`, productionized from `docs/spike_controlled_parser.py` (read it first; it runs on the real PDF and prints every invariant). Scope: every controlled-hunt table in the Big Game PDF: deer, elk, pronghorn, bear (2026: pp. 22–31, 51–59, 64–67, 73). **Locate pages by content** (`"Hunt No."` in page text), never by hard-coded page numbers.
- Columns: `Hunt No. | Controlled Hunt Areas | Tags | Season Dates | Notes`.
- **Superscripts:** filter chars below 75 % of the page's median font size before `find_tables()` (§8.2; the spike uses a fixed 7 pt, which is only safe for this brochure), then read markers from each area cell's bbox on the unfiltered page. Resolve marker meanings from the footnote legend at the bottom of the page, not a hard-coded map. Fail the row if a marker has no legend entry. 2026 legend: `a` = "This hunt includes other units or parts of other units", `b` = "This hunt includes only a portion of this unit".
- `(See pg N)` → `hunt_areas.description_page`. **N is the brochure's printed page number, which is PDF page index − 1 in 2026** ("See pg 31" → PDF page 32). Build a printed→physical map from each page's footer ("…idfg.idaho.gov 21" on PDF p. 22) and store **physical** pages everywhere (`source_page`, `description_page`), keeping the printed number only for display. Add check `page_ref_resolves`: every "See pg N" must map to a page containing the referenced hunt area's `Hunt Area <id> —` entry.
- **Title carry-forward:** a table that starts with the `Hunt No.` header inherits the previous title. Map titles → (`species_id`, `hunt_class`, `weapon`, `sex`, restrictions): "Antlered" → antlered; "Antlerless" / "Extra Antlerless" → antlerless (+`hunt_class='extra'`); "Either Sex" → either; "Doe or Fawn Pronghorn" → antlerless; "Archery Only … Archery Permit Required" → archery; "Muzzleloader Only" → muzzleloader; "Short Range Weapon" → short_range; "Youth Only" → +`youth_only`; "Outfitter Allocation" → `hunt_class='outfitter'` +`outfitter_only`. **An unrecognized title is a `fail`, not a default.**
- **Year resolution:** the year printed in the title wins ("**2027** Controlled Hunts — Extra Antlerless Elk" → 2027 dates). Spring bear tables (application period "January 15 – February 15, 2027") → 2027 dates. If the end is before the start, the end rolls into the next year. `license_year` = the calendar year of the start date. Every row whose year differs from the document year gets a `yoy`-style review flag.
- Tags column → `tag_quota` (`Unlimited` → NULL). Notes column → restriction codes + `notes`. "See Note N, Page P" is stored verbatim and the referenced note text is attached.
- Writes `seasons` (`tag_type_id='controlled'`, `hunt_number`, `hunt_area`) and `hunt_areas` rows. Units come from T-17b. Until T-17b runs, the area's base unit (the leading `\d+[A-C]?` of the area id) is staged as `partial`.
- Implements checks for each section `controlled_<species>`: `two_path_agreement` (text-layer regex twin, as in the spike), `tag_checksum`, `hunt_number_completeness`, `no_duplicate_keys`, `date_token_accounting`, `corrections_applied`.
- **Write it as a shared, brochure-agnostic module.** T-23 (turkey), T-25 (moose/sheep/goat) and T-26 (swans) reuse it, so column positions, footnote legends, checksum header patterns and species prefixes must all be data-driven per brochure, not hard-coded for Big Game.
**Acceptance criteria (2026 Big Game PDF):**
- Exactly **469** controlled hunts: deer **188** (1001–1188, no gaps), elk **221**, pronghorn **54** (4001–4054), bear **6**. 0 duplicates, 0 unparsed date cells.
- `tag_checksum` passes: deer **13,226**, elk **18,670**, pronghorn **2,040**.
- `two_path_agreement`: 0 disagreements. Footnote counts **131 `a` / 77 `b`**.
- Spot checks: **1001** area `1-1`, footnote a, Aug 30–Dec 1, antlered, "See pg 31" → physical description page 32; **1002** area `11`, no footnote, Oct 10–Nov 3, 50 tags; **1028** area `38-1`, footnote b, Oct 10–Nov 24; **2032** elk antlered, area `51`, Nov 1–Nov 30; **2111** elk either-sex, area `30A-1`, footnote b, Aug 1–Aug 29 (its text-layer line is merged with a neighbor; the table path must still get it); **4014** pronghorn either-sex, area `50`, Sep 25–Oct 24, "See pg 67" → physical page 68; **8501** fall bear, area `1-1`, footnote b, Sep 15–Oct 9.
- Matches `tests/golden/2026/controlled_{deer,elk,pronghorn,bear}.json` exactly (T-22).

---

### T-17b · Hunt-area descriptions & unit coverage (MVP)
**Depends on:** T-17a, T-02 · **Size:** L
**Description:**
1. `scripts/build_hunt_area_coverage.py --year YYYY`: download GIS `MapServer/4` features (`BigGame`, `HuntArea`, `Year`, geometry, `outSR=4326`) for the year, falling back to the latest year that has big-game polygons (2025 as of today) and recording `geometry_year`. Project to EPSG:8826, intersect each hunt-area polygon with each GMU, and write `data/hunt_area_coverage/<year>.json`: `{"deer":{"1-1":{"geometry_year":2025,"units":{"1":1.0,"2":1.0,…}}}}`, where each value is the fraction of the **GMU's** area inside the hunt area. Drop slivers < 0.5 % (boundary noise from simplification).
2. `importer/hunt_areas.py`: parse the description pages (deer pp. 32–34, elk 60–62, pronghorn 68 in 2026; locate by the heading "CONTROLLED HUNT AREA DESCRIPTIONS"). These are **two-column**: split at the gutter (§8.2). Entries start with `Hunt Area <id> —`. Store the full verbatim text in `hunt_areas.description`. Also extract the unit tokens mentioned ("All of Units 1, 2, 3…", "That portion of Unit 48…").
3. Build `season_units`: units with fraction ≥ 0.98 → `full`; 0.005–0.98 → `partial` with `coverage_frac`; `coverage_note` = the first sentence of the description plus `(see p. N)`.
4. Checks for each section `hunt_areas_<species>`: `gis_area_match` (every area id has a polygon; fail otherwise); `unit_coverage` (the unit tokens in the description text must equal the set of GIS units with fraction ≥ 0.02; any difference is `fail` with both lists shown); footnote consistency (footnote `b` ⇒ at least one unit `partial`; footnote `a` ⇒ ≥ 2 units **or** described units ≠ the base unit; no footnote ⇒ exactly one unit at ≥ 0.98, i.e. "All of Unit X"). `geometry_year < license_year` → `warn` on every affected area.
**Acceptance criteria:**
- 2026: 469/469 hunt areas resolve to ≥ 1 unit, using 2025 geometry with warnings.
- Area `1-1` (deer) covers units 1, 2, 3, 4, 4A, 5, 6, 7, 9 (per its description, p. 32).
- Area `11` (hunt 1002) → exactly unit 11, `full`.
- Area `38-1` (hunt 1028, footnote b) → unit 38 `partial`.
- The description of deer `1-1X` begins "That portion of Unit 1 within Boundary County east of the Selkirk Mountains crest" (verifies the column split didn't interleave).
- All three consistency checks pass, or any exception is listed with page references for review.

---

### T-17c · Controlled tags in My Tags + engine wiring (MVP)
**Depends on:** T-17b, T-09, T-06 · **Size:** M
**Description:** `GET /api/hunts/<year>/<hunt_number>` returns the hunt (species, class, weapon, sex, dates, area, verbatim description, page, units with coverage). Add Tag → Controlled: type a 4-digit number, then a live lookup previews the hunt card. Save is disabled for unknown numbers or numbers whose seasons are only `rejected`. Tag cards show "Controlled #1002 · Deer · Unit 11 · Oct 10–Nov 3". The engine applies §7.2's controlled-hunt rules: matching by hunt number **and** license year, partial coverage → restricted, archery/muzzleloader permit requirements, and outfitter hunts hidden unless held.
**Acceptance criteria:**
- With only controlled tag 2026 **#1002** and dates Oct 17–18 2026: exactly unit **11** is green. Nothing else lights up.
- With **#1028** on Oct 17–18: unit 38 is **yellow** (`portion_of_unit`) and the tooltip shows the verbatim area description and page.
- A 2025 tag #1002 does **not** light up anything for 2026 dates.
- Controlled + Regular deer tag together: the union shows, and each tooltip season names the tag that unlocks it.
- Archery-only controlled hunt without the Archery Permit → not shown (and the reason appears with include_untagged on).

---

### T-22 · Accuracy harness, golden dataset & publish gate (MVP — highest priority)
**Depends on:** T-10 · **Size:** L
**Description:** Implements §8.7 end to end.
1. `importer/checks/`: a check registry (`@check(section_glob, name)`), each check returning `CheckResult(status, expected, actual, detail)`, all persisted to `import_checks` at the end of `run_import`. Shared implementations: two-path comparison helper, `date_token_accounting` (scan page text inside each table's bbox for `Mon D - Mon D` tokens and require that each maps to exactly one season by `source_bbox`), `unit_tokens_valid`, `no_duplicate_keys`, `general_coverage`, `golden_match`, `yoy_diff`.
2. **Golden dataset for 2026.** `scripts/make_golden.py` dumps a section's parsed rows to `tests/golden/2026/<section>.json` in a canonical sorted form. A human then **verifies every row against the PDF page by page** and records it in `tests/golden/2026/VERIFICATION.md` (a table of page, rows on page, verified-by, date, and corrections made). MVP sections: `deer_general`, `controlled_deer`, `controlled_elk`, `controlled_pronghorn`, `controlled_bear`, `hunt_areas_*`. Golden files are **only** committed after verification. A row the parser got wrong is fixed in the golden file and becomes a failing test until the parser is fixed.
3. `tests/test_golden.py`: re-parses the 2026 PDF (downloaded once to a cache dir; skipped with a clear message if offline) and asserts an exact match for every golden section. CI runs it with the PDF cached by URL hash.
4. Publish gate in `api.py` (§8.4): `POST /publish` → 409 with the list of blocking checks and rows unless the gate is satisfied. Waiver endpoint requires a non-empty reason.
**Acceptance criteria:**
- All checks implemented with unit tests, including a deliberately corrupted fixture for each (e.g. drop one table row → `hunt_number_completeness` and `tag_checksum` fail; strip superscript filtering → `two_path_agreement` fails on 1001).
- `VERIFICATION.md` covers every page of every MVP section, with no unverified pages.
- `pytest -q` passes with golden match = 0 diffs on 2026.
- Publish is impossible via the API while any unwaived `fail` exists (API test).

---

### T-23 · Wild turkey: general + controlled hunts
**Depends on:** T-14 (upland doc classification + 2-year handling), T-17b (polygon coverage machinery) · **Size:** M
**Description:** `importer/turkey.py` for the Upland brochure's turkey sections (printed pp. 18–25: general information, rules, distribution, **general seasons** p. 22, **general season maps** p. 23, **controlled hunt seasons** pp. 24–25). General seasons: spring and fall, by GMU list. Parse the unit lists from the table text only. The p. 23 maps are images and are **not** a data source; if a table refers to "see map", stage the row as `fail` for manual unit entry. Youth seasons → `youth_only`. Controlled turkey: reuse the T-17a parser (same `Hunt No.` table layout; verify on the real pages first) and T-17b coverage. GIS MapServer/4 already has **2026 turkey** hunt-area polygons (15 features, e.g. `21-1`, `22-3`, `38-2`, `50-2`, `50-3`), so no year lag. The brochure covers 2026 and 2027: create seasons for both years with each year's printed dates. Tag types: `turkey_general` (spring/fall as printed; if the regs distinguish spring and fall tags, model them as two tag types and say so in the PR) and `controlled`.
**Acceptance criteria:** all §8.7 checks pass for `turkey_general` and `controlled_turkey` (including `tag_checksum` if the section header prints a total); every controlled turkey hunt area matches a 2026 GIS polygon; hand-verified golden files for both sections; spot checks (three general rows and two controlled hunts, chosen by the implementer, with page refs) listed in the PR.

---

### T-24 · Small game, furbearer hunting, predatory/unprotected, falconry
**Depends on:** T-14 · **Size:** L
**Description:**
1. `scripts/build_region_overlay.py`: GIS `https://gisportal-idfg.idaho.gov/hosting/rest/services/WC_Regions/MapServer/0` (8 polygons; **merge "McCall SubRegion" + "Nampa SubRegion" into "Southwest"**). Intersect with GMUs and write `static/data/region_units.json` with full/partial fractions, like the county overlay. Check: the regions listed in the brochure's furbearer tables must exactly equal the 7 resulting region names.
2. **Cottontail & snowshoe hare** (printed p. 16) and **red squirrel** (p. 17): parse season tables/areas → seasons for both brochure years.
3. **Furbearer hunting** (pp. 31–33, "FURBEARER TRAPPING AND HUNTING SEASONS BY REGION"): import **only** species marked with `*` ("Indicates species can also be hunted"): badger, bobcat, red fox. Ignore trapping-only species. Season area = region → GMUs via the overlay. Region text exceptions (named drainages, waters) → `coverage='partial'` + verbatim note. Example (2026): Bobcat Dec 14 – Feb 16; Fox Oct 10 – Mar 31 in one region and "July 1 – June 30: Year-round" in another. Bobcat → informational restriction `harvest_report_required`.
4. **Predatory & unprotected** (pp. 35–37, 45; ground squirrels p. 42): one species `predatory_unprotected` with a season of Jul 1 – Jun 30 covering every unit. `notes` lists the named species verbatim from the text (2026: predatory = coyote, raccoon, jackrabbit, skunk, weasel; unprotected per p. 45). Ground-squirrel closures/exceptions from p. 42 go in as `see_notes` with verbatim text. This is prose, so the golden file is mandatory, and the check `prose_unchanged` compares the normalized source paragraphs with last year's and fails on any change.
5. **Falconry** (p. 30): seasons with `weapon='falconry'` on the matching species, requiring the new `falconry` permit (§5 `my_permits`). Hidden unless the user holds that permit.
**Acceptance criteria:** the region overlay assigns all 99 non-YNP GMUs to ≥ 1 region (sum of fractions per GMU ≈ 1.0 ± 0.02); with only a hunting license on 2026-12-18, bobcat shows the regions whose bobcat season includes that date; `predatory_unprotected` shows every unit green on any date, with its notes; trapping-only species never appear; all §8.7 checks pass and each section matches its hand-verified golden file.

---

### T-25 · Moose, bighorn sheep & mountain goat brochure
**Depends on:** T-17a, T-17b, T-22 · **Size:** M
**Description:** Classify `kind='moose_sheep_goat'` (title "Idaho Moose, Bighorn Sheep & Mountain Goat YYYY & YYYY Seasons & Rules", so `license_year` = first year and `year_end` = second; seasons are created for **both** years with the printed dates). All hunts are controlled, so drive the shared T-17a parser with an MSG profile, reproducing `docs/spike_msg_parser.py` (read it and run it first):
- `dedupe_chars(tolerance=1)` on every page (the goat page is printed 4× fake-bold).
- Header-located columns: every `Hunt No.` cell starts a 4-column hunt record (two per row for sheep/goat; column 1 for the nested California table).
- Species by hunt-number prefix **and** section title: 3xxx moose (antlered / antlerless from the title), 5xxx Rocky Mountain bighorn, 7xxx California bighorn, 6xxx goat. If the prefix and the title disagree → fail.
- The full-size `*` stays part of `hunt_area` (it matches GIS). Superscript markers (relative threshold) are resolved from each page's legend (2026: `a` = wilderness/extreme terrain → informational restriction `wilderness_terrain`). Area suffix `L` (late) is part of the id.
- Hunt-area descriptions: moose physical pp. 17–18, sheep p. 24 (locate by heading "HUNT AREA DESCRIPTIONS"), using T-17b coverage with GIS BigGame `Moose` / `Rocky Mtn Sheep` / `California Sheep` / `Goat`.
- Corrections pages (pp. 2–3): parse each item ("<SPECIES> – HUNT NO. NNNN (AREA X)" + text) → `corrections_applied`. Hunts with 0 tags → status `closed`, kept for history, never shown as open.
- Lottery/auction tags: no table row; the `tag_checksum` detail must name them (2025 & 2026: sheep header 100 = 98 in tables + lottery + auction).
**Acceptance criteria (2025 & 2026 MSG PDF):** exactly **145** hunts (moose **94**, Rocky Mtn bighorn **28**, California bighorn **4**, goat **19**); 0 duplicates; 0 unparsed dates; `hunt_number_completeness` clean (hunt-table pages only); checksums moose **513** (510 + 3), goat **40**, Rocky Mountain late rams **16**, California **5** with the 7 → 5 gap explained by corrections 7001/7002; **145/145** exact GIS matches; spot checks **3001** moose `1-1*`, 10 tags, Sep 15 – Dec 1; **6004** goat area `22`, 3 tags, Aug 30 – Nov 12; **7001** California area `40`, 0 tags → `closed`; **5005** area `20A*` (superscript `a` captured as a flag, not glued onto the id); golden files for every section.

---

### T-26 · Migratory game birds brochure
**Depends on:** T-14 (county overlay), T-17a/b (swan controlled hunts), T-22 · **Size:** L
**Description:** Classify `kind='migratory'` ("Idaho Migratory Game Bird YYYY-YYYY Seasons & Rules"). One season spans two calendar years, so dates carry explicit years. `license_year` per season = the calendar year of each day (§7.4 crossover applies: a Jan 2027 duck day needs 2027 license/permits).
1. **Ducks/snipe/coot (physical p. 8), Canada geese (p. 9), white-fronted (p. 10), light geese (p. 11):** pages are multi-column with sidebars, so crop to the main column (detect the sidebar box via `page.rects` and exclude it; the "Areas Closed to Hunting… see pages 27–28" sidebar must not leak into area text). Parse `AREA n` blocks: bullet date lines ("• October 19, 2026 – January 31, 2027"; "• September 1 – 15, 2026"), sub-seasons ("• Scaup Season: November 7, 2026 – January 31, 2027" → a `duck` season with `sub_species='scaup'`), and the definition ("includes the following counties: …", "that portion of X County …" → `partial` with verbatim note, "all parts of the state NOT included in areas 2 and 3" → remainder per §5.2). Bag/possession limits → `bag_limit`. Youth/veteran days, if printed, → `youth_only` / `veteran_only` (new narrowing code).
2. **Swans (p. 12):** controlled table, header `Hunt No. | Controlled Hunt Area | Tags | Season Dates | Notes`, dates like "Oct. 10 –⏎Dec. 1". The Notes column holds the area's county list, which resolves through the county overlay (there's no GIS polygon for swans; verify and say so in the PR). Hunt numbers 93xx.
3. **Dove & crow (p. 13):** statewide seasons, plus the extended falconry season (`weapon='falconry'`). Eurasian-collared dove is unprotected year-round → note on `dove`.
4. **Sandhill crane (p. 14):** table `Hunt Area | Season | Tags` with hunt-area carry-forward ("1-Bear Lake" → September 1–30 (300), October 1–30 (150)); 6 areas in 2026. Area definitions are bullet prose ("Includes all of Bear Lake County and all of Caribou County, except that portion lying within …") → county overlay + `partial`. Tag type `sandhill_crane` with `zone` = crane area.
5. **Requirements** lists per species ("Migratory Bird (HIP) Permit—REQUIRED", "Federal Migratory Bird (Duck) Stamp—NOT REQUIRED", "Nontoxic Shot—NOT REQUIRED") → required permits per species (`hip`, `federal_duck_stamp`) used by the engine like the archery permit. "Nontoxic shot required" → informational restriction.
6. Shooting-hour tables (pp. 16–17) are **not** imported; the Unit Brief links to the page.
7. New `common.parse_dates` formats with tests: "Oct. 10 – Dec. 1", "September 1–30", "September 1 – 15, 2026", "October 19, 2026 – January 31, 2027".
**Acceptance criteria (2026–27 PDF):** duck Area 1 = Oct 19 2026 – Jan 31 2027, Area 2 = Oct 3 2026 – Jan 15 2027, Area 3 = Oct 10 2026 – Jan 22 2027, with scaup sub-seasons Nov 7 – Jan 31 / Oct 3 – Dec 27 / Oct 29 – Jan 22; Area 3 = Benewah, Bonner, Boundary, Kootenai, Shoshone counties' units; Canada goose Area 4 has two seasons (Sep 1–15 and Oct 3 – Dec 31, 2026); swan #9301 Area 1, 50 tags, Oct 10 – Dec 1; crane has 6 areas with season/tag counts exactly as printed (e.g. 1-Bear Lake 300 + 150); dove Sep 1 – Oct 30, 2026; with ducks selected on 2026-10-17 and HIP + duck stamp held, Area 2 and Area 3 units are open, Area 1 is closed, and Area 3 shows the informational "scaup closed" badge; no sidebar text appears in any area definition; all §8.7 checks pass; golden files for every section.

---

### T-18 · Supplemental proclamations
**Depends on:** T-10, T-15 · **Size:** S
**Description:** `importer/supplemental.py`: classify `kind='supplemental'`, `priority = 10 + n`. Parse whatever tables or "change" statements it contains, using the T-11/T-13 parsers on matching tables plus a generic "Unit X … season … dates" text pattern. Everything stages at confidence ≤ 0.8 so it is always reviewed. Publishing applies §8.5.
**Acceptance criteria:** a test with a synthetic supplement that changes one deer season's end date supersedes exactly that row.

---

### T-19 · Test fixtures, regression suite & data QA report
**Depends on:** T-11 (grows with each parser) · **Size:** M
**Description:** `scripts/make_fixtures.py` splits named pages from a source PDF into `tests/fixtures/`. Add `scripts/qa_report.py --year 2026`, which prints coverage: units with no general season per species, seasons with confidence < 0.9, seasons with unknown units, overlaps that look like duplicates, and year-over-year changes. Wire the QA report into the review screen as a collapsible "Data QA" panel (`GET /api/documents/<id>/qa`).
**Acceptance criteria:** the QA report runs in under 5 s and its output is included in the README as an example.

---

### T-20 · IDFG Hunt Planner cross-check (optional)
**Depends on:** T-11 · **Size:** M
**Description:** `scripts/crosscheck_huntplanner.py --year 2026 --species deer --sample 25`: for a random sample of published (unit, season) rows, fetch the corresponding IDFG Hunt Planner pages (`fishandgame.idaho.gov/ifwis/huntplanner/…`). Be polite: 1 request every 2 seconds, a custom User-Agent, and an HTML cache in `data/cache/huntplanner/`. Compare dates and weapon, and report mismatches. Manual-run only. Never runs automatically, never in CI.
**Acceptance criteria:** produces a mismatch report. Zero network calls in the test suite (use recorded HTML fixtures).

---

### T-21 · Settings, backup/restore, docs
**Depends on:** T-09 · **Size:** S
**Description:** `#/settings`: profile fields (shared with T-09), `GET /api/backup` (consistent snapshot via `sqlite3.Connection.backup`), `POST /api/restore` (validates schema_version, keeps the old DB as `planner.db.bak-<timestamp>`), and "Reset all data" behind a typed confirmation. README sections: annual workflow ("When the new regs come out: 1. Regulations tab → upload… 2. Review… 3. Publish… 4. My Tags → Copy from last year"), troubleshooting, data sources and licensing, and the disclaimer.
**Acceptance criteria:** backup → reset → restore returns identical availability results (test).

---

## 13. Annual workflow (what using it looks like)

1. **Spring (Big Game regs drop):** open the app → 📥 Regulations → drop `seasons-rules-big-game-2027.pdf` → fix anything the Accuracy panel flags, review changed rows using the side-by-side PDF and the "changed since 2026" diff → **Publish**. (The Upland brochure covers two years, so it's uploaded every other year: 2026, 2028, ….)

   **Upload cadence** (each brochure is just another upload; the app detects which kind it is):

   | Brochure | Covers | Current | Next expected |
   |---|---|---|---|
   | Big Game Seasons & Rules | 1 year | 2026 | 2027 (spring) |
   | Upland Game, Turkey & Furbearer | 2 years | 2026 & 2027 | 2028 |
   | Moose, Bighorn Sheep & Mountain Goat | 2 years | 2025 & 2026 | 2027 |
   | Migratory Game Bird | 1 season (Sep–Mar) | 2026–2027 | 2027–2028 (late summer) |
   | Supplements / corrections | — | as published | — |
2. **Summer:** 🎟️ My Tags → "Copy from 2026" → adjust (bought an elk B tag in the Sawtooth zone, archery permit ✓, drew controlled hunt #1002).
3. **Any Thursday in October:** open the app. It's already on Fri–Sun of this weekend. Pick 🦌 Deer and the map shows where to go. Click a unit for the brief and the source page.
4. **Mid-season supplement published:** upload it, review the few changes, publish. Superseded seasons drop off the map automatically.

---

## 14. Implementing with agents

**Use one fresh agent session per ticket.** Don't run the build in the long design session. Each turn of a session re-sends the whole conversation, so a session that already holds the design research costs several times more per turn than a fresh one. Tickets are written to be self-contained. A new agent reads `docs/AGENT_CONTEXT.md` (shared context, constraints, commands), then the ticket, then only the sections of this document the ticket cites. In Hermes, `/ihp <ticket>` does this.

- **No grouping.** Every ticket gets its own session and its own PR, including small, closely related ones (e.g. T-01/T-04/T-05, T-07/T-08). Context is handed off through the repo: merged code, the PR description, and `AGENT_CONTEXT.md`. This matters most for the parser tickets (T-11, T-17a, T-17b, T-12, T-14, T-23, T-24, T-25, T-26), which are the hardest and need the most room in the context.
- **Merge before you build on it.** Start a ticket only after its dependencies' PRs are merged into `main`.
- **Order** (every step's dependencies are in earlier steps): T-01 → T-02, T-03, T-05 (in parallel) → T-04 → T-10 → **T-22** → T-11 and T-17a (in parallel) → T-17b → T-06 → T-09, T-15 (in parallel) → T-07 → T-08 → T-17c → T-16 → Phase 2+.
- **Hand-off lives in the repo, not in chat:** each ticket ends with a PR whose description lists what was built, check results, and any deviation from this doc. If the design changes, update `DESIGN.md` in the same PR.
- **Golden-data verification (T-22) is human work.** An agent can prepare the page-by-page checklist, but Josh (or a reviewer reading the PDF) ticks off `VERIFICATION.md`.

**Ticket → GitHub issue map.** Every ticket in §12 is a GitHub issue with the ticket text verbatim, acceptance criteria as a checklist, labels (`phase:*`, `size:*`, area), a phase milestone, and native "blocked by" links. Issue numbers match ticket numbers through T-16, then shift by one from T-17a onward. `docs/tools/make_issues.py --link` re-syncs the issue bodies after a ticket is edited here.

| Ticket | Issue | Title |
|---|---|---|
| T-01 | [#1](https://github.com/joshualaney23/Idaho-Hunt-Planner/issues/1) | Repository scaffold & dev environment |
| T-02 | [#2](https://github.com/joshualaney23/Idaho-Hunt-Planner/issues/2) | GMU geometry build script |
| T-03 | [#3](https://github.com/joshualaney23/Idaho-Hunt-Planner/issues/3) | Theme & app shell (static UI) |
| T-04 | [#4](https://github.com/joshualaney23/Idaho-Hunt-Planner/issues/4) | Database schema, migrations & seed |
| T-05 | [#5](https://github.com/joshualaney23/Idaho-Hunt-Planner/issues/5) | Date utilities (Python + JS twins) |
| T-06 | [#6](https://github.com/joshualaney23/Idaho-Hunt-Planner/issues/6) | Availability engine + `/api/availability` |
| T-07 | [#7](https://github.com/joshualaney23/Idaho-Hunt-Planner/issues/7) | SVG map component + filter bar wiring |
| T-08 | [#8](https://github.com/joshualaney23/Idaho-Hunt-Planner/issues/8) | Results table, Unit Brief drawer, map zoom |
| T-09 | [#9](https://github.com/joshualaney23/Idaho-Hunt-Planner/issues/9) | My Tags & Permits (UI + API) |
| T-10 | [#10](https://github.com/joshualaney23/Idaho-Hunt-Planner/issues/10) | PDF ingest framework (upload, store, classify, extract, stage) |
| T-11 | [#11](https://github.com/joshualaney23/Idaho-Hunt-Planner/issues/11) | Deer general-season parser |
| T-12 | [#12](https://github.com/joshualaney23/Idaho-Hunt-Planner/issues/12) | Elk A/B zone parser (with spike) |
| T-13 | [#13](https://github.com/joshualaney23/Idaho-Hunt-Planner/issues/13) | Pronghorn, black bear, mountain lion, wolf parsers |
| T-14 | [#14](https://github.com/joshualaney23/Idaho-Hunt-Planner/issues/14) | Upland bird parser + county overlay |
| T-15 | [#15](https://github.com/joshualaney23/Idaho-Hunt-Planner/issues/15) | Regulations tab: upload & review & publish UI |
| T-16 | [#16](https://github.com/joshualaney23/Idaho-Hunt-Planner/issues/16) | Season Table, manual editor, CSV import/export |
| T-17a | [#17](https://github.com/joshualaney23/Idaho-Hunt-Planner/issues/17) | Controlled-hunt table parser (MVP) |
| T-17b | [#18](https://github.com/joshualaney23/Idaho-Hunt-Planner/issues/18) | Hunt-area descriptions & unit coverage (MVP) |
| T-17c | [#19](https://github.com/joshualaney23/Idaho-Hunt-Planner/issues/19) | Controlled tags in My Tags + engine wiring (MVP) |
| T-18 | [#20](https://github.com/joshualaney23/Idaho-Hunt-Planner/issues/20) | Supplemental proclamations |
| T-19 | [#21](https://github.com/joshualaney23/Idaho-Hunt-Planner/issues/21) | Test fixtures, regression suite & data QA report |
| T-20 | [#22](https://github.com/joshualaney23/Idaho-Hunt-Planner/issues/22) | IDFG Hunt Planner cross-check (optional) |
| T-21 | [#23](https://github.com/joshualaney23/Idaho-Hunt-Planner/issues/23) | Settings, backup/restore, docs |
| T-22 | [#24](https://github.com/joshualaney23/Idaho-Hunt-Planner/issues/24) | Accuracy harness, golden dataset & publish gate (MVP — highest priority) |
| T-23 | [#25](https://github.com/joshualaney23/Idaho-Hunt-Planner/issues/25) | Wild turkey: general + controlled hunts |
| T-24 | [#26](https://github.com/joshualaney23/Idaho-Hunt-Planner/issues/26) | Small game, furbearer hunting, predatory/unprotected, falconry |
| T-25 | [#27](https://github.com/joshualaney23/Idaho-Hunt-Planner/issues/27) | Moose, bighorn sheep & mountain goat brochure |
| T-26 | [#28](https://github.com/joshualaney23/Idaho-Hunt-Planner/issues/28) | Migratory game birds brochure |

**Kickoff prompt template:**
```
Repo: ~/git/Idaho-Hunt-Planner (remote github.com/joshualaney23/Idaho-Hunt-Planner).
Implement ticket T-XX (GitHub issue #N). Read docs/AGENT_CONTEXT.md first, then the ticket (`gh issue view N`),
then only the DESIGN.md sections it cites (`python3 docs/tools/section.py <§>`). Work on branch t-xx-<slug>, run `pytest -q`
until green, push, and open a PR titled "T-XX: <title>" (body includes "Closes #N") whose description lists every acceptance
criterion with its evidence (command output, spot-check values, screenshots). Don't change
behavior outside the ticket's scope; if the design is wrong, say so in the PR and update DESIGN.md.
```
