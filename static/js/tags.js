// My Tags & Permits tab (T-09, DESIGN.md §4.2 "🎟️ My Tags", §6).
//
// Owns `#panel-tags`: a license-year selector, tag cards with a filled toggle and
// delete, the Add-tag dialog (fields follow the tag type), a permits panel, a profile
// panel (no residency field, D1) and "Copy tags from <year-1>".
//
// Anything that changes the tags dispatches `TAGS_CHANGED` on `window` with the fresh
// list, which is the shared-state signal the map listens to: filling a tag drops it
// from the tags filter and invalidates the availability query (T-07 subscribes here).

import { ApiError, deleteJSON, getJSON, postJSON, putJSON } from "./api.js";
import {
  badge,
  button,
  card,
  field,
  fieldError as showError,
  select,
  textEl,
  toast,
} from "./ui.js";
import { WEEKEND_DEFS, createProfileCard } from "./settings.js";
import { fmtShort, parseISO } from "./dates.js";

export const TAGS_CHANGED = "ihp:tags-changed";
// Permits saved from either side (this tab or the map's permit chips, T-07):
// detail `{year, permits, source}` where source is "tags" or "map".
export const PERMITS_CHANGED = "ihp:permits-changed";

/** Tags that still count for availability: the unfilled ones (§7.2). */
export function activeTags(tags) {
  return (tags || []).filter((t) => !t.filled);
}

/** Which extra fields a tag type needs in the Add-tag dialog. */
export function fieldsFor(tagType) {
  return {
    zone: Boolean(tagType && tagType.requires_zone),
    huntNo: Boolean(tagType && tagType.requires_hunt_no),
  };
}

/** A hunt's units: "Unit 11", "Part of Unit 38", "Units 11, 12 +2" (T-17c). */
export function huntUnits(hunt, max = 3) {
  const units = hunt?.units || [];
  if (!units.length) return "no units yet";
  const shown = units.slice(0, max).map((u) => u.unit);
  const rest = units.length - shown.length;
  const noun = units.length === 1 ? "Unit" : "Units";
  const partial = units.every((u) => u.coverage === "partial");
  return `${partial ? "Part of " : ""}${noun} ${shown.join(", ")}${rest ? ` +${rest}` : ""}`;
}

/** A hunt's date span: "Oct 10 – Nov 3". */
export function huntDates(hunt) {
  if (!hunt?.start || !hunt?.end) return "";
  return `${fmtShort(parseISO(hunt.start))} – ${fmtShort(parseISO(hunt.end))}`;
}

/** The one-line hunt summary a controlled tag card shows: "Deer · Unit 11 · Oct 10 – Nov 3". */
export function huntSummary(hunt) {
  if (!hunt) return "";
  return [hunt.species_label, huntUnits(hunt), huntDates(hunt)].filter(Boolean).join(" · ");
}

/** Short label for a tag: "🦌 Regular deer tag", or "🦌 Controlled no. 1002" (T-17c). */
export function tagTitle(tag) {
  const icon = tag.icon || "🎟️";
  if (tag.hunt) return `${icon} Controlled #${tag.hunt_number}`;
  return `${icon} ${tag.label || tag.tag_type_label}`;
}

/** The identifying detail under the title: zone, hunt, and/or note. */
export function tagDetail(tag) {
  const parts = [];
  if (tag.zone) parts.push(`Zone ${tag.zone}`);
  if (tag.hunt) parts.push(huntSummary(tag.hunt));
  else if (tag.hunt_number) parts.push(`Hunt #${tag.hunt_number}`);
  // `hunt: null` (not a missing key) means the lookup ran and found nothing: either the
  // year's regulations aren't imported yet, or the number isn't one of its hunts.
  if (tag.hunt === null) parts.push(`not a hunt in the ${tag.license_year} regulations yet`);
  if (tag.label && tag.label !== tag.tag_type_label) parts.push(tag.tag_type_label);
  if (tag.notes) parts.push(tag.notes);
  if (tag.filled) parts.push("filled — excluded from the map");
  return parts.join(" · ");
}

