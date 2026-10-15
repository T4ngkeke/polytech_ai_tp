> Part of the Edu-LLM documentation. Back to [README](../README.md).

## 3. Database Schema (SQLAlchemy - PostgreSQL + pgvector)

Core tables for decoupled rule injection, the hierarchical class-lab structure,
token tracking, and hybrid retrieval over course documents.

> **Prerequisite:** `CREATE EXTENSION IF NOT EXISTS vector;` must run before the
> tables are created (handled in DB init / `seed.py`).
>
> **No migrations.** There is no Alembic; the schema is applied by
> `Base.metadata.create_all`. Schema changes are applied by **recreating** the
> dev database — there is no production data to migrate.
>
> **Known gap — the indexes below are declared here but not created.** The ANN
> and full-text indexes are described in §10 as the design intends, but nothing
> in the code builds them: they are not in `__table_args__`, and `seed.py` /
> `main.py` only create the extension and the tables. Every vector and BM25
> query is therefore a sequential scan today. Small corpora still answer
> correctly, so this has not shown up as a bug; it will as material grows.

### 1. SystemConfigs
| Column | Type | Constraints / Notes |
| --- | --- | --- |
| id | UUID / Int | Primary Key |
| key | String | Unique. See the model-routing table below for the full list |
| value | String | The configuration value |
| updated_at | Timestamp | For tracking when admin changed configs |

> **Model-routing table.** Generation keeps `LLM_BASE_URL/LLM_API_KEY/LLM_MODEL`.
> Every other role has its own optional endpoint, **defaulting empty → fall back to the
> main LLM**, so a split is opt-in:
>
> | Key | Default | Meaning |
> | --- | --- | --- |
> | `EMBEDDING_MODEL` | `bge-m3` | Embedding model. Must match `EMBEDDING_DIM` and stay the same between indexing and querying |
> | `EMBEDDING_URL` | empty → `LLM_BASE_URL` | Embedding endpoint |
> | `EMBEDDING_API_KEY` | empty → `LLM_API_KEY` | Embedding key |
> | `RERANK_URL` | empty | A real rerank endpoint (the full path accepting `{query, documents}`), not a chat `…/v1` base |
> | `RERANK_MODEL` | `bge-reranker-v2-m3` | Reranker model. Unset or failing → retrieval degrades to fusion-only order |
> | `RERANK_API_KEY` | empty | Key for `RERANK_URL` |
> | `TOKEN_ALPHA` | `0.2` | Prefill (prompt) weight for billed-token quota |
> | `TOKEN_BETA` | `1.0` | Decode (completion) weight for billed-token quota |
>
> Each slot can point at the same endpoint to reuse one engine.
>
> **Other keys.** `AGENT_MODEL` / `AGENT_BASE_URL` / `AGENT_API_KEY` (empty → main
> LLM) — the model that does the reasoning behind retrieval; point it at the
> **big** model, quality over latency. The `ROUTER_*`, `RAG_MAX_RETRIES` and
> `RERANK_SCORE_THRESHOLD` keys of the removed LangGraph path, and the
> `INGEST_*` / `INGEST_TOKEN_BUDGET` keys of the removed pre-MinerU worker,
> are no longer read.
>
> **Context and vision keys.** `CONTEXT_MAX_TOKENS` (default `8000`) — estimated-token budget (chars÷3,
> deliberately conservative) for the chat history sent per message; oldest turns are
> silently dropped, whole user/assistant pairs at a time. OOM protection for the local
> engine — keep it below the engine's own max context length (the final backstop).
> `VLM_MODEL` / `VLM_BASE_URL` / `VLM_API_KEY` — the vision slot for
> garbled-formula transcription (off-peak worker). **Empty model = feature off**;
> vision is opt-in and never falls back to a text model. The admin UI draws these
> slots as a **model-routing map** (sources → models, dashed edges = inherits
> main LLM); click a node to edit that slot, each panel saves independently.

> **All model calls are config-driven HTTP.** Generation, embeddings (`bge-m3`), and
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
| exam_mode | Boolean | Default: False (migration `m4f_answer_modes_v1`). True: every lab of the class answers in Direct mode only, without course search |
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
| is_test | Boolean | Default False. A teacher **test-drive** session: membership bypassed for the owning teacher, excluded from analytics and the citation report |
| is_deleted | Boolean | Default: False. A **student soft-delete** sets this True (the session is hidden from the student but retained and visible to teacher/admin audit). Teacher/admin can also set it; only admin hard-deletes. |
| created_at | Timestamp | Timezone-aware (UTC) |

