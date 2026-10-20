"""Windows launcher for the packaged Idaho Hunt Planner.

PyInstaller freezes this module into `IdahoHuntPlanner.exe` (see `planner.spec`), and
the Inno Setup script wraps that build into one `IdahoHuntPlanner-Setup-<version>.exe`
installer. The user never installs Python: the interpreter, Flask and pdfplumber all
ride inside the exe.

It is the Windows twin of `scripts/setup.sh` (T-30) minus the parts a frozen build has
already done — no Python check, no `.venv`, no `pip install`. What is left is the same
flow:

    reuse a server that is already answering  ->  start the app on a writable data
    directory  ->  wait for /api/health  ->  fetch the IDFG brochures on a first run
    ->  open the browser

Unlike `python -m planner`, the database never lives beside the program files: a
packaged app is installed read-only, so the DB, the uploaded PDFs and the page cache go
to `%LOCALAPPDATA%\\IdahoHuntPlanner` (override with `IHP_DIR`). Uninstalling leaves
that directory alone.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path
from typing import Any

# Frozen, the repo root is the bundle root and `planner` is already importable. Run
# straight from a checkout (`python packaging/windows/ihp_win.py`) and it is not, so
# put the repo root on the path before importing the app.
if not getattr(sys, "frozen", False):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from planner import __version__  # noqa: E402
from planner.app import create_app  # noqa: E402

HOST = "127.0.0.1"
DEFAULT_PORT = 5050
APP_DIR_NAME = "IdahoHuntPlanner"
#: Seconds to wait for the app's own `/api/health` after starting it.
HEALTH_TIMEOUT = 90.0
#: A first-run IDFG fetch downloads and imports several brochures (minutes, not seconds).
FETCH_TIMEOUT = 1800.0


def data_dir() -> Path:
    """Where the database, PDFs and page cache live.

    `IHP_DIR` wins (the same override the macOS `ihp` script uses), then
    `%LOCALAPPDATA%`, then `%APPDATA%`; a home directory fallback keeps the module
    importable and testable off Windows.
    """
    override = os.environ.get("IHP_DIR", "").strip()
    if override:
        return Path(override).expanduser()
    for var in ("LOCALAPPDATA", "APPDATA"):
        base = os.environ.get(var, "").strip()
        if base:
            return Path(base) / APP_DIR_NAME
    return Path.home() / ".idaho-hunt-planner"


def get_port(argv_port: int | None = None) -> int:
    """`--port`, else `PLANNER_PORT`, else 5050."""
    if argv_port:
        return argv_port
    raw = os.environ.get("PLANNER_PORT", "").strip()
    return int(raw) if raw else DEFAULT_PORT


def _get_json(url: str, timeout: float) -> Any | None:
    """GET `url` and parse the body, or return None for any failure at all."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:  # noqa: S310 (localhost)
            return json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError):
        return None


def _post_json(url: str, timeout: float) -> Any | None:
    """POST `url` with no body (the check-updates endpoint takes none) and parse it."""
    request = urllib.request.Request(url, data=b"", method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            return json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError):
        return None


def health(base_url: str, timeout: float = 3.0) -> dict | None:
    """The `/api/health` body when a planner is answering on `base_url`, else None."""
    body = _get_json(f"{base_url}/api/health", timeout)
    return body if isinstance(body, dict) and body.get("ok") else None


def wait_for_health(base_url: str, timeout: float = HEALTH_TIMEOUT) -> dict | None:
    """Poll `/api/health` once a second until it answers or `timeout` passes."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        body = health(base_url)
        if body is not None:
            return body
        time.sleep(1.0)
    return None


def document_count(base_url: str) -> int | None:
    """How many regulation documents are imported, or None if that can't be read."""
    body = _get_json(f"{base_url}/api/documents", 10.0)
    if isinstance(body, dict) and isinstance(body.get("documents"), list):
        return len(body["documents"])
    return None


def should_fetch(base_url: str, mode: str) -> bool:
    """`always`/`never` are literal; `auto` fetches only when nothing is imported yet."""
    if mode == "always":
        return True
    if mode == "never":
        return False
    return document_count(base_url) == 0


