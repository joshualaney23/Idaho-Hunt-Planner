// static/js/drawer.js (T-08): timeline geometry, season grouping, focus-trap index and
// the open → Esc → focus-back cycle, against tests/js/fakedom.mjs. The real modal
// (backdrop click, Tab trap) is checked in Chrome with puppeteer in the T-08 PR.
//
// Timeline expectations by hand: the 2026 window is Aug 1 2026 – Jan 31 2027 =
// 31 (Aug) + 30 (Sep) + 31 (Oct) + 30 (Nov) + 31 (Dec) + 31 (Jan) = 184 days. With
// width = 184, one day = 1 unit and day i of the window starts at x = i (Aug 1 = 0).
// Run: node --test static/js/drawer.test.mjs
import { test, beforeEach } from "node:test";
import assert from "node:assert/strict";

import { installFakeDom } from "../../tests/js/fakedom.mjs";
import {
  AXIS_H,
  ROW_GAP,
  ROW_H,
  createDrawer,
  sourceDocs,
  splitSeasons,
  timelineModel,
  timelineWindow,
  trapIndex,
} from "./drawer.js";

let doc;
beforeEach(() => {
  doc = installFakeDom();
});

const S = [
  { id: 1, weapon: "archery", start: "2026-08-30", end: "2026-09-30" }, // x 29, 32 days
  { id: 2, weapon: "any", start: "2026-10-10", end: "2026-10-31" }, // x 70, 22 days
  { id: 3, weapon: "muzzleloader", start: "2026-12-10", end: "2027-02-15" }, // x 131 → clipped at 184
  { id: 4, weapon: "any", start: "2026-04-01", end: "2026-06-30" }, // spring: outside
  { id: 5, weapon: "archery", start: "2026-07-15", end: "2026-08-10" }, // clipped at 0, 10 days
];

test("timeline window is Aug 1 → Jan 31 of the next year", () => {
  assert.deepEqual(timelineWindow(2026), { start: "2026-08-01", end: "2027-01-31" });
});

test("timelineModel: one bar per season, clipped to the window; band and today", () => {
  const m = timelineModel(S, {
    year: 2026,
    start: "2026-10-16",
    end: "2026-10-18",
    today: "2026-10-14",
    width: 184,
  });
  assert.equal(m.total, 184);
  assert.deepEqual(
    m.months.map((x) => [x.label, x.x]),
    [["Aug", 0], ["Sep", 31], ["Oct", 61], ["Nov", 92], ["Dec", 122], ["Jan", 153]],
  );
  assert.deepEqual(
    m.bars.map((b) => [b.id, b.x, b.w, b.clipStart, b.clipEnd]),
    [
      [1, 29, 32, false, false],
      [2, 70, 22, false, false],
      [3, 131, 53, false, true],
      [5, 0, 10, true, false],
    ],
  );
  const step = ROW_H + ROW_GAP;
  assert.deepEqual(m.bars.map((b) => b.y), [AXIS_H, AXIS_H + step, AXIS_H + 2 * step, AXIS_H + 3 * step]);
  assert.equal(m.outside, 1);
  assert.deepEqual(m.band, { x: 76, w: 3 }); // Oct 16 = day 31 + 30 + 15
  assert.equal(m.today, 74.5); // middle of Oct 14 (day 74)
  assert.equal(m.height, AXIS_H + 4 * step + 2);
});

test("timelineModel: range and today outside the window draw nothing", () => {
  const m = timelineModel([], { year: 2026, start: "2026-05-01", end: "2026-05-03", today: "2026-05-02", width: 184 });
  assert.equal(m.band, null);
  assert.equal(m.today, null);
  assert.equal(m.bars.length, 0);
  // A range that straddles Jan 31 is clipped like a season.
  const m2 = timelineModel([], { year: 2026, start: "2027-01-30", end: "2027-02-02", width: 184 });
  assert.deepEqual(m2.band, { x: 182, w: 2 });
});

test("splitSeasons: species you hold tags for plus the one you're viewing come first", () => {
  const seasons = [
    { id: 1, species_id: "deer" },
    { id: 2, species_id: "elk" },
    { id: 3, species_id: "black_bear" },
    { id: 4, species_id: "deer" },
  ];
  const { primary, other } = splitSeasons(seasons, { heldSpecies: ["elk"], viewing: "deer" });
  assert.deepEqual(primary.map((s) => s.id), [1, 2, 4]);
  assert.deepEqual(other.map((s) => s.id), [3]);
});

test("sourceDocs: one entry per document with its cited pages sorted", () => {
  const docs = sourceDocs([
    { document_id: 7, document_title: "Big Game 2026", source_page: 19 },
    { document_id: 7, document_title: "Big Game 2026", source_page: 13 },
    { document_id: 9, document_title: "Supplement", source_page: null },
    { document_id: 7, source_page: 13 },
  ]);
  assert.deepEqual(docs, [
    { id: 7, title: "Big Game 2026", pages: [13, 19] },
    { id: 9, title: "Supplement", pages: [] },
  ]);
});

