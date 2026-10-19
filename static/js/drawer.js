// Unit Brief drawer (T-08, DESIGN.md §4.3): opened by a click / Enter on a map unit
// (UNIT_OPEN) or a unit button in the results table. It fetches
// GET /api/units/<id>/seasons?year= and shows
//   - a timeline: inline SVG, Aug 1 → Jan 31 of the license year, one bar per season
//     colored by weapon, the selected date range as a translucent --gold band, today
//     as a line;
//   - every season (species you hold tags for, plus the one you're viewing, first),
//     with notes and qualifiers verbatim from the PDF and a `p.N` link to the page;
//   - links: the IDFG Hunt Planner page for the unit and each source PDF.
// It is a modal <dialog> on the right: Esc and a click outside close it, Tab is
// trapped inside, and focus goes back to whatever opened it.

import { badge, textEl } from "./ui.js";
import { days, fmtShort, parseISO } from "./dates.js";
import { pageLink, pdfHref, seasonLine } from "./map.js";

const NS = "http://www.w3.org/2000/svg";
export const TIMELINE_WIDTH = 460;
export const ROW_H = 9;
export const ROW_GAP = 3;
export const AXIS_H = 16;
const MONTHS = ["Aug", "Sep", "Oct", "Nov", "Dec", "Jan"];

/** The timeline's window for a license year: Aug 1 of `year` → Jan 31 of `year + 1`. */
export function timelineWindow(year) {
  return { start: `${year}-08-01`, end: `${year + 1}-01-31` };
}

/**
 * Pure geometry for the timeline. Day `i` of the window (Aug 1 = 0) spans
 * [i, i + 1) × `width / total`. Returns
 * `{width, height, total, months:[{x,label}], bars:[{id,x,w,y,weapon,clipStart,clipEnd}],
 *   outside, band:{x,w}|null, today:x|null}`.
 * Seasons entirely outside the window (spring bear, turkey) get no bar and are counted
 * in `outside`; the list below the timeline still shows them.
 */
export function timelineModel(seasons, { year, start, end, today, width = TIMELINE_WIDTH } = {}) {
  const win = timelineWindow(year);
  const w0 = parseISO(win.start);
  const w1 = parseISO(win.end);
  const total = days(w0, w1); // 184
  const px = (n) => Math.round(((n * width) / total) * 100) / 100;
  const idx = (iso) => days(w0, parseISO(iso)) - 1; // Aug 1 → 0; before the window → -1
  const span = (a, b) => {
    // Clip [a, b] (ISO, inclusive) to the window; null when they don't meet.
    if (b < win.start || a > win.end) return null;
    const i0 = a < win.start ? 0 : idx(a);
    const i1 = b > win.end ? total - 1 : idx(b);
    return { x: px(i0), w: px(i1 + 1) - px(i0), clipStart: a < win.start, clipEnd: b > win.end };
  };

  const months = [];
  for (let m = 0; m < 6; m++) {
    const d = new Date(year, 7 + m, 1, 12);
    months.push({ x: px(days(w0, d) - 1), label: MONTHS[m] });
  }
  const bars = [];
  let outside = 0;
  for (const s of seasons) {
    const sp = span(s.start, s.end);
    if (!sp) {
      outside++;
      continue;
    }
    bars.push({ id: s.id, weapon: s.weapon, y: AXIS_H + bars.length * (ROW_H + ROW_GAP), ...sp });
  }
  const height = AXIS_H + Math.max(1, bars.length) * (ROW_H + ROW_GAP) + 2;
  const band = start && end ? span(start, end) : null;
  let todayX = null;
  if (today && today >= win.start && today <= win.end) todayX = px(idx(today) + 0.5);
  return {
    width,
    height,
    total,
    months,
    bars,
    outside,
    band: band && { x: band.x, w: band.w },
    today: todayX,
  };
}

function svgEl(tag, attrs = {}) {
  const e = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, String(v));
  return e;
}

