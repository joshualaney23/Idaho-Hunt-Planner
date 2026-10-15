// Results table (T-08, DESIGN.md §4.2): the units that aren't closed for the current
// filters, sortable by Unit (natural: 4, 4A, 5, 10, 10A), Status, Days open, Weapons and
// Earliest start. Each row lists the overlapping seasons with weapon and restriction
// badges (labels from /api/meta) and a `p.N` link to the source PDF page. Hovering or
// focusing a row highlights the unit on the map; the unit button opens the Unit Brief.
// Also: CSV export of the same rows.

import { textEl } from "./ui.js";
import { seasonLine } from "./map.js";
import { fmtShort, parseISO } from "./dates.js";

export const STATUS_ORDER = { open: 0, restricted: 1, untagged: 2, closed: 3 };
export const STATUS_TEXT = {
  open: "open",
  restricted: "restricted",
  untagged: "no tag held",
  closed: "closed",
};
export const WEAPON_ORDER = ["any", "archery", "muzzleloader", "short_range", "shotgun", "falconry"];
export const WEAPON_TEXT = {
  any: "any",
  archery: "archery",
  muzzleloader: "muzzleloader",
  short_range: "short-range",
  shotgun: "shotgun",
  falconry: "falconry",
};
/** Short forms for the printed table, where every row must stay on one line. */
export const WEAPON_SHORT = {
  any: "any",
  archery: "arch",
  muzzleloader: "muzz",
  short_range: "SR",
  shotgun: "shot",
  falconry: "falc",
};

/** Digit runs compare as numbers, other runs as text, and a prefix sorts first. */
function tokens(s) {
  return (String(s).toUpperCase().match(/\d+|\D+/g) || []).map((t) =>
    /^\d/.test(t) ? Number(t) : t,
  );
}

/** Natural unit order: 1, 2, 4, 4A, 5, 10, 10A, 11, ... YNP. */
export function naturalCompare(a, b) {
  const ta = tokens(a);
  const tb = tokens(b);
  for (let i = 0; i < Math.min(ta.length, tb.length); i++) {
    const x = ta[i];
    const y = tb[i];
    if (x === y) continue;
    const xn = typeof x === "number";
    const yn = typeof y === "number";
    if (xn && yn) return x - y;
    if (xn !== yn) return xn ? -1 : 1; // numbers before letters
    return x < y ? -1 : 1;
  }
  return ta.length - tb.length;
}

/** One row per unit whose status isn't closed, in natural unit order. */
export function resultRows(availability) {
  const rows = [];
  for (const [unit, u] of Object.entries(availability?.units || {})) {
    if (!u || !u.status || u.status === "closed") continue;
    const seasons = u.seasons || [];
    const weapons = WEAPON_ORDER.filter((w) => seasons.some((s) => s.weapon === w));
    for (const s of seasons) if (!weapons.includes(s.weapon)) weapons.push(s.weapon);
    const earliest = seasons.reduce((m, s) => (m === null || s.start < m ? s.start : m), null);
    rows.push({
      unit,
      status: u.status,
      reasons: u.reasons || [],
      daysOpen: u.days_open ?? 0,
      weapons,
      earliest,
      seasons,
    });
  }
  return rows.sort((a, b) => naturalCompare(a.unit, b.unit));
}

const weaponRank = (w) => {
  const i = WEAPON_ORDER.indexOf(w);
  return i === -1 ? WEAPON_ORDER.length : i;
};

/** Per-column comparators (ascending). */
export const COMPARE = {
  unit: (a, b) => naturalCompare(a.unit, b.unit),
  status: (a, b) => (STATUS_ORDER[a.status] ?? 9) - (STATUS_ORDER[b.status] ?? 9),
  days: (a, b) => a.daysOpen - b.daysOpen,
  weapons: (a, b) => {
    for (let i = 0; i < Math.min(a.weapons.length, b.weapons.length); i++) {
      const d = weaponRank(a.weapons[i]) - weaponRank(b.weapons[i]);
      if (d) return d;
    }
    return a.weapons.length - b.weapons.length;
  },
  // Units without a dated season (none today) sort last either way up.
  earliest: (a, b) => (a.earliest || "9999").localeCompare(b.earliest || "9999"),
};

