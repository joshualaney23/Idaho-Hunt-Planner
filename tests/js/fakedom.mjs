// A tiny DOM stand-in so node can test static/js/ui.js without a browser or
// npm packages. It implements only what ui.js uses; extend it when ui.js grows.
// The real-browser check lives in the T-03 PR (puppeteer + Lighthouse).

class FakeElement {
  constructor(tag) {
    this.tagName = tag.toUpperCase();
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
    createElement: (tag) => new FakeElement(tag),
    getElementById: (id) => {
      for (const e of body.walk()) if (e.id === id) return e;
      return null;
    },
  };
  globalThis.document = document;
  return document;
}
