// Where Can I Hunt filter state (T-07, DESIGN.md §4.2, §6).
//
// Pure and DOM-free so node can test it (state.test.mjs). It owns:
// - the filter state shape and its defaults (profile species + the coming weekend, §7.3),
// - the URL query round-trip, so `#/map?species=elk&start=…` is a shareable,
//   bookmarkable view and a reload restores it,
// - the `/api/availability` query path,
// - a 150 ms debounced fetcher that drops stale responses.

import { defaultRange, localToday, parseISO, toISO } from "./dates.js";

export const WEAPONS = ["all", "rifle", "archery", "muzzleloader", "short_range"];
export const DEBOUNCE_MS = 150;
// T-27: the species filter is a list of up to 4 ids combined by `speciesMode`.
export const MAX_SPECIES = 4;
export const SPECIES_MODES = ["any", "all"];

/**
 * The mode a selection uses unless the user says otherwise: picking several species means
 * "where can I hunt all of them", so 2+ species default to `all`. One species behaves the
 * same either way and stays `any`, so its URL and query are unchanged.
 */
export function defaultMode(species) {
  return (species || []).length > 1 ? "all" : "any";
}

// URL query keys, in the order they're written.
const KEYS = ["species", "mode", "start", "end", "weapon", "tags", "untagged"];
const ISO_RE = /^\d{4}-\d{2}-\d{2}$/;

/** A valid YYYY-MM-DD string, or null. */
export function validISO(s) {
  if (typeof s !== "string" || !ISO_RE.test(s)) return null;
  try {
    return toISO(parseISO(s)) === s ? s : null; // rejects 2026-02-30
  } catch {
    return null;
  }
}

/**
 * The state a fresh load starts from: the profile's species and the coming weekend in the
 * browser's local time (§7.3; the server's `/api/meta` range is only a fallback).
 */
export function defaultState({ profile = {}, today = localToday() } = {}) {
  const [d1, d2] = defaultRange(today, profile.weekend_def || "fri_sun");
  return {
    // A list since T-27; `default_species` stays a single value, so a fresh load is
    // still one species in `any` mode.
    species: [profile.default_species || "deer"],
    speciesMode: "any",
    start: toISO(d1),
    end: toISO(d2),
    weapon: "all",
    tags: null, // null = every unfilled tag I hold ("mine"); else an explicit id list
    includeUntagged: false,
  };
}

/**
 * Parse a species list ("deer,elk") into at most `MAX_SPECIES` unique known ids, in the
 * order written. Returns null when nothing usable is left, so the caller keeps its default.
 */
export function parseSpecies(raw, speciesIds) {
  if (typeof raw !== "string") return null;
  const ids = [...new Set(raw.split(",").map((s) => s.trim()).filter(Boolean))]
    .filter((id) => !speciesIds || speciesIds.includes(id))
    .slice(0, MAX_SPECIES);
  return ids.length ? ids : null;
}

/**
 * Toggle `id` in a species selection: never past `MAX_SPECIES`, never down to none.
 * Returns the same array (identity) when the edit isn't allowed, so callers can revert
 * the chip that was just pressed.
 */
export function toggleSpecies(list, id) {
  if (list.includes(id)) return list.length === 1 ? list : list.filter((x) => x !== id);
  return list.length >= MAX_SPECIES ? list : [...list, id];
}

/** The species a single-species view (tags, the Unit Brief, the CSV name) speaks for. */
export const primarySpecies = (s) => s.species[0];

/** Parse a tag-id list ("3,7") into sorted unique ints; anything malformed → null (= mine). */
function parseTags(raw) {
  if (raw === null || raw === undefined || raw === "mine") return null;
  if (raw === "" || raw === "none") return [];
  const ids = raw.split(",").map((t) => t.trim());
  if (!ids.every((t) => /^\d+$/.test(t))) return null;
  return [...new Set(ids.map(Number))].sort((a, b) => a - b);
}

/**
 * Build a state from a URL query (URLSearchParams or string), falling back to `defaults`
 * for anything missing or invalid. `speciesIds`, when given, limits species to known ids.
 */
