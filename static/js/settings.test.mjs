// T-21 Settings tab: pure helpers plus the three cards rendered into
// tests/js/fakedom.mjs. The real-browser check (puppeteer + Lighthouse) is in the PR.
// Run: node --test static/js/settings.test.mjs

import { test, beforeEach } from "node:test";
import assert from "node:assert/strict";

import { installFakeDom } from "../../tests/js/fakedom.mjs";
import {
  RESET_PHRASE,
  WEEKEND_DEFS,
  backupName,
  createBackupCard,
  createDangerCard,
  createProfileCard,
  profilePatch,
  resetArmed,
  resetSummary,
  restoreSummary,
} from "./settings.js";

let doc;
beforeEach(() => {
  doc = installFakeDom();
});

const SPECIES = [
  { id: "deer", label: "Deer", icon: "🦌" },
  { id: "elk", label: "Elk", icon: "🫎" },
];
const tick = () => new Promise((r) => setTimeout(r, 0));

// ---- pure helpers ----------------------------------------------------------
test("resetArmed only accepts the exact phrase", () => {
  assert.equal(RESET_PHRASE, "RESET");
  assert.equal(resetArmed("RESET"), true);
  assert.equal(resetArmed("  RESET  "), true, "a trailing space from a paste still counts");
  for (const bad of ["", "reset", "Reset", "RESETT", "RESE", null, undefined, "yes"]) {
    assert.equal(resetArmed(bad), false, JSON.stringify(bad));
  }
});

test("profilePatch sends exactly the three fields PUT /api/profile accepts", () => {
  assert.deepEqual(profilePatch({ youth: true, weekendDef: "sat_sun", species: "elk" }), {
    youth: true,
    weekend_def: "sat_sun",
    default_species: "elk",
  });
  // A checkbox's `checked` is already boolean, but a missing value must not send `undefined`.
  assert.equal(profilePatch({ weekendDef: "fri_sun", species: "deer" }).youth, false);
});

test("backupName keeps only the filename of the server-side path", () => {
  assert.equal(backupName("/Users/j/data/planner.db.bak-20261016T090500"), "planner.db.bak-20261016T090500");
  assert.equal(backupName("planner.db.bak-1"), "planner.db.bak-1");
  assert.equal(backupName(null), "");
});

test("restoreSummary names the kept backup and only mentions a migration when there was one", () => {
  const same = restoreSummary({ backup: "/d/planner.db.bak-X", restored_from: 2, schema_version: 2 });
  assert.match(same, /planner\.db\.bak-X/);
  assert.doesNotMatch(same, /migrated/);
  const migrated = restoreSummary({ backup: "/d/b", restored_from: 1, schema_version: 2 });
  assert.match(migrated, /migrated from schema v1 to v2/);
});

test("resetSummary lists what was deleted on disk", () => {
  assert.match(resetSummary({ backup: "/d/b-1", removed: ["regs", "cache"] }), /regs, cache/);
  assert.doesNotMatch(resetSummary({ backup: "/d/b-1", removed: [] }), /deleted/);
});

test("WEEKEND_DEFS offers Fri–Sun first, the D5 default", () => {
  assert.deepEqual(WEEKEND_DEFS.map(([v]) => v), ["fri_sun", "sat_sun"]);
});

// ---- profile card ----------------------------------------------------------
test("createProfileCard renders the saved profile and saves on every change", async () => {
  const sent = [];
  const el = createProfileCard({
    profile: { youth: 1, weekend_def: "sat_sun", default_species: "elk" },
    species: SPECIES,
    idPrefix: "settings",
    save: (body) => {
      sent.push(body);
      return Promise.resolve({ profile: { ...body } });
    },
  });
  doc.body.append(el);

  assert.equal(el.controls.youth.checked, true);
  assert.equal(el.controls.youth.id, "settings-youth");
  assert.equal(el.controls.weekend.value, "sat_sun");
  assert.equal(el.controls.species.value, "elk");
  assert.deepEqual(
    el.controls.species.children.map((o) => o.value),
    ["deer", "elk"],
  );
  // D1: there is no residency control anywhere on the form.
  assert.equal(el.textContent.toLowerCase().includes("nonresident setting"), true);
  assert.equal(el.querySelector("#settings-resident"), null);

  el.controls.youth.checked = false;
  el.controls.youth.dispatch("change");
  await tick();
  assert.deepEqual(sent, [{ youth: false, weekend_def: "sat_sun", default_species: "elk" }]);

  el.controls.weekend.value = "fri_sun";
  el.controls.weekend.dispatch("change");
  await tick();
  assert.equal(sent.length, 2);
  assert.equal(sent[1].weekend_def, "fri_sun");
});

test("createProfileCard reports the saved profile back to its host tab", async () => {
  let got = null;
  const el = createProfileCard({
    profile: { youth: 0, weekend_def: "fri_sun", default_species: "deer" },
    species: SPECIES,
    onSaved: (p) => {
      got = p;
    },
    save: () => Promise.resolve({ profile: { youth: 1, weekend_def: "fri_sun", default_species: "deer" } }),
  });
  el.controls.youth.checked = true;
  el.controls.youth.dispatch("change");
  await tick();
  assert.deepEqual(got, { youth: 1, weekend_def: "fri_sun", default_species: "deer" });
});

