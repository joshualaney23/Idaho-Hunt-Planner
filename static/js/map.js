// GMU map component (T-07, DESIGN.md §4.3, §4.4).
//
// `createMap(svgEl, gmuJson)` renders one <path> per unit, then one <text> label per
// unit on top (so labels never sit under a neighbour's fill). Status colors are classes
// (`u-open`, `u-restricted`, `u-closed`, `u-untagged`) styled in app.css from theme
// tokens; nothing here sets a color.
//
// The untagged dashed outlines, the hover/focus ring and `highlight(unitIds)` are outline
// copies in an overlay group above all fills, so a gold stroke is never half-covered by
// a neighbouring path, and the paths' DOM order (= keyboard Tab order) never changes.

import { badge, textEl } from "./ui.js";
import { fmtShort, parseISO } from "./dates.js";

const NS = "http://www.w3.org/2000/svg";
export const STATUSES = ["open", "restricted", "untagged", "closed"];

function svgEl(tag, attrs = {}) {
  const e = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, String(v));
  return e;
}

/** The accessible name of a unit path: "Unit 39, open". */
export function unitLabel(id, status) {
  return `Unit ${id}, ${status === "untagged" ? "open with a tag you don't hold" : status}`;
}

/**
 * Render the map into `svgEl` and return its controller:
 * `{setStatuses(map), onHover(cb), onClick(cb), highlight(unitIds), paths}`.
 *
 * - `setStatuses({"39": "open", ...})`: units missing from the map are `closed`; a unit
 *   flagged `closed` in gmu.json (Yellowstone) always stays closed.
 * - `onHover(cb)`: `cb(unitId, point)` on mouse move/focus (`point` = `{x, y}` in viewport
 *   pixels) and `cb(null)` on leave/blur.
 * - `onClick(cb)`: `cb(unitId)` on click, Enter or Space.
 * - `highlight(ids)`: gold outline on those units (T-08 uses it for table-row hover).
 */
export function createMap(svg, gmu) {
  svg.setAttribute("viewBox", gmu.viewBox);
  svg.setAttribute("class", "gmu-map");
  svg.setAttribute("role", "group");
  svg.setAttribute("aria-label", "Idaho game management units");

  const hoverCbs = [];
  const clickCbs = [];
  const fills = svgEl("g", { class: "units" });
  const overlay = svgEl("g", { class: "overlay", "aria-hidden": "true" });
  const untaggedRings = svgEl("g", { class: "untagged-rings" });
  const hlRings = svgEl("g", { class: "hl-rings" });
  const labels = svgEl("g", { class: "labels", "aria-hidden": "true" });
  const hover = svgEl("path", { class: "hover-ring" });
  hover.setAttribute("visibility", "hidden");
  const paths = {};
  const texts = {};
  const statuses = {};

  const emitHover = (id, point) => {
    for (const cb of hoverCbs) cb(id, point);
  };
  const showHover = (id) => {
    hover.setAttribute("d", gmu.units[id].d);
    hover.setAttribute("visibility", "visible");
  };
  const hideHover = () => hover.setAttribute("visibility", "hidden");

  for (const [id, u] of Object.entries(gmu.units)) {
    const p = svgEl("path", { d: u.d, tabindex: 0, role: "button" });
    p.dataset.unit = id;
    p.addEventListener("mousemove", (e) => {
      showHover(id);
      emitHover(id, { x: e.clientX, y: e.clientY });
    });
    p.addEventListener("mouseleave", () => {
      hideHover();
      emitHover(null);
    });
    p.addEventListener("focus", () => {
      showHover(id);
      const r = p.getBoundingClientRect ? p.getBoundingClientRect() : null;
      emitHover(id, r ? { x: r.left + r.width / 2, y: r.top + r.height / 2 } : null);
    });
    p.addEventListener("blur", () => {
      hideHover();
      emitHover(null);
    });
    p.addEventListener("click", () => {
      for (const cb of clickCbs) cb(id);
    });
    p.addEventListener("keydown", (e) => {
      if (e.key !== "Enter" && e.key !== " ") return;
      e.preventDefault();
      for (const cb of clickCbs) cb(id);
    });
    fills.appendChild(p);
    paths[id] = p;

    const [x, y] = u.label;
    const t = svgEl("text", { x, y });
    t.textContent = id;
    labels.appendChild(t);
    texts[id] = t;
  }
  overlay.append(untaggedRings, hlRings, hover);
  svg.replaceChildren(fills, overlay, labels);

  function setStatuses(map = {}) {
    const rings = [];
    for (const id of Object.keys(paths)) {
      const st = gmu.units[id].closed ? "closed" : map[id] || "closed";
      statuses[id] = st;
      paths[id].setAttribute("class", `u-${st}`);
      paths[id].setAttribute("aria-label", unitLabel(id, st));
      // Dark text on colored fills, --text3 on closed/untagged (closed fill) units (§4.3).
      texts[id].setAttribute("class", st === "open" || st === "restricted" ? "" : "c");
      if (st === "untagged") rings.push(svgEl("path", { d: gmu.units[id].d, class: "ut" }));
    }
    untaggedRings.replaceChildren(...rings);
  }

  function highlight(ids = []) {
    hlRings.replaceChildren(
      ...ids.filter((id) => gmu.units[id]).map((id) => svgEl("path", { d: gmu.units[id].d, class: "hl" })),
    );
  }

  setStatuses({});
  return {
    setStatuses,
    onHover: (cb) => hoverCbs.push(cb),
    onClick: (cb) => clickCbs.push(cb),
    highlight,
    statusOf: (id) => statuses[id],
    paths,
  };
}

