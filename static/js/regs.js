// Regulations tab (T-15b, DESIGN.md §8.4 without the review screen).
//
// Owns `#panel-regs`: a drag-and-drop upload card that posts to POST /api/documents
// (§6) and, below it, the documents table grouped by license year. `#/regs/<docId>`
// renders the document header and mounts T-15c's review screen (review.js) under it.
//
// Every decision about *what* is shown is a pure function — `uploadView`,
// `groupByYear`, `statusBadge`, `docCounts`, `regsRoute` — so regs.test.mjs covers
// the four upload states and the grouping without a browser. The DOM half is
// verified in real Chrome in the T-15b PR.

import { getJSON, postFile } from "./api.js";
import { createReviewScreen } from "./review.js";
import { badge, card, textEl } from "./ui.js";

/** Where the documents live (§6). */
export const DOCUMENTS_URL = "/api/documents";

/** `source_documents.status` (§5) → the label the table shows. */
export const STATUS_LABELS = {
  uploaded: "Uploaded",
  parsing: "Parsing",
  needs_review: "Needs review",
  published: "Published",
  superseded: "Superseded",
  failed: "Failed",
};

/** `source_documents.kind` (§5, `planner/importer/classify.py`) → a human label. */
export const KIND_LABELS = {
  big_game: "Big Game",
  upland: "Upland · Turkey · Furbearer",
  moose_sheep_goat: "Moose · Sheep · Goat",
  migratory: "Migratory Birds",
  supplemental: "Supplemental",
  manual: "Manual entry",
};

/**
 * The badge for a document status: `{kind, className, label}`. The class is
 * `badge b-<kind>`; colors for those classes live in css/app.css.
 */
export function statusBadge(status) {
  const key = String(status ?? "").toLowerCase();
  const known = Object.hasOwn(STATUS_LABELS, key);
  const kind = known ? key.replace(/_/g, "-") : "unknown";
  return { kind, className: `badge b-${kind}`, label: known ? STATUS_LABELS[key] : "Unknown" };
}

/** A kind label, falling back to the raw value so a new kind still reads. */
export function kindLabel(kind) {
  const key = String(kind ?? "");
  return Object.hasOwn(KIND_LABELS, key) ? KIND_LABELS[key] : key || "—";
}

/** "2026" for a single-year brochure, "2026–27" when it spans two. */
export function yearLabel(doc) {
  const year = Number(doc?.license_year) || 0;
  const end = Number(doc?.year_end) || 0;
  return end && end !== year ? `${year}–${String(end).slice(-2)}` : String(year);
}

/** First 12 hex chars of a sha256 — enough to recognise a file, short enough to read. */
export function shortSha(sha) {
  const s = String(sha ?? "");
  return s ? s.slice(0, 12) : "";
}

/** "125 pages · 469 rows · 469 staged" for one document row. */
export function docCounts(doc) {
  const seasons = doc?.seasons || {};
  const rows = Object.values(seasons).reduce((n, v) => n + (Number(v) || 0), 0);
  const parts = [`${Number(doc?.pages) || 0} pages`, `${rows} rows`];
  const staged = Number(seasons.staged) || 0;
  if (staged) parts.push(`${staged} staged`);
  return parts.join(" · ");
}

/**
 * Documents grouped by license year, newest year first; inside a year the
 * highest-priority document wins (a supplemental overrides its base, §8.5), then
 * the newest id.
 */
export function groupByYear(documents) {
  const groups = new Map();
  for (const doc of documents || []) {
    const year = Number(doc?.license_year) || 0;
    if (!groups.has(year)) groups.set(year, []);
    groups.get(year).push(doc);
  }
  return [...groups.entries()]
    .sort((a, b) => b[0] - a[0])
    .map(([year, docs]) => ({
      year,
      label: `${year} license year`,
      documents: docs
        .slice()
        .sort((a, b) => (b.priority || 0) - (a.priority || 0) || (b.id || 0) - (a.id || 0)),
    }));
}

/** `#/regs` → the list, `#/regs/<docId>` → one document. Anything else is the list. */
export function regsRoute(rest) {
  const raw = (rest || [])[0];
  const id = Number.parseInt(raw, 10);
  return Number.isInteger(id) && id > 0 ? { view: "document", docId: id } : { view: "list" };
}

// ---- the upload card's four states ----------------------------------------
/** The card before anything is dropped. */
export const UPLOAD_IDLE = { status: "idle" };

const IDLE_MESSAGE =
  "…or choose a file below. The import runs on this machine; the Big Game brochure takes about 35 seconds.";

