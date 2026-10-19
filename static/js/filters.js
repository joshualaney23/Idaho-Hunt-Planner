// Where Can I Hunt filter bar (T-07, DESIGN.md §4.2).
//
// `createFilters(container, opts)` builds Species / My tags / Weapon / Permits / Dates
// with `chipGroup`, wires the include-untagged checkbox, and reports every edit as a
// state patch through `onChange(patch)`. It never fetches; main.js owns the store, the
// URL sync and the API calls (state.js), and calls `update(state, ctx)` to repaint.

import { chipGroup, textEl } from "./ui.js";
import { fmtShort, parseISO, presets, toISO } from "./dates.js";
import { activeTags, tagChipLabel } from "./tags.js";
import { MAX_SPECIES, toggleSpecies, withDate } from "./state.js";

// §4.2 shows these twelve first; everything else sits behind "More ▾".
export const FEATURED = [
  "deer",
  "elk",
  "pronghorn",
  "black_bear",
  "mountain_lion",
  "wolf",
  "forest_grouse",
  "quail",
  "chukar",
  "gray_partridge",
  "pheasant",
  "turkey",
];
const BIG_GAME_COUNT = 6; // a separator follows Wolf
const SHORT = {
  black_bear: "Bear",
  mountain_lion: "Lion",
  wolf: "Wolf",
  forest_grouse: "Grouse",
  gray_partridge: "Partridge",
  turkey: "Turkey",
};
export const WEAPON_LABELS = {
  all: "All legal weapons",
  rifle: "Rifle",
  archery: "Archery",
  muzzleloader: "Muzzleloader",
  short_range: "Short-range",
};
export const MAP_PERMITS = [
  ["archery", "🏹 Archery"],
  ["muzzleloader", "Muzzleloader"],
];
const PRESETS = [
  ["thisWeekend", "This weekend"],
  ["nextWeekend", "Next weekend"],
  ["today", "Today"],
  ["next7", "Next 7 days"],
];

/** Chip label for a species: the short §4.2 name where there is one. */
export function speciesChipLabel(sp) {
  return SHORT[sp.id] || sp.label;
}

/**
 * The user's unfilled tags that can apply to `species` (species-less tags apply to all).
 * `species` is one id or, since T-27, a list: then it's the union over the selection.
 */
export function tagsForSpecies(tags, species) {
  const want = new Set(Array.isArray(species) ? species : [species]);
  return activeTags(tags).filter((t) => want.has(t.species_id) || t.species_id == null);
}

/** The Match control's labels (§4.2): what `species_mode` means in words. */
export const MATCH_LABELS = { any: "Any", all: "All" };

/** The breadcrumb's match phrase for a selection: "both open", "all 3 open", "any open". */
export function matchPhrase(state) {
  if (state.species.length < 2) return null;
  if (state.speciesMode !== "all") return "any open";
  return state.species.length === 2 ? "both open" : `all ${state.species.length} open`;
}

/** The tag filter from the chips that are on: all of them collapses to `null` ("mine"). */
export function tagFilter(selectedIds, heldIds) {
  const on = new Set(selectedIds.map(Number));
  const next = heldIds.filter((id) => on.has(id));
  return next.length === heldIds.length ? null : next;
}

/** Which held tags are on for a `tags` filter value. */
export function selectedTagIds(filter, heldIds) {
  if (filter === null) return heldIds;
  const on = new Set(filter);
  return heldIds.filter((id) => on.has(id));
}

/**
 * Breadcrumb parts (§4.2): [species, tags, weapon, range], e.g.
 * ["🦌 Deer", "🦌 Regular deer tag", "All legal weapons", "Oct 16 – Oct 18"].
 */
