// Regulations review screen (T-15c, DESIGN.md §8.4) — `#/regs/<docId>`.
//
// Three stacked parts inside the Regulations tab's detail host:
//
//   1. the Accuracy panel: every `import_checks` row, grouped by §8.7 section, with
//      expected vs. actual, links to the offending rows and pages, the waived badge
//      and a waive dialog that demands a typed reason;
//   2. the row list: staged rows grouped species -> table title -> page, each with its
//      confidence color, restriction badges, source text, the T-15a diff badge and
//      per-row approve / reject / edit (inline editors for every field);
//   3. the publish bar: approve-all-passing plus a Publish button that stays disabled
//      while the gate blocks, with a confirmation modal that surfaces a 409.
//
// Everything that decides *what* is shown is a pure function (`sectionViews`,
// `groupRows`, `confidenceClass`, `diffIndex`, `gateReasons`, `blockedLines`), so
// review.test.mjs covers the semantics without a browser; the DOM half and the
// keyboard shortcuts are verified in real Chrome in the PR.

import { getJSON, postJSON, putJSON } from "./api.js";
import { badge, card, textEl, toast } from "./ui.js";

/** `import_checks.status` -> the glyph the panel shows (§8.4). */
export const CHECK_ICONS = { pass: "✓", fail: "✗", warn: "⚠", skipped: "–" };
/** …and the badge color class (`b-<kind>`), from app.css. */
export const CHECK_KINDS = { pass: "ok", fail: "error", warn: "warn", skipped: "neutral" };

/** §8.7 section id -> a human heading. Unknown sections fall back to the raw id. */
export function sectionLabel(section) {
  const raw = String(section || "").trim();
  if (!raw) return "Unsectioned";
  return raw
    .replace(/^controlled_/, "Controlled · ")
    .replace(/_general$/, " · general")
    .replace(/_/g, " ")
    .replace(/^./, (c) => c.toUpperCase());
}

/** The fields the inline editors can change, in the order the editor lays them out. */
export const EDITABLE_FIELDS = [
  "units",
  "start_date",
  "end_date",
  "tag_type_id",
  "weapon",
  "sex",
  "restrictions",
];

/** `seasons.weapon` (§5) -> label. */
export const WEAPONS = [
  ["any", "Any weapon"],
  ["archery", "Archery"],
  ["muzzleloader", "Muzzleloader"],
  ["short_range", "Short-range"],
  ["shotgun", "Shotgun"],
  ["falconry", "Falconry"],
];

/** `seasons.sex` (§5) -> label. */
export const SEXES = [
  ["antlered", "Antlered"],
  ["antlerless", "Antlerless"],
  ["either", "Either sex"],
  ["any", "Any"],
  ["male", "Male"],
  ["na", "Not applicable"],
];

/** `seasons.hunt_class` (§5) -> label; the Add-season editor's class select (T-16). */
export const HUNT_CLASSES = [
  ["general", "General"],
  ["controlled", "Controlled"],
  ["extra", "Extra"],
  ["youth", "Youth"],
  ["outfitter", "Outfitter"],
  ["other", "Other"],
];

/** The §5.1 closed vocabulary, mirrored for the restriction multi-select. */
export const RESTRICTIONS = [
  ["whitetail_only", "White-tailed deer only"],
  ["mule_deer_only", "Mule deer only"],
  ["antlered_only", "Antlered only"],
  ["antlerless_only", "Antlerless only"],
  ["two_point_only", "Two-point or less only"],
  ["spike_only", "Spike bull only"],
  ["brow_tined", "Brow-tined bull only"],
  ["youth_only", "Youth only"],
  ["private_land_only", "Private land only"],
  ["portion_of_unit", "Portion of unit only"],
  ["outfitter_only", "Outfitted hunters only"],
  ["cwd_testing", "CWD testing required"],
  ["motorized_rule", "Motorized hunting rule"],
  ["second_tag_prohibited", "Second tag prohibited"],
  ["see_notes", "See notes"],
];

const RESTRICTION_LABELS = new Map(RESTRICTIONS);

/** A restriction code's label; an unknown code keeps its code, never a guess (D3). */
export function restrictionLabel(code) {
  return RESTRICTION_LABELS.get(String(code)) || String(code);
}

// ---- the Accuracy panel's view model ---------------------------------------

/**
 * One `import_checks` row as the panel shows it:
 * `{id, section, name, status, icon, kind, expected, actual, blocks, waived,
 *   reason, by, at, rows, pages}`.
 */
export function checkView(check) {
  const status = String(check?.status || "");
  const detail = check?.detail || {};
  return {
    id: check?.id,
    section: check?.section || "",
    name: check?.check_name || "",
    status,
    icon: CHECK_ICONS[status] || "?",
    kind: CHECK_KINDS[status] || "neutral",
    expected: check?.expected ?? null,
    actual: check?.actual ?? null,
    blocks: Boolean(check?.blocks_publish),
    waived: Boolean(check?.waived),
    reason: check?.waive_reason || "",
    by: check?.waived_by || "",
    at: check?.waived_at || "",
    // Links to the offending rows and pages, when the check recorded any.
    rows: (detail.rows || detail.season_ids || []).map(Number).filter(Number.isFinite),
    pages: (detail.pages || []).map(Number).filter(Number.isFinite),
  };
}