const WEAPON_TEXT = {
  any: "any weapon",
  archery: "archery",
  muzzleloader: "muzzleloader",
  short_range: "short-range",
  shotgun: "shotgun",
  falconry: "falconry",
};

/** Render the model as an inline SVG (`.timeline`); colors are classes (app.css). */
export function renderTimeline(model, seasons) {
  const byId = Object.fromEntries(seasons.map((s) => [s.id, s]));
  const svg = svgEl("svg", {
    class: "timeline",
    viewBox: `0 0 ${model.width} ${model.height}`,
    role: "img",
    "aria-label": `Season timeline, Aug 1 to Jan 31: ${model.bars.length} season${
      model.bars.length === 1 ? "" : "s"
    }`,
  });
  const grid = svgEl("g", { class: "tl-grid" });
  for (const m of model.months) {
    grid.appendChild(svgEl("line", { x1: m.x, x2: m.x, y1: 0, y2: model.height }));
    const t = svgEl("text", { x: m.x + 3, y: 11 });
    t.textContent = m.label;
    grid.appendChild(t);
  }
  svg.appendChild(grid);
  if (model.band) {
    svg.appendChild(
      svgEl("rect", { class: "tl-band", x: model.band.x, y: 0, width: Math.max(model.band.w, 1.5), height: model.height }),
    );
  }
  const bars = svgEl("g", { class: "tl-bars" });
  for (const b of model.bars) {
    const s = byId[b.id] || {};
    const r = svgEl("rect", {
      class: `tl-bar tl-${b.weapon}`,
      x: b.x,
      y: b.y,
      width: Math.max(b.w, 1.5),
      height: ROW_H,
      rx: 2,
    });
    r.dataset.season = String(b.id);
    const title = svgEl("title");
    title.textContent = `${s.species_label || ""} ${WEAPON_TEXT[b.weapon] || b.weapon}: ${
      s.start ? fmtShort(parseISO(s.start)) : ""
    } – ${s.end ? fmtShort(parseISO(s.end)) : ""}`.trim();
    r.appendChild(title);
    bars.appendChild(r);
  }
  svg.appendChild(bars);
  if (model.today !== null) {
    svg.appendChild(svgEl("line", { class: "tl-today", x1: model.today, x2: model.today, y1: 0, y2: model.height }));
  }
  return svg;
}

/**
 * Which seasons to show first: the species you hold tags for plus the one you're
 * viewing. Returns `{primary, other}` (both keep the API's order).
 */
export function splitSeasons(seasons, { heldSpecies = [], viewing = null } = {}) {
  // `viewing` is one id or, since T-27, the whole species selection.
  const seen = Array.isArray(viewing) ? viewing : [viewing];
  const want = new Set([...heldSpecies, ...seen].filter(Boolean));
  const primary = [];
  const other = [];
  for (const s of seasons) (want.has(s.species_id) ? primary : other).push(s);
  return { primary, other };
}

/** Distinct source documents of the seasons, with the pages cited from each. */
export function sourceDocs(seasons) {
  const docs = new Map();
  for (const s of seasons) {
    if (s.document_id == null) continue;
    const d = docs.get(s.document_id) || {
      id: s.document_id,
      title: s.document_title || `Document ${s.document_id}`,
      pages: new Set(),
    };
    if (s.source_page) d.pages.add(s.source_page);
    docs.set(s.document_id, d);
  }
  return [...docs.values()].map((d) => ({ ...d, pages: [...d.pages].sort((a, b) => a - b) }));
}

/** Index of the element Tab (or Shift+Tab) should move to, wrapping at both ends. */
export function trapIndex(count, current, shift) {
  if (count <= 0) return -1;
  if (current < 0) return shift ? count - 1 : 0;
  return shift ? (current - 1 + count) % count : (current + 1) % count;
}

const FOCUSABLE =
  'a[href], button:not([disabled]), summary, input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

