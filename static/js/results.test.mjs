// static/js/results.js (T-08): natural + stable sorting, rows, CSV, and the table DOM
// (run against tests/js/fakedom.mjs). Expected orders are written out by hand.
// Run: node --test static/js/results.test.mjs
import { test, beforeEach } from "node:test";
import assert from "node:assert/strict";

import { installFakeDom } from "../../tests/js/fakedom.mjs";
import {
  createResults,
  csvCell,
  csvFilename,
  naturalCompare,
  resultRows,
  sortRows,
  toCSV,
} from "./results.js";

let doc;
beforeEach(() => {
  doc = installFakeDom();
});

const season = (over = {}) => ({
  id: 1,
  status: "open",
  reasons: [],
  restrictions: [],
  weapon: "any",
  sex: "antlered",
  start: "2026-10-10",
  end: "2026-10-31",
  source_page: 13,
  document_id: 7,
  ...over,
});

// days_open: 4 → 3, 4A → 2, 5 → 1, 10 → 2, 10A → 0; unit 1 is closed.
const AVAIL = {
  units: {
    10: { status: "open", reasons: [], days_open: 2, seasons: [season({ id: 10 })] },
    "4A": {
      status: "restricted",
      reasons: ["whitetail_only"],
      days_open: 2,
      seasons: [season({ id: 41, weapon: "archery", start: "2026-08-30", reasons: ["whitetail_only"] })],
    },
    4: {
      status: "open",
      reasons: [],
      days_open: 3,
      seasons: [
        season({ id: 42, weapon: "muzzleloader", start: "2026-11-10", end: "2026-11-30" }),
        season({ id: 43, weapon: "any", start: "2026-10-10" }),
      ],
    },
    5: { status: "restricted", reasons: ["partial_dates"], days_open: 1, seasons: [season({ id: 5, start: "2026-10-17" })] },
    "10A": { status: "untagged", reasons: [], days_open: 0, seasons: [season({ id: 101, status: "untagged" })] },
    1: { status: "closed", reasons: [], days_open: 0, seasons: [] },
  },
};
const units = (rows) => rows.map((r) => r.unit);

test("natural unit order: 4, 4A, 5, 10, 10A", () => {
  const input = ["10A", "4", "YNP", "5", "10", "4A", "1", "11", "2"];
  assert.deepEqual([...input].sort(naturalCompare), ["1", "2", "4", "4A", "5", "10", "10A", "11", "YNP"]);
  assert.ok(naturalCompare("4", "4A") < 0);
  assert.ok(naturalCompare("4A", "5") < 0);
  assert.ok(naturalCompare("5", "10") < 0);
  assert.ok(naturalCompare("10", "10A") < 0);
  assert.equal(naturalCompare("36B", "36B"), 0);
  assert.ok(naturalCompare("36A", "36B") < 0);
});

test("resultRows: units that aren't closed, natural order, weapons and earliest start", () => {
  const rows = resultRows(AVAIL);
  assert.deepEqual(units(rows), ["4", "4A", "5", "10", "10A"]);
  const four = rows[0];
  assert.deepEqual(four.weapons, ["any", "muzzleloader"]); // canonical order, not data order
  assert.equal(four.earliest, "2026-10-10");
  assert.equal(four.daysOpen, 3);
  assert.deepEqual(resultRows({}), []);
  assert.deepEqual(resultRows(null), []);
});

