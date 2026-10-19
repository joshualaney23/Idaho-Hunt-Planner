"""T-12a spike: read the rotated elk A/B zone calendar pages of the Big Game brochure.

Run:
    .venv/bin/python docs/spike_elk_parser.py [path/to/big_game.pdf] [--dump ZONE]

Findings are written up in `docs/elk_parser_notes.md`; `planner/importer/elk.py`
(T-12b) is the production deliverable this spike informs. Nothing here is imported
by the app.

Model (see the notes for the why):
  * the zone pages are landscape artwork rotated 90°, so everything is read in a
    *rotated frame*: `rx` runs along a printed line, `ry` marches down the page.
  * each zone block is a real ruled grid: a tan month strip gives the column
    edges, the hairlines give the cell edges, the first (narrow) column carries
    the "A Tag" / "B Tag" labels.
  * every word of the table is assigned to exactly one region (preamble, month
    header, tag label, body cell, footnote) so nothing can be silently dropped.
"""

from __future__ import annotations

import collections
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

import pdfplumber

REPO = Path(__file__).resolve().parents[1]

DEFAULT_PDF = (
    REPO / "data/regs/2026/feb85a9c8741a72a1aeb40462cceaa391e8d1849df3604fdaa156a07d5494520.pdf"
)
UNITS_SEED = REPO / "static/data/units_seed.json"

ROT_TEXT = {"line_dir_rotated": "ltr", "char_dir_rotated": "btt"}
HEADER_FILL = (0.706, 0.727, 0.661)  # zone title band
MONTH_FILL = (0.815, 0.782, 0.66)  # month header strip and shaded tag rows

MONTHS = "Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec"
DATE_RE = re.compile(
    rf"\b(?:{MONTHS})[a-z]*\.?\s+\d{{1,2}}\s*[-–]\s*(?:{MONTHS})[a-z]*\.?\s+\d{{1,2}}\b"
)
ZONE_HEADER_RE = re.compile(r"^(?P<name>.+?)\s+Zone\s+\(Units?\s+(?P<units>[^)]*)\)(?P<tail>.*)$")
NO_TAG_RE = re.compile(r"No\s+([AB])\s+Tags?\s+in\s+this\s+Zone", re.I)
WEAPON_RE = re.compile(r"(Any weapon|Archery only|Muzzleloader only|Shotgun only)", re.I)
SEX_RE = re.compile(
    r"(antlered only|antlerless only|any elk|brow-tined bulls? only|spike or antlerless|"
    r"spike only|spike elk only|antlerless)",
    re.I,
)


# --------------------------------------------------------------------------- frame
@dataclass(frozen=True)
class Obj:
    """A word or a rule mapped into the rotated reading frame."""

    text: str
    rx0: float
    rx1: float
    ry0: float
    ry1: float

    @property
    def rxc(self) -> float:
        return (self.rx0 + self.rx1) / 2

    @property
    def ryc(self) -> float:
        return (self.ry0 + self.ry1) / 2


def to_rot(page, o, text: str = "") -> Obj:
    return Obj(text, page.height - o["bottom"], page.height - o["top"], o["x0"], o["x1"])


def rot_words(page) -> list[Obj]:
    # NOTE: never pass extra_attrs= here — on these pages per-character size jitter
    # then splits every single character into its own "word".
    return [to_rot(page, w, w["text"]) for w in page.extract_words(**ROT_TEXT)]


def printed_lines(words, tol: float = 2.0) -> list[tuple[float, list[Obj]]]:
    out: list[tuple[float, list[Obj]]] = []
    for w in sorted(words, key=lambda w: (w.ryc, w.rx0)):
        if out and abs(w.ryc - out[-1][0]) <= tol:
            out[-1][1].append(w)
        else:
            out.append((w.ryc, [w]))
    return [(ry, sorted(ws, key=lambda w: w.rx0)) for ry, ws in out]


def text_of(ws) -> str:
    return " ".join(w.text for w in sorted(ws, key=lambda w: w.rx0))


def fill_of(o) -> tuple | None:
    c = o.get("non_stroking_color")
    return tuple(round(v, 3) for v in c) if isinstance(c, (list, tuple)) else None


# --------------------------------------------------------------------------- model
@dataclass
class Cell:
    col: int
    month: str
    ry0: float
    ry1: float
    tag: str = "?"
    words: list[Obj] = field(default_factory=list)

    @property
    def lines(self) -> list[str]:
        return [text_of(ws) for _, ws in printed_lines(self.words)]

    @property
    def text(self) -> str:
        return " | ".join(self.lines)


