// static/js/regs.js (T-15b): the upload card's four states, the documents table's
// year grouping and badge class per status, and the `#/regs/<docId>` route — all
// against tests/js/fakedom.mjs. The real drag-and-drop, routing and Lighthouse
// numbers are checked in Chrome with puppeteer in the T-15b PR.
//
// Run: node --test static/js/regs.test.mjs
import { test, beforeEach } from "node:test";
import assert from "node:assert/strict";

import { installFakeDom } from "../../tests/js/fakedom.mjs";
import { textEl } from "./ui.js";
import {
  UPLOAD_IDLE,
  createUploadCard,
  docCounts,
  groupByYear,
  initRegs,
  kindLabel,
  regsRoute,
  renderDocumentDetail,
  renderDocuments,
  shortSha,
  statusBadge,
  uploadView,
  uploadedOn,
  yearLabel,
  createUpdatesCard,
  lastCheckedLabel,
  sinceLabel,
  updatesSummary,
  updatesView,
} from "./regs.js";

beforeEach(() => {
  installFakeDom();
});

const doc = (over = {}) => ({
  id: 1,
  kind: "big_game",
  title: "2026 Big Game Seasons & Rules",
  filename: "seasons-rules-big-game-2026.pdf",
  license_year: 2026,
  year_end: 2026,
  pages: 125,
  sha256: "a".repeat(64),
  status: "needs_review",
  priority: 0,
  imported_at: "2026-10-15T18:12:02",
  seasons: { staged: 704 },
  checks: { pass: 11 },
  ...over,
});

// A File stand-in: regs.js only needs `name` and `arrayBuffer()`.
const fakeFile = (name = "big-game.pdf", body = "%PDF-1.4 hello") => ({
  name,
  arrayBuffer: async () => new TextEncoder().encode(body).buffer,
});
// sha256("%PDF-1.4 hello"), computed with `shasum -a 256` (not by this module).
const FILE_SHA = "f31cdee0b4d4fdae0638872f6bb7d0e6ee041386a9055d5e64c25d9d35dc88d1";

const tick = () => new Promise((r) => setTimeout(r, 0));
/** Wait for an async paint (the sha digest resolves over a few microtasks). */
async function until(fn, tries = 50) {
  for (let i = 0; i < tries; i++) {
    if (fn()) return true;
    await tick();
  }
  return false;
}

// ---- pure helpers ----------------------------------------------------------
test("every document status has its own badge class", () => {
  assert.deepEqual(statusBadge("uploaded"), {
    kind: "uploaded",
    className: "badge b-uploaded",
    label: "Uploaded",
  });
  assert.equal(statusBadge("parsing").className, "badge b-parsing");
  assert.deepEqual(statusBadge("needs_review"), {
    kind: "needs-review",
    className: "badge b-needs-review",
    label: "Needs review",
  });
  assert.equal(statusBadge("published").className, "badge b-published");
  assert.equal(statusBadge("superseded").className, "badge b-superseded");
  assert.equal(statusBadge("failed").className, "badge b-failed");
  // An unknown status never borrows another status's color.
  assert.deepEqual(statusBadge("weird"), {
    kind: "unknown",
    className: "badge b-unknown",
    label: "Unknown",
  });
  assert.equal(statusBadge(null).kind, "unknown");
});

test("labels: kind, license year span, counts, upload date", () => {
  assert.equal(kindLabel("big_game"), "Big Game");
  assert.equal(kindLabel("moose_sheep_goat"), "Moose · Sheep · Goat");
  assert.equal(kindLabel("future_kind"), "future_kind");
  assert.equal(kindLabel(undefined), "—");

  assert.equal(yearLabel({ license_year: 2026, year_end: 2026 }), "2026");
  assert.equal(yearLabel({ license_year: 2026, year_end: 2027 }), "2026–27");

  assert.equal(docCounts(doc()), "125 pages · 704 rows · 704 staged");
  assert.equal(
    docCounts(doc({ seasons: { published: 235, superseded: 12 } })),
    "125 pages · 247 rows",
  );
  assert.equal(docCounts({}), "0 pages · 0 rows");

  assert.equal(uploadedOn(doc()), "2026-10-15");
  assert.equal(uploadedOn({}), "—");
  assert.equal(shortSha("a".repeat(64)), "aaaaaaaaaaaa");
  assert.equal(shortSha(null), "");
});