/**
 * The panel, grouped by §8.7 section:
 * `[{section, label, waived, clean, blocking, counts, checks:[checkView]}]`.
 * Sections that block come first, then waived ones, then the rest — the eye goes to
 * what stands between this document and Publish.
 */
export function sectionViews(checks, sections) {
  const views = (checks || []).map(checkView);
  const summaries = new Map((sections || []).map((s) => [s.section || "", s]));
  const bySection = new Map();
  for (const v of views) {
    if (!bySection.has(v.section)) bySection.set(v.section, []);
    bySection.get(v.section).push(v);
  }
  for (const key of summaries.keys()) if (!bySection.has(key)) bySection.set(key, []);
  const out = [...bySection.entries()].map(([section, list]) => {
    const summary = summaries.get(section) || {};
    const counts = {};
    for (const c of list) counts[c.status] = (counts[c.status] || 0) + 1;
    return {
      section,
      label: sectionLabel(section),
      waived: Boolean(summary.waived) || list.some((c) => c.waived),
      clean: summary.clean !== undefined ? Boolean(summary.clean) : list.every((c) => !c.blocks),
      blocking: list.filter((c) => c.blocks).map((c) => c.name),
      counts,
      checks: list.slice().sort((a, b) => a.name.localeCompare(b.name)),
    };
  });
  const rank = (s) => (s.blocking.length ? 0 : s.waived ? 1 : 2);
  return out.sort((a, b) => rank(a) - rank(b) || a.label.localeCompare(b.label));
}

// ---- the row list's view model ---------------------------------------------

/** 0..1 -> `sc1`…`sc5`, the §4.1 confidence ramp (sc1 = worst). */
export function confidenceClass(confidence) {
  const c = Number(confidence);
  if (!Number.isFinite(c)) return "sc1";
  const bucket = Math.ceil(Math.min(Math.max(c, 0), 1) * 5);
  return `sc${Math.max(1, bucket)}`;
}

/** The table title a row came from, falling back to its species + tag. */
export function rowTitle(row) {
  const title = String(row?.table_title || row?.title || "").trim();
  if (title) return title;
  const parts = [row?.species_id, row?.tag_type_id].filter(Boolean).map(String);
  return parts.join(" · ") || "Untitled table";
}

/**
 * Rows grouped species -> table title -> page (§8.4), each level sorted, and every
 * row kept: `[{species, titles:[{title, pages:[{page, rows:[…]}]}]}]`.
 */
export function groupRows(seasons) {
  const species = new Map();
  for (const row of seasons || []) {
    const sp = String(row.species_id || "unknown");
    const title = rowTitle(row);
    const page = Number(row.source_page) || 0;
    if (!species.has(sp)) species.set(sp, new Map());
    const titles = species.get(sp);
    if (!titles.has(title)) titles.set(title, new Map());
    const pages = titles.get(title);
    if (!pages.has(page)) pages.set(page, []);
    pages.get(page).push(row);
  }
  return [...species.entries()]
    .sort((a, b) => a[0].localeCompare(b[0]))
    .map(([sp, titles]) => ({
      species: sp,
      titles: [...titles.entries()]
        .sort((a, b) => a[0].localeCompare(b[0]))
        .map(([title, pages]) => ({
          title,
          pages: [...pages.entries()]
            .sort((a, b) => a[0] - b[0])
            .map(([page, rows]) => ({
              page,
              rows: rows.slice().sort((x, y) => (x.id || 0) - (y.id || 0)),
            })),
        })),
    }));
}

/**
 * The Staged rows list order: every row that is not yet approved, grouped as
 * `groupRows` does, then the approved rows grouped the same way at the bottom. An
 * approved row therefore drops to the end of the section when it is approved, and
 * the species blocks repeat once for the approved tail.
 */
export function groupRowsForReview(seasons) {
  const list = seasons || [];
  const pending = list.filter((r) => r.status !== "approved");
  const approved = list.filter((r) => r.status === "approved");
  return [...groupRows(pending), ...groupRows(approved)];
}

/**
 * After `seasonId` is approved, the row the selection should move to: the next row
 * still `staged` after it in the current render order, else the nearest one before
 * it, else `null`. `order` is the rows as currently shown (before the repaint).
 */
export function nextStagedAfter(order, seasonId) {
  const ids = (order || []).map((r) => Number(r.id));
  const at = ids.indexOf(Number(seasonId));
  if (at === -1) return null;
  const staged = (i) => {
    const row = order[i];
    return row && Number(row.id) !== Number(seasonId) && row.status === "staged";
  };
  for (let i = at + 1; i < order.length; i += 1) if (staged(i)) return Number(order[i].id);
  for (let i = at - 1; i >= 0; i -= 1) if (staged(i)) return Number(order[i].id);
  return null;
}

/** Flatten `groupRows` back to render order — the order `j` / `k` walk. */
export function rowOrder(groups) {
  const out = [];
  for (const sp of groups) for (const t of sp.titles) for (const p of t.pages) out.push(...p.rows);
  return out;
}

/**
 * `id -> {mark, changes}` from `GET …/diff` (T-15a). `mark` is `new` | `changed` |
 * `same`; `changes` is the field-by-field `{from, to}` map on a changed row.
 */