def describe_fetch(body: dict) -> list[str]:
    """Human lines for a `/api/documents/check-updates` response (twin of setup.sh)."""
    found = body.get("found") or []
    imported = body.get("imported") or []
    lines = [f"{len(imported)} new document(s) imported, {len(found)} regulation PDF(s) seen"]
    for entry in found:
        line = (
            f"  - {entry.get('filename', '?')}  "
            f"[{entry.get('kind', '?')} {entry.get('year') or ''}]  {entry.get('state')}"
        )
        result = entry.get("import") or {}
        if result:
            staged, pages = result.get("staged", "?"), result.get("pages", "?")
            line += f"  ({staged} rows staged from {pages} pages)"
        if entry.get("error") or entry.get("reason"):
            line += f"  - {entry.get('error') or entry.get('reason')}"
        lines.append(line)
    for source in body.get("sources") or []:
        if source.get("status") != "ok":
            lines.append(f"  ! {source.get('url')}: {source.get('error')}")
    if imported:
        lines.append(
            "Nothing is published yet: open the Regulations tab and review before you hunt."
        )
    return lines


def fetch_regs(base_url: str) -> list[str]:
    """Ask the running server to check IDFG. Never raises: a failure is a warning."""
    body = _post_json(f"{base_url}/api/documents/check-updates", FETCH_TIMEOUT)
    if not isinstance(body, dict):
        return [
            "Couldn't reach idfg.idaho.gov - the app is still running.",
            "Use 'Get latest regs' on the Regulations tab to retry.",
        ]
    return describe_fetch(body)


def after_start(base_url: str, fetch_mode: str, open_browser: bool) -> None:
    """Everything that has to happen *after* the server starts answering.

    Runs on a worker thread because `app.run()` owns the main one. The browser opens
    as soon as the app is healthy; the IDFG download (minutes on a first run) keeps
    reporting into the console window behind it.
    """
    if wait_for_health(base_url) is None:
        print(f"The server did not answer {base_url}/api/health in time.", file=sys.stderr)
        return
    print(f"==> Idaho Hunt Planner is running at {base_url}/")
    if open_browser:
        webbrowser.open(f"{base_url}/")
    if should_fetch(base_url, fetch_mode):
        print("==> Fetching the latest Idaho regulations from idfg.idaho.gov")
        print("    downloading and importing - this takes a few minutes on a first run")
        for line in fetch_regs(base_url):
            print(f"    {line}")
    elif fetch_mode == "never":
        print("    Skipping the IDFG check (--no-fetch-regs)")
    else:
        print("    Regulations already imported - use 'Get latest regs' to check IDFG")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="IdahoHuntPlanner.exe", description="Run the Idaho Hunt Planner on Windows."
    )
    parser.add_argument("--no-browser", action="store_true", help="don't open a browser")
    parser.add_argument("--port", type=int, default=None, help="port to serve on (default 5050)")
    fetch = parser.add_mutually_exclusive_group()
    fetch.add_argument(
        "--fetch-regs",
        dest="fetch",
        action="store_const",
        const="always",
        help="always check IDFG for new regulation PDFs, not just on a first run",
    )
    fetch.add_argument(
        "--no-fetch-regs",
        dest="fetch",
        action="store_const",
        const="never",
        help="never check IDFG; start with whatever is already imported",
    )
    parser.set_defaults(fetch="auto")
    parser.add_argument("--version", action="version", version=f"Idaho Hunt Planner {__version__}")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    port = get_port(args.port)
    base_url = f"http://{HOST}:{port}"

    # A second double-click of the Start Menu shortcut must not start a second server.
    if health(base_url) is not None:
        print(f"Idaho Hunt Planner is already running at {base_url}/ - opening it.")
        if not args.no_browser:
            webbrowser.open(f"{base_url}/")
        return 0

    directory = data_dir()
    directory.mkdir(parents=True, exist_ok=True)
    db_path = directory / "planner.db"
    print(f"Idaho Hunt Planner {__version__}")
    print(f"    data directory: {directory}")

    app = create_app(db_path=db_path)
    threading.Thread(
        target=after_start,
        args=(base_url, args.fetch, not args.no_browser),
        daemon=True,
    ).start()
    print(f"    serving {base_url}/  (close this window to stop)")
    app.run(host=HOST, port=port, debug=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
