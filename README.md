# Edu-LLM: v8.0 Agentic Class-Lab Architecture
---

> **📌 Status — this README describes v8.0. The backend (PR-2a router overhaul + PR-2b hint
> system / human-loop) is shipped on branch `v7.2-upgrade`; the v8.0 frontend and the
> retrieval golden-set eval are still in progress.** The authoritative design log is
> `V8.0_PLAN.md` + `v8_workflow.html`.
>
> **What v8.0 changed vs v7.3** (all backend, shipped):
> - **Router** — the regex + `bge-m3` kNN router is gone; one **LLM-router call** per message
>   classifies the route AND carries dialogue state (a sticky exercise fills a blank number but
>   never picks the route; unresolvable numbers → a clarifying question, never a guess). See §6.
> - **Hints** — teacher-driven lifecycle (`none → generating → pending_review → approved/failed`).
>   Ingestion never generates hints; a teacher triggers generation, reviews per-exercise, and
>   only **approved** hints reach students (retrieval gate). `Exercises.hints` is now a tiered
>   JSON array. See §3.11 and §8.
> - **Answers** — stored in a main-DB `Answers` table for **offline** hint generation (the
>   physical “vault” was abandoned — answers aren’t secret). The student path
>   (chat / agent / prompt / retrieval) never imports the `Answer` model, enforced by
>   `test_answers_isolation`. `doc_type` gains `corrigé` (a standalone answer file). See §3.13.
> - **Telemetry** — `RouterQueryLog` redefined (route / exercise_number / number_source /
>   degraded / is_test / model_name / prompt_version / latency_ms); new `AgentTraceLog`
>   (per-message degradation flags / node latencies / self-eval verdict). See §3.14–15.
> - **Human loop** — teacher **test-drive** sessions (`Session.is_test`, membership bypassed,
>   draft-hint preview, excluded from analytics/LearnerProfile), student 👍/👎 **feedback**,
>   an admin **health panel** (recent degradation counts), and a **teaching-hotspot** panel. See §11.

## 1. Project Overview

**Edu-LLM (v7.3 Agentic Class-Lab Architecture)** is a full-stack educational platform
designed for a controlled classroom environment. It keeps the entire v6 foundation —
strict 3-tier RBAC, Invite-Code zero-friction joining, real-time DB-based rate limiting,
and **Three-Tier Rule Injection** (Class → Lab → Student) — and the v7 shift from a
**transparent LLM proxy** to a **LangGraph Agent**. v7.1 upgrades the retrieval core from
"naive chunk-and-dump + keyword routing" into **PDF-only structured ingestion + multilingual
routing + high-quality hybrid retrieval + an LLM-self-eval bounded agent loop**, all on a
local model and an **off-peak document-ingestion pipeline** (a docker-compose `worker`) that
never preempts live student inference.

The stack stays minimal: **FastAPI + PostgreSQL (with `pgvector`) + React**, orchestrated
by Docker Compose, with **no external message queue** (PostgreSQL itself is the job queue).

**v7 Core Shift (still in force):**
- **Agent, not proxy**: `/api/chat/stream` is a LangGraph graph
  (**[v8.0]** `router → [exercise → tutor | rag | direct | clarify] → Synthesize → SSE`).
- **Hybrid retrieval on a single DB**: structured tables + `pgvector` + Postgres full-text
  (`tsvector`) live together, so exercise lookups and concept RAG JOIN naturally. No
  third-party vector DB.
- **Deferred, load-aware ingestion**: a `worker` service polls an `IngestionJobs` table and
  runs ingestion at low priority, pausing while students are actively chatting (live chat-load
  gate; GPU utilization is an optional extra signal on local engines) — chat always preempts.
- **Reuse the same engine, nothing hardcoded**: chat *and* ingestion call the same
  OpenAI-compatible endpoints read from `SystemConfigs` at runtime — generation, embeddings
  (`bge-m3`), and reranking (`bge-reranker-v2-m3`). No second model is spun up; no model is
  hardcoded; **no torch in the image**.

