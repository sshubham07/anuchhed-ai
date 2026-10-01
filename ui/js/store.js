// Per-browser preferences and the current session id. Storage can be blocked: every access is guarded.

const KEYS = { session: "samvidhan.session", persona: "samvidhan.persona", style: "samvidhan.style", theme: "samvidhan.theme" };

function read(key) {
  try { return localStorage.getItem(key); } catch { return null; }
}
function write(key, value) {
  try {
    if (value === null || value === undefined) localStorage.removeItem(key);
    else localStorage.setItem(key, value);
  } catch { /* private mode: keep going without persistence */ }
}

export const store = {
  get session() {
    return new URLSearchParams(location.search).get("s") || read(KEYS.session);
  },
  set session(id) {
    write(KEYS.session, id);
    const url = new URL(location.href);
    if (id) url.searchParams.set("s", id);
    else url.searchParams.delete("s");
    history.replaceState(null, "", url);
  },
  get persona() { return read(KEYS.persona); },
  set persona(id) { write(KEYS.persona, id); },
  get style() { return read(KEYS.style) || "auto"; },
  set style(value) { write(KEYS.style, value); },
  get theme() { return read(KEYS.theme); },
  set theme(value) { write(KEYS.theme, value); },
};
