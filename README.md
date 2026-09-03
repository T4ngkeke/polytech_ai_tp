# Edu-LLM: v9.0 Class-Lab Tutor

---

> **📌 Status — v9.0 is a deletion release.** The exercise half of the system is
> gone: no `Exercises` table, no `Answers` table, no hint generation, no document
> types, no audience filter. What is left is one upload path for course material
> and a retrieval agent that answers from it. The backend and the teacher UI are
> shipped; the retrieval golden set is not built yet.

## 1. Project Overview

**Edu-LLM** is a full-stack educational platform for a controlled classroom. It
keeps the v6 foundation — strict 3-tier RBAC, invite-code joining, real-time
DB-based rate limiting, and **three-tier rule injection** (Class → Lab → Student)
— and the v7 shift from a transparent LLM proxy to a **LangGraph agent**.

The stack stays minimal: **FastAPI + PostgreSQL (with `pgvector`) + React**,
orchestrated by Docker Compose, with **no external message queue** — PostgreSQL
itself is the job queue.

### What v9.0 removed, and why

The system used to model exercises as data. A teacher uploaded a TD or TP, the
worker cut it into `Exercises` rows, and a student asking "how do I do exercise
3?" got that row served back. A `corrigé` upload became `Answers` rows, from
which a big model generated tiered hints that a teacher reviewed and approved.

Two things were wrong with it.

**Generating hints is harder than it looks.** For a real problem the model has to
solve it correctly first — which can mean actually computing — and then package
the solution into tiers without leaking the result. Two hard things stacked, with
no ground truth to check the output against. A wrong hint reads perfectly well
and teaches the student something false.

**A document is not reliably one type.** A single PDF can be mostly lecture with
a few pages of exercises. Everything downstream branched on a type declared at
upload, so a mixed file was mis-processed by construction.

Removing exercises answers both. Nothing is generated that needs to be correct
beyond what the retrieved material already says, and with one path there is
nothing to classify. Teachers who want something specific said about an exercise
write it into their class or lab **rules** — the prompt-injection path that
already exists.

The cut goes deep because segmentation only ever existed to serve those rows. An
`Exercises` row was a hard claim: the system said "this is exercise 3" and served
it with confidence, so the split had to be exactly right. The LLM line
classifier, the numbering-anomaly detector, the boundary cache and the
compare-and-confirm UI were all built to make that claim trustworthy. With no
rows to build, `chunking.py` drops from 499 lines to 138.

### How it works now

- **Agent, not proxy**: `/api/chat/stream` is a LangGraph graph
  (`router → [rag | direct] → tutor → synthesize → SSE`).
- **One upload path**: PDF, Markdown or HTML, all becoming Contextual-Retrieval
  chunks. The form asks for the file, a language and whether to share it
  class-wide. Nothing else.
- **Hybrid retrieval on a single DB**: `pgvector` and Postgres full-text
  (`tsvector`) live in one table, fused with RRF and reranked. No third-party
  vector DB.
- **Deferred, load-aware ingestion**: a `worker` polls an `IngestionJobs` table
  and runs at low priority, pausing while students are actively chatting. Chat
  always preempts; an urgent job (`priority=0`) may bypass both gates.
- **Reuse the same engine, nothing hardcoded**: chat and ingestion call the same
  OpenAI-compatible endpoints read from `SystemConfigs` at runtime. No model is
  hardcoded; **no torch in the image**.
- **Coaching from the router**: the one routing call also scores effort and flags
  answer-seeking, so the Socratic guardrail costs no extra request and applies on
  every route.
- **Vision repair, opt-in**: garbled formulas in printed PDFs are detected
  deterministically, cropped, and re-read by a vision model under a
  transcribe-only prompt. Empty `VLM_MODEL` = off.

### Red lines (security and governance)

- **Tenant isolation in SQL, never in the prompt**: every retrieval is filtered
  by `lab_id` / `class_id` in the **WHERE clause**, re-checked after
  fusion/rerank. Prompt injection cannot widen a query filter. With `audience`
  removed, this filter carries the whole access boundary on its own.
- **Nothing answer-shaped is stored**: there is no solution column and no answer
  table. The one place model-written text enters the indexed material is a VLM
  formula repair, which is transcription, refused if it adds prose, and logged
  before/after for the teacher.
