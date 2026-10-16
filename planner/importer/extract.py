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

Controlled-hunt tables (T-17a) need two more things, so every page also carries

* `cell_tables` — the same ruled tables, but extracted from the page **without its
  superscript chars** (a footnote marker glues onto the cell text otherwise: `1-1a`)
  and with each cell's bbox, and
* `small_chars` — every char below the superscript cutoff **of the whole page**, with
  its position, so a parser can read the markers back per cell and resolve the footnote
  legend (whose marker is printed left of the `SIDE_CROP_PT` crop).
"""

from __future__ import annotations

import json
import statistics
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, Any

import pdfplumber

#: Most a side may be cropped before extraction (§8.2). The crop is *adaptive*: it
#: reaches exactly as far as the rotated section tab printed in that margin, and no
#: further, because a fixed 40 pt also cuts through real content (2026 p. 72: `60A` is
#: read as `0A` and the Notes column's right rule is lost).
SIDE_CROP_PT = 40.0
#: Share of sideways chars that makes a page a rotated layout (§8.2 → T-12).
ROTATED_SHARE = 0.30
#: Superscript cutoff: chars below this share of the page's median font size (§8.2).
SUPERSCRIPT_SHARE = 0.75
#: Height (pt) of the bottom band kept as `Page.footer`. The brochure prints its own page
#: number there, in the margin the side crop removes (T-17a printed → physical map).
FOOTER_BAND_PT = 40.0
#: Bumped whenever the cached shape changes, which invalidates older cache files.
CACHE_VERSION = 4

Table = list[list[str | None]]


@dataclass(frozen=True)
class Word:
    """One word of the text layer with its position — the text path's geometry (§8.7).

    Column membership and row bands are read from these positions, so the text path
    never borrows `find_tables`' ruled geometry.
    """

    text: str
    x0: float
    top: float
    x1: float
    bottom: float

    @property
    def cx(self) -> float:
        """Horizontal centre — what a word is assigned to a column by."""
        return (self.x0 + self.x1) / 2

    def to_json(self) -> list[Any]:
        return [self.text, self.x0, self.top, self.x1, self.bottom]

    @classmethod
    def from_json(cls, data: Sequence[Any]) -> Word:
        text, x0, top, x1, bottom = data
        return cls(str(text), float(x0), float(top), float(x1), float(bottom))


@dataclass(frozen=True)
class Cell:
    """One ruled-table cell: its text plus where it sits on the page.

    The bbox is what a parser applies back to the *unfiltered* page to read the
    superscript markers that were filtered out of `text` (crops and filters keep
    absolute coordinates). `text` is None for a cell pdfplumber found no content in.
    """

    text: str | None
    x0: float
    top: float
    x1: float
    bottom: float

    @property
    def bbox(self) -> tuple[float, float, float, float]:
        return (self.x0, self.top, self.x1, self.bottom)

    def to_json(self) -> list[Any]:
        return [self.text, self.x0, self.top, self.x1, self.bottom]

    @classmethod
    def from_json(cls, data: Sequence[Any]) -> Cell:
        text, x0, top, x1, bottom = data
        return cls(None if text is None else str(text), *(float(v) for v in (x0, top, x1, bottom)))


#: One ruled table as rows of cells (`None` where the table has no cell at all).
CellTable = list[list[Cell | None]]


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
    words: list[Word] = field(default_factory=list)  # text path geometry (§8.7)
    #: `tables` again, read without superscripts and with each cell's bbox (T-17a).
    cell_tables: list[CellTable] = field(default_factory=list)
    #: Chars below the superscript cutoff, uncropped: footnote markers and legend keys.
    small_chars: list[Word] = field(default_factory=list)
    #: The uncropped bottom band, which carries the brochure's printed page number.
    footer: str = ""
    #: The same page under the **tab-only** crop (`tab_margins`, §8.2), filled in only
    #: when the fixed crop cuts content that crop keeps. Read them through
    #: `full_tables`/`full_words`/`full_text`; `tables`/`words`/`text` stay on the fixed
    #: crop, so parsers written before T-13 see exactly what they always saw.
    wide_tables: list[Table] = field(default_factory=list)
    wide_words: list[Word] = field(default_factory=list)
    wide_text: str = ""

    @property
    def lines(self) -> list[str]:
        return [ln for ln in self.text.splitlines() if ln.strip()]

    @property
    def full_tables(self) -> list[Table]:
        """Ruled tables from the least-cropped view of this page (§8.2)."""
        return self.wide_tables or self.tables

    @property
    def full_words(self) -> list[Word]:
        """Text-layer words from the least-cropped view of this page (§8.2)."""
        return self.wide_words or self.words

    @property
    def full_text(self) -> str:
        """Page text from the least-cropped view of this page (§8.2)."""
        return self.wide_text or self.text

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
            "words": [w.to_json() for w in self.words],
            "cell_tables": [
                [[c.to_json() if c else None for c in row] for row in table]
                for table in self.cell_tables
            ],
            "small_chars": [w.to_json() for w in self.small_chars],
            "footer": self.footer,
            "wide_tables": self.wide_tables,
            "wide_words": [w.to_json() for w in self.wide_words],
            "wide_text": self.wide_text,
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
            words=[Word.from_json(w) for w in data.get("words") or ()],
            cell_tables=[
                [[Cell.from_json(c) if c else None for c in row] for row in table]
                for table in data.get("cell_tables") or ()
            ],
            small_chars=[Word.from_json(w) for w in data.get("small_chars") or ()],
            footer=str(data.get("footer") or ""),
            wide_tables=[[list(row) for row in t] for t in data.get("wide_tables") or ()],
            wide_words=[Word.from_json(w) for w in data.get("wide_words") or ()],
            wide_text=str(data.get("wide_text") or ""),
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


def tab_margins(page: Any, cap: float = SIDE_CROP_PT) -> tuple[float, float]:
    """How far the rotated section tab reaches into each side, capped at `cap` (§8.2).

    The tabs ("DEER / REGULAR") are printed sideways in the margin, and `find_tables`
    detects them as *the* table — that is what the crop is for. Cropping a fixed
    distance instead cuts real content: on the 2026 black bear page the units cell
    starts 30.7 pt in (`60A` becomes `0A`, `68` becomes `8` — valid-looking and silently
    wrong) and the Notes column's right rule sits 4 pt outside the crop, which makes
    `find_tables` drop that column. A side with no tab is therefore not cropped at all.
    """
    width = float(page.width)
    left = right = 0.0
    for char in page.chars:
        if char.get("upright", True):
            continue
        x0, x1 = float(char["x0"]), float(char["x1"])
        if x1 <= cap:
            left = max(left, x1)
        if x0 >= width - cap:
            right = max(right, width - x0)
    return (min(left, cap), min(right, cap))


def crop_page(page: Any, left: float, right: float) -> Any:
    """Crop `left`/`right` points off the page's sides (§8.2)."""
    if left <= 0 and right <= 0:
        return page
    if page.width <= left + right:
        return page
    return page.crop((left, 0, page.width - right, page.height))