/**
 * Stable sort by one column: rows that tie keep the order they arrived in (so sorting
 * by Status after Unit keeps each status group in unit order). `dir` flips only the
 * primary key, never the tie order. Returns a new array.
 */
export function sortRows(rows, key = "unit", dir = "asc") {
  const cmp = COMPARE[key] || COMPARE.unit;
  const sign = dir === "desc" ? -1 : 1;
  return rows
    .map((row, i) => [row, i])
    .sort(([a, i], [b, j]) => sign * cmp(a, b) || i - j)
    .map(([row]) => row);
}

export const COLUMNS = [
  { key: "unit", label: "Unit" },
  { key: "status", label: "Status" },
  { key: "days", label: "Days open", num: true },
  { key: "weapons", label: "Weapons" },
  { key: "earliest", label: "Earliest start" },
  { key: null, label: "Seasons overlapping your dates" },
];

// ---- CSV -------------------------------------------------------------------------
/** RFC 4180 cell: quoted when it holds a comma, quote or line break. */
export function csvCell(v) {
  const s = v === null || v === undefined ? "" : String(v);
  return /[",\r\n]/.test(s) || /^\s|\s$/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

/** The rows as CSV text (CRLF line ends), in the order shown. */
export function toCSV(rows, labels = {}) {
  const label = (code) =>
    labels.restrictions?.[code]?.label || labels.reasons?.[code]?.label || code;
  const head = [
    "unit",
    "status",
    "days_open",
    "weapons",
    "earliest_start",
    "reasons",
    "seasons",
    "source_pages",
  ];
  const lines = [head.join(",")];
  for (const r of rows) {
    const seasons = r.seasons.map((s) =>
      [
        WEAPON_TEXT[s.weapon] || s.weapon,
        s.sex && s.sex !== "either" ? s.sex : null,
        `${s.start}..${s.end}`,
        ...(s.reasons || []).map(label),
        s.source_page ? `p.${s.source_page}` : null,
      ]
        .filter(Boolean)
        .join(" "),
    );
    const pages = [...new Set(r.seasons.map((s) => s.source_page).filter(Boolean))];
    lines.push(
      [
        r.unit,
        r.status,
        r.daysOpen,
        r.weapons.map((w) => WEAPON_TEXT[w] || w).join(" "),
        r.earliest || "",
        r.reasons.map(label).join("; "),
        seasons.join("; "),
        pages.join(" "),
      ]
        .map(csvCell)
        .join(","),
    );
  }
  return `${lines.join("\r\n")}\r\n`;
}

/** `ihp-deer-2026-10-16_2026-10-18.csv` */
export function csvFilename(state) {
  const safe = (s) => String(s).replace(/[^\w.-]+/g, "-");
  return `ihp-${safe(state.species)}-${state.start}_${state.end}.csv`;
}

/** Save `text` as a file through a temporary object URL. */
export function downloadText(text, filename, type = "text/csv;charset=utf-8") {
  const url = URL.createObjectURL(new Blob([text], { type }));
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

// ---- table -----------------------------------------------------------------------
/**
 * Mount the results table in `container`. Options: `labels` ({restrictions, reasons}),
 * `onHover(unit|null)`, `onOpen(unit, button)`. Returns
 * `{update(availability), rows(), sort(key, dir?), sortState()}`.
 */
export function createResults(container, { labels = {}, onHover = () => {}, onOpen = () => {} } = {}) {
  const table = document.createElement("table");
  table.className = "results";
  table.id = "results-table";
  const thead = document.createElement("thead");
  const headRow = document.createElement("tr");
  const tbody = document.createElement("tbody");
  thead.appendChild(headRow);
  table.append(thead, tbody);
  container.replaceChildren(table);

  let base = [];
  let shown = [];
  let sortKey = "unit";
  let sortDir = "asc";
  let hovered = null;

  const ths = {};
  for (const col of COLUMNS) {
    const th = document.createElement("th");
    th.setAttribute("scope", "col");
    if (col.num) th.className = "num";
    if (!col.key) {
      th.textContent = col.label;
      th.className = "seasons-col";
    } else {
      const btn = textEl("button", col.label, "sort");
      btn.type = "button";
      btn.dataset.sort = col.key;
      btn.addEventListener("click", () => {
        const dir = sortKey === col.key && sortDir === "asc" ? "desc" : "asc";
        sort(col.key, dir);
      });
      th.appendChild(btn);
      ths[col.key] = th;
    }
    headRow.appendChild(th);
  }

  function paintHeads() {
    for (const [key, th] of Object.entries(ths)) {
      if (key === sortKey) th.setAttribute("aria-sort", sortDir === "asc" ? "ascending" : "descending");
      else th.setAttribute("aria-sort", "none");
    }
  }

  function setHover(unit) {
    if (unit === hovered) return;
    hovered = unit;
    onHover(unit);
  }

  function rowEl(r) {
    const tr = document.createElement("tr");
    tr.dataset.unit = r.unit;
    tr.className = `st-${r.status}`;
    const unitTd = document.createElement("td");
    const btn = textEl("button", r.unit, "u ulink");
    btn.type = "button";
    btn.setAttribute("aria-label", `Unit ${r.unit} brief`);
    btn.addEventListener("click", () => onOpen(r.unit, btn));
    unitTd.appendChild(btn);
    const stTd = document.createElement("td");
    stTd.appendChild(textEl("span", STATUS_TEXT[r.status] || r.status, `st ${r.status}`));
    const daysTd = textEl("td", String(r.daysOpen), "num");
    const wTd = document.createElement("td");
    wTd.className = "weapons";
    const full = r.weapons.map((w) => WEAPON_TEXT[w] || w).join(", ");
    wTd.append(
      textEl("span", full, "wl"),
      textEl("span", r.weapons.map((w) => WEAPON_SHORT[w] || w).join(" "), "ws"),
    );
    wTd.title = full;
    const eTd = textEl("td", r.earliest ? fmtShort(parseISO(r.earliest)) : "—", "date");
    if (r.earliest) eTd.title = r.earliest;
    const sTd = document.createElement("td");
    sTd.className = "seasons-col";
    for (const s of r.seasons) sTd.appendChild(seasonLine(s, labels, { link: true }));
    tr.append(unitTd, stTd, daysTd, wTd, eTd, sTd);
    tr.addEventListener("mouseenter", () => setHover(r.unit));
    tr.addEventListener("mouseleave", () => setHover(null));
    tr.addEventListener("focusin", () => setHover(r.unit));
    tr.addEventListener("focusout", () => setHover(null));
    return tr;
  }

  function paintBody() {
    if (!shown.length) {
      const tr = document.createElement("tr");
      const td = textEl("td", "Nothing open for these filters.", "muted");
      td.colSpan = COLUMNS.length;
      td.setAttribute("colspan", String(COLUMNS.length));
      tr.appendChild(td);
      tbody.replaceChildren(tr);
    } else {
      tbody.replaceChildren(...shown.map(rowEl));
    }
    container.dataset.rows = String(shown.length);
  }

  function sort(key, dir = "asc") {
    sortKey = COMPARE[key] ? key : "unit";
    sortDir = dir === "desc" ? "desc" : "asc";
    // Stable on top of what's on screen now, so the previous sort breaks ties.
    shown = sortRows(shown, sortKey, sortDir);
    paintHeads();
    paintBody();
  }

  function update(availability) {
    base = resultRows(availability);
    shown = sortRows(base, sortKey, sortDir);
    if (hovered && !base.some((r) => r.unit === hovered)) setHover(null);
    paintHeads();
    paintBody();
  }

  paintHeads();
  paintBody();
  return {
    update,
    sort,
    rows: () => shown.slice(),
    sortState: () => ({ key: sortKey, dir: sortDir }),
    table,
  };
}
