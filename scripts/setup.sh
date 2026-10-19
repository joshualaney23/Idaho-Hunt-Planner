#!/usr/bin/env bash
# One-step install and launch for the Idaho Hunt Planner (T-30, DESIGN.md §12).
#
# Does everything README "Setup" used to ask for by hand: find Python 3.12, create
# .venv, install requirements.txt, start the local server, wait for /api/health, fetch
# the latest IDFG regulations on a first run, and open the browser.
#
# Safe to run again: the install step is a no-op when nothing changed, an already
# running server on the port is reused, and nothing under data/ is ever removed.
set -euo pipefail

REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
PORT="${PLANNER_PORT:-5050}"
HOST="127.0.0.1"
BASE_URL="http://${HOST}:${PORT}"
HEALTH_URL="${BASE_URL}/api/health"
# Server log: outside the repo, so a crash report survives `ihp --clean` and the
# script never writes into data/.
LOG_FILE="${IHP_SETUP_LOG:-${HOME}/Library/Logs/ihp-setup.log}"
HEALTH_TIMEOUT="${IHP_HEALTH_TIMEOUT:-90}"

OPEN_BROWSER=1
FETCH_REGS="auto" # auto = only when no regulation document has been imported yet
[ -n "${IHP_NO_OPEN:-}" ] && OPEN_BROWSER=0

usage() {
  cat <<'EOF'
Usage: scripts/setup.sh [options]

  --no-browser      install and start the server, but don't open a browser
  --fetch-regs      always check IDFG for new regulation PDFs (not just on a first run)
  --no-fetch-regs   never check IDFG; start with whatever is already imported
  -h, --help        show this help

Environment: PLANNER_PORT (default 5050), IHP_NO_OPEN, IHP_SETUP_LOG,
IHP_HEALTH_TIMEOUT (seconds to wait for /api/health, default 90).
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    --no-browser) OPEN_BROWSER=0 ;;
    --fetch-regs) FETCH_REGS="always" ;;
    --no-fetch-regs) FETCH_REGS="never" ;;
    -h | --help)
      usage
      exit 0
      ;;
    *)
      echo "setup.sh: unknown option '$1'" >&2
      usage >&2
      exit 2
      ;;
  esac
  shift
done

say() { printf '\n==> %s\n' "$*"; }
note() { printf '    %s\n' "$*"; }
die() {
  printf '\nsetup.sh: %s\n' "$*" >&2
  exit 1
}

# ───────── 1. Python 3.12 ─────────
# Checked before anything is created, so a Mac without 3.12 ends the run having
# changed nothing at all (acceptance criterion 3).
is_py312() { [ -x "$1" ] && "$1" -c 'import sys; sys.exit(0 if sys.version_info[:2] == (3, 12) else 1)' 2>/dev/null; }

find_python312() {
  local candidate
  for candidate in \
    "${IHP_PYTHON:-}" \
    "$(command -v python3.12 2>/dev/null || true)" \
    "${HOMEBREW_PREFIX:-/opt/homebrew}/bin/python3.12" \
    /usr/local/bin/python3.12 \
    "$(command -v python3 2>/dev/null || true)"; do
    if [ -n "$candidate" ] && is_py312 "$candidate"; then
      echo "$candidate"
      return 0
    fi
  done
  return 1
}

VENV="${REPO_ROOT}/.venv"
VENV_PY="${VENV}/bin/python"

say "Checking for Python 3.12"
if is_py312 "$VENV_PY"; then
  note "already have $("$VENV_PY" -V) in .venv"
  PYTHON="$VENV_PY"
elif PYTHON="$(find_python312)"; then
  note "found $("$PYTHON" -V) at ${PYTHON}"
else
  cat >&2 <<EOF

setup.sh: Python 3.12 was not found, and nothing has been changed.

Install it, then run this script again:

    brew install python@3.12

