// T-09: pure helpers of the My Tags tab (the DOM rendering is verified in a
// real browser in the T-09 PR with puppeteer).

import { test } from "node:test";
import assert from "node:assert/strict";

import { installFakeDom } from "../../tests/js/fakedom.mjs";
installFakeDom();

const {
  TAGS_CHANGED,
  activeTags,
  createHuntPreview,
  fieldsFor,
  huntCard,
  huntDates,
  huntSummary,
  huntUnits,
  tagChipLabel,
  tagDetail,
  tagTitle,
  tagsSummary,
} = await import("./tags.js");

const deer = {
  id: 1,
  icon: "🦌",
  tag_type_id: "deer_regular",
  tag_type_label: "Regular deer tag",
  zone: null,
  hunt_number: null,
  label: null,
  notes: null,
  filled: false,
};
const elk = {
  ...deer,
  id: 2,
  icon: "🫎",
  tag_type_id: "elk_zone_a",
  tag_type_label: "Elk A tag",
  zone: "Sawtooth",
};
const controlled = {
  ...deer,
  id: 3,
  icon: "🎟️",
  tag_type_id: "controlled",
  tag_type_label: "Controlled hunt tag",
  hunt_number: "1002",
};

// The real 2026 hunt #1002 (`GET /api/hunts/2026/1002`), trimmed to what the UI reads.
const hunt1002 = {
  year: 2026,
  hunt_number: "1002",
  species_id: "deer",
  species_label: "Deer",
  icon: "🦌",
  hunt_class: "controlled",
  weapon: "any",
  sex: "antlered",
  hunt_area: "11",
  description: null,
  description_page: null,
  status: "published",
  published: true,
  start: "2026-10-10",
  end: "2026-11-03",
  windows: [{ id: 1, start: "2026-10-10", end: "2026-11-03" }],
  units: [{ unit: "11", coverage: "full", coverage_frac: 1, coverage_note: null }],
  document_id: 7,
  source_page: 22,
};
const tag1002 = { ...controlled, icon: "🦌", license_year: 2026, hunt: hunt1002 };

test("the event name is the shared-state contract", () => {
  assert.equal(TAGS_CHANGED, "ihp:tags-changed");
});

test("filled tags stop counting", () => {
  const tags = [deer, { ...elk, filled: true }, controlled];
  assert.deepEqual(
    activeTags(tags).map((t) => t.id),
    [1, 3],
  );
  assert.deepEqual(activeTags([]), []);
  assert.deepEqual(activeTags(undefined), []);
});

test("the dialog shows the fields the tag type needs", () => {
  assert.deepEqual(fieldsFor({ requires_zone: true, requires_hunt_no: false }), {
    zone: true,
    huntNo: false,
  });
  assert.deepEqual(fieldsFor({ requires_zone: false, requires_hunt_no: true }), {
    zone: false,
    huntNo: true,
  });
  assert.deepEqual(fieldsFor(undefined), { zone: false, huntNo: false });
});

test("tag title and detail", () => {
  assert.equal(tagTitle(deer), "🦌 Regular deer tag");
  assert.equal(tagTitle({ ...deer, icon: null }), "🎟️ Regular deer tag");
  assert.equal(tagTitle({ ...deer, label: "Second deer tag" }), "🦌 Second deer tag");

  assert.equal(tagDetail(deer), "");
  assert.equal(tagDetail(elk), "Zone Sawtooth");
  assert.equal(tagDetail(controlled), "Hunt #1002");
  assert.equal(
    tagDetail({ ...elk, notes: "drew in June", filled: true }),
    "Zone Sawtooth · drew in June · filled — excluded from the map",
  );
});

test("chip labels carry the zone and hunt number", () => {
  assert.equal(tagChipLabel(deer), "🦌 Regular deer tag");
  assert.equal(tagChipLabel(elk), "🫎 Elk A tag (Sawtooth)");
  assert.equal(tagChipLabel(controlled), "🎟️ Controlled hunt tag #1002");
});

test("the map breadcrumb lists only the tags that still count", () => {
  assert.equal(tagsSummary([]), "No tags yet");
  assert.equal(tagsSummary([{ ...deer, filled: true }]), "No tags yet");
  assert.equal(
    tagsSummary([deer, elk, controlled]),
    "🦌 Regular deer tag · 🫎 Elk A tag (Sawtooth) · 🎟️ Controlled hunt tag #1002",
  );
  assert.equal(tagsSummary([deer, { ...elk, filled: true }]), "🦌 Regular deer tag");
  // The breadcrumb and the filter chips use the same label helper.
  assert.equal(tagsSummary([elk]), tagChipLabel(elk));
});

// ---- T-17c: the controlled-hunt card and the Add-tag dialog's live lookup ----------

