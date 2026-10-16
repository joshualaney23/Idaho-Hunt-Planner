// Settings tab (T-21, DESIGN.md §4.2 "⚙️ Settings", §6) — owns `#panel-settings`.
//
// Three cards:
//   1. **Profile** — youth flag, weekend definition and default species. The same
//      form My Tags shows (`createProfileCard` is exported for tags.js), because
//      §4.2 puts the youth switch on both screens and two copies would drift.
//   2. **Backup & restore** — download a consistent `planner.db` snapshot
//      (`GET /api/backup`) and upload one back (`POST /api/restore`). Restoring
//      replaces everything, so it is confirmed and the server keeps the replaced
//      DB as `planner.db.bak-<timestamp>`.
//   3. **Danger zone** — "Reset all data" behind a *typed* confirmation: the button
//      stays disabled until the word RESET is typed, so a stray click can't wipe a
//      season's worth of tags.
//
// Everything that decides *what* happens is a pure function (`resetArmed`,
// `restoreSummary`, `resetSummary`, `profilePatch`), and every network call is
// injected, so settings.test.mjs covers the behaviour without a browser.

import { ApiError, getJSON, postFile, postJSON, putJSON } from "./api.js";
import { button, card, field, select, textEl, toast } from "./ui.js";

/** Weekend definitions offered by the profile form (§7.3; D5 makes Fri–Sun the default). */
export const WEEKEND_DEFS = [
  ["fri_sun", "Fri – Sun (default)"],
  ["sat_sun", "Sat – Sun"],
];

/** The word `POST /api/reset` demands in its body, typed by the user first. */
export const RESET_PHRASE = "RESET";

/** Pure: may the reset button fire? Only on an exact (trimmed) match. */
export function resetArmed(typed) {
  return String(typed ?? "").trim() === RESET_PHRASE;
}

/** Pure: the profile body a form sends — the three fields `PUT /api/profile` accepts. */
export function profilePatch({ youth, weekendDef, species }) {
  return {
    youth: Boolean(youth),
    weekend_def: weekendDef,
    default_species: species,
  };
}

/** Pure: just the filename of a server-side path, for a message the user can act on. */
export function backupName(path) {
  return String(path ?? "").split("/").pop() || "";
}

/** Pure: what to tell the user after a restore. */
export function restoreSummary(result = {}) {
  const migrated =
    result.restored_from && result.restored_from !== result.schema_version
      ? ` (migrated from schema v${result.restored_from} to v${result.schema_version})`
      : "";
  const kept = backupName(result.backup);
  return `Backup restored${migrated}. The database it replaced was kept as ${kept}.`;
}

/** Pure: what to tell the user after a reset. */
export function resetSummary(result = {}) {
  const removed = (result.removed || []).length
    ? ` Stored PDFs and the page cache were deleted (${result.removed.join(", ")}).`
    : "";
  return `All data erased.${removed} The old database was kept as ${backupName(result.backup)}.`;
}

const announce = (err) => toast(err instanceof ApiError ? err.message : String(err), "error");

// ---- profile card (shared with My Tags, T-09) ------------------------------
/**
 * The profile form. `onSaved(profile)` receives the server's copy after each change;
 * `save` is injected only by the tests. `idPrefix` keeps the two mounted copies'
 * element ids unique (`profile-youth` on My Tags, `settings-youth` here).
 */
export function createProfileCard({
  profile = {},
  species = [],
  idPrefix = "profile",
  onSaved,
  save = (body) => putJSON("/api/profile", body),
} = {}) {
  const youth = document.createElement("input");
  youth.type = "checkbox";
  youth.id = `${idPrefix}-youth`;
  youth.checked = Boolean(profile.youth);
  const youthLine = document.createElement("label");
  youthLine.className = "checkline";
  youthLine.append(youth, textEl("span", "Youth hunter (youth-only hunts count as open)"));

  const weekend = field(
    "Weekend",
    select(WEEKEND_DEFS, profile.weekend_def),
    `${idPrefix}-weekend`,
  );
  const speciesField = field(
    "Default species",
    select(
      species.map((s) => [s.id, `${s.icon} ${s.label}`]),
      profile.default_species,
    ),
    `${idPrefix}-species`,
  );

  async function onChange() {
    try {
      const saved = await save(
        profilePatch({
          youth: youth.checked,
          weekendDef: weekend.control.value,
          species: speciesField.control.value,
        }),
      );
      if (onSaved) onSaved(saved.profile);
      toast("Profile saved.", "success");
    } catch (e) {
      announce(e);
    }
  }
  for (const el of [youth, weekend.control, speciesField.control]) {
    el.addEventListener("change", onChange);
  }

  const el = card(
    "Profile",
    youthLine,
    weekend,
    speciesField,
    textEl("p", "Residents only — there is no resident/nonresident setting (D1).", "muted"),
  );
  el.controls = { youth, weekend: weekend.control, species: speciesField.control };
  return el;
}

