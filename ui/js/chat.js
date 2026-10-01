// Conversation: send + stream, citation cards, style toggle, feedback, session lifecycle (spec: ui.md §3.4).
import { ApiError, api, friendly } from "./api.js";
import { debugPanel } from "./debug.js";
import { $, ICONS, copyText, el, icon, toast } from "./dom.js";
import { breadcrumb, excerptOf, getArticle } from "./drawer.js";
import { showLanding } from "./landing.js";
import { citedRefs, labelFor, plainText, renderAnswer } from "./render.js";
import { streamChat } from "./sse.js";
import { store } from "./store.js";

const STYLE_LABEL = { brief: "Brief", detailed: "Detailed", exam: "Exam" };
const ROUTE_LABEL = {
  article_lookup: "Article lookup", simple: "Direct", conceptual: "Conceptual", multi_part: "Multi-part",
  ambiguous: "Clarifying", out_of_scope: "Out of scope", chitchat: "Chat",
};

let sessionId = null;
let controller = null; // AbortController of the answer being streamed
let style = "auto";

// ---------- view switching ----------

function enterChat() {
  $("#landing").hidden = true;
  $("#chat").hidden = false;
  $("#composer-wrap").hidden = false;
  autosize();
  $("#new-chat").hidden = false;
  $("#clear-chat").hidden = false;
  document.body.classList.add("in-chat");
}

function leaveChat() {
  $("#thread").replaceChildren();
  $("#chat").hidden = true;
  $("#composer-wrap").hidden = true;
  $("#new-chat").hidden = true;
  $("#clear-chat").hidden = true;
  $("#landing").hidden = false;
  document.body.classList.remove("in-chat");
  window.scrollTo({ top: 0 });
  showLanding();
}

function nearBottom() {
  return window.innerHeight + window.scrollY >= document.body.scrollHeight - 160;
}
function scrollToEnd(force = false) {
  if (force || nearBottom()) window.scrollTo({ top: document.body.scrollHeight, behavior: force ? "smooth" : "auto" });
}

function announce(text) {
  const live = $("#sr-status");
  live.textContent = "";
  setTimeout(() => (live.textContent = text), 50);
}

// ---------- style toggle ----------

export function setStyle(value) {
  style = STYLE_LABEL[value] ? value : "auto";
  store.style = style;
  for (const button of document.querySelectorAll("#style-toggle [data-style]")) {
    const on = button.dataset.style === style;
    button.setAttribute("aria-checked", String(on));
    button.tabIndex = on ? 0 : -1;
  }
}

function initStyleToggle() {
  const group = $("#style-toggle");
  group.addEventListener("click", (event) => {
    const button = event.target.closest("[data-style]");
    if (button) setStyle(button.dataset.style);
  });
  group.addEventListener("keydown", (event) => {
    const buttons = [...group.querySelectorAll("[data-style]")];
    const index = buttons.indexOf(document.activeElement);
    if (index === -1 || !["ArrowRight", "ArrowLeft"].includes(event.key)) return;
    const next = buttons[(index + (event.key === "ArrowRight" ? 1 : buttons.length - 1)) % buttons.length];
    setStyle(next.dataset.style);
    next.focus();
    event.preventDefault();
  });
  setStyle(store.style);
}

// ---------- messages ----------

function appendUser(text) {
  const node = el("li", { class: "msg-user" }, el("div", { class: "bubble" }, text));
  $("#thread").append(node);
  return node;
}

function loader(text) {
  return el("div", { class: "scribing" },
    icon('<path class="ink" d="M2 16 C10 10 16 20 24 14 S38 10 46 15"/><path class="nib" d="M4 13 9 2l1.6 1-4 11-2.6 1Z"/>', "0 0 54 22"),
    el("span", {}, text));
}