/**
 * Where to put a `w`×`h` tooltip for a pointer at (`x`, `y`) inside a `vw`×`vh` viewport:
 * 14 px below-right of the pointer, flipped left/up when it would overflow, and always
 * at least `pad` px inside the viewport.
 */
export function placeTip(x, y, w, h, vw, vh, { offset = 14, pad = 8 } = {}) {
  let left = x + offset;
  let top = y + offset;
  if (left + w > vw - pad) left = x - offset - w;
  if (top + h > vh - pad) top = y - offset - h;
  left = Math.max(pad, Math.min(left, vw - pad - w));
  top = Math.max(pad, Math.min(top, vh - pad - h));
  return { left: Math.round(left), top: Math.round(top) };
}

const WEAPON_BADGE = { any: "any", archery: "archery", muzzleloader: "muzzleloader" };

/** "Oct 10 – Oct 31" for a season entry. */
export function seasonDates(s) {
  return `${fmtShort(parseISO(s.start))} – ${fmtShort(parseISO(s.end))}`;
}

/**
 * One season line, shared by the tooltip and the brief: weapon badge, sex, dates,
 * narrowing reasons (yellow), informational restrictions (neutral), source page.
 * `labels` = `{restrictions, reasons}` from `/api/meta`.
 */
export function seasonLine(s, labels = {}) {
  const restr = labels.restrictions || {};
  const reasons = labels.reasons || {};
  const label = (code) => restr[code]?.label || reasons[code]?.label || code.replace(/_/g, " ");
  const row = document.createElement("div");
  row.className = "sline";
  const weapon = WEAPON_BADGE[s.weapon] || "short-range";
  row.appendChild(badge(weapon, s.weapon.replace(/_/g, "-")));
  const what = [s.sex && s.sex !== "either" ? s.sex : null, seasonDates(s)].filter(Boolean);
  row.appendChild(textEl("span", `${what.join(" ")} `));
  if (s.status === "untagged") row.appendChild(badge("untagged", "no tag"));
  const narrowing = new Set(s.reasons || []);
  for (const code of s.reasons || []) row.appendChild(badge("r", label(code)));
  for (const code of s.restrictions || []) {
    if (!narrowing.has(code)) row.appendChild(badge("info", label(code)));
  }
  if (s.source_page) row.appendChild(textEl("span", ` p.${s.source_page}`, "muted"));
  return row;
}

/**
 * Tooltip body (§4.3): unit, elk zone, and each overlapping season. `unit` is the
 * availability entry (`{status, reasons, seasons}`), `geo` the gmu.json unit.
 */
export function tooltipContent(id, unit, geo, labels) {
  const frag = [];
  const h = textEl("h3", `Unit ${id}`);
  frag.push(h);
  const st = unit?.status || "closed";
  const meta = document.createElement("div");
  meta.className = "muted";
  meta.append(
    textEl("span", `Elk zone: ${geo?.zone || "—"} · `),
    textEl("span", st === "untagged" ? "no matching tag" : st, `st ${st}`),
  );
  frag.push(meta);
  const seasons = unit?.seasons || [];
  if (geo?.closed) frag.push(textEl("div", "Yellowstone National Park: no hunting.", "muted"));
  else if (!seasons.length) frag.push(textEl("div", "No season for your tags and dates.", "muted"));
  for (const s of seasons) frag.push(seasonLine(s, labels));
  return frag;
}

/** Legend rows with a count per status (§4.3). Untagged appears only when it's on. */
export function legendItems(summary = {}, includeUntagged = false) {
  const rows = [
    ["open", "Open — full range, no narrowing rules"],
    ["restricted", "Restricted / partial"],
    ["closed", "Closed / no matching tag"],
  ];
  if (includeUntagged) rows.splice(2, 0, ["untagged", "Open with a tag you don't hold"]);
  return rows.map(([st, text]) => ({ status: st, text, count: summary[st] ?? 0 }));
}

/** Render the legend into `el`. */
export function renderLegend(el, summary, includeUntagged) {
  el.replaceChildren(
    ...legendItems(summary, includeUntagged).map(({ status, text, count }) => {
      const item = document.createElement("span");
      item.dataset.status = status;
      const sw = document.createElement("i");
      sw.className = `sw sw-${status}`;
      sw.setAttribute("aria-hidden", "true");
      item.append(sw, textEl("span", `${text} `), textEl("b", String(count)));
      return item;
    }),
  );
}
