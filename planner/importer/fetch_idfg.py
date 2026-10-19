"""Find newly posted IDFG regulation PDFs (T-28, DESIGN.md §12).

Two halves, split so the tests never touch the network:

* **Discovery** is pure: `links_in(html, base_url)` reads one recorded rules page and
  returns the regulation PDFs it links to, with `kind` and `license_year` inferred from
  the filename. `tests/fixtures/idfg_pages/` holds verbatim recordings of the five real
  pages (see `record_idfg_pages.py`).
* **Fetching** is a small injected object. `HttpFetcher` is the only thing that opens a
  socket; every caller takes a `fetcher` argument, so `POST /api/documents/check-updates`
  runs end to end in tests against a fake.

Nothing here publishes or even stages: it hands bytes to the normal T-10 pipeline, which
stages them `needs_review` for the §8.4 review gate (D3).
"""

from __future__ import annotations

import re
import time
import urllib.error
import urllib.request
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Protocol
from urllib.parse import unquote, urljoin, urlsplit

#: The pages read on every check. A site reshuffle is a one-line fix here (§12).
#: `/rules` is the index: it is the only page that lists the supplemental proclamation
#: beside all four brochures, so it is the safety net when a topic page is restyled.
SOURCE_PAGES: tuple[str, ...] = (
    "https://idfg.idaho.gov/rules",
    "https://idfg.idaho.gov/rules/big-game",
    "https://idfg.idaho.gov/rules/upland",
    "https://idfg.idaho.gov/rules/moose-sheep-goat",
    "https://idfg.idaho.gov/rules/migratory",
)

#: Only these hosts are ever requested. `idfg.idaho.gov` is the live site; the department's
#: older `fishandgame.idaho.gov` name (the one §12 names) still resolves, so both are kept.
ALLOWED_HOSTS: frozenset[str] = frozenset({"idfg.idaho.gov", "fishandgame.idaho.gov"})

#: Politeness (§12): identify the client, wait between requests, give up after a minute.
USER_AGENT = "IdahoHuntPlanner/1.0 (local single-user hunt planner; manual check only)"
REQUEST_TIMEOUT = 60.0
MIN_INTERVAL = 2.0
#: Same cap as the upload endpoint (`importer.MAX_PDF_BYTES`), repeated here so the
#: fetcher can stop reading mid-stream instead of buffering a hostile response.
MAX_BYTES = 50 * 1024 * 1024

#: filename fragment → `source_documents.kind`. **Order matters**: the real supplement is
#: called `seasons-rules-big-game-supplemental-proclamation-2026-2.pdf`, so the supplement
#: pattern has to win over the big-game one. Anything not listed here is not a regulation
#: brochure we import (the same pages also link fishing, falconry, sage-grouse, LAP and LPH
#: PDFs, and `seasons-rules-fish-2025-2027.pdf` would otherwise look like a brochure).
_KIND_PATTERNS: tuple[tuple[str, str], ...] = (
    ("supplemental", r"supplemental[-_]?proclamation"),
    ("big_game", r"seasons[-_]rules[-_]big[-_]game"),
    ("upland", r"seasons[-_]rules[-_]upland|uplandgame"),
    ("moose_sheep_goat", r"seasons[-_]rules[-_]moose"),
    ("migratory", r"seasons[-_]rules[-_]migratory|migratorygame"),
)
_KIND_RES: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (kind, re.compile(pattern, re.IGNORECASE)) for kind, pattern in _KIND_PATTERNS
)

#: `2026-2027`, `2025-26` or a bare `2026` in a filename.
_YEAR_RANGE_RE = re.compile(r"((?:19|20)\d{2})[-_]((?:19|20)?\d{2})(?!\d)")
_YEAR_RE = re.compile(r"((?:19|20)\d{2})(?!\d)")


class FetchError(RuntimeError):
    """One request failed. `reason` is a stable code the API echoes to the UI."""

    def __init__(self, message: str, *, url: str = "", reason: str = "fetch_failed") -> None:
        super().__init__(message)
        self.url = url
        self.reason = reason