export function breadcrumbParts(state, speciesList, tags) {
  const spText = state.species
    .map((id) => {
      const sp = speciesList.find((s) => s.id === id) || { label: id, icon: "" };
      return `${sp.icon} ${sp.label}`.trim();
    })
    .join(" + ");
  const held = tagsForSpecies(tags, state.species);
  const on = selectedTagIds(
    state.tags,
    held.map((t) => t.id),
  );
  let tagText;
  if (!held.length) tagText = "No tags yet";
  else if (!on.length) tagText = "No tags selected";
  else tagText = held.filter((t) => on.includes(t.id)).map(tagChipLabel).join(" · ");
  const range =
    state.start === state.end
      ? fmtShort(parseISO(state.start))
      : `${fmtShort(parseISO(state.start))} – ${fmtShort(parseISO(state.end))}`;
  return [spText, matchPhrase(state), tagText, WEAPON_LABELS[state.weapon], range].filter(
    Boolean,
  );
}

/** Render the "Viewing …" breadcrumb into `el`. */
export function renderViewing(el, state, speciesList, tags) {
  const parts = breadcrumbParts(state, speciesList, tags);
  const out = ["Viewing "];
  parts.forEach((p, i) => {
    if (i) out.push(" · ");
    out.push(textEl("b", p));
  });
  el.replaceChildren(...out);
}

/** Run `render()` and put keyboard focus back on the chip that had it (by `data-value`). */
function keepFocus(container, render) {
  const active = typeof document !== "undefined" ? document.activeElement : null;
  const value = active && container.contains?.(active) ? active.dataset?.value : undefined;
  render();
  if (value === undefined) return;
  for (const b of container.querySelectorAll?.("button") || []) {
    if (b.dataset.value === value) {
      b.focus();
      return;
    }
  }
}

function filterGroup(labelText, id, ...content) {
  const fg = document.createElement("div");
  fg.className = "fg";
  fg.id = `${id}-group`;
  const l = textEl("span", labelText, "l");
  l.id = `${id}-label`;
  fg.append(l, ...content);
  return fg;
}

/**
 * Build the filter bar into `container`.
 * - `species`: `/api/meta` species (`published_seasons` decides which chips are enabled).
 * - `state`: the initial filter state.
 * - `today`: local today (presets).
 * - `weekendDef`: profile weekend definition (presets).
 * - `untaggedBox`: the include-untagged checkbox.
 * - `onChange(patch)`: a state patch after any edit.
 * - `onPermits(codes)`: the map permits the user now wants on (archery/muzzleloader).
 * Returns `{update(state, {tags, permits, year})}`.
 */
