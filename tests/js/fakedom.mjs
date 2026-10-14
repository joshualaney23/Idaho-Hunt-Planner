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
    const ev = { type, target: this, defaultPrevented: false, ...init };
    ev.preventDefault = () => {
      ev.defaultPrevented = true;
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
  /** Only bare tag selectors ("button") and "#id" are supported. */
  querySelectorAll(sel) {
    const out = [];
    for (const e of this.walk()) {
      if (e === this) continue;
      if (sel.startsWith("#") ? e.id === sel.slice(1) : e.tagName.toLowerCase() === sel) out.push(e);
    }
    return out;
  }
  querySelector(sel) {
    return this.querySelectorAll(sel)[0] || null;
  }
  click() {
    // Browsers never dispatch click on a disabled <button>.
    if (this.tagName === "BUTTON" && this.disabled) return;
    for (const fn of this.listeners.click || []) fn({ type: "click", target: this });
  }
  *walk() {
    yield this;
    for (const c of this.children) yield* c.walk();
  }
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