test("documents group by license year, newest first, supplements on top", () => {
  const docs = [
    doc({ id: 1, license_year: 2025 }),
    doc({ id: 2, license_year: 2026 }),
    doc({ id: 3, license_year: 2026, kind: "supplemental", priority: 10 }),
    doc({ id: 4, license_year: 2026 }),
  ];
  const groups = groupByYear(docs);
  assert.deepEqual(
    groups.map((g) => [g.year, g.label]),
    [
      [2026, "2026 license year"],
      [2025, "2025 license year"],
    ],
  );
  // priority first (a supplemental overrides its base, §8.5), then newest id.
  assert.deepEqual(
    groups[0].documents.map((d) => d.id),
    [3, 4, 2],
  );
  assert.deepEqual(
    groups[1].documents.map((d) => d.id),
    [1],
  );
  assert.deepEqual(groupByYear([]), []);
  assert.deepEqual(groupByYear(undefined), []);
});

test("#/regs routes: the list, one document, and junk", () => {
  assert.deepEqual(regsRoute([]), { view: "list" });
  assert.deepEqual(regsRoute(undefined), { view: "list" });
  assert.deepEqual(regsRoute(["12"]), { view: "document", docId: 12 });
  assert.deepEqual(regsRoute(["new"]), { view: "list" });
  assert.deepEqual(regsRoute(["0"]), { view: "list" });
  assert.deepEqual(regsRoute(["-3"]), { view: "list" });
});

// ---- the upload card's four states ----------------------------------------
test("idle: the drop prompt, nothing busy", () => {
  const v = uploadView(UPLOAD_IDLE);
  assert.equal(v.state, "idle");
  assert.equal(v.busy, false);
  assert.equal(v.disabled, false);
  assert.equal(v.title, "Drop a regulation PDF here");
  assert.match(v.message, /choose a file/);
  assert.equal(v.link, null);
  assert.deepEqual(uploadView(undefined), v);
});

test("uploading: the filename, then the sha and the ~35 s warning", () => {
  const reading = uploadView({ status: "uploading", filename: "big-game.pdf" });
  assert.equal(reading.state, "uploading");
  assert.equal(reading.busy, true);
  assert.equal(reading.disabled, true);
  assert.equal(reading.title, "Importing big-game.pdf …");
  assert.equal(reading.message, "Reading the file …");

  const hashed = uploadView({ status: "uploading", filename: "big-game.pdf", sha: "b".repeat(64) });
  assert.equal(hashed.message, `sha256 bbbbbbbbbbbb · parsing and staging the rows takes about 35 seconds — keep this tab open.`);
  assert.equal(hashed.link, null);
});

test("success: sha, page/row counts, sections and a link to the rows", () => {
  const v = uploadView({
    status: "success",
    sha: "a".repeat(64),
    document: doc(),
    import: {
      document_id: 1,
      status: "needs_review",
      pages: 125,
      parsed: 712,
      staged: 704,
      sections: ["8.7.1 deer general", "8.7.2 controlled"],
    },
  });
  assert.equal(v.state, "success");
  assert.equal(v.tone, "success");
  assert.equal(v.busy, false);
  assert.equal(v.title, "Imported 2026 Big Game Seasons & Rules");
  assert.equal(
    v.message,
    "sha256 aaaaaaaaaaaa · 125 pages · 712 rows parsed · 704 staged · 8.7.1 deer general, 8.7.2 controlled",
  );
  assert.deepEqual(v.link, { href: "#/regs/1", text: "Review the staged rows" });
});

test("success with a failed import reads as an error, not a win", () => {
  const v = uploadView({
    status: "success",
    document: doc({ status: "failed", error: "no parser for this brochure" }),
    import: { status: "failed", error: "no parser for this brochure", pages: 125 },
  });
  assert.equal(v.tone, "error");
  assert.match(v.title, /could not be imported/);
  assert.equal(v.message, "no parser for this brochure");
  assert.deepEqual(v.link, { href: "#/regs/1", text: "Open the document" });
});

