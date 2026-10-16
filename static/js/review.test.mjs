// static/js/review.js (T-15c): the §8.4 review screen — the Accuracy panel's grouping
// and waiver display, the row grouping (species -> table title -> page), confidence
// classes, the T-15a diff badges, the inline editors' patch, the publish gate's
// reasons and the j/k/a/r/e shortcuts, all against tests/js/fakedom.mjs.
// The real-browser checks (focus, the PDF iframe, Lighthouse) live in the T-15c PR.
//
// Run: node --test static/js/review.test.mjs
import { test, beforeEach } from "node:test";
import assert from "node:assert/strict";

import { installFakeDom } from "../../tests/js/fakedom.mjs";
import {
  CHECK_ICONS,
  blockedLines,
  changeText,
  checkView,
  confidenceClass,
  createReviewScreen,
  createRowEditor,
  createWaiveDialog,
  diffIndex,
  gateReasons,
  groupRows,
  groupRowsForReview,
  nextStagedAfter,
  pdfPageHref,
  publishCounts,
  restrictionLabel,
  rowOrder,
  sectionLabel,
  sectionViews,
} from "./review.js";

beforeEach(() => installFakeDom());

// ---- fixtures ----------------------------------------------------------------

const season = (over = {}) => ({
  id: 1,
  status: "staged",
  section: "deer_general",
  species_id: "deer",
  table_title: "Regular Deer",
  tag_type_id: "deer_regular",
  weapon: "any",
  sex: "either",
  start_date: "2026-10-10",
  end_date: "2026-10-24",
  restrictions: [],
  units: ["39", "43"],
  source_page: 12,
  source_text: "39, 43 Oct. 10 - Oct. 24",
  confidence: 1,
  extract_agree: 1,
  ...over,
});

const check = (over = {}) => ({
  id: 7,
  section: "deer_general",
  check_name: "general_coverage",
  status: "fail",
  expected: "99 units covered",
  actual: "88 covered, 11 unexplained",
  detail: null,
  waived: false,
  waive_reason: null,
  waived_by: null,
  waived_at: null,
  blocks_publish: true,
  ...over,
});

const body = (over = {}) => ({
  document: { id: 4, title: "2026 Big Game", status: "needs_review", license_year: 2026 },
  seasons: [season()],
  checks: [check()],
  sections: [
    {
      section: "deer_general",
      checks: { fail: 1 },
      waived: false,
      waived_checks: [],
      blocking_checks: ["general_coverage"],
      clean: false,
    },
  ],
  publish: {
    blocked: true,
    blockers: {
      checks: [{ id: 7, section: "deer_general", check_name: "general_coverage", status: "fail" }],
      rows: [1],
      unreviewed_rows: [{ id: 1 }],
      disagreeing_rows: [],
    },
  },
  ...over,
});

/** A screen with every network call stubbed; `calls` records what it sent. */
function mount(over = {}) {
  const calls = [];
  let current = over.body || body();
  const screen = createReviewScreen(4, {
    load: async () => current,
    loadDiff: async () => over.diff || { rows: [], removed: [] },
    loadUnits: async () => ({ units: [{ id: "39" }, { id: "43" }, { id: "44" }] }),
    loadTagTypes: async () => ({ tag_types: [{ id: "deer_regular", label: "Regular Deer" }] }),
    review: async (sid, patch) => {
      calls.push(["review", sid, patch]);
      return { season: season({ id: sid, ...patch }), publish: current.publish };
    },
    waive: async (cid, reason) => {
      calls.push(["waive", cid, reason]);
      return {};
    },
    approveAll: async () => {
      calls.push(["approveAll"]);
      return { approved: [1], skipped: [] };
    },
    publish: async () => {
      calls.push(["publish"]);
      if (over.publishError) throw over.publishError;
      return { published: [1], superseded: [] };
    },
    notify: () => {},
    ...(over.options || {}),
  });
  return { screen, calls, setBody: (b) => (current = b) };
}

