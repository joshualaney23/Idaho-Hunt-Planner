# Idaho Hunt Planner — Design Document

**Status:** Draft v1 · **Author:** Find A Hunt Planner (Hermes) · **Date:** 2026-10-14
**Repo:** https://github.com/joshualaney23/Idaho-Hunt-Planner
**Companion files:** `docs/mockup.html` (working UI mockup with real 2026 deer data), `docs/spike_deer_parser.py` (parser proof of concept)

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
- Land ownership / access layers (maybe later, see §11).
- Mobile-first layout (it should work on a phone, but it's designed for desktop).
- Multi-user / hosted deployment.
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
| Gotcha 4: **Elk zone season pages are graphical calendars rotated 90°**. `extract_tables()` returns reversed text. | p. 38: `'rebmetpeS'`, `'gaT\nA'`. | Elk needs a separate parser (de-rotate the page, or fall back to text+layout parsing), with a spike ticket first. Manual entry/CSV is the fallback. |
| The **Upland brochure** is **2 years long** (2026–2027) and defines areas by **county lists with GMU exceptions**: e.g. Forest Grouse Area 1 = "Benewah, Bonner, … counties, and Adams and Idaho counties, **except** units 19A, 20A, 22, 23 and 26"; Area 2 = "Remainder of the state." | Upland PDF p. 7. | Area definitions need a **county ↔ GMU overlay** and a **partial-coverage** status (a unit can be partly in an area). |
| **Supplemental proclamations** are published mid-year (`…supplemental-proclamation-2026-2.pdf`). | IDFG big-game page. | A license year can have multiple source documents; later ones override earlier ones. |
| IDFG's online Hunt Planner (`fishandgame.idaho.gov/ifwis/huntplanner/hunt/<id>`) shows structured per-unit, per-season dates. | e.g. Unit 75 archery deer 8/30–9/30/2025. | Optional **cross-check** source to catch parser mistakes (Phase 3). |
| Idaho requires an **Archery Permit** for archery-only seasons and a **Muzzleloader Permit** for muzzleloader-only seasons. Elk **A/B zone tags** are valid only in their zone and only in that tag's seasons. Regular and White-tailed deer tags are separate. | Table titles: "General Archery Only Seasons – Archery Permit Required". | Tags and permits are both modeled. Eligibility = tag type + zone + permits + weapon. |

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
┌──────────────┴──────────── Python 3.11+ · Flask (single process) ──────────┐
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
│       ├── controlled.py       # Phase 2
│       └── supplemental.py     # Phase 2
├── scripts/
│   ├── build_gmu_geometry.py   # IDFG GIS → static/data/gmu.json
│   └── build_county_overlay.py # TIGER counties × GMUs → static/data/county_units.json
├── static/
│   ├── index.html
│   ├── css/theme.css           # tokens (§4)
│   ├── css/app.css
│   ├── js/{main,api,state,map,filters,results,drawer,tags,review,seasons,dates}.js
│   └── data/{gmu.json,county_units.json}
├── data/                       # git-ignored: planner.db, regs/
├── tests/
│   ├── fixtures/               # small single-page PDFs cut from real brochures + expected JSON
│   ├── test_dates.py  test_engine.py  test_common.py
│   ├── test_deer_parser.py  test_elk_parser.py  test_upland_parser.py
│   └── test_api.py
└── docs/
    ├── DESIGN.md  mockup.html  spike_deer_parser.py
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

### 4.2 Layout (desktop ≥ 1100 px)

```
┌ disclaimer banner (bg3, gold "VERIFY" label, dismissible per session) ─────────────────────┐
│ IDAHO HUNT PLANNER  v0.x · regs 2026 (+1 supplement)            Where can I hunt this weekend? │
│ [🗺️ Where Can I Hunt] [🎟️ My Tags] [📅 Season Table] [📥 Regulations] [⚙️ Settings]           │
│ Viewing 🦌 Deer · Regular Deer · All legal weapons · Oct 17 – Oct 18                         │
│ Species [Deer][Elk][Pronghorn][Bear][Lion][Wolf][Forest Grouse][Pheasant]…                 │
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
- **🎟️ My Tags**: cards per license year. Add a tag by picking a tag type (Regular Deer, White-tailed Deer, Elk A, Elk B, Controlled hunt #, Pronghorn, Black Bear, Lion, Wolf, Upland/Hunting license…), plus zone (elk) or hunt number (controlled). There's a permits panel (Archery, Muzzleloader), Settings for youth/senior/DAV/resident status, and a "Copy last year's tags" button.
- **📅 Season Table**: a filterable and sortable table of **all** published seasons (species, tag, unit/zone, weapon, sex, dates, restrictions, source page). Clicking a row highlights its units on the map. CSV export.
- **📥 Regulations**: an upload drop zone, a list of source documents by year (status: `parsing`, `needs review`, `published`, `superseded`), and the **Review screen** (§8.4).
- **⚙️ Settings**: default species, weekend definition (Sat–Sun or Fri–Sun), resident vs. nonresident, youth/senior flags, DB backup/restore, and "Reset all data".

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
  id    TEXT PRIMARY KEY,          -- 'deer','elk','pronghorn','black_bear','mountain_lion','wolf',
                                   -- 'forest_grouse','pheasant','quail','gray_partridge','chukar','sage_grouse','sharptail','turkey'
  label TEXT NOT NULL, icon TEXT, grp TEXT NOT NULL   -- grp: 'big_game' | 'upland' | 'turkey'
);

CREATE TABLE tag_types (
  id               TEXT PRIMARY KEY, -- 'deer_regular','deer_whitetail','elk_zone_a','elk_zone_b','controlled',
                                     -- 'pronghorn_general','black_bear','mountain_lion','wolf','upland_license','turkey_general'
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
  kind          TEXT NOT NULL,          -- 'big_game','upland','supplemental','turkey','moose_sheep_goat','manual'
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
  tag_type_id    TEXT NOT NULL REFERENCES tag_types(id),
  zone           TEXT,               -- elk zone for A/B seasons
  hunt_number    TEXT,               -- controlled hunts
  hunt_class     TEXT NOT NULL CHECK (hunt_class IN ('general','controlled','extra','youth','other')),
  weapon         TEXT NOT NULL CHECK (weapon IN ('any','archery','muzzleloader','short_range','shotgun','falconry')),
  sex            TEXT NOT NULL CHECK (sex IN ('antlered','antlerless','either','any','male','na')),
  start_date     TEXT NOT NULL, end_date TEXT NOT NULL,
  restrictions   TEXT NOT NULL DEFAULT '[]', -- JSON array of codes (§5.1)
  bag_limit      TEXT, notes TEXT,
  source_page    INTEGER, source_text TEXT,  -- raw row/cell text for review
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
  PRIMARY KEY (season_id, unit_id)
);

-- ───────── user ─────────
CREATE TABLE profile (               -- single row, id = 1
  id INTEGER PRIMARY KEY CHECK (id = 1),
  resident INTEGER NOT NULL DEFAULT 1, youth INTEGER NOT NULL DEFAULT 0,
  weekend_def TEXT NOT NULL DEFAULT 'sat_sun', default_species TEXT DEFAULT 'deer'
);

CREATE TABLE my_permits (
  license_year INTEGER NOT NULL, permit TEXT NOT NULL CHECK (permit IN ('archery','muzzleloader','hunting_license','wmb')),
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
`whitetail_only`, `mule_deer_only`, `antlered_only`, `antlerless_only`, `two_point_only`, `spike_only`, `brow_tined`, `youth_only`, `private_land_only`, `portion_of_unit`, `cwd_testing`, `motorized_rule`, `second_tag_prohibited`, `see_notes`.
Each code has a `severity`: **narrowing** codes (`*_only`, `portion_of_unit`, `youth_only` for adults) make a unit *restricted*. **Informational** codes (`cwd_testing`, `motorized_rule`, `second_tag_prohibited`) show as badges but don't change color. The mapping lives in `planner/engine.py::RESTRICTIONS` and is mirrored to the frontend via `/api/meta`.

### 5.2 Upland areas
Upland seasons attach to **areas**, which are resolved into `season_units` rows with `full`/`partial` coverage at import time using `static/data/county_units.json` (precomputed percentage of each GMU inside each county). Rules:
- "Counties A, B, C" → units whose area is ≥ 98 % inside the union → `full`, 2–98 % → `partial`.
- "… except units X, Y" → remove X, Y.
- "Remainder of the state" → all units not `full` in the sibling areas. Units that were `partial` there are `partial` here too.

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
| `POST /api/documents/<id>/approve-all?min_confidence=0.9` | Bulk approve. |
| `POST /api/documents/<id>/publish` | Approved → published. Marks overlapping rows from lower-priority docs as superseded (§8.5). |
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

### 7.3 Default date range (`planner/dates.py` and `static/js/dates.js`, which must agree)
- `sat_sun` (default): if today is Sat, return [today, tomorrow]. If today is Sun, return [today, today]. Otherwise return the next Saturday and Sunday.
- `fri_sun`: same idea with Friday as the start. If today is Fri, Sat or Sun, return [today, Sunday].
- Uses the **browser's local date** on the frontend. The server's `/api/meta` value is only a fallback.
- Presets: *This weekend*, *Next weekend* (+7 days), *Today*, *Next 7 days*, *Opening day* (the earliest start among my tags' seasons on or after today).

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
- Detect rotated layouts. If more than 30 % of a page's words appear reversed (they don't match a dictionary or a month/unit regex but their reverses do), set `page.rotated = True` and route the page to the rotated-layout handler (T-12).
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
- Inline edit of any field, approve/reject per row, "approve all ≥ 0.9", keyboard nav (j/k/a/r).
- A "Publish" button is disabled until no rows remain `staged`.

### 8.5 Supersession
When publishing doc D (license year Y, priority P): for every published season from another doc of the same year with lower priority, the same (species, tag_type, zone/hunt, weapon, sex) and an overlapping unit set, mark it `superseded` and set `supersedes_id`. Uploading a new year never touches prior years. Prior years stay queryable for "last year vs this year".

### 8.6 Fallbacks (the app must stay useful when a parser fails)
1. **Manual season editor** (Season Table → "+ Add season").
2. **CSV import** using the same columns as the export. A template is provided in `docs/seasons_template.csv`.
3. Optional (off by default): an **LLM-assisted extractor** that sends one page's text to a user-configured model and returns rows in the CSV schema. These rows go to staging with `confidence ≤ 0.6` so they are always human-reviewed. Out of scope for v1 tickets; listed as a stretch goal.

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
| **1 – MVP: Deer** | Upload the 2026 big-game PDF, review, publish, add a Regular Deer tag, and see this weekend's green units | T-06 … T-11, T-15, T-16 |
| **2 – Elk & big game** | Elk A/B zones, pronghorn, bear, lion, wolf, supplements | T-12, T-13, T-18 |
| **3 – Upland** | Forest grouse and other birds via county areas | T-14 |
| **4 – Controlled hunts & polish** | Controlled hunt tags, cross-check, zoom, print | T-17, T-19, T-20, T-21 |

Dependency graph: `T-01 → {T-02, T-04} ; T-02 → T-03 → T-07 ; T-04 → {T-05, T-06, T-09, T-10} ; T-05 → T-06 → T-07 → T-08 ; T-10 → {T-11, T-12, T-13, T-14, T-17, T-18} ; T-11 → T-15 ; T-15 → T-16 ; T-19 spans all`.

---

## 11. Risks & open questions

| Risk | Mitigation |
|---|---|
| IDFG changes the PDF layout | Parsers are per-section and fixture-tested. The review screen catches drift. Manual/CSV fallback. Parser version is stored per document. |
| Elk calendar pages are hard to parse | Spike first (T-12a). Worst case is manual entry: about 28 zones × 2 tags × a few seasons is roughly 150 rows, entered once a year. |
| "Portion of unit" nuances | Shown as *restricted* with the verbatim note and page link. Never shown as plain green. |
| Wrong data means an illegal hunt | Permanent disclaimer, source page on every season, a review gate before publish, and no auto-publish. |
| Upland county overlay is approximate | Partial coverage → restricted plus a note. |
| **Open questions for Josh** | (1) Resident? (affects nonresident quota notes only). (2) Want turkey, moose/sheep/goat, waterfowl later? (3) Friday in the default weekend? (4) Any interest in land-ownership overlays (BLM/USFS/state) in a later phase? |

---

## 12. Tickets

Each ticket is self-contained for a coding agent. **Every agent should read §2–§9 of this document first.** Shared conventions:
- Python 3.11+, `ruff` formatting, type hints, no ORM.
- Frontend: vanilla ES modules, no build step, no framework, CSS variables from `theme.css` only (no hard-coded colors).
- All tests via `pytest -q` from the repo root. A ticket isn't done until its tests pass and `python -m planner` still starts.
- Never commit `data/` (DB, PDFs, cache). Do commit `static/data/*.json` and `tests/fixtures/*`.
- Open one PR per ticket, titled `T-XX: <title>`.

---

### T-01 · Repository scaffold & dev environment
**Depends on:** none · **Size:** S
**Description:** Create the layout in §3.3 with empty or minimal modules. Add `requirements.txt` (`flask>=3`, `pdfplumber>=0.11`), `requirements-dev.txt` (`pytest`, `ruff`, `shapely>=2`, `pyproj`, `requests`), and a `.gitignore` covering `data/`, `.venv/`, `__pycache__/`, `*.db`. Make `planner/__main__.py` start Flask on `127.0.0.1:5050` (port overridable via `PLANNER_PORT`), create `data/` if missing, and open the default browser unless `--no-browser` is passed. `app.py` exposes `create_app(db_path=None)` and serves `static/index.html` at `/`. Add `GET /api/health` → `{"ok":true,"version":…}`. README covers setup (`python -m venv .venv`, install, run) and the disclaimer. Add a GitHub Actions workflow running `ruff check` and `pytest` on 3.11 and 3.12.
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
**Description:** Implement `planner/schema.sql` exactly as §5 and `planner/db.py` with `connect(path)` (`PRAGMA foreign_keys=ON`, `journal_mode=WAL`, `row_factory=sqlite3.Row`), `migrate()` (applies `schema.sql` at version 1; future migrations live in `planner/migrations/NNN_*.sql` and are applied in order), and small helpers `q(sql, *args)`, `q1`, `exec`, `tx()` context manager. `planner/seed.py` inserts species (§5 list with icons 🦌 🫎 🐻 🐾 🐺 🐦), tag types (§5), units from `units_seed.json`, and default `zone_units` for the current year from GIS `Elk_Zone`. Seeding is idempotent (`INSERT OR IGNORE`). Also define `RESTRICTIONS` (code → {label, severity}) per §5.1 in `planner/engine.py` as a constant (engine logic comes in T-06).
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
**Description:** API per §6 (`/api/tags`, `/api/permits`, `/api/profile`, copy-year). Validation: `requires_zone` tag types must have a zone from `zone_units` for that year, and `requires_hunt_no` must have a hunt number. UI `#/tags`: a year selector (defaults to the current year), tag cards (species icon, label, zone/hunt #, filled toggle, delete), an "Add tag" dialog whose fields depend on tag type (zone dropdown for elk A/B, free-text hunt number for controlled), a permits panel, a profile panel (resident, youth, weekend definition, default species), and a "Copy tags from <year-1>" button (copies unfilled tag types but not filled status).
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
**Description:** `importer/deer.py`, generalized from `docs/spike_deer_parser.py`. Scope: all "Regular Deer Tag" and "White-tailed Deer Tag" **general** tables (Any Weapon, Archery Only, Muzzleloader Only, Short Range). Skip "Controlled" tables (T-17). Rules: crop sides; header row must be `Unit(s) | Antlered | Antlerless | Notes`; carry the title across pages; carry the unit list across continuation rows; each date range in the Antlered/Antlerless cells produces one season (`sex` = antlered/antlerless); qualifiers in the 60 chars after a date attach to that season; the Notes column parses to restriction codes plus `notes` text; "None" means no row. Map titles: Regular → `deer_regular`, White-tailed → `deer_whitetail` (implicitly add `whitetail_only` only for display, since the tag itself is WT-only). Set `source_page`, `source_text`.
**Fixtures:** cut pages 12–16 and 18–21 of the 2026 Big Game PDF into `tests/fixtures/bg2026_deer_pXX.pdf` (use `pypdf` to split) with hand-verified expected JSON for at least 4 pages.
**Acceptance criteria:**
- On the full 2026 PDF: ≥ 230 rows. Every GMU except {44, 45, 52, 54, 68A, YNP} has at least one Regular-tag general season. Fixture expectations match exactly.
- Spot checks: Unit 39 Regular any-weapon antlered **Oct 10–Oct 31**; Units 1, 2, 3, 4A, 5, 6 antlered Oct 10–31 carries `whitetail_only`; Unit 22 antlered carries `two_point_only`.

---

### T-12 · Elk A/B zone parser (with spike)
**Depends on:** T-10 · **Size:** L
**Description:**
**T-12a (spike, time-box 4 h):** elk zone pages (2026 PDF pp. ~38–50) are rotated calendar graphics. Evaluate, in order: (1) `page.rotation` / derotating chars (pdfplumber `chars` transformed by -90° and re-clustered into words); (2) `pdftotext -layout` after rotating the page with `pypdf` (`page.rotate(90)`); (3) reading the per-zone tables in the general information or "Elk Zone" summary pages if present. Write findings in `docs/elk_parser_notes.md` and pick an approach.
**T-12b:** implement `importer/elk.py`. Output per zone × tag (A/B) × weapon window: `tag_type` = `elk_zone_a|elk_zone_b`, `zone` = the zone name normalized to the GIS `Elk_Zone` values (keep a mapping table for spelling differences, e.g. "Smoky – Bennett"), `sex` (`antlered`/`either`/`antlerless`), restrictions (`spike_only`, `brow_tined`, `portion_of_unit` with note). `season_units` = units of that zone from `zone_units` (also parse zone→unit lists from the PDF's zone table, and if it differs from GIS, update `zone_units` for that year and flag a warning).
**Acceptance criteria:**
- All 28 zones present for both A and B tags where the regs offer them. A zone with only one tag type is acceptable if the PDF says so.
- Fixture tests for at least 3 zones are hand-verified against the PDF.
- If the automated approach yields confidence < 0.8 for a zone, rows are still staged (flagged) rather than dropped.

---

### T-13 · Pronghorn, black bear, mountain lion, wolf parsers
**Depends on:** T-10 · **Size:** M
**Description:** One module each (or the shared `bear_lion_wolf.py`), following the T-11 pattern. Bear has spring and fall seasons, plus `restrictions` for baiting/hound notes as informational codes (add `bait_allowed`, `hounds_allowed` as informational). Lion and wolf seasons are often "All units except …" or long date spans with "statewide" text, so support `units: ALL` and `ALL EXCEPT [...]` in `common.parse_units`. Pronghorn general archery seasons are by unit list.
**Acceptance criteria:** fixture tests per species (≥ 1 page each); after import, each species has non-empty published-able rows; spot checks documented in the PR.

---

### T-14 · Upland parser + county overlay
**Depends on:** T-02, T-10 · **Size:** L
**Description:** (a) Implement `scripts/build_county_overlay.py` per §9 and commit `static/data/county_units.json`. (b) `importer/upland.py` for the Upland brochure: species sections (FOREST GROUSE, PHEASANT, QUAIL, GRAY PARTRIDGE, CHUKAR, SAGE-GROUSE, SHARP-TAILED GROUSE, plus TURKEY general seasons if in the same PDF); per section, parse `AREA n` blocks: the definition text, then `Seasons` lines (one line per year of the 2-year brochure, e.g. `2026 – 2027: August 30, 2026 through January 31, 2027`), and bag/possession limits into `bag_limit`. Resolve area text to `season_units` via §5.2 (county lists, "except units …", "remainder of the state", and explicit unit lists). Tag type `upland_license` (an Idaho hunting license is required; pheasant on WMAs requires the upland game bird permit, so add the `wmb` permit code as an informational restriction `wmb_permit_on_wma`).
**Acceptance criteria:** the 2026–27 Forest Grouse Area 1 resolves to units in the listed counties minus 19A, 20A, 22, 23, 26; Area 2 is the remainder; both years' seasons are created; partial units are marked `partial` with a note naming the counties.

---

### T-15 · Regulations tab: upload & review & publish UI
**Depends on:** T-10, T-11 (for real data), T-03 · **Size:** L
**Description:** `#/regs` per §8.4: a drag-and-drop upload card (shows sha/dup warnings, a spinner, then the result counts), a documents table by year with status badges, and a review screen at `#/regs/<docId>` with a grouped row list, confidence coloring (`--sc1`…`--sc5`), inline editors (unit chip editor with autocomplete from units, date inputs, selects for tag/weapon/sex, restriction multi-select), an embedded PDF iframe synced to the selected row's page, a diff vs prior published year (API: `GET /api/documents/<id>/diff`, implemented here, matching on species+tag+zone+weapon+sex+unit set), approve/reject per row, approve-all ≥ threshold, keyboard shortcuts (j/k/a/r/e), and a Publish button (§8.5 supersession implemented in `api.py`/`db.py` here) with a confirmation modal summarizing counts.
**Acceptance criteria:** end-to-end test: upload the 2026 deer fixture, approve all, publish, and `/api/availability` returns open units. Publishing a supplement supersedes the matching rows. The UI prevents publishing while staged rows remain.

---

### T-16 · Season Table, manual editor, CSV import/export
**Depends on:** T-06, T-15 · **Size:** M
**Description:** `#/seasons`: a filterable (year, species, tag, weapon, unit search, status) and sortable table of seasons; clicking a row highlights its units on the map (switch to the map tab with highlight). "+ Add season" opens the same editor as review; rows are saved under an auto-created `manual` source document for the year, published immediately but badged "manual". CSV export/import with columns `license_year,species,tag_type,zone,hunt_number,hunt_class,weapon,sex,start_date,end_date,units,restrictions,notes,source_page` (units and restrictions `;`-separated). Imported CSV rows go to staging under a `manual` doc for review. Ship `docs/seasons_template.csv` with 3 example rows.
**Acceptance criteria:** CSV round-trip is lossless (export → import → export is identical); invalid rows report line numbers.

---

### T-17 · Controlled hunts (Phase 2)
**Depends on:** T-10, T-09 · **Size:** L
**Description:** Parse the controlled-hunt tables (deer, elk, pronghorn, bear) in the Big Game PDF: hunt number, area description/units, dates, weapon, sex, tags, notes. Units: parse "All of Unit X" / "Units X, Y" / "That portion of Unit X …" (→ `partial` + note). Optionally enrich boundaries from the GIS **Controlled Hunts** layer (MapServer/4) by hunt number: if the layer has a polygon, compute GMU coverage fractions like §5.2. `tag_type='controlled'`, `hunt_number` set, `species_id` from the table. In My Tags, adding a controlled tag validates the hunt number against published or staged seasons for the year and shows its description.
**Acceptance criteria:** with a controlled tag for one known 2026 hunt number, only that hunt's units light up for its dates; hunt numbers reused across years don't leak (filtered by license_year).

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

1. **Spring (Big Game regs drop):** open the app → 📥 Regulations → drop `seasons-rules-big-game-2027.pdf` → review the about 5 % of rows flagged yellow or red, using the side-by-side PDF and the "changed since 2026" diff → **Publish**.
2. **Summer:** 🎟️ My Tags → "Copy from 2026" → adjust (bought an elk B tag in the Sawtooth zone, archery permit ✓).
3. **Any Thursday in October:** open the app. It's already on this weekend. Pick 🦌 Deer and the map shows where to go. Click a unit for the brief and the source page.
4. **Mid-season supplement published:** upload it, review the few changes, publish. Superseded seasons drop off the map automatically.