test("duplicate: a warning that links to the document that already holds the file", () => {
  const v = uploadView({
    status: "error",
    filename: "big-game.pdf",
    code: "duplicate_document",
    message: "this PDF is already imported",
    detail: { document_id: 7, status: "published", sha256: "c".repeat(64) },
  });
  assert.equal(v.state, "error");
  assert.equal(v.tone, "warn");
  assert.equal(v.title, "Already uploaded");
  assert.equal(v.message, "big-game.pdf is already imported (sha256 cccccccccccc).");
  assert.deepEqual(v.link, { href: "#/regs/7", text: "Open the existing document" });
});

test("any other error renders its message inline", () => {
  const v = uploadView({
    status: "error",
    filename: "notes.txt",
    code: "not_a_pdf",
    message: "that file is not a PDF",
    detail: { filename: "notes.txt" },
  });
  assert.equal(v.tone, "error");
  assert.equal(v.title, "Upload failed");
  assert.equal(v.message, "that file is not a PDF");
  assert.equal(v.link, null);
});

// ---- the upload card in the DOM --------------------------------------------
function mountCard(upload) {
  const calls = [];
  const card = createUploadCard({
    upload: (file) => {
      calls.push(file);
      return upload(file);
    },
  });
  document.body.appendChild(card);
  return {
    card,
    calls,
    zone: card.querySelector("#regs-dropzone"),
    input: card.querySelector("#regs-file"),
    title: () => card.querySelector("#regs-upload-status").children[0].textContent,
    message: () => card.querySelector("#regs-upload-status").children[1].textContent,
    link: () => card.querySelector("#regs-upload-status").children[2],
  };
}

test("the card paints idle → uploading → success, and the sha it shows is the file's", async () => {
  let release;
  const held = new Promise((r) => {
    release = r;
  });
  const ui = mountCard(() => held);
  assert.equal(ui.zone.dataset.state, "idle");
  assert.equal(ui.input.disabled, false);
  assert.equal(ui.link().hidden, true);

  const file = fakeFile();
  const done = ui.card.submit(file);
  // Synchronously: busy, disabled, still reading the bytes.
  assert.equal(ui.zone.dataset.state, "uploading");
  assert.equal(ui.zone.getAttribute("aria-busy"), "true");
  assert.equal(ui.input.disabled, true);
  assert.equal(ui.title(), "Importing big-game.pdf …");
  assert.equal(ui.message(), "Reading the file …");
  // …then the sha of the actual bytes (`printf '%PDF-1.4 hello' | shasum -a 256`).
  assert.ok(await until(() => ui.message().startsWith("sha256")), "the sha never appeared");
  assert.equal(
    ui.message(),
    `sha256 ${FILE_SHA.slice(0, 12)} · parsing and staging the rows takes about 35 seconds — keep this tab open.`,
  );

  release({ document: doc(), import: { pages: 125, parsed: 712, staged: 704, sections: [] } });
  await done;
  assert.equal(ui.zone.dataset.state, "success");
  assert.equal(ui.zone.dataset.tone, "success");
  assert.equal(ui.zone.hasAttribute("aria-busy"), false);
  assert.equal(ui.input.disabled, false);
  assert.equal(ui.title(), "Imported 2026 Big Game Seasons & Rules");
  assert.match(ui.message(), /125 pages · 712 rows parsed · 704 staged/);
  assert.equal(ui.link().hidden, false);
  assert.equal(ui.link().href, "#/regs/1");
});

test("a 409 duplicate renders inline as a warning with the existing document's link", async () => {
  const err = Object.assign(new Error("this PDF is already imported"), {
    code: "duplicate_document",
    detail: { document_id: 7, sha256: "c".repeat(64) },
    status: 409,
  });
  const ui = mountCard(() => Promise.reject(err));
  await ui.card.submit(fakeFile());
  assert.equal(ui.zone.dataset.state, "error");
  assert.equal(ui.zone.dataset.tone, "warn");
  assert.equal(ui.title(), "Already uploaded");
  assert.equal(ui.link().href, "#/regs/7");
  // The card recovers: the input is usable again.
  assert.equal(ui.input.disabled, false);
});