**v7.1 Retrieval Upgrade (this release):**
- **PDF-only structured ingestion**: pymupdf text extraction + a cheap character-yield gate
  (bad PDFs are flagged `needs_review`, never silently ingested); deterministic **type +
  audience routing** by upload metadata (`CM/TD/TP`, `student/teacher`); **structure-aware
  chunking** that never cuts an exercise or code block and drops the table-of-contents.
- **Contextual Retrieval**: the worker LLM-generates each chunk's in-document context and
  prepends it before embedding; the original text is still stored for citation. This is what
  makes context-poor CM slides retrievable.
- **[v8.0] One-call LLM router with dialogue state**: a single 9B `json_schema` call per message
  picks the route (`exercise`/`rag`/`direct`/`clarify`) and resolves the exercise number, carrying
  a sticky exercise from the session (fills a blank number, never picks the route). Unresolvable
  numbers ask a clarifying question rather than guess. Timeout/failure → safe `rag` fallback.
  (Replaced the v7.1 regex + `bge-m3` kNN exemplar router.)
- **Hybrid + rerank + bounded self-eval**: vector + BM25 recall → RRF fusion → reranker →
  cheap gate → guided LLM self-eval (`good/partial/bad`) → bounded re-retrieval (admin-
  configurable rounds, default 1) → disclaimer-tagged answer if material is still thin.

**v7.2 Upgrade (this release):**
- **Self-service account management**: any logged-in user can change their own password
  (old-password check); admins can reset any user's password (no old password). No email/OTP
  recovery — out of scope for the controlled classroom.
- **Full model-routing table in `SystemConfigs`**: generation, **embedding**, **rerank**,
  **ingestion**, and **router** each get their own optional endpoint/key/model. Ingestion and
  router default to **empty → fall back to the main LLM**, so the cheap-model split is opt-in
  and existing behaviour is unchanged. Lets the expensive 120B serve chat while a cheap 30B
  does off-peak Contextual-Retrieval and live router intent — without sharing one RPM pool.
- **Cost-aware token accounting**: quota is charged on **weighted** tokens
  `billed = prompt·α + completion·β` (admin-set `TOKEN_ALPHA`/`TOKEN_BETA`; default `0.2`/`1.0`)
  — prefill is cheaper than decode, so it no longer counts the same. Raw prompt/completion
  counts are still stored per message.
- **LLM-assisted exercise routing**: the deterministic exercise-number regex stays the
  fast-path; when it misses but the query looks exercise-shaped, a cheap `ROUTER_MODEL`
  disambiguates. Exercises gain a `number_normalized` integer (Roman/Arabic/`3.1` all map to
  one canonical number) so ingest-side extraction and query-side matching share one
  normalization function.
- **Per-class instructor style — preset library**: teachers maintain a private library of
  reusable `skill` presets and snapshot-copy one into a class's `level=class` rule. The prompt
  injection path is unchanged (it still reads Rules); presets are just templates.
- **Cheaper, length-safe Contextual Retrieval**: per-chunk context is generated from the
  chunk's **own section** (not `full_text[:8000]`), so long documents no longer get a context
  hallucinated from the document's opening, and the call fits any small model. Runs on the
  config-driven `INGEST_MODEL`.
- **Student-facing markdown rendering**: the chat UI renders markdown, syntax-highlighted code
  blocks (with copy), and KaTeX math, with stream-safe incremental rendering.

**v7.3 Upgrade (this release) — pipeline hardening:**
- **Single-source exercise segmentation**: TD/TP segmentation happens **once**, deterministically
  (expanded boundary regex — Arabic / Roman / `Question n` / bare-numbered headings). Exercises
  are built **deterministically** from the segmentation: `number` = the regex-captured label,
  `statement` = the segment body — **no LLM on the happy path** (the whole-document LLM
  extractor, the root cause of wrong exercise numbers, is deleted). An LLM (`resegment_fn`, the
  `INGEST_MODEL`) is consulted **only** when the numbering looks wrong: duplicate normalized
  numbers (sub-questions mistaken for exercises — `1,1,2,3`) or zero boundaries. It names the
  true boundary heading lines verbatim; splitting stays deterministic; its failure keeps the
  regex result. Sub-questions fold into their parent exercise.