def crop_tabs(page: Any, margin: float = SIDE_CROP_PT) -> Any:
    """Crop only as far as the section tabs reach, never more than `margin` (§8.2)."""
    return crop_page(page, *tab_margins(page, margin))


def crop_sides(page: Any, margin: float = SIDE_CROP_PT) -> Any:
    """Crop a fixed `margin` off both sides — the §8.2 crop every parser reads (T-10)."""
    if margin <= 0 or page.width <= 2 * margin:
        return page
    return page.crop((margin, 0, page.width - margin, page.height))


def _word(word: dict[str, Any]) -> Word:
    """One pdfplumber word as the cached `Word`."""
    return Word(
        text=word["text"],
        x0=round(float(word["x0"]), 2),
        top=round(float(word["top"]), 2),
        x1=round(float(word["x1"]), 2),
        bottom=round(float(word["bottom"]), 2),
    )


def small_chars(page: Any, body_size: float | None = None) -> list[Word]:
    """Every char below the superscript cutoff, with its position (uncropped).

    Footnote markers *and* the legend keys that explain them, which are printed in the
    margin outside `SIDE_CROP_PT` — so this reads the whole page, never the crop.
    """
    size = body_size if body_size is not None else median_font_size(page)
    if not size:
        return []
    cutoff = size * SUPERSCRIPT_SHARE
    return [
        Word(
            text=c["text"],
            x0=round(float(c["x0"]), 2),
            top=round(float(c["top"]), 2),
            x1=round(float(c["x1"]), 2),
            bottom=round(float(c["bottom"]), 2),
        )
        for c in page.chars
        if c["text"].strip() and float(c.get("size", size)) < cutoff
    ]


