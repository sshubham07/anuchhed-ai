<div align="center">

# 🇮🇳 Samvidhan RAG

**Ask the Constitution of India anything — get answers cited to the exact Article.**

![Phase](https://img.shields.io/badge/phase-1%20ingestion%20%E2%9C%93-2ea44f)
![Tests](https://img.shields.io/badge/tests-102%20passing-2ea44f)
![Chunks](https://img.shields.io/badge/chunks%20in%20pgvector-702-blue)
![Articles](https://img.shields.io/badge/articles-506%2F506-blue)
![Python](https://img.shields.io/badge/python-3.12-3776AB?logo=python&logoColor=white)
![Postgres](https://img.shields.io/badge/postgres-16%20%2B%20pgvector-336791?logo=postgresql&logoColor=white)

<sub>Informational only — not legal advice.</sub>

</div>

---

## 🗺️ Where we are

<a href="docs/diagrams/roadmap.svg"><img src="docs/diagrams/roadmap.svg" width="100%" alt="Roadmap: 1 Chunk & store in DB (done) → 2 Eval harness (next) → 3 Query & retrieval → 4 LLM & router → 5 Chat API → 6 UI → 7 v1.0"></a>

<sub>🖱️ Click any diagram to open it full size · click a phase below to see its steps</sub>

<details>
<summary>✅ <b>1 · Chunking & storing in DB</b> — done</summary>

1. ⚙️ Project setup — config, logging, Docker Postgres + pgvector, migrations, FastAPI health checks
2. 📕 Official PDF (edition as on 1 May 2024) + local models (`make models`)
3. 🔤 Extract text rows with font size, bold and position (PyMuPDF)
4. 🧹 Clean — drop running headers and page numbers
5. 📝 Footnotes — attach 754 amendment notes
6. 🧱 Segment — Part › Chapter › Article / Schedule / Appendix
7. ✂️ Chunk — one chunk per Article, long ones split at clauses
8. ✅ Validate — 506 / 506 Articles, none missing or duplicated
9. 🧠 Embed with bge-m3 (1024-d)
10. 🐘 Store in Postgres — vector + full-text index, idempotent re-runs
11. 🧪 Tests — unit, integration and real-PDF runs (102 passing)

</details>

<details>
<summary>⏳ <b>2 · Eval harness</b> — next</summary>

1. ✍️ Hand-write 60 golden Q&A cases
2. 🤖 Generate +90 synthetic cases, human-reviewed
3. 💬 20 multi-turn conversations
4. ✂️ Dev / test split (70 / 30)
5. 📏 Metrics — Recall@k, Hit@1, MRR, nDCG
6. 📊 `eval.run --suite retrieval` with reports and baseline compare
7. 🔁 CI job on every PR

</details>

<details>
<summary>⬜ <b>3 · Query & retrieval</b></summary>

1. 🧭 Dense search — query embedding + HNSW cosine
2. 🔤 Lexical search — Postgres full-text
3. 🔀 Fuse both lists with RRF
4. 🎯 Exact lookup — "Article 21A" → that chunk
5. ⚖️ Rerank with bge-reranker (15 → top 5)
6. 🧪 Ablation runs and confidence threshold tuning

</details>

<details>
<summary>⬜ <b>4 · LLM & router</b></summary>

1. 🔌 LiteLLM client — Groq primary, Gemini fallback, every call logged
2. 🔀 Router — rewrites the question and picks the route
3. 🔍 HyDE and multi-part question splitting
4. 🤖 Answer prompt — answers only from retrieved chunks
5. 📌 Citation check against retrieved Articles
6. 🕸️ LangGraph pipeline wiring the steps together

</details>

<details>
<summary>⬜ <b>5 · Chat API</b></summary>

1. 🗂️ Sessions, messages and feedback tables
2. 🧠 Chat memory — last messages + Articles discussed
3. 📡 `POST /v1/chat` with streaming
4. 🚦 Rate limits and message size limits

</details>

<details>
<summary>⬜ <b>6 · UI</b></summary>

1. 💬 Streamlit chat with streaming answers
2. 📎 Citation chips → full Article text
3. 👍 Feedback buttons and a debug panel

</details>

<details>
<summary>⬜ <b>7 · v1.0 release</b></summary>

1. 📊 Full eval with RAGAS on the test split
2. 🏋️ Load test and failure drills
3. 🔒 Security pass
4. 🏷️ Tag `v1.0.0`

</details>

<sub>📋 [Full plan](docs/plans/implementation-plan.md) · 🏛️ [Design (HLD)](docs/design/HLD.md) · 🧭 [Decisions (ADRs)](docs/adr/README.md)</sub>

---

## 🧩 The big picture

<a href="docs/diagrams/big-picture.svg"><img src="docs/diagrams/big-picture.svg" width="100%" alt="Big picture: ① PDF is chunked and stored in pgvector; ③④ a question goes through router, hybrid search, reranker and answer LLM"></a>

---

## 📄 How chunking works

> **One chunk per Article** — the Constitution's own structure, not fixed-size windows. <sub>[Why? → ADR-0001](docs/adr/0001-structure-aware-chunking.md)</sub>

<a href="docs/diagrams/chunking-pipeline.svg"><img src="docs/diagrams/chunking-pipeline.svg" width="100%" alt="Chunking pipeline: PDF → extract → clean → footnotes → segment → chunk → validate → embed → Postgres"></a>

<details>
<summary>🔍 <b>Click — what each step does</b></summary>

| Step | Module | Key trick |
|:--|:--|:--|
| 🔤 Extract | [`extract.py`](src/samvidhan/ingestion/extract.py) | Footnote numbers → `{{fn:N}}` tokens; rows rebuilt by position |
| 🧹 Clean | [`clean.py`](src/samvidhan/ingestion/clean.py) | Body starts at **PREAMBLE**, runs to the end |
| 📝 Footnotes | [`footnotes.py`](src/samvidhan/ingestion/footnotes.py) | Numbered by position — survives PDF typos |
| 🧱 Segment | [`segment.py`](src/samvidhan/ingestion/segment.py) | Headings found by **position**, not bold |
| ✂️ Chunk | [`chunk.py`](src/samvidhan/ingestion/chunk.py) | Long Articles split at clauses `(1)`, `(2)`… |
| ✅ Validate | [`validate.py`](src/samvidhan/ingestion/validate.py) | Every Article present, none twice |
| 🧠 Embed | [`embed.py`](src/samvidhan/ingestion/embed.py) | Local model, no API cost |
| 🐘 Store | [`corpus.py`](src/samvidhan/db/repositories/corpus.py) | Same PDF twice → no-op |

📘 Full detail: [ingestion spec](docs/specs/ingestion.md)
</details>

### ✂️ Chunking rules

<a href="docs/diagrams/chunking-rules.svg"><img src="docs/diagrams/chunking-rules.svg" width="100%" alt="Chunking rules per segment type"></a>

### 🔬 Anatomy of one chunk

<a href="docs/diagrams/chunk-anatomy.svg"><img src="docs/diagrams/chunk-anatomy.svg" width="100%" alt="Anatomy of one chunk"></a>

---

## 📊 What landed in the database

<table>
<tr>
<td width="50%">

<a href="docs/diagrams/chunks-by-type.svg"><img src="docs/diagrams/chunks-by-type.svg" width="100%" alt="702 chunks by type"></a>

</td>
<td width="50%">

<a href="docs/diagrams/chunk-sizes.svg"><img src="docs/diagrams/chunk-sizes.svg" width="100%" alt="Chunk size histogram"></a>

</td>
</tr>
</table>

| | | | |
|:-:|:-:|:-:|:-:|
| 📚 **506** Articles | ✂️ **28** split | 🚫 **34** omitted | 📋 **12** Schedules |
| 📎 **3** Appendices | 📝 **754** footnotes | 📏 **799** max tokens | ⏱️ **~2 min** full ingest |

---

## 🧪 How it was tested

<a href="docs/diagrams/tests.svg"><img src="docs/diagrams/tests.svg" width="100%" alt="Test pyramid: unit, integration, real PDF"></a>

### ✅ Results

| Check | Result |
|:--|:-:|
| All tests (unit + integration + real PDF) | 🟢 **102 / 102** |
| Articles found vs Contents list | 🟢 **506 / 506** |
| Missing · duplicate · unexpected | 🟢 **0 · 0 · 0** |
| Chunks over the 1,024-token limit | 🟢 **0** |
| Every vector 1024-d, unit length | 🟢 |
| Re-running the same PDF | 🟢 no-op |
| ruff · mypy --strict | 🟢 clean |

<details>
<summary>🐛 <b>Click — tricky things the real PDF taught us (and the tests that pin them)</b></summary>

| 😬 Surprise in the PDF | 🔧 Fix |
|:--|:--|
| Headings are **not bold** | Detect by centered position |
| Omitted Articles have **no bold** | Match `21A.` pattern at heading indent |
| Footnote `1` printed on 6 pt vs 7.9 pt text | Compare to the **document** body size |
| Contents say `243-I`, body says `243I` | Normalise both |
| Footnote numbered `2.` twice (typo) | Number footnotes **by position** |
| `1 S.C.C. 362` looked like footnote 1 | Undotted numbers need a marker on the page |
| Part VII omitted entirely → no Art. 238 | Stub chunk: *"Omitted."* + amendment note |
| Appendix I has its own "FIRST SCHEDULE" | Structure rules off inside Appendices |

Tests: [`tests/unit`](tests/unit) · [`tests/integration`](tests/integration)
</details>

### 🔎 Quick search check (vectors only, before Phase 3)

| 🙋 Question | 🥇 Top hit |
|:--|:--|
| Is education a fundamental right? | **Art. 21A** · Right to education |
| How can the Constitution be amended? | **Art. 368** |
| Which amendment removed the right to property? | **Art. 31** (omitted) |
| What happened to J&K special status in 2019? | **Appendix III** · **Appendix II** · **Art. 370** |

---

## 🚀 Run it

<details>
<summary>▶️ <b>Click — 4 commands</b></summary>

```bash
make setup                      # install + create .env (set POSTGRES_PASSWORD)
make up && make migrate         # Postgres + pgvector on :5433
make models                     # download bge-m3 (~2.3 GB, once)
make ingest ARGS=--activate     # PDF in data/raw/ → 702 chunks in Postgres
```

`make ingest ARGS=--dry-run` → writes `data/processed/chunks.jsonl` + `ingestion_report.json`, no DB.
</details>

---

## 🧰 Stack

![FastAPI](https://img.shields.io/badge/FastAPI-009688?logo=fastapi&logoColor=white)
![LangGraph](https://img.shields.io/badge/LangGraph-1C3C3C)
![pgvector](https://img.shields.io/badge/pgvector-336791?logo=postgresql&logoColor=white)
![bge--m3](https://img.shields.io/badge/embeddings-bge--m3-orange)
![PyMuPDF](https://img.shields.io/badge/PyMuPDF-PDF-red)
![LiteLLM](https://img.shields.io/badge/LiteLLM-Groq%20%C2%B7%20Gemini-purple)
![Streamlit](https://img.shields.io/badge/Streamlit-FF4B4B?logo=streamlit&logoColor=white)
![Docker](https://img.shields.io/badge/Docker-2496ED?logo=docker&logoColor=white)

## 📚 Docs

| 🏛️ [Design](docs/design/HLD.md) | 🧭 [ADRs](docs/adr/README.md) | 📘 [Specs](docs/specs/) | 📋 [Plan](docs/plans/implementation-plan.md) | 📐 [Standards](docs/standards/engineering-standards.md) | 🤝 [Contributing](CONTRIBUTING.md) |
|:-:|:-:|:-:|:-:|:-:|:-:|