/**
 * The view model for the upload card. `state` is one of
 * `{status:"idle"}` · `{status:"uploading", filename, sha?}` ·
 * `{status:"success", sha, document, import}` ·
 * `{status:"error", filename, code, message, detail}`.
 *
 * Returns `{state, busy, tone, disabled, title, message, link}` — `tone` picks the
 * class (info | success | warn | error), `link` is `{href, text}` or null.
 */
export function uploadView(state) {
  const s = state || UPLOAD_IDLE;
  if (s.status === "uploading") {
    return {
      state: "uploading",
      busy: true,
      tone: "info",
      disabled: true,
      title: `Importing ${s.filename || "the PDF"} …`,
      message: s.sha
        ? `sha256 ${shortSha(s.sha)} · parsing and staging the rows takes about 35 seconds — keep this tab open.`
        : "Reading the file …",
      link: null,
    };
  }
  if (s.status === "success") {
    const doc = s.document || {};
    const imp = s.import || {};
    const failed = (imp.status || doc.status) === "failed";
    const sha = shortSha(doc.sha256 || s.sha);
    const sections = (imp.sections || []).length ? ` · ${imp.sections.join(", ")}` : "";
    const counts =
      `${imp.pages ?? doc.pages ?? 0} pages · ${imp.parsed ?? 0} rows parsed · ` +
      `${imp.staged ?? 0} staged${sections}`;
    return {
      state: "success",
      busy: false,
      tone: failed ? "error" : "success",
      disabled: false,
      title: failed
        ? `${doc.title || "That PDF"} could not be imported`
        : `Imported ${doc.title || "the PDF"}`,
      message: failed
        ? imp.error || doc.error || "the importer rejected this document"
        : `${sha ? `sha256 ${sha} · ` : ""}${counts}`,
      link: doc.id
        ? {
            href: `#/regs/${doc.id}`,
            text: failed ? "Open the document" : "Review the staged rows",
          }
        : null,
    };
  }
  if (s.status === "error") {
    const duplicate = s.code === "duplicate_document";
    const detail = s.detail || {};
    const existing = Number(detail.document_id) || 0;
    const sha = shortSha(detail.sha256);
    return {
      state: "error",
      busy: false,
      tone: duplicate ? "warn" : "error",
      disabled: false,
      title: duplicate ? "Already uploaded" : "Upload failed",
      message: duplicate
        ? `${s.filename ? `${s.filename} ` : "That PDF "}is already imported${sha ? ` (sha256 ${sha})` : ""}.`
        : s.message || "the upload failed",
      link:
        duplicate && existing
          ? { href: `#/regs/${existing}`, text: "Open the existing document" }
          : null,
    };
  }
  return {
    state: "idle",
    busy: false,
    tone: "info",
    disabled: false,
    title: "Drop a regulation PDF here",
    message: IDLE_MESSAGE,
    link: null,
  };
}

/** sha256 of a File as hex, or null where WebCrypto isn't available. */
export async function sha256Hex(file) {
  try {
    const buf = await file.arrayBuffer();
    const digest = await globalThis.crypto.subtle.digest("SHA-256", buf);
    return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, "0")).join("");
  } catch {
    return null;
  }
}

/** POST the PDF as multipart/form-data; throws ApiError like the rest of api.js. */
export function postDocument(file) {
  return postFile(DOCUMENTS_URL, file);
}

// ---- DOM ------------------------------------------------------------------
/**
 * The upload card. Drag-and-drop and the file input both call `submit(file)`, so
 * there is exactly one upload path. Options: `upload(file)` (injectable for tests),
 * `onUploaded(body)` after a successful POST.
 *
 * Returns the `<section>` with `{submit, getState, setState}` attached.
 */
