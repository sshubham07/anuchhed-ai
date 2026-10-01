# Spec: Web UI ("the Constitution talks back")

- **Status:** Approved
- **Owner:** Shubham
- **Related:** HLD §12, ADR-0013, plan phase 6, spec `api-sessions-memory.md` §3.4–3.5, §10
- **Last updated:** 2026-10-01

## 1. Problem / goal
The API answers questions with Article citations, but there's no screen for people to use it. The UI should feel
like the Constitution itself is talking to you. That means ivory paper, saffron and green accents, navy for
citations, and a touch of the handwritten original manuscript. The flow must work end to end: ask, stream, read
citations, open the full Article, follow up, and give feedback.

## 2. Scope
- In scope:
  - Landing screen: Preamble typewriter that becomes the search box, persona chips. (Article of the Day was removed on 2026-10-01.)
  - Chat: streaming answers, citation pills and cards, "Read full Article" drawer, Auto/Brief/Detailed/Exam
    toggle, 👍/👎 with an optional comment, New chat and Clear, history reload, error states.
  - Debug panel when the API runs with `DEBUG_UI=true`.
  - Light theme and a "night reading" dark theme, mobile layout, reduced motion, keyboard and screen-reader
    support.
- Out of scope: Hindi answers (UI accents only), accounts, file upload, voice, the Eval page (P7.10), and anything
  that changes prompts. Personas are UI presets only and are never sent to the API.

## 3. Design

### 3.1 Delivery
Static files in `ui/`: `index.html`, `css/`, `js/` (ES modules), `assets/` (SVG), `data/` (JSON). FastAPI mounts
`ui/` at `/` (`StaticFiles(html=True)`) after the API routers when `SERVE_UI=true`. All calls are same-origin to
`/v1/...`. No build step and no npm.

### 3.2 Visual system
| Token | Value | Use |
|-------|-------|-----|
| `--paper` | `#FFFDF7` ivory | page background |
| `--ink` | `#2B2118` | body text |
| `--saffron` | `#FF9933` | primary buttons, highlights, active chips |
| `--green` | `#138808` | success, secondary accents, 👍 |
| `--navy` | `#000080` | links, Article badges, citation pills |
| tricolour rule | 3px saffron → white → green | top of the page only |

- Landing page: saffron and green only as accents. **Chat view:** a soft tricolour wash (saffron band at the
  top, ivory middle, green band at the bottom; about 15% strength) with a faint, slowly turning Ashoka Chakra
  watermark in the middle. Text always sits on ivory cards or bubbles, so contrast is unaffected.
- Text on saffron uses `--ink`, never white, to keep AA contrast.
- Typography:
  - **Noto Serif** for Constitution text: quotes, the drawer, the typewriter.
  - **Inter** for the interface.
  - **Noto Sans Devanagari** for Hindi accents.
  - Fonts load from Google Fonts with system fallbacks.
- Texture: paper grain from an inline SVG `feTurbulence` at low opacity.
- Original manuscript-style border: a repeating lotus-bud-and-vine SVG, our own drawing. It's used on the
  landing frame and the drawer header only.
- Logo: the State Emblem of India (Lion Capital, `assets/emblem.svg`, from Wikimedia Commons
  `File:Emblem_of_India.svg`, public domain). It's used as a CSS mask, tinted `--navy`, in the header, on each
  answer and as the favicon. The chat watermark is the Ashoka Chakra (`assets/chakra.svg`).
