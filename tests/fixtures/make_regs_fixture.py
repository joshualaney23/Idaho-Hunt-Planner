"""Build the committed regulation-PDF fixtures (tests/fixtures/*.pdf).

Hand-rolled minimal PDFs: no third-party writer is a project dependency, and the
fixtures must stay tiny and byte-stable so the committed sha256 doesn't drift.
Run: python tests/fixtures/make_regs_fixture.py
"""

from __future__ import annotations

from pathlib import Path

FIXTURES = Path(__file__).resolve().parent
PAGE_W, PAGE_H = 612.0, 792.0


def esc(text: str) -> str:
    return text.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")


def text_op(x: float, y: float, size: float, s: str, *, rotated: bool = False) -> str:
    m = "0 1 -1 0" if rotated else "1 0 0 1"
    return f"BT /F1 {size:g} Tf {m} {x:g} {y:g} Tm ({esc(s)}) Tj ET"


def line_op(x0: float, y0: float, x1: float, y1: float) -> str:
    return f"0.6 w {x0:g} {y0:g} m {x1:g} {y1:g} l S"


def grid(x0: float, y_top: float, widths: list[float], row_h: float, rows: int) -> list[str]:
    xs, x = [x0], x0
    for w in widths:
        x += w
        xs.append(x)
    ys = [y_top - i * row_h for i in range(rows + 1)]
    ops = [line_op(xs[0], y, xs[-1], y) for y in ys]
    ops += [line_op(x, ys[0], x, ys[-1]) for x in xs]
    return ops


def table(
    x0: float,
    y_top: float,
    widths: list[float],
    row_h: float,
    rows: list[list[str]],
    size: float = 9,
) -> list[str]:
    ops = grid(x0, y_top, widths, row_h, len(rows))
    for r, cells in enumerate(rows):
        x = x0
        for c, cell in enumerate(cells):
            ops.append(text_op(x + 3, y_top - r * row_h - row_h + 4, size, cell))
            x += widths[c]
    return ops


def pdf_bytes(pages: list[list[str]]) -> bytes:
    objs: list[bytes] = []

    def add(body: bytes) -> int:
        objs.append(body)
        return len(objs)

    font = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    pages_id = len(objs) + 1 + 2 * len(pages)  # pages tree comes after page+content objects
    kids = []
    for ops in pages:
        stream = "\n".join(ops).encode("latin-1")
        content = add(b"<< /Length %d >>\nstream\n%s\nendstream" % (len(stream), stream))
        page = add(
            b"<< /Type /Page /Parent %d 0 R /MediaBox [0 0 %g %g] "
            b"/Resources << /Font << /F1 %d 0 R >> >> /Contents %d 0 R >>"
            % (pages_id, PAGE_W, PAGE_H, font, content)
        )
        kids.append(page)
    tree = add(
        b"<< /Type /Pages /Kids [%s] /Count %d >>"
        % (b" ".join(b"%d 0 R" % k for k in kids), len(kids))
    )
    assert tree == pages_id, (tree, pages_id)
    root = add(b"<< /Type /Catalog /Pages %d 0 R >>" % tree)

    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objs, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    for off in offsets:
        out += b"%010d 00000 n \n" % off
    out += b"trailer\n<< /Size %d /Root %d 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objs) + 1,
        root,
        xref,
    )
    return bytes(out)


def big_game_pages() -> list[list[str]]:
    cover = [
        text_op(120, 650, 24, "2026 IDAHO"),
        text_op(120, 615, 22, "Big Game Seasons & Rules"),
        text_op(120, 585, 11, "Idaho Department of Fish and Game"),
        text_op(120, 560, 9, "Valid July 1, 2026 through June 30, 2027"),
        text_op(300, 60, 8, "1"),
    ]
    rows = [
        ["Unit(s)", "Antlered", "Antlerless"],
        ["1, 2, 3, 4A", "Oct 10 - Oct 24", "None"],
        ["16A, 17, 19, and 20", "Oct 10 - Nov 3", "Nov 15 - Jan 15 (White-tailed deer only)"],
        ["21-21A, 28", "Aug 30 - Sep 30", "Dec 25 - Dec 31 (Youth hunt only)"],
    ]
    table_page = [
        text_op(10, 400, 11, "DEER / REGULAR", rotated=True),  # side tab, removed by the 40 pt crop
        text_op(60, 700, 11, "2026 Regular Deer Tag / General Any Weapon Seasons"),
        *table(60, 680, [150, 120, 240], 22, rows),
        text_op(300, 60, 8, "12"),
    ]
    rotated_page = [
        text_op(300, 120, 14, "2026 Elk Zone Seasons", rotated=True),
        text_op(330, 120, 10, "Panhandle Zone  Oct 10 - Oct 24", rotated=True),
        text_op(350, 120, 10, "Sawtooth Zone  Oct 15 - Oct 24", rotated=True),
        text_op(370, 120, 10, "Archery Only  Aug 30 - Sep 30", rotated=True),
        text_op(300, 60, 8, "35"),
    ]
    return [cover, table_page, rotated_page]


def upland_pages() -> list[list[str]]:
    cover = [
        text_op(100, 650, 22, "2026-2027 Upland Game, Furbearer and Turkey"),
        text_op(100, 620, 18, "Seasons & Rules"),
        text_op(100, 590, 11, "Idaho Department of Fish and Game"),
    ]
    body = [
        text_op(60, 700, 11, "2026 Wild Turkey General Seasons"),
        *table(
            60, 680, [150, 180], 22, [["Unit(s)", "Season Dates"], ["1, 2, 3", "Apr 15 - May 25"]]
        ),
    ]
    return [cover, body]


def supplemental_pages() -> list[list[str]]:
    return [
        [
            text_op(100, 650, 20, "2026 Supplemental Proclamation"),
            text_op(100, 620, 12, "Big Game Seasons & Rules corrections"),
            text_op(100, 595, 10, "Idaho Department of Fish and Game"),
        ]
    ]


def main() -> None:
    for name, pages in (
        ("big_game_sample.pdf", big_game_pages()),
        ("upland_sample.pdf", upland_pages()),
        ("supplemental_sample.pdf", supplemental_pages()),
    ):
        path = FIXTURES / name
        path.write_bytes(pdf_bytes(pages))
        print(f"{path.name}: {path.stat().st_size} bytes")


if __name__ == "__main__":
    main()
