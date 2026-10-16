// static/js/seasons.js (T-16): the Season Table — sorting, the filter query string,
// the CSV export link, the manual editor's create payload, the row → map highlight and
// the line-numbered CSV import errors, all against tests/js/fakedom.mjs.
// The real-browser checks (routing, keyboard, Lighthouse) live in the T-16 PR.
//
// Run: node --test static/js/seasons.test.mjs
import { test, beforeEach } from "node:test";
import assert from "node:assert/strict";

import { installFakeDom } from "../../tests/js/fakedom.mjs";
import {
  COLUMNS,
  createSeasonsScreen,
  csvErrorLines,
  dateRange,
  exportUrl,
  importSummary,
  queryString,
  rowCells,
  seasonsUrl,
  sortSeasons,
} from "./seasons.js";

beforeEach(() => installFakeDom());

// ---- fixtures ----------------------------------------------------------------

const season = (over = {}) => ({
  id: 1,
  document_id: 3,
  status: "published",
  manual: false,
  license_year: 2026,
  species_id: "deer",
  species_label: "Deer",
  tag_type_id: "deer_regular",
  tag_type_label: "Deer — Regular",
  zone: null,
  hunt_number: null,
  hunt_area: null,
  hunt_class: "general",
  weapon: "any",
  sex: "either",
  start_date: "2026-10-10",
  end_date: "2026-10-24",
  units: ["39", "43"],
  restrictions: [],
  notes: null,
  source_page: 12,
  ...over,
});

const META = {
  species: [
    { id: "deer", label: "Deer" },
    { id: "elk", label: "Elk" },
  ],
  tag_types: [{ id: "deer_regular", label: "Deer — Regular" }],
  weapons: ["any", "archery", "short_range"],
  license_years: [2025, 2026],
};

function mount(rows, extra = {}) {
  const calls = { load: [], create: [], update: [], remove: [], import: [], highlight: [] };
  const screen = createSeasonsScreen({
    load: (filters) => {
      calls.load.push(seasonsUrl(filters));
      return Promise.resolve({ seasons: rows, count: rows.length, filters });
    },
    loadMeta: () => Promise.resolve(META),
    loadUnits: () => Promise.resolve({ units: [{ id: "39" }, { id: "43" }] }),
    create: (body) => {
      calls.create.push(body);
      return Promise.resolve({ season: season({ id: 99, manual: true }) });
    },
    update: (id, body) => {
      calls.update.push([id, body]);
      return Promise.resolve({ season: season({ id, manual: true }) });
    },
    remove: (id) => {
      calls.remove.push(id);
      return Promise.resolve({ deleted: id });
    },
    importCsv: (file) => {
      calls.import.push(file);
      return Promise.resolve({ imported: 2, documents: [{ license_year: 2026 }] });
    },
    highlight: (units, label) => calls.highlight.push([units, label]),
    confirmDelete: () => true,
    notify: () => {},
    ...extra,
  });
  document.body.appendChild(screen.el);
  return { screen, calls };
}

// ---- pure helpers --------------------------------------------------------------

test("queryString drops empty filters and keeps the ticket's keys", () => {
  assert.equal(queryString({}), "");
  assert.equal(
    queryString({ year: 2026, species: "deer", tag_type: "", weapon: null, status: "published" }),
    "year=2026&species=deer&status=published",
  );
  assert.equal(queryString({ unit: " 4A " }), "unit=4A");
  assert.equal(seasonsUrl({}), "/api/seasons");
  assert.equal(seasonsUrl({ species: "elk" }), "/api/seasons?species=elk");
});

test("the CSV export link carries the filters on screen", () => {
  assert.equal(exportUrl({}), "/api/export.csv");
  assert.equal(
    exportUrl({ year: 2026, status: "staged" }),
    "/api/export.csv?year=2026&status=staged",
  );
});

test("sortSeasons is stable and only the primary key flips", () => {
  const rows = [
    season({ id: 1, species_label: "Elk", start_date: "2026-09-01" }),
    season({ id: 2, species_label: "Deer", start_date: "2026-10-10" }),
    season({ id: 3, species_label: "Deer", start_date: "2026-08-01" }),
  ];
  assert.deepEqual(
    sortSeasons(rows, "species", "asc").map((r) => r.id),
    [2, 3, 1],
  );
  assert.deepEqual(
    sortSeasons(rows, "species", "desc").map((r) => r.id),
    [1, 2, 3], // Elk first; the two Deer rows keep their arrival order
  );
  assert.deepEqual(
    sortSeasons(rows, "start", "asc").map((r) => r.id),
    [3, 1, 2],
  );
});

