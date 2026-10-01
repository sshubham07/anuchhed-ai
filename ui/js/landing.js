// Landing: the Preamble types itself out and becomes the search box; persona chips.
import { $, el, prefersReducedMotion } from "./dom.js";
import { store } from "./store.js";

// The drop cap supplies the "W".
const PREAMBLE = "e, the People of India, having solemnly resolved to constitute India into a Sovereign Socialist Secular Democratic Republic and to secure to all its citizens…";

let typing = null;

function becomeSearchBox({ focus = true } = {}) {
  clearTimeout(typing);
  typing = null;
  $("#typed").textContent = PREAMBLE;
  $("#preamble").classList.add("done");
  $("#scribe").classList.add("asking");
  $("#skip-intro").hidden = true;
  if (focus) setTimeout(() => $("#landing-input").focus({ preventScroll: true }), 350);
}

function typewriter() {
  const target = $("#typed");
  target.textContent = "";
  let i = 0;
  const tick = () => {
    const ch = PREAMBLE[i++];
    target.textContent += ch;
    if (i >= PREAMBLE.length) {
      $("#preamble").classList.add("done");
      typing = setTimeout(becomeSearchBox, 1100);
      return;
    }
    const pause = ch === "," ? 240 : ch === " " ? 18 : 26 + Math.random() * 30;
    typing = setTimeout(tick, pause);
  };
  typing = setTimeout(tick, 500);
}

function skipOnInteraction() {
  const skip = (event) => {
    if (!typing) return;
    if (event.type === "keydown" && ["Shift", "Alt", "Meta", "Control", "Tab"].includes(event.key)) return;
    becomeSearchBox();
  };
  $("#skip-intro").addEventListener("click", () => becomeSearchBox());
  $(".folio").addEventListener("click", skip);
  window.addEventListener("keydown", skip);
}

// ---- Personas ----

let personas = [];

function renderStarters(persona, ask) {
  const box = $("#starters");
  box.replaceChildren(
    ...persona.starters.map((question) =>
      el("button", { type: "button", class: "starter", onclick: () => ask(question) },
        el("span", {}, question), el("span", { class: "arrow", "aria-hidden": "true" }, "→")),
    ),
  );
}

function selectPersona(id, { ask, setStyle, announce = true }) {
  const persona = personas.find((p) => p.id === id) || personas[0];
  if (!persona) return;
  for (const chip of document.querySelectorAll(".persona")) {
    const on = chip.dataset.persona === persona.id;
    chip.setAttribute("aria-checked", String(on));
    chip.tabIndex = on ? 0 : -1;
  }
  renderStarters(persona, ask);
  if (announce) {
    store.persona = persona.id;
    setStyle(persona.style);
  }
}

async function initPersonas({ ask, setStyle }) {
  try {
    const data = await (await fetch("data/personas.json")).json();
    personas = data.personas;
    const box = $("#personas");
    box.replaceChildren(
      ...personas.map((p) =>
        el("button", { type: "button", role: "radio", class: "persona", "aria-checked": "false", dataset: { persona: p.id },
          onclick: () => selectPersona(p.id, { ask, setStyle }) },
          el("span", { class: "emoji", "aria-hidden": "true" }, p.emoji),
          el("span", { class: "p-text" }, el("span", { class: "p-name" }, p.name), el("span", { class: "p-hint" }, p.hint))),
      ),
    );
    // Arrow keys move between chips (radiogroup pattern).
    box.addEventListener("keydown", (event) => {
      const chips = [...box.children];
      const index = chips.indexOf(document.activeElement);
      if (index === -1 || !["ArrowRight", "ArrowLeft", "ArrowDown", "ArrowUp"].includes(event.key)) return;
      const next = chips[(index + (event.key === "ArrowRight" || event.key === "ArrowDown" ? 1 : chips.length - 1)) % chips.length];
      next.focus();
      next.click();
      event.preventDefault();
    });
    // Highlight the remembered (or default) persona without touching the style toggle:
    // only an explicit chip click applies a persona's preset style.
    selectPersona(store.persona || data.default, { ask, setStyle, announce: false });
  } catch {
    $("#personas").hidden = true;
  }
}

/**
 * `ask(question)` sends a question (switches to chat); `setStyle(style)` moves the style toggle.
 * `intro` plays the typewriter (first visit of this page load); otherwise the search box shows at once.
 */
export function initLanding({ ask, setStyle, intro = true }) {
  $("#landing-form").addEventListener("submit", (event) => {
    event.preventDefault();
    const input = $("#landing-input");
    const question = input.value.trim();
    if (!question) return;
    input.value = "";
    ask(question);
  });
  skipOnInteraction();
  if (intro && !prefersReducedMotion()) typewriter();
  else becomeSearchBox({ focus: false });
  initPersonas({ ask, setStyle });
}

export function showLanding() {
  becomeSearchBox({ focus: true });
}
