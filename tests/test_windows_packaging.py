"""The Windows packaging: launcher behaviour plus static checks on the build recipe.

The installer itself can only be built and run on Windows (the
`Windows installer` workflow does that, and it is the PR's real evidence), so what a
macOS/Linux `pytest` run can prove is everything *around* it: the launcher's logic is
plain Python and testable here, and the spec/Inno/workflow files are checked for the
mistakes that would make the Windows build silently wrong — a bundled path that no
longer exists, a version that drifted from `planner.__version__`, a workflow that
stopped building or uploading.
"""

from __future__ import annotations

import ast
import importlib.util
import re
from pathlib import Path
from types import ModuleType

import pytest

import planner

REPO_ROOT = Path(__file__).resolve().parent.parent
PACKAGING = REPO_ROOT / "packaging" / "windows"
SPEC = PACKAGING / "planner.spec"
ISS = PACKAGING / "installer.iss"
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "windows-installer.yml"


@pytest.fixture(scope="module")
def launcher() -> ModuleType:
    """`packaging/windows/ihp_win.py` imported by path (it is not an installed package)."""
    spec = importlib.util.spec_from_file_location("ihp_win", PACKAGING / "ihp_win.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ───────── the launcher ─────────


def test_data_dir_prefers_ihp_dir_then_localappdata(launcher, monkeypatch, tmp_path):
    monkeypatch.setenv("IHP_DIR", str(tmp_path / "chosen"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    assert launcher.data_dir() == tmp_path / "chosen"

    monkeypatch.delenv("IHP_DIR")
    assert launcher.data_dir() == tmp_path / "local" / "IdahoHuntPlanner"

    monkeypatch.delenv("LOCALAPPDATA")
    monkeypatch.setenv("APPDATA", str(tmp_path / "roaming"))
    assert launcher.data_dir() == tmp_path / "roaming" / "IdahoHuntPlanner"


def test_data_dir_is_never_inside_the_installed_program(launcher, monkeypatch, tmp_path):
    """A packaged app is installed read-only; the DB must live in the user profile."""
    monkeypatch.delenv("IHP_DIR", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    from planner.app import REPO_ROOT as APP_ROOT

    assert APP_ROOT not in launcher.data_dir().parents


def test_get_port_argument_beats_environment(launcher, monkeypatch):
    monkeypatch.setenv("PLANNER_PORT", "6060")
    assert launcher.get_port(7070) == 7070
    assert launcher.get_port(None) == 6060
    monkeypatch.delenv("PLANNER_PORT")
    assert launcher.get_port(None) == launcher.DEFAULT_PORT == 5050


def test_parse_args_defaults_and_fetch_flags(launcher):
    assert launcher.parse_args([]).fetch == "auto"
    assert launcher.parse_args(["--fetch-regs"]).fetch == "always"
    assert launcher.parse_args(["--no-fetch-regs"]).fetch == "never"
    assert launcher.parse_args([]).no_browser is False
    assert launcher.parse_args(["--no-browser"]).no_browser is True
    assert launcher.parse_args(["--port", "5057"]).port == 5057
    with pytest.raises(SystemExit):  # the two fetch flags contradict each other
        launcher.parse_args(["--fetch-regs", "--no-fetch-regs"])


def test_should_fetch_only_auto_consults_the_database(launcher, monkeypatch):
    calls: list[str] = []

    def fake_count(base_url: str) -> int:
        calls.append(base_url)
        return 0

    monkeypatch.setattr(launcher, "document_count", fake_count)
    assert launcher.should_fetch("http://x", "always") is True
    assert launcher.should_fetch("http://x", "never") is False
    assert calls == []  # neither asked the server
    assert launcher.should_fetch("http://x", "auto") is True
    assert calls == ["http://x"]

    monkeypatch.setattr(launcher, "document_count", lambda url: 6)
    assert launcher.should_fetch("http://x", "auto") is False


def test_unreachable_server_is_a_warning_not_a_crash(launcher, monkeypatch):
    monkeypatch.setattr(launcher, "_post_json", lambda url, timeout: None)
    lines = launcher.fetch_regs("http://127.0.0.1:1")
    assert any("idfg.idaho.gov" in line for line in lines)
    assert any("Get latest regs" in line for line in lines)


def test_describe_fetch_reports_what_was_staged(launcher):
    lines = launcher.describe_fetch(
        {
            "imported": [1],
            "found": [
                {
                    "filename": "seasons-rules-big-game-2026.pdf",
                    "kind": "big_game",
                    "year": 2026,
                    "state": "imported",
                    "import": {"staged": 853, "pages": 125},
                },
                {
                    "filename": "seasons-rules-big-game-supplemental-proclamation-2026-2.pdf",
                    "kind": "supplement",
                    "year": 2026,
                    "state": "unsupported",
                    "reason": "supplements are not imported yet",
                },
            ],
            "sources": [{"url": "https://idfg.idaho.gov/rules", "status": "ok"}],
        }
    )
    body = "\n".join(lines)
    assert "1 new document(s) imported, 2 regulation PDF(s) seen" in body
    assert "853 rows staged from 125 pages" in body
    assert "supplements are not imported yet" in body
    assert "Nothing is published yet" in body  # D3: never publish without review


def test_health_returns_none_when_nothing_is_listening(launcher):
    assert launcher.health("http://127.0.0.1:1", timeout=0.5) is None


def test_main_reuses_a_server_that_is_already_running(launcher, monkeypatch):
    opened: list[str] = []
    monkeypatch.setattr(launcher, "health", lambda base_url, timeout=3.0: {"ok": True})
    monkeypatch.setattr(launcher.webbrowser, "open", lambda url: opened.append(url))
    monkeypatch.setattr(
        launcher, "create_app", lambda **kw: pytest.fail("a second server was started")
    )
    assert launcher.main(["--port", "5050"]) == 0
    assert opened == ["http://127.0.0.1:5050/"]
    assert launcher.main(["--port", "5050", "--no-browser"]) == 0
    assert len(opened) == 1


# ───────── the build recipe ─────────


def _spec_constant(name: str):
    """Read a module-level literal out of the PyInstaller spec without executing it."""
    tree = ast.parse(SPEC.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == name for t in node.targets
        ):
            return ast.literal_eval(node.value)
    raise AssertionError(f"{name} not found in {SPEC.name}")


def test_every_bundled_path_exists_in_the_repo():
    data_files = _spec_constant("DATA_FILES")
    assert data_files, "the bundle would ship no data at all"
    for source, _dest in data_files:
        assert (REPO_ROOT / source).exists(), f"planner.spec bundles missing path {source}"


def test_the_bundle_reproduces_the_layout_the_app_walks_to():
    """`planner/app.py` resolves `static/` from the package's parent directory."""
    destinations = {dest for _src, dest in _spec_constant("DATA_FILES")}
    assert "static" in destinations
    assert "planner" in destinations  # schema.sql beside planner/db.py
    assert "planner/migrations" in destinations


def test_the_spec_freezes_the_windows_launcher_with_a_console():
    text = SPEC.read_text(encoding="utf-8")
    assert "ihp_win.py" in text
    assert "console=True" in text  # the first-run download reports into it


def test_the_installer_version_tracks_the_package_version():
    text = ISS.read_text(encoding="utf-8")
    match = re.search(r'#define\s+AppVersion\s+"([^"]+)"', text)
    assert match, "installer.iss defines no AppVersion"
    assert match.group(1) == planner.__version__


def test_the_installer_packs_the_pyinstaller_build_and_needs_no_admin():
    text = ISS.read_text(encoding="utf-8")
    assert r"..\..\dist\IdahoHuntPlanner\*" in text
    assert "PrivilegesRequired=lowest" in text
    assert "OutputBaseFilename=IdahoHuntPlanner-Setup-{#AppVersion}" in text
    assert "IdahoHuntPlanner.exe" in text
    # The user's hunt database must survive an uninstall.
    assert "UninstallDelete" not in text


def test_the_workflow_builds_smoke_tests_and_uploads_on_windows():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "runs-on: windows-latest" in text
    assert 'python-version: "3.12"' in text
    assert "pyinstaller --clean --noconfirm packaging/windows/planner.spec" in text
    assert "ISCC.exe" in text and "installer.iss" in text
    assert "/api/health" in text  # the exe is run, not just built
    assert "actions/upload-artifact@v4" in text
    assert "if-no-files-found: error" in text


def test_the_readme_tells_a_windows_user_what_to_download():
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    assert "Windows" in readme
    assert "IdahoHuntPlanner-Setup" in readme