@dataclass
class Block:
    page: int
    zone: str
    header_units: str
    tail: str
    ry0: float
    ry1: float
    rx0: float = 0.0
    rx1: float = 0.0
    columns: list[tuple[float, float]] = field(default_factory=list)
    months: list[str] = field(default_factory=list)
    cells: list[Cell] = field(default_factory=list)
    tag_labels: list[tuple[str, float]] = field(default_factory=list)
    preamble: list[str] = field(default_factory=list)
    footnotes: list[str] = field(default_factory=list)
    no_tags: set[str] = field(default_factory=set)
    unassigned: list[Obj] = field(default_factory=list)

    @property
    def controlled_only(self) -> bool:
        return "Controlled Hunts Only" in self.tail


@dataclass
class Row:
    """One season window as printed: zone x tag x weapon/sex x date range."""

    page: int
    zone: str
    tag: str
    month_col: str
    weapon: str
    sex: str
    dates: str
    qualifier: str
    cell_text: str


# --------------------------------------------------------------------------- parse
def is_zone_page(page) -> bool:
    chars = page.chars
    if not chars:
        return False
    rot = sum(1 for c in chars if not c.get("upright", True)) / len(chars)
    return rot >= 0.5 and "Zone" in (page.extract_text(**ROT_TEXT) or "")


def zone_blocks(page, pageno: int) -> list[Block]:
    words = rot_words(page)
    month_rects = [to_rot(page, r) for r in page.rects if fill_of(r) == MONTH_FILL]
    head_rects = [to_rot(page, r) for r in page.rects if fill_of(r) == HEADER_FILL]
    if not month_rects and not head_rects:
        return []
    table_rx0 = min(r.rx0 for r in month_rects + head_rects)
    table_rx1 = max(r.rx1 for r in month_rects + head_rects)
    # the vertical "Idaho Big Game … idfg.idaho.gov" strap lives left of the table
    body = [w for w in words if table_rx0 - 2 <= w.rxc <= table_rx1 + 2]
    rules = [to_rot(page, ln) for ln in page.lines if (ln["x1"] - ln["x0"]) < 2]

    blocks: list[Block] = []
    for ry, ws in printed_lines(body):
        m = ZONE_HEADER_RE.match(text_of(ws))
        if m and len(ws) >= 3:
            blocks.append(
                Block(
                    page=pageno,
                    zone=m.group("name").strip(),
                    header_units=m.group("units").strip(),
                    tail=m.group("tail").strip(),
                    ry0=ry - 6,
                    ry1=max(w.ry1 for w in body) + 1,
                    rx0=table_rx0,
                    rx1=table_rx1,
                )
            )
    for a, b in zip(blocks, blocks[1:]):
        a.ry1 = b.ry0

    for blk in blocks:
        _fill_block(blk, body, month_rects, rules)
    return blocks


