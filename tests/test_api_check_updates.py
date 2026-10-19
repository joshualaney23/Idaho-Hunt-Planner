"""`POST`/`GET /api/documents/check-updates` (T-28, §6 + §12).

Every request is served by a fake fetcher injected through `app.config["IDFG_FETCHER"]`,
so the suite makes no network call. The PDFs are the generated fixtures T-10 uses.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from planner.importer import fetch_idfg

FIXTURES = Path(__file__).resolve().parent / "fixtures"
FILES = "https://idfg.idaho.gov/sites/default/files"
BIG_GAME = f"{FILES}/seasons-rules-big-game-2026.pdf"
UPLAND = f"{FILES}/seasons-rules-upland-game-2026-2027.pdf"
SUPPLEMENT = f"{FILES}/seasons-rules-big-game-supplemental-proclamation-2026-2.pdf"
PAGE = "https://idfg.idaho.gov/rules"

#: A rules page in the shape IDFG really serves: relative hrefs, deep links into the
#: same brochure, and PDFs we must not import beside the ones we must.
PAGE_HTML = """
<html><body>
  <a href="/sites/default/files/seasons-rules-big-game-2026.pdf">Big Game</a>
  <a href="/sites/default/files/seasons-rules-big-game-2026.pdf#page=51">Elk seasons</a>
  <a href="/sites/default/files/seasons-rules-upland-game-2026-2027.pdf">Upland</a>
  <a href="/sites/default/files/seasons-rules-fish-2025-2027.pdf">Fishing</a>
  <a href="https://example.com/seasons-rules-big-game-2026.pdf">a mirror</a>
