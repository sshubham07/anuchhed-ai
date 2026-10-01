# ADR-0013: Static web UI served by the API, instead of Streamlit

- **Status:** Accepted
- **Date:** 2026-10-01
- **Deciders:** Shubham Kumar Gupta
- **Related:** HLD §12, §18 · spec `docs/specs/ui.md`

## Context
The HLD picked Streamlit for the chat UI as the fastest way to get a demo. The UI now has a design brief: ivory
paper with saffron, green and navy accents, a manuscript feel, a Preamble typewriter that turns into the search box,
citation cards with a slide-out "Read full Article" drawer, persona chips and a style toggle. Streamlit's layout
model fights most of that. It reruns the whole script on every interaction, the chat input is fixed, custom HTML
lives in iframes, and it has no drawers.

A Streamlit UI also runs on a server. Every user's request reaches the API from the Streamlit process, so the UI
would have to forward `X-Forwarded-For`, and the API would have to trust it, for per-IP rate limits to work.

## Decision
Build the UI as **static HTML, CSS and vanilla ES modules** in `ui/`, with no framework and no build step.
**FastAPI serves it** from `/` with `StaticFiles` when `SERVE_UI=true`.
- The browser calls `/v1/...` directly from the same origin, so there's no CORS in the default deployment and the
  client IP is the real TCP peer.
- Streaming uses `fetch` with a `ReadableStream` SSE parser, because `EventSource` can't send a POST body.
- The UI still talks only to the public API, with no DB or model access.

Streamlit stays an option only for the internal Eval page (P7.10), which is a tool for us, not a product screen.

## Alternatives considered
- **Streamlit with heavy custom CSS.** Keeps the HLD as is, but gets about 70% of the look. The typewriter can't
  turn into `st.chat_input`, and it needs a proxy setup for client IPs.
- **React or Vite single-page app.** Better component model, but it adds Node, a build step and a second
  toolchain to a Python-only repo, for a few screens.
- **Separate nginx container.** Adds infra (AGENTS rule 7) and brings CORS back, for no gain at this scale.

## Consequences
- One container serves the API and the UI. `docker compose --profile app up` is the whole demo. No `ui` service.
- UI code is plain JS without a type checker. Logic is kept in small, pure modules (SSE parsing, citation
  rendering) so it stays easy to check by hand and in the browser.
- The UI loads fonts from Google Fonts with system fallbacks, and works offline with the fallbacks.
- We drop the `streamlit` dependency for the chat.

## Revisit when
The UI needs client-side routing across many screens, shared state that gets hard to follow in vanilla JS, or a
second frontend developer. At that point, move to a small framework with a build step.
