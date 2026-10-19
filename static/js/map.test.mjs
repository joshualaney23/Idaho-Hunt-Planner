// static/js/map.js (T-07): SVG structure, status classes, a11y, keyboard, tooltip
// placement and legend, run against tests/js/fakedom.mjs. Hover visuals, computed
// colors and real focus order are checked in Chrome (puppeteer) in the T-07 PR.
// Run: node --test static/js/map.test.mjs
import { test, beforeEach } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

import { installFakeDom } from "../../tests/js/fakedom.mjs";
import {
  createMap,
  enableZoom,
  legendItems,
  panBox,
  parseViewBox,
  pdfHref,
  placeTip,
  renderLegend,
  seasonLine,
  tooltipContent,
  unitLabel,
  wheelFactor,
  zoomBox,
} from "./map.js";

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

test("T-27: a multi-species tooltip groups seasons under a species header", () => {
  const labels = { species: { deer: { label: "Deer", icon: "🦌" }, elk: { label: "Elk", icon: "🫎" } } };
  const s = (over) => ({
    weapon: "any",
    sex: "either",
    start: "2026-10-10",
    end: "2026-10-31",
    status: "open",
    reasons: [],
    restrictions: [],
    ...over,
  });
  const unit = {
    status: "restricted",
    reasons: ["elk:partial_dates"],
    days_open: 1,
    per_species: {
      deer: { status: "open", reasons: [], days_open: 2 },
      elk: { status: "restricted", reasons: ["partial_dates"], days_open: 1 },
    },
    seasons: [s({ species: "deer", id: 1 }), s({ species: "elk", id: 2, reasons: ["partial_dates"] })],
  };
  const nodes = tooltipContent("39", unit, GMU.units["39"], labels);
  const text = nodes.map((n) => n.textContent);
  assert.equal(text[1], "Elk zone: Boise River · restricted");
  assert.match(text[2], /^🦌 Deer open$/);
  assert.equal(nodes[2].dataset.species, "deer");
  assert.match(text[4], /^🫎 Elk restricted$/);
  // Each season sits under its own species header, in the payload's order.
  assert.equal(nodes.length, 6);
  assert.match(text[3], /Oct 10 – Oct 31/);
  // A species with no overlapping season says so instead of leaving a bare header.
  const none = { ...unit, seasons: [s({ species: "deer", id: 1 })] };
  assert.equal(tooltipContent("39", none, GMU.units["39"], labels)[5].textContent, "No season for your tags and dates.");
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
  // T-27: in `all` mode green means every selected species is open.
  assert.match(legendItems(sum, false, { mode: "all", speciesCount: 2 })[0].text, /every selected species open/);
  assert.match(legendItems(sum, false, { mode: "any", speciesCount: 2 })[0].text, /full range/);
  assert.match(legendItems(sum, false, { mode: "all", speciesCount: 1 })[0].text, /full range/);
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


// ---- T-08: p.N links and zoom ----------------------------------------------------

test("seasonLine links p.N to the stored PDF page only when asked", () => {
  const s = { weapon: "any", sex: "antlered", start: "2026-10-10", end: "2026-10-31", source_page: 13, document_id: 7 };
  assert.equal(pdfHref(7, 13), "/api/documents/7/pdf#page=13");
  const linked = seasonLine(s, {}, { link: true });
  const a = linked.querySelectorAll("a");
  assert.equal(a.length, 1);
  assert.equal(a[0].href, "/api/documents/7/pdf#page=13");
  assert.equal(a[0].target, "_blank");
  assert.equal(seasonLine(s).querySelectorAll("a").length, 0); // tooltip: plain text
  assert.match(seasonLine(s).textContent, /p\.13/);
});

// ---- T-17c: controlled hunts on a season line ------------------------------------

const CONTROLLED = {
  weapon: "any",
  sex: "antlered",
  start: "2026-10-10",
  end: "2026-11-03",
  hunt_number: "1028",
  source_page: 23,
  document_id: 7,
  tag_ids: [4],
  status: "restricted",
  reasons: ["portion_of_unit"],
  coverage: "partial",
  coverage_note: "That portion of Unit 38 south of Interstate 84… — see p.33",
};

test("a controlled season names its hunt, its tag and its hunt area", () => {
  const labels = { tags: { 4: "🦌 Controlled #1028" }, restrictions: {} };
  const line = seasonLine(CONTROLLED, labels);
  assert.match(line.textContent, /Controlled #1028/);
  assert.match(line.textContent, /Oct 10 – Nov 3/);
  // §7.2: the tooltip says which tag unlocks the season…
  assert.match(line.textContent, /via 🦌 Controlled #1028/);
  // …and a partial hunt area carries the brochure's own words plus the page.
  assert.match(line.textContent, /That portion of Unit 38/);
  assert.match(line.textContent, /p\.33/);
});

test("an unheld tag id, a full-coverage unit and {coverage:false} add nothing", () => {
  assert.doesNotMatch(seasonLine(CONTROLLED, {}).textContent, /via/);
  const full = { ...CONTROLLED, coverage: "full" };
  assert.doesNotMatch(seasonLine(full, {}).textContent, /That portion/);
  const brief = seasonLine(CONTROLLED, {}, { coverage: false });
  assert.doesNotMatch(brief.textContent, /That portion/);
});

test("a general season line is unchanged by the controlled-hunt additions", () => {
  const s = { weapon: "any", sex: "either", start: "2026-10-10", end: "2026-10-31" };
  assert.equal(seasonLine(s, {}).textContent.trim(), "anyOct 10 – Oct 31"); // fakedom joins nodes without spacing
});

const BASE = { x: 0, y: 0, w: 430, h: 690 };

test("zoomBox keeps the focal point fixed and clamps to 1×–8× and the map bounds", () => {
  assert.deepEqual(parseViewBox("0 0 430 690"), BASE);
  // 2× around the centre: 215×345 box, centred.
  assert.deepEqual(zoomBox(BASE, BASE, 2, 215, 345), { x: 107.5, y: 172.5, w: 215, h: 345 });
  // 2× around the top-left corner stays anchored there.
  assert.deepEqual(zoomBox(BASE, BASE, 2, 0, 0), { x: 0, y: 0, w: 215, h: 345 });
  // Clamped at 8× (430 / 8 = 53.75) and at 1×.
  assert.equal(zoomBox(BASE, BASE, 100, 215, 345).w, 53.75);
  assert.deepEqual(zoomBox(BASE, BASE, 0.5, 100, 100), BASE);
  // Zooming out from 2× near the right edge can't leave the map.
  const z = zoomBox(BASE, { x: 215, y: 0, w: 215, h: 345 }, 1 / 1.5, 430, 0);
  assert.ok(z.x + z.w <= 430 && z.x >= 0, JSON.stringify(z));
  // Panning is clamped to the bounds.
  const two = { x: 107.5, y: 172.5, w: 215, h: 345 };
  assert.equal(panBox(BASE, two, -500, 0).x, 0);
  assert.equal(panBox(BASE, two, 1000, 0).x, 215);
  assert.equal(panBox(BASE, two, 0, 1000).y, 345);
});

test("wheelFactor: wheel down zooms out, up zooms in, line deltas scale", () => {
  assert.equal(wheelFactor({ deltaY: 100, deltaMode: 0 }), Math.exp(-0.2));
  assert.equal(wheelFactor({ deltaY: -100, deltaMode: 0 }), Math.exp(0.2));
  assert.equal(wheelFactor({ deltaY: -3, deltaMode: 1 }), Math.exp(48 * 0.002));
  assert.equal(wheelFactor({ deltaY: -10, deltaMode: 0, ctrlKey: true }), Math.exp(0.1));
});

test("enableZoom: buttons/wheel change the viewBox, reset restores it, a drag never clicks", () => {
  createMap(svg, GMU);
  svg.getBoundingClientRect = () => ({ left: 0, top: 0, width: 430, height: 690 });
  const clicks = [];
  const z = enableZoom(svg);
  const seen = [];
  z.onChange((s) => seen.push(s));
  assert.equal(svg.getAttribute("viewBox"), "0 0 430 690");
  assert.equal(svg.style.touchAction, "pan-x pan-y");
  z.zoomBy(2);
  assert.equal(svg.getAttribute("viewBox"), "107.5 172.5 215 345");
  assert.equal(svg.dataset.zoom, "2");
  assert.equal(svg.style.touchAction, "none");
  z.zoomBy(100);
  assert.equal(z.scale(), 8);
  z.reset();
  assert.equal(svg.getAttribute("viewBox"), "0 0 430 690");
  assert.deepEqual(seen, [2, 8, 1]);

  const wheel = svg.dispatch("wheel", { deltaY: -100, deltaMode: 0, clientX: 0, clientY: 0 });
  assert.ok(wheel.defaultPrevented); // the page doesn't scroll under the map
  assert.equal(z.box().x, 0);
  assert.ok(Math.abs(z.scale() - Math.exp(0.2)) < 1e-9);

  z.reset();
  z.zoomBy(2);
  svg.addEventListener("click", () => clicks.push("unit"));
  svg.dispatch("pointerdown", { pointerId: 1, clientX: 100, clientY: 100 });
  svg.dispatch("pointermove", { pointerId: 1, clientX: 120, clientY: 100 });
  // 20 px right at 2× (0.5 user units per px) pans the box 10 units left.
  assert.equal(z.box().x, 97.5);
  let stopped = false;
  svg.listeners.click[0]({ type: "click", stopPropagation: () => (stopped = true), preventDefault() {} });
  assert.ok(stopped, "the click that ends a drag is swallowed");
  svg.dispatch("pointerup", { pointerId: 1 });
  // A plain click (no movement) goes through.
  svg.dispatch("pointerdown", { pointerId: 1, clientX: 100, clientY: 100 });
  stopped = false;
  svg.listeners.click[0]({ type: "click", stopPropagation: () => (stopped = true), preventDefault() {} });
  assert.equal(stopped, false);
});

test("see_notes units get a dot and an aria note; the status color is unchanged", () => {
  const m = createMap(svg, GMU);
  m.setStatuses({ 1: "restricted", 39: "restricted" }, ["39"]);
  const dotsOf = () => svg.querySelectorAll("circle");
  const dots = dotsOf();
  assert.equal(dots.length, 1);
  assert.equal(dots[0].getAttribute("cx"), String(GMU.units["39"].label[0]));
  assert.equal(dots[0].getAttribute("cy"), String(GMU.units["39"].label[1] - 7));
  assert.equal(m.paths["39"].getAttribute("class"), "u-restricted");
  assert.equal(m.paths["39"].getAttribute("aria-label"), "Unit 39, restricted, see notes");
  assert.equal(m.paths["1"].getAttribute("aria-label"), "Unit 1, restricted");
  m.setStatuses({ 39: "open" }, []);
  assert.equal(dotsOf().length, 0);
});
