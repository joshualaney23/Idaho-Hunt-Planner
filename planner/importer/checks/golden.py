"""`golden_match`: the import must reproduce the hand-verified data exactly (§8.7).

The golden files are the only check in the registry that compares against **human**
truth rather than against the document itself: for the first year every row of every
MVP section is verified page by page against the PDF (T-22d–f) and committed as JSON.
Re-parsing that PDF after any parser change must still produce it byte for byte.

A section with no committed golden file is `skipped`, never `warn`: §8.7 says a missing
golden file must not block publishing, and in this repo's gate (T-22b) a `warn` *does*
block until a human waives it. `skipped` is that rule's name here.
"""

from __future__ import annotations

from planner import db, golden
from planner.importer.checks import CheckContext, CheckResult, check


@check()
def golden_match(c: CheckContext) -> CheckResult | None:
    """Compare this section's rows with `tests/golden/<year>/<section>.json` (§8.7)."""
    if db.current_or_none() is None:
        return None  # no database in this context, so there are no rows to compare
    path = golden.golden_path(c.license_year, c.section)
    rows = golden.canonical_rows(c.parse.document_id, c.section)
    if not path.is_file():
        if not rows:
            return None  # nothing parsed and nothing to compare: the section is silent
        return CheckResult(
            name="golden_match",
            status="skipped",
            expected=f"tests/golden/{c.license_year}/{c.section}.json",
            actual=f"no golden file yet for {c.section} {c.license_year}; {len(rows)} rows parsed",
        )
    want = golden.load(path)
    expected = list(want.get("rows") or [])
    diffs = golden.row_diffs(expected, rows)
    return CheckResult(
        name="golden_match",
        status="pass" if not diffs else "fail",
        expected=f"{len(expected)} verified rows ({path.parent.name}/{path.name})",
        actual=f"{len(rows)} parsed, {len(diffs)} differ",
        detail=diffs or None,
    )
