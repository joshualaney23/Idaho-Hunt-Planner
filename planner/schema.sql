-- Canonical schema, DESIGN.md §5 (copied verbatim; schema version 1).
-- Applied by planner/db.py::migrate(). Later changes go in planner/migrations/NNN_*.sql.

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
