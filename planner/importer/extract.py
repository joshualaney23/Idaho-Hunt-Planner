"""pdfplumber wrapper: crop, tables, text, rotation, DESIGN.md §8.2 (T-10).

`iter_pages(path)` yields one `Page` per PDF page, already

* `dedupe_chars`-ed (fake-bold headings are printed several times on top of each other),
* cropped `SIDE_CROP_PT` off the left and right (vertical section tabs otherwise get
  detected as *the* table and hide the real one),
* flagged `rotated` when more than `ROTATED_SHARE` of its chars are sideways, and read
  with the rotated-text options in that case, and
* cached as JSON under `data/cache/<sha256>/p<N>.json`, so re-parsing after a parser fix
  doesn't re-run pdfplumber.

Two independent views of each page are kept on purpose (§8.7): `tables` is pdfplumber's
ruled-table path, `text` is the raw text layer. Parsers compare them.
"""

from __future__ import annotations

import json
import statistics
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, Any

import pdfplumber

#: Points cropped off each side before extraction (§8.2).
SIDE_CROP_PT = 40.0
#: Share of sideways chars that makes a page a rotated layout (§8.2 → T-12).
ROTATED_SHARE = 0.30
#: Superscript cutoff: chars below this share of the page's median font size (§8.2).
SUPERSCRIPT_SHARE = 0.75
#: Bumped whenever the cached shape changes, which invalidates older cache files.
CACHE_VERSION = 1

Table = list[list[str | None]]


