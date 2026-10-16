// A tiny DOM stand-in so node can test static/js/ui.js (and T-07's map.js /
// filters.js) without a browser or npm packages. It implements only what those
// modules use; extend it when they grow.
// The real-browser check lives in the T-03 PR (puppeteer + Lighthouse).

// A text node, so `el.append("text", child)` works like the real DOM.
class FakeText {
  constructor(text) {
    this.textContent = String(text);
    this.parentNode = null;
    this.tagName = "#text";
    this.children = [];
  }
  remove() {
    if (!this.parentNode) return;
    const sib = this.parentNode.children;
    sib.splice(sib.indexOf(this), 1);
    this.parentNode = null;
  }
  *walk() {
    yield this;
  }
}

class FakeElement {
  constructor(tag, ns = null) {
    this.tagName = ns ? tag : tag.toUpperCase(); // SVG keeps its case, like browsers
    this.namespaceURI = ns;
    this.style = {};
    this.value = "";
    this.checked = false;
    this.hidden = false;
    this.children = [];
    this.parentNode = null;
    this.attributes = new Map();
    this.dataset = {};
    this.listeners = {};
    this._text = "";
    this.disabled = false;
    this.className = "";
    this.id = "";
    this.type = "";
    this.title = "";
  }
  get childElementCount() {
    return this.children.length;
  }
  get textContent() {
    return this._text + this.children.map((c) => c.textContent).join("");
  }
  set textContent(v) {
    this.children = [];
    this._text = String(v);
  }
  setAttribute(k, v) {
    this.attributes.set(k, String(v));
  }
  getAttribute(k) {
    return this.attributes.has(k) ? this.attributes.get(k) : null;
  }
  removeAttribute(k) {
    this.attributes.delete(k);
  }
  // `href` reflects its attribute, like an <a> in a browser (T-15b).
  get href() {
    return this.getAttribute("href") ?? "";
  }
  set href(v) {
    this.setAttribute("href", v);
  }
  hasAttribute(k) {
    return this.attributes.has(k);
  }
  appendChild(c) {
    if (typeof c === "string") c = new FakeText(c);
    if (c.parentNode) c.remove();
    c.parentNode = this;
    this.children.push(c);
    return c;
  }
  append(...cs) {
    for (const c of cs) this.appendChild(c);
  }
  replaceChildren(...cs) {
    for (const c of this.children) c.parentNode = null;
    this.children = [];
    this._text = "";
    this.append(...cs);
  }
  prepend(...cs) {
    for (const c of [...cs].reverse()) this.insertBefore(c, this.children[0] || null);
  }
  insertBefore(c, ref) {
    if (c.parentNode) c.remove();
    const i = ref ? this.children.indexOf(ref) : -1;
    c.parentNode = this;
    if (i === -1) this.children.push(c);
    else this.children.splice(i, 0, c);
    return c;
  }
  remove() {
    if (!this.parentNode) return;
    const sib = this.parentNode.children;
    sib.splice(sib.indexOf(this), 1);
    this.parentNode = null;
  }
  addEventListener(type, fn) {
    (this.listeners[type] ||= []).push(fn);
  }
  /** Fire `type` listeners with a minimal event; returns the event. */
  dispatch(type, init = {}) {
    const ev = { type, target: this, defaultPrevented: false, propagationStopped: false, ...init };
    ev.preventDefault = () => {
      ev.defaultPrevented = true;
    };
    // Real handlers call this on a nested button so the row behind it doesn't react
    // (T-16). There is no bubbling here, so recording the call is enough.
    ev.stopPropagation = () => {
      ev.propagationStopped = true;
    };
    for (const fn of this.listeners[type] || []) fn(ev);
    return ev;
  }
  focus() {
    globalThis.document.activeElement = this;
    this.dispatch("focus");
  }
  contains(other) {
    for (const e of this.walk()) if (e === other) return true;
    return false;
  }
  /** Tag, `#id`, `.class` and `[data-attr]` selectors (no combinators). */
  querySelectorAll(sel) {
    const out = [];
    for (const e of this.walk()) {
      if (e === this) continue;
      if (e.tagName === "#text") continue;
      if (matches(e, sel)) out.push(e);
    }
    return out;
  }
  querySelector(sel) {
    return this.querySelectorAll(sel)[0] || null;
  }
  click() {
    // Browsers never dispatch click on a disabled <button>.
    if (this.tagName === "BUTTON" && this.disabled) return;
    return this.dispatch("click");
  }
  // <dialog> (T-15c's waive and publish modals). `open` reflects the attribute, as in
  // a browser, so a test can assert the modal state without a layout engine.
  showModal() {
    this.open = true;
    this.setAttribute("open", "true");
  }
  close(returnValue) {
    this.open = false;
    this.returnValue = returnValue ?? "";
    this.removeAttribute("open");
    this.dispatch("close");
  }
  *walk() {
    yield this;
    for (const c of this.children) yield* c.walk();
  }
}

/** Match one element against the subset of CSS the twins use. */
function matches(el, sel) {
  const s = sel.trim();
  if (s.startsWith("#")) return el.id === s.slice(1);
  // Compound `tag.class` / `.a.b` selectors (T-16's `tr.season-row`, `input.date-input`).
  if (/^[a-zA-Z]*(\.[\w-]+)+$/.test(s)) {
    const [tag, ...classes] = s.split(".");
    const have = String(el.className || "").split(/\s+/);
    if (tag && el.tagName.toLowerCase() !== tag.toLowerCase()) return false;
    return classes.every((c) => have.includes(c));
  }
  if (s.startsWith("[")) {
    // [data-foo] and [data-foo="bar"]
    const m = /^\[([^\]=]+)(?:=["']?([^\]"']*)["']?)?\]$/.exec(s);
    if (!m) return false;
    const key = m[1].replace(/^data-/, "").replace(/-([a-z])/g, (_, c) => c.toUpperCase());
    const have = m[1].startsWith("data-") ? el.dataset[key] : el.getAttribute(m[1]);
    return m[2] === undefined ? have !== undefined && have !== null : String(have) === m[2];
  }
  return el.tagName.toLowerCase() === s.toLowerCase();
}

export function installFakeDom() {
  const body = new FakeElement("body");
  const document = {
    body,
    activeElement: null,
    createElement: (tag) => new FakeElement(tag),
    createElementNS: (ns, tag) => new FakeElement(tag, ns),
    getElementById: (id) => {
      for (const e of body.walk()) if (e.id === id) return e;
      return null;
    },
  };
  globalThis.document = document;
  return document;
}
