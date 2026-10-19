"""Discovery and fetch safety for the "Get latest regs" check (T-28, §12).

Zero network calls: discovery runs against the verbatim IDFG pages recorded in
`tests/fixtures/idfg_pages/` (see `record_idfg_pages.py`), and the HTTP client is
exercised through an injected opener.
"""

from __future__ import annotations

import gzip
import io
import json
import urllib.error
from pathlib import Path

import pytest

from planner.importer import fetch_idfg

PAGES = Path(__file__).resolve().parent / "fixtures" / "idfg_pages"


def recorded() -> dict[str, dict]:
    return json.loads((PAGES / "sources.json").read_text(encoding="utf-8"))


def page(slug: str) -> str:
    return gzip.decompress((PAGES / f"{slug}.html.gz").read_bytes()).decode("utf-8")


def discovered() -> list[fetch_idfg.PdfLink]:
    """Every regulation PDF the five recorded pages link to, merged and deduped."""
    index = recorded()
    return fetch_idfg.merge_links(
        fetch_idfg.links_in(page(slug), meta["url"]) for slug, meta in sorted(index.items())
    )


# ───────── discovery against the recorded pages ─────────
def test_every_source_page_was_recorded():
    """The fixtures cover exactly the pages the check reads, so nothing is untested."""
    assert {meta["url"] for meta in recorded().values()} == set(fetch_idfg.SOURCE_PAGES)


def test_discovery_finds_all_four_brochures_and_the_supplement():
    """AC1: kind and year are right for each of the four brochures plus a supplement."""
    by_name = {link.filename: link for link in discovered()}
    expected = {
        "seasons-rules-big-game-2026.pdf": ("big_game", 2026, 2026),
        "seasons-rules-upland-game-2026-2027.pdf": ("upland", 2026, 2027),
        "seasons-rules-moose-sheep-goat-2025-26.pdf": ("moose_sheep_goat", 2025, 2026),
        "migratorygame2026-2027_web.pdf": ("migratory", 2026, 2027),
        "seasons-rules-big-game-supplemental-proclamation-2026-2.pdf": (
            "supplemental",
            2026,
            2026,
        ),
    }
    for filename, (kind, year, year_end) in expected.items():
        link = by_name[filename]
        assert (link.kind, link.license_year, link.year_end) == (kind, year, year_end), filename


def test_the_supplement_is_not_read_as_a_big_game_brochure():
    """Its real name starts with `seasons-rules-big-game-`, so order matters in the table."""
    name = "seasons-rules-big-game-supplemental-proclamation-2026-2.pdf"
    assert fetch_idfg.infer_kind(name) == "supplemental"
    # …and the trailing `-2` revision number is not mistaken for a year.
    assert fetch_idfg.infer_years(name) == (2026, 2026)


def test_a_brochures_deep_links_collapse_to_one_pdf():
    """The Big Game page links the same PDF once per chapter (`…pdf#page=51`)."""
    html = page("rules-big-game")
    assert html.count("seasons-rules-big-game-2026.pdf") > 10
    links = fetch_idfg.links_in(html, "https://idfg.idaho.gov/rules/big-game")
    urls = [link.url for link in links]
    assert (
        urls.count("https://idfg.idaho.gov/sites/default/files/seasons-rules-big-game-2026.pdf")
        == 1
    )
    assert not any("#" in url for url in urls)


def test_the_prior_years_brochure_is_still_discovered():
    """The Big Game page keeps last year's PDF; the sha256 check decides it is known."""
    names = {link.filename for link in discovered()}
    assert "seasons-rules-big-game-2025.pdf" in names


@pytest.mark.parametrize(
    "filename",
    [
        "seasons-rules-fish-2025-2027.pdf",  # fishing is not a hunting brochure
        "seasons-rules-falconry-peregine-2025-2027.pdf",
        "sage-grouse-seasons-rules-2026-2027.pdf",
        "2026_lap_brochure_web.pdf",  # landowner appreciation permits
        "lph_brochure_2026_final_web.pdf",
    ],
)
def test_other_pdfs_on_the_same_pages_are_ignored(filename):
    """The rules index links a dozen PDFs we must not import (D4: four brochures)."""
    assert fetch_idfg.infer_kind(filename) is None
    assert filename not in {link.filename for link in discovered()}