- **Legal note (owner's decision):** the State Emblem of India (Prohibition of Improper Use) Act, 2005 restricts
  the emblem to official use. To avoid implying government affiliation, the footer and composer say "not an
  official Government of India service". Review before any public deployment.
- Dark theme, "night reading": deep ink-navy paper, ivory text, the same accents. It follows
  `prefers-color-scheme` and can be set by a toggle, which is remembered in `localStorage`.

### 3.3 Landing (empty session)
1. The opening of the Preamble ("We, the People of India, having solemnly resolved to constitute India into a
   Sovereign Socialist Secular Democratic Republic…") types itself out in serif with a quill caret. When it
   finishes, or on any key or click, the text fades and the same frame becomes the input, with the placeholder
   "Ask anything about your Constitution." With `prefers-reduced-motion`, the input shows at once.
2. Subtitle in Devanagari: "हम, भारत के लोग".
3. Persona chips: 🎓 UPSC Aspirant (default style Exam) · 🧑‍⚖️ Advocate (Detailed) · 🙋 Citizen (Brief).
   Picking one sets the style toggle and shows that persona's four starter questions (`ui/data/personas.json`).
   The choice is remembered in `localStorage`.
4. Footer: the edition date from `GET /v1/meta` ("Text as on …") and "Informational only. Not legal advice."

### 3.4 Chat
- **Sending.** `POST /v1/chat {session_id, message, stream: true, answer_style?}`.
  - Auto sends no `answer_style`.
  - The session is created lazily (`POST /v1/sessions`) on the first send.
  - The session id is stored in `localStorage` and mirrored in `?s=<id>`, so a reload or a shared link reopens it.
- **Streaming.** SSE events are parsed from the fetch body stream:
  - `meta`: shows the style tag ("Exam" etc.) and route chip.
  - `token`: appended to the bubble as escaped text.
  - `citations`: builds the cards.
  - `done`: the bubble is re-rendered from `done.answer`, which is authoritative because invalid citations are
    removed. `message_id` enables feedback, and `low_confidence` shows a saffron margin note ("Limited support
    in the text").
  - `error`: inline error with the code's friendly message.
  - A quill loader shows until the first token.
- **Rendering.** Safe mini-markdown. HTML is escaped first, then paragraphs, `**bold**`, `_italics_`, bulleted and
  numbered lists, and headings are formatted. Bracketed citations (`[Art. 21]`, `[Arts. 32 and 226]`, `[Sch. 7]`,
  `[Preamble]`) become navy pills that open the drawer. Hovering a pill highlights its card. Ref parsing mirrors
  `generation/citations.py`.
- **Citation cards.** Each card is styled as a manuscript excerpt: a deckled ivory card with a navy badge
  ("Art. 21"), the title, a "Part III · Fundamental Rights" breadcrumb, and a short serif quote. The quote is
  fetched lazily once per ref, with an in-memory cache. Each card has a "Read full Article ›" button.
- **Drawer.**
  - A side panel on desktop and a bottom sheet on mobile.
  - Shows the full text in serif with clause line breaks kept, and an "Omitted" notice when `is_omitted` is set.
  - Prev/next navigation works for numbered Articles only. It steps the number by one, client-side, skipping refs
    that return 404 (up to three times).
  - Has "Copy" and "Ask about this Article" buttons.
  - Esc closes it, and focus is trapped while it's open.
- **Style toggle.** A segmented control by the input with Auto · Brief · Detailed · Exam.
- **Feedback.** 👍/👎 opens an optional comment popover (≤1000 chars), then `POST /v1/messages/{id}/feedback`.
  The buttons then show the chosen state. They're disabled for answers without a `message_id`.
- **Session.**
  - "New chat" clears the thread and creates a fresh session on the next send.
  - The header brand ("NyayaAI", subtitle "न्याय · Ask the Constitution") is the home link. A plain click does the
    same as New chat; it must not reload, because a reload restores the stored session and shows the chat again.
  - "Clear" sends `DELETE /v1/sessions/{id}`, then does the same as New chat.
  - On load with a stored id, `GET /v1/sessions/{id}/messages` rebuilds the thread. Cards come from
    `cited_articles`, with labels made like `generation/citations.label`. A 404 means the session expired: start
    fresh silently.
- **Errors** (from the `error.code` envelope or SSE `error`):
  - `RATE_LIMITED`: "Slow down — try again in N s" (uses `Retry-After`).
  - `SESSION_FULL`: offer New chat.
  - `MESSAGE_TOO_LONG`: show the limit and keep the text in the input.
  - `EMPTY_MESSAGE`: ignored client-side.
  - `LLM_UNAVAILABLE`, `BUSY`, `SERVICE_UNAVAILABLE` / 503: "The scribe is resting — please retry."
  - Network failure: a retry button.
- **Debug panel.** Shown when `done.debug` is present. A collapsible "Behind the answer" section shows the route
  JSON, standalone query, a chunk table with dense/lexical/RRF/rerank scores, and latency bars from
  `done.latency_ms`.
- **Extras.**
  - The `/` key focuses the input.
  - "Copy answer" copies the answer text plus a list of the cited Article labels.
  - Streaming text goes to an `aria-live="polite"` region. Focus rings are visible, and all controls work by
    keyboard.

### 3.5 Modules
| File | Responsibility |
|------|----------------|
| `js/api.js` | fetch wrappers, `ApiError {code, message, retryAfter}` |
| `js/sse.js` | `streamChat(body, handlers, signal)`: POST + incremental `event:`/`data:` parser |
| `js/render.js` | `escapeHtml`, `renderAnswer(text)`, `parseCitationRefs`, `labelFor(ref)` |
| `js/landing.js` | typewriter, personas |
| `js/chat.js` | thread state, send/stream, toggle, feedback, session lifecycle |
| `js/drawer.js` | Article drawer and the shared article cache |
| `js/debug.js` | debug panel |
| `js/app.js` | bootstrap: theme, meta, wiring |

## 4. Config
`SERVE_UI` (default `true`) and `UI_DIR` (default `ui`) serve the static UI. `DEBUG_UI` (existing) adds `debug`
to `done`. `CORS_ORIGINS` stays for UIs served from another origin; the default deployment doesn't need it.

## 5. Failure modes
| Failure | Behaviour |
|---------|-----------|
| Google Fonts unreachable | System serif/sans fallbacks; layout unchanged |
| `/v1/meta` fails | Footer omits the edition date |
| Stream cut mid-answer (network) | The bubble keeps the partial text, marked "interrupted", with a retry button |
| Stored session expired | Fresh session, no error shown |
| `ui/` missing with `SERVE_UI=true` | App starts; the mount is skipped with a warning log |

## 6. Acceptance criteria
- [ ] `GET /` serves the UI. `/v1/*`, `/healthz` and `/docs` are unaffected. `SERVE_UI=false` → `/` is 404.
- [ ] `answer_style` on `/v1/chat` overrides the router's style (visible in `meta` and in the stored route).
      Omitted → the router decides. Invalid → 422.
- [ ] `done.debug` is present only when `DEBUG_UI=true`.
- [ ] Demo flow in a browser: typewriter → persona → starter question → streamed answer with cards → drawer →
      follow-up → Exam toggle (meta tag shows "exam") → 👍 with a comment → reload restores history → New chat.
- [ ] From a chat, a click on the NyayaAI brand shows a fresh landing (no reload back into the same chat).
- [ ] No horizontal scroll at 375px wide; the drawer becomes a bottom sheet.
- [ ] Reduced motion skips the typewriter; the dark theme keeps AA contrast for body text and pills.
- [ ] Eval: router suite unchanged (no prompt change; override defaults to `None`).

## 7. Test plan
- Unit (`tests/unit/test_api.py`, `test_chat_events.py`, `test_graph.py`): override applied and shown in `meta`;
  `None` keeps the router's value; 422 on a bad style; debug block on/off; static mount on/off; OpenAPI snapshot
  refreshed.
- Manual: browser walkthrough of §6 using the local API, recorded as a GIF.

## 8. Open questions
- None blocking. A Hindi UI (labels only) is a v2 candidate; the font stack is already Hindi-ready.
- Known behaviours (from review, accepted for v1):
  - "Try again" after an in-stream `error` re-posts the question as a new turn. The failed attempt's user row
    stays in history, because the question is stored before the pipeline runs (HLD §8.1 step C).
  - The session id in `?s=` works like a bearer token for that chat's history: sharing the link shares the chat.
  - `DEBUG_UI` exposes the route and retrieval scores to every browser. Set `DEBUG_UI=false` for any public
    deployment.