def _fill_block(blk: Block, body, month_rects, rules) -> None:
    mine = [w for w in body if blk.ry0 <= w.ryc < blk.ry1]
    strip = [r for r in month_rects if blk.ry0 <= r.ryc < blk.ry1]
    if not strip:
        # a zone with no calendar at all (every hunt is controlled): just prose
        blk.preamble = [t for _, t in _lines(mine)]
        for t in blk.preamble:
            m = NO_TAG_RE.search(t)
            if m:
                blk.no_tags.add(m.group(1).upper())
        blk.unassigned = [w for w in mine if DATE_RE.search(w.text)]
        return
    top = min(r.ry0 for r in strip)
    header = [r for r in strip if abs(r.ry0 - top) < 2]
    cols = sorted({(round(r.rx0, 1), round(r.rx1, 1)) for r in header})
    body_top = max(r.ry1 for r in header)
    blk.columns = cols
    lab0, lab1 = cols[0]

    taken: set[int] = set()

    def claim(ws):
        taken.update(id(w) for w in ws)

    # zone header line + preamble (between the header line and the month strip)
    pre = [w for w in mine if w.ryc < top]
    blk.preamble = [t for _, t in _lines(pre)][1:]  # drop the zone header line itself
    claim(pre)

    # month header row
    for (c0, c1), _ in zip(cols, cols):
        hdr = [w for w in mine if top <= w.ryc <= body_top and c0 <= w.rxc <= c1]
        claim(hdr)
        blk.months.append(text_of(hdr))

    # tag labels and footnote markers live in the narrow first column
    labs = [w for w in mine if w.ryc > body_top and w.rxc <= lab1]
    notes_start = blk.ry1
    for ry, ws in printed_lines(labs):
        t = text_of(ws)
        if re.fullmatch(r"([AB])\s*Tag", t):
            blk.tag_labels.append((t[0], ry))
            claim(ws)
        elif re.fullmatch(r"Note\s+[A-Z0-9]+", t) or t.startswith("ELK"):
            # the footnote legend of this zone block starts here
            notes_start = min(notes_start, min(w.ry0 for w in ws) - 2)
            claim(ws)
            blk.footnotes.append(t)

    # Row bands: a band edge is an ry where the hairlines cover most of the table
    # width. The month columns are NOT cell edges — an entry is routinely centred
    # across two of them ("Oct 10 - Nov 3" straddles October|November), so cutting
    # on a column edge splits a date token in half and loses the season.
    body_rx0, body_rx1 = cols[1][0], cols[-1][1]
    width = body_rx1 - body_rx0
    cover: dict[float, float] = collections.defaultdict(float)
    for r in rules:
        if body_top - 2 <= r.ry0 < notes_start and r.rx1 > body_rx0 and r.rx0 < body_rx1:
            cover[round(r.ry0, 1)] += min(r.rx1, body_rx1) - max(r.rx0, body_rx0)
    edges = sorted({round(body_top, 1), round(notes_start, 1)}
                   | {y for y, c in cover.items() if c >= 0.6 * width})

    for y0, y1 in zip(edges, edges[1:]):
        band = [w for w in mine if y0 < w.ryc < y1 and body_rx0 - 2 <= w.rxc <= body_rx1 + 2]
        if not band:
            continue
        claim(band)
        for entry in _entries(band):
            cell = Cell(_col_of(entry, cols), _months_of(entry, cols, blk.months), y0, y1)
            cell.words = entry
            blk.cells.append(cell)

    tail = [w for w in mine if w.ryc >= notes_start and w.rxc > cols[0][1]]
    if tail:
        claim(tail)
        blk.footnotes += [t for _, t in _lines(tail)]

    for cell in blk.cells:
        if blk.tag_labels:
            cell.tag = min(blk.tag_labels, key=lambda t: abs(t[1] - (cell.ry0 + cell.ry1) / 2))[0]

    # "No B Tags in this Zone — See Controlled Hunts" is a statement, not a season
    for cell in list(blk.cells):
        m = NO_TAG_RE.search(cell.text)
        if m:
            blk.no_tags.add(m.group(1).upper())
            blk.cells.remove(cell)

    rest = [w for w in mine if id(w) not in taken]
    for ry, ws in printed_lines(rest):
        t = text_of(ws)
        m = NO_TAG_RE.search(t)
        if m:
            blk.no_tags.add(m.group(1).upper())
            claim(ws)
    blk.unassigned = [w for w in mine if id(w) not in taken]
    blk.footnotes += [t for _, t in _lines([w for w in blk.unassigned if w.rxc > cols[0][1]])]


def _lines(ws) -> list[tuple[float, str]]:
    return [(ry, text_of(g)) for ry, g in printed_lines(ws)]


ENTRY_GAP = 9.0  # pt of white space that separates two entries printed side by side


def _entries(band: list[Obj]) -> list[list[Obj]]:
    """Split one row band into the entries printed side by side inside it.

    Lines are first cut at horizontal gaps, then the segments are clustered by
    horizontal overlap so a label, its date range and its qualifier lines stay
    together even when they have different widths.
    """
    segments: list[list[Obj]] = []
    for _, ws in printed_lines(band):
        cur: list[Obj] = []
        for w in ws:
            if cur and w.rx0 - cur[-1].rx1 > ENTRY_GAP:
                segments.append(cur)
                cur = []
            cur.append(w)
        if cur:
            segments.append(cur)

    clusters: list[list[list[Obj]]] = []
    for seg in sorted(segments, key=lambda s: s[0].rx0):
        s0, s1 = seg[0].rx0, seg[-1].rx1
        hit = None
        for cl in clusters:
            c0 = min(s[0].rx0 for s in cl)
            c1 = max(s[-1].rx1 for s in cl)
            overlap = min(s1, c1) - max(s0, c0)
            if overlap > 0.4 * min(s1 - s0, c1 - c0):
                hit = cl
                break
        if hit is None:
            clusters.append([seg])
        else:
            hit.append(seg)
    return [sorted([w for s in cl for w in s], key=lambda w: (w.ryc, w.rx0)) for cl in clusters]