function citationCard(citation) {
  const excerpt = el("blockquote", { class: "excerpt loading" }, " ");
  const crumb = el("p", { class: "crumb" });
  const title = el("h3", { class: "card-title" }, citation.title || citation.label);
  const card = el("article", { class: "card", dataset: { cardRef: citation.ref } },
    el("div", { class: "card-head" },
      el("span", { class: "badge" }, citation.label),
      el("div", {}, title, crumb)),
    excerpt,
    el("button", { type: "button", class: "btn link", dataset: { ref: citation.ref } }, "Read full Article ", el("span", { "aria-hidden": "true" }, "›")),
  );
  getArticle(citation.ref).then((article) => {
    excerpt.classList.remove("loading");
    if (!article) return excerpt.remove();
    if (!citation.title && article.title) title.textContent = article.title;
    crumb.textContent = breadcrumb(article);
    excerpt.textContent = excerptOf(article, 200);
    if (article.is_omitted) card.classList.add("omitted");
  });
  return card;
}

function linkHover(root) {
  const toggle = (event, on) => {
    const target = event.target.closest(".cite, .card");
    if (!target) return;
    const ref = target.dataset.ref || target.dataset.cardRef;
    for (const node of root.querySelectorAll(`.cite[data-ref="${CSS.escape(ref)}"], .card[data-card-ref="${CSS.escape(ref)}"]`)) {
      node.classList.toggle("hot", on);
    }
  };
  root.addEventListener("mouseover", (event) => toggle(event, true));
  root.addEventListener("mouseout", (event) => toggle(event, false));
}

/** An assistant turn. Returns an object that the stream handlers drive. */
function botTurn() {
  const meta = el("div", { class: "msg-meta" });
  const wait = loader("Consulting the Constitution…");
  const answer = el("div", { class: "answer", "aria-busy": "true" });
  const note = el("p", { class: "margin-note", hidden: true }, "Limited support in the text — read the cited Articles in full before relying on this.");
  const sources = el("section", { class: "sources", hidden: true, "aria-label": "Sources" });
  const actions = el("div", { class: "actions", hidden: true });
  const main = el("div", { class: "msg-main" }, meta, wait, answer, note, sources, actions);
  const node = el("li", { class: "msg-bot" }, el("div", { class: "avatar", "aria-hidden": "true" }, el("span", { class: "emblem" })), main);
  $("#thread").append(node);
  linkHover(main);

  let text = "";
  let frame = 0;
  let metaData = null;
  let finalText = "";
  const paint = () => {
    frame = 0;
    answer.innerHTML = renderAnswer(text);
    scrollToEnd();
  };

  const turn = {
    node,
    setMeta(data) {
      metaData = data;
      meta.replaceChildren(
        el("span", { class: "tag style", title: "Answer style" }, STYLE_LABEL[data.answer_style] || data.answer_style),
        ROUTE_LABEL[data.route_type] ? el("span", { class: "tag" }, ROUTE_LABEL[data.route_type]) : null,
      );
      const refs = data.refs || [];
      const hint = refs.length
        ? `Turning to ${refs.slice(0, 3).map(labelFor).join(", ")}…`
        : data.route_type === "conceptual" ? "Reading across the Constitution…"
        : data.route_type === "multi_part" ? "Gathering each part of the question…"
        : "Searching the text…";
      wait.lastChild.textContent = hint;
    },
    addToken(piece) {
      if (!text) {
        wait.remove();
        answer.classList.add("streaming");
      }
      text += piece;
      if (!frame) frame = requestAnimationFrame(paint);
    },
    setCitations(citations) {
      if (!citations.length) return;
      sources.replaceChildren(el("h3", { class: "sources-title" }, "From the Constitution"), el("div", { class: "cards" }, citations.map(citationCard)));
      sources.hidden = false;
    },
    finish(done) {
      cancelAnimationFrame(frame);
      wait.remove();
      finalText = done.answer ?? text;
      answer.classList.remove("streaming");
      answer.removeAttribute("aria-busy");
      answer.innerHTML = renderAnswer(finalText);
      note.hidden = !done.low_confidence;
      actions.replaceChildren(...answerActions(done.message_id, () => finalText, sources));
      actions.hidden = false;
      if (done.debug) main.append(debugPanel(done, metaData));
      announce("Answer ready. " + plainText(finalText).slice(0, 180));
      scrollToEnd();
    },
    fail(message, action) {
      cancelAnimationFrame(frame);
      wait.remove();
      answer.classList.remove("streaming");
      answer.removeAttribute("aria-busy");
      if (text) {
        answer.innerHTML = renderAnswer(text);
        answer.append(el("p", { class: "margin-note" }, message));
      } else {
        answer.classList.add("error");
        answer.textContent = message;
      }
      if (action) {
        actions.replaceChildren(el("button", { type: "button", class: "btn ghost", onclick: action.run }, icon(ICONS.retry), action.label));
        actions.hidden = false;
      }
      announce(message);
    },
  };
  return turn;
}

