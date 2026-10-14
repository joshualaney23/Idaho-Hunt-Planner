// static/js/ui.js components (T-03), run against tests/js/fakedom.mjs.
// Run: node --test static/js/ui.test.mjs
import { test, beforeEach } from "node:test";
import assert from "node:assert/strict";

import { installFakeDom } from "../../tests/js/fakedom.mjs";
import { badge, card, chipGroup, toast } from "./ui.js";

let doc;
beforeEach(() => {
  doc = installFakeDom();
});

const pressed = (el) =>
  el.children.filter((b) => b.getAttribute("aria-pressed") === "true").map((b) => b.dataset.value);

test("chipGroup renders <button type=button aria-pressed> per item", () => {
  const el = chipGroup({ id: "w", items: [["all", "All"], ["rifle", "Rifle"]], value: "all" });
  assert.equal(el.id, "w");
  assert.equal(el.getAttribute("role"), "group");
  assert.equal(el.children.length, 2);
  for (const b of el.children) {
    assert.equal(b.tagName, "BUTTON");
    assert.equal(b.type, "button");
    assert.equal(b.className, "chip");
  }
  assert.deepEqual(el.children.map((b) => b.textContent), ["All", "Rifle"]);
  assert.deepEqual(pressed(el), ["all"]);
  assert.equal(el.children[1].getAttribute("aria-pressed"), "false");
});

test("chipGroup single: click selects one value and reports it", () => {
  const seen = [];
  const el = chipGroup({ items: ["a", "b", "c"], value: "a", onChange: (v) => seen.push(v) });
  el.children[2].click();
  assert.deepEqual(pressed(el), ["c"]);
  el.children[2].click(); // re-click is a no-op, not a deselect
  assert.deepEqual(pressed(el), ["c"]);
  assert.deepEqual(seen, ["c"]);
  assert.equal(el.getValue(), "c");
});

test("chipGroup multi: toggles, reports values in item order", () => {
  const seen = [];
  const el = chipGroup({
    items: [
      { value: "archery", label: "Archery" },
      { value: "muzzleloader", label: "Muzzleloader" },
    ],
    multi: true,
    value: new Set(["muzzleloader"]),
    onChange: (v) => seen.push(v),
  });
  assert.deepEqual(pressed(el), ["muzzleloader"]);
  el.children[0].click();
  el.children[1].click();
  assert.deepEqual(seen, [["archery", "muzzleloader"], ["archery"]]);
  assert.deepEqual(el.getValue(), ["archery"]);
});

test("chipGroup disabled: predicate, list or true; disabled chips ignore clicks", () => {
  let calls = 0;
  const el = chipGroup({
    items: ["deer", "elk"],
    value: "deer",
    disabled: (v) => v === "elk",
    onChange: () => calls++,
  });
  assert.deepEqual(el.children.map((b) => b.disabled), [false, true]);
  el.children[1].click();
  assert.equal(calls, 0);
  assert.deepEqual(pressed(el), ["deer"]);
  assert.deepEqual(
    chipGroup({ items: ["a", "b"], disabled: ["b"] }).children.map((b) => b.disabled),
    [false, true],
  );
  assert.ok(chipGroup({ items: ["a", "b"], disabled: true }).children.every((b) => b.disabled));
});

test("chipGroup renders into an existing container by id and supports setValue", () => {
  const host = doc.createElement("div");
  host.id = "species";
  doc.body.appendChild(host);
  const el = chipGroup({ id: "species", items: ["deer", "elk"], value: "deer", label: "Species" });
  assert.equal(el, host);
  assert.equal(el.getAttribute("aria-label"), "Species");
  el.setValue("elk");
  assert.deepEqual(pressed(el), ["elk"]);
  chipGroup({ id: "species", items: ["deer"], value: null });
  assert.equal(host.children.length, 1, "re-render replaces the chips");
  assert.deepEqual(pressed(host), []);
});

test("card(title, ...children)", () => {
  const c = card("My Tags", "placeholder text");
  assert.equal(c.tagName, "SECTION");
  assert.equal(c.className, "card");
  assert.equal(c.children[0].tagName, "H2");
  assert.equal(c.children[0].textContent, "My Tags");
  assert.equal(c.children[1].className, "placeholder");
  assert.equal(c.children[1].textContent, "placeholder text");
  assert.equal(card("Only").children.length, 1);
});

test("badge(kind, text) maps kind to a b-<kind> class", () => {
  const b = badge("archery", "archery");
  assert.equal(b.tagName, "SPAN");
  assert.equal(b.className, "badge b-archery");
  assert.equal(b.textContent, "archery");
  assert.equal(badge("Short Range", "short").className, "badge b-short-range");
  assert.equal(badge("open").textContent, "open");
});

test("toast(msg, level) appends to a polite live region; errors are alerts", () => {
  const t = toast("Saved", "success", { ms: 0 });
  const host = doc.getElementById("toasts");
  assert.equal(host.getAttribute("aria-live"), "polite");
  assert.equal(t.className, "toast toast-success");
  assert.equal(t.textContent, "Saved");
  const e = toast("Parse failed", "error");
  assert.equal(e.getAttribute("role"), "alert");
  assert.equal(toast("x", "bogus", { ms: 0 }).className, "toast toast-info");
  assert.equal(host.children.length, 3);
  e.click(); // click dismisses
  assert.equal(host.children.length, 2);
});

test("toast auto-dismisses non-errors after ms", async () => {
  const t = toast("bye", "info", { ms: 5 });
  assert.ok(t.parentNode);
  await new Promise((r) => setTimeout(r, 20));
  assert.equal(t.parentNode, null);
});