def _col_of(entry: list[Obj], cols) -> int:
    c = sum(w.rxc for w in entry) / len(entry)
    for i, (c0, c1) in enumerate(cols):
        if c0 <= c <= c1:
            return i
    return -1


def _months_of(entry: list[Obj], cols, months: list[str]) -> str:
    lo, hi = min(w.rx0 for w in entry), max(w.rx1 for w in entry)
    hit = [months[i] for i, (c0, c1) in enumerate(cols) if c1 > lo and c0 < hi and months[i]]
    return "/".join(hit)


def rows_of(blk: Block) -> list[Row]:
    """One row per printed date range, with the weapon/sex label that governs it."""
    out: list[Row] = []
    for cell in sorted(blk.cells, key=lambda c: (c.ry0, c.col)):
        weapon = sex = ""
        pending: list[str] = []
        for line in cell.lines:
            wm = WEAPON_RE.search(line)
            if wm:
                weapon = wm.group(1)
                sm = SEX_RE.search(line)
                if sm:
                    sex = sm.group(1)
                pending = []
                continue
            sm = SEX_RE.search(line)
            dates = DATE_RE.findall(line)
            ranges = DATE_RE.finditer(line)
            if not dates:
                if sm and not DATE_RE.search(line):
                    sex = sm.group(1)
                pending.append(line)
                continue
            for m in ranges:
                head = line[: m.start()].strip(" :,-")
                tail = line[m.end() :].strip(" :,-")
                qual = " ".join(x for x in (*pending, head, tail) if x).strip()
                line_sex = SEX_RE.search(head)
                out.append(
                    Row(
                        page=blk.page,
                        zone=blk.zone,
                        tag=cell.tag,
                        month_col=cell.month,
                        weapon=weapon,
                        sex=(line_sex.group(1) if line_sex else sex),
                        dates=m.group(0),
                        qualifier=qual,
                        cell_text=cell.text,
                    )
                )
            pending = []
    return out


# --------------------------------------------------------------------------- report
def zone_index(pdf) -> dict[str, str]:
    """`ELK MANAGEMENT ZONES` contents page: zone -> printed unit list."""
    out: dict[str, str] = {}
    for page in pdf.pages:
        txt = page.extract_text(**ROT_TEXT) or ""
        if "ELK MANAGEMENT ZONES" not in txt:
            continue
        for line in txt.split("\n"):
            m = re.match(r"^([A-Za-z .\-–]+?)\s*\.{2,}\s*([0-9A-Za-z, ]+?)\s*\.{2,}\s*\d+$", line)
            if m:
                out[m.group(1).strip()] = m.group(2).strip()
    return out


def gis_zones() -> dict[str, set[str]]:
    import json

    seed = json.loads(UNITS_SEED.read_text())
    units = seed["units"] if isinstance(seed, dict) else seed
    out: dict[str, set[str]] = collections.defaultdict(set)
    for u in units:
        if u.get("elk_zone"):
            out[u["elk_zone"]].add(u["id"])
    return dict(out)


def norm_units(text: str) -> set[str]:
    return {t.strip() for t in re.split(r"[,\s]+", text) if t.strip()}


def norm_zone(name: str) -> str:
    return re.sub(r"[^a-z]", "", name.lower())


