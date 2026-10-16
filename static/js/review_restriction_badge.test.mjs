// Informational §5.1 codes (bait/dogs/calls/methods) render neutral, never yellow.
import { test } from "node:test";
import assert from "node:assert/strict";
import { restrictionBadgeKind } from "./review.js";

test("bait and dogs prohibited are neutral, not yellow", () => {
  assert.equal(restrictionBadgeKind("bait_prohibited"), "neutral");
  assert.equal(restrictionBadgeKind("dogs_prohibited"), "neutral");
});

test("narrowing codes and unknown codes stay yellow", () => {
  assert.equal(restrictionBadgeKind("whitetail_only"), "restricted");
  assert.equal(restrictionBadgeKind("see_notes"), "restricted");
  assert.equal(restrictionBadgeKind("made_up_code"), "restricted");
});