// ---- the Accuracy panel ------------------------------------------------------

test("checkView carries the glyph, the gate flag and the waiver", () => {
  const view = checkView(check({ waived: true, waive_reason: "verified by hand", waived_by: "josh", waived_at: "2026-10-16T09:00:00", blocks_publish: false }));
  assert.equal(view.icon, CHECK_ICONS.fail);
  assert.equal(view.kind, "error");
  assert.equal(view.blocks, false);
  assert.deepEqual([view.waived, view.reason, view.by], [true, "verified by hand", "josh"]);
});

test("checkView picks up the rows and pages a check pointed at", () => {
  const view = checkView(check({ detail: { rows: [3, 9], pages: [12, 13] } }));
  assert.deepEqual(view.rows, [3, 9]);
  assert.deepEqual(view.pages, [12, 13]);
});

test("sectionViews groups per section and puts the blockers first", () => {
  const views = sectionViews(
    [
      check({ id: 1, section: "controlled_deer", check_name: "tag_checksum", status: "pass", blocks_publish: false }),
      check({ id: 2, section: "deer_general" }),
      check({ id: 3, section: "deer_general", check_name: "two_path_agreement", status: "pass", blocks_publish: false }),
    ],
    [
      { section: "controlled_deer", waived: false, clean: true },
      { section: "deer_general", waived: false, clean: false },
    ],
  );
  assert.deepEqual(
    views.map((v) => v.section),
    ["deer_general", "controlled_deer"],
  );
  assert.deepEqual(views[0].blocking, ["general_coverage"]);
  assert.deepEqual(views[0].counts, { fail: 1, pass: 1 });
  assert.equal(views[1].clean, true);
});

test("a section with no checks of its own still appears", () => {
  const views = sectionViews([], [{ section: "controlled_elk", waived: false, clean: true }]);
  assert.deepEqual(views.map((v) => v.section), ["controlled_elk"]);
  assert.deepEqual(views[0].checks, []);
});

test("sectionLabel reads the §8.7 ids", () => {
  assert.equal(sectionLabel("deer_general"), "Deer · general");
  assert.equal(sectionLabel("controlled_black_bear"), "Controlled · black bear");
  assert.equal(sectionLabel(""), "Unsectioned");
});

test("the panel renders a line per check, with expected vs actual", async () => {
  const { screen } = mount();
  await screen.ready;
  const panel = screen.el.querySelector("#accuracy-panel");
  assert.match(panel.textContent, /general_coverage/);
  assert.match(panel.textContent, /expected 99 units covered · actual 88 covered, 11 unexplained/);
  assert.match(panel.textContent, /blocks publish/);
});

test("a waived check shows the reason, who waived it and when — and no Waive button", async () => {
  const waived = body({
    checks: [
      check({
        waived: true,
        blocks_publish: false,
        waive_reason: "nine-page excerpt",
        waived_by: "josh",
        waived_at: "2026-10-16T09:00:00",
      }),
    ],
    sections: [{ section: "deer_general", waived: true, clean: false, blocking_checks: [] }],
  });
  const { screen } = mount({ body: waived });
  await screen.ready;
  const text = screen.el.querySelector("#accuracy-panel").textContent;
  assert.match(text, /Waived: “nine-page excerpt” by josh on 2026-10-16T09:00:00/);
  assert.match(text, /manually verified/);
  const buttons = screen.el.querySelector("#accuracy-panel").querySelectorAll("button");
  assert.equal(buttons.filter((b) => b.textContent === "Waive…").length, 0);
});

// ---- the waive dialog --------------------------------------------------------

