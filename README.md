# Edu-LLM: v8.0 Agentic Class-Lab Architecture
---

> **📌 Status — this README describes v8.0. The backend (PR-2a router换代 + PR-2b hint
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
│   │   │   ├── llm_service.py       # [v7.1] OpenAI-compatible clients from SystemConfigs (generate / embed / rerank / ingest / router / hint)
│   │   │   ├── retrieval_service.py # [v7.1] hybrid (vector+BM25) + rerank, tenant/audience-filtered in SQL ([v8.0] approved-hint gate, class-wide CM scope)
│   │   │   ├── skill_preset_service.py # [v7.2] teacher skill-preset library CRUD + snapshot-copy into class rule
│   │   │   └── billing.py           # [v7.2] weighted billed-token helper (prompt·α + completion·β)
│   │   ├── agent/                   # [v7] LangGraph agent
│   │   │   ├── __init__.py
│   │   │   ├── graph.py             # [v8.0] router → [exercise → tutor | rag | direct | clarify] → Synthesize → SSE
│   │   │   ├── router.py            # [v8.0] one-call LLM router: classify() + resolve_number() (three-tier dialogue state)
│   │   │   ├── exercise_number.py   # [v7.2] shared normalize_exercise_number() (Roman/Arabic/3.1 → canonical int)
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
│   │   │   ├── ProtectedRoute.jsx
│   │   │   └── UserHeader.jsx
│   │   ├── lib/
│   │   │   └── api.js               # Centralized API client
│   │   └── store/                   # Zustand stores (Auth, etc.)
│   ├── package.json
│   ├── tailwind.config.js           # Tailwind v3 config
│   ├── nginx.conf                   # Nginx reverse proxy config for prod
│   └── Dockerfile                   # Node builder + Nginx multi-stage build
├── scripts/
│   └── router_accuracy.py           # [v7.2] router eval harness (kNN vs LLM classification)
└── docker-compose.yml               # Postgres(pgvector) + Backend + Worker + Frontend + named volumes
```

---

## 3. Database Schema (SQLAlchemy - PostgreSQL + pgvector)

The system relies on core tables optimized for decoupled rule injection, the hierarchical
class-lab structure, token tracking, and (v7.1) hybrid retrieval over course documents.

> **v7 prerequisite:** `CREATE EXTENSION IF NOT EXISTS vector;` must run before the new
> tables are created (handled in DB init / migration).
>
> **v7.1 schema note:** there is no Alembic; schema is applied by `Base.metadata.create_all`
> (see `seed.py`). The v7.1 changes (drop `Exercise.solution`; add `needs_review`; add
> `doc_type`/`audience`; add `DocChunk.context`/`section`/`tsv`; add `RouterQueryLog`) are
> applied by **recreating** the dev database — there is no production data to migrate.
>
> **v7.2 schema note:** same approach — the v7.2 changes (add `Exercise.number_normalized` +
> `Exercise.audience`; add `Message.billed_tokens`; add the `SkillPresets` table; add the v7.2
> `SystemConfigs` keys with defaults in `seed.py`) are applied by **recreating** the dev database.
>
> **v7.3 schema note:** same approach. Changes: `Documents` + `language` + `ingest_report`;
> `DocChunks` + `edited_by_teacher`; `Exercises` + `edited_by_teacher`; the v7.3
> `SystemConfigs` keys (hint slot reserved for v8.0, rerank score threshold, worker budgets)
> — applied by **recreating** the dev database.

### 1. SystemConfigs
| Column | Type | Constraints / Notes |
| --- | --- | --- |
| id | UUID / Int | Primary Key |
| key | String | Unique. v7: `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL`, `EMBEDDING_MODEL`. **v7.1 adds** `RERANK_URL`, `RERANK_MODEL`, `RAG_MAX_RETRIES` (default `1`), `ROUTER_KNN_THRESHOLD`. **v7.2 adds** the rest of the model-routing table + cost weights (below) |
| value | String | The configuration value |
| updated_at | Timestamp | For tracking when admin changed configs |

> **v7.2 — model-routing table.** Generation keeps `LLM_BASE_URL/LLM_API_KEY/LLM_MODEL`.
> Each other role now has its own optional endpoint, **defaulting empty → fall back to the
> main LLM** so existing behaviour is unchanged unless an admin opts in:
>
> | Key | Default | Meaning |
> | --- | --- | --- |
> | `EMBEDDING_URL` | empty → `LLM_BASE_URL` | Embedding endpoint (was previously hard-shared with `LLM_BASE_URL`) |
> | `EMBEDDING_API_KEY` | empty → `LLM_API_KEY` | Embedding key |
> | `RERANK_API_KEY` | empty | Key for the existing `RERANK_URL` |
> | `INGEST_MODEL` | empty → `LLM_MODEL` | Cheap model the **off-peak worker** uses for Contextual Retrieval + exercise extraction |
> | `INGEST_BASE_URL` | empty → `LLM_BASE_URL` | Ingestion endpoint (e.g. local 30B) |
> | `INGEST_API_KEY` | empty → `LLM_API_KEY` | Ingestion key |
> | `ROUTER_MODEL` | empty → `LLM_MODEL` | Cheap model for **live** exercise-number disambiguation when the regex misses |
> | `ROUTER_BASE_URL` | empty → `LLM_BASE_URL` | Router endpoint |
> | `ROUTER_API_KEY` | empty → `LLM_API_KEY` | Router key |
> | `TOKEN_ALPHA` | `0.2` | Prefill (prompt) weight for billed-token quota |
> | `TOKEN_BETA` | `1.0` | Decode (completion) weight for billed-token quota |
>
> Ingestion (off-peak) and router (live) have different latency/cost needs, so they are
> configured independently; point them at the same endpoint to reuse one engine.
>
> **v7.3 keys.** `HINT_MODEL` / `HINT_BASE_URL` / `HINT_API_KEY` (empty → main LLM; reserved
> for the **v8.0** answer→hints generator — point it at the **big** model, quality over
> latency), `RERANK_SCORE_THRESHOLD` (absolute relevance floor for context injection;
> **ships empty = off** until calibrated on a golden set), `HINT_MAX_SAMPLES` (per-exercise
> derivation sampling budget), `INGEST_TOKEN_BUDGET` (per-document worker budget). By
> convention the `ROUTER_*` slots serve **all live auxiliary calls** (router, self-eval
> verdict, rewrite); the admin UI warns when the slot is empty (auxiliary load would silently
> land on the big chat model). `ROUTER_KNN_THRESHOLD` is removed in v8.0 with the kNN router.

> **v7.1 — all model calls are config-driven HTTP.** Generation, embeddings (`bge-m3`), and
> reranking (`bge-reranker-v2-m3`) all hit OpenAI-compatible endpoints read from this table at
> runtime. Nothing is hardcoded; **no torch in the image**. In tests these calls are replaced
> by injected fakes.

### 2. Users
| Column | Type | Constraints / Notes |
| --- | --- | --- |
| id | UUID / Integer | Primary Key |
| username | String | Unique, Not Null |
| hashed_password | String | bcrypt. Not Null |
| role | Enum(student, teacher, admin) | Not Null |
| daily_token_quota | Integer | Hard limit (e.g., 50000). Set by Admin. |
| is_deleted | Boolean | Default: False — Soft delete flag |

### 3. Classes
| Column | Type | Constraints / Notes |
| --- | --- | --- |
| id | UUID / Int | Primary Key |
| name | String | Name of the class |
| teacher_id | FK | → Users.id (Must be a teacher) |
| invite_code | String | Unique, 6-character short code for zero-friction joining |
| is_deleted | Boolean | Default: False — Soft delete flag for historical retention |
| created_at | Timestamp | Timezone-aware (UTC) |

### 4. Class_Students
| Column | Type | Constraints / Notes |
| --- | --- | --- |
| class_id | FK | → Classes.id |
| student_id | FK | → Users.id |
| joined_at | Timestamp | Timezone-aware (UTC) |
*Primary Key is composite: (class_id, student_id)*

### 5. Labs
| Column | Type | Constraints / Notes |
| --- | --- | --- |
| id | UUID / Int | Primary Key |
| class_id | FK | → Classes.id |
| name | String | E.g., "Python Basics Lab 1" |
| is_active | Boolean | Default: True. Set to False to lock lab (Read-only) |
| is_deleted | Boolean | Default: False — Soft delete flag |
| created_at | Timestamp | Timezone-aware (UTC) |

### 6. Rules (3-tier polymorphic)
| Column | Type | Constraints / Notes |
| --- | --- | --- |
| id | UUID / Int | Primary Key |
| level | Enum | `class`, `lab`, `student` |
| target_id | UUID / Int | The ID of the Class, Lab, or Student this applies to |
| rules_text | Text | The restrictive prompts/instructions designed by the teacher |
| is_active | Boolean | Default: True. Toggle for applying these rules |

> **v7 note — `skill.md`:** the instructor "persona/style" prompt reuses this table as a
> `level=class` rule. The Prompt Controller places it **first** (sets tone); restrictive
> rules go **after** (constraints win, resisting prompt-injection bypass). Keep it short and
> resident (a few hundred tokens). Long instructor material goes through RAG instead.

### 7. Sessions
| Column | Type | Constraints / Notes |
| --- | --- | --- |
| id | UUID / Int | Primary Key |
| user_id | FK | → Users.id |
| lab_id | FK | → Labs.id. Sessions are tightly scoped to a specific lab |
| title | String | Optional display name, renameable by student |
| current_exercise_id | FK | **[v8.0]** → Exercises.id, nullable, `ON DELETE SET NULL`. Dialogue state: the exercise this session is discussing, so a follow-up ("et la question 2 ?") can omit the number. Updated on every exercise-route hit; a stale pointer self-clears on re-ingestion |
| is_test | Boolean | **[v8.0]** default False. A teacher **test-drive** session (§11A): membership bypassed for the owning teacher, draft (`pending_review`) hints previewable, excluded from analytics / LearnerProfile / router distillation |
| last_route | String | **[v8.0]** the previous turn's route, nullable. Powers the clarify anti-loop (a second consecutive unresolved turn falls through to `rag` instead of asking again) |
| is_deleted | Boolean | Default: False. A **student soft-delete** sets this True (the session is hidden from the student but retained and visible to teacher/admin audit). Teacher/admin can also set it; only admin hard-deletes. |
| created_at | Timestamp | Timezone-aware (UTC) |

### 8. Messages & Usage Stats
Tracks tokens used per user and per message. **[v8.0]** `Messages` gains
`feedback` (Enum `up` / `down`, nullable) — a student 👍/👎 on an assistant reply
(§11B): a free golden-set label; a 👎 links back to that message's `AgentTraceLog`
for review.

---

### [v7.1] Retrieval & Ingestion Tables

### 9. Documents (state machine)
| Column | Type | Constraints / Notes |
| --- | --- | --- |
| id | UUID | Primary Key |
| class_id | FK | → Classes.id. **Security boundary.** Not Null |
| lab_id | FK | → Labs.id. Nullable (NULL = class-wide shared material) |
| filename | String | Original display name |
| storage_path | String | Path inside the `documents_data` Docker volume |
| content_hash | String | Hash of file content — dedup + change detection (triggers re-index) |
| doc_type | Enum | **[v7.1]** `CM` / `TD` / `TP`. **[v8.0]** + `corrigé` (a standalone answer file → produces only `Answers`, never chunks or exercises). Set at upload — the deterministic routing signal |
| has_answers | Boolean | **[v8.0]** reserved flag for a TD/TP that carries answers inline; the real workflow keeps answers in a separate `corrigé`, so this is `False` in practice |
| answers_for_document_id | FK | **[v8.0]** → Documents.id, nullable. On a `corrigé`, pins the question document it answers, so pairing scopes to that TD in a multi-file lab |
| audience | Enum | **[v7.1]** `student` / `teacher`. Set at upload. Students never retrieve `teacher`-audience docs (SQL filter) |
| language | String | **[v7.3]** `fr` (default) / `en`. Set at upload — selects the BM25 `tsvector` config (ingest and query side must match) |
| ingest_report | JSON | **[v7.3]** reconciliation report: numbering anomaly, gaps, in-lab number collisions, LLM re-segmentation flag, exercise count — the teacher audits a warning list |
| status | Enum | `pending` → `processing` → `indexed` → `failed`. **[v7.1]** plus `needs_review` (char-yield gate rejected the PDF back to the teacher) |
| error_message | Text | Populated on `failed`; also carries the `needs_review` reason |
| page_count | Integer | **[v7.1]** parsed page count (Phase 6 summary). Nullable until processed |
| uploaded_by | FK | → Users.id |
| created_at | Timestamp | Timezone-aware (UTC) |
| indexed_at | Timestamp | Set when indexing completes |

> **`needs_review` is not an error.** A PDF whose extracted character yield is below threshold
> (or is mostly garbage / non-word) is flagged `needs_review` with a reason — **never silently
> ingested**. The teacher sees it in DocumentManager and re-exports a clean PDF. No OCR.

### 10. DocChunks (RAG vectors + BM25)
| Column | Type | Constraints / Notes |
| --- | --- | --- |
| id | UUID | Primary Key |
| document_id | FK | → Documents.id `ON DELETE CASCADE` |
| class_id | UUID | **Denormalized** for filter-without-JOIN (security + speed) |
| lab_id | UUID | **Denormalized**, nullable |
| doc_type | Enum | **[v7.1]** denormalized from Document (query-time routing/filter) |
| audience | Enum | **[v7.1]** denormalized — the `audience='student'` retrieval filter lives here |
| chunk_index | Integer | Position in source document (ordering / locating) |
| content | Text | The **original** chunk text — this is what citations and the teacher chunk-inspector show |
| context | Text | **[v7.1]** LLM-generated Contextual-Retrieval context (nullable). Load-bearing for context-poor CM slides |
| section | String | **[v7.1]** heading/section path the chunk came from (nullable) |
| embedding | `vector(N)` | Embedded over the **augmented** text (`context + content`). N = embedding dim (`bge-m3` = 1024) |
| tsv | `tsvector` | **[v7.1]** BM25 full-text index, built over the **augmented** text (multilingual config) |
| page_no | Integer | Citation support — source page |
| edited_by_teacher | Boolean | **[v7.3]** default False. Teacher-corrected chunks survive idempotent re-ingestion |
| created_at | Timestamp | Timezone-aware (UTC) |

*Indexes:* `USING hnsw (embedding vector_cosine_ops)` for ANN; `USING gin (tsv)` for BM25;
B-tree on `lab_id` for the mandatory tenant filter.

### 11. Exercises (LLM-extracted, statements only)
| Column | Type | Constraints / Notes |
| --- | --- | --- |
| id | UUID | Primary Key |
| document_id | FK | → Documents.id `ON DELETE CASCADE` |
| class_id | UUID | Denormalized tenant filter. Not Null |
| lab_id | UUID | Denormalized, nullable |
| audience | Enum | **[v7.2]** `student` / `teacher`, denormalized from the Document. The `audience='student'` filter for exercise search lives here — students never retrieve teacher-audience exercises (SQL WHERE) |
| number | String | Raw label as printed — e.g. "Exercise 2", "3.1", "II". Used for display/citation |
| number_normalized | Integer | **[v7.2]** canonical integer derived from `number` (Roman→int, `3.1`→main `3`, etc.), nullable. Both ingest-side extraction and query-side matching run the **same** `normalize_exercise_number()` over it — decouples matching from the unstable printed format |
| statement | Text | Exercise body. **Student-safe** — this is all that is student-visible |
| hints | JSON | **[v8.0]** tiered L1/L2/L3 hint array (was Text; NULL until generated). Only surfaced to students when `hint_status == approved` — the retrieval layer is the single injection gate |
| hint_status | Enum | **[v8.0]** review lifecycle: `none` → `generating` → `pending_review` → `approved` / `failed`. Ingestion never generates; a teacher triggers generation and approves per-exercise |
| hint_source | Enum | **[v8.0]** how the hints were grounded: `worked` / `derived` / `blind` / `none` (orthogonal to status; `blind` = solved without an answer, flagged for audit) |
| edited_by_teacher | Boolean | **[v7.3]** default False. Teacher-corrected exercises survive idempotent re-ingestion. **[v8.0]** a teacher hand-editing hints sets `hint_status=approved` (their own text is trusted) |
| concept | String | **[reserved]** knowledge-point tag for future Adaptive Tutoring (nullable now) |
| created_at | Timestamp | Timezone-aware (UTC) |

> **🔴 red line — no `solution` column; answers never on the student path.** This table has no
> `solution` field; only student-visible **statements** and **approved** hints are ever surfaced.
> **[v8.0]** uploaded answers live in a separate `Answers` table (§3.16) read only by the
> teacher/worker side — the chat / agent / prompt / retrieval modules never import the `Answer`
> model (enforced by `test_answers_isolation`). **[v7.3]** the exercise `number` comes from the
> deterministic segmentation label, and teacher-corrected rows survive re-ingestion.

### 12. IngestionJobs (DB-as-queue)
| Column | Type | Constraints / Notes |
| --- | --- | --- |
| id | UUID | Primary Key |
| document_id | FK | → Documents.id `ON DELETE CASCADE` |
| status | Enum | `queued` → `processing` → `done` → `failed` |
| job_type | Enum | **[v8.0]** `ingest` / `hint_generate`. The worker dispatches on it: `ingest` runs the pipeline; `hint_generate` runs teacher-triggered hint generation |
| payload | JSON | **[v8.0]** job arguments, e.g. a hint job's target `exercise_ids` (nullable) |
| priority | Integer | Lower = higher priority. **[v8.0]** an urgent hint job uses `priority=0` so it is claimed ahead of ingestion |
| attempts | Integer | Retry counter |
| locked_at | Timestamp | Set when a worker claims the job — used for stale-lock reclaim |
| error_message | Text | Populated on `failed` |
| created_at | Timestamp | Timezone-aware (UTC) |
| updated_at | Timestamp | Touched on each transition |

*Worker pickup is atomic via `FOR UPDATE SKIP LOCKED`, and re-claims any job stuck in
`processing` past a timeout (crash recovery). Ingestion is idempotent: a re-run first deletes
the document's old chunks/exercises, then rebuilds (consistent after a strategy change).*

### 13. LearnerProfiles (prompt-literacy coaching — §8)
| Column | Type | Constraints / Notes |
| --- | --- | --- |
| id | UUID | Primary Key |
| user_id | FK | → Users.id |
| lab_id | FK | → Labs.id. Profile is per *(student, lab)* |
| effort_ema | Float | Smoothed sliding-window effort/clarity score (0–1) |
| samples | Integer | Number of scored questions so far (cold-start = neutral) |
| coaching_level | Enum | `neutral` / `low` / `high` — derived from `effort_ema` (hysteresis) |
| last_tip_msg_index | Integer | Throttle marker for the explicit "how to ask" tip |
| teacher_override | Boolean | Default False. Teacher can pin/override the profile |
| updated_at | Timestamp | Timezone-aware (UTC) |
*Stores structured scores + controlled-vocabulary strategy only — never free-text judgments.*

### 14. RouterQueryLog (every routing decision — §6) — **[v8.0] redefined**
| Column | Type | Constraints / Notes |
| --- | --- | --- |
| id | UUID | Primary Key |
| message | Text | The raw student query |
| route | String | The LLM router's decision: `exercise` / `rag` / `direct` / `clarify` |
| exercise_number | Integer | The resolved canonical number (nullable) |
| number_source | String | Where the number came from: `explicit` / `context` / `sticky` / `none` (dialogue-state attribution) |
| degraded | Boolean | True when this was a timeout/failure fallback (`→ rag`) — **not a real label**, must be excludable when distilling |
| is_test | Boolean | **[§11A]** the row came from a teacher test-drive — excluded from distillation/aggregation |
| model_name | String | Router model used (nullable) |
| prompt_version | String | Router prompt version (nullable) |
| latency_ms | Integer | Router call latency (nullable) |
| lab_id | FK | → Labs.id. Nullable |
| created_at | Timestamp | Timezone-aware (UTC) |

> **[v8.0]** Write-only: **every** routing decision is logged (not just low-confidence ones), so
> a future distilled router has a labelled source and the teaching-hotspot panel (§11C) can rank
> the most-asked exercises. Nothing in the live path reads it.

### 15. Answers (**[v8.0]** uploaded answers — teacher/worker side only)
| Column | Type | Constraints / Notes |
| --- | --- | --- |
| id | UUID | Primary Key |
| exercise_id | FK | → Exercises.id `ON DELETE CASCADE`, nullable — paired at generation time by number |
| document_id | FK | → Documents.id `ON DELETE CASCADE` — the `corrigé`/answer file this came from |
| number_raw / number_normalized | String / Integer | The answer's exercise number (same normalizer as exercises) |
| answer_form | Enum | `worked` / `final_only` / `proof_no_process`, nullable — classified at generation time |
| answer_text | Text | The answer body (may be "restated exercise + answer") |
| derivation_text | Text | A verified derivation (reused on regeneration), nullable |
| verified | Boolean | Whether the derivation was checked |
| created_at / updated_at | Timestamp | Timezone-aware (UTC) |

> **🔴 red line.** This table exists so the **offline** hint workflow (and the teacher's
> answer-view endpoint) can read answers. The **student path never imports the `Answer` model** —
> `test_answers_isolation` scans chat / agent(graph,router,prompt) / retrieval and fails on any
> import. The physical "vault" DB from earlier plans was abandoned (answers aren't secret); the
> single discipline is this import boundary.

### 16. AgentTraceLog (**[v8.0]** per-message observability)
| Column | Type | Constraints / Notes |
| --- | --- | --- |
| id | UUID | Primary Key |
| message_id / session_id / lab_id | UUID | Soft references (nullable) — the async write never fights row-creation ordering |
| route | String | Route taken for this message |
| node_latencies | JSON | Per-node ms: router / retrieval / rerank / selfeval / first-token |
| degraded_flags | JSON | Which fallbacks fired: router / embedding / rerank / all_filtered — the health panel (§11D) counts these |
| recall_count / rerank_filtered_count / all_filtered | Integer / Bool | Retrieval + threshold-gate counts |
| verdict / retry_flipped | String / Bool | Self-eval outcome (feeds the two-week keep/drop decision) |
| red_flag | Boolean | Log-only injection heuristic (never rejects — sorts the teacher audit) |
| created_at | Timestamp | Timezone-aware (UTC) |

### 17. SkillPresets (per-teacher instructor-style library — §5/v7.2)
| Column | Type | Constraints / Notes |
| --- | --- | --- |
| id | UUID | Primary Key |
| owner_teacher_id | FK | → Users.id (the teacher who owns this preset). Private to that teacher |
| name | String | Display name of the preset (e.g. "Strict Socratic", "Friendly TA") |
| content | Text | The `skill.md` body (instructor persona/style), a few hundred tokens |
| created_at | Timestamp | Timezone-aware (UTC) |
| updated_at | Timestamp | Timezone-aware (UTC) |

> **[v7.2]** A reusable library only. Selecting a preset for a class **snapshot-copies** its
> `content` into that class's `level=class` skill rule (the `Rules` row) — the prompt-injection
> path is unchanged and still reads `Rules`. Editing a preset later does **not** retro-change
> classes that already selected it (safe, explicit re-selection required). Teachers may still
> hand-edit the class rule text directly without using a preset.

---

## 4. RBAC Authorization (JWT + FastAPI Depends)

> **Stale Token Prevention:**
> The JWT payload MUST ONLY contain `user_id`. Do NOT store `role` or `quota` in the JWT.
> The `Depends` function must query the `Users` table in real-time to fetch the current role and status.

| Depends Function | Who Gets In | Rejection |
| --- | --- | --- |
| `get_current_user` | Any valid JWT whose user is not deleted | 401 |
| `require_teacher` | role == `teacher` OR `admin` | 403 |
| `require_admin` | role == `admin` ONLY | 403 |

---

## 5. API Routing Contract

> **Rule:** Every router calls its own service layer. No router ever calls another router's functions.
> Admin routes bypass ownership checks; Teacher routes enforce ownership.

### A. Auth & Admin Operations

| Method | Endpoint | Auth | Description |
| --- | --- | --- | --- |
| POST | `/api/auth/signup` | — | Student self-registration (Default role: student). Fails if username exists. |
| POST | `/api/auth/login` | — | Validate credentials. Return JWT containing ONLY `user_id`. |
| POST | `/api/auth/change-password` | get_current_user | **[v7.2]** Self-service password change. Requires `old_password` (verified) + `new_password`; re-hashes (bcrypt). Any role. |
| GET | `/api/admin/users` | require_admin | List all non-deleted users. |
| PUT | `/api/admin/users/{id}/password` | require_admin | **[v7.2]** Admin reset of any user's password (no old password required). |
| POST | `/api/admin/users` | require_admin | Create a single new user. |
| PUT | `/api/admin/users/{id}/role` | require_admin | Change user role (e.g., student ↔ teacher). |
| PUT | `/api/admin/users/{id}/quota` | require_admin | Update user's daily token quota. |
| DELETE | `/api/admin/users/{id}` | require_admin | Soft-delete a user. |
| POST | `/api/admin/users/import` | require_admin | CSV Bulk Import. Dry-run by default. `?force=true` executes. |
| GET | `/api/admin/llm/config` | require_admin | Read current LLM config (base_url, model, api_key, embedding_model; **v7.1**: rerank_url, rerank_model, rag_max_retries, router_knn_threshold; **v7.2**: embedding_url/key, rerank_key, ingest_model/url/key, router_model/url/key, token_alpha, token_beta; **v7.3**: hint_model/url/key, rerank_score_threshold, hint_max_samples, ingest_token_budget). |
| PUT | `/api/admin/llm/config` | require_admin | Zero-downtime update of SystemConfigs (all keys above; **v7.2** the full model-routing table + `TOKEN_ALPHA`/`TOKEN_BETA` cost weights). Empty ingest/router/embedding endpoint fields fall back to the main LLM. |
| GET | `/api/admin/classes` | require_admin | God Mode: List all classes across the system (no ownership filter). |
| GET | `/api/admin/classes/{class_id}/labs` | require_admin | God Mode: List all labs in a class. |
| GET | `/api/admin/classes/{class_id}/students` | require_admin | God Mode: View all students in a specific class. |
| GET | `/api/admin/labs` | require_admin | God Mode: List all labs across the system. |
| PUT | `/api/admin/classes/{class_id}/transfer` | require_admin | Force transfer class ownership (`teacher_id`). |
| DELETE | `/api/admin/classes/{class_id}` | require_admin | Force soft-delete any class (bypasses ownership). |
| PUT | `/api/admin/labs/{lab_id}` | require_admin | **[v7.2]** God-mode lab update: lock/unlock (`is_active`) or rename any lab (bypasses ownership). The teacher route still enforces ownership. |
| DELETE | `/api/admin/labs/{lab_id}` | require_admin | Force soft-delete any lab (bypasses ownership). |
| GET | `/api/admin/analytics` | require_admin | Global platform-wide token usage dashboard (hierarchical). |
| GET | `/api/admin/analytics/classes/{class_id}` | require_admin | Per-class hierarchical analytics (class→lab→student). |
| DELETE | `/api/admin/sessions/{session_id}` | require_admin | Hard-delete a specific session to purge inappropriate content. |
| POST | `/api/admin/maintenance/prune` | require_admin | Bulk hard-delete sessions/messages older than X days. **[v8.0]** also prunes `RouterQueryLog` + `AgentTraceLog`. |
| GET | `/api/admin/ingestion/jobs` | require_admin | [NEW v7] Inspect ingestion queue (status, attempts, errors). |
| GET | `/api/admin/health/degradations` | require_admin | **[v8.0 §11D]** Health panel: per-fallback counts (router / embedding / rerank / all_filtered) over a recent window (`?window_minutes=`). |

### B. Teacher Audit & Control

| Method | Endpoint | Auth | Description |
| --- | --- | --- | --- |
| POST | `/api/teacher/classes` | require_teacher | Create class and generate 6-char `invite_code`. |
| GET | `/api/teacher/classes` | require_teacher | List own classes (name, invite code, student count). |
| PUT | `/api/teacher/classes/{class_id}` | require_teacher | Rename/update class details (ownership enforced). |
| DELETE | `/api/teacher/classes/{class_id}` | require_teacher | Soft-delete own class. |
| POST | `/api/teacher/classes/{class_id}/reset-code` | require_teacher | Generate new invite code (invalidating the old one). |
| DELETE | `/api/teacher/classes/{class_id}/students/{student_id}` | require_teacher | Kick/Remove a student from the class. |
| POST | `/api/teacher/classes/{class_id}/labs` | require_teacher | Create a Lab under a Class (ownership enforced). |
| GET | `/api/teacher/classes/{class_id}/labs` | require_teacher | List labs for a class. |
| PUT | `/api/teacher/labs/{lab_id}` | require_teacher | Rename lab or toggle `is_active` (Open/Close). |
| DELETE | `/api/teacher/labs/{lab_id}` | require_teacher | Soft-delete a Lab (ownership enforced). |
| GET | `/api/teacher/rules` | require_teacher | Get existing rules. Filters: `?level=X&target_id=Y` |
| PUT | `/api/teacher/rules` | require_teacher | Create/Update Rules (Class / Lab / Student level; `skill.md` = class level). |
| GET | `/api/teacher/skill-presets` | require_teacher | **[v7.2]** List the teacher's own skill presets (`id / name / content`). |
| POST | `/api/teacher/skill-presets` | require_teacher | **[v7.2]** Create a skill preset (`name`, `content`). |
| PUT | `/api/teacher/skill-presets/{preset_id}` | require_teacher | **[v7.2]** Update an owned preset (ownership enforced). |
| DELETE | `/api/teacher/skill-presets/{preset_id}` | require_teacher | **[v7.2]** Delete an owned preset (does not affect classes that already snapshot-copied it). |
| POST | `/api/teacher/classes/{class_id}/skill` | require_teacher | **[v7.2]** Apply a preset to a class — **snapshot-copies** `preset.content` into the class's `level=class` skill rule. Body: `{preset_id}` or raw `{content}` for an ad-hoc edit. |
| GET | `/api/teacher/chat-history` | require_teacher | Fetch chat history. Filters: `?class_id=X&lab_id=Y&student_id=Z&session_id=W`. **Includes student soft-deleted sessions** (each row carries `is_deleted` so the UI can flag the hidden ones). |
| GET | `/api/teacher/analytics/classes/{class_id}` | require_teacher | Hierarchical token usage: class → lab → student breakdown. |
| POST | `/api/teacher/labs/{lab_id}/documents` | require_teacher | [v7.1] Upload a **PDF** course document. Multipart form **requires `doc_type` (CM/TD/TP/**[v8.0]** `corrigé`) + `audience`**; **[v7.3]** optional `language`; **[v8.0]** optional `shared` (CM only → class-wide, `lab_id=NULL`) and `answers_for_document_id` (a `corrigé`'s target TD). Stored + enqueued. |
| GET | `/api/teacher/labs/{lab_id}/documents` | require_teacher | [v7.1] List documents + status + summary + `ingest_report`. |
| GET | `/api/teacher/documents/{document_id}/chunks` | require_teacher | [v7.1] Paginated chunk text + `page_no` (+ generated `context`). |
| GET | `/api/teacher/documents/{document_id}/exercises` | require_teacher | [v7.1] Extracted exercises. **[v8.0]** each carries the tiered `hints` array + `hint_status` / `hint_source`. No solution exists. |
| PUT | `/api/teacher/documents/{document_id}/chunks/{chunk_id}` | require_teacher | **[v7.2]** Correct a chunk's text; re-embeds + rebuilds BM25 `tsv`; sets `edited_by_teacher`. |
| PUT | `/api/teacher/documents/{document_id}/exercises/{exercise_id}` | require_teacher | **[v7.2]** Correct an exercise (number/statement); re-derives `number_normalized`; sets `edited_by_teacher`. **[v8.0]** accepts a tiered `hints` array — a hand-edit sets `hint_status=approved` (trusted). |
| POST | `/api/teacher/documents/{document_id}/exercises` | require_teacher | **[v8.0]** Hand-add an exercise the extractor missed (`edited_by_teacher`). |
| DELETE | `/api/teacher/documents/{document_id}/exercises/{exercise_id}` | require_teacher | **[v8.0]** Remove a phantom exercise. |
| POST | `/api/teacher/documents/{document_id}/generate-hints` | require_teacher | **[v8.0]** Batch-generate hints (queues a `hint_generate` job for every exercise still `none`; idempotent). Body `{urgent?}` → `priority=0`. |
| POST | `/api/teacher/documents/{document_id}/exercises/{exercise_id}/generate-hints` | require_teacher | **[v8.0]** Regenerate one exercise — the only entry that overwrites an already-reviewed/edited one. |
| PUT | `/api/teacher/documents/{document_id}/exercises/{exercise_id}/hints/approve` | require_teacher | **[v8.0]** Approve a reviewed draft → students can now see the hints. |
| GET | `/api/teacher/labs/{lab_id}/answers` | require_teacher | **[v8.0]** Read uploaded answers + pairing state (unpaired = a numbering/collision ambiguity to resolve). Teacher-only (decision B). |
| POST | `/api/teacher/labs/{lab_id}/test-session` | require_teacher | **[v8.0 §11A]** Open a **test-drive** session (`is_test`) on an owned lab — chat as a preview (membership bypassed, draft hints visible). |
| GET | `/api/teacher/labs/{lab_id}/hotspots` | require_teacher | **[v8.0 §11C]** The lab's most-asked exercises (ranked from RouterQueryLog, test-drives excluded). |
| DELETE | `/api/teacher/documents/{document_id}` | require_teacher | [v7] Delete a document (cascades chunks/exercises/answers; triggers re-index cleanup). |

### C. Student Flow (Session & Chat)

> **Audit Integrity Rule:** A student "delete" is a **soft delete** — it sets `is_deleted=True`,
> hiding the session from the student's own views, but the session is permanently retained and
> stays fully visible to teacher/admin audit (flagged as deleted). Students can never purge data;
> only admin can hard-delete a session.

| Method | Endpoint | Auth | Description |
| --- | --- | --- | --- |
| POST | `/api/student/classes/join` | get_current_user | Submit 6-char `invite_code` to instantly join a Class. |
| GET | `/api/student/classes` | get_current_user | List all joined classes (with labs). |
| GET | `/api/student/classes/{class_id}/labs` | get_current_user | List active labs for a joined class. |
| DELETE | `/api/student/classes/{class_id}/leave` | get_current_user | Voluntarily leave a joined class. |
| POST | `/api/student/labs/{lab_id}/sessions` | get_current_user | Create a new Chat Session specifically within a Lab. |
| GET | `/api/student/sessions` | get_current_user | List own sessions (optional `?lab_id=` filter). |
| GET | `/api/student/sessions/{session_id}` | get_current_user | Fetch message history. **IDOR check required.** |
| PUT | `/api/student/sessions/{session_id}` | get_current_user | Rename a chat session title (cosmetic only). |
| DELETE | `/api/student/sessions/{session_id}` | get_current_user | **Soft-delete (hide)** own session: sets `is_deleted=True`. Hidden from the student; retained and teacher/admin-visible. **IDOR check required.** |
| GET | `/api/student/usage` | get_current_user | Today's token usage vs. daily quota. |
| POST | `/api/chat/stream` | get_current_user | Core chat endpoint. Scoped by `session_id`. LangGraph agent, SSE streaming. |
| POST | `/api/chat/messages/{message_id}/feedback` | get_current_user | **[v8.0 §11B]** 👍/👎 on an assistant message (body `{feedback: up\|down}`). A student may only rate a message in a session they own. |

---

## 6. POST `/api/chat/stream` — Agent Logic Spec (v8.0)

**Payload:**

```json
{
  "session_id": "uuid",
  "message": "string"
}
```

**Step-by-step Logic (LangGraph agent):**

1. **Security & Context Resolution**
   * Fetch Session to find `lab_id`. Verify `session.user_id == current_user.id`.
   * Verify the user has joined the Class that owns this Lab — **[v8.0]** unless it is the
     owning teacher's `is_test` **test-drive** session, which bypasses the membership check.
   * Verify the Lab `is_active == True` (reject if locked). Resolve `class_id` — the tenant
     filter passed into every retrieval call.
   * **[v8.0] Load dialogue state**: read `session.current_exercise_id` → a sticky exercise
     number + a ~100-char statement excerpt, and `last_route` (for the clarify anti-loop).

2. **Rate Limit** — DB-based daily quota check; `429` if over (billed tokens; router calls
   are **not** charged to the student).

3. **LangGraph Graph Execution (v8.0)**
   ```
   Router — ONE ROUTER_MODEL (9B) call, json_schema output:
     {route: exercise|rag|direct, exercise_number, number_source: explicit|context|none,
      search_terms, sticky_matches, effort, answer_seeking}
     inputs: message (delimited as data) + last 1–2 turns + sticky number + sticky excerpt
     timeout 1–2s / failure → route=rag + degraded (routing never drags down chat)
     every decision → RouterQueryLog
        │
        ├─ exercise → number arbitration (pure fn): explicit(DB) > context(DB) > sticky(+matches) > clarify
        │     ├─ resolvable → search_exercise(n) (statement + APPROVED hints only)
        │     │                → update session.current_exercise_id
        │     │                → supplement CM context (hybrid_search on statement [+search_terms])
        │     │                → tutor (effort/answer_seeking → coaching, EXERCISE leg only) → Synthesize + citations
        │     └─ unresolvable (3 triggers) → clarify: ask "which exercise?" + lab number list
        │            anti-loop: prev turn already clarify + still unresolved → fall through to rag
        ├─ rag  → embed (fail → BM25-only) → hybrid vector+BM25 → RRF → rerank(augmented) → threshold gate
        │            ├─ ≥1 survive → context injection ("possibly relevant; ignore if not") → Synthesize + citations
        │            └─ all filtered → disclaimer-direct ("not covered by the material"; no injection, no citations)
        └─ direct → Synthesize
                       ↓
   Self-eval (observation window): deterministic gate kept; LLM verdict/rewrite run on the 9B
     aux slot, recorded to AgentTraceLog (bad-rate / retry-flip / false-grounding) — two-week keep/drop
                       ↓
   Synthesize (skill.md → 3-tier rules → [statement + approved hints (lazy → L1/L2 only) | context + citations] → history → question)
   ```
   * **Dialogue-state iron rule**: the sticky exercise only **fills a blank** number — it never
     picks the route (routing is always decided from content + history). Priority: explicit >
     context > sticky(+content match) > clarify. **Guessing wrong is worse than asking** — an
     unresolvable number produces a clarifying question, never a wrong-exercise tutoring turn.
   * **Citation iron rule**: any retrieval-touched path (including the exercise leg's CM
     supplement) **must** cite; the zero-injection disclaimer branch must **not**.
   * **Tenant + audience filter is mandatory and in SQL**: every `exercise` / `rag` query filters
     `lab_id` / `class_id` (and `audience='student'` for students) **in the WHERE clause**. **[v8.0]**
     CM may be class-wide shared (`class_id AND (lab_id=X OR lab_id IS NULL)`); TD/TP stay lab-scoped.
   * **Hints only when approved**: `search_exercise` is the single injection gate — a hit's hints
     are populated only when `hint_status == approved` (or `pending_review` for a teacher
     test-drive). Statements are always available; the answer is never stored on the exercise.
   * **Coaching is exercise-only**: the router emits `effort`/`answer_seeking` only on the
     exercise route; the tutor node folds effort into the per-(student, lab) EMA and derives a
     coaching strategy — skipped entirely for `is_test` sessions.
   * **Degradation matrix**: the only hard dependencies are the **main LLM** and the **main DB**;
     router→rag, embedding→BM25-only, rerank→fusion order, self-eval→skipped — each flagged in
     `AgentTraceLog` (surfaced by the admin health panel).
   * **No solution anywhere on the student path**: no `solution` field exists; uploaded answers
     live in the teacher/worker-only `Answers` table, never imported by chat/agent/prompt/retrieval.

4. **Dynamic LLM Config (Zero-Downtime)**
   * Fetch active `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL`, `EMBEDDING_MODEL`, and (v7.1)
     `RERANK_URL` / `RERANK_MODEL` / `RAG_MAX_RETRIES` / `ROUTER_KNN_THRESHOLD` from
     `SystemConfigs`. Nothing is hardcoded — generation, embeddings, and reranking are all
     config-driven HTTP. Swapping a model or threshold is a config change, not a code change.

5. **SSE Streaming & Safe Async Write**
   * Stream tokens via SSE from the synthesize payload. After the token stream, emit a final
     **`citations`** event (`document_id` / `filename` / `page_no`, deduped) and a **`done`**
     event (the client uses `done` to stop streaming and refresh quota). *(An optional throttled
     `tip` event for prompt-literacy coaching is deferred to v7.2.)*
   * Use `BackgroundTasks` to insert User message, LLM message (with tokens), and Upsert
     `usage_stats`. **Token accounting sums the Synthesize node's usage** plus any extra LLM
     calls made during Contextual routing / self-eval.
   * **[v7.2] Cost-aware quota.** Raw `prompt_tokens` / `completion_tokens` are still recorded
     per message, but the quota is charged on **billed tokens** =
     `prompt·TOKEN_ALPHA + completion·TOKEN_BETA` (admin-set, default `0.2`/`1.0`). The daily
     quota check (`429`) compares accumulated **billed** tokens against `daily_token_quota`, and
     `GET /api/student/usage` reports billed-vs-quota. The per-message `billed_tokens` is also
     stored on the LLM `Message` so class/lab analytics reconcile with the quota (re-deriving it
     later would be wrong once an admin changes the weights). The streaming `create()` call sets
     `stream_options={"include_usage": True}` so real token counts arrive — without it usage
     would be omitted and billing would fall back to a flat estimate.

---

## 7. Ingestion Pipeline (v7.1, hardened v7.3)

A deferred, GPU-aware, **PDF-only structured** pipeline that never preempts live students.
**No Airflow, no k8s, no OCR, no LibreOffice.**

- **`worker` service** (in docker-compose): shares the backend image/code/DB; the only
  difference is the entrypoint — a polling loop instead of uvicorn. It reuses the same
  DB-as-queue pattern already used for rate limiting.
- **Input = PDF only.** Instructors export slides (`.ppt/.pptx`) to PDF themselves before
  upload (1 slide = 1 page). This collapses ingestion to a single pymupdf path.
- **Per-job pipeline (concurrency = 1, `FOR UPDATE SKIP LOCKED`):**
  1. **Parse + input gate** (`parsing.py`): pymupdf text extraction; a cheap **character-yield
     gate** (chars/page below threshold, or high garbage / non-word ratio) marks the document
     `needs_review` and rejects it back to the teacher — **never silently ingest garbage**.
  2. **Type + audience routing**: deterministic, from the upload metadata (`doc_type`,
     `audience`) — decides chunk-path vs exercise-path and whether Contextual Retrieval applies.
     No structure guessing.
  3. **Structure-aware chunking** (`chunking.py`): split on heading / paragraph / exercise
     boundaries; **never cut an exercise or a code block in half**; **detect and drop the TOC**
     (table des matières) so it doesn't match every query.
  4. **Contextual Retrieval**: per chunk, the worker LLM generates the chunk's in-document
     context and prepends it; the **augmented** text (`context + content`) is embedded and
     BM25-indexed, while the original `content` is stored for citation. Load-bearing for
     context-poor CM slides. **[v7.2]** the context is generated from the chunk's **own local
     scope** — its `section` when the chunker found one (TD/TP), otherwise its **page/slide**
     (CM = 1 page per slide) — not `full_text[:8000]`, so a long document never gets a context
     hallucinated from its opening pages, and the call fits any small model. No hard length gate
     — long docs just degrade to per-section/per-slide context. Generation uses `INGEST_MODEL`
     (defaults to `LLM_MODEL`), so a cheap off-peak model can do this without touching the chat
     model.
  5. **Exercise extraction — statements only**: extract `number / statement / hints` via
     **structured decoding**. **[v7.2]** uses the OpenAI-standard
     `response_format: {"type":"json_schema", …}` (enforced by vLLM/SGLang guided decoding,
     supported natively by gateways like Albert) — **not** the vLLM-only `guided_json` extra_body,
     which other engines silently ignore. Parsing is defensive (empty / fenced / prose output →
     `0 exercises`), and extraction is **decoupled from chunk indexing**: if it yields nothing the
     document still reaches `indexed` (chunks/RAG work) rather than `failed`. **No `solution` is
     extracted or stored** (the column does not exist).
  6. **Build the BM25 column** (`tsvector`, **[v7.3]** config selected by the document's
     `language`) and enrich metadata (`doc_type` / `audience` / `section` / `page_no`).
- **[v7.3] Pipeline hardening + the answers path:**
  1. **Clean once, upstream**: repeated header/footer lines stripped; TOC pages removed once —
     every downstream step (chunking *and* extraction) sees only cleaned pages.
  2. **Segmentation is the single source of truth**: expanded deterministic boundaries
     (Arabic / Roman / `Question n` / bare-numbered headings). The exercise `number` comes
     from the captured label — never from an LLM reading the whole document.
  3. **Deterministic exercise build + LLM only on anomaly**: exercises are written straight
     from the segments (number = label, statement = segment body, hints = NULL) with **zero
     LLM calls on the happy path**. A numbering anomaly — duplicate normalized numbers
     (sub-questions mistaken for exercises: `1,1,2,3`) or zero boundaries — triggers **one**
     `resegment_fn` call (`INGEST_MODEL`): the LLM names the true boundary heading lines
     verbatim, splitting stays deterministic, hallucinated anchors are ignored, and its
     failure keeps the regex result (never fails the document).
  4. **Strict split** (`IngestionPlan.produce_chunks`): TD/TP skip the chunk/embed/tsv stage
     entirely; CM skips exercise building. **CM size control**: tiny paragraphs merged,
     oversized ones sentence-split at 1600 chars, code blocks kept intact (blank lines inside
     indented blocks don't split), document-level paragraph dedup.
  5. **Reconciliation report** (`ingest_report`): numbering anomaly, gaps, in-lab number
     collisions, LLM re-segmentation flag, exercise count — the teacher audits a warning
     list, not the whole document. Teacher-edited rows (`edited_by_teacher`) survive
     re-ingestion (matched back by chunk_index / number_normalized; an edited exercise whose
     number vanished is kept as an extra row).
- **[v8.0] Answers + teacher-driven hint lifecycle** (`IngestionPlan.produce_answers`):
  1. **Answer extraction**: a `corrigé` (the standalone answer file — the real workflow always
     keeps answers separate from the pure question TD) is segmented by number like a TD and
     written to the `Answers` table (one row per number; `answer_form` classified later). It
     produces **no** chunks and **no** exercises — the exercises come from the pure TD, so nothing
     answer-shaped ever lands in a student-visible `statement`.
  2. **Ingestion never generates hints.** A teacher triggers generation (`generate-hints`);
     the worker dispatches the `hint_generate` job (`worker/hint_jobs.py`): it **pairs** answers
     to exercises by number (a `corrigé` may pin its target TD via `answers_for_document_id`),
     then for each exercise runs the hint workflow (`worker/hints.py`, plain Python, `HINT_MODEL`):
     classify answer form → single-shot tiered generation (never revealing the final result) →
     deterministic leak-lint + a 1-token LLM judge → bounded retry → `pending_review` / `failed`.
  3. **Review gate**: the teacher approves per-exercise (`hint_status → approved`) — only then do
     hints reach students. Batch generation is idempotent (only `none` exercises); single-exercise
     generation is the sole override for an already-reviewed one.
- **Config-driven engine, optionally split:** embeddings and Contextual-Retrieval generation
  are read from `SystemConfigs` — nothing hardcoded, no torch. **[v7.2]** by default they still
  fall back to the one chat engine, but an admin can point `INGEST_MODEL` / `INGEST_BASE_URL`
  at a separate cheap model (e.g. local 30B) so ingestion doesn't share the chat model's RPM
  pool. Per-chunk Contextual-Retrieval calls run entirely on the **off-peak worker** and never
  preempt students.
- **"When to run" lives in the worker's Python** (`gpu_gate.py`): the **primary signal is
  live chat load** — the worker counts recent `Messages` in the DB and pauses ingestion while
  students are actively chatting (sliding window + hysteresis to avoid flapping). **GPU
  utilization is an optional additional condition**: on a local engine the worker also requires
  the GPU idle via `pynvml`; with a remote API there is no local GPU, so `pynvml` reads idle and
  that condition no-ops. The same gate therefore works for both local-GPU and remote-API
  engines. At night / no-class windows it may pull freely. No external cron.
- **Crash recovery & idempotency:** stale `processing` jobs past a timeout are re-claimed; a
  re-run deletes the document's old chunks/exercises before rebuilding (consistent after a
  strategy change).

---

## 8. Adaptive Tutoring — Prompt-Literacy Coaching (v7.1)

> **Teaching goal:** teach students **how to ask GenAI** — a learnable skill. We judge the
> *most observable signal — how the student asks* — not their mastery, using **cheap,
> rules-first heuristics** (no LLM), mirroring the `router` and `GpuGate` patterns. The heavy
> Learner-Model / BKT-DKT / LLM-judge / per-concept stack is intentionally dropped.

Two layers:

- **(a) Effort / clarity — windowed, formative.** A per-message heuristic score (code-dump vs.
  explanation; "what I tried"; a concrete error; an exercise reference) feeds a **smoothed
  sliding window** (EMA) per *(student, lab)*. The trend sets a *coaching level* that adjusts
  the tutor's scaffolding. **Never gates, always answers.** When the window stays low, a
  **throttled** tip nudges better questioning. A low-skill ("stupid") question is **never
  limited** — only encouraged toward more thinking, less copy-paste.

- **(b) Lazy / answer-seeking — per-message guardrail.** Detects offloading ("just give me the
  answer to Q2"). Forces a **strict Socratic response** (guiding questions/hints only, never
  the final answer). It **still responds** — "limit" means *don't hand over the answer*.
  Distinguished from a thinking question ("I think the answer is X because Y, right?"), which
  is answered normally. Keys on **absence of the student's own attempt**, not on mentioning an
  answer/exercise.

- **Governance:** store **structured score + controlled-vocabulary strategy** only — never
  insulting free text. Profiles are **teacher-visible, teacher-overridable, transparent to the
  student.** Complements the data-layer red line (no solution is stored anywhere).

---

## 9. Analytics Schema — Hierarchical Response

The `ClassAnalyticsResponse` includes a full Lab→Student breakdown:

```json
{
  "class_id": "uuid",
  "class_name": "Algorithms 2024",
  "total_tokens": 12000,
  "total_requests": 340,
  "labs": [
    {
      "lab_id": "uuid",
      "lab_name": "Python Lab 1",
      "tokens": 7000,
      "requests": 200,
      "students": [
        { "user_id": "uuid", "username": "alice", "tokens_used": 3500, "request_count": 120 },
        { "user_id": "uuid", "username": "bob",   "tokens_used": 3500, "request_count": 80 }
      ]
    },
    {
      "lab_id": "uuid",
      "lab_name": "Python Lab 2",
      "tokens": 5000,
      "requests": 140,
      "students": []
    }
  ]
}
```

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
