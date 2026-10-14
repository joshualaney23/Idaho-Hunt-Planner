# Idaho Hunt Planner

A local, single-user web app for planning Idaho hunts. It parses the official IDFG regulation PDFs into SQLite, stores the tags and permits you hold, and shows an Idaho GMU map of where you can legally hunt in a date range.

> **Disclaimer: verify against the official regulations.** This is a planning aid, not legal advice. Parsed data can be wrong or out of date. Before every hunt, confirm seasons, units, weapons, and rules in the official [Idaho Fish and Game regulations](https://idfg.idaho.gov/rules) and any emergency closures. You are responsible for hunting legally.

## Setup

Requires Python 3.12. On macOS the system `python3` may be older, so install one with `brew install python@3.12` if needed.

```bash
git clone https://github.com/joshualaney23/Idaho-Hunt-Planner.git
cd Idaho-Hunt-Planner
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt            # add -r requirements-dev.txt for tests/linting
```

## Run

```bash
python -m planner                # serves http://127.0.0.1:5050 and opens your browser
python -m planner --no-browser   # server only
PLANNER_PORT=6060 python -m planner   # different port
```

Health check: `curl http://127.0.0.1:5050/api/health` returns `{"ok": true, "version": "..."}`.

Local data (the SQLite DB and the regulation PDFs) lives in `data/`. It's created on first run and never committed.

## Development

```bash
pip install -r requirements.txt -r requirements-dev.txt
pytest -q
ruff check .
```

Design and tickets: [`docs/DESIGN.md`](docs/DESIGN.md). Agent/contributor quick-start: [`docs/AGENT_CONTEXT.md`](docs/AGENT_CONTEXT.md).
