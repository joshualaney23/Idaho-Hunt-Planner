// App shell entry point (T-03): disclaimer, header version, hash-routed tabs,
// placeholder cards for tabs that later tickets fill in, and a static shell of
// the Where Can I Hunt filter bar. T-07 replaces the filter shell with
// filters.js + state.js wired to /api/availability.
//
// T-09 mounts the My Tags tab (tags.js) and subscribes the map tab to
// TAGS_CHANGED, so filling or deleting a tag updates the tag filter and the
// breadcrumb at once (the shared state that invalidates availability).

import { card, chipGroup, textEl } from "./ui.js";
import { TABS, parseRoute, tabHash } from "./router.js";
import { defaultRange, fmtShort, localToday, parseISO, presets, toISO } from "./dates.js";
import { TAGS_CHANGED, activeTags, initTags, tagChipLabel, tagsSummary } from "./tags.js";
import { getJSON } from "./api.js";

const DISCLAIMER_KEY = "ihp.disclaimerDismissed";

// ---- disclaimer (dismissal lasts for this browser session only) -----------
function initDisclaimer() {
  const banner = document.getElementById("disclaimer");
  let dismissed = false;
  try {
    dismissed = sessionStorage.getItem(DISCLAIMER_KEY) === "1";
  } catch {
    // storage blocked: keep showing the banner
  }
  banner.hidden = dismissed;
  document.getElementById("disclaimer-dismiss").addEventListener("click", () => {
    banner.hidden = true;
    try {
      sessionStorage.setItem(DISCLAIMER_KEY, "1");
    } catch {
      // ignore
    }
  });
}

// ---- header: version + active regs ------------------------------------------
async function initVersion() {
  const el = document.getElementById("ver");
  try {
    const resp = await fetch("/api/health");
    if (!resp.ok) return;
    const { version } = await resp.json();
    // The active-regs label comes from /api/meta once published documents exist (T-06/T-15).
    el.textContent = `v${version} · no regs published`;
  } catch {
    // offline or server gone: keep the static label
  }
}

// ---- placeholder cards for tabs owned by later tickets ---------------------
const PLACEHOLDERS = {
  seasons: [
    "Season Table",
    "A sortable, filterable table of every published season arrives in T-16.",
  ],
  regs: [
    "Regulations",
    "Upload the IDFG PDFs, review parsed rows and publish them here (T-10, T-15).",
  ],
  settings: ["Settings", "Default species, weekend definition and backups arrive in T-21."],
};

let tagsMounted = false;
function ensureTags() {
  if (tagsMounted) return;
  tagsMounted = true;
  initTags().catch(() => {
    tagsMounted = false;
  });
}

function renderPlaceholder(tab) {
  const panel = document.getElementById(`panel-${tab}`);
  if (panel.childElementCount) return;
  const [title, text] = PLACEHOLDERS[tab];
  const wrap = document.createElement("div");
  wrap.className = "wrap single";
  wrap.appendChild(card(title, text, textEl("p", "Nothing here yet.", "placeholder")));
  panel.appendChild(wrap);
}

// ---- Where Can I Hunt: breadcrumb + filter-bar shell -------------------------
const SPECIES = [
  ["deer", "Deer", "🦌"],
  ["elk", "Elk", "🫎"],
  ["pronghorn", "Pronghorn", "🦌"],
  ["black_bear", "Bear", "🐻"],
  ["mountain_lion", "Lion", "🐆"],
  ["wolf", "Wolf", "🐺"],
  ["forest_grouse", "Grouse", "🐦"],
  ["quail", "Quail", "🐦"],
  ["chukar", "Chukar", "🐦"],
  ["gray_partridge", "Partridge", "🐦"],
  ["pheasant", "Pheasant", "🐦"],
  ["turkey", "Turkey", "🦃"],
];
const BIG_GAME_COUNT = 6; // a separator follows Wolf, as in §4.2
const WEAPONS = [
  ["all", "All legal weapons"],
  ["rifle", "Rifle"],
  ["archery", "Archery"],
  ["muzzleloader", "Muzzleloader"],
];
const PERMITS = [
  ["archery", "🏹 Archery"],
  ["muzzleloader", "Muzzleloader"],
];
const PRESETS = [
  ["thisWeekend", "This weekend"],
  ["nextWeekend", "Next weekend"],
  ["today", "Today"],
  ["next7", "Next 7 days"],
];