- **Security in the database, pedagogy in the prompt**: the Socratic guardrail is
  a *behavioral* guardrail. If a student talks their way past it the harm is low,
  because there is no stored answer key. Security boundaries must never degrade
  into prompt guardrails.
- **Structured-only governance**: store controlled-vocabulary scores only — never
  free-text judgments about students.
- **Service layer**: all business/DB logic lives in `backend/app/services/`.
  Routers are thin, and no router imports another.
- **Audit integrity**: a student "delete" is a **soft delete** — hidden from the
  student, permanently retained and visible to teacher/admin audit. Only an admin
  can hard-delete.

### Known gaps

- **The ANN and full-text indexes are not created.** `DATABASE.md` describes an
  HNSW index on the embedding and a GIN index on `tsv`; no code builds either, so
  both recalls sequential-scan today. Correct, but it will not stay fast.
- **`RERANK_SCORE_THRESHOLD` ships empty (off).** It needs calibrating against a
  golden set that does not exist yet.
- **Query rewriting is a stub.** `_identity_rewrite` returns the query unchanged,
  so the self-eval retry re-runs an identical search and the bounded
  re-retrieval loop does nothing.

---

## 2. Directory Tree

```text
polytech_ai_tp/
├── backend/
│   ├── app/
│   │   ├── main.py                  # FastAPI app entry point
│   │   ├── config.py                # env-backed settings
│   │   ├── database.py              # SQLAlchemy engine & session (pgvector enabled)
│   │   ├── models.py                # ORM models (15 tables)
│   │   ├── schemas.py               # Pydantic request/response schemas
│   │   ├── auth.py                  # JWT creation & get_current_user deps
│   │   ├── services/                # Business logic layer
│   │   │   ├── class_service.py     # Class CRUD + invite code generation
│   │   │   ├── lab_service.py       # Lab CRUD + active toggle
│   │   │   ├── rule_service.py      # Rule upsert + queries (skill.md = level=class rule)
│   │   │   ├── analytics_service.py # Hierarchical token aggregation
│   │   │   ├── session_service.py   # Session CRUD + student soft-delete
│   │   │   ├── document_service.py  # Upload + enqueue; chunk read/edit; delete
│   │   │   ├── router_service.py    # Log every routing decision
│   │   │   ├── trace_service.py     # TraceBuilder (per-message AgentTraceLog) + health counts
│   │   │   ├── learner_service.py   # Prompt-literacy effort profiles (per student, lab)
│   │   │   ├── model_routing.py     # OpenAI-compatible clients from SystemConfigs
│   │   │   ├── retrieval_service.py # Hybrid (vector+BM25) + rerank, tenant-filtered in SQL
│   │   │   ├── skill_preset_service.py # Teacher skill-preset library + snapshot-copy
│   │   │   └── billing.py           # Weighted billed-token helper (prompt·α + completion·β)
│   │   ├── agent/                   # LangGraph agent
│   │   │   ├── graph.py             # router → [rag | direct] → tutor → synthesize
│   │   │   ├── router.py            # One-call LLM router: route + search terms + coaching
│   │   │   ├── selfeval.py          # Cheap gate + guided good/partial/bad + bounded retry
│   │   │   ├── history.py           # Per-turn chat-history token cap (OOM protection)
│   │   │   └── prompt.py            # Prompt Controller (skill.md → rules → context → history)
│   │   └── routers/
│   │       ├── auth.py              # POST /api/auth/signup, /api/auth/login
│   │       ├── admin.py             # /api/admin/* — no ownership checks
│   │       ├── teacher.py           # /api/teacher/* — ownership-checked
│   │       ├── student.py           # /api/student/* — join, sessions
│   │       └── chat.py              # POST /api/chat/stream (LangGraph agent + SSE)
│   ├── worker/                      # Off-peak ingestion worker
│   │   ├── main.py                  # Polling loop + production model-client glue
│   │   ├── queue.py                 # FOR UPDATE SKIP LOCKED claim + stale-lock reclaim
│   │   ├── ingest.py                # parse → gate → clean → repair → chunk → embed → index
│   │   ├── parsing.py               # PDF/MD/HTML text extraction + character-yield gate
│   │   ├── chunking.py              # TOC drop, code-aware split, size control, dedup
│   │   ├── vlm.py                   # Garbled-formula detection, crop, transcribe, audit
│   │   └── gpu_gate.py              # Chat-load gate (+ optional pynvml GPU signal)
│   ├── tests/                       # pytest suite (SQLite + live pgvector fixtures)
│   ├── seed.py                      # Dev DB bootstrap: schema + SystemConfigs defaults
│   ├── requirements.txt
│   └── Dockerfile                   # Python 3.12-slim — shared by backend & worker
├── frontend/
│   ├── src/
│   │   ├── App.jsx                  # React Router setup
│   │   ├── pages/                   # Login, Register, Account, Chat, Teacher, Admin
│   │   ├── components/
│   │   │   ├── HierarchicalSidebar.jsx  # Role-aware Class→Lab tree
│   │   │   ├── DocumentManager.jsx      # Upload + status list + chunk inspector
│   │   │   ├── SkillPresetManager.jsx   # Teacher skill-preset library
│   │   │   ├── MessageContent.jsx       # Markdown + highlighted code + KaTeX, stream-safe
│   │   │   ├── MainLayout.jsx
│   │   │   └── ProtectedRoute.jsx
│   │   ├── lib/
│   │   │   ├── api.js               # Centralized API client
│   │   │   ├── ingestReport.js      # ingest_report → teacher warnings
│   │   │   └── streamEvents.js      # SSE frame → UI action
│   │   └── store/                   # Zustand stores (Auth, etc.)
│   ├── tailwind.config.js
│   ├── nginx.conf                   # Nginx reverse proxy config for prod
│   └── Dockerfile                   # Node builder + Nginx multi-stage build
├── scripts/
│   └── vlm_detect_spike.py          # Garbled-line detection spike over the real corpus
├── documentation/                   # DATABASE.md · API.md · ARCHITECTURE.md
├── system_architecture.html         # Standalone architecture diagram
└── docker-compose.yml               # Postgres(pgvector) + Backend + Worker + Frontend
```