test("the waive dialog refuses an empty reason without calling the server", async () => {
  let sent = null;
  const dialog = createWaiveDialog(async (c, reason) => {
    sent = [c.id, reason];
  });
  document.body.appendChild(dialog.dlg);
  dialog.open(checkView(check()));
  assert.equal(dialog.dlg.open, true);

  const save = dialog.dlg.querySelectorAll("button").find((b) => b.textContent === "Waive");
  const reason = dialog.dlg.querySelector("#waive-reason");
  reason.value = "   ";
  save.click();
  await Promise.resolve();
  assert.equal(sent, null);
  assert.equal(dialog.dlg.open, true);
  assert.match(dialog.dlg.textContent, /needs a written reason/);

  reason.value = "  checked against the printed page  ";
  save.click();
  await Promise.resolve();
  await Promise.resolve();
  assert.deepEqual(sent, [7, "checked against the printed page"]);
  assert.equal(dialog.dlg.open, false);
});

test("a server-side missing_reason is shown in the dialog, which stays open", async () => {
  const dialog = createWaiveDialog(async () => {
    throw new Error("a waiver needs a written reason");
  });
  document.body.appendChild(dialog.dlg);
  dialog.open(checkView(check()));
  dialog.dlg.querySelector("#waive-reason").value = "because";
  dialog.dlg.querySelectorAll("button").find((b) => b.textContent === "Waive").click();
  await Promise.resolve();
  await Promise.resolve();
  assert.equal(dialog.dlg.open, true);
  assert.match(dialog.dlg.textContent, /a waiver needs a written reason/);
});

test("waiving from the panel posts the typed reason", async () => {
  const { screen, calls } = mount();
  await screen.ready;
  const waive = screen.el.querySelectorAll("button").find((b) => b.textContent === "Waive…");
  waive.click();
  const dlg = screen.el.querySelector("#waive-dialog");
  dlg.querySelector("#waive-reason").value = "verified against p. 12 by hand";
  dlg.querySelectorAll("button").find((b) => b.textContent === "Waive").click();
  await new Promise((r) => setTimeout(r, 0));
  assert.deepEqual(calls[0], ["waive", 7, "verified against p. 12 by hand"]);
});

// ---- the row list ------------------------------------------------------------

test("groupRows nests species -> table title -> page, keeping every row", () => {
  const groups = groupRows([
    season({ id: 3, source_page: 13 }),
    season({ id: 1, source_page: 12 }),
    season({ id: 2, source_page: 12 }),
    season({ id: 4, species_id: "elk", table_title: "Elk Zones", source_page: 20 }),
  ]);
  assert.deepEqual(groups.map((g) => g.species), ["deer", "elk"]);
  const deer = groups[0].titles[0];
  assert.equal(deer.title, "Regular Deer");
  assert.deepEqual(deer.pages.map((p) => p.page), [12, 13]);
  assert.deepEqual(deer.pages[0].rows.map((r) => r.id), [1, 2]);
  assert.deepEqual(rowOrder(groups).map((r) => r.id), [1, 2, 3, 4]);
});

test("groupRowsForReview moves approved rows to the bottom, keeping the rest in order", () => {
  const groups = groupRowsForReview([
    season({ id: 1, source_page: 12, status: "approved" }),
    season({ id: 2, source_page: 12 }),
    season({ id: 3, source_page: 13, status: "rejected" }),
    season({ id: 4, species_id: "elk", table_title: "Elk Zones", source_page: 20 }),
    season({ id: 5, source_page: 13, status: "approved" }),
  ]);
  assert.deepEqual(rowOrder(groups).map((r) => r.id), [2, 3, 4, 1, 5]);
});

test("nextStagedAfter picks the next staged row, else the one before, else null", () => {
  const order = [
    season({ id: 1, status: "staged" }),
    season({ id: 2, status: "staged" }),
    season({ id: 3, status: "rejected" }),
    season({ id: 4, status: "staged" }),
  ];
  assert.equal(nextStagedAfter(order, 1), 2);
  assert.equal(nextStagedAfter(order, 2), 4);
  assert.equal(nextStagedAfter(order, 4), 2);
  assert.equal(nextStagedAfter([season({ id: 9, status: "staged" })], 9), null);
  assert.equal(nextStagedAfter(order, 42), null);
});