- **Strict split (red line)**: concept questions are answered from **CM chunks only** (hybrid
  RAG) and exercises from the **Exercises table only** (agentic search) — so **TD/TP never
  produce DocChunks** (no embedding, no tsv; `IngestionPlan.produce_chunks`) and DocChunks come
  from CM only. Leakage is cut on the write side; the read path needs no trust.
- **Ingestion cleanup**: repeated header/footer lines stripped (two narrow rules — byte-identical
  repeats and pure pagination lines — so numbered structure is never eaten); TOC pages removed
  once for the whole pipeline; CM chunks get min/max size control (merge tiny, sentence-split
  huge — 120–1600 chars) with **code-aware paragraph splitting** (blank lines inside indented
  blocks don't cut); document-level paragraph dedup; `normalize_exercise_number()` strips
  `TD n`/`TP n` prefixes before reading the number ("TD 2 – Exercice 3" is exercise 3, not 2);
  per-chunk Contextual-Retrieval calls run concurrently (bounded gather).
- **Reconciliation report**: ingestion writes `Documents.ingest_report` (numbering anomaly,
  gaps, in-lab number collisions, whether the LLM re-segmented, exercise count) — the teacher
  audits a warning list instead of re-reading the document.
- **Teacher-edit protection**: chunk/exercise rows edited by a teacher (`edited_by_teacher`)
  survive idempotent re-ingestion instead of being wiped by delete-and-rebuild; an edited
  exercise whose number vanished from a re-export is kept as an extra row, never silently dropped.
- **Retrieval fixes**: BM25 was silently dead for natural-language French questions
  (`simple` config + `plainto_tsquery` AND semantics — one extra query word zeroed the recall);
  now **OR semantics** over content words with per-document **`language`** configs (fr default /
  en), stemming included ("fonction" matches "fonctions"), same config on ingest and query
  sides. The reranker now scores the **augmented** text (context + content) — the same text
  recall matched on — instead of vetoing the context-poor slide chunks Contextual Retrieval was
  built to save. A **`RERANK_SCORE_THRESHOLD`** injection gate is wired end-to-end in the
  retrieval layer (below-threshold chunks dropped, `all_filtered` signalled) and **ships
  disabled** until calibrated on a golden set; the zero-context disclaimer branch that consumes
  the signal lands in v8.0.
- **Model-routing slots (v8.0 groundwork)**: `HINT_MODEL/URL/KEY` (reserved for the v8.0
  answer→hints generator; empty → main LLM), `RERANK_SCORE_THRESHOLD`, `HINT_MAX_SAMPLES`,
  `INGEST_TOKEN_BUDGET` — all admin-configurable. The admin UI renames the router slot to
  **"Auxiliary model (router · self-eval · rewrite)"** and warns when it is empty (auxiliary
  load silently landing on the big chat model). Tiering convention: chat = the big model;
  live auxiliary = small fast model; `INGEST_*` = small; `HINT_*` = big, off-peak only.

*(v8.0 — next release — replaces the router with a single LLM call carrying dialogue state,
adds answer-grounded tiered hints with a teacher review lifecycle, per-message trace telemetry,
and the zero-context disclaimer branch. See `V8.0_PLAN.md`.)*

**v6 Principles (still in force):**
- **Service Layer**: All business/DB logic lives in `backend/app/services/`. Routers are thin.
- **No Cross-Router Calls**: routers call services independently — no router imports another.
- **Audit Integrity**: A student "delete" is a **soft delete** (`is_deleted=True`) — the session is hidden from the student but permanently retained and fully visible to teacher/admin audit (flagged as deleted). Only admin can hard-delete. History is immutable and teacher-auditable.
- **Hierarchical UI**: Class → Lab tree navigation across all roles.

**Red Lines (v7.1, revised v7.3 — security/governance):**
- **Tenant isolation in SQL, never in the prompt**: every retrieval is filtered by
  `lab_id` / `class_id` (and `audience` for students) in the **SQL WHERE clause** — re-checked
  after fusion/rerank. Prompt-injection cannot break a query filter. No cross-class / cross-lab
  access.
- **Answers never on the student path**: there is no `Exercise.solution` column; only
  student-safe **statements** and **approved hints** are ever surfaced. **[v8.0]** uploaded
  answers are stored in an `Answers` table for **offline** hint generation, read only by the
  teacher/worker side — the chat / agent / prompt / retrieval modules never import the `Answer`
  model (enforced by the `test_answers_isolation` architecture test). Any non-derivable
  instructor convention goes in the **class-level prompt**, not a per-exercise answer field.
- **[v7.3] Strict split**: **TD/TP documents never produce DocChunks** — exercises live only in
  the structured `Exercises` table (agentic search) and the RAG chunk store is fed by CM only.
  Exercise text cannot surface through concept retrieval; the separation is enforced at write
  time, not by query-time filters.
- **Security in the database, pedagogy in the prompt**: the Socratic guardrail is a *behavioral*
  guardrail; if a student jailbreaks it the harm is low (no stored answer key). Security
  boundaries must never degrade into a prompt guardrail.
- **Structured-only governance**: store controlled-vocabulary scores only — never free-text
  judgments about students.

---

## 2. Directory Tree

```text
polytech_ai_tp/
├── backend/
│   ├── app/
│   │   ├── __init__.py
│   │   ├── main.py                  # FastAPI app entry point
│   │   ├── database.py              # SQLAlchemy engine & session (pgvector enabled)
│   │   ├── models.py                # ORM models (…, Documents, DocChunks, Exercises, IngestionJobs, RouterQueryLog)
│   │   ├── schemas.py               # Pydantic request/response schemas
│   │   ├── auth.py                  # JWT creation & get_current_user deps
│   │   ├── services/                # Business logic layer
│   │   │   ├── __init__.py
│   │   │   ├── class_service.py     # Class CRUD + invite code generation
│   │   │   ├── lab_service.py       # Lab CRUD + active toggle
│   │   │   ├── rule_service.py      # Rule upsert + queries (skill.md = level=class rule)
│   │   │   ├── analytics_service.py # Hierarchical token aggregation
│   │   │   ├── session_service.py   # Session CRUD + student soft-delete (hide; retained for audit)
│   │   │   ├── document_service.py  # [v7] Upload + enqueue; chunk/exercise reads; [v8.0] hint-gen enqueue, approve, exercise add/delete
│   │   │   ├── answer_service.py    # [v8.0] pair Answers↔Exercises by number; list lab answers (teacher side)
│   │   │   ├── router_service.py    # [v8.0] log every routing decision (RouterQueryLog v2); exercise hotspots
│   │   │   ├── trace_service.py     # [v8.0] TraceBuilder (per-message AgentTraceLog) + health degradation counts
│   │   │   ├── learner_service.py   # [v7] prompt-literacy effort profiles (per student, lab)
│   │   │   ├── model_routing.py     # [v7.1] OpenAI-compatible clients from SystemConfigs (generate / embed / rerank / ingest / router / hint)
│   │   │   ├── retrieval_service.py # [v7.1] hybrid (vector+BM25) + rerank, tenant/audience-filtered in SQL ([v8.0] approved-hint gate, class-wide CM scope)
│   │   │   ├── skill_preset_service.py # [v7.2] teacher skill-preset library CRUD + snapshot-copy into class rule
│   │   │   └── billing.py           # [v7.2] weighted billed-token helper (prompt·α + completion·β)
│   │   ├── agent/                   # [v7] LangGraph agent
│   │   │   ├── __init__.py
│   │   │   ├── graph.py             # [v8.0] router → [exercise → tutor | rag | direct | clarify] → Synthesize → SSE
│   │   │   ├── router.py            # [v8.0] one-call LLM router: classify() + resolve_number() (three-tier dialogue state)
│   │   │   ├── exercise_number.py   # [v8.0] shared number normalizers (normalize_heading_number / normalize_exercise_number; Roman/Arabic/3.1 → canonical int)
│   │   │   ├── selfeval.py          # [v7.1] cheap gate + guided good/partial/bad verdict + bounded re-retrieval
│   │   │   └── prompt.py            # Prompt Controller (skill.md → 3-tier rules → context/hints → history → question)
│   │   │                            # ([v8.0] effort.py/lazy.py deleted — folded into the router's effort/answer_seeking output)
│   │   └── routers/
│   │       ├── __init__.py
│   │       ├── auth.py              # POST /api/auth/signup, /api/auth/login
│   │       ├── admin.py             # /api/admin/* — calls services, no ownership checks
│   │       ├── teacher.py           # /api/teacher/* — ownership-checked (+ documents: upload, list, chunks, exercises)
│   │       ├── student.py           # /api/student/* — join, sessions (soft-delete = hide, retained for audit)
│   │       └── chat.py              # POST /api/chat/stream (LangGraph agent + SSE)
│   ├── worker/                      # [v7] Off-peak ingestion worker
│   │   ├── __init__.py
│   │   ├── main.py                  # Polling loop; [v8.0] dispatches on job_type (ingest | hint_generate)
│   │   ├── queue.py                 # FOR UPDATE SKIP LOCKED claim + stale-lock reclaim
│   │   ├── ingest.py                # [v7.1] parse + gate + segment + Contextual Retrieval + tsvector; [v8.0] corrigé → Answers extraction
│   │   ├── routing.py               # [v8.0] IngestionPlan (chunks / exercises / answers) per doc_type
│   │   ├── hints.py                 # [v8.0] hint workflow (classify → single-shot tiers → leak-lint + judge → retry), all llm_fn injected
│   │   ├── hint_jobs.py             # [v8.0] DB-aware hint-generate runner: pair answers → generate → write hints/status/source
│   │   ├── parsing.py               # [v7.1] PDF text extraction (pymupdf) + character-yield gate
│   │   ├── chunking.py              # [v7.3] single-source segmentation (boundaries, anomaly detection, size control, code blocks)
│   │   └── gpu_gate.py              # chat-load gate (+ optional pynvml GPU signal); off-peak scheduling
│   ├── seed.py                      # Dev DB bootstrap: schema + SystemConfigs defaults
│   ├── .env                         # DATABASE_URL, JWT_SECRET (LLM configs live in DB)
│   ├── requirements.txt
│   └── Dockerfile                   # Python 3.12-slim — shared by backend & worker (entrypoint differs)
├── frontend/
│   ├── public/
│   ├── src/
│   │   ├── App.jsx                  # React Router setup
│   │   ├── pages/
│   │   │   ├── Login.jsx            # Login + link to Register
│   │   │   ├── Register.jsx         # Student self-registration
│   │   │   ├── Account.jsx          # [v7.2] User center — self-service change password (all roles)
│   │   │   ├── Chat.jsx             # Student chat (agent answers + citations) ([v7.2] markdown + code highlight + KaTeX)
│   │   │   ├── Teacher.jsx          # Teacher workspace (tree + rules + document manager)
│   │   │   └── Admin.jsx            # Admin god-mode
│   │   ├── components/
│   │   │   ├── HierarchicalSidebar.jsx  # Role-aware Class→Lab tree
│   │   │   ├── DocumentManager.jsx      # [v7.1] Upload (doc_type/audience) + status list + chunk inspector + exercises
│   │   │   ├── SkillPresetManager.jsx   # [v7.2] Teacher skill-preset library CRUD + apply-to-class
│   │   │   ├── MessageContent.jsx       # [v7.2] markdown + syntax-highlighted code (copy) + KaTeX, stream-safe
│   │   │   ├── MainLayout.jsx
│   │   │   └── ProtectedRoute.jsx
│   │   ├── lib/
│   │   │   └── api.js               # Centralized API client
│   │   └── store/                   # Zustand stores (Auth, etc.)
│   ├── package.json
│   ├── tailwind.config.js           # Tailwind v3 config
│   ├── nginx.conf                   # Nginx reverse proxy config for prod
│   └── Dockerfile                   # Node builder + Nginx multi-stage build
├── scripts/
│   ├── router_accuracy.py           # [v8.0] router eval harness (classify() accuracy over labelled conversations)
│   └── retrieval_golden.py          # [v8.0] retrieval golden-set harness (hit@k + false-grounding + threshold sweep)
└── docker-compose.yml               # Postgres(pgvector) + Backend + Worker + Frontend + named volumes
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
  - **Chat:** a generation model — e.g. `qwen3` (35B local / 120B prod) → `LLM_MODEL`. Also
    used for the guided self-eval verdict, and (unless split) Contextual-Retrieval generation
    and the router exercise-number fallback.
  - **[v7.2] Optional cheap models:** set `INGEST_MODEL` (off-peak Contextual Retrieval +
    exercise extraction) and/or `ROUTER_MODEL` (live exercise-number disambiguation) to a
    smaller model — e.g. `qwen3:30b` / `mistral-small` — to keep the expensive chat model's RPM
    pool for students. Leave empty to reuse `LLM_MODEL`. Embedding/rerank likewise get optional
    independent endpoints (`EMBEDDING_URL`, `RERANK_API_KEY`, …).
  - **[v7.3] Tiering convention (write it into your config, the code only provides slots):**
    **chat = the big model** (non-negotiable — the only thing students see);
    **`ROUTER_*` = one small fast model shared by ALL live auxiliary calls** (router,
    self-eval verdict, rewrite — e.g. a 9B like Ministral);
    **`INGEST_*` = small model** (context generation, extraction, classification);
    **`HINT_*` = the big model, off-peak only** (reserved for the v8.0 answer→hints
    generator — quality over latency; the GPU gate keeps it off student time).
    **Auxiliary tasks never run on the
    chat model's live capacity**; an empty slot falls back to the main LLM, and the admin UI
    warns when that silently puts auxiliary load on the big model.
  - **Embeddings (RAG + router kNN):** `bge-m3` (1024-dim, matches `EMBEDDING_DIM`) →
    `EMBEDDING_MODEL`. Pull it once: `ollama pull bge-m3`. Indexing, querying, and router
    exemplars must all use this same model.
  - **Reranker (v7.1):** `bge-reranker-v2-m3`, served behind an OpenAI-compatible rerank
    endpoint (e.g. TEI / Infinity / vLLM / Albert `…/v1/rerank`) → `RERANK_URL` + `RERANK_MODEL`.
    Ollama does not expose a rerank API, so point these at whatever rerank server you run.
    **`RERANK_URL` must be a real rerank endpoint** (the full path that accepts
    `{query, documents}`, e.g. `https://albert.api.etalab.gouv.fr/v1/rerank`) — **not** the chat
    `…/v1` base, which 404s on a rerank body. **[v7.2]** If unset *or failing* (unreachable /
    4xx-5xx / malformed response), retrieval degrades gracefully to fusion-only ordering and logs
    a warning — a bad reranker never breaks a student's chat.
- `pgvector`-enabled PostgreSQL image (e.g. `pgvector/pgvector:pg16`). v7.1 also uses Postgres
  full-text (`tsvector` / GIN) for BM25 — no extra extension needed.
- **[v8.1] OOM protection — two layers.** The app caps each message's chat-history size
  (`CONTEXT_MAX_TOKENS`, default 8000 estimated tokens; oldest turns silently forgotten).
  **Also set an engine-side maximum context length** (vLLM `--max-model-len` / SGLang
  `--context-length`) as the final backstop — the app cap must stay below it.

> **v7.1 testing note:** hybrid retrieval (pgvector ANN + `tsvector` BM25 + rerank) cannot run
> on SQLite. The retrieval test suite runs against a live pgvector Postgres via the `pg_session`
> fixture (`TEST_PG_URL`, default `localhost:5433/edudb_test`); start it with
> `docker compose up -d postgres`. All model calls (embed / rerank / generate) are replaced by
> injected fakes in tests, so no inference engine is required to run the suite.

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