test("dropping a file and picking one take the same code path", async () => {
  const body = { document: doc(), import: { pages: 125, parsed: 712, staged: 704 } };
  const ui = mountCard(() => Promise.resolve(body));

  const dropped = fakeFile("dropped.pdf");
  ui.zone.dispatch("drop", { dataTransfer: { files: [dropped] } });
  await ui.card.pending();
  const afterDrop = ui.card.getState();

  const picked = fakeFile("picked.pdf");
  ui.input.files = [picked];
  ui.input.dispatch("change");
  await ui.card.pending();
  const afterPick = ui.card.getState();

  assert.deepEqual(ui.calls, [dropped, picked]); // one upload per file, same function
  assert.equal(afterDrop.status, "success");
  assert.deepEqual(
    { ...afterDrop, sha: null },
    { ...afterPick, sha: null },
    "both entry points leave the card in the same state",
  );
  assert.equal(afterDrop.sha, afterPick.sha); // same bytes → same sha

  // dragover must be cancelled or the browser navigates to the file instead.
  const over = ui.zone.dispatch("dragover", {});
  assert.equal(over.defaultPrevented, true);
  assert.equal(ui.zone.dataset.over, "1");
  ui.zone.dispatch("dragleave", {});
  assert.equal(ui.zone.dataset.over, undefined);
});

// ---- the documents table ----------------------------------------------------
test("the table groups by year and gives each row its status badge", () => {
  const host = document.createElement("div");
  renderDocuments(host, [
    doc({ id: 1, license_year: 2025, status: "published", seasons: { published: 235 } }),
    doc({ id: 2, license_year: 2026, status: "needs_review" }),
    doc({ id: 3, license_year: 2026, status: "failed", kind: "upland", year_end: 2027 }),
  ]);
  const years = host.querySelectorAll("section");
  assert.equal(years.length, 2);
  assert.equal(years[0].querySelector("h3").textContent, "2026 license year");
  assert.equal(years[1].querySelector("h3").textContent, "2025 license year");

  const rows = years[0].querySelectorAll("tr").slice(1); // drop the header row
  assert.deepEqual(
    rows.map((r) => r.dataset.docId),
    ["3", "2"],
  );
  assert.deepEqual(
    rows.map((r) => r.querySelector("span").className),
    ["badge b-failed", "badge b-needs-review"],
  );
  assert.deepEqual(
    rows.map((r) => r.querySelector("a").getAttribute("href")),
    ["#/regs/3", "#/regs/2"],
  );
  const cells = rows[0].querySelectorAll("td").map((td) => td.textContent);
  assert.equal(cells[2], "Upland · Turkey · Furbearer");
  assert.equal(cells[3], "2026–27");
  assert.equal(cells[4], "125 pages · 704 rows · 704 staged");
  assert.equal(cells[5], "2026-10-15");

  const published = years[1].querySelectorAll("tr")[1];
  assert.equal(published.querySelector("span").className, "badge b-published");

  // Column headers are real <th scope=col> so the table reads in a screen reader.
  const ths = years[0].querySelectorAll("th");
  assert.deepEqual(
    ths.map((th) => th.textContent),
    ["Status", "Document", "Kind", "Year", "Pages / rows", "Uploaded"],
  );
  assert.ok(ths.every((th) => th.getAttribute("scope") === "col"));
});

test("an empty list says so instead of rendering an empty table", () => {
  const host = document.createElement("div");
  renderDocuments(host, []);
  assert.equal(host.querySelector("table"), null);
  assert.match(host.textContent, /No regulation PDFs yet/);
});