function answerActions(messageId, getText, sources) {
  const up = el("button", { type: "button", class: "btn up", "aria-pressed": "false", "aria-label": "Helpful" }, icon(ICONS.up));
  const down = el("button", { type: "button", class: "btn down", "aria-pressed": "false", "aria-label": "Not helpful" }, icon(ICONS.down));
  const copy = el("button", { type: "button", class: "btn" }, icon(ICONS.copy), "Copy");
  const row = [up, down, el("span", { class: "spacer" }), copy];

  copy.addEventListener("click", () => {
    const labels = [...sources.querySelectorAll(".badge")].map((b) => b.textContent);
    copyText(plainText(getText()) + (labels.length ? `\n\nCited: ${labels.join(", ")}` : ""));
  });

  if (!messageId) {
    up.disabled = down.disabled = true;
    up.title = down.title = "Feedback is available for saved answers";
    return row;
  }
  const choose = (rating) => {
    up.setAttribute("aria-pressed", String(rating === 1));
    down.setAttribute("aria-pressed", String(rating === -1));
    const actions = up.parentElement;
    actions.querySelector(".fb-form")?.remove();
    const comment = el("textarea", { maxlength: "1000", rows: "1", placeholder: rating === 1 ? "What was helpful? (optional)" : "What was wrong or missing? (optional)", "aria-label": "Feedback comment (optional)" });
    const submit = async (withComment) => {
      form.querySelectorAll("button").forEach((b) => (b.disabled = true));
      try {
        await api.feedback(messageId, rating, withComment ? comment.value.trim() || null : null);
        form.replaceWith(el("span", { class: "fb-thanks" }, "Thank you — noted."));
        up.disabled = down.disabled = true;
      } catch (error) {
        toast(friendly(error));
        form.querySelectorAll("button").forEach((b) => (b.disabled = false));
      }
    };
    const form = el("div", { class: "fb-form" }, comment,
      el("button", { type: "button", class: "btn primary", onclick: () => submit(true) }, "Send"),
      el("button", { type: "button", class: "btn ghost", onclick: () => submit(false) }, "Skip"));
    comment.addEventListener("keydown", (event) => {
      if (event.key === "Enter" && !event.shiftKey) {
        event.preventDefault();
        submit(true);
      }
    });
    actions.append(form);
    comment.focus();
  };
  up.addEventListener("click", () => choose(1));
  down.addEventListener("click", () => choose(-1));
  return row;
}

// ---------- sending ----------

function setBusy(busy) {
  $("#send").hidden = busy;
  $("#stop").hidden = !busy;
  $("#landing-form button[type=submit]").disabled = busy;
}

async function ensureSession() {
  if (sessionId) return sessionId;
  const { session_id } = await api.createSession();
  sessionId = session_id;
  store.session = sessionId;
  return sessionId;
}

