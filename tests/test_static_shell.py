"""T-03 app shell: structure, design tokens and the "colors only from theme.css" rule.

The browser-level checks (routing with back/forward, Lighthouse accessibility,
screenshots) are recorded in the T-03 PR. These tests pin the static contract so
later tickets can't silently break it.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from html.parser import HTMLParser
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
STATIC = REPO / "static"
THEME = STATIC / "css" / "theme.css"
APP_CSS = STATIC / "css" / "app.css"
INDEX = STATIC / "index.html"
TAB_IDS = ["map", "tags", "seasons", "regs", "settings"]

# A CSS/JS color literal: #rgb, #rgba, #rrggbb, #rrggbbaa, rgb()/rgba()/hsl()/hsla().
COLOR_LITERAL = re.compile(
    r"#(?:[0-9a-fA-F]{8}|[0-9a-fA-F]{6}|[0-9a-fA-F]{3,4})\b|\b(?:rgba?|hsla?)\(", re.ASCII
)
VAR_DEF = re.compile(r"(--[\w-]+)\s*:\s*([^;]+);")
VAR_USE = re.compile(r"var\(\s*(--[\w-]+)")


def _app_js() -> list[Path]:
    return sorted(p for p in (STATIC / "js").glob("*.js"))


def _theme_vars() -> dict[str, str]:
    return {k: " ".join(v.split()) for k, v in VAR_DEF.findall(THEME.read_text())}


class _Index(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.elements: list[tuple[str, dict[str, str | None]]] = []
        self.h1_spans: list[str] = []
        self._in_h1_span = False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        self.elements.append((tag, a))
        if tag == "span" and self.elements and any(t == "h1" for t, _ in self.elements[-2:]):
            self._in_h1_span = True

    def handle_endtag(self, tag):
        if tag == "span":
            self._in_h1_span = False

    def handle_data(self, data):
        if self._in_h1_span and data.strip():
            self.h1_spans.append(data.strip())

    def find(self, **want: str) -> list[dict[str, str | None]]:
        return [a for _, a in self.elements if all(a.get(k) == v for k, v in want.items())]


@pytest.fixture(scope="module")
def index() -> _Index:
    p = _Index()
    p.feed(INDEX.read_text())
    return p


# ---- design tokens ---------------------------------------------------------


def test_theme_tokens_match_design_4_1():
    """Every token in DESIGN.md §4.1 exists in theme.css with the same value."""
    design = (REPO / "docs" / "DESIGN.md").read_text()
    block = design.split("### 4.1 Design tokens", 1)[1].split("```css", 1)[1].split("```", 1)[0]
    spec = {k: " ".join(v.split()) for k, v in VAR_DEF.findall(block)}
    assert len(spec) >= 25, "could not parse the §4.1 token block"
    theme = _theme_vars()
    mismatched = {k: (v, theme.get(k)) for k, v in spec.items() if theme.get(k) != v}
    assert not mismatched, f"theme.css differs from §4.1: {mismatched}"


@pytest.mark.parametrize("path", [APP_CSS, INDEX, *_app_js()], ids=lambda p: p.name)
def test_no_color_literals_outside_theme(path: Path):
    text = path.read_text()
    if path.suffix == ".css":
        text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    hits = COLOR_LITERAL.findall(text)
    assert not hits, f"{path.name} has color literals {hits}; add a token to theme.css instead"


@pytest.mark.parametrize("path", [APP_CSS, INDEX, *_app_js()], ids=lambda p: p.name)
def test_every_var_used_is_defined_in_theme(path: Path):
    used = set(VAR_USE.findall(path.read_text()))
    missing = used - set(_theme_vars())
    assert not missing, f"{path.name} uses undefined tokens {sorted(missing)}"


def test_fonts_have_system_fallbacks():
    theme = _theme_vars()
    assert theme["--font-display"].startswith('"Bebas Neue"')
    assert theme["--font-body"].startswith('"DM Sans"')
    assert theme["--font-mono"].startswith('"DM Mono"')
    for k in ("--font-display", "--font-body", "--font-mono"):
        assert theme[k].endswith(("sans-serif", "monospace")), f"{k} lacks a generic fallback"


def test_focus_ring_is_gold2():
    css = THEME.read_text()
    rule = re.search(r":focus-visible\s*\{([^}]*)\}", css)
    assert rule and "var(--gold2)" in rule.group(1) and "solid" in rule.group(1)


def test_layout_stacks_below_1100_and_chips_scroll_below_700():
    css = APP_CSS.read_text()
    narrow = re.search(r"@media \(max-width: 1099px\)\s*\{(.*?)\n\}", css, re.S)
    assert narrow and "grid-template-columns: 1fr" in narrow.group(1)
    phone = re.search(r"@media \(max-width: 699px\)\s*\{(.*?)\n\}", css, re.S)
    assert phone and "overflow-x: auto" in phone.group(1) and "nowrap" in phone.group(1)


# ---- index.html structure -------------------------------------------------


def test_tablist_and_tabs(index: _Index):
    assert len(index.find(role="tablist")) == 1
    tabs = index.find(role="tab")
    assert [t["href"] for t in tabs] == [f"#/{t}" for t in TAB_IDS]
    assert [t["aria-selected"] for t in tabs] == ["true"] + ["false"] * 4
    panels = {p["id"]: p for p in index.find(role="tabpanel")}
    for t in tabs:
        panel = panels[t["aria-controls"]]
        assert panel["aria-labelledby"] == t["id"]


def test_router_tabs_match_index():
    router = (STATIC / "js" / "router.js").read_text()
    assert re.findall(r'id: "(\w+)"', router) == TAB_IDS


def test_header_disclaimer_and_assets(index: _Index):
    assert index.h1_spans == ["HUNT"], "title must be IDAHO <span>HUNT</span> PLANNER"
    html = INDEX.read_text()
    assert "IDAHO <span>HUNT</span> PLANNER" in html
    assert "VERIFY" in html and "idfg.idaho.gov" in html
    assert index.find(id="disclaimer-dismiss")
    fonts = [
        str(a["href"])
        for a in index.find(rel="stylesheet")
        if "fonts.googleapis" in (a["href"] or "")
    ]
    assert fonts and all(f in fonts[0] for f in ("Bebas+Neue", "DM+Sans", "DM+Mono"))
    assert index.find(type="module", src="/static/js/main.js")
    for container in ("viewing", "filters", "map-wrap", "ver", "toasts"):
        assert index.find(id=container), container
    js = (STATIC / "js" / "main.js").read_text()
    assert "sessionStorage" in js, "disclaimer dismissal is per session (§4.2)"


def test_index_and_assets_are_served(client):
    resp = client.get("/")
    assert resp.status_code == 200 and b'role="tablist"' in resp.data
    for path in ("css/theme.css", "css/app.css", "js/main.js", "js/ui.js", "js/router.js"):
        r = client.get(f"/static/{path}")
        assert r.status_code == 200, path
        if path.endswith(".js"):
            assert "javascript" in r.mimetype, (path, r.mimetype)
        r.close()


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed (CI runs it)")
@pytest.mark.parametrize("name", ["ui.test.mjs", "router.test.mjs"])
def test_js_components_under_node(name: str):
    proc = subprocess.run(
        ["node", "--test", f"static/js/{name}"],
        cwd=REPO,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