export function diffIndex(diff) {
  const index = new Map();
  for (const row of diff?.rows || []) {
    index.set(Number(row.id), { mark: row.diff || row.mark || "same", changes: row.changes || {} });
  }
  return index;
}

/** `{from, to}` rendered as "old → new", dates and lists included. */
export function changeText(field, change) {
  const show = (v) =>
    Array.isArray(v) ? (v.length ? v.join(", ") : "none") : v === null || v === "" ? "none" : v;
  return `${field}: ${show(change?.from)} → ${show(change?.to)}`;
}

// ---- the publish gate's view model ------------------------------------------

/**
 * Why Publish is disabled, in §8.4's own order, as plain sentences. An empty array
 * means the gate is satisfied.
 */
export function gateReasons(publish) {
  const blockers = publish?.blockers || {};
  const reasons = [];
  const staged = (blockers.unreviewed_rows || []).length;
  if (staged) reasons.push(`${staged} row${staged === 1 ? "" : "s"} still staged`);
  const checks = blockers.checks || [];
  if (checks.length) {
    const names = checks.map((c) => `${c.check_name} (${c.status})`).join(", ");
    reasons.push(`${checks.length} unwaived check${checks.length === 1 ? "" : "s"}: ${names}`);
  }
  const disagreeing = (blockers.disagreeing_rows || []).length;
  if (disagreeing) reasons.push(`${disagreeing} row(s) where the two paths disagree, unreviewed`);
  return reasons;
}

/** A 409 `publish_blocked` body -> the lines the modal shows, naming check and row. */
export function blockedLines(error) {
  const detail = error?.detail || {};
  const lines = (detail.checks || []).map(
    (c) => `${c.section} · ${c.check_name}: expected ${c.expected ?? "—"}, got ${c.actual ?? "—"}`,
  );
  const rows = detail.rows || [];
  if (rows.length) {
    const shown = rows.slice(0, 12).join(", ");
    lines.push(`Rows not reviewed: ${shown}${rows.length > 12 ? `, +${rows.length - 12} more` : ""}`);
  }
  return lines;
}

/** Counts for the confirmation modal: what is about to be published. */
export function publishCounts(seasons) {
  const counts = { approved: 0, rejected: 0, staged: 0 };
  for (const s of seasons || []) {
    if (Object.hasOwn(counts, s.status)) counts[s.status] += 1;
  }
  return counts;
}

// ---- DOM --------------------------------------------------------------------

const el = (tag, cls, text) => {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined) node.textContent = text;
  return node;
};

const button = (cls, text, onClick, title) => {
  const b = el("button", cls, text);
  b.type = "button";
  if (title) b.title = title;
  if (onClick) b.addEventListener("click", onClick);
  return b;
};

/**
 * `/api/documents/<id>/pdf?page=N#page=N` — the embedded viewer's src (§8.4).
 *
 * The query parameter looks redundant and is not: Chrome's PDF viewer only honours
 * `#page=` on a real navigation, and assigning an `iframe.src` that differs from the
 * current one **only** in its fragment navigates nothing, so the viewer silently stays
 * on page 1 (verified in Chrome, T-15c). Changing the query makes it a new URL, so the
 * viewer reloads and lands on the page. The API ignores unknown query arguments.
 */
export function pdfPageHref(docId, page) {
  const n = Number(page) || 1;
  return `/api/documents/${docId}/pdf?page=${n}#page=${n}`;
}

/**
 * The waive dialog: a `<dialog>` with a required reason. `onWaive(reason)` runs only
 * when something was typed; an empty reason never reaches the server, and a server-side
 * `missing_reason` is shown in the same place. Returns `{dlg, open}`.
 */
export function createWaiveDialog(onWaive) {
  const dlg = el("dialog", "waive-dialog");
  dlg.id = "waive-dialog";
  const title = el("h3", null, "Waive a failing check");
  const what = el("p", "muted", "");
  const label = el("label", null, "Why is this check safe to override?");
  const input = el("textarea", "waive-reason");
  input.id = "waive-reason";
  input.setAttribute("rows", "3");
  input.setAttribute(
    "placeholder",
    "e.g. verified by hand against the printed page; the count differs because…",
  );
  label.setAttribute("for", "waive-reason");
  const err = el("p", "err");
  err.hidden = true;
  const bar = el("p", "row-actions");
  let current = null;

  const save = button("btn primary", "Waive", async () => {
    const reason = String(input.value || "").trim();
    if (!reason) {
      err.textContent = "A waiver needs a written reason — it is kept on the document forever.";
      err.hidden = false;
      input.focus();
      return;
    }
    try {
      await onWaive(current, reason);
      dlg.close();
    } catch (e) {
      err.textContent = e && e.message ? e.message : String(e);
      err.hidden = false;
    }
  });
  bar.append(button("btn", "Cancel", () => dlg.close()), save);
  dlg.append(title, what, label, input, err, bar);

  return {
    dlg,
    open(check) {
      current = check;
      what.textContent = `${sectionLabel(check.section)} · ${check.name} — expected ${
        check.expected ?? "—"
      }, got ${check.actual ?? "—"}.`;
      input.value = "";
      err.hidden = true;
      if (dlg.showModal) dlg.showModal();
      else dlg.setAttribute("open", "true");
      input.focus();
    },
  };
}

