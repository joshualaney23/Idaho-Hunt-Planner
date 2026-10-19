// static/js/state.js (T-07): defaults, URL round-trip, API path, debounced fetcher.
// Expected dates are worked out by hand from the calendar (Oct 2026: the 14th is a
// Wednesday), never from dates.js.
// Run: node --test static/js/state.test.mjs
import { test, mock } from "node:test";
import assert from "node:assert/strict";

import {
  DEBOUNCE_MS,
  MAX_SPECIES,
  availabilityPath,
  createFetcher,
  createStore,
  defaultState,
  hasFilterKeys,
  parseSpecies,
  primarySpecies,
  rangeYears,
  stateFromQuery,
  toQuery,
  toggleSpecies,
  validISO,
  withDate,
} from "./state.js";

const WED = new Date(2026, 9, 14, 12); // Wed Oct 14 2026, local noon
const SAT = new Date(2026, 9, 17, 12);
const defaults = defaultState({ profile: { default_species: "deer" }, today: WED });

test("defaults: profile species + the coming Fri–Sun weekend (D5)", () => {
  assert.deepEqual(defaults, {
    species: ["deer"],
    speciesMode: "any",
    start: "2026-10-16",
    end: "2026-10-18",
    weapon: "all",
    tags: null,
    includeUntagged: false,
  });
  const elk = defaultState({ profile: { default_species: "elk", weekend_def: "sat_sun" }, today: WED });
  assert.deepEqual(elk.species, ["elk"]);
  assert.deepEqual([elk.start, elk.end], ["2026-10-17", "2026-10-18"]);
  // On a Saturday, Fri–Sun is today through tomorrow.
  const sat = defaultState({ profile: {}, today: SAT });
  assert.deepEqual([sat.species, sat.start, sat.end], [["deer"], "2026-10-17", "2026-10-18"]);
});

test("no URL params → exactly the defaults", () => {
  assert.deepEqual(stateFromQuery("", defaults), defaults);
  assert.equal(hasFilterKeys(""), false);
  assert.equal(hasFilterKeys("species=elk"), true);
  assert.equal(hasFilterKeys("foo=1"), false);
});

test("URL round-trip restores every filter", () => {
  const s = {
    species: ["elk"],
    speciesMode: "any",
    start: "2026-10-24",
    end: "2026-10-31",
    weapon: "archery",
    tags: [3, 7],
    includeUntagged: true,
  };
  const q = toQuery(s);
  assert.equal(q, "species=elk&start=2026-10-24&end=2026-10-31&weapon=archery&tags=3%2C7&untagged=1");
  assert.deepEqual(stateFromQuery(q, defaults), s);
  assert.deepEqual(stateFromQuery(new URLSearchParams(q), defaults), s);
  // Default tag scope and untagged-off stay out of the URL.
  assert.equal(toQuery(defaults), "species=deer&start=2026-10-16&end=2026-10-18&weapon=all");
  // An explicitly empty tag selection survives the round-trip (it isn't "mine").
  const none = { ...defaults, tags: [] };
  assert.equal(toQuery(none).includes("tags=none"), true);
  assert.deepEqual(stateFromQuery(toQuery(none), defaults).tags, []);
});

test("bad URL values fall back to defaults instead of breaking the page", () => {
  const s = stateFromQuery(
    "species=unicorn&start=2026-02-30&end=nope&weapon=bazooka&tags=1,x&untagged=maybe",
    defaults,
    { speciesIds: ["deer", "elk"] },
  );
  assert.deepEqual(s, { ...defaults, includeUntagged: false });
  // Reversed range is swapped; a lone start becomes a one-day range.
  const r = stateFromQuery("start=2026-11-02&end=2026-10-30", defaults);
  assert.deepEqual([r.start, r.end], ["2026-10-30", "2026-11-02"]);
  const one = stateFromQuery("start=2026-11-02", defaults);
  assert.deepEqual([one.start, one.end], ["2026-11-02", "2026-11-02"]);
  // Duplicate / unsorted tag ids normalize.
  assert.deepEqual(stateFromQuery("tags=7,3,7", defaults).tags, [3, 7]);
  assert.equal(stateFromQuery("tags=mine", defaults).tags, null);
});

test("T-27: a species list and the match mode round-trip through the URL", () => {
  const s = { ...defaults, species: ["deer", "elk"], speciesMode: "all" };
  const q = toQuery(s);
  assert.equal(q, "species=deer%2Celk&mode=all&start=2026-10-16&end=2026-10-18&weapon=all");
  assert.deepEqual(stateFromQuery(q, defaults), s);
  // `any` is the default, so a one-species URL stays exactly T-07's.
  assert.equal(toQuery(defaults).includes("mode="), false);
  assert.equal(hasFilterKeys("mode=all"), true);
  // Order is kept, duplicates and unknown ids drop, and at most 4 survive.
  const ids = ["deer", "elk", "pronghorn", "wolf", "turkey"];
  assert.deepEqual(parseSpecies("elk,deer,elk", ids), ["elk", "deer"]);
  assert.deepEqual(parseSpecies("deer,unicorn,elk", ids), ["deer", "elk"]);
  assert.deepEqual(parseSpecies("deer,elk,pronghorn,wolf,turkey", ids), [
    "deer",
    "elk",
    "pronghorn",
    "wolf",
  ]);
  assert.equal(parseSpecies("unicorn", ids), null);
  assert.equal(parseSpecies("", ids), null);
  // A bad mode falls back rather than breaking the page.
  assert.equal(stateFromQuery("mode=both", defaults).speciesMode, "any");
  assert.deepEqual(stateFromQuery("species=deer,elk,unicorn", defaults, { speciesIds: ids }).species, [
    "deer",
    "elk",
  ]);
  assert.equal(
    availabilityPath(s),
    "/api/availability?species=deer%2Celk&species_mode=all&start=2026-10-16&end=2026-10-18&weapon=all&tags=mine&include_untagged=0",
  );
});

