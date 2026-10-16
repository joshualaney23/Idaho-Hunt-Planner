# Agent context — Idaho Hunt Planner

Read this first on every ticket, then the ticket, then **only the DESIGN.md sections it cites** (DESIGN.md is ~140 KB — never read it whole).

This file holds what *every* ticket needs and what is expensive to look up. It is deliberately not an API reference: for signatures and shapes, open the module — faster, and never stale. **If this file disagrees with the repo, the repo wins; fix the file in your PR.**

## What this is
A local, single-user Flask app. It parses the official IDFG regulation PDFs into SQLite, stores the tags/permits the user holds, and shows an Idaho GMU map: green = legal to hunt in the date range, yellow = restricted/partial, grey = closed. Visual reference: `docs/mockup.html` (Treeline's State Heat Map).

## Decisions that bind every ticket (DESIGN.md §0)
- **D1** Residents only. No nonresident rules, fees, or quotas in the UI.
- **D2** Controlled hunts are MVP scope.
- **D3** **PDF parsing accuracy is the top priority.** Deterministic parsers only, never publish LLM-extracted data, never guess (an unexplained cell is staged `fail`), and publishing stays blocked while any §8.7 check fails.
- **D4/D7** Every species huntable under 4 brochures: Big Game, Upland/Turkey/Furbearer, Moose/Sheep/Goat, Migratory. Trapping-only species are out.
- **D5** The default date range is the coming weekend, **Fri–Sun**.
- **D6** No land-ownership/access layers, ever. The map shows GMUs only.
- Always show the "verify against official regulations" disclaimer, and link every season to its source PDF page.

## Stack & conventions
- Python 3.12 (the only version required and tested), Flask 3, stdlib `sqlite3` (plain SQL, no ORM), pdfplumber. shapely/pyproj only in build scripts (`requirements-dev.txt`).
- Frontend: vanilla ES modules + SVG. No framework, no build step. Every JS module has a `*.test.mjs` twin run by `node --test`.
- Colors come only from the CSS vars in `static/css/theme.css` — **the one file allowed color literals** (a test enforces it).
- Type hints, `ruff`, `pytest -q` from the repo root. The server binds `127.0.0.1:5050` (`PLANNER_PORT` overrides).
- Commit `static/data/*.json`, `tests/fixtures/*`, `tests/golden/*`. **Never commit `data/`** (DB, PDFs, page cache); `.gitignore` anchors `/data/` to the repo root so `static/data/` stays committed.
- Find PDF sections by their content (e.g. `"Hunt No."`), never by hard-coded page numbers. Printed page ≠ PDF index.
- Schema changes go in `planner/migrations/NNN_*.sql`, not by editing `schema.sql` alone (a test asserts `schema.sql` == DESIGN §5).
- `planner/dates.py` and `static/js/dates.js` are twins sharing `tests/fixtures/dates_cases.json`: change the fixture, not one twin.

## Where DESIGN.md answers what
`python3 docs/tools/section.py <§ or T-XX>` prints one section; `--toc` lists all.

| § | Topic |
|---|---|
| §0 | The binding decisions above |
| §3.3 | Target repo layout |
| §4.1 · §4.3 · §4.4 | Design tokens · map behavior · **status semantics (the core rule)** |
| §5 · §5.1 · §5.3 | SQLite schema · restriction codes (closed vocabulary) · species coverage matrix |
| §6 | API surface |
| §7 | Availability engine (§7.3 default range, §7.4 license-year crossover) |
| §8 | Import pipeline (§8.1 classify, §8.2 extract, §8.3 common parsing, §8.5 supersession, §8.6 fallbacks) |
| §8.7 | **Accuracy strategy — read it for any importer/parser/publish ticket** |
| §10 · §12 · §14 | Phasing · ticket texts · build order, issue map, agent rules |

## Repo map
One line per path: what it owns, and the ticket that brought it. Open the file for its API.

| Path | What it owns |
|---|---|
| `planner/app.py`, `__main__.py`, `__init__.py` | `create_app(db_path=None)`, `DATA_DIR` beside the DB (so tests never write the repo's `data/`), `python -m planner`, `__version__` (T-01) |
| `planner/api.py` | Every JSON endpoint plus `ApiError` → `{error:{code,message,detail}}`: meta/units/availability (T-06), tags/permits/profile (T-09), unit seasons (T-08), documents + PDF serving (T-10), the §8.4 review gate — per-row approve/reject **and field edits**, guarded `approve-all`, check waivers, `publish` (409 `publish_blocked`) (T-22b, T-15c), year-over-year diff (T-15a), the Season Table — `/api/seasons` CRUD under a `manual` document plus `export.csv`/`import.csv` (T-16), backup/restore/reset (T-21). Spec is §6 |
| `planner/engine.py` | Availability evaluation, pure over dicts (§7.2/§7.4), plus `RESTRICTIONS` — the §5.1 closed vocabulary (T-06) |
| `planner/seasons.py` | Season CSV round-trip: the §12 T-16 columns, RFC 4180 export in a deterministic order, and a parser whose every error carries its line number. Pure (T-16) |
| `planner/diff.py` | Year-over-year season diff (§8.4): identity match on species/tag/zone/hunt no./weapon/sex + unit **set**, rows tagged `new`/`changed`/`same` plus `removed`. Pure, read-only (T-15a) |
| `planner/dates.py` | Date-range helpers and presets; twin of `static/js/dates.js` (§7.3) (T-05) |
| `planner/backup.py` | DB snapshot/validate/restore/reset via `sqlite3.Connection.backup`, keeping `planner.db.bak-<timestamp>` (T-21) |
| `planner/golden.py` · `scripts/make_golden.py` · `scripts/make_verification_checklist.py` · `scripts/make_verification_sheet.py` | §8.7 golden harness: the canonical row form + diffs · dump a section / `--check` it · render every cited page and write the `VERIFICATION.md` log a human signs · the per-page `review.html` (every parsed row under its page image) the human reads while signing (T-22c, T-22d) |
| `planner/db.py`, `schema.sql`, `migrations/`, `seed.py` | §5 schema, migrations, seed from `static/data/units_seed.json` (T-04); `002_publish_gate.sql` adds `seasons.section`/`reviewed_at` + `import_checks.waived_by`/`waived_at` (T-22b) |
| `planner/importer/__init__.py` | §8 pipeline: `@parser` registry, `ParsedSeason`/`UnitRef`/`ParseContext`, `run_import()`, `validate`, `PARSER_VERSION`. Rows land `staged`; a rejected row becomes a failing `import_checks` row (T-10) |
| `planner/importer/extract.py` | §8.2 page extraction → `Page` (text, ruled + cell tables, small chars, footer, rotation) and the `data/cache/<sha>/` page cache — bump `CACHE_VERSION` when you add a field (T-10, T-17a). The side crop is adaptive (`tab_margins`): `tables`/`words`/`text` keep the old fixed 40 pt view, the tab-only view is opt-in via `full_tables`/`full_words`/`full_text` (T-13) |
| `planner/importer/common.py`, `classify.py` | §8.3 shared parsing (date ranges, unit lists, qualifiers → §5.1 codes, titles, confidence) · §8.1 brochure classification, which raises `ClassifyError` instead of guessing (T-10) |
| `planner/importer/textlayer.py` | Text-layer geometry shared by parsers (lines, bands, columns) — the §8.7 "path B" primitives (T-11) |
| `planner/importer/deer.py` | General deer tables, both paths (ruled tables vs. word positions) (T-11) |
| `planner/importer/bear_lion_wolf.py` | General black bear, mountain lion and wolf season tables (one shared shape), the license-year clipping of recurring/spring windows, and the printed method codes of §5.1 (T-13) |
| `planner/importer/controlled.py` | Controlled-hunt tables for every species, data-driven per `Brochure` so T-23/25/26 reuse it (T-17a) |
| `planner/importer/hunt_areas.py` | Two-column `CONTROLLED HUNT AREA DESCRIPTIONS` pages → `hunt_areas.description`, and every controlled season's `season_units` resolved from `static/data/hunt_area_coverage/<year>.json` (full ≥ 0.98, else partial + `coverage_frac`); no polygon ⇒ the description's own unit tokens (T-17b) |
| `planner/importer/checks/` | §8.7 check registry: `@check(section_glob)`; a parser's `finalize` returns one `SectionFacts` and the registry runs every matching invariant — **every** section finalizes before any check runs, so a check sees the rows another section rewrote. `hunt_areas.py` holds the four `hunt_areas_*` invariants (T-17b). **A facts field left `None` skips that check — opt in by measuring it, never by re-implementing a check inside a parser** (T-22a) |
| `static/index.html` · `css/theme.css` · `css/app.css` | App shell (disclaimers, tablist/tabpanels, map containers) · §4.1 tokens + `@media print` light theme · all layout, var-only (T-03, T-07, T-08) |
| `static/js/` | One module per area, each with a `.test.mjs` twin: `main.js` (boot, routing, map tab), `router`, `ui`, `api` (JSON + `postFile` multipart upload), `state`, `map`, `filters`, `results`, `drawer`, `tags`, `dates`, `regs` (upload card, documents table, `#/regs/<docId>`; T-15b), `review` (the §8.4 review screen the detail route mounts, plus the shared row editor; T-15c), `seasons` (Season Table, manual editor, CSV export/import; T-16), `settings` (profile form shared with My Tags, backup/restore, typed-confirmation reset; T-21). Shared form bits (`field`, `select`, `button`, `fieldError`) live in `ui.js` |
| `static/data/*.json` | Committed build outputs — `gmu.json` (map geometry, 100 units), `units_seed.json`, `idfg_units.json`, `hunt_area_coverage/<year>.json` (hunt area × GMU overlap fractions, T-17b) — regenerated by `scripts/build_gmu_geometry.py`, `build_idfg_unit_links.py` and `build_hunt_area_coverage.py` (all `--check` = exit 1 when stale; `render_gmu_svg.py` draws the map) |
| `scripts/build_hunt_area_coverage.py` | §9/T-17b GIS overlay: MapServer/4 hunt-area polygons × GMUs → the fraction of each GMU inside each controlled-hunt area, per species and `geometry_year` (2026 runs on 2025 polygons) |
| `tests/` | `conftest.py` (temp-DB `app`/`client`), one `test_*.py` per area, `test_review_e2e.py` (upload the deer fixture → gate → waive → publish → availability), `test_api_backup.py` (backup → reset → restore keeps availability identical), `test_startup.py` (boots `python -m planner`), `test_static_shell.py` (tokens, no color literals, runs the node tests), `js/fakedom.mjs` (the DOM stand-in the node twins render into — tags, `#id`, `.class`, `[data-x]` selectors and `<dialog>`) |
| `tests/fixtures/` | Synthetic PDFs **generated** by `make_*_fixture(s).py` (byte-stable, no writer dependency), plus real 2026 brochure pages with hand-verified `*_expected/*.json` |
| `tests/golden/<year>/` | Human-verified golden data (T-22d–f) + `VERIFICATION.md`; `deer_general.json` (235 rows, signed 2026-10-16) · `fixture_controlled_deer.json` (81 rows) proves the harness; `sources.json` lists the source PDFs CI caches. **Never mark a golden row verified yourself** |
| `docs/seasons_template.csv` | The 3-row example CSV the Season Table links to, served by `GET /api/seasons/template.csv` (T-16) |
| `docs/mockup.html` · `spike_*_parser.py` | UI mockup with real 2026 deer data (also the seed source for map demos) · proven parser spikes that parser tickets productionize |
| `docs/tools/` | `section.py` prints a §/ticket · `make_issues.py` syncs issues from §12 (`--dry-run`/`--create`/`--link`) · `issue_map.json` ticket → issue number |
| `.github/workflows/ci.yml` | `ruff check` + `ruff format --check` + `pytest -q` + node tests (TZ=America/Boise) on 3.12. The `main: require CI` ruleset requires `test (3.12)` and a branch up to date with `main` |

## Commands
```bash
cd ~/git/Idaho-Hunt-Planner
python3 docs/tools/section.py T-11          # one ticket or section; also 8.7, 5.3, --toc
jq -r '."T-17a"' docs/tools/issue_map.json  # ticket -> issue number
gh issue view <N>                            # ticket text + acceptance criteria

# Environment: this Mac's system python3 is 3.9; the project needs 3.12.
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt

pytest -q && ruff check . && ruff format --check .   # the gates; CI runs all three
ruff format .                                        # formatter
TZ=America/Boise node --test static/js/*.test.mjs    # JS tests (node 22+; pytest runs them too)

python -m planner --no-browser   # http://127.0.0.1:5050; first run creates + seeds data/planner.db
sqlite3 data/planner.db '.tables'
curl -s '127.0.0.1:5050/api/availability?species=deer&start=2026-10-17&end=2026-10-18&include_untagged=1' | jq .summary

# Import a regulation PDF (stores data/regs/<year>/<sha>.pdf, caches pages under data/cache/)
curl -s -F file=@data/regs/seasons-rules-big-game-2026.pdf 127.0.0.1:5050/api/documents | jq '.document, .import'
curl -s 127.0.0.1:5050/api/documents/1 | jq '[.checks[] | {check_name,status}]'   # §8.7 results

# Season Table + CSV (T-16). Export, import (rows land `staged` under a `manual` doc) and
# re-export are byte-identical; a bad row is rejected with its line number, importing nothing.
curl -s '127.0.0.1:5050/api/export.csv?year=2026' -o seasons.csv
curl -s --data-binary @seasons.csv -H 'Content-Type: text/csv' 127.0.0.1:5050/api/import.csv | jq .imported
# Re-upload of the same file -> 409 duplicate_document; -F kind=big_game -F year=2026 overrides the
# classifier. python tests/fixtures/make_regs_fixture.py rebuilds the synthetic test PDFs.

# Backup / restore / reset (T-21). Restore and reset keep data/planner.db.bak-<timestamp>.
curl -s 127.0.0.1:5050/api/backup -o planner-backup.db
curl -s -F file=@planner-backup.db 127.0.0.1:5050/api/restore | jq .
curl -s -X POST -H 'Content-Type: application/json' -d '{"confirm":"RESET"}' 127.0.0.1:5050/api/reset | jq .

python scripts/build_gmu_geometry.py --check   # committed geometry stale? (same flag on build_idfg_unit_links.py)
python scripts/build_hunt_area_coverage.py --year 2026 --check   # hunt-area × GMU coverage (~4 min to rebuild, network)

# Controlled-hunt area coverage (T-17b). ~4 min of GIS downloads; --input/--save-raw replay it offline.
python scripts/build_hunt_area_coverage.py --year 2026            # writes static/data/hunt_area_coverage/2026.json
python scripts/build_hunt_area_coverage.py --year 2026 --check    # stale? (--species all for T-23/25/26)

# Golden data (T-22c). Dump a section, diff it, prepare it for human verification.
python scripts/make_golden.py controlled_deer --pdf tests/fixtures/bg2026_controlled.pdf \
    --as fixture_controlled_deer            # add --check to diff instead of write (exit 1)
python scripts/make_verification_checklist.py controlled_deer --pdf <pdf>  # pages + VERIFICATION.md
python scripts/make_verification_sheet.py deer_general   # data/verify/2026/deer_general/review.html
# `golden_match` is `skipped` until that year/section has a committed golden file. The real
# brochure is read from data/pdf_cache/<sha256(url)[:16]>.pdf (git-ignored, `--url` fetches it
# once); tests/test_golden.py re-parses it and skips loudly when it is missing.
```

## Facts that cost time to rediscover
- **Source PDFs** live in `data/regs/` (git-ignored), base URL `https://idfg.idaho.gov/sites/default/files/`:
  `seasons-rules-big-game-2026.pdf` · `seasons-rules-upland-game-2026-2027.pdf` · `seasons-rules-moose-sheep-goat-2025-26.pdf` · `migratorygame2026-2027_web.pdf`
- **GIS:** `https://gisportal-idfg.idaho.gov/hosting/rest/services/Hunting/MapServer` (layer 3 = GMUs, 4 = controlled hunt areas).
- **2026 Big Game brochure:** 125 pages; pages 38–50 and 85 are rotated (elk calendars); 62 pages have tables, 24 contain `Hunt No.`; general-deer tables on pp. 12–21 → 132 unit rows / 235 date ranges; controlled tables → 469 hunts. Cold extraction ~35 s, warm (page cache) ~0.02 s, so re-importing while iterating is cheap.
- **Issue numbers ≠ ticket numbers.** They match through T-16, shift by one from T-17a (T-17a = #17, T-18 = #20), and later additions take the next free number (T-27 = #36, T-22a–f = #45–#50, T-15a–c = #57–#59). The originals T-22 (#24) and T-15 (#15) were split (into T-22a–f and T-15a/b/c) and closed as superseded. Read `issue_map.json`; never guess.
- **IDFG Hunt Planner URLs use internal ids**, not unit numbers (`unit.aspx?ID=5` is unit 4A) — mapping in `static/data/idfg_units.json`.
- The map shows no color without **published** seasons: seed a temp DB (as `tests/test_api_availability.py` does, or from `docs/mockup.html`'s `DATA.seasons`) before any visual check.

## Workflow per ticket
1. `git checkout main && git pull --ff-only`, then `git checkout -b t-xx-<slug>`. Never push to `main` — it is ruleset-protected, and every change lands through a PR with green CI.
2. Read the issue, then only the DESIGN.md sections it cites (plus §8.7 for any importer/accuracy ticket).
3. Stay in the ticket's scope. If the design is wrong, say so in the PR and fix `DESIGN.md` in the same PR.
4. Gates green: `pytest -q`, `ruff check .`, `ruff format --check .`.
5. Stage explicit paths (`git add <files>`, not `-A`), push, then `gh pr create` titled `T-XX: <title>` with `Closes #N`, listing **every acceptance criterion with real evidence** (command output, spot-check values, screenshots in `docs/images/`, linked by commit SHA so they survive branch deletion).
6. **Update this file in the same PR** when you change commands, layout, environment, or add a path — one line, no API detail. Keep the whole file under ~10 KB: every future ticket re-reads it. Detail belongs in the module, the PR description, or DESIGN.md.
