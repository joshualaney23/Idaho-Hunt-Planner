# Idaho Hunt Planner

A local, single-user web app for planning Idaho hunts. It parses the official IDFG regulation PDFs into SQLite, stores the tags and permits you hold, and shows an Idaho GMU map of where you can legally hunt in a date range.

> **Disclaimer: verify against the official regulations.** This is a planning aid, not legal advice. Parsed data can be wrong or out of date. Before every hunt, confirm seasons, units, weapons, and rules in the official [Idaho Fish and Game regulations](https://idfg.idaho.gov/rules) and any emergency closures. You are responsible for hunting legally.

## Setup

Requires Python 3.12. On macOS the system `python3` may be older, so install one with `brew install python@3.12` if needed.

```bash
git clone https://github.com/joshualaney23/Idaho-Hunt-Planner.git
cd Idaho-Hunt-Planner
./scripts/setup.sh
```

That one command does everything: it finds Python 3.12 (and tells you to `brew install python@3.12` if it's missing, without changing anything), creates `.venv`, installs `requirements.txt`, starts the server, waits for `/api/health`, **downloads the current IDFG regulation brochures and imports them**, and opens the browser. In Finder you can double-click **`Install Idaho Hunt Planner.command`** instead — same flow, in a Terminal window.

Run it again any time: the install step is a no-op when `requirements.txt` hasn't changed, an already-running server on the port is reused, and nothing under `data/` is touched.

The regulation download runs automatically only on a first install (an empty database). Afterwards use **Get latest regs** on the Regulations tab, or `./scripts/setup.sh --fetch-regs`. Downloaded seasons land *staged* — nothing is published until you review it.

```bash
./scripts/setup.sh --no-browser      # install + serve, don't open a browser
./scripts/setup.sh --fetch-regs      # always check IDFG, not just on a first run
./scripts/setup.sh --no-fetch-regs   # skip the IDFG check entirely
PLANNER_PORT=6060 ./scripts/setup.sh # any port
```

<details>
<summary>Manual setup (fallback)</summary>

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt            # add -r requirements-dev.txt for tests/linting
```

</details>

## Run

```bash
python -m planner                # serves http://127.0.0.1:5050 and opens your browser
python -m planner --no-browser   # server only
PLANNER_PORT=6060 python -m planner   # different port
```

Health check: `curl http://127.0.0.1:5050/api/health` returns `{"ok": true, "version": "..."}`.

Local data (the SQLite DB and the regulation PDFs) lives in `data/`. It's created on first run and never committed.

## The annual workflow

When the new regulations come out:

1. **📥 Regulations tab → upload** the new brochure PDF. The parser runs on upload — a full Big Game brochure takes about 35 seconds — and every season it finds lands *staged*, not live.
2. **Review.** Open the document. The accuracy panel lists the §8.7 checks, and each staged row shows what was parsed next to the page it came from. Approve the rows that match the PDF, fix the ones that don't, reject the rest. A check that failed or warned blocks publishing until you waive it with a reason.
3. **Publish.** Approved rows go live, and overlapping seasons from older documents are superseded. Only published seasons color the map.
4. **🎟️ My Tags → Copy from last year**, then edit. Controlled-hunt tags are deliberately *not* copied: IDFG reuses hunt numbers for different hunts each year, so each one has to be re-entered and re-verified.

Day to day you only touch the first tab: pick a species, a date range (the coming weekend by default) and the tags you hold, and read the map — green is open, yellow is restricted, grey is closed.

## Settings, backup and restore

The **⚙️ Settings** tab holds your profile (youth status, weekend definition, default species) and the data controls:

- **Download backup** writes a single `planner-backup-<timestamp>.db` file — a consistent SQLite snapshot of your tags, permits, profile and every parsed season. The regulation PDFs are *not* inside it; re-upload them on the Regulations tab if you move to another machine.
- **Restore** replaces the database with a backup file. It is validated first (SQLite header, integrity check, the planner's own tables, and a schema version this build understands), and a backup from an older schema is migrated forward. Nothing is touched when validation fails.
- **Reset all data** erases every tag, permit, document and season behind a typed `RESET` confirmation, re-seeds the unit/species reference data, and deletes the stored PDFs and the page cache.

Restore and reset both keep the database they replaced as `data/planner.db.bak-<timestamp>`, so neither is a one-way door. From the command line:

```bash
curl -s http://127.0.0.1:5050/api/backup -o planner-backup.db
curl -s -F file=@planner-backup.db http://127.0.0.1:5050/api/restore | jq .
curl -s -X POST -H 'Content-Type: application/json' \
     -d '{"confirm":"RESET"}' http://127.0.0.1:5050/api/reset | jq .
```

## Troubleshooting

| Symptom | What's happening | Fix |
|---|---|---|
| The map is all grey | Nothing is **published** for that license year — staged and approved rows never color the map. | Regulations tab → review → Publish. |
| A unit you expect is grey | You hold no tag that unlocks it, or the season is outside the date range. | Tick "Also show seasons I don't hold a tag for", or widen the range. |
| Publishing is refused (409 `publish_blocked`) | A §8.7 accuracy check failed or warned. That is the gate working — parsed data isn't trusted until a human signs off. | Fix the rows, or waive the check with a reason on the review screen. |
| Upload returns 409 `duplicate_document` | That exact PDF (by sha256) is already imported. | Open the existing document instead, or delete it first. |
| Upload returns 422 `unclassified` | The classifier couldn't tell which brochure it is — usually an excerpt with no cover page. | Re-upload with the overrides: `-F kind=big_game -F year=2026`. |
| "The planner server is not responding" | `python -m planner` isn't running, or it's on another port. | Restart it; check `PLANNER_PORT`. |
| Port 5050 is already in use | Something else (often a previous instance) holds it. | `PLANNER_PORT=6060 python -m planner`. |
| A restore is rejected as `incompatible_backup` | The backup was written by a newer version of the planner than this checkout. | `git pull`, reinstall requirements, retry. |
| Something went badly wrong | Every destructive action left a snapshot behind. | Restore `data/planner.db.bak-<timestamp>` from the Settings tab. |

Starting completely over: stop the server, delete `data/`, and start it again — the DB is re-created and re-seeded. Everything in `data/` is disposable except your tags and your reviewed seasons, so take a backup first.

## Data sources and licensing

Season data comes from the official Idaho Department of Fish and Game publications, downloaded from `https://idfg.idaho.gov/sites/default/files/`:

| Source | Covers |
|---|---|
| `seasons-rules-big-game-2026.pdf` | Deer, elk, pronghorn, bear, lion, wolf — general and controlled hunts |
| `seasons-rules-upland-game-2026-2027.pdf` | Upland game, turkey, furbearers |
| `seasons-rules-moose-sheep-goat-2025-26.pdf` | Moose, bighorn sheep, mountain goat |
| `migratorygame2026-2027_web.pdf` | Waterfowl and other migratory birds |

Unit and controlled-hunt-area geometry comes from the IDFG GIS portal (`https://gisportal-idfg.idaho.gov/hosting/rest/services/Hunting/MapServer`, layer 3 = GMUs, layer 4 = controlled hunt areas), simplified into the committed `static/data/gmu.json`.

Those regulations, maps and boundaries are IDFG's work, published by the State of Idaho. This project only reads them and is not affiliated with or endorsed by IDFG. The PDFs themselves are never committed — they are downloaded into the git-ignored `data/` directory on your own machine.

This repository carries **no LICENSE file**, so default copyright applies: it is a personal tool in a private repository, not a public open-source release.

## Development

```bash
pip install -r requirements.txt -r requirements-dev.txt
pytest -q
ruff check .
```

Design and tickets: [`docs/DESIGN.md`](docs/DESIGN.md). Agent/contributor quick-start: [`docs/AGENT_CONTEXT.md`](docs/AGENT_CONTEXT.md).
