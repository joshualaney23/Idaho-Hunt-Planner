// static/js/filters.js (T-07): filter-bar helpers and wiring, run against
// tests/js/fakedom.mjs. Real-browser behaviour (timing, URL, reload) is in the PR.
// Run: node --test static/js/filters.test.mjs
import { test, beforeEach } from "node:test";
import assert from "node:assert/strict";

import { installFakeDom } from "../../tests/js/fakedom.mjs";
import {
  FEATURED,
  breadcrumbParts,
  createFilters,
  selectedTagIds,
  speciesChipLabel,
  tagFilter,
  tagsForSpecies,
} from "./filters.js";

const SPECIES = [
  { id: "deer", label: "Deer", icon: "🦌", published_seasons: 235 },
  { id: "elk", label: "Elk", icon: "🫎", published_seasons: 0 },
  { id: "black_bear", label: "Black bear", icon: "🐻", published_seasons: 4 },
  { id: "moose", label: "Moose", icon: "🫎", published_seasons: 2 },
];
const TAGS = [
  { id: 1, species_id: "deer", icon: "🦌", tag_type_label: "Regular deer tag", filled: false },
  { id: 2, species_id: "deer", icon: "🦌", tag_type_label: "White-tailed deer tag", filled: false },
  { id: 3, species_id: "deer", icon: "🦌", tag_type_label: "Regular deer tag", filled: true },
  { id: 4, species_id: "elk", icon: "🫎", tag_type_label: "Elk A tag", zone: "Sawtooth", filled: false },
  { id: 5, species_id: null, icon: "🎟️", tag_type_label: "Controlled hunt tag", hunt_number: "1001", filled: false },
];
const STATE = {
  species: "deer",
  start: "2026-10-16",
  end: "2026-10-18",
  weapon: "all",
  tags: null,
  includeUntagged: false,
};
const WED = new Date(2026, 9, 14, 12);

let doc;
beforeEach(() => {
  doc = installFakeDom();
});

test("tag chips: unfilled tags for the species plus species-less (controlled) tags", () => {
  assert.deepEqual(tagsForSpecies(TAGS, "deer").map((t) => t.id), [1, 2, 5]);
  assert.deepEqual(tagsForSpecies(TAGS, "elk").map((t) => t.id), [4, 5]);
  assert.deepEqual(tagsForSpecies([], "deer"), []);
});

test("tag filter: all chips on collapses to null (mine)", () => {
  assert.equal(tagFilter([1, 2, 5], [1, 2, 5]), null);
  assert.deepEqual(tagFilter(["2", "1"], [1, 2, 5]), [1, 2]);
  assert.deepEqual(tagFilter([], [1, 2]), []);
  assert.deepEqual(selectedTagIds(null, [1, 2]), [1, 2]);
  assert.deepEqual(selectedTagIds([2, 9], [1, 2]), [2]);
});

test("breadcrumb: species · tags · weapon · range", () => {
  assert.deepEqual(breadcrumbParts(STATE, SPECIES, TAGS), [
    "🦌 Deer",
    "🦌 Regular deer tag · 🦌 White-tailed deer tag · 🎟️ Controlled hunt tag #1001",
    "All legal weapons",
    "Oct 16 – Oct 18",
  ]);
  const one = { ...STATE, tags: [2], weapon: "archery", start: "2026-11-02", end: "2026-11-02" };
  assert.deepEqual(breadcrumbParts(one, SPECIES, TAGS), ["🦌 Deer", "🦌 White-tailed deer tag", "Archery", "Nov 2"]);
  assert.equal(breadcrumbParts({ ...STATE, tags: [] }, SPECIES, TAGS)[1], "No tags selected");
  assert.equal(breadcrumbParts({ ...STATE, species: "black_bear" }, SPECIES, [])[1], "No tags yet");
});

test("species chip labels use the §4.2 short names", () => {
  assert.equal(speciesChipLabel({ id: "black_bear", label: "Black bear" }), "Bear");
  assert.equal(speciesChipLabel({ id: "deer", label: "Deer" }), "Deer");
  assert.equal(FEATURED.length, 12);
});

function mount(state = STATE, ctx = { tags: TAGS, permits: ["archery", "hip"], year: 2026 }) {
  const container = doc.createElement("div");
  doc.body.appendChild(container);
  const box = doc.createElement("input");
  box.type = "checkbox";
  doc.body.appendChild(box);
  const patches = [];
  const permits = [];
  const f = createFilters(container, {
    species: SPECIES,
    state,
    today: WED,
    untaggedBox: box,
    onChange: (p) => patches.push(p),
    onPermits: (c) => permits.push(c),
  });
  f.update(state, ctx);
  return { container, box, patches, permits, f };
}
const byId = (id) => doc.getElementById(id);
const chips = (id) => byId(id).children.filter((c) => c.tagName === "BUTTON");

