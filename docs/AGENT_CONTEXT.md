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
| App code (`planner/`, `static/`, `tests/`) | **Not started.** T-01 creates the scaffold. |

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
pytest -q && ruff check .                    # must pass before a PR

# Source PDFs (go in data/regs/, git-ignored; base URL https://idfg.idaho.gov/sites/default/files/)
#   seasons-rules-big-game-2026.pdf   seasons-rules-upland-game-2026-2027.pdf
#   seasons-rules-moose-sheep-goat-2025-26.pdf   migratorygame2026-2027_web.pdf
# GIS: https://gisportal-idfg.idaho.gov/hosting/rest/services/Hunting/MapServer (3=GMUs, 4=controlled hunt areas)
```

## Workflow per ticket
1. `git checkout main && git pull`, then `git checkout -b t-xx-<slug>`.
2. Read the issue, then only the DESIGN.md sections it cites (§8.7 too for any importer/accuracy ticket).
3. Stay in the ticket's scope. If the design is wrong, say so in the PR and fix `DESIGN.md` in the same PR.
4. Get `pytest -q` and `ruff check` green, and confirm `python -m planner --no-browser` starts.
5. Push and run `gh pr create` titled `T-XX: <title>`, with `Closes #N` in the body. The PR description lists every acceptance criterion with its evidence (real command output and spot-check values).
6. **Update this file in the same PR** if you changed commands, layout, environment, or the "What exists" table. Keep it short.