test("sorting is stable: ties keep the previous order, desc flips only the key", () => {
  const rows = resultRows(AVAIL); // 4, 4A, 5, 10, 10A
  const byStatus = sortRows(rows, "status", "asc");
  assert.deepEqual(units(byStatus), ["4", "10", "4A", "5", "10A"]);
  // Desc reverses the status groups, but each group stays in unit order.
  assert.deepEqual(units(sortRows(rows, "status", "desc")), ["10A", "4A", "5", "4", "10"]);
  // Days from unit order: 4A and 10 tie at 2 and keep unit order.
  assert.deepEqual(units(sortRows(rows, "days", "asc")), ["10A", "5", "4A", "10", "4"]);
  assert.deepEqual(units(sortRows(rows, "days", "desc")), ["4", "4A", "10", "5", "10A"]);
  // Days on top of a status sort: the status order breaks the 4A/10 tie (10 is open).
  assert.deepEqual(units(sortRows(byStatus, "days", "asc")), ["10A", "5", "10", "4A", "4"]);
  // Re-sorting by unit restores natural order from any starting order.
  assert.deepEqual(units(sortRows(byStatus, "unit", "asc")), ["4", "4A", "5", "10", "10A"]);
  assert.deepEqual(units(sortRows(rows, "unit", "desc")), ["10A", "10", "5", "4A", "4"]);
  // Earliest start: 4A Aug 30; 4, 10, 10A Oct 10 (tie, unit order); 5 Oct 17.
  assert.deepEqual(units(sortRows(rows, "earliest", "asc")), ["4A", "4", "10", "10A", "5"]);
  // Weapons: [any] < [any, muzzleloader] < [archery].
  assert.deepEqual(units(sortRows(rows, "weapons", "asc")), ["5", "10", "10A", "4", "4A"]);
  // Input untouched.
  assert.deepEqual(units(rows), ["4", "4A", "5", "10", "10A"]);
});

test("CSV: RFC 4180 quoting, labels from /api/meta, CRLF", () => {
  assert.equal(csvCell("plain"), "plain");
  assert.equal(csvCell("a,b"), '"a,b"');
  assert.equal(csvCell('say "hi"'), '"say ""hi"""');
  assert.equal(csvCell("two\nlines"), '"two\nlines"');
  assert.equal(csvCell(null), "");
  assert.equal(csvCell(0), "0");
  const labels = { restrictions: { whitetail_only: { label: "White-tailed deer only" } } };
  const rows = resultRows(AVAIL);
  const csv = toCSV(rows, labels);
  const lines = csv.split("\r\n");
  assert.equal(lines[0], "unit,status,days_open,weapons,earliest_start,reasons,seasons,source_pages");
  assert.equal(lines.length, rows.length + 2); // header + rows + trailing empty
  assert.equal(
    lines[1],
    "4,open,3,any muzzleloader,2026-10-10,,muzzleloader antlered 2026-11-10..2026-11-30 p.13; any antlered 2026-10-10..2026-10-31 p.13,13",
  );
  assert.equal(
    lines[2],
    "4A,restricted,2,archery,2026-08-30,White-tailed deer only,archery antlered 2026-08-30..2026-10-31 White-tailed deer only p.13,13",
  );
  assert.equal(
    csvFilename({ species: ["deer"], start: "2026-10-16", end: "2026-10-18" }),
    "ihp-deer-2026-10-16_2026-10-18.csv",
  );
  assert.equal(
    csvFilename({ species: ["deer", "elk"], speciesMode: "all", start: "2026-10-16", end: "2026-10-18" }),
    "ihp-deer-elk-all-2026-10-16_2026-10-18.csv",
  );
});

