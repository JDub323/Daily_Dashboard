// Toast, modal, KaTeX.

import { $, mount } from "./dom.js";

let toastTimer = null;

export function toast(message, kind = "error") {
  const node = $("#toast");
  node.textContent = message;
  node.className = kind === "info" ? "info" : "";
  node.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => {
    node.hidden = true;
  }, 4000);
}

const DELIMITERS = [
  { left: "$$", right: "$$", display: true },
  { left: "$", right: "$", display: false },
];

/**
 * Render the LaTeX inside `node`.
 *
 * KaTeX is vendored and loaded with `defer`, so on a fast boot a panel can
 * render before it exists. Silently doing nothing would leave raw `$…$` on
 * screen with no explanation, so wait for it instead.
 */
export function typeset(node) {
  if (window.renderMathInElement) {
    window.renderMathInElement(node, { delimiters: DELIMITERS, throwOnError: false });
    return;
  }
  window.addEventListener("load", () => typeset(node), { once: true });
}

let onClose = null;

export function openModal(title, build) {
  $("#modal-title").textContent = title;
  const body = $("#modal-body");
  mount(body);
  build(body);
  $("#modal-overlay").hidden = false;
}

export function closeModal() {
  $("#modal-overlay").hidden = true;
  onClose?.();
}

/** Called after any modal closes — the chips re-read state from the server. */
export function onModalClose(fn) {
  onClose = fn;
}

export function initModal() {
  $("#modal-close").addEventListener("click", closeModal);
  $("#modal-overlay").addEventListener("click", (e) => {
    if (e.target.id === "modal-overlay") closeModal();
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !$("#modal-overlay").hidden) closeModal();
  });
}