// ---- backup & restore ------------------------------------------------------
/**
 * Download / upload card. Both actions are injected:
 *   `download()` — defaults to navigating to `/api/backup` (the browser saves it);
 *   `restore(file)` — defaults to `POST /api/restore`;
 *   `reload()` — what to do once a restore lands (everything on screen is stale).
 */
export function createBackupCard({
  download = () => {
    window.location.href = "/api/backup";
  },
  restore = (file) => postFile("/api/restore", file),
  reload = () => window.location.reload(),
  confirm = (msg) => window.confirm(msg),
} = {}) {
  const dl = button("⬇ Download backup", "chip", () => download());
  dl.id = "backup-download";

  const picker = document.createElement("input");
  picker.type = "file";
  picker.accept = ".db,.sqlite,.sqlite3,application/vnd.sqlite3";
  const fileField = field("Restore from a backup file", picker, "restore-file");

  const go = button("Restore", "chip danger", async () => {
    const file = (picker.files || [])[0];
    if (!file) {
      toast("Pick a backup file first.", "warn");
      return;
    }
    if (
      !confirm(
        `Replace every tag, permit, document and season with "${file.name}"?` +
          " The current database is kept as planner.db.bak-<timestamp>.",
      )
    ) {
      return;
    }
    go.disabled = true;
    try {
      const result = await restore(file);
      toast(restoreSummary(result), "success", { ms: 8000 });
      reload();
    } catch (e) {
      announce(e);
    } finally {
      go.disabled = false;
    }
  });
  go.id = "backup-restore";

  const row = document.createElement("div");
  row.className = "tagbar";
  row.append(dl, go);

  const el = card(
    "Backup & restore",
    textEl(
      "p",
      "A backup is a single SQLite file holding your tags, permits, profile and every" +
        " parsed season. The regulation PDFs themselves are not inside it — re-upload them" +
        " on the Regulations tab if you move to another machine.",
      "muted",
    ),
    fileField,
    row,
  );
  el.controls = { download: dl, picker, restore: go };
  return el;
}

// ---- danger zone -----------------------------------------------------------
/**
 * "Reset all data" behind a typed confirmation: the button is disabled until the
 * input reads exactly RESET. `reset()` is injected for the tests.
 */
export function createDangerCard({
  reset = () => postJSON("/api/reset", { confirm: RESET_PHRASE }),
  reload = () => window.location.reload(),
} = {}) {
  const input = document.createElement("input");
  input.type = "text";
  input.autocomplete = "off";
  input.placeholder = RESET_PHRASE;
  const typed = field(`Type ${RESET_PHRASE} to enable the button`, input, "reset-confirm");

  const go = button("Reset all data", "chip danger", async () => {
    if (!resetArmed(input.value)) return;
    go.disabled = true;
    try {
      const result = await reset();
      input.value = "";
      toast(resetSummary(result), "success", { ms: 8000 });
      reload();
    } catch (e) {
      announce(e);
      go.disabled = false;
    }
  });
  go.id = "reset-all";
  go.disabled = true;
  input.addEventListener("input", () => {
    go.disabled = !resetArmed(input.value);
  });

  const el = card(
    "Danger zone",
    textEl(
      "p",
      "Erases every tag, permit, uploaded document and parsed season, and re-seeds the" +
        " unit and species reference data. The database you erase is kept as" +
        " planner.db.bak-<timestamp> in your data folder, so this is undoable —" +
        " download a backup first if you'd rather not rely on that.",
      "muted",
    ),
    typed,
    go,
  );
  el.controls = { input, reset: go };
  return el;
}

// ---- tab -------------------------------------------------------------------
/** Render the Settings tab into `#panel-settings`. */
export async function initSettings(panel = document.getElementById("panel-settings")) {
  let profile = {};
  let species = [];
  try {
    const [p, meta] = await Promise.all([getJSON("/api/profile"), getJSON("/api/meta")]);
    profile = p.profile;
    species = meta.species;
  } catch (e) {
    panel.replaceChildren(
      card("Settings", "Could not reach the planner server. Is `python -m planner` still running?"),
    );
    announce(e);
    return null;
  }

  const wrap = document.createElement("div");
  wrap.className = "wrap";
  const right = document.createElement("div");
  right.className = "stack";
  const profileCard = createProfileCard({ profile, species, idPrefix: "settings" });
  const backupCard = createBackupCard();
  const dangerCard = createDangerCard();
  right.append(backupCard, dangerCard);
  wrap.append(profileCard, right);
  panel.replaceChildren(wrap);
  panel.dataset.settingsReady = "1";
  return { profileCard, backupCard, dangerCard };
}