def test_discovery_reports_a_dead_page_and_still_reads_the_others():
    """AC3: one failing source never hides the rest."""

    class Fetcher:
        def get(self, url):
            if url.endswith("/rules/big-game"):
                raise fetch_idfg.FetchError("HTTP 404 Not Found", url=url, reason="http_error")
            return fetch_idfg.Fetched(url, page("rules-upland").encode())

    result = fetch_idfg.discover(Fetcher(), fetch_idfg.SOURCE_PAGES)
    statuses = {s.url.rsplit("/", 1)[-1]: s.status for s in result.sources}
    assert statuses["big-game"] == "error"
    assert sorted(statuses.values()).count("ok") == len(fetch_idfg.SOURCE_PAGES) - 1
    assert any(link.kind == "upland" for link in result.links)


def test_links_are_absolute_and_deduped_across_pages():
    """Every page links `/sites/default/files/…`; the merge keeps one entry per URL."""
    links = discovered()
    assert all(link.url.startswith("https://idfg.idaho.gov/sites/default/files/") for link in links)
    assert len({link.url for link in links}) == len(links)


# ───────── fetch safety (AC4) ─────────
@pytest.mark.parametrize(
    "url",
    [
        "http://idfg.idaho.gov/rules",  # not https
        "https://example.com/seasons-rules-big-game-2026.pdf",  # not IDFG
        "https://evil.idfg.idaho.gov.attacker.test/x.pdf",  # lookalike host
        "ftp://idfg.idaho.gov/x.pdf",
    ],
)
def test_a_foreign_or_insecure_url_is_refused(url):
    with pytest.raises(fetch_idfg.FetchError):
        fetch_idfg.require_allowed(url)
    assert not fetch_idfg.is_allowed(url)


def test_a_link_to_another_host_is_never_discovered():
    html = '<a href="https://example.com/seasons-rules-big-game-2026.pdf">brochure</a>'
    assert fetch_idfg.links_in(html, "https://idfg.idaho.gov/rules") == []


class _Response(io.BytesIO):
    """The slice of `http.client.HTTPResponse` the fetcher uses."""

    def __init__(self, data: bytes, content_type: str = "application/pdf") -> None:
        super().__init__(data)
        self.headers = {"Content-Type": content_type}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


def _fetcher(opener, **kw):
    return fetch_idfg.HttpFetcher(opener=opener, sleep=lambda s: None, **kw)


def test_an_oversize_body_is_refused_without_buffering_it_all():
    """AC4: the 50 MB cap is enforced by reading one byte past it."""
    reads: list[int] = []

    class Big(_Response):
        def read(self, n=-1):
            reads.append(n)
            return b"%PDF-" + b"x" * (n - 5)

    fetcher = _fetcher(lambda req, timeout=None: Big(b""), max_bytes=1024)
    with pytest.raises(fetch_idfg.FetchError) as exc:
        fetcher.get("https://idfg.idaho.gov/x.pdf")
    assert exc.value.reason == "too_large"
    assert reads == [1025]


def test_http_and_network_errors_become_fetch_errors():
    def http_error(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 404, "Not Found", {}, None)

    def unreachable(req, timeout=None):
        raise urllib.error.URLError("name resolution failed")

    def slow(req, timeout=None):
        raise TimeoutError

    for opener, reason in (
        (http_error, "http_error"),
        (unreachable, "unreachable"),
        (slow, "timeout"),
    ):
        with pytest.raises(fetch_idfg.FetchError) as exc:
            _fetcher(opener).get("https://idfg.idaho.gov/x.pdf")
        assert exc.value.reason == reason


def test_the_client_identifies_itself_and_waits_between_requests():
    """§12 politeness: a custom User-Agent, 1 request per 2 seconds, 60 s timeout."""
    seen: list[tuple[str, str, float]] = []
    slept: list[float] = []
    # now() is read once after each request and once before each later one.
    clock = iter([0.0, 0.5, 0.5, 10.0, 10.0])

    def opener(req, timeout=None):
        seen.append((req.full_url, req.get_header("User-agent"), timeout))
        return _Response(b"%PDF-1.7")

    fetcher = fetch_idfg.HttpFetcher(
        opener=opener, sleep=slept.append, now=lambda: next(clock), min_interval=2.0
    )
    for _ in range(3):
        fetcher.get("https://idfg.idaho.gov/x.pdf")
    assert [ua for _, ua, _ in seen] == [fetch_idfg.USER_AGENT] * 3
    assert [t for _, _, t in seen] == [fetch_idfg.REQUEST_TIMEOUT] * 3
    # Second request came 0.5 s later → wait 1.5 s; the third came 9.5 s later → no wait.
    assert slept == [1.5]