test("units sort naturally, and rows without a hunt number sort last", () => {
  const rows = [
    season({ id: 1, units: ["10"] }),
    season({ id: 2, units: ["4A"] }),
    season({ id: 3, units: ["2"] }),
  ];
  assert.deepEqual(
    sortSeasons(rows, "units", "asc").map((r) => r.id),
    [3, 2, 1],
  );
  const hunts = [season({ id: 1 }), season({ id: 2, hunt_number: "1001" })];
  assert.deepEqual(
    sortSeasons(hunts, "hunt", "asc").map((r) => r.id),
    [2, 1],
  );
});

test("rowCells renders dates, units and restriction labels", () => {
  const cells = rowCells(season({ restrictions: ["antlerless_only", "nonsense_code"] }));
  assert.equal(cells.species, "Deer");
  assert.equal(cells.start, "Oct 10 – Oct 24");
  assert.equal(cells.units, "39, 43");
  assert.equal(cells.restrictions, "Antlerless only, nonsense_code"); // unknown code kept (D3)
  assert.equal(cells.hunt, "general");
  assert.equal(rowCells(season({ hunt_number: "1001" })).hunt, "#1001");
  assert.equal(dateRange({ start_date: "2026-11-01", end_date: "2026-11-01" }), "Nov 1");
});

test("importSummary and csvErrorLines say what happened", () => {
  assert.equal(
    importSummary({ imported: 2, documents: [{ license_year: 2026 }] }),
    "2 rows staged for review under Manual entries 2026.",
  );
  assert.equal(importSummary({ imported: 1, documents: [] }), "1 row staged for review.");
  const lines = csvErrorLines({
    message: "3 problem(s)",
    detail: {
      errors: [
        { line: 3, field: "species", message: "unknown species 'unicorn'" },
        { line: 5, field: "units", message: "unknown unit(s): 99Z" },
      ],
    },
  });
  assert.deepEqual(lines, [
    "Line 3: species — unknown species 'unicorn'",
    "Line 5: units — unknown unit(s): 99Z",
  ]);
  assert.deepEqual(csvErrorLines({ message: "offline" }), ["offline"]);
});

// ---- the screen ----------------------------------------------------------------

test("the table paints one row per season, with every column", async () => {
  const { screen } = mount([season(), season({ id: 2, species_label: "Elk" })]);
  await screen.ready;
  const rows = screen.el.querySelectorAll("tr.season-row");
  assert.equal(rows.length, 2);
  const headers = screen.el.querySelectorAll("th");
  assert.equal(headers.length, COLUMNS.length + 1); // + Actions
  assert.equal(document.body.dataset.seasonsRows, "2");
  assert.equal(document.getElementById("seasons-count").textContent, "2 seasons");
});

test("published is the default filter and every filter reaches the query", async () => {
  const { screen, calls } = mount([season()]);
  await screen.ready;
  assert.equal(calls.load[0], "/api/seasons?status=published");
  screen.setFilter("species", "elk");
  screen.setFilter("unit", "39");
  await screen.refresh();
  assert.equal(calls.load.at(-1), "/api/seasons?species=elk&status=published&unit=39");
  assert.deepEqual(screen.filters(), { status: "published", species: "elk", unit: "39" });
});

test("the export link follows the filters", async () => {
  const { screen } = mount([season()]);
  await screen.ready;
  screen.setFilter("year", "2026");
  await screen.refresh();
  assert.equal(
    document.getElementById("seasons-export").getAttribute("href"),
    "/api/export.csv?year=2026&status=published",
  );
});

test("the filter bar survives a refresh, so typing in the search keeps focus", async () => {
  const { screen } = mount([season()]);
  await screen.ready;
  const before = document.getElementById("seasons-unit");
  const beforeSelect = document.getElementById("seasons-status");
  await screen.refresh();
  // Repainting the bar would replace these nodes and drop focus after one keystroke.
  assert.equal(document.getElementById("seasons-unit"), before);
  assert.equal(document.getElementById("seasons-status"), beforeSelect);
  assert.equal(document.getElementById("seasons-import-file"), document.getElementById("seasons-import-file"));
});

test("clicking a row asks the map to highlight its units", async () => {
  const { screen, calls } = mount([season({ units: ["39", "43"] })]);
  await screen.ready;
  screen.el.querySelector("tr.season-row").dispatch("click");
  assert.deepEqual(calls.highlight[0][0], ["39", "43"]);
  assert.match(calls.highlight[0][1], /Deer/);
});

