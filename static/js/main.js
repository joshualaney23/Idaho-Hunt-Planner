// App shell entry point (T-03): disclaimer, header version, hash-routed tabs and
// placeholder cards for tabs that later tickets fill in. T-07 mounts the Where Can I
// Hunt tab: filters.js (filter bar) + state.js (URL-synced state, debounced
// /api/availability) + map.js (SVG map, tooltip, legend).
//
// T-09 mounts the My Tags tab (tags.js) and subscribes the map tab to
// TAGS_CHANGED, so filling or deleting a tag updates the tag filter and the
// breadcrumb at once (the shared state that invalidates availability).

import { card, textEl, toast } from "./ui.js";
import { TABS, parseRoute, tabHash } from "./router.js";
import { localToday } from "./dates.js";
import { PERMITS_CHANGED, TAGS_CHANGED, initTags } from "./tags.js";
import { getJSON, putJSON } from "./api.js";
import {
  availabilityPath,
  createFetcher,
  createStore,
  defaultState,
  hasFilterKeys,
  rangeYears,
  stateFromQuery,
  toQuery,
} from "./state.js";
import { MAP_PERMITS, createFilters, renderViewing } from "./filters.js";
import { createMap, placeTip, renderLegend, tooltipContent } from "./map.js";

/** Fired when a unit is clicked or Enter is pressed on it; T-08's drawer listens. */
export const UNIT_OPEN = "ihp:unit-open";

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

// ---- Where Can I Hunt: filters + map (T-07) ------------------------------------
// The store (state.js) is the single source of truth. Every change: rewrite the URL
// query (replaceState, so filters don't flood history), repaint the filter bar and the
// breadcrumb at once, and schedule a debounced /api/availability fetch (150 ms).
let mapTab = null;

