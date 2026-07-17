> Part of the Edu-LLM documentation. Back to [README](../README.md).

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
| pairing_suspect | Boolean | **[v8.1]** default False. The hint runner's semantic re-check (`verify_pairing`, hint model) judged that this answer does not answer its paired exercise — generation is skipped (`failed`) and the flag is teacher-visible; a later passing re-check clears it |
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