/** One check's line in the Accuracy panel. */
function checkLine(view, { onWaive, onJump } = {}) {
  const line = el("li", `check check-${view.status}`);
  line.dataset.check = String(view.id);
  line.dataset.status = view.status;
  const head = el("p", "check-head");
  // Neutral badge on purpose: the shared `b-error` palette is only 3.05:1 at this size,
  // so the status rides on the glyph and the colored left border (§4.4 never relies on
  // color alone anyway).
  head.append(badge("neutral", `${view.icon} ${view.status}`), " ", el("strong", null, view.name));
  if (view.waived) head.append(" ", badge("warn", "⚠ manually verified"));
  line.appendChild(head);

  if (view.expected !== null || view.actual !== null) {
    line.appendChild(
      textEl("p", `expected ${view.expected ?? "—"} · actual ${view.actual ?? "—"}`, "check-vs"),
    );
  }
  if (view.rows.length || view.pages.length) {
    const links = el("p", "check-links");
    if (view.rows.length) {
      links.append(
        button("link", `${view.rows.length} offending row(s)`, () => onJump && onJump(view.rows[0])),
      );
    }
    for (const page of view.pages.slice(0, 6)) {
      links.append(" ", button("link", `p. ${page}`, () => onJump && onJump(null, page)));
    }
    line.appendChild(links);
  }
  if (view.waived) {
    const who = [view.by && `by ${view.by}`, view.at && `on ${view.at}`].filter(Boolean).join(" ");
    line.appendChild(textEl("p", `Waived: “${view.reason}” ${who}`.trim(), "check-waiver"));
  } else if (view.blocks) {
    const bar = el("p", "check-links");
    bar.appendChild(button("btn small", "Waive…", () => onWaive && onWaive(view)));
    line.appendChild(bar);
  }
  return line;
}

/** The Accuracy panel (§8.4, §8.7): every check, grouped by section. */
export function renderAccuracyPanel(host, views, handlers = {}) {
  const body = el("div", "accuracy");
  if (!views.length) {
    body.appendChild(textEl("p", "This import recorded no checks.", "muted"));
  }
  for (const section of views) {
    const block = el("section", "accuracy-section");
    block.dataset.section = section.section;
    const h = el("h3", null, section.label);
    if (section.waived) h.append(" ", badge("warn", "⚠ manually verified"));
    if (section.blocking.length) h.append(" ", badge("neutral", "✗ blocks publish"));
    const counts = Object.entries(section.counts)
      .map(([status, n]) => `${CHECK_ICONS[status] || "?"} ${n} ${status}`)
      .join(" · ");
    block.append(h, textEl("p", counts || "no checks", "muted"));
    const list = el("ul", "check-list");
    for (const view of section.checks) list.appendChild(checkLine(view, handlers));
    block.appendChild(list);
    body.appendChild(block);
  }
  host.replaceChildren(body);
  return host;
}

/** The read-only cells of one row, in §8.4's column order. */
function rowSummary(row, { diff, waivedSection } = {}) {
  const wrap = el("div", "row-main");
  const units = el("p", "row-units");
  for (const unit of row.units || []) units.append(textEl("span", unit, "chip static"), " ");
  if (!(row.units || []).length) units.appendChild(textEl("span", "no units", "muted"));
  wrap.appendChild(units);

  const facts = el("p", "row-facts");
  facts.append(
    textEl("span", row.tag_type_id || "—", "fact"),
    textEl("span", row.weapon || "—", "fact"),
    textEl("span", row.sex || "—", "fact"),
    textEl("span", `${row.start_date} → ${row.end_date}`, "fact dates"),
  );
  if (row.hunt_number) facts.append(textEl("span", `hunt no. ${row.hunt_number}`, "fact"));
  if (row.zone) facts.append(textEl("span", `zone ${row.zone}`, "fact"));
  wrap.appendChild(facts);

  const marks = el("p", "row-badges");
  for (const code of row.restrictions || []) marks.append(badge("restricted", restrictionLabel(code)), " ");
  const conf = badge("neutral", `confidence ${Number(row.confidence ?? 1).toFixed(2)}`);
  // The §4.1 confidence ramp is a class, never a literal color (theme.css owns --sc1…--sc5).
  conf.className = `badge b-neutral conf ${confidenceClass(row.confidence)}`;
  marks.append(conf, " ");
  if (!row.extract_agree) marks.append(badge("warn", "paths disagree"), " ");
  if (waivedSection) marks.append(badge("warn", "⚠ manually verified"), " ");
  if (diff) {
    marks.append(badge(diff.mark === "new" ? "ok" : diff.mark === "changed" ? "warn" : "neutral", diff.mark), " ");
  }
  wrap.appendChild(marks);

  if (diff && diff.mark === "changed") {
    const changes = Object.entries(diff.changes || {}).map(([field, change]) =>
      textEl("li", changeText(field, change), "change"),
    );
    if (changes.length) {
      const ul = el("ul", "row-changes");
      ul.append(...changes);
      wrap.appendChild(ul);
    }
  }
  if (row.source_text) wrap.appendChild(textEl("p", row.source_text, "row-source"));
  return wrap;
}