/** One tag as a filter chip: "🫎 Elk A tag (Sawtooth)", or the controlled tag plus its hunt number. */
export function tagChipLabel(tag) {
  const icon = tag.icon || "🎟️";
  // A controlled tag reads as its hunt once the lookup knows it: "🦌 Controlled no. 1002".
  if (tag.hunt) return `${icon} Controlled #${tag.hunt_number}`;
  const bits = [`${icon} ${tag.tag_type_label}`];
  if (tag.zone) bits.push(`(${tag.zone})`);
  if (tag.hunt_number) bits.push(`#${tag.hunt_number}`);
  return bits.join(" ");
}

/** The map breadcrumb: the same labels as the chips, so the two can't drift. */
export function tagsSummary(tags) {
  const active = activeTags(tags);
  if (!active.length) return "No tags yet";
  return active.map(tagChipLabel).join(" · ");
}

// ---- small form helpers: field/select/button/showError now live in ui.js ----

// ---- state -----------------------------------------------------------------
const state = {
  year: new Date().getFullYear(),
  tags: [],
  tagTypes: [],
  species: [],
  zones: [],
  years: [],
  permits: [],
  available: [],
  profile: { youth: 0, weekend_def: "fri_sun", default_species: "deer" },
};

function announce(err) {
  toast(err instanceof ApiError ? err.message : String(err), "error");
}

function publish() {
  window.dispatchEvent(
    new CustomEvent(TAGS_CHANGED, { detail: { year: state.year, tags: state.tags } }),
  );
}

async function loadYear(year) {
  const [tags, permits] = await Promise.all([
    getJSON(`/api/tags?year=${year}`),
    getJSON(`/api/permits?year=${year}`),
  ]);
  state.year = tags.year;
  state.tags = tags.tags;
  state.tagTypes = tags.tag_types;
  state.species = tags.species;
  state.zones = tags.zones;
  state.years = tags.years;
  state.permits = permits.permits;
  state.available = permits.available;
}

// ---- tag list ---------------------------------------------------------------
function tagRow(tag, rerender) {
  const li = document.createElement("li");
  li.className = `tagrow${tag.filled ? " is-filled" : ""}`;
  li.dataset.tagId = String(tag.id);

  const main = document.createElement("div");
  main.className = "tmain";
  main.append(textEl("b", tagTitle(tag)), textEl("span", tagDetail(tag), "muted"));

  const filled = button(tag.filled ? "Filled" : "Mark filled", "chip filltoggle", async () => {
    try {
      await putJSON(`/api/tags/${tag.id}`, { filled: !tag.filled });
      await rerender();
      toast(
        tag.filled ? "Tag reopened — it counts again." : "Tag filled — removed from the map.",
        "success",
      );
    } catch (e) {
      announce(e);
    }
  });
  filled.setAttribute("aria-pressed", tag.filled ? "true" : "false");
  filled.dataset.role = "filled";

  const del = button("×", "icon-btn", async () => {
    try {
      await deleteJSON(`/api/tags/${tag.id}`);
      await rerender();
      toast("Tag deleted.", "success");
    } catch (e) {
      announce(e);
    }
  });
  del.dataset.role = "delete";
  del.setAttribute("aria-label", `Delete ${tag.tag_type_label}`);

  li.append(main, filled, del);
  return li;
}

function tagsCard(rerender, openDialog) {
  const list = document.createElement("ul");
  list.className = "taglist";
  list.id = "taglist";
  if (state.tags.length) {
    list.append(...state.tags.map((t) => tagRow(t, rerender)));
  } else {
    const empty = document.createElement("li");
    empty.className = "placeholder";
    empty.id = "tags-empty";
    empty.textContent = `No tags for ${state.year} yet. Add one, or copy last year's.`;
    list.appendChild(empty);
  }
  const add = button("+ Add tag", "chip", openDialog);
  add.id = "add-tag";
  const foot = document.createElement("div");
  foot.className = "cardfoot";
  foot.append(add, textEl("span", `${activeTags(state.tags).length} counting toward the map`, "muted"));
  return card(`My tags (${state.year})`, list, foot);
}

