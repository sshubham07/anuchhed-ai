# Spec: Ingestion & chunking (chunker v1)

- **Status:** Implemented (P1.0–P1.12) — approved by owner 2026-09-30; Appendices added to scope
- **Owner:** Shubham
- **Related:** HLD §7, §10 · ADR-0001 (structure-aware chunking), ADR-0005, ADR-0008 (local bge-m3) ·
  observability.md §1.3 · plan Phase 1 (P1.0–P1.13)
- **Last updated:** 2026-09-30

## 1. Problem / goal

Turn the official Constitution of India PDF into validated, structure-aware chunks in Postgres, so retrieval can
find the right Article and every answer can cite it exactly. We follow the document's own hierarchy
(Part → Chapter → group heading → Article → clause) with **one chunk per Article** instead of fixed-size windows:
fixed windows split Articles in half and mix unrelated ones, which makes a citation like "Article 21" point at a
messy fragment (ADR-0001).

## 2. Scope

- **In scope:** Preamble; every Article, including lettered (`21A`, `243ZH`) and omitted ones (`31`, `259`);
  Part / Chapter / group-heading metadata; the 12 Schedules (Seventh Schedule by List and entry); amendment
  footnotes; **Appendices I–III** of this edition (the Constitution (One Hundredth Amendment) Act, 2015 with the
  India–Bangladesh land lists; the Constitution (Application to Jammu and Kashmir) Order, 2019 (C.O. 272); the
  declaration under Article 370(3) (C.O. 273)) — users ask about them; embeddings (bge-m3 dense); storage in
  `documents` / `chunks` (+ migration 002 for the `appendix` chunk type); validation against a canonical Article
  list; `chunks.jsonl` + ingestion report; the `ingest` CLI; downloading local models.
- **Out of scope:** the Contents pages as chunks (used only to build the expected-Article list); Hindi text; OCR /
  scanned PDFs; sparse / ColBERT vectors from bge-m3; any other document; the retrieval side (Phase 3); citing
  Appendices in answers (Phase 4, see §8).

## 3. Design

### 3.1 Source document

| Field | Value |
|-------|-------|
| File | `data/raw/constitution.pdf` (gitignored; not committed) |
| Edition | "THE CONSTITUTION OF INDIA [As on 1st May, 2024]", Legislative Department, pocket edition, amendments up to the 106th Amendment Act, 2023 |
| `version_date` | `2024-05-01` (parsed from the title page; CLI flag overrides) |
| `source_url` | `https://legislative.gov.in/constitution-of-india/` |
| Pages | 402, all with a text layer (no OCR) |
| Layout | pp. 1–4 title/preface/abbreviations · pp. 5–31 Contents · **pp. 32–381 body** (Preamble → Twelfth Schedule) · **pp. 382–402 Appendices I–III** |

Layout facts the parser relies on (checked with PyMuPDF on this edition):

- **Running header** on every body page: `THE CONSTITUTION OF INDIA` (bold), a context line such as
  `(Part III.—Fundamental Rights)` or `(Seventh Schedule)`, and the page number. All three are dropped.
- **Body** is ~9.1 pt. An **Article heading** is bold: `21. Protection of life and personal liberty.—No person…`.
  The heading can wrap onto a second bold line (Art. 6). The title ends at `.—`; body text follows on the same line.
- **Inserted/substituted text** is wrapped as `N[ … ]`, where `N` is a ~6 pt superscript footnote number. An
  inserted Article starts with the marker, e.g. `2[21A. Right to education.—…]`, so its first span is small and
  not bold. The segmenter must look past a leading marker.
- **Omitted text:** `[259. Armed Forces in States in Part B of the First Schedule.—Omitted.]` or star rows
  `2[(f)* * * * *]`.
- **Footnotes** sit below a `______` rule (~12 pt), at ~7.9 pt: `1. Subs. by the Constitution (Forty-second
  Amendment) Act, 1976, s. 2, for "…" (w.e.f. 3-1-1977).` Numbering **restarts on every page**, so a marker is
  resolved against footnotes on the same page.
- **Seventh Schedule:** bold `List I—Union List` / `List II—State List` / `List III—Concurrent List`; entries
  `1.`, `2.`, `2A.` (inserted entries use the same `N[` marker convention).