---

## Documentation

The full technical reference lives under [`documentation/`](documentation/):

- **[Database Schema](documentation/DATABASE.md)** — the 17 SQLAlchemy / PostgreSQL + pgvector tables (SystemConfigs, Users, Documents, DocChunks, Exercises, Answers, trace logs, …).
- **[API Reference](documentation/API.md)** — RBAC (JWT + FastAPI `Depends`) and the full auth / admin / teacher / student routing contract.
- **[Architecture](documentation/ARCHITECTURE.md)** — the `/api/chat/stream` LangGraph agent spec, the ingestion pipeline, prompt-literacy coaching, and the analytics response shape.

---

## 10. Deployment & Run Instructions

The application is fully containerized using Docker, allowing for a single-command deployment.

### Prerequisites
- Docker and Docker Compose installed.
- A running OpenAI-compatible inference engine (your existing chat engine — vLLM/Ollama;
  vLLM/SGLang preferred where guided decoding + priority batching are needed):
  - The **chat path and the ingestion worker share this one engine and the one model
    configured in `SystemConfigs`** — the model is **not hardcoded** anywhere.
  - The active endpoint/model is hot-swappable at runtime (e.g. a smaller model for local
    test, a larger one for production) with zero downtime and no code change.
  - Reached via `host.docker.internal`.
- **Models on that engine** (test setup = one Ollama instance):
  - **Chat:** a generation model — e.g. `qwen3` (35B local / 120B prod) →
    `LLM_MODEL`. Unless split, it also serves the self-eval verdict and
    Contextual-Retrieval generation.
  - **Optional cheap models:** set `INGEST_MODEL` (off-peak Contextual Retrieval)
    and/or `ROUTER_MODEL` (live routing, self-eval, rewrite) to a smaller model —
    e.g. `qwen3:30b` / `mistral-small` — to keep the expensive chat model's RPM
    pool for students. Leave empty to reuse `LLM_MODEL`. Embedding and rerank get
    optional independent endpoints too (`EMBEDDING_URL`, `RERANK_API_KEY`, …).
  - **Tiering convention** (write it into your config; the code only provides the
    slots): **chat = the big model** — non-negotiable, it is the only thing
    students see. **`ROUTER_*` = one small fast model** shared by every live
    auxiliary call (routing, self-eval verdict, rewrite — e.g. a 9B).
    **`INGEST_*` = small model** for context generation. **`AGENT_*` = the big
    model**, for the reasoning behind retrieval — quality over latency.
    Auxiliary work should never run on the chat model's live capacity; an empty
    slot falls back to the main LLM, and the admin UI warns when that silently
    puts auxiliary load on the big model.
  - **Embeddings:** `bge-m3` (1024-dim, matches `EMBEDDING_DIM`) →
    `EMBEDDING_MODEL`. Pull it once: `ollama pull bge-m3`. Indexing and querying
    must use the same model — swapping it invalidates every stored vector, and
    nothing currently detects that.
  - **Reranker:** `bge-reranker-v2-m3`, served behind an OpenAI-compatible rerank
    endpoint (e.g. TEI / Infinity / vLLM / Albert `…/v1/rerank`) → `RERANK_URL` + `RERANK_MODEL`.
    Ollama does not expose a rerank API, so point these at whatever rerank server you run.
    **`RERANK_URL` must be a real rerank endpoint** (the full path that accepts
    `{query, documents}`, e.g. `https://albert.api.etalab.gouv.fr/v1/rerank`) — **not** the chat
    `…/v1` base, which 404s on a rerank body. If unset *or failing* (unreachable /
    4xx-5xx / malformed response), retrieval degrades gracefully to fusion-only ordering and logs
    a warning — a bad reranker never breaks a student's chat.