test("confidenceClass walks the §4.1 ramp", () => {
  assert.deepEqual(
    [0, 0.1, 0.3, 0.5, 0.7, 0.9, 1].map(confidenceClass),
    ["sc1", "sc1", "sc2", "sc3", "sc4", "sc5", "sc5"],
  );
  assert.equal(confidenceClass(undefined), "sc1"); // never guess green (D3)
});

test("restrictionLabel keeps an unknown code verbatim", () => {
  assert.equal(restrictionLabel("whitetail_only"), "White-tailed deer only");
  assert.equal(restrictionLabel("brand_new_code"), "brand_new_code");
});

test("a row shows units, facts, restrictions, confidence and its source text", async () => {
  const { screen } = mount({
    body: body({ seasons: [season({ restrictions: ["whitetail_only"], confidence: 0.4 })] }),
  });
  await screen.ready;
  const row = screen.el.querySelector("[data-season-id]");
  assert.match(row.textContent, /39/);
  assert.match(row.textContent, /White-tailed deer only/);
  assert.match(row.textContent, /2026-10-10 → 2026-10-24/);
  assert.match(row.textContent, /39, 43 Oct. 10 - Oct. 24/);
  const conf = row.querySelectorAll(".conf")[0];
  assert.match(conf.className, /sc2/);
});

test("a row in a waived section wears the manually-verified badge", async () => {
  const { screen } = mount({
    body: body({
      sections: [{ section: "deer_general", waived: true, clean: false, blocking_checks: [] }],
    }),
  });
  await screen.ready;
  assert.match(screen.el.querySelector("[data-season-id]").textContent, /manually verified/);
});

test("a row whose paths disagreed says so", async () => {
  const { screen } = mount({ body: body({ seasons: [season({ extract_agree: 0 })] }) });
  await screen.ready;
  assert.match(screen.el.querySelector("[data-season-id]").textContent, /paths disagree/);
});

// ---- the T-15a diff ----------------------------------------------------------

test("diffIndex and changeText render old → new", () => {
  const index = diffIndex({
    rows: [{ id: 1, diff: "changed", changes: { end_date: { from: "2026-10-24", to: "2026-10-31" } } }],
  });
  assert.equal(index.get(1).mark, "changed");
  assert.equal(changeText("end_date", index.get(1).changes.end_date), "end_date: 2026-10-24 → 2026-10-31");
  assert.equal(changeText("units", { from: [], to: ["39"] }), "units: none → 39");
});

test("the diff badge and its old → new line land on the row", async () => {
  const { screen } = mount({
    diff: {
      rows: [{ id: 1, diff: "changed", changes: { start_date: { from: "2026-10-10", to: "2026-10-11" } } }],
      removed: [
        {
          species_id: "deer",
          tag_type_id: "deer_regular",
          units: ["44"],
          start_date: "2025-10-10",
          end_date: "2025-10-24",
        },
      ],
    },
  });
  await screen.ready;
  const row = screen.el.querySelector("[data-season-id]");
  assert.match(row.textContent, /changed/);
  assert.match(row.textContent, /start_date: 2026-10-10 → 2026-10-11/);
  assert.match(screen.el.textContent, /Removed since last year \(1\)/);
  assert.match(screen.el.textContent, /deer · deer_regular · 44/);
});

// ---- the inline editors ------------------------------------------------------

test("the editor sends only the fields that changed", async () => {
  let sent = null;
  const form = createRowEditor(season(), {
    units: [{ id: "39" }, { id: "43" }, { id: "44" }],
    tagTypes: [{ id: "deer_regular", label: "Regular Deer" }],
    onSave: async (patch) => {
      sent = patch;
    },
  });
  document.body.appendChild(form);
  assert.deepEqual(form.patch(), {}); // untouched: nothing to send

  form.querySelector(".unit-input").value = "39, 44";
  form.querySelectorAll(".date-input")[1].value = "2026-10-31";
  const selects = form.querySelectorAll("select");
  selects.find((s) => s.className.includes("restrictions")).children.find(
    (o) => o.value === "whitetail_only",
  ).selected = true;

  assert.deepEqual(form.patch(), {
    units: ["39", "44"],
    end_date: "2026-10-31",
    restrictions: ["whitetail_only"],
  });
  form.querySelectorAll("button").find((b) => b.textContent === "Save").click();
  await Promise.resolve();
  await Promise.resolve();
  assert.deepEqual(sent, { units: ["39", "44"], end_date: "2026-10-31", restrictions: ["whitetail_only"] });
});

