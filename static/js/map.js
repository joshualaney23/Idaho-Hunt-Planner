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
export function unitLabel(id, status, notes = false) {
  const base = `Unit ${id}, ${status === "untagged" ? "open with a tag you don't hold" : status}`;
  return notes ? `${base}, see notes` : base;
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
  // A dot over units with a `see_notes` reason: the status color is unchanged (D3 still
  // narrows), the dot only says "read the brochure notes for this unit" (§4.3).
  const noteDots = svgEl("g", { class: "note-dots", "aria-hidden": "true" });
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
  overlay.append(untaggedRings, hlRings, noteDots, hover);
  svg.replaceChildren(fills, overlay, labels);

  function setStatuses(map = {}, notes = []) {
    const rings = [];
    const dots = [];
    const noted = new Set(notes);
    for (const id of Object.keys(paths)) {
      const st = gmu.units[id].closed ? "closed" : map[id] || "closed";
      statuses[id] = st;
      paths[id].setAttribute("class", `u-${st}`);
      paths[id].setAttribute("aria-label", unitLabel(id, st, noted.has(id)));
      if (noted.has(id)) {
        const [x, y] = gmu.units[id].label;
        dots.push(svgEl("circle", { cx: x, cy: y - 7, r: 2.6, class: "note-dot" }));
      }
      // Dark text on colored fills, --text3 on closed/untagged (closed fill) units (§4.3).
      texts[id].setAttribute("class", st === "open" || st === "restricted" ? "" : "c");
      if (st === "untagged") rings.push(svgEl("path", { d: gmu.units[id].d, class: "ut" }));
    }
    untaggedRings.replaceChildren(...rings);
    noteDots.replaceChildren(...dots);
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

/** The stored source PDF opened at a page (`GET /api/documents/<id>/pdf`, §6, T-10). */
export function pdfHref(documentId, page) {
  return `/api/documents/${encodeURIComponent(documentId)}/pdf${page ? `#page=${page}` : ""}`;
}

/** `p.N` as a link to the source PDF page (or plain text when the document is unknown). */
export function pageLink(s) {
  if (!s.source_page) return null;
  if (s.document_id == null) return textEl("span", ` p.${s.source_page}`, "muted");
  const a = textEl("a", `p.${s.source_page}`, "pg");
  a.href = pdfHref(s.document_id, s.source_page);
  a.target = "_blank";
  a.rel = "noopener";
  a.title = `Source PDF, page ${s.source_page}`;
  return a;
}

/**
 * One season line, shared by the tooltip, the results table and the brief: weapon
 * badge, sex, dates, narrowing reasons (yellow), informational restrictions (neutral),
 * source page. `labels` = `{restrictions, reasons, tags}` from `/api/meta` plus the
 * tags held (`{<tag id>: "🦌 Regular deer tag"}`, T-17c), which is what lets a line say
 * *which* tag unlocks it when a controlled and a general tag both apply (§7.2).
 * With `{link: true}` the page is a `p.N` link to the PDF (the tooltip can't be
 * clicked); `{coverage: false}` drops the partial-coverage note for callers that
 * already show it (the Unit Brief).
 */
export function seasonLine(s, labels = {}, { link = false, coverage = true } = {}) {
  const restr = labels.restrictions || {};
  const reasons = labels.reasons || {};
  const tagLabels = labels.tags || {};
  const label = (code) => restr[code]?.label || reasons[code]?.label || code.replace(/_/g, " ");
  const row = document.createElement("div");
  row.className = "sline";
  const weapon = WEAPON_BADGE[s.weapon] || "short-range";
  row.appendChild(badge(weapon, s.weapon.replace(/_/g, "-")));
  const what = [
    s.hunt_number ? `Controlled #${s.hunt_number}` : null,
    s.sex && s.sex !== "either" ? s.sex : null,
    seasonDates(s),
  ].filter(Boolean);
  row.appendChild(textEl("span", `${what.join(" ")} `));
  if (s.status === "untagged") row.appendChild(badge("untagged", "no tag"));
  const narrowing = new Set(s.reasons || []);
  for (const code of s.reasons || []) row.appendChild(badge("r", label(code)));
  for (const code of s.restrictions || []) {
    if (!narrowing.has(code)) row.appendChild(badge("info", label(code)));
  }
  if (link && s.source_page) {
    row.append(" ", pageLink(s));
  } else if (s.source_page) {
    row.appendChild(textEl("span", ` p.${s.source_page}`, "muted"));
  }
  const via = (s.tag_ids || []).map((id) => tagLabels[id]).filter(Boolean);
  if (via.length) row.appendChild(textEl("div", `via ${via.join(" · ")}`, "muted"));
  // §7.2: a controlled hunt that covers only part of the unit carries the brochure's own
  // area description and the page it is printed on — never a bare "portion of unit".
  if (coverage && s.coverage === "partial" && s.coverage_note) {
    row.appendChild(textEl("div", s.coverage_note, "muted"));
  }
  return row;
}

// ---- zoom (T-08, §4.3): wheel / pinch on the viewBox, clamped to 1×–8× ----------
export const ZOOM_MIN = 1;
export const ZOOM_MAX = 8;

/** Parse "x y w h" into `{x, y, w, h}`. */
export function parseViewBox(vb) {
  const [x, y, w, h] = String(vb).trim().split(/[\s,]+/).map(Number);
  return { x, y, w, h };
}

export const viewBoxString = (b) =>
  [b.x, b.y, b.w, b.h].map((n) => Math.round(n * 1000) / 1000).join(" ");

const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));

