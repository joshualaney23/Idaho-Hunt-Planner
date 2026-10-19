"""The one-step installer is syntactically sound and stays executable (T-30, §12).

The script itself is exercised for real in the PR evidence (a fresh clone, a second
run, a Mac with no Python 3.12). What a test can own cheaply is the part that breaks
silently: a syntax error, a lost executable bit after a clone, and the promises the
script makes about `data/` and `PLANNER_PORT`.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SETUP = REPO_ROOT / "scripts" / "setup.sh"
LAUNCHER = REPO_ROOT / "Install Idaho Hunt Planner.command"


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, check=True
    ).stdout


@pytest.mark.parametrize("script", [SETUP, LAUNCHER], ids=["setup.sh", "command"])
def test_the_installer_scripts_parse(script: Path) -> None:
    assert script.exists(), f"{script.name} is missing"
    result = subprocess.run(["bash", "-n", str(script)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("script", [SETUP, LAUNCHER], ids=["setup.sh", "command"])
def test_the_installer_scripts_are_executable_in_git(script: Path) -> None:
    """Mode 100755 in the index, or a fresh clone can't run them (§12)."""
    rel = script.relative_to(REPO_ROOT).as_posix()
    entry = _git("ls-files", "-s", "--", rel).strip()
    assert entry, f"{rel} is not tracked"
    assert entry.split()[0] == "100755", entry


def test_setup_never_deletes_anything_under_data() -> None:
    """The script may read `data/`, but `rm`/`mv` must never point at it (§12)."""
    body = SETUP.read_text()
    for line in body.splitlines():
        code = line.split("#", 1)[0]
        if re.search(r"\b(rm|mv|rmdir|truncate)\b", code):
            assert "data/" not in code and "$DATA" not in code, line


def test_setup_honors_planner_port_and_leaves_the_default_at_5050() -> None:
    body = SETUP.read_text()
    assert 'PORT="${PLANNER_PORT:-5050}"' in body
    # Nothing may hard-code the port once PORT is known.
    uses = [ln for ln in body.splitlines() if "5050" in ln and "PLANNER_PORT" not in ln]
    assert uses == [], uses


def test_the_launcher_runs_the_setup_script_from_the_repo_directory() -> None:
    body = LAUNCHER.read_text()
    assert "cd -- " in body, "the .command must cd to its own directory for Finder"
    assert "./scripts/setup.sh" in body


@pytest.mark.skipif(shutil.which("shellcheck") is None, reason="shellcheck not installed")
def test_shellcheck_is_clean() -> None:
    result = subprocess.run(
        ["shellcheck", str(SETUP), str(LAUNCHER)], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stdout + result.stderr