test("T-27: toggleSpecies never goes past 4 or down to none", () => {
  assert.deepEqual(toggleSpecies(["deer"], "elk"), ["deer", "elk"]);
  assert.deepEqual(toggleSpecies(["deer", "elk"], "deer"), ["elk"]);
  // The last species can't be unselected, and a 5th is refused: same array back.
  const one = ["deer"];
  assert.equal(toggleSpecies(one, "deer"), one);
  const four = ["deer", "elk", "pronghorn", "wolf"];
  assert.equal(toggleSpecies(four, "turkey"), four);
  assert.equal(MAX_SPECIES, 4);
  assert.deepEqual(primarySpecies({ species: ["elk", "deer"] }), "elk");
});

test("validISO rejects impossible dates", () => {
  assert.equal(validISO("2026-10-16"), "2026-10-16");
  assert.equal(validISO("2028-02-29"), "2028-02-29");
  for (const bad of ["2026-02-29", "2026-13-01", "2026-1-01", "", null, undefined, "x"]) {
    assert.equal(validISO(bad), null, String(bad));
  }
});

test("availabilityPath carries every filter in §6 form", () => {
  assert.equal(
    availabilityPath(defaults),
    "/api/availability?species=deer&species_mode=any&start=2026-10-16&end=2026-10-18&weapon=all&tags=mine&include_untagged=0",
  );
  const p = availabilityPath({ ...defaults, tags: [2, 5], includeUntagged: true, weapon: "rifle" });
  assert.equal(
    p,
    "/api/availability?species=deer&species_mode=any&start=2026-10-16&end=2026-10-18&weapon=rifle&tags=2%2C5&include_untagged=1",
  );
  assert.ok(availabilityPath({ ...defaults, tags: [] }).includes("tags=&"));
});

test("withDate drags the other end so the range never inverts", () => {
  assert.deepEqual(withDate(defaults, "start", "2026-10-20"), {
    ...defaults,
    start: "2026-10-20",
    end: "2026-10-20",
  });
  assert.deepEqual(withDate(defaults, "end", "2026-10-10"), {
    ...defaults,
    start: "2026-10-10",
    end: "2026-10-10",
  });
  assert.deepEqual(withDate(defaults, "end", "2026-10-25"), { ...defaults, end: "2026-10-25" });
  assert.equal(withDate(defaults, "start", ""), defaults); // cleared input: no change
});

test("rangeYears spans a Dec 31 → Jan 1 range (§7.4)", () => {
  assert.deepEqual(rangeYears(defaults), [2026]);
  assert.deepEqual(rangeYears({ start: "2026-12-31", end: "2027-01-01" }), [2026, 2027]);
});

test("store notifies only on real changes", () => {
  const store = createStore(defaults);
  const seen = [];
  store.subscribe((s, prev) => seen.push([prev.weapon, s.weapon]));
  assert.equal(store.set({ weapon: "all" }), false);
  assert.equal(store.set({ weapon: "rifle" }), true);
  assert.equal(store.set({ tags: [1] }), true);
  assert.equal(store.set({ tags: [1] }), false); // equal arrays are no change
  assert.deepEqual(seen, [
    ["all", "rifle"],
    ["rifle", "rifle"],
  ]);
  assert.equal(store.get().weapon, "rifle");
});

test("fetcher debounces 150 ms and keeps only the last request", async () => {
  mock.timers.enable({ apis: ["setTimeout"] });
  try {
    assert.equal(DEBOUNCE_MS, 150);
    const calls = [];
    const results = [];
    const f = createFetcher(async (p) => (calls.push(p), `r:${p}`), {
      onResult: (d) => results.push(d),
    });
    f.schedule("/a");
    mock.timers.tick(100);
    f.schedule("/b");
    mock.timers.tick(149);
    assert.deepEqual(calls, []); // window restarted by /b
    mock.timers.tick(1);
    await new Promise((r) => setImmediate(r));
    assert.deepEqual(calls, ["/b"]);
    assert.deepEqual(results, ["r:/b"]);
  } finally {
    mock.timers.reset();
  }
});

test("fetcher drops a slow response once a newer one is scheduled", async () => {
  let release;
  const slow = new Promise((r) => (release = r));
  const results = [];
  const f = createFetcher((p) => (p === "/slow" ? slow : Promise.resolve(p)), {
    ms: 0,
    onResult: (d) => results.push(d),
  });
  const first = f.now("/slow");
  await f.now("/fast");
  release("/slow");
  await first;
  assert.deepEqual(results, ["/fast"]);
  const errors = [];
  const g = createFetcher(() => Promise.reject(new Error("boom")), { onError: (e) => errors.push(e.message) });
  await g.now("/x");
  assert.deepEqual(errors, ["boom"]);
});