export async function ask(question, { retried = false } = {}) {
  const message = question.trim();
  if (!message || controller) return;
  enterChat();
  const userNode = retried ? null : appendUser(message);
  const turn = botTurn();
  scrollToEnd(true);
  controller = new AbortController();
  setBusy(true);
  let freshSession = false;
  try {
    const body = { session_id: await ensureSession(), message };
    if (style !== "auto") body.answer_style = style;
    await streamChat(body, {
      onMeta: (data) => turn.setMeta(data),
      onToken: (data) => turn.addToken(data.text),
      onCitations: (data) => turn.setCitations(data.citations),
      onDone: (data) => turn.finish(data),
      onError: (data) => turn.fail(friendly(new ApiError(data.code, data.message)), { label: "Try again", run: () => resend(turn, message) }),
    }, controller.signal);
  } catch (error) {
    if (error.name === "AbortError") {
      turn.fail("Stopped. Ask again whenever you're ready.");
    } else if (error.code === "NOT_FOUND" && !retried) {
      // The stored session expired or was cleared elsewhere: start a fresh one, once.
      sessionId = null;
      store.session = null;
      turn.node.remove();
      freshSession = true;
    } else if (error.code === "MESSAGE_TOO_LONG") {
      turn.node.remove();
      userNode?.remove();
      $("#composer-input").value = message;
      autosize();
      toast(friendly(error));
    } else if (error.code === "SESSION_FULL") {
      turn.fail(friendly(error), { label: "New chat", run: newChat });
    } else {
      turn.fail(friendly(error), { label: "Try again", run: () => resend(turn, message) });
    }
  } finally {
    controller = null;
    setBusy(false);
  }
  if (freshSession) await ask(message, { retried: true });
}

function resend(turn, message) {
  if (controller) return;
  turn.node.remove();
  ask(message, { retried: true });
}

// ---------- history ----------

function renderStored(message) {
  if (message.role === "user") return appendUser(message.content);
  const turn = botTurn();
  const refs = message.cited_articles?.length ? message.cited_articles : citedRefs(message.content);
  turn.setCitations(refs.map((ref) => ({ ref, label: labelFor(ref), title: null })));
  turn.finish({ answer: message.content, message_id: message.id, low_confidence: false });
  return turn.node;
}

/** Rebuild the thread for a stored session. Returns false when there is nothing to show. */
export async function restore() {
  const id = store.session;
  if (!id) return false;
  try {
    const page = await api.messages(id, 50);
    sessionId = id;
    store.session = id;
    if (!page.messages.length) return false;
    enterChat();
    page.messages.forEach(renderStored);
    requestAnimationFrame(() => window.scrollTo({ top: document.body.scrollHeight }));
    return true;
  } catch (error) {
    if (error.code === "NOT_FOUND" || error.status === 422) store.session = null; // expired: start fresh quietly
    return false;
  }
}

// ---------- new chat / clear ----------

export function newChat() {
  controller?.abort();
  sessionId = null;
  store.session = null;
  leaveChat();
}

let confirmTimer;
async function clearChat(event) {
  const button = event.currentTarget;
  if (!button.classList.contains("confirm")) {
    button.classList.add("confirm");
    button.querySelector("span").textContent = "Delete this chat?";
    confirmTimer = setTimeout(() => {
      button.classList.remove("confirm");
      button.querySelector("span").textContent = "Clear";
    }, 4000);
    return;
  }
  clearTimeout(confirmTimer);
  button.classList.remove("confirm");
  button.querySelector("span").textContent = "Clear";
  const id = sessionId;
  controller?.abort();
  try {
    if (id) await api.deleteSession(id);
    toast("Chat cleared");
  } catch (error) {
    if (error.code !== "NOT_FOUND") return toast(friendly(error));
  }
  newChat();
}

// ---------- composer ----------

function autosize() {
  const input = $("#composer-input");
  input.style.height = "auto";
  if (input.scrollHeight) input.style.height = Math.min(input.scrollHeight, 200) + "px"; // 0 while hidden
  $("#send").disabled = !input.value.trim();
}

export function initChat() {
  document.body.append(el("div", { id: "sr-status", class: "visually-hidden", "aria-live": "polite" }));
  initStyleToggle();
  const input = $("#composer-input");
  input.addEventListener("input", autosize);
  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
      event.preventDefault();
      $("#composer").requestSubmit();
    }
  });
  $("#composer").addEventListener("submit", (event) => {
    event.preventDefault();
    const question = input.value;
    if (!question.trim() || controller) return;
    input.value = "";
    autosize();
    ask(question);
  });
  $("#stop").addEventListener("click", () => controller?.abort());
  $("#new-chat").addEventListener("click", newChat);
  $("#clear-chat").addEventListener("click", clearChat);
  autosize();
}