def main(argv: list[str]) -> int:
    args = [a for a in argv if not a.startswith("--")]
    dump = next((a.split("=", 1)[1] for a in argv if a.startswith("--dump=")), None)
    path = Path(args[0]) if args else DEFAULT_PDF

    blocks: list[Block] = []
    rows: list[Row] = []
    page_tokens: dict[int, list[str]] = {}
    with pdfplumber.open(path) as pdf:
        index = zone_index(pdf)
        for n, page in enumerate(pdf.pages, start=1):
            if not is_zone_page(page):
                continue
            blks = zone_blocks(page, n)
            blocks += blks
            for b in blks:
                rows += rows_of(b)
            table_words = [w for b in blks for w in _block_words(b, page)]
            page_tokens[n] = [m for _, t in _lines(table_words) for m in DATE_RE.findall(t)]

    print(f"== elk zone pages: {sorted({b.page for b in blocks})}")
    print(f"== zone blocks: {len(blocks)}   index page zones: {len(index)}   rows: {len(rows)}\n")

    hdr = f"{'page':>4} {'zone':<16} {'A':>3} {'B':>3} {'rows':>4}  notes"
    print(hdr)
    print("-" * len(hdr))
    for b in blocks:
        rs = rows_of(b)
        a = sum(1 for r in rs if r.tag == "A")
        bb = sum(1 for r in rs if r.tag == "B")
        notes = []
        if b.controlled_only:
            notes.append("controlled-hunts-only")
        if b.no_tags:
            notes.append("no " + "/".join(sorted(b.no_tags)) + " tag")
        if b.unassigned:
            notes.append(f"UNASSIGNED {len(b.unassigned)} words")
        print(f"{b.page:>4} {b.zone:<16} {a:>3} {bb:>3} {len(rs):>4}  {', '.join(notes)}")

    # ---- checks
    print("\n== C1 zone set (header vs ELK MANAGEMENT ZONES index)")
    hz = {norm_zone(b.zone): b.zone for b in blocks}
    iz = {norm_zone(k): k for k in index}
    print("   blocks:", len(hz), " index:", len(iz))
    print("   only in index :", [iz[k] for k in sorted(set(iz) - set(hz))])
    print("   only in blocks:", [hz[k] for k in sorted(set(hz) - set(iz))])

    print("\n== C2 zone -> units: page header vs index page vs GIS elk_zone")
    gis = {norm_zone(k): v for k, v in gis_zones().items()}
    bad = 0
    for b in blocks:
        k = norm_zone(b.zone)
        head = norm_units(b.header_units)
        idx = norm_units(index.get(iz.get(k, ""), ""))
        g = gis.get(k, set())
        if head != idx or head != g:
            bad += 1
            print(
                f"   {b.zone:<16} header={sorted(head)}\n"
                f"   {'':<16} index ={sorted(idx)}\n"
                f"   {'':<16} gis   ={sorted(g)}"
            )
    print(f"   zones where all three agree: {len(blocks) - bad}/{len(blocks)}")

    print("\n== C3 date-token accounting (tokens printed in the zone tables)")
    consumed = collections.Counter((r.page, r.dates) for r in rows)
    total_printed = total_used = 0
    for n, toks in sorted(page_tokens.items()):
        printed = collections.Counter((n, t) for t in toks)
        used = collections.Counter({k: v for k, v in consumed.items() if k[0] == n})
        total_printed += sum(printed.values())
        total_used += sum(used.values())
        missing = printed - used
        extra = used - printed
        flag = "" if not missing and not extra else "  <-- "
        if missing:
            flag += f"unconsumed={[t for (_, t) in missing.elements()]}"
        if extra:
            flag += f" extra={[t for (_, t) in extra.elements()]}"
        print(f"   p{n:>3} printed={sum(printed.values()):>3} rows={sum(used.values()):>3}{flag}")
    print(f"   total printed={total_printed} consumed={total_used}")

    print("\n== C4 tag coverage")
    for b in blocks:
        rs = rows_of(b)
        have = {r.tag for r in rs}
        missing = {"A", "B"} - have - b.no_tags
        if missing and not b.controlled_only:
            print(f"   {b.zone:<16} no rows for tag {sorted(missing)} and no 'No X Tags' line")

    print("\n== C5 unassigned words (nothing may be silently dropped)")
    for b in blocks:
        if b.unassigned:
            print(f"   {b.zone:<16} p{b.page}: {[w.text for w in b.unassigned][:25]}")

    print("\n== preambles / footnotes that carry a date token (not seasons)")
    for b in blocks:
        for t in b.preamble + b.footnotes:
            if DATE_RE.search(t):
                print(f"   {b.zone:<16} {t[:120]}")

    if dump:
        print(f"\n== dump: {dump}")
        for b in blocks:
            if norm_zone(b.zone) != norm_zone(dump):
                continue
            print(f"   page {b.page}  units({b.header_units})  months={b.months}")
            for t in b.preamble:
                print(f"   preamble: {t}")
            for r in rows_of(b):
                print(
                    f"   {r.tag} tag | {r.month_col:<17} | {r.weapon:<17} | {r.sex:<22} | "
                    f"{r.dates:<18} | {r.qualifier}"
                )
            for t in b.footnotes:
                print(f"   footnote: {t}")
    return 0


def _block_words(b: Block, page) -> list[Obj]:
    return [
        w
        for w in rot_words(page)
        if b.ry0 <= w.ryc < b.ry1 and b.rx0 - 2 <= w.rxc <= b.rx1 + 2
    ]


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