/**
 * Can Tab land on `el`? Content of a closed <details> still has layout boxes in Chrome
 * (it's only `content-visibility: hidden`), so `getClientRects()` isn't enough: ask
 * `checkVisibility()` and skip anything inside a closed <details> but its <summary>.
 */
function tabbable(el) {
  if (el.hidden || el.closest?.("[hidden]")) return false;
  const closed = el.closest?.("details:not([open])");
  if (closed && !(el.tagName === "SUMMARY" && el.parentElement === closed)) return false;
  if (typeof el.checkVisibility === "function") {
    return el.checkVisibility({ contentVisibilityAuto: true, visibilityProperty: true });
  }
  return el.getClientRects?.().length !== 0;
}

/** One season in the list: the shared season line plus tag, coverage, bag and notes. */
function seasonItem(s, labels) {
  const li = document.createElement("li");
  li.className = "bseason";
  li.dataset.season = String(s.id);
  // No engine run here: show the PDF's narrowing codes yellow, the rest neutral (§5.1).
  const narrowing = (s.restrictions || []).filter(
    (c) => labels.restrictions?.[c]?.severity === "narrowing",
  );
  // The hunt number rides on the season line itself (T-17c) and the coverage note has
  // its own row below, so neither is repeated in the meta line.
  li.appendChild(
    seasonLine({ ...s, reasons: narrowing }, labels, { link: true, coverage: false }),
  );
  const meta = [
    s.tag_type_label,
    s.zone ? `${s.zone} zone` : null,
    s.hunt_area && s.hunt_area !== s.hunt_number ? `area ${s.hunt_area}` : null,
    s.hunt_class && s.hunt_class !== "general" ? s.hunt_class : null,
    s.sub_species,
  ].filter(Boolean);
  if (meta.length) li.appendChild(textEl("div", meta.join(" · "), "muted"));
  if (s.coverage === "partial") {
    const cov = document.createElement("div");
    cov.append(badge("r", "portion of unit"), textEl("span", s.coverage_note || "", "muted"));
    li.appendChild(cov);
  }
  if (s.bag_limit) li.appendChild(textEl("div", `Bag: ${s.bag_limit}`, "muted"));
  if (s.notes) li.appendChild(textEl("p", s.notes, "note"));
  return li;
}

function speciesGroups(seasons, labels) {
  const out = [];
  let cur = null;
  for (const s of seasons) {
    if (!cur || cur.species !== s.species_id) {
      cur = { species: s.species_id, label: `${s.species_icon || ""} ${s.species_label || s.species_id}`.trim(), list: document.createElement("ul") };
      cur.list.className = "bseasons";
      out.push(cur);
    }
    cur.list.appendChild(seasonItem(s, labels));
  }
  return out.flatMap((g) => [textEl("h4", g.label), g.list]);
}

/**
 * Create the drawer (appended to `document.body`). Options: `labels`, `fetchBrief(unit,
 * year)` → the endpoint's JSON, `today` (ISO), `gmu` (gmu.json, for the elk zone).
 * Returns `{open(unit, {state, unit: availabilityEntry, opener}), close(), isOpen(), el}`.
 */