</body></html>
"""


class FakeFetcher:
    """Serves recorded bytes; anything unmapped raises the way a 404 would."""

    def __init__(self, bodies: dict[str, bytes]) -> None:
        self.bodies = bodies
        self.requested: list[str] = []

    def get(self, url: str) -> fetch_idfg.Fetched:
        fetch_idfg.require_allowed(url)
        self.requested.append(url)
        body = self.bodies.get(url)
        if body is None:
            raise fetch_idfg.FetchError("HTTP 404 Not Found", url=url, reason="http_error")
        return fetch_idfg.Fetched(url, body)


def pdf(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


@pytest.fixture
def bodies() -> dict[str, bytes]:
    return {
        PAGE: PAGE_HTML.encode(),
        BIG_GAME: pdf("big_game_sample.pdf"),
        UPLAND: pdf("upland_sample.pdf"),
    }


@pytest.fixture
def checker(app, bodies):
    """`run(**overrides)` posts the check with a fake fetcher and returns the body."""
    app.config["IDFG_SOURCE_PAGES"] = (PAGE,)
    client = app.test_client()

    def run(extra: dict[str, bytes] | None = None):
        fetcher = FakeFetcher({**bodies, **(extra or {})})
        app.config["IDFG_FETCHER"] = fetcher
        resp = client.post("/api/documents/check-updates")
        assert resp.status_code == 200, resp.get_json()
        body = resp.get_json()
        body["_fetcher"] = fetcher
        return body

    run.client = client
    return run


def by_name(body) -> dict[str, dict]:
    return {entry["filename"]: entry for entry in body["found"]}


# ───────── staging, never publishing ─────────
def test_new_documents_are_staged_for_review_and_never_published(checker):
    body = checker()
    found = by_name(body)
    assert {name: e["state"] for name, e in found.items()} == {
        "seasons-rules-big-game-2026.pdf": "new",
        "seasons-rules-upland-game-2026-2027.pdf": "new",
    }
    assert len(body["imported"]) == 2
    docs = checker.client.get("/api/documents").get_json()["documents"]
    assert {d["status"] for d in docs} == {"needs_review"}
    assert all(d["seasons"].get("published", 0) == 0 for d in docs)


def test_the_classifier_confirms_the_filenames_guess(checker):
    found = by_name(checker())
    assert found["seasons-rules-big-game-2026.pdf"]["kind"] == "big_game"
    assert found["seasons-rules-upland-game-2026-2027.pdf"]["kind"] == "upland"
    assert found["seasons-rules-upland-game-2026-2027.pdf"]["year"] == 2026


def test_a_known_sha256_is_reported_known_and_not_re_imported(checker):
    first = checker()
    second = checker()
    assert [e["state"] for e in second["found"]] == ["known", "known"]
    assert second["imported"] == []
    assert {e["document_id"] for e in second["found"]} == set(first["imported"])
    assert len(checker.client.get("/api/documents").get_json()["documents"]) == 2


def test_a_same_filename_pdf_with_a_new_hash_is_changed_and_linked(checker):
    """AC2: a silent IDFG correction stages a new version above the one it replaces."""
    first = checker()
    original = first["imported"][0]
    # Same URL and filename, different bytes: the supplement stands in for a re-posting.
    changed = checker({BIG_GAME: pdf("supplemental_sample.pdf")})
    entry = by_name(changed)["seasons-rules-big-game-2026.pdf"]
    assert entry["state"] == "changed"
    assert entry["replaces"]["id"] == original

    new_doc = checker.client.get(f"/api/documents/{entry['document_id']}").get_json()["document"]
    old_doc = checker.client.get(f"/api/documents/{original}").get_json()["document"]
    assert new_doc["replaces"]["id"] == original
    assert new_doc["replaces"]["title"] == old_doc["title"]
    # Staged above the document it replaces, so publishing supersedes it (§8.5).
    assert new_doc["priority"] > old_doc["priority"]
    assert new_doc["status"] == "needs_review"


# ───────── failures never hide the other sources ─────────
def test_a_dead_source_page_is_reported_and_the_others_still_run(app, bodies):
    dead = "https://idfg.idaho.gov/rules/gone"
    app.config["IDFG_SOURCE_PAGES"] = (dead, PAGE)
    app.config["IDFG_FETCHER"] = FakeFetcher(bodies)
    body = app.test_client().post("/api/documents/check-updates").get_json()
    pages = {s["url"]: s for s in body["sources"] if s["role"] == "page"}
    assert pages[dead]["status"] == "error"
    assert "404" in pages[dead]["error"]
    assert pages[PAGE]["status"] == "ok"
    assert len(body["imported"]) == 2


def test_a_pdf_that_404s_is_an_error_entry_and_the_other_pdf_imports(checker, bodies):
    del bodies[BIG_GAME]
    body = checker()
    found = by_name(body)
    assert found["seasons-rules-big-game-2026.pdf"]["state"] == "error"
    assert found["seasons-rules-upland-game-2026-2027.pdf"]["state"] == "new"
    assert len(body["imported"]) == 1
    errors = [s for s in body["sources"] if s["status"] == "error"]
    assert [s["url"] for s in errors] == [BIG_GAME]


def test_a_non_pdf_payload_is_rejected(checker):
    body = checker({BIG_GAME: b"<html>sorry, moved</html>"})
    entry = by_name(body)["seasons-rules-big-game-2026.pdf"]
    assert entry["state"] == "error"
    assert "not a PDF" in entry["error"]
    assert body["imported"] == [
        by_name(body)["seasons-rules-upland-game-2026-2027.pdf"]["document_id"]
    ]


def test_a_foreign_host_and_a_non_brochure_are_never_fetched(checker):
    """AC4: the example.com mirror and the fishing brochure are not even requested."""
    body = checker()
    requested = body["_fetcher"].requested
    assert not any("example.com" in url for url in requested)
    assert not any("fish" in url for url in requested)
    assert sorted(requested) == sorted([PAGE, BIG_GAME, UPLAND])


def test_a_deep_link_is_fetched_once(checker):
    """The same brochure linked twice (`…#page=51`) is one download."""
    body = checker()
    assert body["_fetcher"].requested.count(BIG_GAME) == 1


# ───────── supplements: discovered, not imported (T-18 is unbuilt) ─────────
def test_a_supplement_is_reported_but_not_imported(app, bodies):
    html = PAGE_HTML.replace(
        "</body>",
        '<a href="/sites/default/files/'
        'seasons-rules-big-game-supplemental-proclamation-2026-2.pdf">Supplement</a></body>',
    )
    app.config["IDFG_SOURCE_PAGES"] = (PAGE,)
    app.config["IDFG_FETCHER"] = FakeFetcher(
        {**bodies, PAGE: html.encode(), SUPPLEMENT: pdf("supplemental_sample.pdf")}
    )
    body = app.test_client().post("/api/documents/check-updates").get_json()
    entry = by_name(body)["seasons-rules-big-game-supplemental-proclamation-2026-2.pdf"]
    assert entry["state"] == "unsupported"
    assert "T-18" in entry["reason"]
    kinds = {d["kind"] for d in app.test_client().get("/api/documents").get_json()["documents"]}
    assert "supplemental" not in kinds


# ───────── the stored log ─────────
def test_the_last_result_is_remembered(checker):
    assert checker.client.get("/api/documents/check-updates").get_json() == {
        "checked_at": None,
        "sources": [],
        "found": [],
        "imported": [],
    }
    body = checker()
    body.pop("_fetcher")
    stored = checker.client.get("/api/documents/check-updates").get_json()
    assert stored == body
    assert stored["checked_at"]