@dataclass(frozen=True)
class Page:
    """One extracted page: both extraction paths plus the geometry parsers need."""

    number: int  # 1-based PDF index (NOT the printed page number)
    text: str
    tables: list[Table] = field(default_factory=list)
    rotated: bool = False
    width: float = 0.0
    height: float = 0.0
    font_size: float = 0.0  # median body font size, for superscript detection

    @property
    def lines(self) -> list[str]:
        return [ln for ln in self.text.splitlines() if ln.strip()]

    def to_json(self) -> dict[str, Any]:
        return {
            "v": CACHE_VERSION,
            "number": self.number,
            "text": self.text,
            "tables": self.tables,
            "rotated": self.rotated,
            "width": self.width,
            "height": self.height,
            "font_size": self.font_size,
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> Page:
        return cls(
            number=int(data["number"]),
            text=data["text"],
            tables=[[list(row) for row in t] for t in data["tables"]],
            rotated=bool(data["rotated"]),
            width=float(data["width"]),
            height=float(data["height"]),
            font_size=float(data.get("font_size") or 0.0),
        )


# ───────── page-level helpers ─────────


def _open(source: str | Path | IO[bytes]) -> Any:
    """`pdfplumber.open` on a path or an in-memory file (uploads are classified first)."""
    return pdfplumber.open(str(source) if isinstance(source, str | Path) else source)


def median_font_size(page: Any) -> float:
    """Median body font size of a page (0.0 when it has no chars)."""
    sizes = [c["size"] for c in page.chars if c.get("size")]
    return float(statistics.median(sizes)) if sizes else 0.0


def rotated_share(page: Any) -> float:
    """Share of the page's chars that are sideways."""
    chars = page.chars
    if not chars:
        return 0.0
    return sum(1 for c in chars if not c.get("upright", True)) / len(chars)


def is_rotated(page: Any) -> bool:
    """True for calendar-style pages printed sideways (§8.2)."""
    return rotated_share(page) > ROTATED_SHARE


def without_superscripts(page: Any, body_size: float | None = None) -> Any:
    """The page with footnote-marker chars removed, so they don't glue onto cell text.

    Markers are smaller than `SUPERSCRIPT_SHARE` of the page's median body size — a
    fixed point cutoff is unsafe (MSG prints a legitimate 7.0 pt `a`, §8.2). Read the
    markers back from the *unfiltered* page: crops keep absolute coordinates.
    """
    size = body_size if body_size is not None else median_font_size(page)
    if not size:
        return page
    cutoff = size * SUPERSCRIPT_SHARE
    return page.filter(
        lambda obj: obj.get("object_type") != "char" or obj.get("size", size) >= cutoff
    )


def crop_sides(page: Any, margin: float = SIDE_CROP_PT) -> Any:
    """Crop `margin` points off the left and right edges (§8.2)."""
    if margin <= 0 or page.width <= 2 * margin:
        return page
    return page.crop((margin, 0, page.width - margin, page.height))


def extract_page(page: Any, number: int, *, margin: float = SIDE_CROP_PT) -> Page:
    """Build a `Page` from a pdfplumber page (no caching)."""
    body = page.dedupe_chars(tolerance=1)
    rotated = is_rotated(page)
    size = median_font_size(page)
    cropped = crop_sides(body, margin)
    opts: dict[str, Any] = {"line_dir_rotated": "ltr", "char_dir_rotated": "btt"} if rotated else {}
    text = cropped.extract_text(**opts) or ""
    tables: list[Table] = [] if rotated else [list(t) for t in cropped.extract_tables()]
    return Page(
        number=number,
        text=text,
        tables=tables,
        rotated=rotated,
        width=float(page.width),
        height=float(page.height),
        font_size=size,
    )


# ───────── caching ─────────


def cache_dir_for(sha256: str, data_dir: str | Path = "data") -> Path:
    """`data/cache/<sha256>/` — the per-page JSON cache directory (§8.2)."""
    return Path(data_dir) / "cache" / sha256


def _cache_path(cache_dir: Path, number: int) -> Path:
    return cache_dir / f"p{number}.json"


def load_cached(cache_dir: str | Path, number: int) -> Page | None:
    """A cached page, or None when it is absent or was written by an older version."""
    path = _cache_path(Path(cache_dir), number)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if data.get("v") != CACHE_VERSION:
        return None
    return Page.from_json(data)


def save_cached(cache_dir: str | Path, page: Page) -> Path:
    """Write one page's JSON cache file."""
    directory = Path(cache_dir)
    directory.mkdir(parents=True, exist_ok=True)
    path = _cache_path(directory, page.number)
    path.write_text(json.dumps(page.to_json()), encoding="utf-8")
    return path


# ───────── the iterator parsers use ─────────


def iter_pages(
    pdf_path: str | Path | IO[bytes],
    *,
    cache_dir: str | Path | None = None,
    pages: Sequence[int] | None = None,
    margin: float = SIDE_CROP_PT,
) -> Iterator[Page]:
    """Yield every page (or just `pages`, 1-based) of a PDF, using `cache_dir` if given.

    The PDF is opened lazily: when every requested page is already cached, pdfplumber
    is never started.
    """
    wanted = list(pages) if pages is not None else None
    cached: dict[int, Page] = {}
    if cache_dir is not None and wanted is not None:
        cached = {n: p for n in wanted if (p := load_cached(cache_dir, n)) is not None}
        if len(cached) == len(wanted):
            for n in wanted:
                yield cached[n]
            return

    with _open(pdf_path) as pdf:
        numbers = wanted if wanted is not None else range(1, len(pdf.pages) + 1)
        for number in numbers:
            if number in cached:
                yield cached[number]
                continue
            if cache_dir is not None and (hit := load_cached(cache_dir, number)) is not None:
                yield hit
                continue
            page = extract_page(pdf.pages[number - 1], number, margin=margin)
            if cache_dir is not None:
                save_cached(cache_dir, page)
            yield page


def page_texts(source: str | Path | IO[bytes], limit: int | None = None) -> list[str]:
    """Just the text of the first `limit` pages — the cheap path used by `classify`."""
    out: list[str] = []
    with _open(source) as pdf:
        for i, page in enumerate(pdf.pages, 1):
            if limit is not None and i > limit:
                break
            out.append(crop_sides(page.dedupe_chars(tolerance=1)).extract_text() or "")
    return out


def page_count(pdf_path: str | Path | IO[bytes]) -> int:
    """Number of pages in a PDF."""
    with _open(pdf_path) as pdf:
        return len(pdf.pages)