@dataclass(frozen=True)
class PdfLink:
    """One regulation PDF linked from a rules page."""

    url: str
    filename: str
    kind: str
    license_year: int | None
    year_end: int | None
    found_on: str = ""


@dataclass(frozen=True)
class Fetched:
    """A successful response body."""

    url: str
    data: bytes
    content_type: str = ""


@dataclass
class SourceStatus:
    """What happened with one requested URL, for the response's `sources` list."""

    url: str
    role: str  # 'page' | 'pdf'
    status: str  # 'ok' | 'error'
    error: str | None = None

    def to_json(self) -> dict[str, object]:
        body: dict[str, object] = {"url": self.url, "role": self.role, "status": self.status}
        if self.error:
            body["error"] = self.error
        return body


@dataclass
class Discovery:
    """Every page's outcome, plus the PDF links found across all of them."""

    sources: list[SourceStatus] = field(default_factory=list)
    links: list[PdfLink] = field(default_factory=list)


class Fetcher(Protocol):
    """What the API needs from an HTTP client: one GET that returns bytes or raises."""

    def get(self, url: str) -> Fetched: ...


# ───────── discovery (pure) ─────────
class _LinkParser(HTMLParser):
    """Collect every `<a href>` in document order."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.hrefs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        for name, value in attrs:
            if name == "href" and value:
                self.hrefs.append(value)


def hrefs_in(html: str) -> list[str]:
    """Every `<a href>` of a page, in document order."""
    parser = _LinkParser()
    parser.feed(html)
    parser.close()
    return parser.hrefs


def filename_of(url: str) -> str:
    """The last path segment, percent-decoded and without query or fragment."""
    return unquote(urlsplit(url).path.rsplit("/", 1)[-1])


def infer_kind(filename: str) -> str | None:
    """The `source_documents.kind` this filename names, or None when it is not a brochure."""
    for kind, pattern in _KIND_RES:
        if pattern.search(filename):
            return kind
    return None


def infer_years(filename: str) -> tuple[int, int] | None:
    """`(license_year, year_end)` from the filename, or None.

    `2026-2027` and `2025-26` are ranges; a bare `2026` covers one year. A trailing
    revision number (`…-2026-2.pdf`) is not a year, so the range only matches when the
    second number really reads as a year.
    """
    if m := _YEAR_RANGE_RE.search(filename):
        start = int(m.group(1))
        tail = m.group(2)
        end = int(tail) if len(tail) == 4 else int(str(start)[:2] + tail.zfill(2))
        if start <= end <= start + 1:
            return start, end
    if m := _YEAR_RE.search(filename):
        year = int(m.group(1))
        return year, year
    return None


def is_allowed(url: str) -> bool:
    """True when `url` is an https URL on an IDFG host (§12 safety)."""
    parts = urlsplit(url)
    return (
        parts.scheme == "https"
        and parts.hostname is not None
        and parts.hostname.lower() in ALLOWED_HOSTS
    )


def require_allowed(url: str) -> None:
    """Raise unless `url` is https on an IDFG host."""
    parts = urlsplit(url)
    if parts.scheme != "https":
        raise FetchError(
            f"only https URLs are fetched, not {parts.scheme or '(none)'}://",
            url=url,
            reason="insecure_url",
        )
    host = (parts.hostname or "").lower()
    if host not in ALLOWED_HOSTS:
        raise FetchError(
            f"{host or 'that host'} is not an IDFG host", url=url, reason="foreign_host"
        )


def links_in(html: str, base_url: str) -> list[PdfLink]:
    """The regulation PDFs linked from one rules page, deduped by URL.

    Fragments are dropped before deduping: the Big Game page links the same brochure
    fourteen times, once per `#page=N` deep link into a chapter.
    """
    found: dict[str, PdfLink] = {}
    for href in hrefs_in(html):
        absolute = urljoin(base_url, href)
        parts = urlsplit(absolute)
        clean = parts._replace(fragment="").geturl()
        if clean in found:
            continue
        name = filename_of(clean)
        if not name.lower().endswith(".pdf"):
            continue
        kind = infer_kind(name)
        if kind is None or not is_allowed(clean):
            continue
        years = infer_years(name)
        found[clean] = PdfLink(
            url=clean,
            filename=name,
            kind=kind,
            license_year=years[0] if years else None,
            year_end=years[1] if years else None,
            found_on=base_url,
        )
    return list(found.values())


def merge_links(groups: Iterable[Iterable[PdfLink]]) -> list[PdfLink]:
    """All pages' links as one list, first occurrence of each URL winning."""
    merged: dict[str, PdfLink] = {}
    for group in groups:
        for link in group:
            merged.setdefault(link.url, link)
    return list(merged.values())


