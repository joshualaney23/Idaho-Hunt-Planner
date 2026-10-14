"""`python -m planner`: run the local server and open the browser."""

from __future__ import annotations

import argparse
import os
import threading
import webbrowser

from planner.app import DATA_DIR, create_app

HOST = "127.0.0.1"
DEFAULT_PORT = 5050


def get_port() -> int:
    """Port from `PLANNER_PORT`, defaulting to 5050."""
    raw = os.environ.get("PLANNER_PORT", "").strip()
    return int(raw) if raw else DEFAULT_PORT


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m planner", description=__doc__)
    parser.add_argument(
        "--no-browser", action="store_true", help="don't open the default web browser"
    )
    args = parser.parse_args(argv)

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    port = get_port()
    url = f"http://{HOST}:{port}/"
    app = create_app()

    if not args.no_browser:
        threading.Timer(1.0, webbrowser.open, args=(url,)).start()

    print(f"Idaho Hunt Planner running at {url}  (Ctrl+C to stop)")
    app.run(host=HOST, port=port, debug=False)


if __name__ == "__main__":
    main()
