#!/usr/bin/env python3
"""Create GitHub issues for every ticket in docs/DESIGN.md §12.

Usage: python docs/tools/make_issues.py [--dry-run] [--create] [--link]
Re-run `--link` after editing a ticket in DESIGN.md to re-sync every issue body.
  --dry-run : parse + render bodies to scratch/issue_bodies/, no GitHub calls
  --create  : create labels, milestones, and issues (skips tickets whose title already exists)
  --link    : second pass: rewrite every body with #N links for deps/blocks
"""
import json, re, subprocess, sys, os
from pathlib import Path

REPO = "joshualaney23/Idaho-Hunt-Planner"
ROOT = Path(__file__).resolve().parents[2]
DOC = (ROOT / "docs/DESIGN.md").read_text()
HERE = Path(__file__).resolve().parent
OUT = Path(os.environ.get("TMPDIR", "/tmp")) / "issue_bodies"
MAP_FILE = HERE / "issue_map.json"   # ticket id -> GitHub issue number (committed)
BLOB = f"https://github.com/{REPO}/blob/main"

PHASES = {
    0: ("Phase 0 – Foundations", "Repo runs, themed shell, map renders"),
    1: ("Phase 1 – MVP: Deer + controlled hunts", "General deer + every controlled deer/elk/pronghorn/bear hunt, accuracy-gated; tags; this weekend's green units"),
    2: ("Phase 2 – Elk A/B & other big game", "Elk A/B zones, pronghorn, bear, lion, wolf, supplements"),
    3: ("Phase 3 – Upland birds & turkey", "Forest grouse, quail, chukar, partridge, sage/sharp-tailed grouse, pheasant via county areas; turkey general + controlled"),
    4: ("Phase 4 – Small game, furbearers, predatory, falconry", "Cottontail, snowshoe hare, red squirrel, badger/bobcat/red fox (hunting), predatory/unprotected, falconry"),
    5: ("Phase 5 – Moose/sheep/goat & migratory birds", "MSG and Migratory brochures pass the accuracy gate"),
    6: ("Phase 6 – Polish", "QA report, optional Hunt Planner spot-check, zoom, print, settings"),
}
PHASE_OF = {}
for t in ["T-01", "T-02", "T-03", "T-04", "T-05"]: PHASE_OF[t] = 0
for t in ["T-06", "T-07", "T-08", "T-09", "T-10", "T-11", "T-15", "T-16", "T-17a", "T-17b", "T-17c", "T-22"]: PHASE_OF[t] = 1
for t in ["T-12", "T-13", "T-18"]: PHASE_OF[t] = 2
for t in ["T-14", "T-23"]: PHASE_OF[t] = 3
PHASE_OF["T-24"] = 4
for t in ["T-25", "T-26"]: PHASE_OF[t] = 5
for t in ["T-19", "T-20", "T-21"]: PHASE_OF[t] = 6

BUILD_ORDER = [["T-01", "T-04", "T-05"], ["T-02", "T-03", "T-10"], ["T-22"], ["T-11", "T-17a"], ["T-17b"], ["T-06"],
               ["T-09", "T-15"], ["T-07", "T-08"], ["T-17c"], ["T-16"]]
GROUPS = [["T-01", "T-04", "T-05"], ["T-17a", "T-17b"], ["T-07", "T-08"]]
SEPARATE_PARSERS = {"T-11", "T-17a", "T-12", "T-14", "T-23", "T-24", "T-25", "T-26"}