/** Keep a `w`×`h` box inside `base` (it never pans past the map's edges). */
function clampBox(base, b) {
  return {
    x: clamp(b.x, base.x, base.x + base.w - b.w),
    y: clamp(b.y, base.y, base.y + base.h - b.h),
    w: b.w,
    h: b.h,
  };
}

/** The zoom level of `cur` relative to `base` (1 = whole map). */
export const zoomScale = (base, cur) => base.w / cur.w;

/**
 * Zoom `cur` by `factor` around the focal point (`fx`, `fy`, in SVG user units), so
 * the point under the cursor stays put. The level is clamped to [min, max] and the box
 * to the map's bounds.
 */
export function zoomBox(base, cur, factor, fx, fy, { min = ZOOM_MIN, max = ZOOM_MAX } = {}) {
  const scale = clamp(zoomScale(base, cur) * factor, min, max);
  const w = base.w / scale;
  const h = base.h / scale;
  const k = w / cur.w;
  return clampBox(base, { x: fx - (fx - cur.x) * k, y: fy - (fy - cur.y) * k, w, h });
}

/** Pan `cur` by (`dx`, `dy`) user units, clamped to the map's bounds. */
export function panBox(base, cur, dx, dy) {
  return clampBox(base, { ...cur, x: cur.x + dx, y: cur.y + dy });
}

/** Wheel delta → zoom factor: one 100 px notch ≈ 1.22×; ctrl+wheel (trackpad pinch) is finer-grained, so it's scaled up. */
export function wheelFactor(e) {
  const px = e.deltaY * (e.deltaMode === 1 ? 16 : e.deltaMode === 2 ? 400 : 1);
  return Math.exp(-px * (e.ctrlKey ? 0.01 : 0.002));
}

/**
 * Wheel, pinch (two pointers) and drag-to-pan (one pointer, when zoomed) on `svg`'s
 * viewBox. A drag never also counts as a click on the unit under it. Returns
 * `{zoomBy(factor, clientPoint?), reset(), scale(), box(), onChange(cb)}`.
 */