function filterGroup(labelText, id, ...content) {
  const fg = document.createElement("div");
  fg.className = "fg";
  const l = textEl("span", labelText, "l");
  l.id = `${id}-label`;
  fg.append(l, ...content);
  return fg;
}

function initMapTab() {
  const today = localToday();
  const [d1, d2] = defaultRange(today);
  const shell = {
    species: "deer",
    weapon: "all",
    permits: [],
    start: toISO(d1),
    end: toISO(d2),
    year: today.getFullYear(),
    tags: [],
  };

  const viewing = document.getElementById("viewing");
  function renderViewing() {
    const sp = SPECIES.find(([k]) => k === shell.species);
    const weapon = WEAPONS.find(([k]) => k === shell.weapon)[1];
    const range = `${fmtShort(parseISO(shell.start))} – ${fmtShort(parseISO(shell.end))}`;
    viewing.replaceChildren(
      "Viewing ",
      textEl("b", `${sp[2]} ${sp[1]}`),
      " · ",
      textEl("b", tagsSummary(shell.tags)),
      " · ",
      textEl("b", weapon),
      " · ",
      textEl("b", range),
    );
  }

  const species = chipGroup({
    id: "f-species",
    items: SPECIES.map(([k, label]) => [k, label]),
    value: shell.species,
    labelledBy: "f-species-label",
    onChange: (v) => {
      shell.species = v;
      renderViewing();
    },
  });
  const sep = document.createElement("span");
  sep.className = "chip-sep";
  sep.setAttribute("aria-hidden", "true");
  species.insertBefore(sep, species.children[BIG_GAME_COUNT]);

  // My tags: filled tags are dropped here the moment tags.js reports a change,
  // which is what invalidates the availability query (T-07 wires the map itself).
  const tagsRow = document.createElement("div");
  tagsRow.className = "chips";
  tagsRow.id = "f-tags-row";
  function renderTagsRow() {
    const active = activeTags(shell.tags);
    const chips = active.map((t) => {
      const chip = textEl("span", tagChipLabel(t), "chip tagchip");
      // A static list, not a toggle: no aria-pressed on a span (it isn't allowed
      // without a widget role, and the chips aren't clickable yet — T-07 owns that).
      chip.dataset.tagId = String(t.id);
      return chip;
    });
    const manage = textEl("a", active.length ? "Manage tags" : "No tags yet → add one", "chip");
    manage.href = "#/tags";
    tagsRow.replaceChildren(...chips, manage);
  }
  renderTagsRow();

  window.addEventListener(TAGS_CHANGED, (e) => {
    if (!e.detail || e.detail.year !== shell.year) return;
    shell.tags = e.detail.tags;
    renderTagsRow();
    renderViewing();
  });
  getJSON(`/api/tags?year=${shell.year}`)
    .then(({ tags }) => {
      shell.tags = tags;
      renderTagsRow();
      renderViewing();
    })
    .catch(() => {
      // offline: keep the empty tag row
    });

  const weapon = chipGroup({
    id: "f-weapon",
    items: WEAPONS.map(([k, label]) => [k, k === "all" ? "All" : label]),
    value: shell.weapon,
    labelledBy: "f-weapon-label",
    onChange: (v) => {
      shell.weapon = v;
      renderViewing();
    },
  });

  const permits = chipGroup({
    id: "f-permits",
    items: PERMITS,
    multi: true,
    value: shell.permits,
    labelledBy: "f-permits-label",
    onChange: (v) => {
      shell.permits = v;
    },
  });

  const dates = document.createElement("div");
  dates.className = "dates";
  const in1 = document.createElement("input");
  const in2 = document.createElement("input");
  for (const [inp, name] of [
    [in1, "Start date"],
    [in2, "End date"],
  ]) {
    inp.type = "date";
    inp.setAttribute("aria-label", name);
  }
  dates.append(in1, textEl("span", "→"), in2);

  const presetRanges = () => {
    const p = presets(today);
    return Object.fromEntries(PRESETS.map(([k]) => [k, p[k].map(toISO)]));
  };
  const matchingPreset = () => {
    const ranges = presetRanges();
    const hit = PRESETS.find(([k]) => ranges[k][0] === shell.start && ranges[k][1] === shell.end);
    return hit ? hit[0] : null;
  };
  const presetChips = chipGroup({
    id: "f-presets",
    items: PRESETS,
    value: matchingPreset(),
    label: "Date presets",
    onChange: (k) => {
      [shell.start, shell.end] = presetRanges()[k];
      syncDates();
    },
  });

  function syncDates() {
    in1.value = shell.start;
    in2.value = shell.end;
    presetChips.setValue(matchingPreset());
    renderViewing();
  }
  in1.addEventListener("change", () => {
    if (!in1.value) return;
    shell.start = in1.value;
    if (shell.end < shell.start) shell.end = shell.start;
    syncDates();
  });
  in2.addEventListener("change", () => {
    if (!in2.value) return;
    shell.end = in2.value;
    if (shell.end < shell.start) shell.start = shell.end;
    syncDates();
  });

  document
    .getElementById("filters")
    .replaceChildren(
      filterGroup("Species", "f-species", species),
      filterGroup(`My tags (${today.getFullYear()})`, "f-tags", tagsRow),
      filterGroup("Weapon", "f-weapon", weapon),
      filterGroup("Permits", "f-permits", permits),
      filterGroup("Dates", "f-dates", dates, presetChips),
    );
  syncDates();

  document
    .getElementById("map-wrap")
    .replaceChildren(
      card(
        "Open Units Map",
        "The GMU map arrives in T-07: green = open, yellow = restricted or partial, grey = closed.",
      ),
      card("Open units", "The results table (unit, status, overlapping seasons, source page) arrives in T-08."),
    );
}

