#!/usr/bin/env python3
"""Print one section of docs/DESIGN.md (with its subsections) so agents read only what they need.

Usage: python3 docs/tools/section.py 8.7      # numbered section, e.g. 0, 5, 5.3, 8.7
       python3 docs/tools/section.py T-17a    # one ticket from §12
       python3 docs/tools/section.py --toc    # heading list
"""
import re
import sys
from pathlib import Path

DOC = Path(__file__).resolve().parents[1] / "DESIGN.md"
HEAD = re.compile(r"^(#{2,4}) (.*)$")


def main(arg: str) -> int:
    lines = DOC.read_text().splitlines()
    if arg == "--toc":
        print("\n".join(l for l in lines if HEAD.match(l)))
        return 0
    key = re.compile(rf"^{re.escape(arg)}(\.|\s|$)", re.I)
    out, level = [], None
    for line in lines:
        m = HEAD.match(line)
        if level is None:
            if m and key.match(m.group(2)):
                level = len(m.group(1))
                out.append(line)
            continue
        if m and len(m.group(1)) <= level:
            break
        if line.strip() == "---" and arg.upper().startswith("T-"):
            break
        out.append(line)
    if not out:
        print(f"section {arg!r} not found; try --toc", file=sys.stderr)
        return 1
    while out and out[-1].strip() in ("", "---"):
        out.pop()
    print("\n".join(out))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "--toc"))