- Some pages (e.g. Ninth Schedule p. 361) emit one word per span or line at a slightly different size (8.7 pt), so
  lines are rebuilt from spans by y-position, not taken from PyMuPDF's line objects as-is.
- **Tables:** the First Schedule (pp. 284–) is a two-column table (State/Union territory name | Territories) whose
  cells wrap independently, and Appendix I pp. 391–400 are six-column enclave tables. Extract keeps each row's
  x-separated cells in `Line.parts`; the chunker must rebuild table rows from `parts` rather than from the joined
  `text`, in which wrapped cells of the two columns interleave.
- The Contents pages list **506 Articles** (35 omitted, shown in `[...]`). They print `243-I`, `243Z-O`, `371-I`
  (a hyphen so I/O are not read as digits) and `371B .`; these are normalised to `243I`, `243ZO`, `371I`, `371B`.
- **Appendices** open with a bold `APPENDIX I` / `APPENDIX  II` (note the double space) / `APPENDIX III`; the running
  context line is `(Appendix I)`. Appendix I (pp. 382–400) is an amending Act with its **own** `THE FIRST
  SCHEDULE`, `PA R T I` (letter-spaced), sections and footnotes; pp. 391–400 are enclave **tables** (one cell per
  line). Appendix II (p. 401) and III (p. 402) are short Presidential orders; their title line starts with a
  footnote marker (`1THE CONSTITUTION (APPLICATION TO …`).

### 3.2 Pipeline and modules

```
PDF → extract → clean → footnotes → segment → chunk → embed_text → embed → store → validate
```

| Step | Module | Input → output |
|------|--------|----------------|
| Extract | `ingestion/extract.py` | PDF → `list[Line]`: PyMuPDF lines whose vertical extents overlap form one row, spans ordered by x; footnote numbers become `{{fn:N}}` tokens |
| Clean | `ingestion/clean.py` | drop header/page-number lines, find the body range (PREAMBLE → end of document), de-hyphenate, normalize whitespace/quotes |
| Footnotes | `ingestion/footnotes.py` | per page: split at the rule line → footnotes keyed by (page, n); body rows keep their `{{fn:N}}` tokens until chunking. Footnotes are numbered **by position** (the PDF has typos such as "2." printed twice); a row starts a footnote on `N.` or, without the dot, only if the page references N — so continuation rows starting with a year or "1 S.C.C." are not footnotes. Leading unnumbered rows are an editorial note (n = 0) |
| Segment | `ingestion/segment.py` | lines → `list[Segment]` via a state machine (§3.4) |
| Chunk | `ingestion/chunk.py` | segments → `list[ChunkRecord]` (§3.5, §3.6) |
| Embed | `ingestion/embed.py` | `embed_text` → 1024-d normalized vectors (§3.7) |
| Store | `db/repositories/corpus.py` | document + chunks in one transaction (§3.8) |
| Validate | `ingestion/validate.py` | chunks vs `eval/fixtures/expected_articles.txt` → report; fail on missing/duplicate |
| CLI | `ingestion/cli.py` | wires it together (§3.9) |
| Models | `ingestion/models.py` | downloads model weights into the HF cache (§3.10) |

Each step is a pure function over plain data except embed / store / CLI, so extract → validate can be unit-tested
without a model or DB.

### 3.3 Data types (`ingestion/types.py`, frozen dataclasses)

```python
Line(page: int, text: str, size: float, bold_prefix: str, y: float, x0: float, parts: tuple[str, ...])
    # .is_bold — whole row bold (ignoring tokens). text contains "{{fn:N}}" where a footnote number was;
    # bold_prefix = leading bold run (an Article heading's "21. Title."); parts = cells split at gaps > 15 pt
Footnote(page: int, n: int, text: str)
Segment(kind: Literal["preamble", "article", "schedule", "appendix"], seq: int,
        part_no: str | None, part_title: str | None, chapter: str | None, group_heading: str | None,
        article_no: str | None, article_title: str | None,
        schedule_no: str | None, schedule_list: str | None,       # "I" / "II" / "III" for the Seventh Schedule
        appendix_no: str | None, appendix_title: str | None,     # "II", "The Constitution (Application to …) Order, 2019"
        lines: tuple[Line, ...], footnotes: tuple[Footnote, ...], is_omitted: bool)
    # lines keep layout (x0 for paragraphs, parts for tables) and footnote tokens
ChunkRecord  # the `chunks` columns (HLD §10) minus document_id, embedding, tsv
```

