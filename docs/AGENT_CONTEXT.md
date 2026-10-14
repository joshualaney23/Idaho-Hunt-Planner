# Agent context — Idaho Hunt Planner

Read this first on every ticket. It's kept short on purpose: **read only the DESIGN.md sections your ticket cites**, not the whole doc (~120 KB).

## What this is
A local, single-user web app. It parses the official IDFG regulation PDFs into SQLite, stores the tags/permits Josh holds, and shows an Idaho GMU map: green = legal to hunt in the date range, yellow = restricted/partial, grey = closed. The UI style follows Treeline's State Heat Map (`docs/mockup.html`).

## Decisions that bind every ticket (DESIGN.md §0)
- **D1** Residents only. No nonresident rules, fees, or quotas in the UI.
- **D2** Controlled hunts are MVP scope.
- **D3** **PDF parsing accuracy is the top priority.** Use deterministic parsers only, never publish LLM-extracted data, never guess (unexplained cells are staged as `fail`), and publishing stays blocked while any check fails (§8.7).
- **D4/D7** Every species huntable under 4 brochures: Big Game, Upland/Turkey/Furbearer, Moose/Sheep/Goat, Migratory. Trapping-only species are out.
- **D5** The default date range is the coming weekend, **Fri–Sun**.
- **D6** No land ownership/access layers, ever. The map shows GMUs only.
- Always show the "verify against official regulations" disclaimer, and link every season to its source PDF page.

## Stack & conventions
- Python 3.11+, Flask 3, stdlib `sqlite3` (plain SQL, no ORM), pdfplumber. Use shapely/pyproj only in build scripts (`requirements-dev.txt`).
- Frontend: vanilla ES modules plus SVG, no framework, no build step. Colors come only from the CSS vars in `static/css/theme.css`.
- Type hints, `ruff`, and `pytest -q` from the repo root. The server binds `127.0.0.1:5050` (override with `PLANNER_PORT`).
- Commit `static/data/*.json`, `tests/fixtures/*`, `tests/golden/*`. **Never commit `data/`** (DB, PDFs).
- Find PDF sections by their content (e.g. `"Hunt No."`), never by hard-coded page numbers. Printed page ≠ PDF index.