### 8. Messages & Usage Stats
Tracks tokens used per user and per message. `Messages` carries `feedback`
(Enum `up` / `down`, nullable) — a student 👍/👎 on an assistant reply. These are
free golden-set labels.
`clarification` (JSON, nullable, migration `m4e_clarification_v1`) is set when a
research answer is a question back to the student: `{"question", "options"}`.
`answer_mode` (VARCHAR(16), nullable, migration `m4f_answer_modes_v1`) is the mode
that wrote an assistant answer: `direct`, `search` or `research` (NULL for answers
written before the modes).

---

### Retrieval & Ingestion Tables

### 9. Documents (state machine)
| Column | Type | Constraints / Notes |
| --- | --- | --- |
| id | UUID | Primary Key |
| class_id | FK | → Classes.id. **Security boundary.** Not Null |
| lab_id | FK | → Labs.id. Nullable (NULL = class-wide shared material) |
| filename | String | Original display name |
| storage_path | String | Path inside the `documents_data` Docker volume |
| content_hash | String | Hash of file content — dedup + change detection (triggers re-index) |
| language | String | `fr` (default) / `en`. Set at upload — selects the BM25 `tsvector` config (ingest and query side must match) |
| ingest_report | JSON | Summary of the run: chunk count, estimated token spend, whether it went over budget, and any VLM formula repairs — the teacher audits this instead of re-reading the document |
| status | Enum | `pending` → `processing` → `indexed` → `failed`, plus `needs_review` (the character-yield gate handed the file back to the teacher) |
| error_message | Text | Populated on `failed`; also carries the `needs_review` reason |
| page_count | Integer | Parsed page count. Nullable until processed |
| uploaded_by | FK | → Users.id |
| created_at | Timestamp | Timezone-aware (UTC) |
| indexed_at | Timestamp | Set when indexing completes |

> **`needs_review` is not an error.** A file whose extracted character yield is
> below threshold (or is mostly garbage) is flagged `needs_review` with a reason
> rather than silently ingested. The teacher sees it in DocumentManager and
> re-exports a clean file.
>
> There is no document **type** and no **audience**. Everything a teacher uploads
> is course material the whole class can read, so tenant scoping (class + lab)
> carries the access boundary on its own.

### 10. DocChunks (RAG vectors + BM25)
| Column | Type | Constraints / Notes |
| --- | --- | --- |
| id | UUID | Primary Key |
| document_id | FK | → Documents.id `ON DELETE CASCADE` |
| class_id | UUID | **Denormalized** for filter-without-JOIN (security + speed) |
| lab_id | UUID | **Denormalized**, nullable |
| chunk_index | Integer | Position in source document (ordering / locating) |
| content | Text | The **original** chunk text — what citations and the teacher chunk-inspector show |
| context | Text | Context line written by the removed pre-MinerU worker (nullable; empty for structured chunks) |
| section | String | Heading/section path the chunk came from (nullable — the current chunker does not fill it) |
| embedding | `vector(N)` | Embedded over the **augmented** text (`context + content`). N = embedding dim (`bge-m3` = 1024) |
| tsv | `tsvector` | BM25 full-text column, built over the **augmented** text (config from the document language) |
| page_no | Integer | Citation support — source page |
| edited_by_teacher | Boolean | Default False. Teacher-corrected chunks survive idempotent re-ingestion |
| created_at | Timestamp | Timezone-aware (UTC) |

*Indexes:* the B-tree indexes on `class_id` / `lab_id` (the mandatory tenant
filter) exist. The two that matter for retrieval speed — `USING hnsw (embedding
vector_cosine_ops)` for ANN and `USING gin (tsv)` for BM25 — are **not created by
any code yet**, so both recalls sequential-scan. See the note at the top.