def footer_text(page: Any, band: float = FOOTER_BAND_PT) -> str:
    """The page's bottom band, **uncropped**: the printed page number lives in the margin."""
    if page.height <= band:
        return ""
    strip = page.crop((0, page.height - band, page.width, page.height))
    return strip.extract_text() or ""


def table_cells(table: Any) -> CellTable:
    """One pdfplumber table as `Cell`s: its extracted text joined to its geometry."""
    rows = table.extract()
    out: CellTable = []
    for row, geometry in zip(rows, table.rows, strict=False):
        cells: list[Cell | None] = []
        for index, bbox in enumerate(geometry.cells):
            value = row[index] if index < len(row) else None
            cells.append(None if bbox is None else Cell(value, *(float(v) for v in bbox)))
        out.append(cells)
    return out


def extract_page(page: Any, number: int, *, margin: float = SIDE_CROP_PT) -> Page:
    """Build a `Page` from a pdfplumber page (no caching)."""
    body = page.dedupe_chars(tolerance=1)
    rotated = is_rotated(page)
    size = median_font_size(page)
    cropped = crop_sides(body, margin)
    opts: dict[str, Any] = {"line_dir_rotated": "ltr", "char_dir_rotated": "btt"} if rotated else {}
    text = cropped.extract_text(**opts) or ""
    small = small_chars(body, size)
    found = [] if rotated else list(cropped.find_tables())
    tables: list[Table] = [list(t.extract()) for t in found]
    # Superscript markers glue onto cell text, so the cell view is read from the page
    # without them — but only where the crop actually holds a superscript, because a
    # second `find_tables` pass is the most expensive thing this module does (§8.2).
    if found and any(margin <= w.x0 <= page.width - margin for w in small):
        found = list(crop_sides(without_superscripts(body, size), margin).find_tables())
    cell_tables = [table_cells(t) for t in found]
    words = [_word(w) for w in cropped.extract_words(**opts)]
    # The fixed crop also cuts real content (§8.2 `tab_margins`). Where the tab-only
    # crop keeps more of the page, that wider view is extracted too and parsers that
    # want it read `full_tables`/`full_words`/`full_text`. Only table pages pay for it.
    wide_tables: list[Table] = []
    wide_words: list[Word] = []
    wide_text = ""
    left, right = tab_margins(body, margin)
    if tables and (left, right) != (margin, margin):
        wide = crop_page(body, left, right)
        wide_tables = [list(t.extract()) for t in wide.find_tables()]
        wide_words = [_word(w) for w in wide.extract_words(**opts)]
        wide_text = wide.extract_text(**opts) or ""
    return Page(
        number=number,
        text=text,
        tables=tables,
        rotated=rotated,
        width=float(page.width),
        height=float(page.height),
        font_size=size,
        words=words,
        cell_tables=cell_tables,
        small_chars=small,
        footer=footer_text(body),
        wide_tables=wide_tables,
        wide_words=wide_words,
        wide_text=wide_text,
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
