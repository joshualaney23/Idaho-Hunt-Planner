"""Render `static/data/gmu.json` to a plain SVG for visual sliver/gap checks (T-02).

    python scripts/render_gmu_svg.py out.svg              # whole state, square canvas
    python scripts/render_gmu_svg.py zoom.svg --crop 150 330 120   # x y size crop

Fills only, no strokes: any topology gap between neighbours shows up as white.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
GMU_PATH = REPO_ROOT / "static" / "data" / "gmu.json"

# Verification-only palette. The app's real colors come from static/css/theme.css (§3.3).
PALETTE = [
    "#2f6b3f",
    "#4a8c5c",
    "#7fae6a",
    "#c9a227",
    "#a8763e",
    "#6b8ea8",
    "#8a6ba8",
    "#b55c4a",
]
CLOSED_FILL = "#555555"


def render(
    data: dict[str, Any],
    crop: tuple[float, float, float] | None = None,
    *,
    labels: bool = True,
) -> str:
    units: dict[str, Any] = data["units"]
    if crop:
        x, y, size = crop
        view = f"{x:g} {y:g} {size:g} {size:g}"
        rect = f'<rect x="{x:g}" y="{y:g}" width="{size:g}" height="{size:g}" fill="#ffffff"/>'
        labels = False
    else:
        _, _, width, height = (float(v) for v in data["viewBox"].split())
        side = max(width, height)
        x = (width - side) / 2.0
        view = f"{x:g} 0 {side:g} {side:g}"
        rect = f'<rect x="{x:g}" y="0" width="{side:g}" height="{side:g}" fill="#ffffff"/>'

    paths = []
    texts = []
    for i, (name, unit) in enumerate(units.items()):
        fill = CLOSED_FILL if unit.get("closed") else PALETTE[i % len(PALETTE)]
        paths.append(f'<path d="{unit["d"]}" fill="{fill}"/>')
        if labels:
            lx, ly = unit["label"]
            texts.append(
                f'<text x="{lx:g}" y="{ly:g}" font-size="6" text-anchor="middle" '
                f'fill="#ffffff" font-family="Helvetica">{name}</text>'
            )
    body = "".join(paths) + "".join(texts)
    return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{view}">{rect}{body}</svg>\n'


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("out", type=Path, help="output .svg path")
    parser.add_argument(
        "--crop",
        nargs=3,
        type=float,
        metavar=("X", "Y", "SIZE"),
        help="square crop in viewBox units (zoomed sliver check)",
    )
    parser.add_argument("--data", type=Path, default=GMU_PATH)
    args = parser.parse_args(argv)

    data = json.loads(args.data.read_text())
    crop = tuple(args.crop) if args.crop else None  # type: ignore[assignment]
    args.out.write_text(render(data, crop))
    print(f"wrote {args.out} ({len(data['units'])} units)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
