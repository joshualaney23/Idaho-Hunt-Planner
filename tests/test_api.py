from __future__ import annotations

from planner import __version__
from planner.__main__ import DEFAULT_PORT, get_port


def test_health(client):
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.get_json() == {"ok": True, "version": __version__}


def test_index_serves_placeholder(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert b"Idaho Hunt Planner" in resp.data


def test_create_app_db_path(app, tmp_path):
    assert app.config["DB_PATH"] == tmp_path / "test.db"


def test_port_default_and_override(monkeypatch):
    monkeypatch.delenv("PLANNER_PORT", raising=False)
    assert get_port() == DEFAULT_PORT == 5050
    monkeypatch.setenv("PLANNER_PORT", "6060")
    assert get_port() == 6060