// ---- tabs + hash routing -----------------------------------------------------
function activate(tab, { focus = false } = {}) {
  for (const t of TABS) {
    const on = t.id === tab;
    const a = document.getElementById(`tab-${t.id}`);
    a.setAttribute("aria-selected", on ? "true" : "false");
    a.tabIndex = on ? 0 : -1;
    document.getElementById(`panel-${t.id}`).hidden = !on;
    if (on && focus) a.focus();
  }
  if (tab === "tags") ensureTags();
  else if (tab !== "map") renderPlaceholder(tab);
  const label = TABS.find((t) => t.id === tab).label;
  document.title = `${label} · Idaho Hunt Planner`;
  document.body.dataset.tab = tab;
}

function route({ focus = false } = {}) {
  const r = parseRoute(location.hash);
  if (!r.known) {
    // Normalize without adding a history entry, so Back still leaves the app.
    history.replaceState(null, "", tabHash(r.tab));
  }
  activate(r.tab, { focus });
}

function initTabs() {
  const list = document.querySelector('[role="tablist"]');
  // Arrow keys move between tabs (WAI-ARIA tabs pattern, automatic activation).
  list.addEventListener("keydown", (e) => {
    const ids = TABS.map((t) => t.id);
    const cur = ids.indexOf(parseRoute(location.hash).tab);
    let next = null;
    if (e.key === "ArrowRight") next = (cur + 1) % ids.length;
    else if (e.key === "ArrowLeft") next = (cur - 1 + ids.length) % ids.length;
    else if (e.key === "Home") next = 0;
    else if (e.key === "End") next = ids.length - 1;
    if (next === null) return;
    e.preventDefault();
    location.hash = tabHash(ids[next]);
    document.getElementById(`tab-${ids[next]}`).focus();
  });
  window.addEventListener("hashchange", () => route());
  route();
}

initDisclaimer();
initMapTab();
initTabs();
initVersion();
