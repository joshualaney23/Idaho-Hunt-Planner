"""Definition of done: `python -m planner --no-browser` must start and serve /api/health."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_python_m_planner_starts():
    port = _free_port()
    env = {**os.environ, "PLANNER_PORT": str(port)}
    proc = subprocess.Popen(
        [sys.executable, "-m", "planner", "--no-browser"],
        cwd=REPO_ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    try:
        deadline = time.monotonic() + 15
        last_err: Exception | None = None
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                out = proc.stdout.read().decode() if proc.stdout else ""
                raise AssertionError(f"server exited early ({proc.returncode}):\n{out}")
            try:
                url = f"http://127.0.0.1:{port}/api/health"
                with urllib.request.urlopen(url, timeout=1) as resp:
                    assert json.load(resp)["ok"] is True
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=1) as resp:
                    assert resp.status == 200
                return
            except OSError as e:  # not listening yet
                last_err = e
                time.sleep(0.2)
        raise AssertionError(f"server did not answer within 15s: {last_err}")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