(If Homebrew isn't installed: https://brew.sh — or set IHP_PYTHON=/path/to/python3.12.)
EOF
  exit 1
fi

# ───────── 2. virtualenv ─────────
say "Preparing the virtual environment"
if [ -x "$VENV_PY" ]; then
  is_py312 "$VENV_PY" || die ".venv exists but is not Python 3.12 — remove it and re-run: rm -rf '${VENV}'"
  note ".venv already exists — keeping it"
else
  note "creating .venv"
  "$PYTHON" -m venv "$VENV"
fi
PYTHON="$VENV_PY"

# ───────── 3. requirements ─────────
# A stamp of requirements.txt's own bytes: unchanged file plus a healthy import of
# the app's dependencies means pip has nothing to do (acceptance criterion 2).
REQ_FILE="${REPO_ROOT}/requirements.txt"
STAMP="${VENV}/.requirements.sha256"
REQ_SHA="$("$PYTHON" -c 'import hashlib,sys;print(hashlib.sha256(open(sys.argv[1],"rb").read()).hexdigest())' "$REQ_FILE")"

say "Installing requirements"
if [ -f "$STAMP" ] && [ "$(cat "$STAMP")" = "$REQ_SHA" ] && "$PYTHON" -c 'import flask, pdfplumber' 2>/dev/null; then
  note "requirements.txt unchanged and already installed — nothing to do"
else
  "$PYTHON" -m pip install --quiet --upgrade pip
  "$PYTHON" -m pip install --quiet -r "$REQ_FILE"
  printf '%s\n' "$REQ_SHA" >"$STAMP"
  note "installed from requirements.txt"
fi

# ───────── 4. server ─────────
health_ok() { curl -fsS --max-time 3 "$HEALTH_URL" 2>/dev/null | grep -q '"ok"[[:space:]]*:[[:space:]]*true'; }

say "Starting the Idaho Hunt Planner"
if health_ok; then
  note "a server is already answering on ${BASE_URL} — reusing it"
else
  mkdir -p "$(dirname "$LOG_FILE")" 2>/dev/null || LOG_FILE="${TMPDIR:-/tmp}/ihp-setup.log"
  note "launching python -m planner --no-browser (log: ${LOG_FILE})"
  (cd "$REPO_ROOT" && PLANNER_PORT="$PORT" nohup "$PYTHON" -m planner --no-browser >>"$LOG_FILE" 2>&1 &)
  waited=0
  until health_ok; do
    waited=$((waited + 1))
    if [ "$waited" -ge "$HEALTH_TIMEOUT" ]; then
      die "the server did not answer ${HEALTH_URL} within ${HEALTH_TIMEOUT}s — see ${LOG_FILE}"
    fi
    sleep 1
  done
  note "up after ${waited}s"
fi
printf '    '
curl -fsS "$HEALTH_URL"
printf '\n'

# ───────── 5. latest regulations ─────────
# A fresh install has an empty database, so the app would open on a blank map. Ask
# the running server to go get the current IDFG brochures (T-28's check-updates:
# download, classify, import). Everything lands `staged`/`needs_review` — the §8.4
# review gate and D3 still require a human before anything is published.
document_count() {
  curl -fsS --max-time 10 "${BASE_URL}/api/documents" 2>/dev/null |
    "$PYTHON" -c 'import json,sys; print(len(json.load(sys.stdin)["documents"]))' 2>/dev/null || echo "?"
}

should_fetch() {
  case "$FETCH_REGS" in
    always) return 0 ;;
    never) return 1 ;;
    *) [ "$(document_count)" = "0" ] ;;
  esac
}

if should_fetch; then
  say "Fetching the latest Idaho regulations from idfg.idaho.gov"
  note "downloading and importing — this takes a few minutes on a first run"
  if curl -fsS --max-time 1800 -X POST "${BASE_URL}/api/documents/check-updates" >"${TMPDIR:-/tmp}/ihp-check-updates.json" 2>/dev/null; then
    "$PYTHON" - "${TMPDIR:-/tmp}/ihp-check-updates.json" <<'PY'
import json, sys

body = json.load(open(sys.argv[1]))
found = body.get("found", [])
imported = body.get("imported", [])
print(f"    {len(imported)} new document(s) imported, {len(found)} regulation PDF(s) seen")
for entry in found:
    line = f"      - {entry.get('filename', '?')}  [{entry.get('kind', '?')} {entry.get('year') or ''}]  {entry.get('state')}"
    result = entry.get("import") or {}
    if result:
        line += f"  ({result.get('staged', '?')} rows staged from {result.get('pages', '?')} pages)"
    if entry.get("error") or entry.get("reason"):
        line += f"  — {entry.get('error') or entry.get('reason')}"
    print(line)
errors = [s for s in body.get("sources", []) if s.get("status") != "ok"]
for src in errors:
    print(f"      ! {src.get('url')}: {src.get('error')}")
if imported:
    print("    Nothing is published yet: open the Regulations tab and review before hunting on it.")
PY
  else
    note "couldn't reach idfg.idaho.gov — the app still starts; use the"
    note "'Get latest regs' button on the Regulations tab to retry."
  fi
else
  case "$FETCH_REGS" in
    never) note "skipping the IDFG check (--no-fetch-regs)" ;;
    *) note "regulations already imported — use 'Get latest regs' (or --fetch-regs) to check IDFG" ;;
  esac
fi

# ───────── 6. browser ─────────
say "Idaho Hunt Planner is running at ${BASE_URL}/"
if [ "$OPEN_BROWSER" = "1" ]; then
  if command -v open >/dev/null 2>&1; then
    open "${BASE_URL}/"
  else
    "$PYTHON" -c 'import sys, webbrowser; webbrowser.open(sys.argv[1])' "${BASE_URL}/"
  fi
fi
note "Stop it with: pkill -f 'planner --no-browser'"
