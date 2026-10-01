// "Behind the answer": route, scored chunks and latency, shown when the API runs with DEBUG_UI=true.
import { el } from "./dom.js";

const STAGES = ["dense", "lexical", "rrf", "rerank"];
const fmt = (value) => (typeof value === "number" ? value.toFixed(3) : "—");

function chunkTable(chunks) {
  if (!chunks.length) return el("p", {}, "No chunks — this reply didn't search the text.");
  return el("div", { class: "debug-table-wrap" },
    el("table", {},
      el("thead", {}, el("tr", {}, el("th", {}, "#"), el("th", {}, "Provision"), ...STAGES.map((s) => el("th", {}, s)))),
      el("tbody", {}, chunks.map((c, i) =>
        el("tr", {},
          el("td", { class: c.pinned ? "pin" : undefined, title: c.pinned ? "pinned by reference" : undefined }, c.pinned ? `${i + 1}•` : i + 1),
          el("td", {}, c.label || "—", c.title ? el("span", { class: "muted" }, ` ${c.title.slice(0, 40)}`) : null),
          ...STAGES.map((s) => el("td", {}, fmt(c.scores?.[s]))),
        ),
      )),
    ),
  );
}

function latencyBars(latency) {
  const entries = Object.entries(latency || {}).filter(([k]) => k !== "total");
  const total = latency?.total ?? Math.max(1, ...entries.map(([, v]) => v));
  return el("div", { class: "lat" },
    entries.flatMap(([stage, ms]) => [
      el("span", {}, stage),
      el("span", { class: "bar", style: `width:${Math.max(1, Math.round((ms / total) * 100))}%` }),
      el("span", { class: "ms" }, `${ms} ms`),
    ]),
    latency?.total != null ? [el("strong", {}, "total"), el("span"), el("strong", { class: "ms" }, `${latency.total} ms`)] : null,
  );
}

export function debugPanel(done, meta) {
  const { debug } = done;
  return el("details", { class: "debug" },
    el("summary", {}, "Behind the answer"),
    el("div", { class: "debug-body" },
      el("section", {}, el("h4", {}, "Standalone query"), el("p", {}, meta?.standalone_query || "—")),
      el("section", {}, el("h4", {}, "Retrieved context"), chunkTable(debug.chunks || [])),
      el("section", {}, el("h4", {}, "Latency"), latencyBars(done.latency_ms)),
      debug.invalid_citations?.length
        ? el("section", {}, el("h4", {}, "Removed citations"), el("p", {}, debug.invalid_citations.join(", ")))
        : null,
      el("section", {}, el("h4", {}, "Route"), el("pre", {}, JSON.stringify(debug.route, null, 2))),
    ),
  );
}