test("trapIndex wraps Tab and Shift+Tab at both ends", () => {
  assert.equal(trapIndex(3, 0, false), 1);
  assert.equal(trapIndex(3, 2, false), 0); // last → first
  assert.equal(trapIndex(3, 0, true), 2); // first → last
  assert.equal(trapIndex(3, -1, false), 0); // focus outside → first
  assert.equal(trapIndex(3, -1, true), 2);
  assert.equal(trapIndex(0, 0, false), -1);
});

test("drawer: open fetches the license year, renders, Esc closes and restores focus", async () => {
  const calls = [];
  const opener = doc.createElement("button");
  doc.body.appendChild(opener);
  const labels = { restrictions: { whitetail_only: { label: "White-tailed deer only", severity: "narrowing" } } };
  const d = createDrawer({
    labels,
    today: "2026-10-14",
    fetchBrief: async (unit, year) => {
      calls.push([unit, year]);
      return {
        unit: { id: unit, elk_zone: "Boise River", idfg_url: "https://idfg.idaho.gov/ifwis/huntplanner/unit.aspx?ID=53" },
        year,
        held_species: ["deer"],
        seasons: [
          {
            id: 1,
            species_id: "deer",
            species_label: "Deer",
            species_icon: "🦌",
            tag_type_label: "Regular deer tag",
            weapon: "any",
            sex: "antlered",
            start: "2026-10-10",
            end: "2026-10-31",
            restrictions: ["whitetail_only"],
            notes: "Verbatim footnote.",
            source_page: 13,
            document_id: 7,
            document_title: "Big Game 2026",
          },
          {
            id: 2,
            species_id: "black_bear",
            species_label: "Black bear",
            weapon: "any",
            sex: "na",
            start: "2026-08-30",
            end: "2026-10-31",
            restrictions: [],
            source_page: 40,
            document_id: 7,
            document_title: "Big Game 2026",
          },
        ],
      };
    },
  });
  opener.focus();
  await d.open("39", { state: { species: "deer", start: "2026-10-16", end: "2026-10-18" }, unit: { status: "open", days_open: 3 } });
  assert.deepEqual(calls, [["39", 2026]]);
  assert.ok(d.isOpen());
  assert.equal(doc.body.dataset.brief, "39");
  assert.equal(doc.activeElement.id, "brief-close"); // focus moves into the drawer
  const text = d.el.textContent;
  assert.match(text, /Unit 39 brief/);
  assert.match(text, /Elk zone: Boise River/);
  assert.match(text, /Verbatim footnote\./);
  assert.match(text, /White-tailed deer only/);
  assert.match(text, /1 season for other species/);
  const hrefs = d.el.querySelectorAll("a").map((a) => a.href);
  assert.ok(hrefs.includes("https://idfg.idaho.gov/ifwis/huntplanner/unit.aspx?ID=53"));
  assert.ok(hrefs.includes("/api/documents/7/pdf#page=13"));
  const bars = d.el.querySelectorAll("rect").filter((r) => String(r.getAttribute("class")).includes("tl-bar"));
  assert.equal(bars.length, 1); // the timeline shows the deer season (held + viewing)
  assert.equal(d.el.querySelectorAll("rect").filter((r) => r.getAttribute("class") === "tl-band").length, 1);

  const ev = d.el.dispatch("keydown", { key: "Escape" });
  assert.ok(ev.defaultPrevented);
  assert.equal(d.isOpen(), false);
  assert.equal(doc.activeElement, opener);
  assert.equal(doc.body.dataset.brief, "");
});

test("drawer: a click on the backdrop (the dialog itself) closes; inside clicks don't", async () => {
  const d = createDrawer({ fetchBrief: async (unit, year) => ({ unit: { id: unit }, year, held_species: [], seasons: [] }) });
  await d.open("1", { state: { start: "2026-10-16", end: "2026-10-18" } });
  d.el.children[0].dispatch("click"); // .drawer-inner: target is not the dialog
  d.el.dispatch("click", { target: d.el.children[0] });
  assert.ok(d.isOpen());
  d.el.dispatch("click"); // target === dialog → backdrop
  assert.equal(d.isOpen(), false);
});

test("drawer: a stale async close event after a quick re-open doesn't close it", async () => {
  const d = createDrawer({ fetchBrief: async (unit, year) => ({ unit: { id: unit }, year, held_species: [], seasons: [] }) });
  // Model the real <dialog>: showModal/close flip `open`; the close event comes later.
  d.el.showModal = function () {
    this.open = true;
  };
  d.el.close = function () {
    this.open = false;
  };
  await d.open("39", { state: { start: "2026-10-16", end: "2026-10-18" } });
  d.el.dispatch("click"); // backdrop → close() → dialog.close() (event still queued)
  await d.open("40", { state: { start: "2026-10-16", end: "2026-10-18" } });
  d.el.dispatch("close"); // the stale event from the first close arrives now
  assert.ok(d.isOpen(), "the re-opened brief must stay open");
  assert.equal(d.current(), "40");
  assert.equal(d.el.open, true);
});