/**
 * The inline editor for one row: unit chips with autocomplete, date inputs, selects for
 * tag/weapon/sex and a restriction multi-select. `onSave(patch)` gets only the fields
 * that changed, so an untouched field is never rewritten.
 *
 * `create: true` (T-16's "+ Add season") adds the columns a new row needs and that a
 * parsed row already carries — license year, species, hunt class, hunt number, zone,
 * notes and source page. The diff-only contract is unchanged: a blank optional field
 * is simply not sent, and the server names anything still missing.
 */
export function createRowEditor(
  row,
  { units = [], tagTypes = [], species = [], create = false, onSave, onCancel } = {},
) {
  const form = el("form", "row-editor");
  form.setAttribute("novalidate", "true");
  const fields = new Map();

  const field = (label, control, hint) => {
    const wrap = el("label", "row-field");
    wrap.append(textEl("span", label, "row-field-label"), control);
    if (hint) wrap.appendChild(textEl("span", hint, "muted"));
    return wrap;
  };

  const unitInput = el("input", "unit-input");
  unitInput.type = "text";
  unitInput.value = (row.units || []).join(", ");
  unitInput.setAttribute("list", "unit-options");
  unitInput.setAttribute("autocomplete", "off");
  fields.set("units", () =>
    unitInput.value
      .split(",")
      .map((u) => u.trim())
      .filter(Boolean),
  );

  const datalist = el("datalist");
  datalist.id = "unit-options";
  for (const unit of units) {
    const option = el("option");
    option.value = String(unit.id ?? unit);
    datalist.appendChild(option);
  }

  const dateInput = (name) => {
    const input = el("input", "date-input");
    input.type = "date";
    input.value = row[name] || "";
    fields.set(name, () => input.value);
    return input;
  };

  const select = (name, options) => {
    const sel = el("select", "row-select");
    for (const [value, label] of options) {
      const option = el("option", null, label);
      option.value = value;
      if (String(row[name]) === value) option.setAttribute("selected", "true");
      sel.appendChild(option);
    }
    // A new row has no value yet: a select must still read back something valid
    // (assigning an unmatched value blanks the selection in a real browser).
    sel.value = row[name] || (create && options.length ? options[0][0] : "");
    fields.set(name, () => sel.value);
    return sel;
  };

  const restrictions = el("select", "row-select restrictions");
  restrictions.setAttribute("multiple", "true");
  const held = new Set(row.restrictions || []);
  for (const [value, label] of RESTRICTIONS) {
    const option = el("option", null, label);
    option.value = value;
    option.selected = held.has(value);
    if (held.has(value)) option.setAttribute("selected", "true");
    restrictions.appendChild(option);
  }
  fields.set("restrictions", () =>
    [...restrictions.children].filter((o) => o.selected).map((o) => o.value),
  );

  const number = (name, label, attrs = {}) => {
    const input = el("input", "num-input");
    input.type = "number";
    input.value = row[name] == null ? "" : String(row[name]);
    for (const [k, v] of Object.entries(attrs)) input.setAttribute(k, String(v));
    fields.set(name, () => (input.value === "" ? null : Number(input.value)));
    return field(label, input);
  };

  const line = (name, label, hint) => {
    const input = el("input", "text-input");
    input.type = "text";
    input.value = row[name] == null ? "" : String(row[name]);
    fields.set(name, () => input.value.trim() || null);
    return field(label, input, hint);
  };

  if (create) {
    form.append(
      number("license_year", "License year", { min: 1900, max: 2100, required: "true" }),
      field(
        "Species",
        select(
          "species_id",
          species.map((s) => [s.id, s.label || s.id]),
        ),
      ),
      field("Hunt class", select("hunt_class", HUNT_CLASSES)),
    );
  }

  form.append(
    field("Units", unitInput, "comma separated, autocompleted from the seeded GMUs"),
    datalist,
    field("Start", dateInput("start_date")),
    field("End", dateInput("end_date")),
    field("Tag", select("tag_type_id", tagTypes.map((t) => [t.id, t.label || t.id]))),
    field("Weapon", select("weapon", WEAPONS)),
    field("Sex", select("sex", SEXES)),
    field("Restrictions", restrictions, "§5.1 codes only"),
  );

  if (create) {
    form.append(
      line("hunt_number", "Hunt number", "controlled hunts only"),
      line("zone", "Zone", "elk A/B seasons only"),
      line("notes", "Notes"),
      number("source_page", "Source page", { min: 1 }),
    );
  }

  const err = el("p", "err");
  err.hidden = true;
  const bar = el("p", "row-actions");
  const save = button("btn primary", "Save", () => submit());
  bar.append(button("btn", "Cancel", () => onCancel && onCancel()), save);
  form.append(err, bar);

  /**
   * Only the fields whose value actually moved — except in `create` mode, where there
   * is no row yet, so every field that carries a value is sent (a prefilled year must
   * reach the server, not look "unchanged").
   */
  function patch() {
    const out = {};
    for (const [name, read] of fields) {
      const next = read();
      if (create) {
        const empty = Array.isArray(next) ? !next.length : next === null || next === "";
        if (!empty) out[name] = next;
        continue;
      }
      const before = row[name];
      const same = Array.isArray(next)
        ? JSON.stringify([...next].sort()) === JSON.stringify([...(before || [])].sort())
        : String(next ?? "") === String(before ?? "");
      if (!same) out[name] = next;
    }
    return out;
  }

  async function submit() {
    const body = patch();
    if (!Object.keys(body).length) {
      onCancel && onCancel();
      return;
    }
    try {
      save.disabled = true;
      await onSave(body);
    } catch (e) {
      err.textContent = e && e.message ? e.message : String(e);
      err.hidden = false;
      save.disabled = false;
    }
  }

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    submit();
  });
  form.patch = patch;
  return form;
}

