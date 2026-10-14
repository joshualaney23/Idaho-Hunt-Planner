// static/js/dates.js against the shared fixture (T-05). tests/test_dates.py uses the same file.
// Run: node --test static/js/dates.test.mjs   (set TZ=America/Boise to exercise DST)
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

import * as dates from "./dates.js";

const CASES = JSON.parse(
  readFileSync(new URL("../../tests/fixtures/dates_cases.json", import.meta.url), "utf8"),
);
const WEEKDAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const isoRange = (r) => (r === null ? null : r.map(dates.toISO));

test("fixture covers every weekday for both weekend defs", () => {
  const covered = new Set(CASES.default_range.map((c) => `${c.weekday}/${c.weekend_def}`));
  for (const w of WEEKDAYS) {
    for (const d of dates.WEEKEND_DEFS) assert.ok(covered.has(`${w}/${d}`), `${w}/${d}`);
  }
});

for (const c of CASES.default_range) {
  test(`defaultRange ${c.today} ${c.weekday} ${c.weekend_def}`, () => {
    const today = dates.parseISO(c.today);
    assert.equal(WEEKDAYS[today.getDay()], c.weekday, "fixture weekday label is wrong");
    const r = dates.defaultRange(today, c.weekend_def);
    assert.deepEqual(isoRange(r), c.expect);
    for (const d of r) assert.equal(d.getHours(), 12, "results stay at local noon");
  });
}

test("default weekend def is fri_sun", () => {
  const wed = dates.parseISO("2026-10-14");
  assert.equal(dates.DEFAULT_WEEKEND_DEF, "fri_sun");
  assert.deepEqual(isoRange(dates.defaultRange(wed)), isoRange(dates.defaultRange(wed, "fri_sun")));
});

test("unknown weekend def throws", () => {
  assert.throws(() => dates.defaultRange(dates.parseISO("2026-10-14"), "thu_sun"));
});

for (const c of CASES.presets) {
  test(`presets ${c.today} ${c.weekend_def}`, () => {
    const got = dates.presets(dates.parseISO(c.today), c.weekend_def, c.seasons);
    const iso = Object.fromEntries(Object.entries(got).map(([k, v]) => [k, isoRange(v)]));
    assert.deepEqual(iso, c.expect);
  });
}

for (const c of CASES.overlap) {
  test(`overlap ${c.a} x ${c.b}`, () => {
    const [a1, a2, b1, b2] = [...c.a, ...c.b].map(dates.parseISO);
    assert.equal(dates.overlap(a1, a2, b1, b2), c.expect);
    assert.equal(dates.overlap(b1, b2, a1, a2), c.expect);
  });
}

for (const c of CASES.days) {
  test(`days ${c.a}..${c.b}`, () => {
    assert.equal(dates.days(dates.parseISO(c.a), dates.parseISO(c.b)), c.expect);
  });
}

for (const c of CASES.fmt_short) {
  test(`fmtShort ${c.date}`, () => {
    assert.equal(dates.fmtShort(dates.parseISO(c.date)), c.expect);
  });
}

for (const bad of CASES.parse_iso_invalid) {
  test(`parseISO rejects ${JSON.stringify(bad)}`, () => {
    assert.throws(() => dates.parseISO(bad));
  });
}

test("toISO round-trips and localToday is local noon", () => {
  assert.equal(dates.toISO(dates.parseISO("2027-01-03")), "2027-01-03");
  const t = dates.localToday(new Date(2026, 10, 1, 0, 30)); // 00:30 local on DST day
  assert.equal(dates.toISO(t), "2026-11-01");
  assert.equal(t.getHours(), 12);
});

test("snake_case aliases match the Python names", () => {
  assert.equal(dates.default_range, dates.defaultRange);
  assert.equal(dates.parse_iso, dates.parseISO);
});