### 3.4 Segmenter (state machine)

`ingestion/segment.py`. The state holds the mode (preamble / parts / schedule / appendix), the current Part,
Chapter, group heading and Schedule. Rules run on the row text with footnote tokens, a leading `[`/`*` and extra
spaces removed. **Bold is not a reliable signal in this edition** (checked on the real PDF, P1.5): Part titles,
Chapter lines and group headings are centered *non-bold* rows, some `PART` headings are not bold (`[PART IXA`),
and omitted Articles are often not bold (`[2A. [Sikkim…`), so position and text decide.

"Centered" = row x0 ≥ 215 pt (body rows start at 162–200). "Heading indent" = x0 175–196 pt.

| # | Pattern (on the stripped row) | Condition | Effect |
|---|-------------------------------|-----------|--------|
| 1 | `^APPENDIX\s+(I{1,3})$` | any mode | open an Appendix; capitalised rows up to `C.O. …` / `[date]` are its title; all other rules are off from here |
| 2 | `^(FIRST\|…\|TWELFTH)\s+SCHEDULE$` | centered, not in an Appendix | open a Schedule; clear Part / Chapter |
| 3 | `^List (I{1,3})\s*[—-]` | Seventh Schedule | open a Seventh Schedule List segment (`group_heading` = the List line) |
| 4 | `^PREAMBLE$` | bold, start of body | open the Preamble |
| 5 | `^PART\s+([IVXL]+[A-Z]?)$` | centered | set `part_no`; the next row is the Part title, and following ALL-CAPS rows continue it wherever they start (Part XI's title is at x0 194); reset Chapter / group heading |
| 6 | `^CHAPTER\s+([IVXL]+)\.?\s*[—-]*\s*(.*)$` | centered heading row | set `chapter` ("Chapter I — The Executive"); ALL-CAPS rows right after continue it |
| 7 | starts with a capital, ≤ 90 chars | centered, in Parts | `group_heading` ("Right to Freedom"); consecutive heading rows are joined |
| 8 | `^(\d{1,3})((?:-?[A-Z]){0,3})\.\s*(?=[A-Z\[])` | heading indent, in Parts, number ≥ previous Article number | open an Article; `371-I` → `371I` |
| 9 | anything else | — | body row of the open segment |

- **Title / body split:** the Article title ends at the first `—` and may wrap over up to 3 rows (Art. 6); the
  row holding the dash continues as the first body row. Title cleanup drops the number and `[ ] .`
  (`31. [Compulsory acquisition of property.].` → `Compulsory acquisition of property`).
- **Omitted:** `is_omitted` when the body starts with `Omitted` / `Rep.` or is only stars/brackets.
- **Why the checks are safe:** centered rows that are not headings are sub-clauses (start with `(`) or the one oath
  line `solemnly affirm` (lower case) — both fail rule 7. Numbered list items at the heading indent fail rule 8
  because their number goes backwards.
- **Empty Part:** a Part with no Article (Part VII, omitted as a whole) is recorded with its heading footnotes. An
  expected *omitted* Article with no body text (Art. 238, listed in the Contents as "[238. Omitted.]") gets a
  stub segment there: text "Omitted.", that Part, and the Part's footnote. Reported as `stubbed_from_contents`.
- **Footnotes** attach to the segment whose rows hold the `{{fn:N}}` token (page + N), in order, plus any editorial
  note (n = 0) on those pages. Tokens with no footnote on the page are reported as orphan markers (one, p. 204,
  a typo in the PDF).
- **Brackets are kept** in the text: `[…]` marks amended text in the official edition, and dropping it would
  change the wording. Only the footnote numbers are removed.
- Headings in the Contents pages never reach the segmenter (the body starts at the bold `PREAMBLE` followed by
  "WE, THE PEOPLE" and runs to the end of the document).

### 3.5 Chunking rules (chunker v1)

| Case | Rule |
|------|------|
| Normal Article | One chunk |
| Long Article (> `CHUNK_MAX_TOKENS` = 800 tokens, e.g. Art. 368) | Split at clause boundaries `(1)`, `(2)`… into 300–700-token pieces. Each piece repeats the Article header. Ids `art-368#0`, `art-368#1`; `clause_range` e.g. `"(1)-(3)"` |
| Short Article | Its own chunk, no merging. The header gives context and citations stay exact |
| Omitted Article (e.g. Art. 31) | Kept as a chunk with `is_omitted=true`, so the bot can say "Article 31 was omitted by the 44th Amendment" |
| Preamble | One chunk (`preamble#0`) |
| Seventh Schedule | Groups of `SCHEDULE7_ENTRIES_PER_CHUNK` (10) entries per List, entry numbers kept; ids `sch-7-list1#0`…; `clause_range` = `"entries 1-10"` |
| Other Schedules | Split by paragraph or Part, 300–700 tokens; ids `sch-10#0`… |
| Appendices | Split by section / paragraph, 300–700 tokens; ids `app-1#0`, `app-2#0`, `app-3#0`. Appendix I tables (pp. 391–400) are rebuilt row by row (cells on one y-band joined with ` \| `), packed into 300–700-token pieces, and each piece repeats the table's column header |
| Amendment footnotes | Appended to `embed_text` as `Amendment notes: …` and stored in `amendment_notes` |

**Split algorithm** (long Articles and other Schedules): cut the body into blocks at top-level clause markers
(`^\(\d+[A-Z]?\)`) or schedule paragraph/Part markers; greedily pack blocks while the piece stays ≤
`CHUNK_TARGET_MAX_TOKENS`; a piece under `CHUNK_TARGET_MIN_TOKENS` is merged into its neighbour. A single block
over the max is split on sub-clauses `(a)`, `(b)`… then on sentences. Provisos and Explanations stay with the
clause they follow.

**Tokens** are counted with the bge-m3 tokenizer (`AutoTokenizer.from_pretrained(EMBED_MODEL)`), the same one the
embedder uses, over `embed_text`. Every chunk must be ≤ `EMBED_MAX_LENGTH` (1024), so nothing is truncated when
embedded.

### 3.6 Chunk texts and metadata

`text` — the clean body shown in citation panels (no header, no footnote numbers, inserted text kept, no
Amendment notes).

`embed_text` — used for search (dense and the `tsv` full-text column):

```
Part III — Fundamental Rights > Right to Freedom > Article 21: Protection of life and personal liberty

No person shall be deprived of his life or personal liberty except according to procedure established by law.

Amendment notes: …
```

The header path skips empty levels (no chapter / group heading). Schedules use
`Seventh Schedule > List II — State List > Entries 1–10`; Appendices use
`Appendix II — The Constitution (Application to Jammu and Kashmir) Order, 2019`. `Amendment notes:` lists each footnote's text once, in
order; the line is left out when there are none.

`amendment_notes` (jsonb) — `[{"n": 2, "text": "Ins. by the Constitution (Eighty-sixth Amendment) Act, 2002, s. 2
(w.e.f. 1-4-2010).", "action": "Ins.", "act": "Constitution (Eighty-sixth Amendment) Act", "year": 2002}]`.
`action` / `act` / `year` are best-effort regex fields (`null` if not parsed); `text` is always the verbatim
footnote.

Other columns: `chunk_type` (`preamble` / `article` / `schedule` / `appendix`), `seq` (reading order across the
document), `part_no`, `part_title`, `chapter`, `group_heading`, `article_no` (`"21A"`), `article_title`,
`schedule_no` (`"7"`), `appendix_no` (`"II"`), `clause_range`, `is_omitted`, `token_count`. For an Appendix the
title goes in `part_title` (it is the top-level container, like a Part) and an internal heading such as
"THE THIRD SCHEDULE" of the amending Act goes in `group_heading`. This lets "Article 21" be fetched by metadata, not by
embedding similarity, allows filtering by Part, and gives precise citations.

### 3.7 Embeddings

- `Embedder` protocol: `embed(texts: Sequence[str]) -> list[list[float]]` and `model_id: str`.
- `BgeM3Embedder`: `SentenceTransformer(EMBED_MODEL, device=…)`, `normalize_embeddings=True`, batch
  `EMBED_BATCH_SIZE` (16), `max_seq_length = EMBED_MAX_LENGTH` (1024). Device from `MODEL_DEVICE`; `auto` =
  cuda → mps → cpu. Loaded once per process.
- `FakeEmbedder` (tests): deterministic hash-seeded unit vectors of size `EMBEDDING_DIM`.
- Vector size is fixed by `EMBEDDING_DIM = 1024` (`db/models.py`); a mismatch fails before any DB write.

### 3.8 Storage, idempotency and blue/green

- `db/repositories/corpus.py`:
  `find_document(sha256, chunker_version, embed_model) -> Document | None`,
  `insert_document_with_chunks(document, chunks) -> uuid.UUID` (one transaction, bulk insert),
  `activate(document_id)` (in one transaction: set all `is_active=false`, then this one `true`).
- Key = PDF `sha256` + `CHUNKER_VERSION` + `EMBED_MODEL` (the unique constraint in migration 001). If it exists,
  the CLI logs `ingestion_skipped_existing` and exits 0 — re-running changes nothing.
- A new edition or chunker version is inserted as a new, inactive document; after `make eval-retrieval` passes it
  is switched with `--activate` (or `ingest … --activate`). The old document stays until removed by hand.
- **Migration 002 `002_appendix_chunks`:** widen `ck_chunks_chunk_type` to
  `('preamble','article','schedule','appendix')` and add `appendix_no TEXT NULL`. `downgrade()` deletes
  `chunk_type='appendix'` rows, drops the column and restores the old check (tested in integration, database
  rules). `Chunk` ORM + HLD §10 updated to match.

### 3.9 CLI

```
uv run python -m samvidhan.ingestion.cli ingest <pdf>
    [--version-date YYYY-MM-DD] [--source-url URL] [--activate] [--dry-run] [--out data/processed]
uv run python -m samvidhan.ingestion.cli activate <document_id>
```

- `--dry-run`: extract → validate only; no model load, no DB. Writes `chunks.jsonl` and the report, so the
  segmenter can be iterated on in seconds.
- Exit codes: `0` ok / skipped, `1` validation failed, `2` bad input (not a text PDF, unreadable, bad flags).
- Outputs (gitignored, under `data/processed/`):
  - `chunks.jsonl` — one `ChunkRecord` per line, no embeddings (used by eval and debugging).
  - `ingestion_report.json` — counts per `chunk_type`, Articles found vs expected, `missing`, `duplicates`,
    `unexpected`, omitted list, Appendices found, orphan footnote markers, token histogram (buckets of 100), min/avg/max tokens,
    stage timings.

### 3.10 Local models

`python -m samvidhan.ingestion.models download [--rerank]` (`make models`, `RERANK=1` for the reranker) calls
`huggingface_hub.snapshot_download(EMBED_MODEL, ignore_patterns=["onnx/*", "*.onnx"])` into the HF cache
(`HF_HOME`; `/models/hf` in Docker). Skipping the ONNX copy saves ~2 GB; bge-m3 is ~2.3 GB on disk. The call is a
no-op when the weights are already cached. Logs `model_downloaded` (`model`, `path`, `size_mb`).

### 3.11 Validation

- `eval/fixtures/expected_articles.txt` — one Article number per line, in order, `# omitted` noted for omitted
  ones (506 Articles, 35 omitted; `python -m samvidhan.ingestion.contents <pdf>`). Bootstrapped once by a script that parses the Contents pages (pp. 5–31 — a different code path from the
  body segmenter), then reviewed by hand against the PDF and committed. It is a fixture, not regenerated per run.
- Checks: every expected Article has ≥ 1 chunk (**fail if any missing**); no Article appears as two separate
  segments (**fail on duplicates**; split pieces `#0`, `#1` of one Article are fine); Articles found but not
  expected are reported (fail); exactly one Preamble chunk; all 12 Schedules present; Appendices I–III present; every chunk ≤
  `EMBED_MAX_LENGTH` tokens; `seq` strictly increasing.

### 3.12 Log events

Added to observability.md §1.3: `ingestion_started` (`pdf`, `sha256`, `chunker_version`, `embed_model`),
`ingestion_stage_completed` (`stage`, `count`, `duration_ms`), `ingestion_skipped_existing` (`document_id`),
`ingestion_orphan_marker` WARNING (`page`, `n`), `ingestion_validation_failed` ERROR (`missing`, `duplicates`,
`unexpected`), `ingestion_completed` (`document_id`, `n_chunks`, `duration_ms`, `activated`),
`model_downloaded` (`model`, `path`, `size_mb`).

## 4. Config

| Key | Default | Notes |
|-----|---------|-------|
| `CHUNKER_VERSION` | `v1` | exists; part of the idempotency key — bump on any chunking rule change |
| `EMBED_BATCH_SIZE` | `16` | added in P1.0 |
| `EMBED_MAX_LENGTH` | `1024` | tokens; added in P1.0. Also the per-chunk hard cap |
| `CHUNK_MAX_TOKENS` | `800` | above this an Article is split |
| `CHUNK_TARGET_MIN_TOKENS` | `300` | split-piece lower bound |
| `CHUNK_TARGET_MAX_TOKENS` | `700` | split-piece upper bound |
| `SCHEDULE7_ENTRIES_PER_CHUNK` | `10` | Seventh Schedule grouping |
| `INGEST_MIN_TEXT_PAGE_RATIO` | `0.9` | added in P1.2; share of pages that must have a text layer |

Existing, reused: `EMBED_MODEL`, `MODEL_DEVICE`, `DATABASE_URL`.

> ⚠️ Ingestion is keyed by (sha256, `CHUNKER_VERSION`, `EMBED_MODEL`) only. Changing any `CHUNK_*` key or
> `EMBED_MAX_LENGTH` (or chunking code) **requires bumping `CHUNKER_VERSION`**, otherwise the run is skipped and
> old chunks stay active. Settings reject `CHUNK_TARGET_MIN < CHUNK_TARGET_MAX ≤ CHUNK_MAX ≤ EMBED_MAX_LENGTH`
> violations at startup.

**Extract rules (implemented in P1.2, layout constants in `extract.py`, not config — they describe this PDF's
typography, not tunable behaviour):** a digit-only span smaller than 0.75 × the **document-wide** body size
(9.1 pt) is a footnote number. The body size must be document-wide: on footnote-heavy pages (e.g. p. 88) the
7.9 pt footnote text dominates, and a per-page size would miss 6 pt markers. A horizontal gap > 15 pt starts a new
cell (justified word gaps reach ~12 pt); a gap > 1 pt between spans with no whitespace inserts a space.

## 5. Failure modes

| What | Behaviour |
|------|-----------|
| PDF has < `INGEST_MIN_TEXT_PAGE_RATIO` pages with text (scanned), or cannot be opened | `InvalidSourceError`, exit 2 before any work |
| Body start/end headings not found | exit 2 (wrong document or edition layout changed) |
| Expected Article missing / duplicated / unexpected | `ingestion_validation_failed`, report written, exit 1, **nothing stored** |
| Footnote marker with no footnote on that page (or the reverse) | `ingestion_orphan_marker` WARNING, counted in the report; not fatal. A footnote that continues onto the next page is appended to the previous footnote |
| Chunk over `EMBED_MAX_LENGTH` after splitting | validation failure (exit 1) — never silently truncated |
| Model weights not cached | error telling the user to run `make models` (no silent 2 GB download mid-ingest) |
| Wrong vector size | fail before DB write |
| DB down / insert error | transaction rolls back, exit 1; no half-written document |
| Same PDF + chunker + model re-run | `ingestion_skipped_existing`, exit 0 |

## 6. Acceptance criteria

- [ ] `make models` downloads bge-m3 without the ONNX copy; smoke test gives a 1024-d unit vector (done in P1.0).
- [ ] Full ingest of `data/raw/constitution.pdf` passes validation: 0 missing, 0 duplicate, 0 unexpected Articles
      vs `expected_articles.txt`; 1 Preamble chunk; 12 Schedules; Appendices I–III.
- [ ] Every chunk ≤ 1024 tokens; no split piece of a long Article is < 300 tokens unless it is the whole remainder.
- [ ] Spot metadata: Art. 21 (Part III, "Right to Freedom", 1 chunk); Art. 21A (inserted, amendment note mentions
      the Eighty-sixth Amendment, 2002); Art. 31 (`is_omitted`); Art. 368 (≥ 2 chunks, header repeated,
      `clause_range` set); Preamble (2 amendment notes, Forty-second Amendment); Seventh Schedule List II entry 1
      in `sch-7-list2#0`; Appendix II chunk `app-2#0` with `appendix_no="II"` and the J&K Order title; no
      chunk from inside Appendix I has `chunk_type='schedule'` or an `article_no`.
- [ ] Migration 002 upgrades and downgrades cleanly.
- [ ] No footnote numbers or `N[` markers left in any `text` (regex check over `chunks.jsonl`).
- [ ] Re-running the same ingest is a no-op (no new rows).
- [ ] `--activate` leaves exactly one active document.
- [ ] Full ingest ≤ 20 min on CPU; `--dry-run` ≤ 30 s.
- [ ] Manual spot check of 30 random Articles vs the PDF ≥ 29/30 clean (`eval/reports/ingestion_check.md`).
- [ ] Eval: Recall@5 ≥ 0.90 once the Phase 2 harness exists (evaluation.md).

## 7. Test plan

- **Unit — extract/clean:** header, context line and page-number removal; de-hyphenation; spans merged by y (the
  one-word-per-span Ninth Schedule case); body-range detection.
- **Unit — footnotes:** rule-line split; `N. text` + continuation lines; per-page numbering; marker stripping keeps
  the inserted text; orphan marker; spill to next page; `action`/`act`/`year` parsing.
- **Unit — segmenter (≥ 15 snippets):** plain Article; lettered `21A`; inserted `2[21A. …]`; three-letter
  `243ZH`; omitted `[31. …—Omitted.]`; star-row omission inside an Article; two-line bold heading (Art. 6);
  `PART IVA`, `PART IXA`, `PART IXB`; `CHAPTER I.—…` with title on the next line; group heading; Preamble;
  Seventh Schedule List switch + `2A.` entry; other Schedule paragraphs; Article number in running text
  ("article 5") must not open a segment; `APPENDIX  II` (double space) opens an Appendix; `THE FIRST SCHEDULE`
  and `PA R T I` inside Appendix I stay body text.
- **Unit — Appendix tables:** one-cell-per-line rows rebuilt by y; header repeated per piece.
- **Unit — chunk:** short Article = 1 chunk; long Article split at clauses with header repeated and ids `#0..#n`;
  oversized single clause → sub-clause split; min-size merge; Seventh Schedule groups of 10; `embed_text` exact
  format (snapshot); amendment notes line present/absent.
- **Unit — validate:** missing / duplicate / unexpected detection; over-length chunk.
- **Integration:** migration 002 up/down; a small fixture (text-level, ~10 Articles incl. omitted, inserted, long, plus a few Schedule
  entries) → `FakeEmbedder` → testcontainers Postgres → rows with correct metadata; re-run no-op; activate flips.
- **Slow:** `test_embed_model.py` (real bge-m3); optional full-PDF `--dry-run` test when the PDF is present.

## 8. Open questions / HLD conflicts

1. **Chunk count.** HLD §7.3 expected ~1,200–1,800 chunks. **Actual (first full run, 2026-09-30): 702 chunks**
   — 549 Article (28 Articles split), 133 Schedule, 19 Appendix, 1 Preamble; tokens min 10 / avg 302 / max 799.
   **Decided (owner):** the count is informational (reported, not gated); HLD §7.3 updated.
2. **Appendices** — **decided (owner, 2026-09-30):** in scope, since users may ask about them (§2). Follow-up for
   Phase 4: the router's ref extraction and citation validation must accept `Appendix I–III` refs, and the answer
   prompt should say an Appendix is reproduced for reference (an amending Act / Presidential order), not an
   Article of the Constitution.
3. **Expected-Article list** — **decided (owner, 2026-09-30):** bootstrapped from the Contents pages, reviewed
   by hand, committed (§3.11).
4. **Hyphen vs dash in headings.** The PDF uses `.—` (em dash). A few older Articles may use `.-` or `—` with
   spaces; the regex allows `[—-]{1,2}`. Confirm during the spot check.

## 9. Known limitations (v1, as built)

- **Tables with wrapped cells** (First Schedule, Appendix I land lists) are joined row by row as `a | b`; where the
  two columns wrap independently, a wrapped cell's continuation can land in the next row's text. Readable, but
  table questions ("which territories form Andhra Pradesh") may retrieve less precisely. Revisit with cell-level
  table reconstruction if the retrieval eval shows misses there.
- `amendment_notes[].act/year` parse for 628 of 754 footnotes; the rest say "ibid." (refer to the previous note).
  `text` is always verbatim.
- Art. 232 is listed as omitted in the Contents but its body says "substituted"; it is not flagged `is_omitted`
  (reported under `omitted_mismatch`).