//: Row status -> badge kind. `rejected` stays neutral (its red left border carries the
//: signal); the shared `b-error` badge misses WCAG AA at badge size.
const STATUS_KINDS = { staged: "warn", approved: "ok", rejected: "neutral" };

/**
 * The whole review screen. `options` injects every network call, so the node twin and
 * the browser share one implementation:
 * `{load, loadDiff, loadUnits, loadTagTypes, review, waive, approveAll, publish}`.
 * Returns `{el, refresh, select, rows, keydown, ready}`.
 */
export function createReviewScreen(docId, options = {}) {
  const {
    load = () => getJSON(`/api/documents/${docId}`),
    loadDiff = () => getJSON(`/api/documents/${docId}/diff`),
    loadUnits = () => getJSON("/api/units"),
    loadTagTypes = () => getJSON("/api/meta"),
    review = (sid, body) => putJSON(`/api/documents/${docId}/seasons/${sid}`, body),
    waive = (cid, reason) => postJSON(`/api/documents/${docId}/checks/${cid}/waive`, { reason }),
    approveAll = () => postJSON(`/api/documents/${docId}/approve-all`, {}),
    publish = () => postJSON(`/api/documents/${docId}/publish`, {}),
    notify = toast,
  } = options;

  const root = el("div", "review");
  root.id = "regs-review";
  root.setAttribute("tabindex", "-1");

  const header = el("div", "review-head");
  const accuracyHost = el("div", "accuracy-host");
  const rowsHost = el("div", "review-rows");
  rowsHost.id = "review-rows";
  const removedHost = el("div", "review-removed");
  const gateHost = el("div", "publish-bar");
  const viewer = el("iframe", "pdf-viewer");
  viewer.id = "review-pdf";
  viewer.setAttribute("title", "The source page for the selected row");
  viewer.setAttribute("loading", "lazy");

  const accuracyCard = card("Accuracy", accuracyHost);
  accuracyCard.id = "accuracy-panel";
  const rowsCard = card("Staged rows", rowsHost, removedHost);
  rowsCard.id = "review-rows-card";
  const split = el("div", "review-split");
  split.append(rowsCard, viewer);

  root.append(header, accuracyCard, gateHost, split);

  let state = { document: null, seasons: [], checks: [], sections: [], publish: null };
  let diffs = new Map();
  let removed = [];
  let units = [];
  let tagTypes = [];
  let order = [];
  let selectedId = null;
  let editing = null;

  const waiver = createWaiveDialog(async (check, reason) => {
    await waive(check.id, reason);
    await refresh();
    notify(`Waived ${check.name}.`, "warn");
  });
  root.appendChild(waiver.dlg);

  const confirm = el("dialog", "publish-dialog");
  confirm.id = "publish-dialog";
  root.appendChild(confirm);

  // ---- painting -------------------------------------------------------------

  function paintHeader() {
    const doc = state.document || {};
    header.replaceChildren();
    const back = el("a", "link");
    back.href = "#/regs";
    back.textContent = "← All documents";
    const title = el("h2", null, doc.title || `Document ${docId}`);
    const meta = textEl(
      "p",
      `${doc.status || "—"} · ${doc.license_year || "—"} · ${state.seasons.length} rows`,
      "muted",
    );
    header.append(back, title, meta);
  }

  function paintAccuracy() {
    renderAccuracyPanel(accuracyHost, sectionViews(state.checks, state.sections), {
      onWaive: (view) => waiver.open(view),
      onJump: (sid, page) => {
        if (sid) select(sid);
        else if (page) showPage(page);
      },
    });
  }

  function waivedSections() {
    return new Set((state.sections || []).filter((s) => s.waived).map((s) => s.section || ""));
  }

  function rowElement(row, waived) {
    const item = el("article", `review-row status-${row.status}`);
    item.dataset.seasonId = String(row.id);
    item.dataset.page = String(row.source_page || "");
    item.setAttribute("tabindex", "-1");
    // `aria-current`, not `aria-selected`: the rows are grouped by species / table /
    // page, so they are not a flat listbox, and `aria-selected` is not allowed outside
    // one (Lighthouse `aria-allowed-attr`, T-15c).
    item.setAttribute("aria-current", "false");

    const head = el("p", "row-head");
    head.append(badge(STATUS_KINDS[row.status] || "neutral", row.status));
    if (row.source_page) head.append(" ", textEl("span", `p. ${row.source_page}`, "muted"));
    item.appendChild(head);

    if (editing === row.id) {
      item.appendChild(
        createRowEditor(row, {
          units,
          tagTypes,
          onSave: async (body) => {
            const result = await review(row.id, body);
            editing = null;
            await refresh();
            notify(`Saved ${Object.keys(body).join(", ")}.`, "success");
            return result;
          },
          onCancel: () => {
            editing = null;
            paintRows();
          },
        }),
      );
    } else {
      item.appendChild(rowSummary(row, { diff: diffs.get(Number(row.id)), waivedSection: waived }));
      const actions = el("p", "row-actions");
      actions.append(
        button("btn small", "Approve (a)", () => decide(row.id, "approved")),
        button("btn small", "Reject (r)", () => decide(row.id, "rejected")),
        button("btn small", "Edit (e)", () => startEdit(row.id)),
      );
      item.appendChild(actions);
    }
    item.addEventListener("click", () => select(row.id, { focus: false }));
    item.addEventListener("focus", () => select(row.id, { focus: false }));
    return item;
  }

  function paintRows() {
    const groups = groupRowsForReview(state.seasons);
    order = rowOrder(groups);
    const waived = waivedSections();
    const body = el("div", "row-groups");
    for (const species of groups) {
      const block = el("section", "row-species");
      block.appendChild(el("h3", null, species.species));
      for (const title of species.titles) {
        const titleBlock = el("section", "row-table");
        titleBlock.appendChild(el("h4", null, title.title));
        for (const page of title.pages) {
          const pageBlock = el("section", "row-page");
          const heading = el("h5", null, `Page ${page.page || "—"}`);
          heading.appendChild(
            button("link", "show page", () => showPage(page.page), "Sync the PDF viewer"),
          );
          pageBlock.appendChild(heading);
          for (const row of page.rows) {
            pageBlock.appendChild(rowElement(row, waived.has(row.section || "")));
          }
          titleBlock.appendChild(pageBlock);
        }
        block.appendChild(titleBlock);
      }
      body.appendChild(block);
    }
    rowsHost.replaceChildren(body);
    paintRemoved();
    if (selectedId !== null && !order.some((r) => Number(r.id) === Number(selectedId))) {
      selectedId = null;
    }
    if (selectedId === null && order.length) selectedId = Number(order[0].id);
    markSelection();
  }

  function paintRemoved() {
    removedHost.replaceChildren();
    if (!removed.length) return;
    const block = el("section", "removed");
    block.appendChild(el("h3", null, `Removed since last year (${removed.length})`));
    const list = el("ul", "removed-list");
    for (const row of removed.slice(0, 50)) {
      list.appendChild(
        textEl(
          "li",
          `${row.species_id} · ${row.tag_type_id} · ${(row.units || []).join(", ")} · ${row.start_date} → ${row.end_date}`,
          "removed-row",
        ),
      );
    }
    block.appendChild(list);
    if (removed.length > 50) {
      block.appendChild(textEl("p", `…and ${removed.length - 50} more.`, "muted"));
    }
    removedHost.appendChild(block);
  }

  function paintGate() {
    const reasons = gateReasons(state.publish);
    const blocked = Boolean(state.publish && state.publish.blocked);
    gateHost.replaceChildren();
    const bar = el("div", "gate");
    const approve = button("btn", "Approve all passing rows", async () => {
      const result = await approveAll();
      await refresh();
      const n = (result.approved || []).length;
      notify(
        n
          ? `Approved ${n} row(s) whose section passes every check.`
          : "No row qualified: every staged row is in a section with a failing or waived check.",
        n ? "success" : "warn",
      );
    });
    const publishBtn = button("btn primary", "Publish", () => openConfirm());
    publishBtn.id = "publish-button";
    publishBtn.disabled = blocked;
    if (blocked) publishBtn.title = reasons.join(" · ");
    bar.append(approve, publishBtn);
    gateHost.appendChild(bar);
    const why = el("ul", "gate-reasons");
    why.id = "gate-reasons";
    for (const reason of reasons) why.appendChild(textEl("li", reason, "gate-reason"));
    if (!reasons.length) {
      why.appendChild(textEl("li", "The §8.4 gate is satisfied — this document can publish.", "ok"));
    }
    gateHost.appendChild(why);
  }

  function openConfirm() {
    const counts = publishCounts(state.seasons);
    confirm.replaceChildren();
    const err = el("div", "err");
    err.hidden = true;
    confirm.append(
      el("h3", null, "Publish this document?"),
      textEl(
        "p",
        `${counts.approved} approved row(s) become live; ${counts.rejected} rejected and ${counts.staged} staged row(s) do not.`,
        "muted",
      ),
      textEl("p", "Matching rows of an older document for the same year are superseded (§8.5).", "muted"),
      err,
    );
    const bar = el("p", "row-actions");
    const go = button("btn primary", "Publish", async () => {
      try {
        go.disabled = true;
        const result = await publish();
        confirm.close();
        await refresh();
        notify(`Published ${(result.published || []).length} row(s).`, "success");
      } catch (e) {
        go.disabled = false;
        err.replaceChildren(textEl("p", e.message || String(e), "err"));
        for (const line of blockedLines(e)) err.appendChild(textEl("p", line, "err-line"));
        err.hidden = false;
        await refresh();
      }
    });
    bar.append(button("btn", "Cancel", () => confirm.close()), go);
    confirm.appendChild(bar);
    if (confirm.showModal) confirm.showModal();
    else confirm.setAttribute("open", "true");
  }

  // ---- selection, the PDF viewer and the keyboard ---------------------------

  function markSelection() {
    for (const node of rowsHost.querySelectorAll("article")) {
      const on = node.dataset.seasonId === String(selectedId);
      node.setAttribute("aria-current", on ? "true" : "false");
      node.dataset.selected = on ? "1" : "";
    }
    const row = order.find((r) => Number(r.id) === Number(selectedId));
    if (row) showPage(row.source_page);
    root.dataset.selectedRow = selectedId === null ? "" : String(selectedId);
  }

  function showPage(page) {
    const href = pdfPageHref(docId, page);
    if (viewer.getAttribute("src") !== href) viewer.setAttribute("src", href);
    root.dataset.pdfPage = String(Number(page) || 1);
  }

  function select(seasonId, { focus = true } = {}) {
    if (seasonId === null || seasonId === undefined) return;
    selectedId = Number(seasonId);
    markSelection();
    const node = [...rowsHost.querySelectorAll("article")].find(
      (n) => n.dataset.seasonId === String(selectedId),
    );
    if (node) {
      if (node.scrollIntoView) node.scrollIntoView({ block: "nearest" });
      if (focus) node.focus();
    }
  }

  function move(step) {
    if (!order.length) return;
    const at = order.findIndex((r) => Number(r.id) === Number(selectedId));
    const next = at === -1 ? 0 : Math.min(Math.max(at + step, 0), order.length - 1);
    select(order[next].id);
  }

  async function decide(seasonId, status) {
    try {
      // An approved row drops to the bottom of the list on repaint. Move the selection
      // to the next staged row first, so focus never follows the approved row there.
      if (status === "approved") {
        const next = nextStagedAfter(order, seasonId);
        selectedId = next;
      }
      await review(seasonId, { status });
      await refresh();
      notify(`Row ${seasonId} ${status}.`, status === "approved" ? "success" : "warn");
    } catch (e) {
      notify(e.message || String(e), "error");
    }
  }

  function startEdit(seasonId) {
    editing = Number(seasonId);
    selectedId = Number(seasonId);
    paintRows();
    const node = [...rowsHost.querySelectorAll("input")][0];
    if (node) node.focus();
  }

  /** `j`/`k` move, `a` approves, `r` rejects, `e` edits — never inside a field. */
  function keydown(event) {
    const tag = String(event?.target?.tagName || "").toLowerCase();
    if (["input", "textarea", "select", "option"].includes(tag)) return false;
    if (event.metaKey || event.ctrlKey || event.altKey) return false;
    const key = event.key;
    if (key === "j") move(1);
    else if (key === "k") move(-1);
    else if (key === "a") decide(selectedId, "approved");
    else if (key === "r") decide(selectedId, "rejected");
    else if (key === "e") startEdit(selectedId);
    else return false;
    if (event.preventDefault) event.preventDefault();
    return true;
  }
  root.addEventListener("keydown", keydown);

  // ---- loading ---------------------------------------------------------------

  async function refresh() {
    const body = await load();
    state = {
      document: body.document || null,
      seasons: body.seasons || [],
      checks: body.checks || [],
      sections: body.sections || [],
      publish: body.publish || null,
    };
    // Every repaint replaces the row elements, which drops focus to <body> and would
    // silently kill the keyboard shortcuts right after the first `a` or `r`. Remember
    // whether the user was driving from the keyboard and hand focus back afterwards.
    const hadFocus = root === document.activeElement || root.contains(document.activeElement);
    paintHeader();
    paintAccuracy();
    paintRows();
    paintGate();
    restoreFocus(hadFocus);
    document.body.dataset.reviewReady = "1";
    return state;
  }

  /** Put focus back on the selected row after a repaint (never over an open editor). */
  function restoreFocus(had) {
    if (!had || editing !== null) return;
    const node = [...rowsHost.querySelectorAll("article")].find(
      (n) => n.dataset.seasonId === String(selectedId),
    );
    // preventScroll: an approved row leaves the view when it drops to the bottom, and
    // focusing the next row must not drag the page down to where it landed.
    if (node && node.focus) node.focus({ preventScroll: true });
  }

  async function loadExtras() {
    const [unitBody, metaBody, diffBody] = await Promise.all([
      loadUnits().catch(() => null),
      loadTagTypes().catch(() => null),
      loadDiff().catch(() => null),
    ]);
    units = (unitBody && unitBody.units) || [];
    tagTypes = (metaBody && (metaBody.tag_types || metaBody.tagTypes)) || [];
    if (diffBody) {
      diffs = diffIndex(diffBody);
      removed = diffBody.removed || [];
    }
  }

  const ready = (async () => {
    await loadExtras();
    await refresh();
  })().catch((err) => {
    rowsHost.replaceChildren(textEl("p", `This document failed to load: ${err.message || err}`, "err"));
    document.body.dataset.reviewReady = "error";
  });

  return {
    el: root,
    refresh,
    select,
    keydown,
    ready,
    rows: () => order,
    state: () => state,
    selected: () => selectedId,
    editing: () => editing,
  };
}