AREA = {  # area labels
    "T-01": ["infra"], "T-02": ["gis"], "T-03": ["frontend"], "T-04": ["backend"],
    "T-05": ["backend", "frontend"], "T-06": ["backend"], "T-07": ["frontend"],
    "T-08": ["frontend"], "T-09": ["backend", "frontend"], "T-10": ["importer"],
    "T-11": ["importer", "accuracy"], "T-12": ["importer", "accuracy"], "T-13": ["importer"],
    "T-14": ["importer", "gis", "accuracy"], "T-15": ["frontend", "backend", "accuracy"],
    "T-16": ["frontend", "backend"], "T-17a": ["importer", "accuracy"],
    "T-17b": ["importer", "gis", "accuracy"], "T-17c": ["backend", "frontend"],
    "T-18": ["importer"], "T-19": ["accuracy"], "T-20": ["accuracy"],
    "T-21": ["backend", "frontend", "documentation"], "T-22": ["accuracy"],
    "T-23": ["importer", "accuracy"], "T-24": ["importer", "gis", "accuracy"],
    "T-25": ["importer", "accuracy"], "T-26": ["importer", "gis", "accuracy"],
}
LABELS = {
    "phase:0": ("c5def5", "Phase 0 – Foundations"),
    "phase:1-mvp": ("0e8a16", "Phase 1 – MVP (deer + controlled hunts, accuracy-gated)"),
    "phase:2": ("bfd4f2", "Phase 2 – Elk A/B & other big game"),
    "phase:3": ("bfd4f2", "Phase 3 – Upland birds & turkey"),
    "phase:4": ("bfd4f2", "Phase 4 – Small game, furbearers, predatory, falconry"),
    "phase:5": ("bfd4f2", "Phase 5 – Moose/sheep/goat & migratory birds"),
    "phase:6": ("bfd4f2", "Phase 6 – Polish"),
    "size:S": ("ededed", "Small ticket"), "size:M": ("d4c5f9", "Medium ticket"), "size:L": ("b60205", "Large ticket"),
    "ticket": ("1d76db", "Implementation ticket from docs/DESIGN.md §12"),
    "infra": ("5319e7", "Scaffold, CI, tooling"), "gis": ("006b75", "GIS geometry / overlays"),
    "frontend": ("fbca04", "Vanilla JS / HTML / CSS UI"), "backend": ("0052cc", "Flask API, DB, engine"),
    "importer": ("d93f0b", "PDF regulation parsers"),
    "accuracy": ("b60205", "Accuracy gate (§8.7): checks, golden data — top priority (D3)"),
    "priority:highest": ("b60205", "Highest priority"),
    "human-verification": ("f9d0c4", "Needs human verification against the PDF"),
}
PHASE_LABEL = {0: "phase:0", 1: "phase:1-mvp", 2: "phase:2", 3: "phase:3", 4: "phase:4", 5: "phase:5", 6: "phase:6"}

TID = r"T-\d{2}[a-c]?"


def anchor(heading: str) -> str:
    a = heading.strip().lower()
    a = re.sub(r"[^\w\- ]", "", a)
    return a.replace(" ", "-")


def parse_tickets():
    sec12 = DOC.split("## 12. Tickets", 1)[1].split("## 13.", 1)[0]
    header = sec12.split("\n### ", 1)[0]
    conventions = [l[2:].strip() for l in header.splitlines() if l.startswith("- ")]
    parts = re.split(r"\n### ", sec12)[1:]
    tickets = []
    for p in parts:
        p = p.strip().rstrip("-").strip()
        heading, rest = p.split("\n", 1)
        m = re.match(rf"({TID}) · (.+)", heading)
        tid, title = m.group(1), m.group(2).strip()
        meta = re.search(r"\*\*Depends on:\*\*\s*(.*?)\s*·\s*\*\*Size:\*\*\s*(\w+)", rest)
        dep_text, size = meta.group(1), meta.group(2)
        expanded = re.sub(r"(T-\d{2})([a-c])((?:/[a-c])+)",
                          lambda m: ", ".join(m.group(1) + x for x in [m.group(2)] + m.group(3).split("/")[1:]), dep_text)
        deps = [] if dep_text.strip().lower() == "none" else list(dict.fromkeys(re.findall(TID, expanded)))
        body = rest[meta.end():].strip()
        # split description / acceptance
        am = re.search(r"\*\*Acceptance criteria[^*]*\*\*", body)
        desc, acc = (body[:am.start()], body[am.end():]) if am else (body, "")
        desc = re.sub(r"^\*\*Description:\*\*\s*", "", desc.strip())
        tickets.append(dict(id=tid, title=title, heading=heading, dep_text=dep_text, deps=deps,
                            size=size, desc=desc.strip(), acc_raw=acc.strip(), acc_header=am.group(0) if am else ""))
    return tickets, conventions


def split_top(s, sep=";"):
    out, depth, cur = [], 0, ""
    for ch in s:
        if ch in "([": depth += 1
        if ch in ")]": depth -= 1
        if ch == sep and depth == 0:
            out.append(cur); cur = ""
        else:
            cur += ch
    out.append(cur)
    return [x.strip().rstrip(".").strip() for x in out if x.strip()]


def acc_checklist(t):
    raw = t["acc_raw"]
    qual = re.sub(r"\*\*Acceptance criteria\s*|:\*\*", "", t["acc_header"]).strip()
    lines = raw.splitlines()
    items = []
    if any(l.startswith("- ") for l in lines):
        lead = []
        for l in lines:
            if l.startswith("- "):
                items.append(l[2:].strip())
            elif l.strip():
                (lead if not items else items).append(l.strip()) if not items else items.__setitem__(-1, items[-1] + " " + l.strip())
        lead_txt = " ".join(lead)
    else:
        lead_txt = ""
        items = []
        for piece in split_top(raw):
            items += sentences(piece)
    out = []
    if qual: out.append(f"_{qual}_\n")
    if lead_txt: out.append(lead_txt + "\n")
    for i in items:
        if i.lower().startswith("spot checks:"):
            out.append("- [ ] Spot checks:")
            out += [f"  - [ ] {s}" for s in split_top(i.split(":", 1)[1])]
        else:
            out.append(f"- [ ] {i}")
    return "\n".join(out)


