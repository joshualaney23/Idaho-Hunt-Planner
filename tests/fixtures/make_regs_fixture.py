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


def upland_bird_pages() -> list[list[str]]:
    """The bird pages of the Upland brochure, with the traps the real one prints (T-14).

    Page 2 reproduces the forest grouse block (a county list that wraps mid-clause, an
    `except … units` clause, a `Remainder of the state` sibling and an 8 pt caption
    printed to the right of the bag-limit line). Page 3 reproduces the sharp-tailed
    page, whose county **map** is printed over the season column: 2.7 pt county labels
    on the body's own baselines, a 24 pt area number beside the `Seasons` heading, and
    a `Possession Limit` whose dotted leader is pushed onto its own line.
    """
    cover = [
        text_op(90, 650, 22, "Idaho Upland Game,"),
        text_op(90, 620, 22, "Turkey & Furbearer"),
        text_op(90, 592, 16, "2026 & 2027 Seasons & Rules"),
        text_op(90, 566, 11, "Idaho Department of Fish and Game"),
    ]
    grouse = [
        text_op(18, 300, 11, "Upland Game Bird Seasons", rotated=True),  # the side tab
        text_op(90, 740, 14, "FOREST GROUSE: DUSKY (BLUE), RUFFED, AND SPRUCE"),
        text_op(40, 700, 11, "AREA 1"),
        text_op(40, 684, 10, "Benewah, Bonner, Boundary, Clearwater, Kootenai, Latah,"),
        text_op(40, 672, 10, "Lewis, Nez Perce, and Shoshone counties, and Adams and"),
        text_op(40, 660, 10, "Idaho counties, except those portions in game management"),
        text_op(40, 648, 10, "units 19A, 20A, 22, 23, and 26."),
        text_op(40, 628, 11, "Seasons"),
        text_op(40, 612, 10, "2026 - 2027: August 30, 2026 through January 31, 2027"),
        text_op(40, 596, 10, "2027 - 2028: August 30, 2027 through January 31, 2028"),
        text_op(40, 580, 10, "Daily Bag Limit ......................4 in the aggregate"),
        text_op(432, 580, 8, "Blue grouse are now known"),  # caption, not part of the limit
        text_op(40, 564, 10, "Possession Limit ..................12 in the aggregate"),
        text_op(40, 528, 11, "AREA 2"),
        text_op(40, 512, 10, "Remainder of the state."),
        text_op(40, 492, 11, "Seasons"),
        text_op(40, 476, 10, "2026: August 30, 2026 through December 31, 2026"),
        text_op(40, 460, 10, "2027: August 30, 2027 through December 31, 2027"),
        text_op(40, 444, 10, "Daily Bag Limit ......................4 in the aggregate"),
        text_op(40, 428, 10, "Possession Limit ..................12 in the aggregate"),
        text_op(40, 400, 10, "Distribution and Habitat Use: Shaded areas show general"),
        text_op(40, 388, 10, "distribution of these species. Ruffed grouse are common."),
        text_op(300, 60, 8, "6"),
    ]
    sharptail = [
        text_op(18, 300, 11, "Upland Game Bird Seasons", rotated=True),
        text_op(90, 740, 14, "SHARP-TAILED GROUSE"),
        text_op(40, 700, 11, "AREA 1"),
        text_op(350, 700, 2.7, "BOUNDARY"),
        text_op(40, 684, 10, "Bingham, Bonneville, Clark, and Jefferson counties east of"),
        text_op(350, 684, 2.7, "BONNER"),
        text_op(40, 672, 10, "Interstate 15, Power County south of Interstate 86, Bannock,"),
        text_op(40, 660, 10, "Bear Lake, Caribou, Franklin, Fremont, Madison, Oneida,"),
        text_op(360, 660, 2.7, "BENEWAH"),
        text_op(40, 648, 10, "and Teton counties."),
        text_op(40, 628, 11, "Seasons"),
        text_op(360, 628, 24, "2"),  # the map's area number, printed beside the heading
        text_op(40, 612, 10, "2026: October 1, 2026 through November 8, 2026"),
        text_op(40, 596, 10, "2027: October 1, 2027 through November 8, 2027"),
        text_op(40, 580, 10, "Daily Bag Limit ..........................................2"),
        text_op(330, 580, 24, "CLOSED"),
        text_op(160, 565, 10, "....................................6"),  # orphaned leader
        text_op(40, 564, 10, "Possession Limit"),
        text_op(380, 564, 2.7, "WASHINGTON"),
        text_op(40, 528, 11, "AREA 2"),
        text_op(40, 512, 10, "Remainder of the state: CLOSED."),
        text_op(360, 512, 2.7, "ONEIDA FRANKLIN"),
        text_op(40, 484, 10, "Distribution and Habitat Use: Shaded areas show general"),
        text_op(300, 60, 8, "10"),
    ]
    sage = [
        text_op(90, 740, 14, "SAGE-GROUSE"),
        text_op(40, 700, 12, "SAGE-GROUSE SEASONS WILL BE SET IN JULY"),
        text_op(40, 684, 10, "A separate brochure will be available at idfg.idaho.gov after"),
        text_op(40, 672, 10, "seasons have been set in July. Tags go on sale August 3, 2026."),
        text_op(300, 60, 8, "9"),
    ]
    chukar = [
        text_op(90, 740, 14, "CHUKAR AND GRAY PARTRIDGE"),
        text_op(40, 716, 12, "ENTIRE STATE OPEN"),
        text_op(40, 700, 11, "STATEWIDE"),
        text_op(40, 680, 11, "Seasons"),
        text_op(40, 664, 10, "2026 - 2027: September 15, 2026 through January 31, 2027"),
        text_op(40, 648, 10, "Daily Bag Limit ...........8 Chukar & 8 Gray Partridge"),
        text_op(40, 632, 10, "Possession Limit .......24 Chukar & 24 Gray Partridge"),
        text_op(40, 604, 10, "Distribution and Habitat Use: Shaded areas show general"),
        text_op(300, 60, 8, "8"),
    ]
    pheasant = [
        text_op(90, 740, 14, "PHEASANTS - ALL VARIETIES"),
        text_op(40, 716, 12, "NO SEASON ON HEN (FEMALE) PHEASANTS"),
        text_op(40, 700, 11, "STATEWIDE"),
        text_op(40, 680, 11, "Seasons"),
        text_op(40, 664, 10, "2026: Resident: October 15, 2026 through December 31, 2026"),
        text_op(40, 648, 10, "Nonresident: October 20, 2026 through December 31, 2026"),
        text_op(40, 632, 10, "Daily Bag Limit .........................3 roosters*"),
        text_op(40, 616, 10, "Possession Limit .....................9 roosters"),
        text_op(40, 600, 10, "*Daily bag limit: 3 roosters, only 2 of which can be harvested"),
        text_op(40, 588, 10, "on Upland Game Bird Permit Release Areas."),
        text_op(40, 568, 11, "Youth Hunt Season"),
        text_op(40, 552, 10, "2026 - October 8, 2026 through October 14, 2026"),
        text_op(40, 524, 10, "Distribution and Habitat Use: Shaded areas show general"),
        text_op(300, 60, 8, "11"),
    ]
    return [cover, grouse, chukar, sage, sharptail, pheasant]


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
        ("upland_birds_sample.pdf", upland_bird_pages()),
        ("supplemental_sample.pdf", supplemental_pages()),
    ):
        path = FIXTURES / name
        path.write_bytes(pdf_bytes(pages))
        print(f"{path.name}: {path.stat().st_size} bytes")


if __name__ == "__main__":
    main()
