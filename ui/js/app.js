// Bootstrap: theme, edition date, drawer, chat, landing, history restore, keyboard shortcuts.
import { api } from "./api.js";
import { ask, initChat, newChat, restore, setStyle } from "./chat.js";
import { $ } from "./dom.js";
import { initDrawer } from "./drawer.js";
import { initLanding } from "./landing.js";
import { store } from "./store.js";

function isDark() {
  const theme = document.documentElement.dataset.theme;
  return theme ? theme === "dark" : window.matchMedia("(prefers-color-scheme: dark)").matches;
}

function initTheme() {
  const button = $("#theme-toggle");
  const label = () => button.setAttribute("aria-label", isDark() ? "Switch to day reading" : "Switch to night reading");
  button.addEventListener("click", () => {
    const next = isDark() ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    store.theme = next;
    label();
  });
  label();
}

async function initEdition() {
  try {
    const meta = await api.meta();
    if (!meta.edition_date) return;
    const date = new Date(meta.edition_date + "T00:00:00");
    const text = date.toLocaleDateString("en-IN", { day: "numeric", month: "long", year: "numeric" });
    $("#edition").textContent = `The Constitution of India, text as on ${text}`;
  } catch { /* footer keeps its default text */ }
}

function initShortcuts() {
  window.addEventListener("keydown", (event) => {
    if (event.key !== "/" || event.metaKey || event.ctrlKey) return;
    const typing = event.target.closest?.("input, textarea, [contenteditable]");
    if (typing || !$("#drawer").hidden) return;
    const target = $("#chat").hidden ? $("#landing-input") : $("#composer-input");
    event.preventDefault();
    target.focus();
  });
  const bar = $(".topbar");
  window.addEventListener("scroll", () => bar.classList.toggle("scrolled", window.scrollY > 4), { passive: true });
}

// The brand link would reload the page and restore the saved session; go to a fresh landing instead.
// Modified clicks (new tab, etc.) keep the plain link.
function initHomeLink() {
  $(".brand").addEventListener("click", (event) => {
    if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    newChat();
  });
}

async function main() {
  initTheme();
  initShortcuts();
  initHomeLink();
  initDrawer({ ask });
  initChat();
  initEdition();
  const restored = await restore();
  initLanding({ ask, setStyle, intro: !restored });
}

main();