test("the editor offers every unit for autocomplete and every §5.1 code", () => {
  const form = createRowEditor(season(), {
    units: [{ id: "39" }, { id: "43" }, { id: "44" }],
    tagTypes: [{ id: "deer_regular", label: "Regular Deer" }],
  });
  assert.equal(form.querySelector("datalist").children.length, 3);
  assert.equal(form.querySelector(".unit-input").getAttribute("list"), "unit-options");
  const restrictions = form.querySelectorAll("select").find((s) => s.className.includes("restrictions"));
  assert.equal(restrictions.getAttribute("multiple"), "true");
  assert.equal(restrictions.children.length, 15);
});

test("a rejected edit keeps the form open and shows the API's message", async () => {
  const form = createRowEditor(season(), {
    onSave: async () => {
      throw new Error("no such unit: 99");
    },
  });
  form.querySelector(".unit-input").value = "99";
  form.querySelectorAll("button").find((b) => b.textContent === "Save").click();
  await Promise.resolve();
  await Promise.resolve();
  assert.match(form.textContent, /no such unit: 99/);
});

test("the screen opens an editor on the selected row and saves through the API", async () => {
  const { screen, calls } = mount();
  await screen.ready;
  screen.keydown({ key: "e", target: { tagName: "DIV" } });
  assert.equal(screen.editing(), 1);
  const form = screen.el.querySelector(".row-editor");
  form.querySelectorAll(".date-input")[0].value = "2026-10-11";
  form.querySelectorAll("button").find((b) => b.textContent === "Save").click();
  await new Promise((r) => setTimeout(r, 0));
  assert.deepEqual(calls[0], ["review", 1, { start_date: "2026-10-11" }]);
});

// ---- keyboard, selection and the PDF viewer ----------------------------------

test("pdfPageHref carries the page in the query as well as the fragment", () => {
  // Hash-only src changes don't navigate, so Chrome's PDF viewer would never move
  // (verified in Chrome, T-15c). The query makes every page a distinct URL.
  assert.equal(pdfPageHref(4, 12), "/api/documents/4/pdf?page=12#page=12");
  assert.equal(pdfPageHref(4, null), "/api/documents/4/pdf?page=1#page=1");
  assert.notEqual(pdfPageHref(4, 12).split("#")[0], pdfPageHref(4, 13).split("#")[0]);
});

test("j and k walk the rows and the iframe follows the selection", async () => {
  const rows = [season({ id: 1, source_page: 12 }), season({ id: 2, source_page: 13 }), season({ id: 3, source_page: 14 })];
  const { screen } = mount({ body: body({ seasons: rows }) });
  await screen.ready;
  const viewer = screen.el.querySelector("#review-pdf");
  assert.equal(screen.selected(), 1);
  assert.equal(viewer.getAttribute("src"), "/api/documents/4/pdf?page=12#page=12");

  screen.keydown({ key: "j", target: { tagName: "DIV" } });
  assert.equal(screen.selected(), 2);
  assert.equal(viewer.getAttribute("src"), "/api/documents/4/pdf?page=13#page=13");

  screen.keydown({ key: "j", target: { tagName: "DIV" } });
  screen.keydown({ key: "j", target: { tagName: "DIV" } }); // clamped at the end
  assert.equal(screen.selected(), 3);
  screen.keydown({ key: "k", target: { tagName: "DIV" } });
  assert.equal(screen.selected(), 2);
  assert.equal(viewer.getAttribute("src"), "/api/documents/4/pdf?page=13#page=13");
});

