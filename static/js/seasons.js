// Season Table (T-16, DESIGN.md §6 `/api/seasons` · §12 T-16) — the `#/seasons` tab.
//
// Three parts:
//   1. a filter bar (year, species, tag type, weapon, status and a unit *search*) whose
//      values go straight into the `/api/seasons` query string, so a shared URL and the
//      CSV export see exactly the rows on screen;
//   2. a sortable table of seasons — clicking a row highlights its units on the map and
//      switches to the map tab (UNITS_HIGHLIGHT), and manual rows carry Edit/Delete;
//   3. the manual editor: "+ Add season" opens review.js's row editor in `create` mode
//      (`POST /api/seasons`, published at once under a `manual` document) and CSV
//      export / import (`GET /api/export.csv`, `POST /api/import.csv`, staged rows).
//
// Everything that decides *what* is shown is a pure function (`sortSeasons`,
// `queryString`, `exportUrl`, `rowCells`, `importSummary`, `csvErrorLines`), so
// seasons.test.mjs covers the semantics without a browser.

import { deleteJSON, getJSON, postFile, postJSON, putJSON } from "./api.js";
import { badge, card, textEl, toast } from "./ui.js";
import { naturalCompare } from "./results.js";
import { HUNT_CLASSES, createRowEditor, restrictionLabel } from "./review.js";

export { HUNT_CLASSES };

/** Fired when a row is clicked: `{units, label}`. main.js paints them on the map. */
export const UNITS_HIGHLIGHT = "ihp:units-highlight";

/** `seasons.status` values the table can ask for, plus the `all` catch-all. */
export const STATUSES = [
  ["published", "Published"],
  ["staged", "Staged (awaiting review)"],
  ["approved", "Approved"],
  ["rejected", "Rejected"],
  ["superseded", "Superseded"],
  ["all", "All statuses"],
];

/** Filter keys, in the order the bar lays them out. `unit` is a prefix search. */
export const FILTER_KEYS = ["year", "species", "tag_type", "weapon", "status", "unit"];

export const DEFAULT_FILTERS = { status: "published" };

const STATUS_KINDS = {
  published: "ok",
  staged: "warn",
  approved: "ok",
  rejected: "neutral",
  superseded: "neutral",
};

const text = (v) => (v == null || v === "" ? "—" : String(v));

/** `2026-10-10` + `2026-10-24` -> `Oct 10 – Oct 24` (the table is year-filtered). */
export function dateRange(row) {
  const fmt = (iso) => {
    const [, m, d] = String(iso || "").split("-");
    const months = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split(" ");
    const month = months[Number(m) - 1];
    return month ? `${month} ${Number(d)}` : String(iso || "");
  };
  if (!row.start_date) return "—";
  return row.start_date === row.end_date
    ? fmt(row.start_date)
    : `${fmt(row.start_date)} – ${fmt(row.end_date)}`;
}

/** The sortable columns, in table order. */
export const COLUMNS = [
  { key: "species", label: "Species" },
  { key: "tag", label: "Tag" },
  { key: "hunt", label: "Hunt" },
  { key: "weapon", label: "Weapon" },
  { key: "sex", label: "Sex" },
  { key: "start", label: "Dates" },
  { key: "units", label: "Units" },
  { key: "restrictions", label: "Restrictions" },
  { key: "status", label: "Status" },
];

const firstUnit = (row) => (row.units && row.units.length ? row.units[0] : "\uffff");

/** Per-column comparators (ascending). */
export const COMPARE = {
  species: (a, b) => String(a.species_label || a.species_id).localeCompare(String(b.species_label || b.species_id)),
  tag: (a, b) => String(a.tag_type_label || a.tag_type_id).localeCompare(String(b.tag_type_label || b.tag_type_id)),
  hunt: (a, b) => naturalCompare(a.hunt_number || "\uffff", b.hunt_number || "\uffff"),
  weapon: (a, b) => String(a.weapon).localeCompare(String(b.weapon)),
  sex: (a, b) => String(a.sex).localeCompare(String(b.sex)),
  start: (a, b) => String(a.start_date).localeCompare(String(b.start_date)),
  units: (a, b) => naturalCompare(firstUnit(a), firstUnit(b)),
  restrictions: (a, b) => (a.restrictions || []).length - (b.restrictions || []).length,
  status: (a, b) => String(a.status).localeCompare(String(b.status)),
};