// ---- controlled-hunt lookup (T-17c) -----------------------------------------
const PROMPT = "Type the hunt number printed in the regulations (e.g. 1002).";
const LOOKUP_DELAY = 250; // ms of quiet before the dialog asks the server

/** The PDF page a hunt-area description is printed on (`GET /api/documents/<id>/pdf`). */
function pdfLink(hunt) {
  if (!hunt.description_page) return null;
  const a = textEl("a", `p.${hunt.description_page}`, "pg");
  a.href = `/api/documents/${encodeURIComponent(hunt.document_id)}/pdf?page=${hunt.description_page}#page=${hunt.description_page}`;
  a.target = "_blank";
  a.rel = "noopener";
  return a;
}

/** The preview card: what `GET /api/hunts/<year>/<no>` found, verbatim (§12 T-17c). */
export function huntCard(hunt) {
  const rows = [
    textEl("b", `${hunt.icon || "🎟️"} ${hunt.species_label} · Controlled #${hunt.hunt_number}`),
    textEl("div", `${huntUnits(hunt, 6)} · ${huntDates(hunt)}`),
    textEl(
      "div",
      [
        `${hunt.weapon.replace(/_/g, " ")} weapon`,
        hunt.sex && hunt.sex !== "either" ? hunt.sex : null,
        hunt.hunt_area ? `area ${hunt.hunt_area}` : null,
        hunt.windows.length > 1 ? `${hunt.windows.length} season windows` : null,
      ]
        .filter(Boolean)
        .join(" · "),
      "muted",
    ),
  ];
  if (hunt.description) {
    const p = textEl("p", `${hunt.description} `, "note");
    const link = pdfLink(hunt);
    if (link) p.appendChild(link);
    rows.push(p);
  }
  if (!hunt.published) {
    const line = document.createElement("div");
    line.append(
      badge("warn", hunt.status),
      textEl("span", " this hunt isn't published yet — the map won't use it until it is.", "muted"),
    );
    rows.push(line);
  }
  return rows;
}

/**
 * The live hunt lookup behind the Add-tag dialog's Hunt number field.
 *
 * `fetchHunt(year, number)` is injected so the node twin can drive it without a network;
 * `onState({hunt, error, pending})` is what disables Save. Out-of-order replies are
 * dropped (a slow lookup for "100" must never overwrite the card for "1002").
 */
export function createHuntPreview({ fetchHunt, onState = () => {}, prompt = PROMPT } = {}) {
  const box = document.createElement("div");
  box.className = "huntcard";
  box.id = "hunt-preview";
  let seq = 0;
  const paint = (...kids) => box.replaceChildren(...kids);
  const idle = () => {
    box.dataset.state = "idle";
    paint(textEl("p", prompt, "muted"));
  };
  idle();

  async function lookup(year, number) {
    const n = String(number || "").trim();
    const mine = ++seq;
    if (!n) {
      idle();
      onState({ hunt: null, error: null, pending: false });
      return null;
    }
    box.dataset.state = "loading";
    paint(textEl("p", `Looking up hunt #${n}…`, "muted"));
    onState({ hunt: null, error: null, pending: true });
    let hunt = null;
    let error = null;
    try {
      hunt = (await fetchHunt(year, n)).hunt;
    } catch (e) {
      error = e instanceof ApiError ? e.message : String(e);
    }
    if (mine !== seq) return null; // a later keystroke already won
    box.dataset.state = hunt ? "found" : "missing";
    paint(...(hunt ? huntCard(hunt) : [textEl("p", error, "note")]));
    onState({ hunt, error, pending: false });
    return hunt;
  }

  return { el: box, lookup, reset: idle };
}