test("a and r review the focused row; the shortcuts never fire inside a field", async () => {
  const { screen, calls } = mount();
  await screen.ready;
  screen.keydown({ key: "a", target: { tagName: "DIV" } });
  await new Promise((r) => setTimeout(r, 0));
  screen.keydown({ key: "r", target: { tagName: "DIV" } });
  await new Promise((r) => setTimeout(r, 0));
  assert.deepEqual(calls, [
    ["review", 1, { status: "approved" }],
    ["review", 1, { status: "rejected" }],
  ]);

  assert.equal(screen.keydown({ key: "a", target: { tagName: "INPUT" } }), false);
  assert.equal(screen.keydown({ key: "j", target: { tagName: "TEXTAREA" } }), false);
  assert.equal(screen.keydown({ key: "a", metaKey: true, target: { tagName: "DIV" } }), false);
  assert.equal(screen.keydown({ key: "x", target: { tagName: "DIV" } }), false);
});

test("focus returns to the selected row after a repaint, so a/r keep working", async () => {
  const { screen, calls } = mount({ body: body({ seasons: [season({ id: 1 }), season({ id: 2 })] }) });
  await screen.ready;
  screen.el.querySelectorAll("[data-season-id]")[0].focus();
  assert.equal(document.activeElement.dataset.seasonId, "1");

  // `a` approves and repaints — the row element is replaced, but focus must survive,
  // or every shortcut after the first one is swallowed (found in Chrome, T-15c).
  // Approving 1 moves the selection to the next staged row (2), not the approved one.
  screen.keydown({ key: "a", target: document.activeElement });
  await new Promise((r) => setTimeout(r, 0));
  assert.equal(document.activeElement.dataset.seasonId, "2");
  // …and on the *live* element, not the detached one the repaint threw away.
  assert.ok(screen.el.contains(document.activeElement));
  screen.keydown({ key: "r", target: document.activeElement });
  await new Promise((r) => setTimeout(r, 0));
  assert.deepEqual(calls, [
    ["review", 1, { status: "approved" }],
    ["review", 2, { status: "rejected" }],
  ]);
});

test("a repaint never steals focus from an open editor", async () => {
  const { screen } = mount();
  await screen.ready;
  screen.el.querySelector("[data-season-id]").focus();
  screen.keydown({ key: "e", target: document.activeElement });
  const input = screen.el.querySelector(".unit-input");
  input.focus();
  await screen.refresh();
  assert.equal(document.activeElement, input);
});

test("the selected row is the one marked aria-current (not aria-selected)", async () => {
  const { screen } = mount({ body: body({ seasons: [season({ id: 1 }), season({ id: 2 })] }) });
  await screen.ready;
  screen.select(2);
  const rows = screen.el.querySelectorAll("[data-season-id]");
  const marked = rows.filter((n) => n.getAttribute("aria-current") === "true");
  assert.deepEqual(marked.map((n) => n.dataset.seasonId), ["2"]);
  // aria-selected is only legal inside a listbox/grid; these rows are grouped sections.
  assert.ok(rows.every((n) => n.getAttribute("aria-selected") === null));
});

// ---- the publish gate --------------------------------------------------------

test("gateReasons names §8.4's three conditions", () => {
  assert.deepEqual(
    gateReasons({
      blocked: true,
      blockers: {
        checks: [{ check_name: "general_coverage", status: "fail" }],
        rows: [1, 2],
        unreviewed_rows: [{ id: 1 }, { id: 2 }],
        disagreeing_rows: [{ id: 3 }],
      },
    }),
    [
      "2 rows still staged",
      "1 unwaived check: general_coverage (fail)",
      "1 row(s) where the two paths disagree, unreviewed",
    ],
  );
  assert.deepEqual(gateReasons({ blocked: false, blockers: {} }), []);
});