test("a failed profile save leaves the host's copy alone", async () => {
  let got = "untouched";
  const el = createProfileCard({
    profile: {},
    species: SPECIES,
    onSaved: (p) => {
      got = p;
    },
    save: () => Promise.reject(new Error("nope")),
  });
  el.controls.species.dispatch("change");
  await tick();
  assert.equal(got, "untouched");
});

// ---- backup card -----------------------------------------------------------
test("the download button asks for the backup", () => {
  let hits = 0;
  const el = createBackupCard({ download: () => hits++ });
  el.controls.download.click();
  assert.equal(hits, 1);
});

test("restore does nothing without a file, and asks before replacing the database", async () => {
  const calls = [];
  let reloaded = 0;
  const el = createBackupCard({
    restore: (f) => {
      calls.push(f.name);
      return Promise.resolve({ backup: "/d/planner.db.bak-X", restored_from: 2, schema_version: 2 });
    },
    reload: () => reloaded++,
    confirm: () => false,
  });
  doc.body.append(el);

  el.controls.restore.click();
  await tick();
  assert.deepEqual(calls, [], "no file picked: nothing is sent");

  el.controls.picker.files = [{ name: "planner-backup.db" }];
  el.controls.restore.click();
  await tick();
  assert.deepEqual(calls, [], "declining the confirmation sends nothing");
  assert.equal(reloaded, 0);
});

test("a confirmed restore uploads the file and reloads the stale page", async () => {
  const calls = [];
  let reloaded = 0;
  const el = createBackupCard({
    restore: (f) => {
      calls.push(f.name);
      return Promise.resolve({ backup: "/d/planner.db.bak-X", restored_from: 1, schema_version: 2 });
    },
    reload: () => reloaded++,
    confirm: () => true,
  });
  doc.body.append(el);
  el.controls.picker.files = [{ name: "planner-backup-20261016T090500.db" }];
  el.controls.restore.click();
  await tick();
  assert.deepEqual(calls, ["planner-backup-20261016T090500.db"]);
  assert.equal(reloaded, 1);
  assert.equal(el.controls.restore.disabled, false, "the button comes back after the upload");
  const toastText = doc.body.querySelector(".toast-success").textContent;
  assert.match(toastText, /migrated from schema v1 to v2/);
});

test("a failed restore does not reload and re-enables the button", async () => {
  let reloaded = 0;
  const el = createBackupCard({
    restore: () => Promise.reject(new Error("that file is not a SQLite database")),
    reload: () => reloaded++,
    confirm: () => true,
  });
  doc.body.append(el);
  el.controls.picker.files = [{ name: "notes.txt" }];
  el.controls.restore.click();
  await tick();
  assert.equal(reloaded, 0);
  assert.equal(el.controls.restore.disabled, false);
  assert.match(doc.body.querySelector(".toast-error").textContent, /not a SQLite database/);
});

// ---- danger zone -----------------------------------------------------------
test("the reset button stays disabled until RESET is typed exactly", () => {
  const el = createDangerCard({ reset: () => Promise.resolve({}) });
  const { input, reset } = el.controls;
  assert.equal(reset.disabled, true);
  for (const typed of ["R", "rese", "reset", "RESETT"]) {
    input.value = typed;
    input.dispatch("input");
    assert.equal(reset.disabled, true, typed);
  }
  input.value = "RESET";
  input.dispatch("input");
  assert.equal(reset.disabled, false);
  input.value = "";
  input.dispatch("input");
  assert.equal(reset.disabled, true, "clearing the box re-arms the safety");
});

test("a disabled reset button fires nothing even if clicked", async () => {
  let hits = 0;
  const el = createDangerCard({ reset: () => { hits++; return Promise.resolve({}); } });
  el.controls.reset.click();
  await tick();
  assert.equal(hits, 0);
});

test("a confirmed reset clears the box, reports what was kept and reloads", async () => {
  let reloaded = 0;
  const el = createDangerCard({
    reset: () => Promise.resolve({ backup: "/d/planner.db.bak-20261016T090500", removed: ["regs"] }),
    reload: () => reloaded++,
  });
  doc.body.append(el);
  el.controls.input.value = "RESET";
  el.controls.input.dispatch("input");
  el.controls.reset.click();
  await tick();
  assert.equal(reloaded, 1);
  assert.equal(el.controls.input.value, "");
  const msg = doc.body.querySelector(".toast-success").textContent;
  assert.match(msg, /planner\.db\.bak-20261016T090500/);
  assert.match(msg, /regs/);
});

test("a failed reset keeps the data and re-enables the armed button", async () => {
  let reloaded = 0;
  const el = createDangerCard({
    reset: () => Promise.reject(new Error("server gone")),
    reload: () => reloaded++,
  });
  doc.body.append(el);
  el.controls.input.value = "RESET";
  el.controls.input.dispatch("input");
  el.controls.reset.click();
  await tick();
  assert.equal(reloaded, 0);
  assert.equal(el.controls.reset.disabled, false);
  assert.match(doc.body.querySelector(".toast-error").textContent, /server gone/);
});
