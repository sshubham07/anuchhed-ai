// Answer rendering: escape first, then a small Markdown subset, then [Art. X] citations → navy pills.
// Citation parsing mirrors `generation/citations.py` (parse_bracket / label).

export function escapeHtml(text) {
  return String(text).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}

const BRACKET = /\[([^[\]\n]{1,160})\]/g;
const SPLIT = /\s*(?:[,;]|\band\b|&)\s*/i;
const PREFIX = /^(articles?|arts?\.?|sch(?:edule)?s?\.?|app(?:endix)?\.?|preamble)\s*(.*)$/i;
const ORDINAL_SCHEDULE = /^\w+\s+schedule$/i;
const ROMAN = { i: 1, ii: 2, iii: 3, iv: 4, v: 5, vi: 6, vii: 7, viii: 8, ix: 9, x: 10, xi: 11, xii: 12 };
const ORDINALS = ["first", "second", "third", "fourth", "fifth", "sixth", "seventh", "eighth", "ninth", "tenth", "eleventh", "twelfth"];
const ROMAN_OUT = ["", "I", "II", "III"];

/** `PREAMBLE` → Preamble, `SCH-7` → Sch. 7, `APP-I` → App. I, `21A` → Art. 21A. */
export function labelFor(ref) {
  if (ref === "PREAMBLE") return "Preamble";
  if (ref.startsWith("SCH-")) return `Sch. ${ref.slice(4)}`;
  if (ref.startsWith("APP-")) return `App. ${ref.slice(4)}`;
  return `Art. ${ref}`;
}

function kindOf(prefix) {
  const p = prefix.toLowerCase();
  if (p.startsWith("sch")) return "schedule";
  if (p.startsWith("app")) return "appendix";
  if (p.startsWith("pre")) return "preamble";
  return "article";
}

function toNumber(token) {
  const t = token.toLowerCase().replace(/\.$/, "");
  if (/^\d+$/.test(t)) return Number(t);
  return ROMAN[t] ?? (ORDINALS.indexOf(t) + 1 || null);
}

function canonical(kind, item) {
  if (kind === "preamble") return "PREAMBLE";
  if (kind === "schedule") {
    const n = toNumber(item.replace(/\s*schedule$/i, ""));
    return n && n <= 12 ? `SCH-${n}` : null;
  }
  if (kind === "appendix") {
    const n = toNumber(item);
    return n && n <= 3 ? `APP-${ROMAN_OUT[n]}` : null;
  }
  const match = item.split("(")[0].replace(/[\s-]/g, "").match(/^(\d{1,3})([A-Za-z]{0,2})$/);
  return match ? `${Number(match[1])}${match[2].toUpperCase()}` : null;
}

/**
 * Refs in one bracket, or null when it isn't a citation (`[sic]`).
 * Each item keeps its display text (`Art. 21(1)`) and canonical ref (`21`).
 */
export function parseBracket(content) {
  const text = content.trim();
  if (!PREFIX.test(text)) {
    if (ORDINAL_SCHEDULE.test(text)) {
      const ref = canonical("schedule", text);
      return ref ? [{ ref, text }] : null;
    }
    return null;
  }
  const items = [];
  let kind = "article";
  let prefix = "Art.";
  for (let item of text.split(SPLIT)) {
    if (!item) continue;
    const match = item.match(PREFIX);
    if (match) {
      kind = kindOf(match[1]);
      prefix = { article: "Art.", schedule: "Sch.", appendix: "App.", preamble: "" }[kind];
      item = match[2].trim().replace(/^[ .]+|[ .]+$/g, "");
    }
    const ref = canonical(kind, item);
    if (ref && !items.some((i) => i.ref === ref)) {
      items.push({ ref, text: kind === "preamble" ? "Preamble" : `${prefix} ${item}` });
    }
  }
  return items.length ? items : null;
}

/** Canonical refs cited anywhere in `text`, in order of first mention. */
export function citedRefs(text) {
  const refs = [];
  for (const [, content] of String(text).matchAll(BRACKET)) {
    for (const { ref } of parseBracket(content) || []) if (!refs.includes(ref)) refs.push(ref);
  }
  return refs;
}