async function initMapTab() {
  const today = localToday();
  const [meta, gmu] = await Promise.all([
    getJSON("/api/meta"),
    fetch("/static/data/gmu.json").then((r) => r.json()),
  ]);
  const profile = meta.profile || {};
  const defaults = defaultState({ profile, today });
  const speciesIds = meta.species.map((s) => s.id);
  const route0 = parseRoute(location.hash);
  const initial =
    route0.tab === "map" ? stateFromQuery(route0.query, defaults, { speciesIds }) : defaults;
  const store = createStore(initial);
  const labels = { restrictions: meta.restrictions, reasons: meta.reasons };

  const tagsByYear = {}; // license year → tags (filled included; filters drop them)
  const permitsByYear = {}; // license year → permit codes held
  let availability = null;
  let selected = null;
  let repaints = 0;

  // ---- layout: map card (svg + legend) and the brief/results card ----
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.id = "gmu-map";
  const legend = document.createElement("div");
  legend.className = "legend";
  legend.id = "legend";
  const mapCard = card("Open Units Map", svg, legend);
  mapCard.id = "map-card";
  const summaryEl = textEl("h2", "Open units");
  summaryEl.id = "results-title";
  const brief = document.createElement("div");
  brief.id = "brief";
  brief.setAttribute("aria-live", "polite");
  const sideCard = document.createElement("section");
  sideCard.className = "card";
  sideCard.id = "side-card";
  sideCard.append(summaryEl, brief);
  document.getElementById("map-wrap").replaceChildren(mapCard, sideCard);

  const map = createMap(svg, gmu);
  const tip = document.createElement("div");
  tip.className = "tip";
  tip.id = "tip";
  tip.hidden = true;
  document.body.appendChild(tip);

  map.onHover((id, point) => {
    if (!id || !point) {
      tip.hidden = true;
      return;
    }
    tip.replaceChildren(
      ...tooltipContent(id, availability?.units?.[id], gmu.units[id], labels),
    );
    tip.hidden = false;
    const { left, top } = placeTip(
      point.x,
      point.y,
      tip.offsetWidth,
      tip.offsetHeight,
      window.innerWidth,
      window.innerHeight,
    );
    tip.style.left = `${left}px`;
    tip.style.top = `${top}px`;
  });
  map.onClick((id) => {
    selected = id;
    renderSide();
    window.dispatchEvent(new CustomEvent(UNIT_OPEN, { detail: { unit: id, state: store.get() } }));
  });

  // ---- side card: summary + the unit brief stub (the drawer itself is T-08) ----
  function renderSide() {
    const sum = availability?.summary;
    summaryEl.textContent = sum
      ? `${sum.open} open · ${sum.restricted} restricted${
          store.get().includeUntagged ? ` · ${sum.untagged} untagged` : ""
        }`
      : "Open units";
    if (!selected) {
      brief.replaceChildren(
        textEl(
          "p",
          "Hover a unit for its seasons; click it (or Tab to it and press Enter) for the unit brief. " +
            "The results table and the full brief drawer arrive in T-08.",
          "placeholder",
        ),
      );
      return;
    }
    const geo = gmu.units[selected];
    const unit = availability?.units?.[selected];
    const close = textEl("button", "×", "icon-btn");
    close.type = "button";
    close.setAttribute("aria-label", `Close the unit ${selected} brief`);
    close.addEventListener("click", () => {
      const back = map.paths[selected];
      selected = null;
      renderSide();
      back?.focus();
    });
    const head = document.createElement("div");
    head.className = "brief-head";
    head.append(textEl("h3", `📄 Unit ${selected} brief`), close);
    brief.replaceChildren(head, ...tooltipContent(selected, unit, geo, labels).slice(1));
  }

  // ---- filter bar ----
  const viewing = document.getElementById("viewing");
  const untaggedBox = document.getElementById("include-untagged");
  const filters = createFilters(document.getElementById("filters"), {
    species: meta.species,
    state: store.get(),
    today,
    weekendDef: profile.weekend_def,
    untaggedBox,
    onChange: (patch) => store.set(patch),
    onPermits: (codes) => savePermits(codes),
  });

  const yearsOf = (s) => rangeYears(s);
  const tagsInRange = (s) => yearsOf(s).flatMap((y) => tagsByYear[y] || []);
  const ctxFor = (s) => {
    const year = yearsOf(s)[0];
    return { tags: tagsInRange(s), permits: permitsByYear[year] || [], year };
  };
  function paintFilters() {
    const s = store.get();
    filters.update(s, ctxFor(s));
    renderViewing(viewing, s, meta.species, tagsInRange(s));
  }

  async function loadYears(s) {
    const missing = yearsOf(s).filter((y) => !(y in tagsByYear));
    if (!missing.length) return false;
    await Promise.all(
      missing.map(async (y) => {
        const [t, p] = await Promise.all([
          getJSON(`/api/tags?year=${y}`),
          getJSON(`/api/permits?year=${y}`),
        ]);
        tagsByYear[y] = t.tags;
        permitsByYear[y] = p.permits;
      }),
    );
    return true;
  }

  async function savePermits(codes) {
    const s = store.get();
    const year = yearsOf(s)[0];
    const others = (permitsByYear[year] || []).filter(
      (p) => !MAP_PERMITS.some(([k]) => k === p),
    );
    try {
      const saved = await putJSON(`/api/permits?year=${year}`, { permits: [...others, ...codes] });
      permitsByYear[year] = saved.permits;
      window.dispatchEvent(new CustomEvent(PERMITS_CHANGED, { detail: { ...saved, source: "map" } }));
      paintFilters();
      fetcher.schedule(availabilityPath(store.get()));
    } catch (e) {
      toast(e.message || String(e), "error");
      paintFilters(); // revert the chips to what the server holds
    }
  }

  // ---- availability ----
  const fetcher = createFetcher(getJSON, {
    onStart: () => mapCard.setAttribute("aria-busy", "true"),
    onResult: (data) => {
      mapCard.removeAttribute("aria-busy");
      availability = data;
      const statuses = Object.fromEntries(
        Object.entries(data.units).map(([id, u]) => [id, u.status]),
      );
      map.setStatuses(statuses);
      renderLegend(legend, data.summary, data.include_untagged);
      renderSide();
      // Test hooks: the query the map shows, and a counter that ticks on every repaint.
      document.body.dataset.availability = availabilityPath(store.get());
      document.body.dataset.availabilitySeq = String(++repaints);
    },
    onError: (err) => {
      mapCard.removeAttribute("aria-busy");
      toast(err.message || String(err), "error");
    },
  });

  function syncUrl() {
    if (parseRoute(location.hash).tab !== "map") return;
    const hash = `#/map?${toQuery(store.get())}`;
    if (location.hash !== hash) history.replaceState(null, "", hash);
  }

  store.subscribe((s, prev) => {
    syncUrl();
    paintFilters();
    fetcher.schedule(availabilityPath(s));
    if (s.start !== prev.start || s.end !== prev.end) {
      loadYears(s)
        .then((loaded) => loaded && paintFilters())
        .catch(() => {});
    }
  });

  window.addEventListener(TAGS_CHANGED, (e) => {
    if (!e.detail) return;
    tagsByYear[e.detail.year] = e.detail.tags;
    paintFilters();
    fetcher.schedule(availabilityPath(store.get()));
  });
  window.addEventListener(PERMITS_CHANGED, (e) => {
    if (!e.detail || e.detail.source === "map") return;
    permitsByYear[e.detail.year] = e.detail.permits;
    paintFilters();
    fetcher.schedule(availabilityPath(store.get()));
  });

  await loadYears(store.get()).catch(() => {});
  paintFilters();
  renderLegend(legend, {}, store.get().includeUntagged);
  renderSide();
  syncUrl();
  await fetcher.now(availabilityPath(store.get()));

  mapTab = {
    /** A `#/map?...` route arrived (paste, bookmark, back/forward): adopt its filters. */
    applyRoute(query) {
      if (!hasFilterKeys(query)) {
        syncUrl(); // bare #/map (the tab link): keep the current filters
        return;
      }
      store.set(stateFromQuery(query, defaults, { speciesIds }));
    },
    store,
    map,
  };
  document.body.dataset.mapReady = "1";
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
  if (r.tab === "map" && mapTab) mapTab.applyRoute(r.query);
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
initTabs();
initVersion();
initMapTab().catch((e) => {
  toast(`The map failed to load: ${e.message || e}`, "error");
});