export function createUploadCard({ upload = postDocument, onUploaded } = {}) {
  const zone = document.createElement("div");
  zone.className = "dropzone";
  zone.id = "regs-dropzone";

  const status = document.createElement("div");
  status.className = "dz-status";
  status.id = "regs-upload-status";
  status.setAttribute("role", "status");
  status.setAttribute("aria-live", "polite");
  const title = textEl("p", "", "dz-title");
  const message = textEl("p", "", "dz-msg");
  const link = document.createElement("a");
  link.className = "dz-link";
  link.hidden = true;
  status.append(title, message, link);

  const field = document.createElement("div");
  field.className = "field";
  const label = document.createElement("label");
  label.textContent = "Regulation PDF";
  label.htmlFor = "regs-file";
  const input = document.createElement("input");
  input.type = "file";
  input.id = "regs-file";
  input.accept = "application/pdf,.pdf";
  input.setAttribute("aria-describedby", status.id);
  field.append(label, input);

  zone.append(status, field);
  const el = card("Upload regulations", zone);
  el.id = "regs-upload";

  let state = UPLOAD_IDLE;
  let busy = false;

  function paint() {
    const view = uploadView(state);
    zone.dataset.state = view.state;
    zone.dataset.tone = view.tone;
    if (view.busy) zone.setAttribute("aria-busy", "true");
    else zone.removeAttribute("aria-busy");
    title.textContent = view.title;
    message.textContent = view.message;
    if (view.link) {
      link.href = view.link.href;
      link.textContent = view.link.text;
      link.hidden = false;
    } else {
      link.hidden = true;
      link.textContent = "";
    }
    input.disabled = view.disabled;
  }

  function setState(next) {
    state = next || UPLOAD_IDLE;
    paint();
  }

  /** The one upload path: the file picker and a drop both land here. */
  async function submit(file) {
    if (!file || busy) return null;
    busy = true;
    setState({ status: "uploading", filename: file.name });
    const sha = await sha256Hex(file);
    if (busy && sha) setState({ status: "uploading", filename: file.name, sha });
    try {
      const body = (await upload(file)) || {};
      setState({
        status: "success",
        sha,
        document: body.document || {},
        import: body.import || {},
      });
      if (onUploaded) onUploaded(body);
      return body;
    } catch (err) {
      setState({
        status: "error",
        filename: file.name,
        code: err && err.code,
        message: err && err.message,
        detail: (err && err.detail) || null,
      });
      return null;
    } finally {
      busy = false;
      // Let the same file be picked again after a failure.
      try {
        input.value = "";
      } catch {
        // some browsers refuse to clear a file input: harmless
      }
    }
  }

  // Both entry points — the file picker and a drop — call `submit`, so there is
  // one upload path. `pending` exposes the in-flight promise for tests.
  let pending = Promise.resolve(null);
  const start = (file) => {
    pending = submit(file);
    return pending;
  };

  input.addEventListener("change", () => start((input.files || [])[0]));
  for (const type of ["dragenter", "dragover"]) {
    zone.addEventListener(type, (e) => {
      e.preventDefault();
      zone.dataset.over = "1";
    });
  }
  for (const type of ["dragleave", "dragend"]) {
    zone.addEventListener(type, () => delete zone.dataset.over);
  }
  zone.addEventListener("drop", (e) => {
    e.preventDefault();
    delete zone.dataset.over;
    const files = (e.dataTransfer && e.dataTransfer.files) || [];
    start(files[0]);
  });

  paint();
  el.submit = start;
  el.pending = () => pending;
  el.getState = () => state;
  el.setState = setState;
  return el;
}

/** An `<a>` to a document's route. */
function docLink(doc) {
  const a = document.createElement("a");
  a.href = `#/regs/${doc.id}`;
  a.textContent = doc.title || doc.filename || `Document ${doc.id}`;
  return a;
}

/** `2026-10-15T18:12:02` → `2026-10-15`. */
export function uploadedOn(doc) {
  const raw = String(doc?.imported_at ?? "");
  return raw ? raw.slice(0, 10) : "—";
}

/**
 * Render the documents table into `container`, one group per license year.
 * Returns the container.
 */
export function renderDocuments(container, documents) {
  const groups = groupByYear(documents);
  if (!groups.length) {
    container.replaceChildren(
      textEl("p", "No regulation PDFs yet — upload one above.", "placeholder"),
    );
    return container;
  }
  const sections = groups.map((group) => {
    const section = document.createElement("section");
    section.className = "docs-year";
    const heading = textEl("h3", group.label);
    heading.id = `regs-year-${group.year}`;
    const table = document.createElement("table");
    table.className = "results docs";
    table.setAttribute("aria-labelledby", heading.id);
    const thead = document.createElement("thead");
    const headRow = document.createElement("tr");
    for (const [text, cls] of [
      ["Status", ""],
      ["Document", ""],
      ["Kind", ""],
      ["Year", ""],
      ["Pages / rows", ""],
      ["Uploaded", "date"],
    ]) {
      const th = textEl("th", text, cls || undefined);
      th.setAttribute("scope", "col");
      headRow.appendChild(th);
    }
    thead.appendChild(headRow);
    const tbody = document.createElement("tbody");
    for (const doc of group.documents) {
      const tr = document.createElement("tr");
      tr.dataset.docId = String(doc.id);
      tr.dataset.status = String(doc.status || "");
      const badgeInfo = statusBadge(doc.status);
      const tdStatus = document.createElement("td");
      tdStatus.appendChild(badge(badgeInfo.kind, badgeInfo.label));
      const tdTitle = document.createElement("td");
      tdTitle.appendChild(docLink(doc));
      if (doc.filename && doc.filename !== doc.title) {
        tdTitle.appendChild(textEl("div", doc.filename, "muted"));
      }
      tr.append(
        tdStatus,
        tdTitle,
        textEl("td", kindLabel(doc.kind)),
        textEl("td", yearLabel(doc)),
        textEl("td", docCounts(doc)),
        textEl("td", uploadedOn(doc), "date"),
      );
      tbody.appendChild(tr);
    }
    table.append(thead, tbody);
    section.append(heading, table);
    return section;
  });
  container.replaceChildren(...sections);
  return container;
}

