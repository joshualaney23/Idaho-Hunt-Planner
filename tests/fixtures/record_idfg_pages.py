"""Record the IDFG rules pages the update check reads (T-28).

Run by hand when IDFG restyles its site; **never** in CI — the test suite makes no
network calls. Each page is stored verbatim, gzipped (a rules page is ~150 KB of markup
for a handful of links), beside a `sources.json` naming the URL, the recording date and
the sha256 of the uncompressed bytes, so a fixture can be proved to be the real page.

    python tests/fixtures/record_idfg_pages.py          # refresh every page
    python tests/fixtures/record_idfg_pages.py --list   # what the current fixtures hold
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
from datetime import date
from pathlib import Path

from planner.importer import fetch_idfg

HERE = Path(__file__).resolve().parent
PAGES_DIR = HERE / "idfg_pages"
INDEX = PAGES_DIR / "sources.json"


def slug_for(url: str) -> str:
    """`https://idfg.idaho.gov/rules/big-game` → `rules-big-game`."""
    path = url.split("//", 1)[-1].split("/", 1)[-1] if "//" in url else url
    return path.strip("/").replace("/", "-") or "index"


def load(slug: str) -> str:
    """One recorded page as text."""
    return gzip.decompress((PAGES_DIR / f"{slug}.html.gz").read_bytes()).decode("utf-8")


def recorded() -> dict[str, dict[str, object]]:
    """The fixture index: slug → `{url, recorded_on, sha256, bytes}`."""
    return json.loads(INDEX.read_text(encoding="utf-8"))


def record(urls: list[str]) -> dict[str, dict[str, object]]:
    fetcher = fetch_idfg.HttpFetcher()
    PAGES_DIR.mkdir(parents=True, exist_ok=True)
    index = recorded() if INDEX.is_file() else {}
    for url in urls:
        slug = slug_for(url)
        data = fetcher.get(url).data
        (PAGES_DIR / f"{slug}.html.gz").write_bytes(gzip.compress(data, 9))
        index[slug] = {
            "url": url,
            "recorded_on": date.today().isoformat(),
            "sha256": hashlib.sha256(data).hexdigest(),
            "bytes": len(data),
        }
        print(f"{slug:24} {len(data):>7} bytes  {url}")
    INDEX.write_text(json.dumps(index, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return index


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--list", action="store_true", help="print the current index and exit")
    args = ap.parse_args()
    if args.list:
        for slug, meta in sorted(recorded().items()):
            print(f"{slug:24} {meta['recorded_on']}  {meta['bytes']:>7} bytes  {meta['url']}")
        return 0
    record(list(fetch_idfg.SOURCE_PAGES))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