## What exists (update this table when your ticket lands)
| Path | Status |
|---|---|
| `docs/DESIGN.md` | Full design: §3.3 is the target repo layout, §5 schema, §6 API, §7 engine, §8 importer, §8.7 accuracy, §12 tickets, §14 build order |
| `docs/mockup.html` | Working UI mockup with real 2026 deer data, the visual reference for T-03/07/08 |
| `docs/spike_*_parser.py` | Proven parser spikes (deer general, controlled Big Game, MSG). Parser tickets productionize these. |
| `docs/tools/issue_map.json` | Ticket → GitHub issue number. Numbers match through T-16, then shift by one (T-17a = #17, T-18 = #20, T-22 = #24). |
| `docs/tools/make_issues.py` | Re-syncs issue bodies from §12 (`--link`) after a ticket is edited |
| `docs/tools/section.py` | Prints one DESIGN.md section or ticket |
| `docs/images/` | PR evidence renders (`t-02-gmu-*.png`; `t-03-*.png` app-shell screenshots, plus `t-03-mockup-reference.png`) |
| `scripts/build_gmu_geometry.py` | T-02: IDFG MapServer/3 → EPSG:8826 → 300 m coverage-simplify → `static/data/gmu.json` + `units_seed.json`. `--check` (stale exit 1), `--input FILE` (offline). |
| `scripts/render_gmu_svg.py` | T-02: renders `gmu.json` to a stroke-free SVG for visual gap checks (`--crop X Y SIZE`) |
| `static/data/gmu.json` | T-02 geometry: 100 units, 139 KB, `viewBox` `0 0 430 690`. `d` is `M<abs> l<relative deltas> Z` (1 decimal, 1 svg unit ≈ 1.1 km); `YNP` is `"closed": true`. |
| `static/data/units_seed.json` | T-02: `[{id, sort_key, elk_zone, area_acres}]` in map order; T-04 seeds the `units` table from it |
| `planner/` | T-01 scaffold: `python -m planner` (`__main__.py`), `create_app(db_path=None)` (`app.py`), `GET /api/health` (`api.py`), `__version__` in `__init__.py`. `dates.py` (T-05): `default_range`, `overlap`, `days`, `parse_iso`, `to_iso`, `fmt_short`, presets (`presets()` returns all five). Other modules are docstring stubs for their tickets. |
| `static/js/dates.js` | T-05 JS twin of `planner/dates.py` (local-noon `Date`s; camelCase + snake_case aliases). `dates.test.mjs` runs `node --test`. |
| `static/index.html` | T-03 app shell: VERIFY disclaimer (`sessionStorage` dismissal) + always-on footer disclaimer, header (`#ver`), `role="tablist"` of `<a role="tab" href="#/…">` with one `role="tabpanel"` per tab, and map-panel containers `#viewing`, `#filters`, `#include-untagged`, `#map-wrap`, plus `#toasts` |
| `static/css/theme.css` · `app.css` | T-03: §4.1 tokens plus named tints and font stacks. **The only file allowed color literals.** `:focus-visible` ring is `--gold2`. `app.css` (var-only) holds the layout: `.wrap` 2-col → 1-col below 1100 px, chip rows scroll below 700 px, `.chip[aria-pressed]`, `.card`, `.badge.b-<kind>`, `.toast-<level>` |
| `static/js/ui.js` | T-03 components: `chipGroup({id, items, multi, value, onChange, disabled, label, labelledBy})` (returns the container with `getValue/setValue`; chips are `<button aria-pressed>`), `card(title, ...children)`, `badge(kind, text)`, `toast(msg, level, {ms})`, `textEl` |
| `static/js/router.js` | T-03: `TABS`, `parseRoute(hash)` → `{tab, rest, query, known}` (`#/regs/12` → rest `["12"]`; query kept for T-07), `tabHash` |
| `static/js/main.js` | T-03 boot: disclaimer, version from `/api/health` (regs label waits for `/api/meta`), hash routing (unknown → `replaceState` `#/map`; arrow/Home/End keys), placeholder cards for tags/seasons/regs/settings, and a **local-only filter-bar shell** (species/weapon/permit chips, dates + presets from `dates.js`, breadcrumb). T-07 replaces the shell with `filters.js`/`state.js`. |
| `tests/` | `conftest.py` (`app`/`client` fixtures, temp DB path), `test_api.py` smoke tests, `test_startup.py` (boots `python -m planner --no-browser` on a free port), `test_dates.py` (also runs the JS twin under 4 time zones when node is installed; `fixtures/dates_cases.json` is the shared Python/JS date contract: change it, not just one twin), `test_build_gmu_geometry.py` (T-02: decodes the committed paths with an independent SVG number parser and asserts the units tile Idaho with no gaps; `fixtures/gmu_sample.geojson` is its offline 3-unit input), `test_static_shell.py` (T-03: tokens equal DESIGN §4.1, no color literals outside `theme.css`, every `var(--x)` is defined, tablist/tabpanel wiring, runs `ui`/`router` node tests). `js/fakedom.mjs` is a tiny DOM shim for `static/js/ui.test.mjs`; extend it when `ui.js` needs more DOM. |
| `.github/workflows/ci.yml` | `ruff check` + `ruff format --check` + `pytest -q` + `node --test static/js/*.test.mjs` (TZ=America/Boise) on 3.11 and 3.12 for pushes to main and PRs |
| `pyproject.toml` | ruff + pytest config (`docs/` excluded from ruff) |

## Commands
```bash
cd ~/git/Idaho-Hunt-Planner
python3 docs/tools/section.py T-11          # one ticket; also 8.7, 5.3, --toc
gh issue view <N>                            # ticket + acceptance criteria + definition of done
jq -r '."T-17a"' docs/tools/issue_map.json   # ticket -> issue number

# Environment: this Mac's system python3 is 3.9. Use 3.11+ (e.g. `brew install python@3.12`).
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
python -m planner --no-browser               # run (http://127.0.0.1:5050, GET /api/health)
pytest -q && ruff check . && ruff format --check .   # must pass before a PR (CI runs all three;
                                             #   tests/test_startup.py boots `python -m planner`)

ruff format .                                # formatter (config in pyproject.toml)
TZ=America/Boise node --test static/js/*.test.mjs   # JS tests (node 22+; pytest also runs them)
PLANNER_PORT=6060 python -m planner          # alternate port; omit --no-browser to open browser

# UI checks (T-03 pattern; tools live in a scratch dir, not the repo)
#   npm i lighthouse puppeteer-core   (in scratch), CHROME_PATH=/Applications/Google Chrome.app/…
#   npx lighthouse http://127.0.0.1:5050/#/map --only-categories=accessibility --preset=desktop
#   Chrome --headless=new --screenshot=out.png --window-size=1400,900 <url>   # quick render

# Map geometry (T-02; regenerate only when IDFG updates the units)
python scripts/build_gmu_geometry.py         # fetch + rewrite static/data/gmu.json + units_seed.json
python scripts/build_gmu_geometry.py --check # exit 1 if the committed files are stale
python scripts/render_gmu_svg.py /tmp/gmu.svg --crop 150 330 120   # visual gap check (no strokes)
qlmanage -t -s 1400 -o /tmp /tmp/gmu.svg     # SVG -> PNG on macOS, no extra tooling
# .gitignore anchors /data/ to the repo root so static/data/ stays committed.

# Source PDFs (go in data/regs/, git-ignored; base URL https://idfg.idaho.gov/sites/default/files/)
#   seasons-rules-big-game-2026.pdf   seasons-rules-upland-game-2026-2027.pdf
#   seasons-rules-moose-sheep-goat-2025-26.pdf   migratorygame2026-2027_web.pdf
# GIS: https://gisportal-idfg.idaho.gov/hosting/rest/services/Hunting/MapServer (3=GMUs, 4=controlled hunt areas)
```

## Workflow per ticket
1. `git checkout main && git pull`, then `git checkout -b t-xx-<slug>`.
2. Read the issue, then only the DESIGN.md sections it cites (§8.7 too for any importer/accuracy ticket).
3. Stay in the ticket's scope. If the design is wrong, say so in the PR and fix `DESIGN.md` in the same PR.
4. Get `pytest -q`, `ruff check` and `ruff format --check` green, and confirm `python -m planner --no-browser` starts.
5. Push and run `gh pr create` titled `T-XX: <title>`, with `Closes #N` in the body. The PR description lists every acceptance criterion with its evidence (real command output and spot-check values).
6. **Update this file in the same PR** if you changed commands, layout, environment, or the "What exists" table. Keep it short.