test("Publish is disabled while the gate blocks, and enabled when it clears", async () => {
  const { screen } = mount();
  await screen.ready;
  assert.equal(screen.el.querySelector("#publish-button").disabled, true);
  assert.match(screen.el.querySelector("#gate-reasons").textContent, /1 row still staged/);

  const { screen: clear } = mount({
    body: body({
      seasons: [season({ status: "approved" })],
      publish: { blocked: false, blockers: { checks: [], rows: [], unreviewed_rows: [], disagreeing_rows: [] } },
    }),
  });
  await clear.ready;
  assert.equal(clear.el.querySelector("#publish-button").disabled, false);
  assert.match(clear.el.querySelector("#gate-reasons").textContent, /gate is satisfied/);
});

test("publishCounts summarises what the modal is about to publish", () => {
  assert.deepEqual(
    publishCounts([
      season({ status: "approved" }),
      season({ status: "approved" }),
      season({ status: "rejected" }),
      season({ status: "staged" }),
      season({ status: "published" }),
    ]),
    { approved: 2, rejected: 1, staged: 1 },
  );
});

test("the confirmation modal states the counts and publishes on confirm", async () => {
  const { screen, calls } = mount({
    body: body({
      seasons: [season({ status: "approved" })],
      publish: { blocked: false, blockers: {} },
    }),
  });
  await screen.ready;
  screen.el.querySelector("#publish-button").click();
  const dlg = screen.el.querySelector("#publish-dialog");
  assert.equal(dlg.open, true);
  assert.match(dlg.textContent, /1 approved row\(s\) become live/);
  assert.match(dlg.textContent, /superseded/);
  dlg.querySelectorAll("button").find((b) => b.textContent === "Publish").click();
  await new Promise((r) => setTimeout(r, 0));
  assert.deepEqual(calls, [["publish"]]);
  assert.equal(dlg.open, false);
});

test("blockedLines names every blocking check and row from a 409", () => {
  const lines = blockedLines({
    code: "publish_blocked",
    detail: {
      checks: [
        { section: "deer_general", check_name: "general_coverage", expected: "99", actual: "88" },
      ],
      rows: [1, 2, 3],
    },
  });
  assert.deepEqual(lines, [
    "deer_general · general_coverage: expected 99, got 88",
    "Rows not reviewed: 1, 2, 3",
  ]);
});

test("a 409 keeps the modal open and spells out why", async () => {
  const error = new Error("the §8.7 review gate is not satisfied");
  error.code = "publish_blocked";
  error.detail = {
    checks: [{ section: "deer_general", check_name: "general_coverage", expected: "99", actual: "88" }],
    rows: [1],
  };
  const { screen } = mount({
    body: body({ publish: { blocked: false, blockers: {} } }),
    publishError: error,
  });
  await screen.ready;
  screen.el.querySelector("#publish-button").click();
  const dlg = screen.el.querySelector("#publish-dialog");
  dlg.querySelectorAll("button").find((b) => b.textContent === "Publish").click();
  await new Promise((r) => setTimeout(r, 0));
  assert.equal(dlg.open, true);
  assert.match(dlg.textContent, /review gate is not satisfied/);
  assert.match(dlg.textContent, /deer_general · general_coverage: expected 99, got 88/);
});

test("approve-all goes through the guarded endpoint, not a confidence filter", async () => {
  const { screen, calls } = mount();
  await screen.ready;
  screen.el.querySelectorAll("button").find((b) => b.textContent.startsWith("Approve all")).click();
  await new Promise((r) => setTimeout(r, 0));
  assert.deepEqual(calls[0], ["approveAll"]);
});

test("a failed load says so instead of rendering an empty screen", async () => {
  const screen = createReviewScreen(4, {
    load: async () => {
      throw new Error("no such document 4");
    },
    loadDiff: async () => ({ rows: [] }),
    loadUnits: async () => ({ units: [] }),
    loadTagTypes: async () => ({ tag_types: [] }),
    notify: () => {},
  });
  await screen.ready;
  assert.equal(document.body.dataset.reviewReady, "error");
  assert.match(screen.el.textContent, /no such document 4/);
});
