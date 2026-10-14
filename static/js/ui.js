// Reusable UI components (DESIGN.md §4, T-03). Vanilla DOM, no framework.
//
// Every component builds plain elements styled by css/app.css. Colors never
// come from JS; components only set classes and ARIA attributes.

const toKey = (v) => String(v);

/** Normalize chip items: [[value, label]], [{value, label}], or ["value"]. */
function normItems(items) {
  return items.map((it) => {
    if (Array.isArray(it)) return { value: toKey(it[0]), label: String(it[1] ?? it[0]) };
    if (it !== null && typeof it === "object") {
      return { value: toKey(it.value), label: String(it.label ?? it.value), title: it.title };
    }
    return { value: toKey(it), label: String(it) };
  });
}

/** Normalize `disabled`: a predicate, an iterable of values, or a boolean. */
function disabledFn(disabled) {
  if (typeof disabled === "function") return (v) => Boolean(disabled(v));
  if (disabled === true) return () => true;
  if (disabled && typeof disabled[Symbol.iterator] === "function") {
    const set = new Set([...disabled].map(toKey));
    return (v) => set.has(v);
  }
  return () => false;
}

/**
 * A row of toggle chips. Each chip is a <button aria-pressed>.
 *
 * - `id`: id of an existing container to render into. A new <div> with that
 *   id is created when none exists (the caller then inserts it).
 * - `items`: [[value, label]] | [{value, label, title?}] | [value].
 * - `multi`: false = exactly one value (radio-like), true = any subset.
 * - `value`: the selected value, or for `multi` an array/Set of values.
 * - `onChange(next)`: called after a click with the new value (a string, or an
 *   array of strings in item order for `multi`).
 * - `disabled`: predicate(value), iterable of values, or `true` for all.
 * - `label`: accessible name for the group (else use `labelledBy`).
 *
 * Returns the container, with `getValue()` and `setValue(v)` attached.
 */
export function chipGroup({
  id,
  items,
  multi = false,
  value,
  onChange,
  disabled,
  label,
  labelledBy,
}) {
  let el = id ? document.getElementById(id) : null;
  if (!el) {
    el = document.createElement("div");
    if (id) el.id = id;
  }
  el.className = "chips";
  el.setAttribute("role", "group");
  if (label) el.setAttribute("aria-label", label);
  if (labelledBy) el.setAttribute("aria-labelledby", labelledBy);

  const list = normItems(items);
  const isDisabled = disabledFn(disabled);
  let selected = multi
    ? new Set([...(value ?? [])].map(toKey))
    : value === undefined || value === null
      ? null
      : toKey(value);

  const current = () => (multi ? list.map((i) => i.value).filter((v) => selected.has(v)) : selected);

  const buttons = list.map((item) => {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "chip";
    b.textContent = item.label;
    b.dataset.value = item.value;
    if (item.title) b.title = item.title;
    b.disabled = isDisabled(item.value);
    b.addEventListener("click", () => {
      if (b.disabled) return;
      if (multi) {
        if (selected.has(item.value)) selected.delete(item.value);
        else selected.add(item.value);
      } else {
        if (selected === item.value) return; // radio-like: re-clicking is a no-op
        selected = item.value;
      }
      paint();
      if (onChange) onChange(current());
    });
    return b;
  });

  function paint() {
    for (const b of buttons) {
      const v = b.dataset.value;
      const on = multi ? selected.has(v) : selected === v;
      b.setAttribute("aria-pressed", on ? "true" : "false");
    }
  }

  el.replaceChildren(...buttons);
  paint();

  el.getValue = current;
  el.setValue = (v) => {
    selected = multi ? new Set([...(v ?? [])].map(toKey)) : v == null ? null : toKey(v);
    paint();
  };
  return el;
}

/**
 * A card: <section class="card"> with an <h2> title. Optional children are
 * appended after the title. Returns the section.
 */
export function card(title, ...children) {
  const section = document.createElement("section");
  section.className = "card";
  if (title) {
    const h = document.createElement("h2");
    h.textContent = title;
    section.appendChild(h);
  }
  for (const c of children) {
    section.appendChild(typeof c === "string" ? textEl("p", c, "placeholder") : c);
  }
  return section;
}

/**
 * A small inline badge. `kind` picks the color class `b-<kind>`: weapons
 * (any, archery, muzzleloader, short-range), status (open, restricted), or
 * levels (ok, warn, error). Unknown kinds fall back to the neutral style.
 */
export function badge(kind, text) {
  const k = String(kind || "neutral")
    .toLowerCase()
    .replace(/[^a-z0-9-]+/g, "-");
  return textEl("span", text ?? kind, `badge b-${k}`);
}

const TOAST_LEVELS = ["info", "success", "warn", "error"];

/**
 * Show a transient message in the bottom-right corner. `level` is one of
 * info | success | warn | error. Errors stay until clicked; the rest leave
 * after `ms` (default 4 s). Returns the toast element.
 */
export function toast(msg, level = "info", { ms = 4000 } = {}) {
  const lvl = TOAST_LEVELS.includes(level) ? level : "info";
  let host = document.getElementById("toasts");
  if (!host) {
    host = document.createElement("div");
    host.id = "toasts";
    host.className = "toasts";
    host.setAttribute("role", "status");
    host.setAttribute("aria-live", "polite");
    document.body.appendChild(host);
  }
  const t = textEl("div", msg, `toast toast-${lvl}`);
  if (lvl === "error") t.setAttribute("role", "alert");
  const remove = () => t.remove();
  t.addEventListener("click", remove);
  host.appendChild(t);
  if (lvl !== "error" && ms > 0) setTimeout(remove, ms);
  return t;
}

/** Create `<tag class=cls>text</tag>`. */
export function textEl(tag, text, cls) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  e.textContent = text;
  return e;
}