export function createDrawer({ labels = {}, fetchBrief, today = null, gmu = null } = {}) {
  const dlg = document.createElement("dialog");
  dlg.className = "drawer";
  dlg.id = "unit-brief";
  dlg.setAttribute("aria-labelledby", "brief-title");
  const inner = document.createElement("div");
  inner.className = "drawer-inner";
  const head = document.createElement("div");
  head.className = "brief-head";
  const title = textEl("h2", "Unit brief");
  title.id = "brief-title";
  const closeBtn = textEl("button", "×", "icon-btn");
  closeBtn.type = "button";
  closeBtn.id = "brief-close";
  closeBtn.setAttribute("aria-label", "Close the unit brief");
  head.append(title, closeBtn);
  const body = document.createElement("div");
  body.className = "drawer-body";
  body.setAttribute("aria-live", "polite");
  inner.append(head, body);
  dlg.appendChild(inner);
  document.body.appendChild(dlg);

  let opener = null;
  let token = 0;
  let current = null;

  function close() {
    if (!current) return;
    current = null;
    token++;
    if (dlg.open && typeof dlg.close === "function") dlg.close();
    else dlg.removeAttribute?.("open");
    document.body.dataset.brief = "";
    const back = opener;
    opener = null;
    if (back && typeof back.focus === "function" && back.isConnected !== false) back.focus();
  }

  closeBtn.addEventListener("click", close);
  // Esc: the dialog's own cancel event (fires once per Esc press).
  dlg.addEventListener("cancel", (e) => {
    e.preventDefault();
    close();
  });
  dlg.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      e.preventDefault();
      close();
      return;
    }
    if (e.key !== "Tab") return;
    const items = [...dlg.querySelectorAll(FOCUSABLE)].filter(tabbable);
    const next = trapIndex(items.length, items.indexOf(document.activeElement), e.shiftKey);
    e.preventDefault();
    if (next >= 0) items[next].focus();
  });
  // A click on the backdrop lands on the <dialog> itself (the panel is .drawer-inner).
  dlg.addEventListener("click", (e) => {
    if (e.target === dlg) close();
  });
  // Closed some other way (form method=dialog, devtools): still restore focus. The
  // event is async, so a stale one can arrive after a quick re-open: ignore it then.
  dlg.addEventListener("close", () => {
    if (current && !dlg.open) close();
  });

  function render(unit, data, ctx) {
    const { state = {}, unit: avail } = ctx;
    const geo = gmu?.units?.[unit];
    const u = data.unit || {};
    const parts = [];

    const zone = u.elk_zone || geo?.zone;
    const sub = [zone ? `Elk zone: ${zone}` : null, u.region ? `Region: ${u.region}` : null]
      .filter(Boolean)
      .join(" · ");
    if (sub) parts.push(textEl("div", sub, "muted"));

    if (avail) {
      const st = avail.status || "closed";
      const line = document.createElement("div");
      line.className = "brief-status";
      line.append(
        textEl("span", "Your filters: ", "muted"),
        textEl("span", st === "untagged" ? "open with a tag you don't hold" : st, `st ${st}`),
      );
      if (state.start && state.end) {
        line.append(
          textEl(
            "span",
            ` · ${fmtShort(parseISO(state.start))} – ${fmtShort(parseISO(state.end))}`,
            "muted",
          ),
        );
      }
      if (avail.days_open) line.append(textEl("span", ` · ${avail.days_open} day${avail.days_open === 1 ? "" : "s"} open`, "muted"));
      parts.push(line);
    }
    if (geo?.closed) parts.push(textEl("p", "Yellowstone National Park: no hunting.", "muted"));

    const { primary, other } = splitSeasons(data.seasons || [], {
      heldSpecies: data.held_species || [],
      viewing: state.species,
    });

    // Timeline over the seasons listed first (or all of them when none match).
    const tlSeasons = primary.length ? primary : other;
    const tl = document.createElement("section");
    tl.className = "brief-sec";
    tl.append(textEl("h3", `Timeline · ${data.year} license year`));
    const model = timelineModel(tlSeasons, {
      year: data.year,
      start: state.start,
      end: state.end,
      today,
    });
    tl.appendChild(renderTimeline(model, tlSeasons));
    const key = document.createElement("div");
    key.className = "tl-key muted";
    for (const w of ["any", "archery", "muzzleloader", "short_range"]) {
      if (!tlSeasons.some((s) => s.weapon === w)) continue;
      const k = document.createElement("span");
      const sw = document.createElement("i");
      sw.className = `tl-sw tl-${w}`;
      sw.setAttribute("aria-hidden", "true");
      k.append(sw, textEl("span", WEAPON_TEXT[w]));
      key.appendChild(k);
    }
    const band = document.createElement("span");
    const bsw = document.createElement("i");
    bsw.className = "tl-sw tl-band-sw";
    bsw.setAttribute("aria-hidden", "true");
    band.append(bsw, textEl("span", "your dates"));
    key.appendChild(band);
    if (model.today !== null) {
      const t = document.createElement("span");
      const tsw = document.createElement("i");
      tsw.className = "tl-sw tl-today-sw";
      tsw.setAttribute("aria-hidden", "true");
      t.append(tsw, textEl("span", "today"));
      key.appendChild(t);
    }
    tl.appendChild(key);
    if (model.outside) {
      tl.appendChild(
        textEl("p", `${model.outside} season${model.outside === 1 ? "" : "s"} outside Aug–Jan (listed below).`, "muted"),
      );
    }
    parts.push(tl);

    const list = document.createElement("section");
    list.className = "brief-sec";
    list.append(textEl("h3", "Seasons"));
    if (!(data.seasons || []).length) {
      list.append(textEl("p", `No published seasons for unit ${unit} in ${data.year}.`, "muted"));
    } else if (!primary.length) {
      list.append(textEl("p", "No seasons for your tags or the species you're viewing.", "muted"));
    }
    list.append(...speciesGroups(primary, labels));
    if (other.length) {
      const more = document.createElement("details");
      more.className = "brief-more";
      more.append(
        textEl("summary", `${other.length} season${other.length === 1 ? "" : "s"} for other species`),
        ...speciesGroups(other, labels),
      );
      list.appendChild(more);
    }
    parts.push(list);

    const links = document.createElement("section");
    links.className = "brief-sec brief-links";
    links.append(textEl("h3", "Links"));
    const ul = document.createElement("ul");
    if (u.idfg_url) {
      const a = textEl("a", `IDFG Hunt Planner: Unit ${unit} ↗`);
      a.href = u.idfg_url;
      a.target = "_blank";
      a.rel = "noopener";
      const li = document.createElement("li");
      li.appendChild(a);
      ul.appendChild(li);
    }
    for (const d of sourceDocs(data.seasons || [])) {
      const li = document.createElement("li");
      const a = textEl("a", `Source PDF: ${d.title}`);
      a.href = pdfHref(d.id, d.pages[0]);
      a.target = "_blank";
      a.rel = "noopener";
      li.appendChild(a);
      for (const p of d.pages) li.append(" ", pageLink({ document_id: d.id, source_page: p }));
      ul.appendChild(li);
    }
    if (!ul.childElementCount) ul.appendChild(textEl("li", "No links for this unit.", "muted"));
    links.appendChild(ul);
    parts.push(links);
    parts.push(
      textEl("p", "Verify every season against the official IDFG regulations before you hunt.", "muted disclaim"),
    );
    body.replaceChildren(...parts);
  }

  async function open(unit, ctx = {}) {
    const mine = ++token;
    if (!current) opener = ctx.opener || document.activeElement || null;
    current = unit;
    title.textContent = `📄 Unit ${unit} brief`;
    body.replaceChildren(textEl("p", "Loading…", "muted"));
    if (!dlg.open) {
      if (typeof dlg.showModal === "function") dlg.showModal();
      else dlg.setAttribute("open", "");
    }
    closeBtn.focus();
    document.body.dataset.brief = unit;
    const year = Number((ctx.state?.start || today || "").slice(0, 4)) || new Date().getFullYear();
    try {
      const data = await fetchBrief(unit, year);
      if (mine !== token) return;
      render(unit, data, ctx);
      document.body.dataset.briefLoaded = unit;
    } catch (e) {
      if (mine !== token) return;
      body.replaceChildren(textEl("p", `Could not load the brief: ${e.message || e}`, "err"));
    }
  }

  return { open, close, isOpen: () => current !== null, current: () => current, el: dlg };
}