export function enableZoom(svg, { min = ZOOM_MIN, max = ZOOM_MAX } = {}) {
  const base = parseViewBox(svg.getAttribute("viewBox"));
  let cur = { ...base };
  const cbs = [];
  const pointers = new Map();
  let pinch = null;
  let moved = 0;

  function apply(next) {
    cur = next;
    svg.setAttribute("viewBox", viewBoxString(cur));
    const s = zoomScale(base, cur);
    svg.dataset.zoom = String(Math.round(s * 100) / 100);
    // Let the page scroll over an unzoomed map; take every gesture once zoomed in.
    svg.style.touchAction = s > 1.001 ? "none" : "pan-x pan-y";
    for (const cb of cbs) cb(s, cur);
  }

  /** Client pixels → SVG user units (the svg keeps the viewBox aspect: height auto). */
  function toUser(clientX, clientY) {
    const r = svg.getBoundingClientRect();
    return {
      x: cur.x + ((clientX - r.left) / (r.width || 1)) * cur.w,
      y: cur.y + ((clientY - r.top) / (r.height || 1)) * cur.h,
    };
  }

  function zoomBy(factor, point) {
    const r = svg.getBoundingClientRect();
    const p = point
      ? toUser(point.clientX, point.clientY)
      : toUser(r.left + r.width / 2, r.top + r.height / 2);
    apply(zoomBox(base, cur, factor, p.x, p.y, { min, max }));
  }

  svg.addEventListener(
    "wheel",
    (e) => {
      e.preventDefault();
      zoomBy(wheelFactor(e), e);
    },
    { passive: false },
  );

  const dist = (a, b) => Math.hypot(a.x - b.x, a.y - b.y);
  svg.addEventListener("pointerdown", (e) => {
    pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
    if (pointers.size === 1) moved = 0;
    if (pointers.size === 2) {
      const [a, b] = [...pointers.values()];
      pinch = { d: dist(a, b) };
    }
  });
  svg.addEventListener("pointermove", (e) => {
    const prev = pointers.get(e.pointerId);
    if (!prev) return;
    const next = { x: e.clientX, y: e.clientY };
    pointers.set(e.pointerId, next);
    if (pointers.size >= 2 && pinch) {
      const [a, b] = [...pointers.values()];
      const d = dist(a, b);
      if (pinch.d > 0 && d > 0) {
        zoomBy(d / pinch.d, { clientX: (a.x + b.x) / 2, clientY: (a.y + b.y) / 2 });
      }
      pinch.d = d;
      moved += 10; // a pinch is never a click
      return;
    }
    if (zoomScale(base, cur) <= 1.001) return;
    moved += Math.abs(next.x - prev.x) + Math.abs(next.y - prev.y);
    if (moved < 4) return;
    const r = svg.getBoundingClientRect();
    const k = cur.w / (r.width || 1);
    apply(panBox(base, cur, -(next.x - prev.x) * k, -(next.y - prev.y) * k));
  });
  const up = (e) => {
    pointers.delete(e.pointerId);
    if (pointers.size < 2) pinch = null;
  };
  svg.addEventListener("pointerup", up);
  svg.addEventListener("pointercancel", up);
  svg.addEventListener("pointerleave", up);
  // A drag or pinch ends with a click on whatever unit is under the pointer: swallow it
  // (capture phase, before the unit path's own click handler).
  svg.addEventListener(
    "click",
    (e) => {
      if (moved >= 4) {
        e.stopPropagation();
        e.preventDefault();
      }
      moved = 0;
    },
    true,
  );

  apply(cur);
  return {
    zoomBy,
    reset: () => apply({ ...base }),
    scale: () => zoomScale(base, cur),
    box: () => ({ ...cur }),
    onChange: (cb) => cbs.push(cb),
  };
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
  if (geo?.closed) {
    frag.push(textEl("div", "Yellowstone National Park: no hunting.", "muted"));
    return frag;
  }
  // T-27: with several species the seasons are grouped under a species-icon header
  // carrying that species' own status, so a combined `restricted` says which one narrowed it.
  const per = unit?.per_species;
  if (per) {
    for (const [sp, ps] of Object.entries(per)) {
      frag.push(speciesHeader(sp, ps, labels));
      const mine = seasons.filter((s) => s.species === sp);
      if (!mine.length) frag.push(textEl("div", "No season for your tags and dates.", "muted"));
      for (const s of mine) frag.push(seasonLine(s, labels));
    }
    return frag;
  }
  if (!seasons.length) frag.push(textEl("div", "No season for your tags and dates.", "muted"));
  for (const s of seasons) frag.push(seasonLine(s, labels));
  return frag;
}

/** "🦌 Deer — restricted": one species' header inside a multi-species tooltip. */
export function speciesHeader(sp, ps, labels = {}) {
  const info = (labels.species || {})[sp] || {};
  const h = textEl("h4", `${info.icon || ""} ${info.label || sp}`.trim(), "sp-head");
  h.dataset.species = sp;
  const st = ps?.status || "closed";
  h.append(" ", textEl("span", st === "untagged" ? "no matching tag" : st, `st ${st}`));
  return h;
}

/**
 * Legend rows with a count per status (§4.3). Untagged appears only when it's on.
 * With a T-27 `all` selection the green caption says what green now means (§12).
 */
export function legendItems(summary = {}, includeUntagged = false, { mode = "any", speciesCount = 1 } = {}) {
  const everySpecies = mode === "all" && speciesCount > 1;
  const rows = [
    ["open", everySpecies ? "Open — every selected species open" : "Open — full range, no narrowing rules"],
    ["restricted", "Restricted / partial"],
    ["closed", "Closed / no matching tag"],
  ];
  if (includeUntagged) rows.splice(2, 0, ["untagged", "Open with a tag you don't hold"]);
  return rows.map(([st, text]) => ({ status: st, text, count: summary[st] ?? 0 }));
}

/** Render the legend into `el`. */
export function renderLegend(el, summary, includeUntagged, opts) {
  el.replaceChildren(
    ...legendItems(summary, includeUntagged, opts).map(({ status, text, count }) => {
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
