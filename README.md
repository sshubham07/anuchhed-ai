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

```mermaid
flowchart LR
    P0["⚙️ 0 · Foundation"]:::done --> P1["📄 1 · Ingestion & chunking"]:::done --> P2["🎯 2 · Eval harness"]:::next --> P3["🔎 3 · Retrieval"]:::todo --> P4["🤖 4 · LLM & router"]:::todo --> P5["💬 5 · Chat API"]:::todo --> P6["🖥️ 6 · UI"]:::todo --> P7["🚀 7 · v1.0"]:::todo
    classDef done fill:#2ea44f,color:#fff,stroke:#1b7f3b
    classDef next fill:#f9c513,color:#000,stroke:#b08800
    classDef todo fill:#eaeef2,color:#57606a,stroke:#d0d7de
```

<sub>📋 [Full plan](docs/plans/implementation-plan.md) · 🏛️ [Design (HLD)](docs/design/HLD.md) · 🧭 [Decisions (ADRs)](docs/adr/README.md)</sub>

---

## 🧩 The big picture

```mermaid
flowchart LR
    PDF[/"📕 Official PDF<br/>402 pages"/] ==> ING["📄 Ingestion<br/><b>built ✓</b>"]:::done ==> DB[("🐘 Postgres + pgvector<br/>702 chunks")]:::done
    Q(["🙋 Question"]) --> R["🔀 Router LLM"] --> S["🔎 Hybrid search<br/>vector + full-text"] --> RR["⚖️ Reranker"] --> A["🤖 Answer LLM"] --> C(["💬 Answer + citations"])
    DB -.-> S
    classDef done fill:#2ea44f,color:#fff
```

---

## 📄 How chunking works

> **One chunk per Article** — the Constitution's own structure, not fixed-size windows. <sub>[Why? → ADR-0001](docs/adr/0001-structure-aware-chunking.md)</sub>

```mermaid
flowchart TD
    A["📕 PDF<br/><b>402 pages</b>"] -->|"PyMuPDF · 2 s"| B["🔤 Extract<br/><b>14,601 rows</b><br/>font size · bold · position"]
    B --> C["🧹 Clean<br/><b>12,948 body rows</b><br/>headers & page numbers dropped"]
    C --> D["📝 Footnotes<br/><b>754 amendment notes</b>"]
    D --> E["🧱 Segment<br/><b>525 segments</b><br/>Part › Chapter › Article"]
    E --> F["✂️ Chunk<br/><b>702 chunks</b><br/>~300 tokens avg"]
    F --> G{"✅ Validate<br/>506 / 506 Articles"}
    G -->|pass| H["🧠 Embed · bge-m3<br/><b>702 × 1024-d</b> · 97 s"]
    G -->|fail| X["⛔ Stop<br/>nothing stored"]
    H --> I[("🐘 Postgres<br/>vector + full-text index")]
    style G fill:#fff8c5,stroke:#b08800
    style X fill:#ffebe9,stroke:#cf222e
    style I fill:#ddf4ff,stroke:#0969da
```

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

```mermaid
flowchart LR
    S{{"Segment"}} --> P["📜 Preamble"] --> P1["1 chunk"]
    S --> A["⚖️ Article"] --> L{"> 800<br/>tokens?"}
    L -->|no| A1["1 chunk"]
    L -->|yes| A2["split at clauses<br/>300–700 each<br/>header repeated"]
    S --> O["🚫 Omitted Article"] --> O1["1 chunk<br/>is_omitted ✓"]
    S --> SC["📋 Seventh Schedule"] --> SC1["10 entries<br/>per chunk"]
    S --> OS["📑 Other Schedules<br/>& Appendices"] --> OS1["packed by<br/>paragraph"]
    style A2 fill:#fff8c5
    style O1 fill:#ffebe9
```

### 🔬 Anatomy of one chunk

```mermaid
flowchart LR
    subgraph C ["📦 chunk art-21A#0"]
        direction TB
        H["🧭 <b>Header</b><br/>Part III › Right to Freedom › Article 21A"]
        N["📝 <b>Amendment notes</b><br/>Ins. by 86th Amendment Act, 2002"]
        T["📖 <b>Text</b><br/>The State shall provide free and<br/>compulsory education…"]
        M["🏷️ <b>Metadata</b><br/>article_no · part · is_omitted · seq"]
    end
    H & N & T ==> E["🧠 <b>embed_text</b><br/>→ vector + keyword search"]
    T ==> D["🖥️ <b>text</b><br/>→ citation panel"]
    M ==> L["🎯 <b>exact lookup</b><br/>“Article 21A” → this chunk"]
    style C fill:#f6f8fa,stroke:#d0d7de
    style E fill:#ddf4ff,stroke:#0969da
    style D fill:#dafbe1,stroke:#2ea44f
    style L fill:#fff8c5,stroke:#b08800
```

---

## 📊 What landed in the database

<table>
<tr>
<td width="50%">

```mermaid
pie showData title 702 chunks by type
    "Article" : 549
    "Schedule" : 133
    "Appendix" : 19
    "Preamble" : 1
```

</td>
<td width="50%">

```mermaid
%%{init: {"themeVariables": {"xyChart": {"plotColorPalette": "#0969da"}}}}%%
xychart-beta
    title "Chunk size (tokens)"
    x-axis ["0", "100", "200", "300", "400", "500", "600", "700"]
    y-axis "chunks" 0 --> 170
    bar [100, 159, 142, 111, 58, 52, 67, 13]
```

</td>
</tr>
</table>

| | | | |
|:-:|:-:|:-:|:-:|
| 📚 **506** Articles | ✂️ **28** split | 🚫 **34** omitted | 📋 **12** Schedules |
| 📎 **3** Appendices | 📝 **754** footnotes | 📏 **799** max tokens | ⏱️ **~2 min** full ingest |

---

## 🧪 How it was tested

```mermaid
flowchart TB
    subgraph U ["⚡ Unit · 85 tests (56 ingestion) · 0.5 s"]
        direction LR
        u1["🔤 extract 13"] ~~~ u2["🧹 clean 5"] ~~~ u3["📝 footnotes 5"] ~~~ u4["🧱 segment 14"] ~~~ u5["✂️ chunk 9"] ~~~ u6["✅ validate 5"] ~~~ u7["📑 contents 3"] ~~~ u8["🔗 pipeline 2"]
    end
    subgraph I ["🐳 Integration · real Postgres in Docker"]
        direction LR
        i1["🐘 store & activate 4"] ~~~ i2["🔁 migrations up/down"]
    end
    subgraph R ["📕 Real PDF · slow"]
        direction LR
        r1["🔤 extraction 5"] ~~~ r2["🔗 full pipeline 4"] ~~~ r3["🧠 bge-m3 smoke 1"]
    end
    U --> I --> R
    style U fill:#dafbe1,stroke:#2ea44f
    style I fill:#ddf4ff,stroke:#0969da
    style R fill:#fbefff,stroke:#8250df
```

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
