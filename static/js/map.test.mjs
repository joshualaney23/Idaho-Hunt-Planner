// static/js/map.js (T-07): SVG structure, status classes, a11y, keyboard, tooltip
// placement and legend, run against tests/js/fakedom.mjs. Hover visuals, computed
// colors and real focus order are checked in Chrome (puppeteer) in the T-07 PR.
// Run: node --test static/js/map.test.mjs
import { test, beforeEach } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

import { installFakeDom } from "../../tests/js/fakedom.mjs";
import { createMap, legendItems, placeTip, renderLegend, tooltipContent, unitLabel } from "./map.js";

const GMU = {
  viewBox: "0 0 430 690",
  units: {
    1: { d: "M0 0l10 0 0 10Z", label: [5, 5], zone: "Panhandle" },
    39: { d: "M20 20l10 0 0 10Z", label: [25, 25], zone: "Boise River" },
    "4A": { d: "M40 40l10 0 0 10Z", label: [45, 45], zone: "Palouse" },
    YNP: { d: "M60 60l10 0 0 10Z", label: [65, 65], zone: null, closed: true },
  },
};

let doc;
let svg;
beforeEach(() => {
  doc = installFakeDom();
  svg = doc.createElementNS("http://www.w3.org/2000/svg", "svg");
  doc.body.appendChild(svg);
});

const unitPaths = (s) => [...s.walk()].filter((e) => e.tagName === "path" && e.dataset.unit);
const labels = (s) => [...s.walk()].filter((e) => e.tagName === "text");

test("one path per unit, then one label per unit drawn above every fill", () => {
  createMap(svg, GMU);
  assert.equal(svg.getAttribute("viewBox"), "0 0 430 690");
  const [fills, overlay, labelGroup] = svg.children;
  assert.equal(fills.getAttribute("class"), "units");
  assert.equal(overlay.getAttribute("class"), "overlay");
  assert.equal(labelGroup.getAttribute("class"), "labels");
  assert.deepEqual(
    unitPaths(svg).map((p) => p.dataset.unit),
    ["1", "39", "4A", "YNP"],
  );
  assert.deepEqual(
    labels(svg).map((t) => [t.textContent, t.getAttribute("x"), t.getAttribute("y")]),
    [
      ["1", "5", "5"],
      ["39", "25", "25"],
      ["4A", "45", "45"],
      ["YNP", "65", "65"],
    ],
  );
  assert.equal(labelGroup.getAttribute("aria-hidden"), "true");
});

test("setStatuses: classes, aria-labels and label contrast class", () => {
  const m = createMap(svg, GMU);
  // Before any data every unit is closed.
  assert.ok(unitPaths(svg).every((p) => p.getAttribute("class") === "u-closed"));
  m.setStatuses({ 1: "restricted", 39: "open", "4A": "untagged", YNP: "open" });
  const byId = Object.fromEntries(unitPaths(svg).map((p) => [p.dataset.unit, p]));
  assert.equal(byId["39"].getAttribute("class"), "u-open");
  assert.equal(byId["39"].getAttribute("aria-label"), "Unit 39, open");
  assert.equal(byId["1"].getAttribute("aria-label"), "Unit 1, restricted");
  assert.equal(byId["4A"].getAttribute("class"), "u-untagged");
  // Yellowstone is closed whatever the data says.
  assert.equal(byId.YNP.getAttribute("class"), "u-closed");
  assert.equal(byId.YNP.getAttribute("aria-label"), "Unit YNP, closed");
  const lab = Object.fromEntries(labels(svg).map((t) => [t.textContent, t.getAttribute("class")]));
  assert.deepEqual(lab, { 1: "", 39: "", "4A": "c", YNP: "c" });
  // Untagged units get a dashed ring in the overlay; missing units fall back to closed.
  const rings = [...svg.walk()].filter((e) => e.getAttribute?.("class") === "ut");
  assert.deepEqual(rings.map((r) => r.getAttribute("d")), [GMU.units["4A"].d]);
  m.setStatuses({});
  assert.equal(m.statusOf("39"), "closed");
  assert.equal([...svg.walk()].filter((e) => e.getAttribute?.("class") === "ut").length, 0);
});

test("keyboard: every unit is a tabbable button; Enter and Space open it", () => {
  const m = createMap(svg, GMU);
  for (const p of unitPaths(svg)) {
    assert.equal(p.getAttribute("tabindex"), "0");
    assert.equal(p.getAttribute("role"), "button");
  }
  const opened = [];
  m.onClick((id) => opened.push(id));
  const p39 = m.paths["39"];
  const enter = p39.dispatch("keydown", { key: "Enter" });
  assert.equal(enter.defaultPrevented, true);
  p39.dispatch("keydown", { key: " " });
  p39.dispatch("keydown", { key: "a" });
  p39.dispatch("click");
  assert.deepEqual(opened, ["39", "39", "39"]);
});

test("hover and focus report the unit; leave and blur clear it", () => {
  const m = createMap(svg, GMU);
  const seen = [];
  m.onHover((id, pt) => seen.push([id, pt]));
  const p = m.paths["4A"];
  p.dispatch("mousemove", { clientX: 30, clientY: 40 });
  const ring = [...svg.walk()].find((e) => e.getAttribute?.("class") === "hover-ring");
  assert.equal(ring.getAttribute("visibility"), "visible");
  assert.equal(ring.getAttribute("d"), GMU.units["4A"].d);
  p.dispatch("mouseleave");
  assert.equal(ring.getAttribute("visibility"), "hidden");
  p.getBoundingClientRect = () => ({ left: 10, top: 20, width: 40, height: 60 });
  p.focus();
  p.dispatch("blur");
  assert.deepEqual(seen, [
    ["4A", { x: 30, y: 40 }],
    [null, undefined],
    ["4A", { x: 30, y: 50 }],
    [null, undefined],
  ]);
});

