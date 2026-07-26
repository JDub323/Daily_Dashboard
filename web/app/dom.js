// Element construction.
//
// The old front end built every panel from a template string and relied on a
// hand-rolled esc() that covered only & < > " and was applied inconsistently:
// p.statement and p.answer went in raw for KaTeX, and a URL pasted into the
// paper queue was attribute-escaped but never scheme-checked, so `javascript:`
// in that box executed.
//
// Nothing here interpolates into markup. Text becomes a text node and
// attributes are set through setAttribute, so escaping is structural rather
// than something each render has to remember.

const SVG_NS = "http://www.w3.org/2000/svg";

/** el("div.row", {onclick}, "text", childNode, [more, nodes]) */
export function el(spec, props = null, ...children) {
  const [tag, ...classes] = spec.split(".");
  const node = document.createElement(tag || "div");
  if (classes.length) node.className = classes.join(" ");
  apply(node, props);
  append(node, children);
  return node;
}

/** Same, in the SVG namespace — createElement produces unusable HTML elements. */
export function svg(spec, props = null, ...children) {
  const [tag, ...classes] = spec.split(".");
  const node = document.createElementNS(SVG_NS, tag);
  if (classes.length) node.setAttribute("class", classes.join(" "));
  apply(node, props, true);
  append(node, children);
  return node;
}

function apply(node, props, isSvg = false) {
  if (!props) return;
  for (const [key, value] of Object.entries(props)) {
    if (value == null || value === false) continue;
    if (key === "class") {
      isSvg ? node.setAttribute("class", value) : node.classList.add(...String(value).split(" "));
    } else if (key === "text") {
      node.textContent = String(value);
    } else if (key === "dataset") {
      Object.assign(node.dataset, value);
    } else if (key.startsWith("on") && typeof value === "function") {
      node.addEventListener(key.slice(2), value);
    } else if (!isSvg && key in node && key !== "list") {
      node[key] = value; // value, disabled, hidden, checked …
    } else {
      node.setAttribute(key, String(value));
    }
  }
}

function append(node, children) {
  for (const child of children.flat(Infinity)) {
    if (child == null || child === false || child === "") continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
}

/** Replace a node's contents. The only sanctioned way to re-render. */
export function mount(node, ...children) {
  node.replaceChildren();
  append(node, children);
  return node;
}

const SAFE_SCHEMES = new Set(["http:", "https:"]);

/**
 * An external link, or plain text if the URL is not http(s).
 *
 * The server already rejects other schemes at the request models, so this is
 * the second lock: feed items arrive from RSS and arXiv, which this app does
 * not control.
 */
export function link(href, label, props = {}) {
  let url = null;
  try {
    url = new URL(href, document.baseURI);
  } catch {
    return el("span.faint", { text: String(label) });
  }
  if (!SAFE_SCHEMES.has(url.protocol)) return el("span.faint", { text: String(label) });
  return el("a", { href: url.href, target: "_blank", rel: "noopener noreferrer", ...props }, label);
}

export const $ = (sel, root = document) => root.querySelector(sel);
export const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
