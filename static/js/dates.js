// Weekend default and date-overlap math, DESIGN.md §7.3 (T-05).
//
// Twin of planner/dates.py. Both are tested against the same fixture,
// tests/fixtures/dates_cases.json, so any change here must be made there too.
//
// Every Date handled here is a LOCAL-time calendar day pinned to 12:00 noon.
// Noon keeps day arithmetic correct across DST changes (a day is 23 or 25 hours
// long then, which never moves noon onto another calendar day). Ranges are
// inclusive [start, end] pairs, the same convention as the database.

export const WEEKEND_DEFS = ["fri_sun", "sat_sun"];
export const DEFAULT_WEEKEND_DEF = "fri_sun"; // D5

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const DAY_MS = 86400000;
const FIRST_DAY = { fri_sun: 5, sat_sun: 6 }; // Date#getDay(): Sun=0 … Sat=6

const pad = (n) => String(n).padStart(2, "0");

/** Local noon on the given calendar day. */
function noon(y, m0, d) {
  return new Date(y, m0, d, 12, 0, 0, 0);
}

/** Today's local date at noon (the browser's local date is the source of truth, §7.3). */
export function localToday(now = new Date()) {
  return noon(now.getFullYear(), now.getMonth(), now.getDate());
}

/** Parse "YYYY-MM-DD" to a local-noon Date. Throws on anything else. */
export function parseISO(s) {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(s);
  if (!m) throw new Error(`not an ISO date: ${JSON.stringify(s)}`);
  const d = noon(+m[1], +m[2] - 1, +m[3]);
  if (d.getMonth() !== +m[2] - 1 || d.getDate() !== +m[3]) {
    throw new Error(`not a real date: ${s}`);
  }
  return d;
}
export { parseISO as parse_iso };

export function toISO(d) {
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

/** "Oct 17". Locale-independent, matching fmt_short in dates.py. */
export function fmtShort(d) {
  return `${MONTHS[d.getMonth()]} ${d.getDate()}`;
}

export function addDays(d, n) {
  return noon(d.getFullYear(), d.getMonth(), d.getDate() + n);
}

/** Number of days in the inclusive range [a, b]; 0 if b is before a. */
export function days(a, b) {
  return Math.max(0, Math.round((b - a) / DAY_MS) + 1);
}

/** Number of days shared by the inclusive ranges [a1, a2] and [b1, b2]. */
export function overlap(a1, a2, b1, b2) {
  const start = a1 > b1 ? a1 : b1;
  const end = a2 < b2 ? a2 : b2;
  return days(start, end);
}

/**
 * The coming weekend per §7.3, as [start, end].
 * fri_sun: Fri/Sat/Sun -> [today, Sunday]; otherwise next Fri–Sun.
 * sat_sun: Sat -> [today, Sunday]; Sun -> [today, today]; otherwise next Sat–Sun.
 */
export function defaultRange(today, weekendDef = DEFAULT_WEEKEND_DEF) {
  if (!(weekendDef in FIRST_DAY)) {
    throw new Error(`unknown weekend_def: ${JSON.stringify(weekendDef)}`);
  }
  const first = FIRST_DAY[weekendDef];
  const wd = today.getDay();
  // Days until Sunday, treating Sunday as the last day of the week.
  const toSunday = wd === 0 ? 0 : 7 - wd;
  const inWeekend = wd === 0 || wd >= first;
  if (inWeekend) return [addDays(today, 0), addDays(today, toSunday)];
  const start = addDays(today, first - wd);
  return [start, addDays(today, toSunday)];
}
export { defaultRange as default_range };

// --- presets (§7.3) ---------------------------------------------------------------

export function thisWeekend(today, weekendDef = DEFAULT_WEEKEND_DEF) {
  return defaultRange(today, weekendDef);
}

/**
 * The full weekend after this one (this weekend's Sunday + 7 days). Equal to
 * "this weekend + 7 days" except when today is already inside the weekend: on a
 * Saturday with fri_sun it returns next Fri–Sun, not just next Sat–Sun.
 */
export function nextWeekend(today, weekendDef = DEFAULT_WEEKEND_DEF) {
  const [, sunday] = defaultRange(today, weekendDef);
  return defaultRange(addDays(sunday, 1), weekendDef);
}

export function todayRange(today) {
  return [addDays(today, 0), addDays(today, 0)];
}

/** Today plus the following six days (7 days inclusive). */
export function next7(today) {
  return [addDays(today, 0), addDays(today, 6)];
}

/**
 * [d, d] for the earliest season start on or after today, or null.
 * seasons: objects with `start` (API shape, §6) or `start_date` (DB row), as an
 * ISO string or a Date. Pass only the seasons for the user's tags.
 */
export function openingDay(seasons, today) {
  let first = null;
  for (const s of seasons ?? []) {
    const raw = s.start ?? s.start_date;
    const d = raw instanceof Date ? addDays(raw, 0) : parseISO(raw);
    if (d >= today && (first === null || d < first)) first = d;
  }
  return first === null ? null : [first, addDays(first, 0)];
}

/** All five §7.3 presets, keyed the same way as presets() in dates.py. */
export function presets(today, weekendDef = DEFAULT_WEEKEND_DEF, seasons = []) {
  return {
    thisWeekend: thisWeekend(today, weekendDef),
    nextWeekend: nextWeekend(today, weekendDef),
    today: todayRange(today),
    next7: next7(today),
    openingDay: openingDay(seasons, today),
  };
}