/**
 * The `#/regs/<docId>` detail panel. T-15c's review screen (review.js) owns it; what
 * is known from the document list is shown as a header while it loads, and a document
 * that failed to import never gets a review screen — there is nothing to review.
 */
export function renderDocumentDetail(container, docId, doc, options = {}) {
  const { mount = createReviewScreen } = options;
  const body = [];
  if (doc) {
    const head = document.createElement("p");
    const info = statusBadge(doc.status);
    head.append(
      badge(info.kind, info.label),
      ` ${kindLabel(doc.kind)} · ${yearLabel(doc)} · ${docCounts(doc)}`,
    );
    body.push(head);
    if (doc.sha256) body.push(textEl("p", `sha256 ${shortSha(doc.sha256)}`, "muted"));
  }
  const links = document.createElement("p");
  const pdf = document.createElement("a");
  pdf.href = `${DOCUMENTS_URL}/${docId}/pdf`;
  pdf.target = "_blank";
  pdf.rel = "noopener";
  pdf.textContent = "Open the stored PDF";
  const back = document.createElement("a");
  back.href = "#/regs";
  back.textContent = "← All documents";
  links.append(back, " · ", pdf);
  body.push(links);

  const detail = card(doc ? doc.title || `Document ${docId}` : `Document ${docId}`, ...body);
  detail.id = "regs-detail";
  if (doc && doc.status === "failed") {
    detail.appendChild(
      textEl("p", doc.error || "This import failed, so it has no rows to review.", "err"),
    );
    container.replaceChildren(detail);
    return container;
  }
  const review = mount(docId);
  container.replaceChildren(detail, review.el);
  container.review = review;
  return container;
}

/**
 * Mount the Regulations tab into `panel` (default `#panel-regs`). The DOM is built
 * synchronously; the document list arrives from `GET /api/documents` and sets
 * `body[data-regs-ready]` when it lands. Returns `{el, refresh, applyRoute, upload}`.
 */
export function initRegs(panel = document.getElementById("panel-regs"), options = {}) {
  const { load = () => fetchDocuments(), upload = postDocument, mount } = options;
  const wrap = document.createElement("div");
  wrap.className = "wrap single";

  const uploadCard = createUploadCard({
    upload,
    onUploaded: () => {
      refresh();
    },
  });

  const listBody = document.createElement("div");
  listBody.id = "regs-documents";
  const listCard = card("Regulation documents", listBody);
  listCard.id = "regs-list";

  const detailHost = document.createElement("div");
  detailHost.id = "regs-detail-host";
  detailHost.hidden = true;

  wrap.append(uploadCard, listCard, detailHost);
  panel.replaceChildren(wrap);

  let documents = [];
  let route = { view: "list" };
  //: Which document the detail host currently holds. The review screen owns its own
  //: state (selection, edits, polled gate), so it is mounted once per document and
  //: never rebuilt by a document-list refresh underneath the user.
  let mountedDoc = null;

  function paintRoute() {
    const isDoc = route.view === "document";
    detailHost.hidden = !isDoc;
    listCard.hidden = isDoc;
    if (isDoc) {
      if (mountedDoc === route.docId) return;
      renderDocumentDetail(
        detailHost,
        route.docId,
        documents.find((d) => Number(d.id) === route.docId) || null,
        mount ? { mount } : {},
      );
      mountedDoc = route.docId;
    } else {
      detailHost.replaceChildren();
      mountedDoc = null;
    }
  }

  async function refresh() {
    const body = await load();
    documents = (body && body.documents) || [];
    renderDocuments(listBody, documents);
    paintRoute();
    document.body.dataset.regsReady = "1";
    return documents;
  }

  function applyRoute(rest) {
    route = regsRoute(rest);
    paintRoute();
  }

  renderDocuments(listBody, documents);
  const ready = refresh().catch((err) => {
    listBody.replaceChildren(
      textEl("p", `The document list failed to load: ${err.message || err}`, "err"),
    );
    document.body.dataset.regsReady = "error";
  });

  return { el: wrap, refresh, applyRoute, ready, uploadCard, documents: () => documents };
}

/** `GET /api/documents`. Separate so tests can inject a list without fetch. */
export function fetchDocuments() {
  return getJSON(DOCUMENTS_URL);
}
