# 2026 golden data — verification log

Every row of every section below is verified **by hand against the source PDF**, one
page at a time (DESIGN.md §8.7). The parser's output is not evidence of itself: sign a
page only after reading it on the page image, and write down every correction you made.

Page images are rendered by `scripts/make_verification_checklist.py` into
`data/verify/2026/<section>/` and are never committed.

<!-- section: controlled_deer -->
## `controlled_deer`

Source: `tests/fixtures/bg2026_controlled.pdf` · **81 rows** on 3 pages · images: `data/verify/2026/controlled_deer/`

| PDF page | Rows | Verified by | Date | Corrections made |
|---|---:|---|---|---|
| p001 | 27 | | | |
| p002 | 31 | | | |
| p003 | 23 | | | |
| **total** | **81** | | | |

<!-- /section: controlled_deer -->

<!-- section: deer_general -->
## `deer_general`

Source: `data/pdf_cache/da4260b617b679db.pdf` · **235 rows** on 9 pages · images: `data/verify/2026/deer_general/`

| PDF page | Rows | Verified by | Date | Corrections made |
|---|---:|---|---|---|
| p012 | 13 | josh | 2026-10-16 | none |
| p013 | 33 | josh | 2026-10-16 | none |
| p014 | 9 | josh | 2026-10-16 | none |
| p015 | 44 | josh | 2026-10-16 | none |
| p016 | 12 | josh | 2026-10-16 | none |
| p018 | 24 | josh | 2026-10-16 | none |
| p019 | 39 | josh | 2026-10-16 | none |
| p020 | 42 | josh | 2026-10-16 | none |
| p021 | 19 | josh | 2026-10-16 | none |
| **total** | **235** | josh | 2026-10-16 | **none — 0 corrections** |

<!-- /section: deer_general -->

<!-- section: elk_zones -->
## `elk_zones`

Source: `data/regs/2026/feb85a9c8741a72a1aeb40462cceaa391e8d1849df3604fdaa156a07d5494520.pdf` · **99 rows** on 13 pages · images: `data/verify/2026/elk_zones/`

| PDF page | Rows | Verified by | Date | Corrections made |
|---|---:|---|---|---|
| p038 | 15 | josh | 2026-10-19 | none |
| p039 | 5 | josh | 2026-10-19 | none |
| p040 | 6 | josh | 2026-10-19 | none |
| p041 | 10 | josh | 2026-10-19 | none |
| p042 | 11 | josh | 2026-10-19 | none |
| p043 | 8 | josh | 2026-10-19 | none |
| p044 | 5 | josh | 2026-10-19 | none |
| p045 | 5 | josh | 2026-10-19 | none |
| p046 | 6 | josh | 2026-10-19 | none |
| p047 | 7 | josh | 2026-10-19 | none |
| p048 | 6 | josh | 2026-10-19 | none |
| p049 | 12 | josh | 2026-10-19 | 1 row restored: Bear River B-tag archery window (Aug 30 – Sep 14), see PR #106 |
| p050 | 3 | josh | 2026-10-19 | none |
| **total** | **99** | josh | 2026-10-19 | 1 row restored (p049) |

<!-- /section: elk_zones -->
