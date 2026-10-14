// T-09: pure helpers of the My Tags tab (the DOM rendering is verified in a
// real browser in the T-09 PR with puppeteer).

import { test } from "node:test";
import assert from "node:assert/strict";

import {
  TAGS_CHANGED,
  activeTags,
  fieldsFor,
  tagChipLabel,
  tagDetail,
  tagTitle,
  tagsSummary,
} from "./tags.js";

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