function pills(escaped) {
  return escaped.replace(BRACKET, (whole, content) => {
    // `content` is already escaped; parse its unescaped form for refs (only &amp; can matter).
    const items = parseBracket(content.replace(/&amp;/g, "&"));
    if (!items) return whole;
    const links = items.map(
      ({ ref, text }) =>
        `<button type="button" class="cite" data-ref="${escapeHtml(ref)}" aria-label="Open ${escapeHtml(labelFor(ref))}">${escapeHtml(text)}</button>`,
    );
    return `<span class="cite-group">${links.join(" ")}</span>`;
  });
}

function inline(escaped) {
  let s = escaped;
  s = s.replace(/`([^`\n]+)`/g, "<code>$1</code>");
  s = s.replace(/\*\*([^*\n]+?)\*\*|__([^_\n]+?)__/g, (_, a, b) => `<strong>${a ?? b}</strong>`);
  s = s.replace(/(^|[\s(“"])\*([^*\n]+?)\*(?=[\s).,;:!?”"]|$)/g, "$1<em>$2</em>");
  s = s.replace(/(^|[\s(“"])_([^_\n]+?)_(?=[\s).,;:!?”"]|$)/g, "$1<em>$2</em>");
  return pills(s);
}

const BULLET = /^\s*[-*•]\s+/;
const NUMBERED = /^\s*\d+[.)]\s+/;

/** Markdown subset → HTML. Safe: the input is escaped before any tag is added. */
export function renderAnswer(text) {
  // Headings always stand alone, even when the model doesn't leave a blank line after them.
  const blocks = escapeHtml(String(text).replace(/\r\n?/g, "\n"))
    .replace(/^(#{1,6}\s+.+)$/gm, "\n$1\n")
    .split(/\n{2,}/);
  const out = [];
  for (const raw of blocks) {
    const block = raw.replace(/^\n+|\n+$/g, "");
    if (!block.trim()) continue;
    const lines = block.split("\n");
    const heading = block.match(/^#{1,6}\s+(.+)$/);
    if (heading && lines.length === 1) {
      out.push(`<h4>${inline(heading[1])}</h4>`);
    } else if (lines.every((l) => BULLET.test(l) || /^\s{2,}\S/.test(l)) && BULLET.test(lines[0])) {
      out.push(list("ul", lines, BULLET));
    } else if (lines.every((l) => NUMBERED.test(l) || /^\s{2,}\S/.test(l)) && NUMBERED.test(lines[0])) {
      out.push(list("ol", lines, NUMBERED));
    } else if (lines.every((l) => l.startsWith("&gt;"))) {
      out.push(`<blockquote>${inline(lines.map((l) => l.replace(/^&gt;\s?/, "")).join("<br>"))}</blockquote>`);
    } else {
      // A paragraph that starts with a heading line followed by text, or a list after an intro line.
      const firstList = lines.findIndex((l) => BULLET.test(l) || NUMBERED.test(l));
      if (firstList > 0 && lines.slice(firstList).every((l) => BULLET.test(l) || NUMBERED.test(l))) {
        out.push(`<p>${inline(lines.slice(0, firstList).join("<br>"))}</p>`);
        const marker = BULLET.test(lines[firstList]) ? BULLET : NUMBERED;
        out.push(list(marker === BULLET ? "ul" : "ol", lines.slice(firstList), marker));
        continue;
      }
      const body = inline(lines.join("<br>"));
      const disclaimer = /^<em>Informational only/.test(body) || /^Informational only/.test(block);
      out.push(disclaimer ? `<p class="disclaimer">${body}</p>` : `<p>${body}</p>`);
    }
  }
  return out.join("");
}

function list(tag, lines, marker) {
  const items = [];
  for (const line of lines) {
    if (marker.test(line)) items.push(line.replace(marker, ""));
    else if (items.length) items[items.length - 1] += "<br>" + line.trim();
  }
  return `<${tag}>${items.map((i) => `<li>${inline(i)}</li>`).join("")}</${tag}>`;
}

/** Plain text for "Copy answer": strip Markdown emphasis, keep citations as written. */
export function plainText(text) {
  return String(text).replace(/\*\*|__/g, "").replace(/(^|\s)[_*]([^_*\n]+)[_*](?=\s|[.,;:]|$)/g, "$1$2").trim();
}