test("only manual rows carry Edit and Delete", async () => {
  const { screen, calls } = mount([
    season({ id: 1, manual: false }),
    season({ id: 2, manual: true, status: "published" }),
  ]);
  await screen.ready;
  const [parsed, manual] = screen.el.querySelectorAll("tr.season-row");
  const labels = (tr) => tr.querySelectorAll("button").map((b) => b.textContent);
  assert.deepEqual(labels(parsed), []);
  assert.deepEqual(labels(manual), ["Edit", "Delete"]);
  manual.querySelectorAll("button")[1].click();
  await new Promise((r) => setTimeout(r, 0));
  assert.deepEqual(calls.remove, [2]);
});

test("+ Add season opens the create editor and POSTs the whole row", async () => {
  const { screen, calls } = mount([]);
  await screen.ready;
  document.getElementById("seasons-add").click();
  assert.equal(screen.editing(), "new");
  const form = screen.el.querySelector("form.row-editor");
  assert.ok(form, "the editor is mounted");
  // The create editor carries the columns a parsed row already has.
  const labels = screen.el.querySelectorAll(".row-field-label").map((s) => s.textContent);
  assert.ok(labels.includes("License year"));
  assert.ok(labels.includes("Species"));
  assert.ok(labels.includes("Hunt class"));
  assert.ok(labels.includes("Source page"));

  screen.el.querySelector(".unit-input").value = "39, 43";
  screen.el.querySelectorAll("input.date-input")[0].value = "2026-10-10";
  screen.el.querySelectorAll("input.date-input")[1].value = "2026-10-24";
  screen.el.querySelector("input.num-input").value = "2026";
  form.dispatch("submit");
  await new Promise((r) => setTimeout(r, 0));
  const body = calls.create[0];
  assert.equal(body.license_year, 2026);
  assert.deepEqual(body.units, ["39", "43"]);
  assert.equal(body.start_date, "2026-10-10");
  assert.equal(body.species_id, "deer"); // the select defaults to a real value, never ""
  assert.equal(body.hunt_class, "general");
  assert.equal(body.weapon, "any");
});

test("editing a manual row PUTs only what changed", async () => {
  const { screen, calls } = mount([season({ id: 7, manual: true })]);
  await screen.ready;
  screen.el.querySelector("tr.season-row").querySelectorAll("button")[0].click();
  const form = screen.el.querySelector("form.row-editor");
  screen.el.querySelector(".unit-input").value = "39";
  form.dispatch("submit");
  await new Promise((r) => setTimeout(r, 0));
  assert.deepEqual(calls.update, [[7, { units: ["39"] }]]);
});

test("a rejected CSV import lists every bad line and imports nothing", async () => {
  const error = Object.assign(new Error("2 problem(s) in the CSV; nothing was imported"), {
    detail: {
      errors: [
        { line: 3, field: "species", message: "unknown species 'unicorn'" },
        { line: 4, field: "start_date", message: "'2026-13-01' is not a valid date" },
      ],
      lines: [3, 4],
      rows_ok: 1,
    },
  });
  const toasts = [];
  const { screen, calls } = mount([season()], {
    importCsv: () => Promise.reject(error),
    notify: (msg, level) => toasts.push([msg, level]),
  });
  await screen.ready;
  const input = document.getElementById("seasons-import-file");
  input.files = [{ name: "seasons.csv" }];
  input.dispatch("change");
  await new Promise((r) => setTimeout(r, 0));
  assert.equal(calls.load.length, 1, "nothing was re-fetched: the import failed");
  const box = document.getElementById("seasons-csv-errors");
  assert.ok(box, "the error box is shown");
  assert.match(box.textContent, /Line 3: species/);
  assert.match(box.textContent, /Line 4: start_date/);
  assert.equal(toasts.at(-1)[1], "error");
});

test("a good CSV import refreshes the table and says where the rows went", async () => {
  const toasts = [];
  const { screen, calls } = mount([season()], { notify: (msg) => toasts.push(msg) });
  await screen.ready;
  const input = document.getElementById("seasons-import-file");
  input.files = [{ name: "seasons.csv" }];
  input.dispatch("change");
  await new Promise((r) => setTimeout(r, 0));
  assert.equal(calls.import.length, 1);
  assert.equal(calls.load.length, 2, "the table reloaded after the import");
  assert.match(toasts.at(-1), /2 rows staged for review under Manual entries 2026\./);
});

test("an empty result shows the placeholder, not an empty table", async () => {
  const { screen } = mount([]);
  await screen.ready;
  assert.equal(screen.el.querySelectorAll("table.seasons-table").length, 0);
  assert.match(screen.el.textContent, /No seasons match these filters/);
});
