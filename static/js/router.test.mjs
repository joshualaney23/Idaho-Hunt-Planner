// static/js/router.js (T-03). Run: node --test static/js/router.test.mjs
import { test } from "node:test";
import assert from "node:assert/strict";

import { DEFAULT_TAB, TABS, parseRoute, tabHash } from "./router.js";

test("five tabs in §4.2 order", () => {
  assert.deepEqual(
    TABS.map((t) => t.id),
    ["map", "tags", "seasons", "regs", "settings"],
  );
  assert.equal(DEFAULT_TAB, "map");
});

for (const [hash, tab, rest, known] of [
  ["#/map", "map", [], true],
  ["#/tags", "tags", [], true],
  ["#/seasons", "seasons", [], true],
  ["#/regs", "regs", [], true],
  ["#/settings", "settings", [], true],
  ["#/regs/12", "regs", ["12"], true],
  ["#/TAGS/", "tags", [], true],
  ["/settings", "settings", [], true],
  ["", "map", [], false],
  ["#", "map", [], false],
  ["#/", "map", [], false],
  ["#/nope", "map", [], false],
  [undefined, "map", [], false],
]) {
  test(`parseRoute(${JSON.stringify(hash)})`, () => {
    const r = parseRoute(hash);
    assert.equal(r.tab, tab);
    assert.deepEqual(r.rest, rest);
    assert.equal(r.known, known);
  });
}

test("parseRoute keeps the query string (T-07 shareable filters)", () => {
  const r = parseRoute("#/map?species=elk&start=2026-10-16");
  assert.equal(r.tab, "map");
  assert.equal(r.query.get("species"), "elk");
  assert.equal(r.query.get("start"), "2026-10-16");
});

test("tabHash", () => {
  assert.equal(tabHash("seasons"), "#/seasons");
  assert.equal(tabHash("bogus"), "#/map");
});