test("a controlled tag card reads as its hunt", () => {
  assert.equal(huntUnits(hunt1002), "Unit 11");
  assert.equal(huntDates(hunt1002), "Oct 10 – Nov 3");
  assert.equal(huntSummary(hunt1002), "Deer · Unit 11 · Oct 10 – Nov 3");
  // §12 T-17c: 'Tag cards show "Controlled #1002 · Deer · Unit 11 · Oct 10–Nov 3"'.
  assert.equal(tagTitle(tag1002), "🦌 Controlled #1002");
  assert.equal(tagDetail(tag1002), "Deer · Unit 11 · Oct 10 – Nov 3");
  assert.equal(tagChipLabel(tag1002), "🦌 Controlled #1002");
});

test("hunt units: several units, a partial area, and the overflow count", () => {
  const u = (unit, coverage = "full") => ({ unit, coverage });
  assert.equal(huntUnits({ units: [u("11"), u("12")] }), "Units 11, 12");
  assert.equal(huntUnits({ units: [u("38", "partial")] }), "Part of Unit 38");
  assert.equal(
    huntUnits({ units: [u("1"), u("2"), u("3"), u("4"), u("5")] }),
    "Units 1, 2, 3 +2",
  );
  assert.equal(huntUnits({ units: [] }), "no units yet");
});

test("a controlled tag whose hunt the regulations don't have says so", () => {
  const orphan = { ...controlled, license_year: 2026, hunt: null };
  assert.equal(tagDetail(orphan), "Hunt #1002 · not a hunt in the 2026 regulations yet");
  // A tag loaded without the lookup (no `hunt` key at all) keeps the T-09 wording.
  assert.equal(tagDetail(controlled), "Hunt #1002");
});

test("the preview card shows the verbatim area description and its page", () => {
  const hunt = {
    ...hunt1002,
    hunt_number: "1028",
    hunt_area: "38-1",
    description: "That portion of Unit 38 lying south of Interstate 84.",
    description_page: 33,
    units: [{ unit: "38", coverage: "partial", coverage_frac: 0.41 }],
  };
  const text = huntCard(hunt)
    .map((el) => el.textContent)
    .join(" ");
  assert.match(text, /🦌 Deer · Controlled #1028/);
  assert.match(text, /Part of Unit 38/);
  assert.match(text, /That portion of Unit 38 lying south of Interstate 84\./);
  const link = huntCard(hunt).at(-1).querySelectorAll("a")[0];
  assert.equal(link.textContent, "p.33");
  assert.match(link.href, /\/api\/documents\/7\/pdf\?page=33#page=33/);
});

test("an unpublished hunt is previewed with its status", () => {
  const text = huntCard({ ...hunt1002, status: "staged", published: false })
    .map((el) => el.textContent)
    .join(" ");
  assert.match(text, /staged/);
  assert.match(text, /isn't published yet/);
});

test("the lookup drives Save: found enables, unknown disables", async () => {
  const states = [];
  const preview = createHuntPreview({
    fetchHunt: async (year, number) => {
      if (number !== "1002") {
        const err = new Error(`no hunt #${number} in the ${year} regulations`);
        err.name = "ApiError";
        throw err;
      }
      return { hunt: hunt1002 };
    },
    onState: (s) => states.push(s),
  });
  assert.equal(preview.el.dataset.state, "idle");

  assert.equal(await preview.lookup(2026, "1002"), hunt1002);
  assert.equal(preview.el.dataset.state, "found");
  assert.match(preview.el.textContent, /Controlled #1002/);
  assert.deepEqual(states.map((s) => s.pending), [true, false]);
  assert.equal(states.at(-1).hunt, hunt1002);

  assert.equal(await preview.lookup(2026, "9999"), null);
  assert.equal(preview.el.dataset.state, "missing");
  assert.match(preview.el.textContent, /no hunt #9999 in the 2026 regulations/);
  assert.equal(states.at(-1).hunt, null);

  await preview.lookup(2026, "");
  assert.equal(preview.el.dataset.state, "idle");
  assert.equal(states.at(-1).pending, false);
});

test("a slow lookup can never overwrite a newer one", async () => {
  const delay = { 100: 20, 1002: 1 };
  const preview = createHuntPreview({
    fetchHunt: async (year, number) => {
      await new Promise((r) => setTimeout(r, delay[number] ?? 0));
      return { hunt: { ...hunt1002, hunt_number: number } };
    },
  });
  const slow = preview.lookup(2026, "100"); // typed first, answers last
  const fast = preview.lookup(2026, "1002");
  const [slowResult, fastResult] = await Promise.all([slow, fast]);
  assert.equal(slowResult, null); // dropped: a later keystroke won
  assert.equal(fastResult.hunt_number, "1002");
  assert.match(preview.el.textContent, /Controlled #1002/);
});
