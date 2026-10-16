"""Method-window accounting, DESIGN.md §8.7 principle 4 (§12 T-13).

The bear and lion season tables print date windows that are **not** seasons: `Dogs
Prohibited` and `Dog Training` say how the take season may be hunted. They produce no
row, so `date_token_accounting` would never see them — and a column the parser quietly
ignored is exactly how a printed date goes missing (D3).

A parser that reads such a column reports one `MethodWindow` per cell in
`SectionFacts.extra["method_windows"]`; this check asserts every one of them was read
(at least one window, nothing left over). A section that reports none is skipped.
"""

from __future__ import annotations

from typing import Any

from planner.importer.checks import CheckContext, CheckResult, MethodWindow, check


@check()
def method_window_accounting(c: CheckContext) -> CheckResult | None:
    """Every printed method window (dogs prohibited, dog training) was read (§8.7)."""
    windows: list[MethodWindow] = list(c.facts.extra.get("method_windows") or ())
    if not windows:
        return None
    unaccounted: list[dict[str, Any]] = [
        {
            "page": w.page,
            "column": w.name,
            "units": w.label,
            "text": w.text,
            "windows": w.windows,
            "leftover": list(w.leftovers),
        }
        for w in windows
        if not w.windows or w.leftovers
    ]
    total = sum(w.windows for w in windows)
    return CheckResult(
        name="method_window_accounting",
        status="pass" if not unaccounted else "fail",
        expected=f"{len(windows)} method cells read",
        actual=f"{total} windows, {len(unaccounted)} unaccounted",
        detail=unaccounted or None,
    )