def sentences(s):
    """Split a paragraph into sentences (outside parens/backticks), keeping ** bold balanced."""
    parts = re.split(r"(?<=[.])\s+(?=\*\*|[A-Z`§])", s)
    out, open_bold = [], False
    for p in parts:
        p = p.strip()
        if open_bold:
            p = "**" + p
        n = p.count("**")
        open_bold = n % 2 == 1
        if open_bold:
            p = p + "**"
        out.append(p.rstrip(".").rstrip() + "")
    # fix '. **' -> keep text; clean doubled '****'
    return [re.sub(r"\*\*\*\*", "", x).strip() for x in out if x.strip("* ")]


def related_files(t):
    text = t["desc"] + t["acc_raw"]
    files = sorted(set(re.findall(r"docs/[\w./-]+\.(?:py|html|md|csv)", text)))
    existing = [f for f in files if (ROOT / f).exists()]
    return existing


def slug(title):
    s = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return "-".join(s.split("-")[:5])


def render(t, tickets, conventions, issue_map):
    ref = lambda x: f"#{issue_map[x]} ({x})" if x in issue_map else f"{x}"
    blocks = [o["id"] for o in tickets if t["id"] in o["deps"]]
    ph = PHASE_OF[t["id"]]
    group = next((g for g in GROUPS if t["id"] in g), None)
    num = issue_map.get(t["id"])
    lines = []
    lines.append(f"> **{t['id']}** from [`docs/DESIGN.md` §12]({BLOB}/docs/DESIGN.md#{anchor(t['heading'])}) · "
                 f"**{PHASES[ph][0]}** · **Size {t['size']}**")
    lines.append(">")
    dep_line = ", ".join(ref(d) for d in t["deps"]) or "none"
    if t["dep_text"].strip().lower() not in ("none",) and re.sub(TID, "", t["dep_text"]).strip(" ,"):
        dep_line += f"  _(as written in the doc: {t['dep_text']})_"
    lines.append(f"> **Depends on:** {dep_line}")
    lines.append(f"> **Blocks:** {', '.join(ref(b) for b in blocks) or 'none'}")
    if group:
        others = [g for g in group if g != t["id"]]
        lines.append(f"> **Session grouping (§14):** implement together with {', '.join(ref(o) for o in others)} in one agent session.")
    elif t["id"] in SEPARATE_PARSERS:
        lines.append("> **Session grouping (§14):** parser ticket — give it its own fresh agent session.")
    else:
        lines.append("> **Session grouping (§14):** one fresh agent session for this ticket.")
    step = next((i + 1 for i, g in enumerate(BUILD_ORDER) if t["id"] in g), None)
    if step:
        par = [x for x in BUILD_ORDER[step - 1] if x != t["id"]]
        lines.append(f"> **Build order (§14):** step {step} of {len(BUILD_ORDER)} (MVP path)" + (f", alongside {', '.join(ref(x) for x in par)}" if par else ""))
    else:
        lines.append(f"> **Build order (§14):** after the MVP path ({PHASES[PHASE_OF[t['id']]][0]})")
    lines.append("")
    if t["id"] == "T-22":
        lines += ["> [!IMPORTANT]", "> Highest-priority ticket (D3: PDF parsing accuracy is the top priority). "
                  "Golden-data verification is **human work** — an agent prepares the page-by-page checklist; Josh ticks off `VERIFICATION.md`.", ""]
    lines += ["## Before you start",
              f"Read [`docs/AGENT_CONTEXT.md`]({BLOB}/docs/AGENT_CONTEXT.md) first, then this ticket, then only the [`docs/DESIGN.md`]({BLOB}/docs/DESIGN.md) sections it cites "
              "(`python3 docs/tools/section.py 8.7`; importer/accuracy tickets always read §8.7). "
              "The ticket text below is copied verbatim from §12; if it ever disagrees with DESIGN.md, DESIGN.md on `main` wins.",
              "", "**Shared conventions (§12):**"]
    lines += [f"- {c}" for c in conventions]
    rf = related_files(t)
    if rf:
        lines += ["", "**Related files in the repo:** " + ", ".join(f"[`{f}`]({BLOB}/{f})" for f in rf)]
    lines += ["", "## Description", "", t["desc"], "", "## Acceptance criteria", "", acc_checklist(t), "",
              "## Definition of done",
              f"- [ ] Work on branch `{t['id'].lower()}-{slug(t['title'])}`.",
              "- [ ] `pytest -q` passes from the repo root and `python -m planner --no-browser` still starts.",
              "- [ ] `ruff check` is clean; nothing under `data/` is committed.",
              f"- [ ] PR titled `{t['id']}: {t['title']}` with `Closes #{num if num else 'N'}` in the body.",
              "- [ ] PR description lists **every acceptance criterion with its evidence** (command output, spot-check values, screenshots) and any deviation from DESIGN.md.",
              "- [ ] If the design changed, `docs/DESIGN.md` is updated in the same PR.",
              "", "## Kickoff prompt", "", "```",
              "Repo: ~/git/Idaho-Hunt-Planner (remote github.com/joshualaney23/Idaho-Hunt-Planner).",
              f"Implement ticket {t['id']} (GitHub issue #{num if num else 'N'}). Read docs/AGENT_CONTEXT.md first, then the ticket",
              "(`gh issue view " + (str(num) if num else "N") + "`), then only the DESIGN.md sections it cites. "
              f"Work on branch {t['id'].lower()}-{slug(t['title'])}, run `pytest -q`",
              f"until green, push, and open a PR titled \"{t['id']}: {t['title']}\" (body includes \"Closes #{num if num else 'N'}\") whose description lists every acceptance",
              "criterion with its evidence (command output, spot-check values, screenshots). Don't change",
              "behavior outside the ticket's scope; if the design is wrong, say so in the PR and update DESIGN.md.",
              "```"]
    return "\n".join(lines) + "\n"