export function stateFromQuery(query, defaults, { speciesIds } = {}) {
  const q = typeof query === "string" ? new URLSearchParams(query) : query;
  const s = { ...defaults };
  const sp = parseSpecies(q.get("species"), speciesIds);
  if (sp) s.species = sp;
  const mode = q.get("mode");
  if (mode && SPECIES_MODES.includes(mode)) s.speciesMode = mode;
  else s.speciesMode = defaultMode(s.species);

  const start = validISO(q.get("start"));
  const end = validISO(q.get("end"));
  if (start && end) [s.start, s.end] = start <= end ? [start, end] : [end, start];
  else if (start) [s.start, s.end] = [start, start];
  else if (end) [s.start, s.end] = [end, end];

  const w = q.get("weapon");
  if (w && WEAPONS.includes(w)) s.weapon = w;
  if (q.has("tags")) s.tags = parseTags(q.get("tags"));
  const u = q.get("untagged");
  if (u !== null) s.includeUntagged = u === "1" || u === "true";
  return s;
}

/** The URL query for a state: always the core keys, plus tags/untagged only when set. */
export function toQuery(s) {
  const q = new URLSearchParams();
  q.set("species", s.species.join(","));
  // Only when it bites: the mode is implied by the selection size (one species = `any`,
  // several = `all`), so the URL carries `mode` only when the user overrode that.
  if ((s.speciesMode || "any") !== defaultMode(s.species)) q.set("mode", s.speciesMode);
  q.set("start", s.start);
  q.set("end", s.end);
  q.set("weapon", s.weapon);
  if (s.tags !== null) q.set("tags", s.tags.length ? s.tags.join(",") : "none");
  if (s.includeUntagged) q.set("untagged", "1");
  return q.toString();
}

/** True when the query string carries any filter key (vs. a bare `#/map`). */
export function hasFilterKeys(query) {
  const q = typeof query === "string" ? new URLSearchParams(query) : query;
  return KEYS.some((k) => q.has(k));
}

/** `GET /api/availability` path for a state (§6). */
export function availabilityPath(s) {
  const q = new URLSearchParams();
  q.set("species", s.species.join(","));
  q.set("species_mode", s.speciesMode || "any");
  q.set("start", s.start);
  q.set("end", s.end);
  q.set("weapon", s.weapon);
  q.set("tags", s.tags === null ? "mine" : s.tags.join(","));
  q.set("include_untagged", s.includeUntagged ? "1" : "0");
  return `/api/availability?${q.toString()}`;
}

/** The license years a range touches (a Dec 31 → Jan 1 range touches two, §7.4). */
export function rangeYears(s) {
  const y1 = Number(s.start.slice(0, 4));
  const y2 = Number(s.end.slice(0, 4));
  const out = [];
  for (let y = y1; y <= y2; y++) out.push(y);
  return out;
}

/**
 * Apply a date edit the way the inputs behave: moving one end past the other drags the
 * other end along, so the range never inverts.
 */
export function withDate(s, which, value) {
  const v = validISO(value);
  if (!v) return s;
  if (which === "start") return { ...s, start: v, end: s.end < v ? v : s.end };
  return { ...s, end: v, start: s.start > v ? v : s.start };
}

/** A small observable store. `set(patch)` merges and notifies when anything changed. */
export function createStore(initial) {
  let state = { ...initial };
  const subs = new Set();
  return {
    get: () => state,
    set(patch) {
      const next = { ...state, ...patch };
      const changed = Object.keys(next).some(
        (k) => JSON.stringify(next[k]) !== JSON.stringify(state[k]),
      );
      if (!changed) return false;
      const prev = state;
      state = next;
      for (const fn of subs) fn(state, prev);
      return true;
    },
    subscribe(fn) {
      subs.add(fn);
      return () => subs.delete(fn);
    },
  };
}

/**
 * Debounced, last-wins fetching. `request(path)` is called at most once per quiet window
 * of `ms`; a response is delivered to `onResult` only if no newer request was scheduled
 * in the meantime (so a slow early response can't overwrite a fast later one).
 */
export function createFetcher(request, { ms = DEBOUNCE_MS, onResult, onError, onStart } = {}) {
  let timer = null;
  let seq = 0;
  function run(path) {
    const mine = ++seq;
    if (onStart) onStart(path);
    return Promise.resolve()
      .then(() => request(path))
      .then(
        (data) => {
          if (mine === seq && onResult) onResult(data, path);
        },
        (err) => {
          if (mine === seq && onError) onError(err, path);
        },
      );
  }
  return {
    /** Schedule a fetch after the debounce window (resets the window). */
    schedule(path) {
      if (timer) clearTimeout(timer);
      seq++; // invalidate any request already in flight
      timer = setTimeout(() => {
        timer = null;
        run(path);
      }, ms);
    },
    /** Fetch right away (first load). */
    now(path) {
      if (timer) clearTimeout(timer);
      timer = null;
      return run(path);
    },
    cancel() {
      if (timer) clearTimeout(timer);
      timer = null;
      seq++;
    },
  };
}
