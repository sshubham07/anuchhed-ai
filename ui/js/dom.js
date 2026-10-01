// Tiny DOM helpers. `el("p", {class: "x", onclick}, "text", child)` — strings become text nodes (never HTML).

export const $ = (selector, root = document) => root.querySelector(selector);

export function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value === undefined || value === null || value === false) continue;
    if (key.startsWith("on") && typeof value === "function") node.addEventListener(key.slice(2), value);
    else if (key === "class") node.className = value;
    else if (key === "html") node.innerHTML = value; // only for output of render.js (escaped first)
    else if (key === "dataset") Object.assign(node.dataset, value);
    else node.setAttribute(key, value === true ? "" : value);
  }
  for (const child of children.flat()) {
    if (child === undefined || child === null || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

export function icon(path, viewBox = "0 0 24 24") {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", viewBox);
  svg.setAttribute("aria-hidden", "true");
  svg.innerHTML = path;
  return svg;
}

export const ICONS = {
  up: '<path d="M7 11v9H4v-9h3Zm0 0 4-7c1.5 0 2.5 1 2.2 2.6L12.6 10H18a2 2 0 0 1 2 2.3l-1.1 6A2 2 0 0 1 17 20H7"/>',
  down: '<path d="M17 13V4h3v9h-3Zm0 0-4 7c-1.5 0-2.5-1-2.2-2.6l.6-3.4H6a2 2 0 0 1-2-2.3l1.1-6A2 2 0 0 1 7 4h10"/>',
  copy: '<rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 8V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h2"/>',
  retry: '<path d="M4 12a8 8 0 1 0 2.4-5.7M4 4v4h4"/>',
};

let toastTimer;
export function toast(message, action) {
  const box = $("#toasts");
  box.replaceChildren();
  const node = el("div", { class: "toast" }, message);
  if (action) node.append(el("button", { class: "btn", type: "button", onclick: () => { node.remove(); action.run(); } }, action.label));
  box.append(node);
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => node.remove(), action ? 9000 : 5000);
}

export async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    toast("Copied");
  } catch {
    toast("Couldn't copy — select the text instead");
  }
}

export const prefersReducedMotion = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches;