test("the document detail heads the review screen with the PDF and a way back", () => {
  const host = document.createElement("div");
  const mounted = [];
  const mount = (id) => {
    mounted.push(id);
    return { el: textEl("div", "review screen", "review") };
  };
  renderDocumentDetail(host, 1, doc(), { mount });
  assert.deepEqual(mounted, [1]); // T-15c's screen is mounted for this document
  assert.match(host.textContent, /sha256 aaaaaaaaaaaa/);
  assert.match(host.textContent, /review screen/);
  const hrefs = host.querySelectorAll("a").map((a) => a.getAttribute("href"));
  assert.deepEqual(hrefs, ["#/regs", "/api/documents/1/pdf"]);

  // Unknown document (deep link before the list loads): still renders and still mounts.
  const bare = document.createElement("div");
  renderDocumentDetail(bare, 9, null, { mount });
  assert.match(bare.textContent, /Document 9/);
  assert.deepEqual(mounted, [1, 9]);
});

test("a failed import gets its error instead of a review screen", () => {
  const host = document.createElement("div");
  let mounts = 0;
  renderDocumentDetail(host, 3, doc({ status: "failed", error: "unreadable PDF" }), {
    mount: () => {
      mounts += 1;
      return { el: textEl("div", "nope") };
    },
  });
  assert.equal(mounts, 0);
  assert.match(host.textContent, /unreadable PDF/);
});

// ---- the tab -----------------------------------------------------------------
test("initRegs renders the list, and #/regs/<id> swaps in the detail panel", async () => {
  const panel = document.createElement("div");
  panel.id = "panel-regs";
  document.body.appendChild(panel);
  const tab = initRegs(panel, {
    load: async () => ({ documents: [doc({ id: 1 }), doc({ id: 2, license_year: 2025 })] }),
    upload: async () => ({ document: doc(), import: {} }),
    // The review screen has its own twin (review.test.mjs); here it is a stub, so this
    // test stays about routing.
    mount: () => ({ el: textEl("div", "review screen", "review") }),
  });
  await tab.ready;
  assert.equal(document.body.dataset.regsReady, "1");
  assert.equal(panel.querySelectorAll("table").length, 2); // one per year

  const list = panel.querySelector("#regs-list");
  const detailHost = panel.querySelector("#regs-detail-host");
  assert.equal(list.hidden, false);
  assert.equal(detailHost.hidden, true);

  tab.applyRoute(["2"]);
  assert.equal(list.hidden, true);
  assert.equal(detailHost.hidden, false);
  assert.match(detailHost.textContent, /review screen/);
  assert.equal(detailHost.querySelector("a").getAttribute("href"), "#/regs");

  tab.applyRoute([]);
  assert.equal(list.hidden, false);
  assert.equal(detailHost.hidden, true);
  assert.equal(detailHost.childElementCount, 0);
});

test("a successful upload refreshes the documents table", async () => {
  const panel = document.createElement("div");
  let documents = [];
  const tab = initRegs(panel, {
    load: async () => ({ documents }),
    upload: async () => {
      documents = [doc({ id: 5 })];
      return { document: doc({ id: 5 }), import: { pages: 125, parsed: 1, staged: 1 } };
    },
  });
  await tab.ready;
  assert.match(panel.textContent, /No regulation PDFs yet/);
  await tab.uploadCard.submit(fakeFile());
  await tick();
  assert.equal(panel.querySelectorAll("table").length, 1);
  assert.deepEqual(
    tab.documents().map((d) => d.id),
    [5],
  );
});


// ---- "Get latest regs" (T-28) ----------------------------------------------
const checkBody = (over = {}) => ({
  checked_at: "2026-10-16 09:00:00",
  sources: [{ url: "https://idfg.idaho.gov/rules", role: "page", status: "ok" }],
  found: [],
  imported: [],
  ...over,
});

test("how long ago the last check was, in plain words", () => {
  const now = Date.parse("2026-10-19T12:00:00");
  assert.equal(sinceLabel("2026-10-19 08:00:00", now), "today");
  assert.equal(sinceLabel("2026-10-18 08:00:00", now), "yesterday");
  assert.equal(sinceLabel("2026-10-16 09:00:00", now), "3 days ago");
  assert.equal(sinceLabel(null, now), "");
  assert.equal(sinceLabel("not a date", now), "");
  assert.equal(lastCheckedLabel("2026-10-16 09:00:00", now), "Last checked 3 days ago.");
  assert.equal(lastCheckedLabel(null, now), "IDFG has not been checked yet.");
});