// ---- add-tag dialog ---------------------------------------------------------
function addDialog(onSaved) {
  const dlg = document.createElement("dialog");
  dlg.id = "tag-dialog";
  dlg.setAttribute("aria-label", "Add a tag");

  const form = document.createElement("form");
  form.method = "dialog";
  form.className = "tagform";
  form.append(textEl("h2", `Add a tag for ${state.year}`));

  const typeField = field(
    "Tag type",
    select(state.tagTypes.map((t) => [t.id, `${t.icon} ${t.label}`])),
    "tag-type",
  );
  const zoneField = field(
    "Zone",
    select([["", "— pick a zone —"], ...state.zones.map((z) => [z, z])]),
    "tag-zone",
  );
  const huntInput = document.createElement("input");
  huntInput.type = "text";
  huntInput.inputMode = "numeric";
  huntInput.placeholder = "1002";
  const huntField = field("Hunt number", huntInput, "tag-hunt");
  const preview = createHuntPreview({
    fetchHunt: (year, number) =>
      getJSON(`/api/hunts/${year}/${encodeURIComponent(number)}`),
    onState: ({ hunt, pending }) => {
      lookedUp = hunt;
      // §12 T-17c: Save stays disabled for a number this year's regulations don't print
      // (and for one whose rows were all rejected in review — the API 404s both).
      save.disabled = pending || (!huntField.hidden && !hunt);
    },
  });
  huntField.appendChild(preview.el);
  const notesInput = document.createElement("input");
  notesInput.type = "text";
  notesInput.placeholder = "optional";
  const notesField = field("Note", notesInput, "tag-notes");

  const actions = document.createElement("div");
  actions.className = "cardfoot";
  let lookedUp = null; // the hunt the preview found, or null
  let timer = null;
  const save = button("Save tag", "chip", null, { type: "submit" });
  save.id = "tag-save";
  const cancel = button("Cancel", "chip", () => dlg.close());
  cancel.id = "tag-cancel";
  actions.append(save, cancel);
  form.append(typeField, zoneField, huntField, notesField, actions);
  dlg.appendChild(form);

  function syncFields() {
    const tagType = state.tagTypes.find((t) => t.id === typeField.control.value);
    const need = fieldsFor(tagType);
    zoneField.hidden = !need.zone;
    huntField.hidden = !need.huntNo;
    for (const f of [typeField, zoneField, huntField, notesField]) showError(f, "");
    // A controlled tag can't be saved before its hunt is known; every other type can.
    save.disabled = need.huntNo && !lookedUp;
  }
  typeField.control.addEventListener("change", syncFields);
  syncFields();

  huntInput.addEventListener("input", () => {
    showError(huntField, "");
    save.disabled = true;
    clearTimeout(timer);
    const number = huntInput.value;
    timer = setTimeout(() => preview.lookup(state.year, number), LOOKUP_DELAY);
  });

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    for (const f of [typeField, zoneField, huntField, notesField]) showError(f, "");
    try {
      await postJSON("/api/tags", {
        license_year: state.year,
        tag_type_id: typeField.control.value,
        zone: zoneField.hidden ? null : zoneField.control.value,
        hunt_number: huntField.hidden ? null : huntField.control.value,
        notes: notesField.control.value,
      });
      dlg.close();
      await onSaved();
      toast("Tag added.", "success");
    } catch (err) {
      if (!(err instanceof ApiError)) throw err;
      const fields = err.fields;
      const map = {
        tag_type_id: typeField,
        zone: zoneField,
        hunt_number: huntField,
        notes: notesField,
      };
      let shown = false;
      for (const [key, message] of Object.entries(fields)) {
        if (map[key]) {
          showError(map[key], message);
          shown = true;
        }
      }
      if (!shown) announce(err);
    }
  });
  return dlg;
}