### 11. IngestionJobs (DB-as-queue)
| Column | Type | Constraints / Notes |
| --- | --- | --- |
| id | UUID | Primary Key |
| document_id | FK | → Documents.id `ON DELETE CASCADE` |
| status | Enum | `queued` → `processing` → `done` → `failed` |
| job_type | Enum | `ingest` — the only kind. The column stays as the worker's dispatch point if a second kind is ever added |
| payload | JSON | Free-form job arguments (nullable) |
| priority | Integer | Lower = higher priority. An urgent job uses `priority=0`, which is claimed first and bypasses both worker gates |
| attempts | Integer | Retry counter |
| locked_at | Timestamp | Set when a worker claims the job — used for stale-lock reclaim |
| error_message | Text | Populated on `failed` |
| created_at | Timestamp | Timezone-aware (UTC) |
| updated_at | Timestamp | Touched on each transition |

*Worker pickup is atomic via `FOR UPDATE SKIP LOCKED`, and re-claims any job
stuck in `processing` past a timeout (crash recovery). Ingestion is idempotent: a
re-run first deletes the document's old chunks, then rebuilds — so a strategy
change lands cleanly. Teacher-edited chunks survive the rebuild.*

### 12–14. Removed: LearnerProfiles, RouterQueryLog, AgentTraceLog

These tables belonged to the LangGraph chat path (prompt-literacy coaching,
router decisions, per-message traces), removed on 2026-10-15. They are no
longer models and nothing writes them; an existing database keeps the tables
and their rows until an administrator drops them. The research agent records
its runs in `agent_runs` and `model_call_usage` (§16).

### 15. SkillPresets (per-teacher instructor-style library — §5)
| Column | Type | Constraints / Notes |
| --- | --- | --- |
| id | UUID | Primary Key |
| owner_teacher_id | FK | → Users.id (the teacher who owns this preset). Private to that teacher |
| name | String | Display name of the preset (e.g. "Strict Socratic", "Friendly TA") |
| content | Text | The `skill.md` body (instructor persona/style), a few hundred tokens |
| created_at | Timestamp | Timezone-aware (UTC) |
| updated_at | Timestamp | Timezone-aware (UTC) |

> A reusable library only. Selecting a preset for a class **snapshot-copies** its
> `content` into that class's `level=class` skill rule (the `Rules` row) — the prompt-injection
> path is unchanged and still reads `Rules`. Editing a preset later does **not** retro-change
> classes that already selected it (safe, explicit re-selection required). Teachers may still
> hand-edit the class rule text directly without using a preset.

### 16. Research agent tables (opt-in, migration `m4c_research_persistence_v1`)

Written only by the research agent (`AGENTIC_SEARCH_ENABLED`), in all three answer
modes. Apply `scripts/ingestion_agent_schema_m4c.sql`,
`scripts/ingestion_agent_schema_m4e.sql` (`messages.clarification`) and
`scripts/ingestion_agent_schema_m4f.sql` (`classes.exam_mode`, `messages.answer_mode`)
with `scripts/migrate_document_snapshots.py`; with the flag on, startup checks them.

| Table | One row per | Main columns |
| --- | --- | --- |
| `agent_runs` | research request | `user_id`, `session_id`, `client_request_id` (unique per user), `question`, `status` (`running`, `cancelling`, `finished`, `failed`, `cancelled`), `snapshot_map` (document → revision used), `user_message_id`, `assistant_message_id`, `stop_reason` (`clarification` for a question back), `settlement_status`. At most one running run per session. |
| `message_citations` | cited source of an answer | `message_id`, `label` (E1, ...), `document_id`, `revision_id`, `page_id`, `pdf_page_no`, `block_id`, `range_start`/`range_end` (null for a visual observation), `asset_id`, `origin`, `availability`. No foreign key to the document: deleting it marks the citation `unavailable` and keeps the row. |
| `session_read_state` | session | Unused since 2026-10-12 (the follow-up source selection was removed). Kept because the m4c migration creates it. |
| `usage_reservations` | model call | The tokens held on the student's daily quota before the call (`reserved_tokens`, `state`: `active`, `unknown`, `settled`). An `unknown` reservation still counts against the quota until reconciled. |
| `model_call_usage` | model, embedding or rerank call | `purpose` (`agent`, `answer`, `answer_check`, `citation_correction`, `vision`, `embedding`, `rerank`), `model`, token counts, `usage_source` (`actual`, `estimated`, `none`), `billed_tokens`, `elapsed_seconds`, `status`. |