test("T-27: `all` mode adds a status column per species and sorts by status then days", () => {
  const box = doc.createElement("div");
  doc.body.appendChild(box);
  const r = createResults(box, { speciesLabels: { deer: "🦌 Deer", elk: "🫎 Elk" } });
  const unit = (status, days, per) => ({
    status,
    reasons: [],
    days_open: days,
    seasons: [season()],
    per_species: per,
  });
  r.update({
    species: ["deer", "elk"],
    species_mode: "all",
    units: {
      // 39 open both days; 40 restricted (elk only one day); 12 restricted but fewer days.
      39: unit("open", 2, { deer: { status: "open", days_open: 2 }, elk: { status: "open", days_open: 2 } }),
      40: unit("restricted", 1, { deer: { status: "open", days_open: 2 }, elk: { status: "restricted", days_open: 1 } }),
      12: unit("restricted", 0, { deer: { status: "restricted", days_open: 1 }, elk: { status: "closed", days_open: 0 } }),
    },
  });
  const ths = r.table.querySelectorAll("th");
  assert.deepEqual(
    ths.map((th) => th.textContent),
    ["Unit", "Status", "🦌 Deer", "🫎 Elk", "Days open", "Weapons", "Earliest start", "Seasons overlapping your dates"],
  );
  // Combined status first (open before restricted), then days open descending.
  const bodyRows = () => r.table.querySelectorAll("tr").filter((tr) => tr.dataset.unit);
  assert.deepEqual(bodyRows().map((tr) => tr.dataset.unit), ["39", "40", "12"]);
  assert.deepEqual(r.sortState(), { key: "status", dir: "asc" });
  const pills = bodyRows()[1].querySelectorAll("td").filter((td) => td.dataset.species);
  assert.deepEqual(pills.map((td) => [td.dataset.species, td.textContent]), [
    ["deer", "open"],
    ["elk", "restricted"],
  ]);
  // Back to one species: the extra columns go away and the default sort is Unit again.
  r.update({ species: ["deer"], species_mode: "any", units: { 39: unit("open", 2) } });
  assert.equal(r.columns().length, 6);
  assert.deepEqual(r.sortState(), { key: "unit", dir: "asc" });
  // `any` mode with two species keeps T-08's six columns (no per-species pills).
  r.update({ species: ["deer", "elk"], species_mode: "any", units: { 39: unit("open", 2, {}) } });
  assert.equal(r.columns().length, 6);
});

test("table: sortable headers with aria-sort, hover highlights, unit button opens the brief", () => {
  const box = doc.createElement("div");
  doc.body.appendChild(box);
  const hovered = [];
  const opened = [];
  const r = createResults(box, { onHover: (u) => hovered.push(u), onOpen: (u) => opened.push(u) });
  r.update(AVAIL);
  assert.equal(box.dataset.rows, "5");
  const ths = r.table.querySelectorAll("th");
  assert.deepEqual(
    ths.map((th) => th.textContent),
    ["Unit", "Status", "Days open", "Weapons", "Earliest start", "Seasons overlapping your dates"],
  );
  assert.equal(ths[0].getAttribute("aria-sort"), "ascending");
  const bodyRows = () => r.table.querySelectorAll("tr").filter((tr) => tr.dataset.unit);
  assert.deepEqual(bodyRows().map((tr) => tr.dataset.unit), ["4", "4A", "5", "10", "10A"]);

  const daysBtn = ths[2].querySelector("button");
  daysBtn.click();
  assert.equal(ths[2].getAttribute("aria-sort"), "ascending");
  assert.equal(ths[0].getAttribute("aria-sort"), "none");
  assert.deepEqual(bodyRows().map((tr) => tr.dataset.unit), ["10A", "5", "4A", "10", "4"]);
  daysBtn.click();
  assert.equal(ths[2].getAttribute("aria-sort"), "descending");
  assert.deepEqual(r.sortState(), { key: "days", dir: "desc" });

  // A new availability keeps the chosen sort.
  r.update(AVAIL);
  assert.deepEqual(units(r.rows()), ["4", "4A", "10", "5", "10A"]);

  const row4 = bodyRows().find((tr) => tr.dataset.unit === "4");
  row4.dispatch("mouseenter");
  row4.dispatch("mouseleave");
  assert.deepEqual(hovered, ["4", null]);
  row4.querySelector("button").click();
  assert.deepEqual(opened, ["4"]);

  // p.N links point at the stored PDF page.
  const links = row4.querySelectorAll("a");
  assert.equal(links[0].href, "/api/documents/7/pdf#page=13");
  assert.equal(links[0].textContent, "p.13");

  r.update({ units: {} });
  assert.equal(box.dataset.rows, "0");
  assert.match(r.table.textContent, /Nothing open for these filters/);
});
