"""T-10: `planner/importer/extract.py` + `classify.py` against the fixture PDFs (§8.1–8.2).

The fixtures are built by `tests/fixtures/make_regs_fixture.py` and mimic the brochures'
awkward bits: a vertical side tab that the 40 pt crop must remove, a ruled season table,
and a sideways calendar page.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from planner.importer import classify as classify_mod
from planner.importer import extract

FIXTURES = Path(__file__).resolve().parent / "fixtures"
BIG_GAME = FIXTURES / "big_game_sample.pdf"
UPLAND = FIXTURES / "upland_sample.pdf"
SUPPLEMENTAL = FIXTURES / "supplemental_sample.pdf"


@pytest.fixture(scope="module")
def pages() -> list[extract.Page]:
    return list(extract.iter_pages(BIG_GAME))


def test_iter_pages_yields_every_page(pages: list[extract.Page]) -> None:
    assert [p.number for p in pages] == [1, 2, 3]
    assert extract.page_count(BIG_GAME) == 3
    assert all(p.width > 0 and p.height > 0 for p in pages)


def test_side_crop_removes_the_vertical_section_tab(pages: list[extract.Page]) -> None:
    """Without the 40 pt crop the tab is read as reversed text and hides the table (§8.2)."""
    table_page = pages[1]
    assert "RALUGER" not in table_page.text  # "DEER / REGULAR" printed sideways at x≈10
    assert "2026 Regular Deer Tag / General Any Weapon Seasons" in table_page.text
    uncropped = extract.iter_pages(BIG_GAME, pages=[2], margin=0)
    assert "RALUGER" in next(iter(uncropped)).text


def test_table_path_reads_the_season_table(pages: list[extract.Page]) -> None:
    (table,) = pages[1].tables
    assert table[0] == ["Unit(s)", "Antlered", "Antlerless"]
    assert table[1] == ["1, 2, 3, 4A", "Oct 10 - Oct 24", "None"]
    assert table[2][2] == "Nov 15 - Jan 15 (White-tailed deer only)"
    assert len(table) == 4


def test_rotation_detection(pages: list[extract.Page]) -> None:
    assert [p.rotated for p in pages] == [False, False, True]
    # the rotated page is read with the rotated-text options, not as 'rebmetpeS'
    assert "Panhandle" in pages[2].text
    assert pages[2].tables == []  # rotated layouts go to T-12's handler, not find_tables


def test_median_font_size_and_superscript_filter() -> None:
    import pdfplumber

    with pdfplumber.open(str(BIG_GAME)) as pdf:
        page = pdf.pages[1]
        assert extract.median_font_size(page) == pytest.approx(9.0)
        assert extract.rotated_share(page) < extract.ROTATED_SHARE
        filtered = extract.without_superscripts(page)
        assert len(filtered.chars) <= len(page.chars)


def test_cache_round_trip_and_reuse(tmp_path: Path) -> None:
    cache = tmp_path / "cache" / "abc123"
    first = list(extract.iter_pages(BIG_GAME, cache_dir=cache))
    files = sorted(p.name for p in cache.iterdir())
    assert files == ["p1.json", "p2.json", "p3.json"]

    # a cached page is returned without touching the PDF at all
    second = list(extract.iter_pages(tmp_path / "gone.pdf", cache_dir=cache, pages=[1, 2, 3]))
    assert [p.to_json() for p in second] == [p.to_json() for p in first]

    # a cache written by an older extractor version is ignored
    stale = json.loads((cache / "p1.json").read_text())
    stale["v"] = extract.CACHE_VERSION - 1
    (cache / "p1.json").write_text(json.dumps(stale))
    assert extract.load_cached(cache, 1) is None


def test_cache_dir_for() -> None:
    assert extract.cache_dir_for("deadbeef", "data") == Path("data/cache/deadbeef")


# ───────── classification (§8.1) ─────────


def test_classify_big_game() -> None:
    info = classify_mod.classify(extract.page_texts(BIG_GAME, 3))
    assert (info.kind, info.license_year, info.year_end) == ("big_game", 2026, 2026)
    assert info.title.startswith("2026 IDAHO")
    assert info.priority == 0


def test_classify_upland_uses_the_year_range() -> None:
    info = classify_mod.classify(extract.page_texts(UPLAND, 3))
    assert (info.kind, info.license_year, info.year_end) == ("upland", 2026, 2027)


def test_classify_supplemental_has_priority() -> None:
    info = classify_mod.classify(extract.page_texts(SUPPLEMENTAL, 3))
    assert (info.kind, info.license_year, info.year_end) == ("supplemental", 2026, 2026)
    assert info.priority > 0


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        ("2026 Big Game Seasons & Rules", "big_game"),
        ("2026 Big Game Seasons and Rules", "big_game"),
        ("2026-2027 Upland Game, Furbearer and Turkey Seasons & Rules", "upland"),
        ("2025-26 Moose, Bighorn Sheep and Mountain Goat Seasons & Rules", "moose_sheep_goat"),
        ("2026-2027 Migratory Game Bird Seasons", "migratory"),
        ("2026 Supplemental Proclamation", "supplemental"),
        ("Idaho Fishing Seasons & Rules 2026", None),
    ],
)
def test_detect_kind(text: str, kind: str | None) -> None:
    assert classify_mod.detect_kind(text) == kind


@pytest.mark.parametrize(
    ("text", "years"),
    [
        ("2026 Big Game Seasons & Rules", (2026, 2026)),
        ("2026-2027 Upland Game, Furbearer and Turkey", (2026, 2027)),
        ("2025-26 Moose, Bighorn Sheep and Mountain Goat", (2025, 2026)),
        ("2026–27 Migratory Game Bird", (2026, 2027)),
    ],
)
def test_year_detection(text: str, years: tuple[int, int]) -> None:
    info = classify_mod.classify([text])
    assert (info.license_year, info.year_end) == years


def test_manual_override_wins() -> None:
    info = classify_mod.classify(["an unlabelled scan"], kind="big_game", year=2024)
    assert (info.kind, info.license_year, info.year_end) == ("big_game", 2024, 2024)
    assert info.manual_kind and info.manual_year


def test_unclassifiable_never_guesses() -> None:
    with pytest.raises(classify_mod.ClassifyError) as exc:
        classify_mod.classify(["Idaho Fishing Seasons & Rules 2026"])
    assert "kind" in str(exc.value)
    with pytest.raises(classify_mod.ClassifyError):
        classify_mod.classify(["Big Game Seasons & Rules"])  # no year anywhere
    with pytest.raises(classify_mod.ClassifyError):
        classify_mod.classify(["whatever"], kind="not_a_kind")


# ───────── rotated pages: the calendar frame (T-12) ─────────

ELK_PAGE = FIXTURES / "bg2026_elk_p38.pdf"


def test_a_rotated_page_carries_its_reading_frame_and_its_drawn_grid() -> None:
    """A sideways calendar page has no ruled table, so geometry is the only way in."""
    page = next(iter(extract.iter_pages(ELK_PAGE)))
    assert page.rotated and not page.tables
    # `x` runs along the printed line, `top`/`bottom` down the printed page, so the
    # first printed line is the zone title — and it is read uncropped, which the fixed
    # 40 pt side crop (the first and last printed lines of a rotated page) would cut.
    top = min(w.top for w in page.rot_words)
    title = " ".join(w.text for w in sorted(page.rot_words, key=lambda w: w.x0) if w.top - top < 2)
    assert title.startswith("Panhandle Zone (Units 1, 2, 3, 4, 4A, 5, 6, 7, 9)")
    fills = {s.fill for s in page.rot_rects}
    assert (0.706, 0.727, 0.661) in fills  # the zone title band
    assert (0.815, 0.782, 0.66) in fills  # the month header strip
    assert any(r.bottom - r.top < 2 for r in page.rot_rules)  # row hairlines


def test_an_upright_page_pays_nothing_for_the_rotated_frame() -> None:
    page = next(iter(extract.iter_pages(FIXTURES / "bg2026_deer_p12.pdf")))
    assert not page.rotated
    assert (page.rot_words, page.rot_rects, page.rot_rules) == ([], [], [])


def test_the_rotated_frame_survives_the_page_cache(tmp_path) -> None:
    cached = extract.cache_dir_for("elk", tmp_path)
    first = next(iter(extract.iter_pages(ELK_PAGE, cache_dir=cached)))
    again = next(iter(extract.iter_pages(ELK_PAGE, cache_dir=cached)))
    assert again.rot_words == first.rot_words
    assert again.rot_rects == first.rot_rects and again.rot_rules == first.rot_rules
