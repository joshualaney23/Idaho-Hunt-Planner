"""REST endpoints (DESIGN.md §6). Mounted under `/api`."""

from __future__ import annotations

from flask import Blueprint, jsonify

from planner import __version__

api = Blueprint("api", __name__, url_prefix="/api")


@api.get("/health")
def health():
    return jsonify({"ok": True, "version": __version__})