// ---- permits + profile ------------------------------------------------------
function permitsCard() {
  const list = document.createElement("div");
  list.className = "checklist";
  list.id = "permits";
  for (const { code, label } of state.available) {
    const line = document.createElement("label");
    line.className = "checkline";
    const box = document.createElement("input");
    box.type = "checkbox";
    box.value = code;
    box.checked = state.permits.includes(code);
    box.addEventListener("change", async () => {
      const wanted = [...list.querySelectorAll("input:checked")].map((i) => i.value);
      try {
        const saved = await putJSON(`/api/permits?year=${state.year}`, { permits: wanted });
        state.permits = saved.permits;
        window.dispatchEvent(
          new CustomEvent(PERMITS_CHANGED, { detail: { ...saved, source: "tags" } }),
        );
        toast("Permits saved.", "success");
      } catch (e) {
        box.checked = !box.checked;
        announce(e);
      }
    });
    line.append(box, textEl("span", label));
    list.appendChild(line);
  }
  return card(
    `Permits (${state.year})`,
    list,
    textEl("p", "Archery and muzzleloader seasons stay hidden until you hold the permit.", "muted"),
  );
}

// The profile panel is the same form the Settings tab shows (T-21); it lives in
// settings.js so the two tabs can never drift apart.
function profileCard() {
  return createProfileCard({
    profile: state.profile,
    species: state.species,
    idPrefix: "profile",
    onSaved: (saved) => {
      state.profile = saved;
    },
  });
}

// ---- tab -------------------------------------------------------------------
function toolbar(rerender) {
  const bar = document.createElement("div");
  bar.className = "tagbar";
  const years = state.years.includes(state.year) ? state.years : [state.year, ...state.years];
  const yearField = field("License year", select(years.map((y) => [y, String(y)]), state.year), "tag-year");
  yearField.control.addEventListener("change", async () => {
    await rerender(Number(yearField.control.value));
  });

  const prev = state.year - 1;
  const copy = button(`Copy tags from ${prev}`, "chip", async () => {
    try {
      const body = await postJSON(`/api/tags/copy?from=${prev}&to=${state.year}`);
      await rerender();
      const skipped = body.skipped || {};
      const extra = skipped.controlled
        ? ` (${skipped.controlled} controlled tag${skipped.controlled === 1 ? "" : "s"} skipped — hunt numbers are reused)`
        : "";
      toast(
        body.copied ? `Copied ${body.copied} tag(s) from ${prev}.${extra}` : `Nothing to copy from ${prev}.${extra}`,
        body.copied ? "success" : "info",
      );
    } catch (e) {
      announce(e);
    }
  });
  copy.id = "copy-tags";
  bar.append(yearField, copy);
  return bar;
}

/** Render (or re-render) the My Tags tab into `#panel-tags`. */
export async function initTags(panel = document.getElementById("panel-tags")) {
  async function rerender(year = state.year) {
    await loadYear(year);
    const wrap = document.createElement("div");
    wrap.className = "wrap";
    const right = document.createElement("div");
    right.className = "stack";
    right.append(permitsCard(), profileCard());
    wrap.append(tagsCard(rerender, openDialog), right);
    panel.replaceChildren(toolbar(rerender), wrap);
    publish();
  }

  function openDialog() {
    const dlg = addDialog(rerender);
    panel.appendChild(dlg);
    dlg.addEventListener("close", () => dlg.remove());
    dlg.showModal();
  }

  // Keep the checkboxes in step with permits toggled from the map's filter bar.
  window.addEventListener(PERMITS_CHANGED, (e) => {
    if (!e.detail || e.detail.source === "tags" || e.detail.year !== state.year) return;
    state.permits = e.detail.permits;
    for (const box of panel.querySelectorAll("#permits input")) {
      box.checked = state.permits.includes(box.value);
    }
  });

  try {
    const { profile } = await getJSON("/api/profile");
    state.profile = profile;
    await rerender(state.year);
  } catch (e) {
    panel.replaceChildren(
      card("My Tags", "Could not reach the planner server. Is `python -m planner` still running?"),
    );
    announce(e);
  }
}