test("highlight draws gold rings without reordering the unit paths", () => {
  const m = createMap(svg, GMU);
  const order = () => unitPaths(svg).map((p) => p.dataset.unit);
  const before = order();
  m.highlight(["39", "nope", "1"]);
  const hl = [...svg.walk()].filter((e) => e.getAttribute?.("class") === "hl");
  assert.deepEqual(hl.map((h) => h.getAttribute("d")), [GMU.units["39"].d, GMU.units["1"].d]);
  assert.deepEqual(order(), before);
  m.highlight([]);
  assert.equal([...svg.walk()].filter((e) => e.getAttribute?.("class") === "hl").length, 0);
});

test("placeTip: below-right by default, flipped and clamped at the edges", () => {
  // Plenty of room: 14 px below-right of the pointer.
  assert.deepEqual(placeTip(100, 100, 200, 80, 1400, 900), { left: 114, top: 114 });
  // Near the right edge: flips to the left of the pointer.
  assert.deepEqual(placeTip(1350, 100, 200, 80, 1400, 900), { left: 1136, top: 114 });
  // Near the bottom: flips above.
  assert.deepEqual(placeTip(100, 880, 200, 80, 1400, 900), { left: 114, top: 786 });
  // Tooltip wider than the room on either side: pinned 8 px inside.
  assert.deepEqual(placeTip(150, 20, 300, 80, 320, 200), { left: 8, top: 34 });
  // Bigger than the viewport: still starts inside it.
  assert.deepEqual(placeTip(5, 5, 500, 500, 300, 300), { left: 8, top: 8 });
});

test("tooltip content follows §4.3: unit, elk zone, season badges, source page", () => {
  const labelsMeta = {
    restrictions: {
      whitetail_only: { label: "White-tailed deer only", severity: "narrowing" },
      antlered_only: { label: "Antlered only", severity: "informational" },
    },
    reasons: { partial_dates: { label: "Season doesn't cover the whole date range" } },
  };
  const unit = {
    status: "restricted",
    seasons: [
      {
        weapon: "any",
        sex: "antlered",
        start: "2026-10-10",
        end: "2026-10-31",
        status: "restricted",
        restrictions: ["whitetail_only", "antlered_only"],
        reasons: ["whitetail_only"],
        source_page: 12,
      },
      {
        weapon: "archery",
        sex: "either",
        start: "2026-08-30",
        end: "2026-09-30",
        status: "untagged",
        restrictions: [],
        reasons: ["partial_dates"],
        source_page: 13,
      },
    ],
  };
  const nodes = tooltipContent("1", unit, GMU.units["1"], labelsMeta);
  const text = nodes.map((n) => n.textContent);
  assert.equal(text[0], "Unit 1");
  assert.equal(text[1], "Elk zone: Panhandle · restricted");
  assert.equal(text[2], "anyantlered Oct 10 – Oct 31 White-tailed deer onlyAntlered only p.12");
  const badges = (n) => n.children.filter((c) => c.className.startsWith("badge")).map((c) => c.className);
  assert.deepEqual(badges(nodes[2]), ["badge b-any", "badge b-r", "badge b-info"]);
  assert.deepEqual(badges(nodes[3]), ["badge b-archery", "badge b-untagged", "badge b-r"]);
  assert.match(text[3], /Aug 30 – Sep 30 no tagSeason doesn't cover the whole date range p\.13/);
  // No seasons / Yellowstone.
  assert.equal(tooltipContent("39", undefined, GMU.units["39"], {})[2].textContent, "No season for your tags and dates.");
  assert.match(tooltipContent("YNP", undefined, GMU.units.YNP, {})[2].textContent, /Yellowstone/);
  assert.equal(unitLabel("4A", "untagged"), "Unit 4A, open with a tag you don't hold");
});

test("legend shows a count per status; untagged only when the toggle is on", () => {
  const sum = { open: 68, restricted: 13, untagged: 4, closed: 15 };
  assert.deepEqual(
    legendItems(sum, false).map((i) => [i.status, i.count]),
    [
      ["open", 68],
      ["restricted", 13],
      ["closed", 15],
    ],
  );
  assert.deepEqual(
    legendItems(sum, true).map((i) => i.status),
    ["open", "restricted", "untagged", "closed"],
  );
  const el = doc.createElement("div");
  renderLegend(el, sum, true);
  assert.equal(el.children.length, 4);
  assert.equal(el.children[2].textContent, "Open with a tag you don't hold 4");
  assert.equal(el.children[0].children[0].className, "sw sw-open");
});

test("the real gmu.json renders 100 tabbable units", () => {
  const gmu = JSON.parse(readFileSync(new URL("../data/gmu.json", import.meta.url), "utf8"));
  const m = createMap(svg, gmu);
  m.setStatuses({ 39: "open" });
  const paths = unitPaths(svg);
  assert.equal(paths.length, 100);
  assert.equal(labels(svg).length, 100);
  assert.equal(m.paths["39"].getAttribute("aria-label"), "Unit 39, open");
  assert.equal(m.paths.YNP.getAttribute("class"), "u-closed");
});