/**
 * Stable sort by one column: rows that tie keep the order they arrived in, and `dir`
 * flips only the primary key (same contract as the results table, T-08).
 */
export function sortSeasons(rows, key = "species", dir = "asc") {
  const cmp = COMPARE[key] || COMPARE.species;
  const sign = dir === "desc" ? -1 : 1;
  return rows
    .map((row, i) => [row, i])
    .sort(([a, i], [b, j]) => sign * cmp(a, b) || i - j)
    .map(([row]) => row);
}

/** Filters -> query string. Empty values are dropped, so the URL stays readable. */
export function queryString(filters = {}) {
  const parts = [];
  for (const key of FILTER_KEYS) {
    const value = filters[key];
    if (value === undefined || value === null || value === "") continue;
    parts.push(`${key}=${encodeURIComponent(String(value).trim())}`);
  }
  return parts.join("&");
}

export const seasonsUrl = (filters) => {
  const q = queryString(filters);
  return q ? `/api/seasons?${q}` : "/api/seasons";
};

/** The CSV download carries the same filters, so you export what you see. */
export const exportUrl = (filters) => {
  const q = queryString(filters);
  return q ? `/api/export.csv?${q}` : "/api/export.csv";
};

/** `{imported: 3, documents:[…]}` -> the sentence the toast shows. */
export function importSummary(result) {
  const n = Number(result?.imported || 0);
  const years = (result?.documents || []).map((d) => d.license_year).filter(Boolean);
  const where = years.length ? ` under Manual entries ${[...new Set(years)].sort().join(", ")}` : "";
  return `${n} row${n === 1 ? "" : "s"} staged for review${where}.`;
}

/**
 * A rejected CSV upload -> one line per problem, each starting with its line number.
 * Nothing is imported, so the message says so (the server is strict by design).
 */
export function csvErrorLines(error) {
  const detail = (error && error.detail) || {};
  const errors = Array.isArray(detail.errors) ? detail.errors : [];
  if (!errors.length) return [error?.message || "The CSV could not be read."];
  return errors.map((e) => `Line ${e.line}: ${e.field} — ${e.message}`);
}

/** One table row's cells as plain text/values, for the DOM and for the node twin. */
export function rowCells(row) {
  const hunt = row.hunt_number ? `#${row.hunt_number}` : text(row.hunt_area);
  return {
    species: text(row.species_label || row.species_id),
    tag: text(row.tag_type_label || row.tag_type_id),
    hunt: row.hunt_class === "general" && !row.hunt_number ? "general" : hunt,
    weapon: String(row.weapon || ""),
    sex: String(row.sex || ""),
    start: dateRange(row),
    units: (row.units || []).join(", ") || "—",
    restrictions: (row.restrictions || []).map(restrictionLabel).join(", ") || "—",
    status: String(row.status || ""),
  };
}

const el = (tag, cls, content) => {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (content != null) node.textContent = String(content);
  return node;
};

const button = (cls, label, onClick, title) => {
  const b = el("button", cls, label);
  b.type = "button";
  if (title) b.title = title;
  b.addEventListener("click", (e) => {
    e.stopPropagation();
    onClick(e);
  });
  return b;
};

function select(id, label, options, value, onChange) {
  const wrap = el("label", "filter");
  wrap.append(textEl("span", label, "filter-label"));
  const sel = el("select", "filter-select");
  sel.id = id;
  for (const [v, l] of options) {
    const option = el("option", null, l);
    option.value = String(v);
    if (String(value ?? "") === String(v)) option.setAttribute("selected", "true");
    sel.appendChild(option);
  }
  sel.value = String(value ?? "");
  sel.addEventListener("change", () => onChange(sel.value));
  wrap.appendChild(sel);
  return wrap;
}