- `pgvector`-enabled PostgreSQL image (e.g. `pgvector/pgvector:pg16`). Postgres
  full-text (`tsvector`) provides BM25 — no extra extension needed.
- **OOM protection — two layers.** The app caps each message's chat-history size
  (`CONTEXT_MAX_TOKENS`, default 8000 estimated tokens; oldest turns silently forgotten).
  **Also set an engine-side maximum context length** (vLLM `--max-model-len` / SGLang
  `--context-length`) as the final backstop — the app cap must stay below it.

> **Testing note:** hybrid retrieval (pgvector ANN + `tsvector` BM25 + rerank)
> cannot run on SQLite. Those tests use the `pg_session` fixture against a live
> pgvector Postgres (`TEST_PG_URL`, default `localhost:5433/edudb_test`) and skip
> themselves when it is unreachable — so run it, or you will silently test less
> than you think. One way:
>
> ```bash
> docker run -d --name edullm-test-db \
>   -e POSTGRES_USER=postgres -e POSTGRES_PASSWORD=postgres \
>   -e POSTGRES_DB=edudb_test -p 5433:5432 pgvector/pgvector:pg16
> pytest backend/tests -q
> ```
>
> All model calls (embed / rerank / generate) are replaced by injected fakes, so
> no inference engine is needed to run the suite.

### Persistent Storage (Docker volumes)
Named volumes persist data across container rebuilds:
- `pg_data` → PostgreSQL data directory.
- `documents_data` → uploaded course documents (`Documents.storage_path` points inside this
  volume). Mounted into **both** `backend` (for upload) and `worker` (for ingestion).

### 🚀 Running with Docker (Production/Demo Mode)
Spin up Postgres(pgvector) + FastAPI backend + ingestion worker + Nginx/React frontend:

```bash
docker compose up -d --build
```
- **Frontend**: `http://localhost` (Port 80).
- **Backend API**: Proxied through Nginx via `/api/`, or directly at `http://localhost:8000`.
- **Worker**: no exposed port; `restart: always` polling loop.
- **Database**: Port mapped on `5432`.

#### 🔐 Initialize Database (First Time Only)
After starting the containers for the first time, enable pgvector + seed initial accounts:
```bash
docker compose exec backend python -m backend.seed
```
**Default Accounts Created:**
- `admin` / `admin123`
- `teacher` / `teacher123`
- `student1` / `student123`
- `student2` / `student123`

*(Note: The backend & worker containers use `host.docker.internal` to reach the host's local
inference engine without complex networking. Both share the one engine/model set in
`SystemConfigs` — pick whatever model fits the box; nothing is hardcoded.)*

### 🛠️ Running Natively (Development Mode)
If you prefer running services outside of Docker for development:

1. **Database**: Start a `pgvector` Postgres server (`docker compose up -d postgres`).
2. **Backend (from project root)**:
   ```bash
   pip install -r backend/requirements.txt

   # Initialize the database (first time only)
   python -m backend.seed

   # Start the API
   uvicorn backend.app.main:app --reload --port 8000
   ```
3. **Worker (separate process, from project root)**:
   ```bash
   python -m backend.worker.main
   ```
4. **Frontend**:
   ```bash
   cd frontend
   npm install
   npm run dev
   ```
   Access the dev server at `http://localhost:5173`.