def labels_for(t):
    ls = ["ticket", PHASE_LABEL[PHASE_OF[t["id"]]], f"size:{t['size']}"] + AREA[t["id"]]
    if t["id"] == "T-22": ls += ["priority:highest", "human-verification"]
    if t["id"] in ("T-11", "T-12", "T-14", "T-23", "T-24", "T-25", "T-26"): ls.append("human-verification")  # golden files
    return sorted(set(ls))


def sort_key(t):
    m = re.match(r"T-(\d+)([a-c]?)", t["id"]); return (int(m.group(1)), m.group(2))


def gh(*args, input=None):
    r = subprocess.run(["gh", *args], capture_output=True, text=True, input=input, cwd=ROOT)
    if r.returncode != 0:
        raise SystemExit(f"gh {' '.join(args)} failed: {r.stderr}")
    return r.stdout.strip()


def main():
    tickets, conventions = parse_tickets()
    tickets.sort(key=sort_key)
    issue_map = json.loads(MAP_FILE.read_text()) if MAP_FILE.exists() else {}
    mode = sys.argv[1] if len(sys.argv) > 1 else "--dry-run"
    if mode == "--dry-run":
        OUT.mkdir(exist_ok=True)
        for t in tickets:
            (OUT / f"{t['id']}.md").write_text(render(t, tickets, conventions, issue_map))
            print(t["id"], t["size"], "deps=", t["deps"], "labels=", labels_for(t), "| acc items:", acc_checklist(t).count("- [ ]"))
        print(len(tickets), "tickets; conventions:", len(conventions))
        return
    if mode == "--create":
        for name, (color, desc) in LABELS.items():
            gh("label", "create", name, "--color", color, "--description", desc, "--force")
        existing_ms = {m["title"]: m["number"] for m in json.loads(gh("api", f"repos/{REPO}/milestones?state=all&per_page=100"))}
        for ph, (title, desc) in PHASES.items():
            if title not in existing_ms:
                gh("api", f"repos/{REPO}/milestones", "-f", f"title={title}", "-f", f"description={desc}")
        existing = {i["title"]: i["number"] for i in json.loads(gh("issue", "list", "--state", "all", "--limit", "200", "--json", "title,number"))}
        for t in tickets:
            title = f"{t['id']}: {t['title']}"
            if title in existing:
                issue_map[t["id"]] = existing[title]; continue
            body = render(t, tickets, conventions, issue_map)
            args = ["issue", "create", "--title", title, "--body-file", "-", "--milestone", PHASES[PHASE_OF[t["id"]]][0]]
            for l in labels_for(t): args += ["--label", l]
            url = gh(*args, input=body)
            issue_map[t["id"]] = int(url.rsplit("/", 1)[1])
            MAP_FILE.write_text(json.dumps(issue_map, indent=1))
            print("created", title, url)
        MAP_FILE.write_text(json.dumps(issue_map, indent=1))
        return
    if mode == "--link":
        for t in tickets:
            body = render(t, tickets, conventions, issue_map)
            gh("issue", "edit", str(issue_map[t["id"]]), "--body-file", "-", input=body)
            print("linked", t["id"], "#", issue_map[t["id"]])


if __name__ == "__main__":
    main()
