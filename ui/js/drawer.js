// "Read full Article" drawer (side panel on desktop, bottom sheet on mobile) + the shared article cache.
import { api } from "./api.js";
import { $, el, copyText, toast } from "./dom.js";
import { labelFor } from "./render.js";

const cache = new Map(); // ref → Promise<ArticleOut | null>

/** Fetch an Article once per page load; resolves to null on 404 / failure. */
export function getArticle(ref) {
  if (!cache.has(ref)) {
    cache.set(ref, api.article(ref).catch((error) => {
      if (error.code !== "NOT_FOUND") cache.delete(ref); // retry transient failures next time
      return null;
    }));
  }
  return cache.get(ref);
}

export function breadcrumb(article) {
  if (!article) return "";
  if (article.part_no) return `Part ${article.part_no}${article.part_title ? " · " + article.part_title : ""}`;
  if (article.ref === "PREAMBLE") return "The Constitution of India";
  if (article.ref.startsWith("SCH-")) return "Schedule";
  if (article.ref.startsWith("APP-")) return "Appendix";
  return "";
}

/** The first sentence-ish of the text for a card excerpt. */
export function excerptOf(article, max = 260) {
  const text = (article?.text || "").replace(/\s+/g, " ").trim();
  if (text.length <= max) return text;
  const cut = text.slice(0, max);
  return cut.slice(0, Math.max(cut.lastIndexOf(" "), max - 20)) + "…";
}

/** Neighbouring ref for prev/next: numbered Articles and Schedules only. */
function step(ref, delta) {
  const sch = ref.match(/^SCH-(\d+)$/);
  if (sch) {
    const n = Number(sch[1]) + delta;
    return n >= 1 && n <= 12 ? `SCH-${n}` : null;
  }
  const art = ref.match(/^(\d+)([A-Z]*)$/);
  if (!art) return null;
  const n = Number(art[1]);
  const next = delta > 0 ? n + 1 : art[2] ? n : n - 1;
  return next >= 1 && next <= 395 ? String(next) : null;
}

let current = null;
let onAsk = () => {};
let returnFocus = null;

function renderBody(article) {
  const body = $("#drawer-body");
  body.classList.remove("loading");
  body.replaceChildren();
  if (article.is_omitted) {
    body.append(el("p", { class: "omitted" }, "This provision has been omitted from the Constitution. The text below is what remains in the official edition."));
  }
  const lines = article.text.split(/\n+/).map((l) => l.trim()).filter(Boolean);
  lines.forEach((line, i) => {
    const clause = /^\[?\(([0-9]+[A-Z]?|[a-z]{1,4})\)/.exec(line);
    const cls = clause ? (/^\d/.test(clause[1]) ? "clause" : "clause sub") : i === 0 ? "drop" : "";
    body.append(el("p", { class: cls || undefined }, line));
  });
  body.scrollTop = 0;
}

async function show(ref) {
  current = ref;
  const label = labelFor(ref);
  $("#drawer-badge").textContent = label;
  $("#drawer-title").textContent = label;
  $("#drawer-crumb").textContent = "";
  const body = $("#drawer-body");
  body.classList.add("loading");
  body.textContent = "Turning to the page…";
  $("#drawer-prev").disabled = !step(ref, -1);
  $("#drawer-next").disabled = !step(ref, 1);

  const article = await getArticle(ref);
  if (current !== ref) return; // the user moved on
  if (!article) {
    body.textContent = `${label} isn't in this edition of the text.`;
    return;
  }
  $("#drawer-title").textContent = article.title || label;
  $("#drawer-crumb").textContent = breadcrumb(article);
  renderBody(article);
}

async function move(delta) {
  let ref = current;
  for (let tries = 0; tries < 4; tries++) {
    ref = ref && step(ref, delta);
    if (!ref) return;
    if (await getArticle(ref)) return show(ref);
  }
  toast("No more Articles in that direction");
}

export function openArticle(ref) {
  const drawer = $("#drawer");
  if (drawer.hidden) {
    returnFocus = document.activeElement;
    drawer.hidden = false;
    drawer.classList.remove("closing");
    $("#scrim").hidden = false;
    document.body.style.overflow = "hidden";
  }
  show(ref);
  $("#drawer-close").focus();
}

export function closeArticle() {
  const drawer = $("#drawer");
  if (drawer.hidden) return;
  drawer.classList.add("closing");
  $("#scrim").hidden = true;
  document.body.style.overflow = "";
  setTimeout(() => {
    drawer.hidden = true;
    drawer.classList.remove("closing");
  }, 200);
  current = null;
  returnFocus?.focus?.();
}

function trapFocus(event) {
  if (event.key !== "Tab") return;
  const focusables = [...$("#drawer").querySelectorAll("button:not([disabled]), [tabindex='0']")];
  const first = focusables[0];
  const last = focusables[focusables.length - 1];
  if (event.shiftKey && document.activeElement === first) {
    last.focus();
    event.preventDefault();
  } else if (!event.shiftKey && document.activeElement === last) {
    first.focus();
    event.preventDefault();
  }
}

/** `ask(label)` is called by "Ask about this". */
export function initDrawer({ ask }) {
  onAsk = ask;
  $("#drawer-close").addEventListener("click", closeArticle);
  $("#scrim").addEventListener("click", closeArticle);
  $("#drawer-prev").addEventListener("click", () => move(-1));
  $("#drawer-next").addEventListener("click", () => move(1));
  $("#drawer-copy").addEventListener("click", async () => {
    const article = current && (await getArticle(current));
    if (article) copyText(`${labelFor(article.ref)}${article.title ? " — " + article.title : ""}\n\n${article.text}`);
  });
  $("#drawer-ask").addEventListener("click", async () => {
    const ref = current;
    const article = ref && (await getArticle(ref));
    closeArticle();
    if (ref) onAsk(`Explain ${labelFor(ref)}${article?.title ? ` (${article.title})` : ""} in simple words.`);
  });
  $("#drawer").addEventListener("keydown", (event) => {
    if (event.key === "Escape") closeArticle();
    trapFocus(event);
  });
  // Any citation pill or card button anywhere opens the drawer.
  document.addEventListener("click", (event) => {
    const trigger = event.target.closest("[data-ref]");
    if (trigger && !trigger.closest("#drawer")) openArticle(trigger.dataset.ref);
  });
}
