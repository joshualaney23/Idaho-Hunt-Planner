// Hash routing for the five tabs (DESIGN.md §4.2, §4.5). Pure, no DOM, so
// node can test it (router.test.mjs).
//
// Routes look like `#/<tab>[/<rest>][?<query>]`, e.g. `#/regs/12` (review
// screen, T-15) or `#/map?species=elk` (shareable filters, T-07).

export const TABS = [
  { id: "map", icon: "🗺️", label: "Where Can I Hunt" },
  { id: "tags", icon: "🎟️", label: "My Tags" },
  { id: "seasons", icon: "📅", label: "Season Table" },
  { id: "regs", icon: "📥", label: "Regulations" },
  { id: "settings", icon: "⚙️", label: "Settings" },
];

export const DEFAULT_TAB = "map";
const IDS = new Set(TABS.map((t) => t.id));

/**
 * Parse a location hash into `{tab, rest, query, known}`.
 * Unknown or empty hashes resolve to the default tab with `known: false`.
 */
export function parseRoute(hash) {
  let h = String(hash ?? "");
  if (h.startsWith("#")) h = h.slice(1);
  const qi = h.indexOf("?");
  const path = qi === -1 ? h : h.slice(0, qi);
  const query = new URLSearchParams(qi === -1 ? "" : h.slice(qi + 1));
  const parts = path.split("/").filter(Boolean);
  const head = (parts[0] || "").toLowerCase();
  if (!IDS.has(head)) return { tab: DEFAULT_TAB, rest: [], query, known: false };
  return { tab: head, rest: parts.slice(1), query, known: true };
}

/** The canonical hash for a tab: `#/<tab>`. */
export function tabHash(tab) {
  return `#/${IDS.has(tab) ? tab : DEFAULT_TAB}`;
}