def discover(fetcher: Fetcher, pages: Sequence[str] = SOURCE_PAGES) -> Discovery:
    """Read every source page and collect its regulation PDF links.

    A page that errors is reported in `sources` and never stops the others (§12).
    """
    result = Discovery()
    groups: list[list[PdfLink]] = []
    for page in pages:
        try:
            require_allowed(page)
            fetched = fetcher.get(page)
        except FetchError as exc:
            result.sources.append(SourceStatus(page, "page", "error", str(exc)))
            continue
        except Exception as exc:  # a client we did not write: never kill the run
            result.sources.append(
                SourceStatus(page, "page", "error", f"{type(exc).__name__}: {exc}")
            )
            continue
        html = fetched.data.decode("utf-8", errors="replace")
        groups.append(links_in(html, page))
        result.sources.append(SourceStatus(page, "page", "ok"))
    result.links = merge_links(groups)
    result.links.sort(key=lambda link: (link.kind, link.filename))
    return result


# ───────── fetching (the only socket in the feature) ─────────
class HttpFetcher:
    """A polite, bounded HTTP client: https + IDFG hosts only, 1 request / 2 s, 50 MB cap.

    `sleep`/`now` and `opener` are injectable so the rate limiter and the size cap can be
    tested without a network.
    """

    def __init__(
        self,
        *,
        user_agent: str = USER_AGENT,
        timeout: float = REQUEST_TIMEOUT,
        min_interval: float = MIN_INTERVAL,
        max_bytes: int = MAX_BYTES,
        opener: object | None = None,
        sleep: object | None = None,
        now: object | None = None,
    ) -> None:
        self.user_agent = user_agent
        self.timeout = timeout
        self.min_interval = min_interval
        self.max_bytes = max_bytes
        self._open = opener or urllib.request.urlopen
        self._sleep = sleep or time.sleep
        self._now = now or time.monotonic
        self._last: float | None = None

    def _wait(self) -> None:
        if self._last is None:
            return
        remaining = self.min_interval - (self._now() - self._last)  # type: ignore[operator]
        if remaining > 0:
            self._sleep(remaining)  # type: ignore[operator]

    def get(self, url: str) -> Fetched:
        """GET `url`, refusing anything that is not a bounded response from IDFG."""
        require_allowed(url)
        self._wait()
        request = urllib.request.Request(url, headers={"User-Agent": self.user_agent})
        try:
            with self._open(request, timeout=self.timeout) as response:  # type: ignore[operator]
                # One byte past the cap is enough to know the body is too big.
                data = response.read(self.max_bytes + 1)
                content_type = response.headers.get("Content-Type", "") or ""
        except urllib.error.HTTPError as exc:
            raise FetchError(
                f"HTTP {exc.code} {exc.reason}", url=url, reason="http_error"
            ) from None
        except urllib.error.URLError as exc:
            raise FetchError(f"{exc.reason}", url=url, reason="unreachable") from None
        except TimeoutError:
            raise FetchError(
                f"no response within {self.timeout:.0f}s", url=url, reason="timeout"
            ) from None
        finally:
            self._last = self._now()  # type: ignore[operator]
        if len(data) > self.max_bytes:
            raise FetchError(
                f"larger than the {self.max_bytes // (1024 * 1024)} MB limit",
                url=url,
                reason="too_large",
            )
        return Fetched(url=url, data=data, content_type=content_type)