test("a check that found nothing says so", () => {
  const summary = updatesSummary(
    checkBody({ found: [{ filename: "a.pdf", state: "known", document_id: 1 }] }),
  );
  assert.equal(summary.title, "Up to date");
  assert.equal(summary.tone, "info");
  assert.deepEqual(summary.items, []);
});

test("staged documents are counted and linked, a replacement says so", () => {
  const summary = updatesSummary(
    checkBody({
      found: [
        { filename: "big.pdf", state: "new", document_id: 4 },
        { filename: "upland.pdf", state: "changed", document_id: 5 },
        { filename: "old.pdf", state: "known", document_id: 1 },
      ],
      imported: [4, 5],
    }),
  );
  assert.equal(summary.title, "2 new documents staged for review");
  assert.equal(summary.tone, "success");
  assert.deepEqual(summary.items, [
    { text: "big.pdf", href: "#/regs/4" },
    { text: "upland.pdf (replaces an older version)", href: "#/regs/5" },
  ]);
  assert.match(summary.message, /1 replaces a document already here/);
});

test("one failing source is reported without hiding the rest", () => {
  const summary = updatesSummary(
    checkBody({
      sources: [
        { url: "https://idfg.idaho.gov/rules", role: "page", status: "ok" },
        {
          url: "https://idfg.idaho.gov/rules/big-game",
          role: "page",
          status: "error",
          error: "HTTP 404 Not Found",
        },
      ],
      found: [{ filename: "upland.pdf", state: "new", document_id: 9 }],
      imported: [9],
    }),
  );
  // The win is still the headline; the failure rides along in the message.
  assert.equal(summary.title, "1 new document staged for review");
  assert.match(summary.message, /rules\/big-game — HTTP 404 Not Found/);
  assert.deepEqual(summary.items, [{ text: "upland.pdf", href: "#/regs/9" }]);
});

test("a check with only failures warns instead of claiming it is up to date", () => {
  const summary = updatesSummary(
    checkBody({
      sources: [{ url: "https://idfg.idaho.gov/rules", role: "page", status: "error", error: "x" }],
    }),
  );
  assert.equal(summary.title, "Checked IDFG, but some sources failed");
  assert.equal(summary.tone, "warn");
});

test("a supplement is reported as skipped, never as staged", () => {
  const summary = updatesSummary(
    checkBody({
      found: [
        {
          filename: "supplemental-proclamation-2026.pdf",
          state: "unsupported",
          reason: "supplemental proclamations need T-18 (#20) before they can be parsed",
        },
      ],
    }),
  );
  assert.equal(summary.title, "Up to date");
  assert.deepEqual(summary.items, []);
  assert.match(summary.message, /supplemental proclamations need T-18/);
});

test("the view model: idle, checking, done, error", () => {
  const now = Date.parse("2026-10-19T12:00:00");
  const idle = updatesView({ status: "idle", last: checkBody() }, now);
  assert.equal(idle.busy, false);
  assert.equal(idle.disabled, false);
  assert.equal(idle.hint, "Last checked 3 days ago.");

  const checking = updatesView({ status: "checking" }, now);
  assert.equal(checking.busy, true);
  assert.equal(checking.disabled, true);
  assert.match(checking.title, /Checking idfg\.idaho\.gov/);

  const done = updatesView({ status: "done", body: checkBody() }, now);
  assert.equal(done.title, "Up to date");
  assert.equal(done.hint, "Last checked 3 days ago.");

  const failed = updatesView({ status: "error", message: "offline" }, now);
  assert.equal(failed.tone, "error");
  assert.equal(failed.message, "offline");
});