/**
 * The whole Season Table. Every network call is injected, so the node twin and the
 * browser share one implementation:
 * `{load, loadMeta, loadUnits, create, update, remove, importCsv, notify, highlight}`.
 * Returns `{el, refresh, rows, filters, setFilter, sort, ready}`.
 */
export function createSeasonsScreen(options = {}) {
  const {
    load = (filters) => getJSON(seasonsUrl(filters)),
    loadMeta = () => getJSON("/api/meta"),
    loadUnits = () => getJSON("/api/units"),
    create = (body) => postJSON("/api/seasons", body),
    update = (id, body) => putJSON(`/api/seasons/${id}`, body),
    remove = (id) => deleteJSON(`/api/seasons/${id}`),
    importCsv = (file) => postFile("/api/import.csv", file),
    notify = toast,
    highlight = (units, label) =>
      window.dispatchEvent(new CustomEvent(UNITS_HIGHLIGHT, { detail: { units, label } })),
    confirmDelete = (row) =>
      typeof window !== "undefined" && window.confirm
        ? window.confirm(`Delete this manual season (${(row.units || []).join(", ")})?`)
        : true,
    filters: initialFilters = {},
  } = options;

  const root = el("div", "seasons");
  root.id = "seasons-screen";

  const filterBar = el("div", "seasons-filters");
  filterBar.id = "seasons-filters";
  const toolbar = el("div", "seasons-tools");
  const tableHost = el("div", "seasons-table-wrap");
  tableHost.id = "seasons-table";
  const editorHost = el("div", "seasons-editor");
  editorHost.hidden = true;
  const countEl = textEl("p", "", "muted");
  countEl.id = "seasons-count";

  let filters = { ...DEFAULT_FILTERS, ...initialFilters };
  let rows = [];
  let meta = { species: [], tag_types: [], weapons: [], license_years: [] };
  let units = [];
  let sortKey = "species";
  let sortDir = "asc";
  let editing = null; // season id, or "new"

  // ---- the filter bar --------------------------------------------------------
  function paintFilters() {
    const years = (meta.license_years || []).map((y) => [String(y), String(y)]);
    const speciesOptions = (meta.species || []).map((s) => [s.id, s.label || s.id]);
    const tagOptions = (meta.tag_types || []).map((t) => [t.id, t.label || t.id]);
    const weaponOptions = (meta.weapons || []).map((w) => [w, w.replace(/_/g, " ")]);
    const unitInput = el("input", "filter-input");
    unitInput.type = "search";
    unitInput.id = "seasons-unit";
    unitInput.value = filters.unit || "";
    unitInput.setAttribute("placeholder", "e.g. 39");
    unitInput.addEventListener("input", () => setFilter("unit", unitInput.value));
    const unitWrap = el("label", "filter");
    unitWrap.append(textEl("span", "Unit", "filter-label"), unitInput);

    filterBar.replaceChildren(
      select("seasons-year", "Year", [["", "Any year"], ...years], filters.year, (v) =>
        setFilter("year", v),
      ),
      select(
        "seasons-species",
        "Species",
        [["", "All species"], ...speciesOptions],
        filters.species,
        (v) => setFilter("species", v),
      ),
      select("seasons-tag", "Tag", [["", "All tags"], ...tagOptions], filters.tag_type, (v) =>
        setFilter("tag_type", v),
      ),
      select(
        "seasons-weapon",
        "Weapon",
        [["", "All weapons"], ...weaponOptions],
        filters.weapon,
        (v) => setFilter("weapon", v),
      ),
      select("seasons-status", "Status", STATUSES, filters.status, (v) => setFilter("status", v)),
      unitWrap,
    );
  }

  // ---- the toolbar: add, export, import --------------------------------------
  function paintToolbar() {
    const addBtn = button("btn primary", "+ Add season", () => startCreate(), "Add a season by hand");
    addBtn.id = "seasons-add";
    const exportLink = el("a", "btn", "⬇ Export CSV");
    exportLink.id = "seasons-export";
    exportLink.href = exportUrl(filters);
    exportLink.setAttribute("download", "");
    exportLink.title = "Download the rows these filters show";

    const fileInput = el("input", "csv-input");
    fileInput.type = "file";
    fileInput.hidden = true; // a hidden input still opens the picker on .click()
    fileInput.id = "seasons-import-file";
    fileInput.setAttribute("accept", ".csv,text/csv");
    fileInput.addEventListener("change", () => {
      const file = fileInput.files && fileInput.files[0];
      if (file) doImport(file);
      fileInput.value = "";
    });
    const importBtn = button("btn", "⬆ Import CSV", () => fileInput.click(), "Stage rows from a CSV");
    importBtn.id = "seasons-import";
    const template = el("a", "link", "template");
    template.href = "/api/seasons/template.csv";
    template.setAttribute("download", "seasons_template.csv");

    toolbar.replaceChildren(
      addBtn,
      exportLink,
      importBtn,
      fileInput,
      textEl("span", " · ", "muted"),
      template,
      countEl,
    );
  }

  // ---- the table -------------------------------------------------------------
  function headerCell(column) {
    const th = el("th");
    const active = sortKey === column.key;
    th.setAttribute("scope", "col");
    th.setAttribute("aria-sort", active ? (sortDir === "asc" ? "ascending" : "descending") : "none");
    th.appendChild(
      button("sort-btn", `${column.label}${active ? (sortDir === "asc" ? " ▲" : " ▼") : ""}`, () => {
        if (sortKey === column.key) sortDir = sortDir === "asc" ? "desc" : "asc";
        else {
          sortKey = column.key;
          sortDir = "asc";
        }
        paintTable();
      }),
    );
    return th;
  }

  function rowElement(row) {
    const tr = el("tr", row.manual ? "season-row manual" : "season-row");
    tr.dataset.seasonId = String(row.id);
    tr.setAttribute("tabindex", "0");
    const cells = rowCells(row);
    for (const column of COLUMNS) {
      const td = el("td");
      if (column.key === "status") {
        td.append(badge(STATUS_KINDS[row.status] || "neutral", cells.status));
        if (row.manual) td.append(" ", badge("info", "manual"));
      } else if (column.key === "units") {
        td.className = "units";
        td.textContent = cells.units;
      } else {
        td.textContent = cells[column.key];
      }
      tr.appendChild(td);
    }
    const actions = el("td", "row-actions");
    if (row.source_page) {
      const page = el("a", "link", `p. ${row.source_page}`);
      page.href = `/api/documents/${row.document_id}/pdf?page=${row.source_page}#page=${row.source_page}`;
      page.setAttribute("target", "_blank");
      page.setAttribute("rel", "noopener");
      actions.append(page, " ");
    }
    if (row.manual) {
      actions.append(
        button("btn small", "Edit", () => startEdit(row.id)),
        button("btn small", "Delete", () => doDelete(row)),
      );
    }
    tr.appendChild(actions);
    const open = () => {
      if (!(row.units || []).length) return;
      highlight(row.units, `${cells.species} · ${cells.start}`);
    };
    tr.addEventListener("click", open);
    tr.addEventListener("keydown", (e) => {
      if (e.key !== "Enter") return;
      e.preventDefault();
      open();
    });
    return tr;
  }

  function paintTable() {
    const table = el("table", "seasons-table");
    table.setAttribute("aria-label", "Seasons");
    const thead = el("thead");
    const headRow = el("tr");
    for (const column of COLUMNS) headRow.appendChild(headerCell(column));
    const actionsHead = el("th", null, "Actions");
    actionsHead.setAttribute("scope", "col");
    headRow.appendChild(actionsHead);
    thead.appendChild(headRow);
    const tbody = el("tbody");
    const sorted = sortSeasons(rows, sortKey, sortDir);
    for (const row of sorted) tbody.appendChild(rowElement(row));
    table.append(thead, tbody);
    tableHost.replaceChildren(
      rows.length ? table : textEl("p", "No seasons match these filters.", "placeholder"),
    );
    countEl.textContent = `${rows.length} season${rows.length === 1 ? "" : "s"}`;
    document.body.dataset.seasonsRows = String(rows.length);
  }

  // ---- the manual editor -----------------------------------------------------
  function closeEditor() {
    editing = null;
    editorHost.hidden = true;
    editorHost.replaceChildren();
  }

  function openEditor(row, { create: isCreate }) {
    editorHost.hidden = false;
    editorHost.replaceChildren(
      el("h3", null, isCreate ? "Add a season" : `Edit manual season ${row.id}`),
      textEl(
        "p",
        isCreate
          ? "Saved under this year's Manual entries document and published at once, badged “manual”."
          : "Manual rows only — a parsed row is corrected in the Regulations review screen.",
        "muted",
      ),
      createRowEditor(row, {
        units,
        tagTypes: meta.tag_types || [],
        species: meta.species || [],
        create: isCreate,
        onSave: async (body) => {
          const result = isCreate ? await create(body) : await update(row.id, body);
          closeEditor();
          await refresh();
          notify(isCreate ? "Season added." : "Season updated.", "success");
          return result;
        },
        onCancel: () => closeEditor(),
      }),
    );
  }

  function startCreate() {
    editing = "new";
    openEditor(
      { license_year: Number(filters.year) || undefined, units: [], restrictions: [] },
      { create: true },
    );
  }

  function startEdit(id) {
    const row = rows.find((r) => r.id === id);
    if (!row) return;
    editing = id;
    openEditor(row, { create: false });
  }

  async function doDelete(row) {
    if (!confirmDelete(row)) return;
    try {
      await remove(row.id);
      await refresh();
      notify("Season deleted.", "success");
    } catch (e) {
      notify(e.message || String(e), "error");
    }
  }

  async function doImport(file) {
    try {
      const result = await importCsv(file);
      await refresh();
      notify(importSummary(result), "success");
    } catch (e) {
      const lines = csvErrorLines(e);
      notify(`CSV rejected — nothing imported. ${lines.slice(0, 3).join(" · ")}`, "error", {
        ms: 12000,
      });
      tableHost.prepend(importErrors(lines));
    }
  }

  function importErrors(lines) {
    const box = el("div", "csv-errors");
    box.id = "seasons-csv-errors";
    box.setAttribute("role", "alert");
    box.append(el("h4", null, "The CSV was rejected — nothing was imported"));
    const list = el("ul");
    for (const line of lines) list.appendChild(el("li", null, line));
    box.append(list, button("btn small", "Dismiss", () => box.remove()));
    return box;
  }

  // ---- data ------------------------------------------------------------------
  function setFilter(key, value) {
    filters = { ...filters, [key]: value };
    if (!value) delete filters[key];
    refresh().catch(() => {});
  }

  async function refresh() {
    const data = await load(filters);
    rows = (data && data.seasons) || [];
    paintFilters();
    paintToolbar();
    paintTable();
    document.body.dataset.seasonsQuery = queryString(filters);
    return rows;
  }

  const ready = (async () => {
    try {
      const [m, u] = await Promise.all([loadMeta(), loadUnits()]);
      meta = m || meta;
      units = (u && u.units) || [];
    } catch {
      // The table still works without labels; the fetch error shows on refresh.
    }
    await refresh();
  })().catch((e) => {
    tableHost.replaceChildren(textEl("p", e.message || String(e), "err"));
  });

  const seasonsCard = card("Seasons", filterBar, toolbar, editorHost, tableHost);
  seasonsCard.id = "seasons-card";
  root.appendChild(seasonsCard);

  return {
    el: root,
    refresh,
    rows: () => rows,
    filters: () => ({ ...filters }),
    setFilter,
    sort: (key, dir) => {
      sortKey = key;
      sortDir = dir || "asc";
      paintTable();
    },
    sortState: () => ({ key: sortKey, dir: sortDir }),
    editing: () => editing,
    startCreate,
    ready,
  };
}

/** Mount the Season Table into its tab panel (called once, on first visit). */
export function initSeasons(panel = document.getElementById("panel-seasons"), options = {}) {
  const screen = createSeasonsScreen(options);
  const wrap = document.createElement("div");
  wrap.className = "wrap single";
  wrap.appendChild(screen.el);
  panel.replaceChildren(wrap);
  return screen;
}