export function createFilters(container, opts) {
  const { species, today, weekendDef = "fri_sun", untaggedBox, onChange, onPermits } = opts;
  let state = opts.state;
  let ctx = { tags: [], permits: [], year: Number(state.start.slice(0, 4)) };

  // ---- species ------------------------------------------------------------
  const byId = Object.fromEntries(species.map((s) => [s.id, s]));
  const featured = FEATURED.filter((id) => byId[id]);
  const more = species.map((s) => s.id).filter((id) => !featured.includes(id));
  let expanded = more.some((id) => state.species.includes(id));
  const unpublished = (id) => !(byId[id]?.published_seasons > 0) && !state.species.includes(id);

  const speciesBox = document.createElement("div");
  speciesBox.className = "species-box";
  const speciesHint = textEl("p", "", "muted hint");
  speciesHint.id = "f-species-hint";
  let speciesChips = null;
  // Set when the user pressed a 5th species chip; cleared by the next accepted edit.
  let atLimit = false;

  // ---- Match any|all (§4.2): shown only while 2+ species are selected --------
  const matchBox = document.createElement("div");
  matchBox.className = "match-box";
  matchBox.id = "f-match-box";
  const matchLabel = textEl("span", "Match", "l");
  matchLabel.id = "f-match-label";
  const matchChips = chipGroup({
    id: "f-match",
    items: Object.entries(MATCH_LABELS),
    value: state.speciesMode,
    labelledBy: "f-match-label",
    onChange: (v) => onChange({ speciesMode: v }),
  });
  matchBox.append(matchLabel, matchChips);
  function paintMatch() {
    matchBox.hidden = state.species.length < 2;
    matchChips.setValue(state.speciesMode);
  }

  /** Apply one species chip press under the T-27 limits (max 4, never zero). */
  function onSpeciesChip(sel) {
    const cur = state.species;
    const changed = sel.find((id) => !cur.includes(id)) ?? cur.find((id) => !sel.includes(id));
    const next = changed === undefined ? cur : toggleSpecies(cur, changed);
    if (next === cur) {
      atLimit = sel.length > cur.length; // a 5th was pressed, not the last one dropped
      speciesChips.setValue(cur);
      renderSpecies();
      return;
    }
    atLimit = false;
    // The Match control only exists for 2+; dropping back to one returns to `any`.
    onChange({ species: next, tags: null, ...(next.length < 2 ? { speciesMode: "any" } : {}) });
  }

  function renderSpecies() {
    const ids = expanded ? [...featured, ...more] : featured;
    speciesChips = chipGroup({
      id: "f-species",
      items: ids.map((id) => ({
        value: id,
        label: speciesChipLabel(byId[id]),
        title: byId[id].published_seasons
          ? `${byId[id].label}: ${byId[id].published_seasons} published seasons`
          : `${byId[id].label}: no published seasons yet`,
      })),
      multi: true,
      value: state.species,
      labelledBy: "f-species-label",
      disabled: unpublished,
      onChange: onSpeciesChip,
    });
    const sep = document.createElement("span");
    sep.className = "chip-sep";
    sep.setAttribute("aria-hidden", "true");
    speciesChips.insertBefore(sep, speciesChips.children[BIG_GAME_COUNT]);
    if (more.length) {
      const toggle = textEl("button", expanded ? "Fewer ▴" : "More ▾", "chip more");
      toggle.type = "button";
      toggle.id = "f-species-more";
      toggle.setAttribute("aria-expanded", expanded ? "true" : "false");
      toggle.addEventListener("click", () => {
        expanded = !expanded;
        speciesKey = `${state.species.join(",")}|${expanded}|${atLimit}`;
        renderSpecies();
        document.getElementById("f-species-more")?.focus();
      });
      speciesChips.appendChild(toggle);
    }
    const empty = state.species.filter((id) => !(byId[id]?.published_seasons > 0));
    speciesHint.hidden = !atLimit && !empty.length;
    speciesHint.replaceChildren();
    if (atLimit) {
      speciesHint.append(`Up to ${MAX_SPECIES} species at a time — unselect one first.`);
    } else if (empty.length) {
      const link = textEl("a", "upload the regulations", "");
      link.href = "#/regs";
      const names = empty.map((id) => byId[id]?.label || id).join(", ");
      speciesHint.append(`No published ${names} seasons yet: `, link, ".");
    }
    paintMatch();
    speciesBox.replaceChildren(speciesChips, matchBox, speciesHint);
  }

  // ---- my tags ------------------------------------------------------------
  const tagsRow = document.createElement("div");
  tagsRow.className = "tags-row";
  const tagsLabel = textEl("span", "", "l");
  tagsLabel.id = "f-tags-label";

  function renderTags() {
    tagsLabel.textContent = `My tags (${ctx.year})`;
    const held = tagsForSpecies(ctx.tags, state.species);
    const heldIds = held.map((t) => t.id);
    const nodes = [];
    if (held.length) {
      nodes.push(
        chipGroup({
          id: "f-tags",
          items: held.map((t) => [t.id, tagChipLabel(t)]),
          multi: true,
          value: selectedTagIds(state.tags, heldIds),
          labelledBy: "f-tags-label",
          onChange: (sel) => onChange({ tags: tagFilter(sel, heldIds) }),
        }),
      );
    }
    const manage = textEl("a", held.length ? "Manage tags" : "No tags yet → add one", "chip link");
    manage.href = "#/tags";
    manage.id = "f-tags-manage";
    nodes.push(manage);
    tagsRow.replaceChildren(...nodes);
  }

  // ---- weapon -------------------------------------------------------------
  const weapon = chipGroup({
    id: "f-weapon",
    items: Object.keys(WEAPON_LABELS).map((k) => [k, k === "all" ? "All" : WEAPON_LABELS[k]]),
    value: state.weapon,
    labelledBy: "f-weapon-label",
    onChange: (v) => onChange({ weapon: v }),
  });

  // ---- permits ------------------------------------------------------------
  const permits = chipGroup({
    id: "f-permits",
    items: MAP_PERMITS,
    multi: true,
    value: [],
    labelledBy: "f-permits-label",
    onChange: (v) => onPermits(v),
  });
  const permitsLabel = textEl("span", "", "l");
  permitsLabel.id = "f-permits-label";

  // ---- dates --------------------------------------------------------------
  const dates = document.createElement("div");
  dates.className = "dates";
  const in1 = document.createElement("input");
  const in2 = document.createElement("input");
  for (const [inp, name, id] of [
    [in1, "Start date", "f-start"],
    [in2, "End date", "f-end"],
  ]) {
    inp.type = "date";
    inp.id = id;
    inp.setAttribute("aria-label", name);
  }
  dates.append(in1, textEl("span", "→"), in2);
  const presetRanges = () => {
    const p = presets(today, weekendDef);
    return Object.fromEntries(PRESETS.map(([k]) => [k, p[k].map(toISO)]));
  };
  const matchingPreset = () => {
    const r = presetRanges();
    const hit = PRESETS.find(([k]) => r[k][0] === state.start && r[k][1] === state.end);
    return hit ? hit[0] : null;
  };
  const presetChips = chipGroup({
    id: "f-presets",
    items: PRESETS,
    value: matchingPreset(),
    label: "Date presets",
    onChange: (k) => {
      const [start, end] = presetRanges()[k];
      onChange({ start, end });
    },
  });
  in1.addEventListener("change", () => {
    const s = withDate(state, "start", in1.value);
    if (s === state) in1.value = state.start;
    else onChange({ start: s.start, end: s.end });
  });
  in2.addEventListener("change", () => {
    const s = withDate(state, "end", in2.value);
    if (s === state) in2.value = state.end;
    else onChange({ start: s.start, end: s.end });
  });

  if (untaggedBox) {
    untaggedBox.addEventListener("change", () => onChange({ includeUntagged: untaggedBox.checked }));
  }

  const tagsGroup = document.createElement("div");
  tagsGroup.className = "fg";
  tagsGroup.id = "f-tags-group";
  tagsGroup.append(tagsLabel, tagsRow);
  const permitsGroup = document.createElement("div");
  permitsGroup.className = "fg";
  permitsGroup.id = "f-permits-group";
  permitsGroup.append(permitsLabel, permits);

  container.replaceChildren(
    filterGroup("Species", "f-species", speciesBox),
    tagsGroup,
    filterGroup("Weapon", "f-weapon", weapon),
    permitsGroup,
    filterGroup("Dates", "f-dates", dates, presetChips),
  );

  let speciesKey = null;
  let tagsKey = null;
  function update(next, nextCtx) {
    const speciesChanged = String(next.species) !== String(state.species);
    state = next;
    if (nextCtx) ctx = { ...ctx, ...nextCtx };
    if (speciesChanged && state.species.some((id) => more.includes(id))) expanded = true;
    // Rebuild chip rows only when their content changes; otherwise just repaint the
    // pressed state, so a keyboard user's focus stays on the chip they toggled.
    const sk = `${state.species.join(",")}|${expanded}|${atLimit}`;
    if (sk !== speciesKey) {
      speciesKey = sk;
      keepFocus(speciesBox, renderSpecies);
    } else {
      paintMatch();
    }
    const held = tagsForSpecies(ctx.tags, state.species);
    const tk = JSON.stringify([ctx.year, held.map((t) => [t.id, tagChipLabel(t)])]);
    if (tk !== tagsKey) {
      tagsKey = tk;
      keepFocus(tagsRow, renderTags);
    } else {
      const chips = document.getElementById("f-tags");
      if (chips?.setValue) chips.setValue(selectedTagIds(state.tags, held.map((t) => t.id)));
    }
    weapon.setValue(state.weapon);
    permitsLabel.textContent = `Permits (${ctx.year})`;
    permits.setValue(ctx.permits.filter((p) => MAP_PERMITS.some(([k]) => k === p)));
    in1.value = state.start;
    in2.value = state.end;
    presetChips.setValue(matchingPreset());
    if (untaggedBox) untaggedBox.checked = state.includeUntagged;
  }
  update(state);
  return { update };
}