function mountUpdates(check, last = checkBody()) {
  const el = createUpdatesCard({
    check,
    loadLast: async () => last,
    now: () => Date.parse("2026-10-19T12:00:00"),
  });
  const status = el.querySelector("#regs-updates-status");
  return {
    el,
    btn: el.querySelector("#regs-check-updates"),
    title: () => status.children[0].textContent,
    message: () => status.children[1].textContent,
    links: () => status.children[2],
    hint: () => status.children[3].textContent,
  };
}

test("the button paints idle → checking → done and links the staged documents", async () => {
  let release;
  const held = new Promise((r) => {
    release = r;
  });
  const ui = mountUpdates(() => held);
  assert.equal(ui.el.dataset.state, "idle");
  assert.equal(ui.btn.textContent, "Get latest regs");
  assert.equal(ui.btn.disabled, false);
  await ui.el.ready();
  assert.equal(ui.hint(), "Last checked 3 days ago.");

  const done = ui.el.run();
  // Synchronously: the spinner is on and the button can't be clicked twice.
  assert.equal(ui.el.dataset.state, "checking");
  assert.equal(ui.btn.getAttribute("aria-busy"), "true");
  assert.equal(ui.btn.disabled, true);

  release(
    checkBody({
      checked_at: "2026-10-19 11:00:00",
      found: [{ filename: "seasons-rules-big-game-2027.pdf", state: "new", document_id: 12 }],
      imported: [12],
    }),
  );
  await done;
  assert.equal(ui.el.dataset.state, "done");
  assert.equal(ui.el.dataset.tone, "success");
  assert.equal(ui.btn.hasAttribute("aria-busy"), false);
  assert.equal(ui.btn.disabled, false);
  assert.equal(ui.title(), "1 new document staged for review");
  assert.equal(ui.links().children.length, 1);
  assert.equal(ui.links().children[0].querySelector("a").getAttribute("href"), "#/regs/12");
  assert.equal(ui.hint(), "Last checked today.");
});

test("a failed check shows its message and leaves the button usable", async () => {
  const ui = mountUpdates(() => Promise.reject(new Error("The planner server is not responding.")));
  await ui.el.run();
  assert.equal(ui.el.dataset.state, "error");
  assert.equal(ui.message(), "The planner server is not responding.");
  assert.equal(ui.btn.disabled, false);
});

test("a never-checked planner says so instead of showing a stale date", async () => {
  const ui = mountUpdates(() => Promise.resolve(checkBody()), {
    checked_at: null,
    sources: [],
    found: [],
    imported: [],
  });
  await ui.el.ready();
  assert.equal(ui.hint(), "IDFG has not been checked yet.");
});

test("initRegs puts the button in the upload card and refreshes the table after a run", async () => {
  const panel = document.createElement("div");
  let documents = [];
  const tab = initRegs(panel, {
    load: async () => ({ documents }),
    checkUpdates: async () => {
      documents = [doc({ id: 8 })];
      return checkBody({
        found: [{ filename: "x.pdf", state: "new", document_id: 8 }],
        imported: [8],
      });
    },
    loadLastCheck: async () => checkBody(),
  });
  await tab.ready;
  const button = panel.querySelector("#regs-check-updates");
  assert.ok(button, "the button is missing");
  assert.ok(
    panel.querySelector("#regs-upload").querySelector("#regs-check-updates"),
    "the button must sit beside the drop zone, in the upload card",
  );
  assert.match(panel.textContent, /No regulation PDFs yet/);

  button.dispatch("click");
  await tab.updatesCard.run();
  await tick();
  assert.equal(panel.querySelectorAll("table").length, 1);
  assert.deepEqual(
    tab.documents().map((d) => d.id),
    [8],
  );
});

test("the document header names what a re-posted brochure replaces", () => {
  const host = document.createElement("div");
  renderDocumentDetail(host, 9, doc({ id: 9, replaces: { id: 4, title: "2026 Big Game (first)" } }), {
    mount: () => ({ el: textEl("p", "review screen") }),
  });
  assert.match(host.textContent, /Replaces 2026 Big Game \(first\)/);
  const href = [...host.querySelectorAll("a")].map((a) => a.getAttribute("href"));
  assert.ok(href.includes("#/regs/4"), "the replaced document is linked");
});