test("species chips: enabled only with published seasons; More reveals the rest", () => {
  const { patches } = mount();
  const sp = chips("f-species");
  const state = Object.fromEntries(sp.map((b) => [b.dataset.value || b.id, b.disabled]));
  // Only featured species in the meta list are shown, plus the More toggle.
  assert.deepEqual(state, { deer: false, elk: true, black_bear: false, "f-species-more": false });
  sp.find((b) => b.dataset.value === "elk").click(); // disabled: no-op
  sp.find((b) => b.dataset.value === "black_bear").click();
  assert.deepEqual(patches, [{ species: "black_bear", tags: null }]);
  const more = byId("f-species-more");
  assert.equal(more.getAttribute("aria-expanded"), "false");
  more.click();
  assert.ok(chips("f-species").some((b) => b.dataset.value === "moose"));
  assert.equal(byId("f-species-more").getAttribute("aria-expanded"), "true");
  // The hint stays hidden while the species has published seasons.
  assert.equal(byId("f-species-hint").hidden, true);
});

test("a species without published seasons explains why the map is empty", () => {
  mount({ ...STATE, species: "elk" }, { tags: TAGS, permits: [], year: 2026 });
  const hint = byId("f-species-hint");
  assert.equal(hint.hidden, false);
  assert.match(hint.textContent, /No published Elk seasons yet/);
  // The selected species stays pressed and clickable even with no seasons.
  const elk = chips("f-species").find((b) => b.dataset.value === "elk");
  assert.equal(elk.disabled, false);
  assert.equal(elk.getAttribute("aria-pressed"), "true");
});

test("tag chips toggle the tag filter; no tags shows the add-one link", () => {
  const { patches, f } = mount();
  const tagChips = chips("f-tags");
  assert.deepEqual(tagChips.map((b) => b.textContent), [
    "🦌 Regular deer tag",
    "🦌 White-tailed deer tag",
    "🎟️ Controlled hunt tag #1001",
  ]);
  assert.ok(tagChips.every((b) => b.getAttribute("aria-pressed") === "true"));
  tagChips[1].click();
  assert.deepEqual(patches.at(-1), { tags: [1, 5] });
  assert.equal(byId("f-tags-manage").textContent, "Manage tags");
  assert.equal(byId("f-tags-manage").href, "#/tags");
  f.update({ ...STATE, species: "black_bear" }, { tags: TAGS.filter((t) => t.species_id), year: 2026 });
  assert.equal(byId("f-tags"), null);
  assert.equal(byId("f-tags-manage").textContent, "No tags yet → add one");
});

test("weapon, presets, dates and include-untagged all emit patches", () => {
  const { patches, box } = mount();
  // "This weekend" is pressed for the default range.
  const pressed = chips("f-presets").filter((b) => b.getAttribute("aria-pressed") === "true");
  assert.deepEqual(pressed.map((b) => b.dataset.value), ["thisWeekend"]);
  chips("f-weapon").find((b) => b.dataset.value === "rifle").click();
  chips("f-presets").find((b) => b.dataset.value === "nextWeekend").click();
  const start = byId("f-start");
  assert.equal(start.value, "2026-10-16");
  start.value = "2026-10-20"; // after the end: the end follows
  start.dispatch("change");
  const end = byId("f-end");
  end.value = ""; // cleared: snaps back, no patch
  end.dispatch("change");
  assert.equal(end.value, "2026-10-18");
  box.checked = true;
  box.dispatch("change");
  assert.deepEqual(patches, [
    { weapon: "rifle" },
    { start: "2026-10-23", end: "2026-10-25" },
    { start: "2026-10-20", end: "2026-10-20" },
    { includeUntagged: true },
  ]);
});

test("permit chips reflect held permits and report the wanted map permits", () => {
  const { permits } = mount();
  const p = chips("f-permits");
  assert.deepEqual(p.map((b) => [b.dataset.value, b.getAttribute("aria-pressed")]), [
    ["archery", "true"],
    ["muzzleloader", "false"],
  ]);
  p[1].click();
  assert.deepEqual(permits, [["archery", "muzzleloader"]]);
  assert.equal(byId("f-permits-label").textContent, "Permits (2026)");
});

test("repainting keeps keyboard focus on the toggled chip", () => {
  const { f } = mount();
  const rifle = chips("f-weapon").find((b) => b.dataset.value === "rifle");
  rifle.focus();
  rifle.click();
  f.update({ ...STATE, weapon: "rifle" });
  assert.equal(doc.activeElement, rifle); // weapon chips are repainted, not rebuilt
  const tag = chips("f-tags")[0];
  tag.focus();
  f.update({ ...STATE, tags: [2, 5] }, { tags: TAGS });
  assert.equal(doc.activeElement, chips("f-tags")[0]);
  assert.equal(chips("f-tags")[0].getAttribute("aria-pressed"), "false");
});
