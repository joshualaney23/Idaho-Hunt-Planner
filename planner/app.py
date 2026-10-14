"""Flask app factory: serves `static/` and registers the API blueprint."""

from __future__ import annotations

from pathlib import Path

from flask import Flask, send_from_directory

from planner.api import api

REPO_ROOT = Path(__file__).resolve().parent.parent
STATIC_DIR = REPO_ROOT / "static"
DATA_DIR = REPO_ROOT / "data"
DEFAULT_DB_PATH = DATA_DIR / "planner.db"


def create_app(db_path: str | Path | None = None) -> Flask:
    """Build the app. `db_path` defaults to `data/planner.db` (schema arrives in T-04)."""
    app = Flask(__name__, static_folder=str(STATIC_DIR), static_url_path="/static")
    app.config["DB_PATH"] = Path(db_path) if db_path is not None else DEFAULT_DB_PATH
    app.register_blueprint(api)

    @app.get("/")
    def index():
        return send_from_directory(STATIC_DIR, "index.html")

    return app
