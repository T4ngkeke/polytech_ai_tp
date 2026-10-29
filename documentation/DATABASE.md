> Part of the Edu-LLM documentation. Back to [README](../README.md).

# Database

This file describes every table and column of the PostgreSQL database (with the `pgvector` extension), and the settings that are stored in the table `system_configs`. The tables are defined in `backend/app/models.py`. The endpoints that use them are in [API.md](API.md).

## How the schema is created

- **Extension.** `CREATE EXTENSION IF NOT EXISTS vector` must run before the tables exist. The backend does it at startup, and `seed.py` does it too.
- **New tables.** The backend creates missing tables at startup with `Base.metadata.create_all`. There is no Alembic.
- **Changes to existing tables.** `create_all` cannot add columns to a table that exists. These changes are SQL files in `scripts/`, applied in order by `python -m scripts.migrate_document_snapshots --database-url URL [--apply]`. The command is a dry run without `--apply`. The script keeps its record in the table `document_schema_migrations` (`version`, `checksum`, `applied_at`), which is not an ORM model.
- **Startup checks.** The backend stops with an error if an old table lacks a column that a migration adds. With `STRUCTURED_INGEST_ENABLED` or `AGENTIC_SEARCH_ENABLED` on, it also stops if a required migration is missing. The required list is `backend/app/schema_versions.py`.
- **Tables that came without a migration file.** `quota_grants` and `audit_reports` are new tables, so `create_all` makes them.
- **Types in tests.** The tests run on SQLite for most cases. There a UUID is stored as `CHAR(36)`, and the vector and full-text columns are plain text. Tests of search use PostgreSQL.

**Known gap: two indexes do not exist.** Search over `doc_chunks` needs an HNSW index on `embedding` (`USING hnsw (embedding vector_cosine_ops)`) and a GIN index on `tsv` (`USING gin (tsv)`). No code creates them, so both searches read the whole table. The answers are correct, but the search will slow down as the material grows.

Common types: *UUID* is a UUID key. *Timestamp* is a timestamp with time zone (UTC).

## Settings (`system_configs`)

This table holds one row per setting, read at run time. The admin edits the model settings with `PUT /api/admin/llm/config`, and no restart is needed.

| Column | Type | Notes |
| --- | --- | --- |
| id | UUID | Primary key. |
| key | String(255) | Unique. The name of the setting. |
| value | Text | The value, always a string. |
| updated_at | Timestamp | When the value last changed. |

The keys below are the ones the code reads. An empty value means "use the default" in the column Fallback.

| Key | Fallback when empty | Meaning |
| --- | --- | --- |
| `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL` | the environment values of the same name | The main model. In the current deployment this is Qwen3.8 Flash-Next, served under the name `flash-next`. |
| `AGENT_BASE_URL`, `AGENT_API_KEY`, `AGENT_MODEL` | the main model | The model of the research agent. |
| `VLM_BASE_URL`, `VLM_API_KEY` | the main model's URL and key | The vision model that reads images (`view_image`, "Write with AI"). |
| `VLM_MODEL` | none: empty means vision is off | The name of the vision model. It never falls back to a text model. |
| `EMBEDDING_URL`, `EMBEDDING_API_KEY` | the main model's URL and key | The embedding server. In the current deployment it is `bge-m3` on vLLM. |
| `EMBEDDING_MODEL` | `BAAI/bge-m3` | The embedding model. It must give 1024 numbers (`EMBEDDING_DIM`) and must stay the same for indexing and searching. Changing it makes every stored vector wrong; recompute them with `scripts/reembed_documents.py`. |
| `RERANK_URL` | none: empty means no reranking | A real rerank endpoint (the full path that accepts `{query, documents}`), not a chat `.../v1` address. If it is empty or fails, search uses the merged order without reranking. |
| `RERANK_API_KEY` | the main model's key | Key for the reranker. |
| `RERANK_MODEL` | `Qwen/Qwen3-Reranker-0.6B` | The reranker. A name that contains `qwen3-reranker` is treated as Qwen3-Reranker (the current deployment). |
| `TOKEN_ALPHA`, `TOKEN_BETA` | `0.2`, `1.0` | Weights of the billed tokens: `prompt tokens * alpha + completion tokens * beta`. |
| `CONTEXT_MAX_TOKENS` | `8000` | Estimated tokens of chat history sent with a message (characters divided by 3). The oldest turns are dropped first. In Research mode it is also capped at a quarter of `AGENT_MAX_INPUT_TOKENS`. Keep it below the context length of the model server. |
| `PDF_MINERU_SERVER_URL` | the `PDF_MINERU_SERVER_URL` of the worker's environment | The MinerU server. The worker reads it again at each tick. |
| `AGENT_MAX_TOOL_CALLS`, `AGENT_MAX_VISUAL_CALLS`, `AGENT_RESEARCH_SECONDS`, `AGENT_TOTAL_SECONDS`, `AGENT_CALL_SECONDS`, `AGENT_MAX_INPUT_TOKENS` | the defaults in [API.md](API.md) | The limits of one research run. |

The keys `ROUTER_*`, `INGEST_*`, `INGEST_TOKEN_BUDGET`, `RAG_MAX_RETRIES` and `RERANK_SCORE_THRESHOLD` belonged to features that were removed. No code reads them. `seed.py` still writes some of them, and an older database may still hold them.

## Users and classes

### users

The accounts of all three roles.

| Column | Type | Notes |
| --- | --- | --- |
| id | UUID | Primary key. |
| username | String(150) | Unique. |
| hashed_password | String(255) | bcrypt hash. |
| role | Enum | `student`, `teacher` or `admin`. Default `student`. |
| daily_token_quota | Integer | Daily limit of billed tokens. Default 50000. Set by the admin. |
| is_deleted | Boolean | Default false. A deleted user cannot log in. |

### classes

A class is owned by one teacher and joined by students with an invite code.

| Column | Type | Notes |
| --- | --- | --- |
| id | UUID | Primary key. |
| name | String(255) | |
| teacher_id | UUID | Foreign key to `users.id`. The owner (a teacher, or an admin after a transfer). |
| invite_code | String(6) | Unique. Students join with it. |
| is_deleted | Boolean | Default false. Soft delete. |
| exam_mode | Boolean | Default false. When true, every lab of the class answers in Direct mode only, without course search. New classes get the value of `NEW_CLASS_EXAM_MODE`. Added by migration `m4f_answer_modes_v1`. |
| created_at | Timestamp | |

### class_students

Which students joined which class. The primary key is (`class_id`, `student_id`).

| Column | Type | Notes |
| --- | --- | --- |
| class_id | UUID | Foreign key to `classes.id`. |
| student_id | UUID | Foreign key to `users.id`. |
| joined_at | Timestamp | |

### labs

A lab is one activity inside a class. Chat sessions and lab documents belong to a lab.

| Column | Type | Notes |
| --- | --- | --- |
| id | UUID | Primary key. |
| class_id | UUID | Foreign key to `classes.id`. |
| name | String(255) | |
| is_active | Boolean | Default true. False locks the lab: chat is refused. |
| is_deleted | Boolean | Default false. Soft delete. |
| created_at | Timestamp | |

## Rules

### rules

The teacher's instructions for the model, at three levels. The pair (`level`, `target_id`) is unique. The class-level rule is also the `skill.md` text that sets the tutor's style. It is placed first in the instructions, and the other rules come after it.

| Column | Type | Notes |
| --- | --- | --- |
| id | UUID | Primary key. |
| level | Enum | `class`, `lab` or `student`. |
| target_id | UUID | The id of the class, lab or student the rule applies to. |
| rules_text | Text | The instructions. |
| is_active | Boolean | Default true. |

### skill_presets

A private library of style texts that a teacher can reuse. Applying a preset to a class copies its `content` into the class's rule. Changing the preset later does not change classes that already copied it.

| Column | Type | Notes |
| --- | --- | --- |
| id | UUID | Primary key. |
| owner_teacher_id | UUID | Foreign key to `users.id`. Only this teacher sees the preset. |
| name | String(255) | |
| content | Text | The text, a few hundred tokens. |
| created_at | Timestamp | |
| updated_at | Timestamp | |

## Chat

### sessions

A conversation of one user in one lab.

| Column | Type | Notes |
| --- | --- | --- |
| id | UUID | Primary key. |
| user_id | UUID | Foreign key to `users.id`. |
| lab_id | UUID | Foreign key to `labs.id`. Nullable in the schema, but chat needs a lab. |
| title | String(255) | Nullable. Written from the first question and answer, or by the student. |
| is_test | Boolean | Default false. A teacher test drive. It is left out of analytics and of the citation report. |
| is_deleted | Boolean | Default false. A student's delete sets it. The session stays visible to teachers and admins. |
| created_at | Timestamp | |

### messages

The messages of a session, with the tokens each one used.

| Column | Type | Notes |
| --- | --- | --- |
| id | UUID | Primary key. |
| session_id | UUID | Foreign key to `sessions.id`. |
| sender | Enum | `user` or `llm`. |
| content | Text | |
| prompt_tokens, completion_tokens, total_tokens | Integer | Nullable. Token counts of the answer. |
| billed_tokens | Integer | Nullable. The weighted tokens charged to the quota. |
| feedback | Enum | Nullable. `up` or `down`, set by the student on an answer. |
| clarification | JSON | Nullable. `{"question", "options"}` when the answer is a question back to the student. Added by migration `m4e_clarification_v1`. |
| answer_mode | String(16) | Nullable. The mode that wrote an answer: `direct`, `search` or `research`. Null for answers written before the modes. Added by migration `m4f_answer_modes_v1`. |
| created_at | Timestamp | |

## Usage and quota

### usage_stats

The tokens each user used per day. One row per user and day (unique). It is never rewritten to hide usage.

| Column | Type | Notes |
| --- | --- | --- |
| id | UUID | Primary key. |
| user_id | UUID | Foreign key to `users.id`. |
| date | Date | |
| tokens_used | BigInteger | Billed tokens of the day. Default 0. |
| request_count | Integer | Default 0. |

### quota_grants

Tokens that a teacher adds to a student's limit for one day (the quota refill). The limit of a day is `users.daily_token_quota` plus the grants of that day. Every quota check uses it.

| Column | Type | Notes |
| --- | --- | --- |
| id | UUID | Primary key. |
| user_id | UUID | Foreign key to `users.id`. The student. |
| date | Date | The day the grant is valid for. |
| tokens | Integer | More than 0. |
| granted_by | UUID | Nullable. Foreign key to `users.id`. The teacher. |
| created_at | Timestamp | |

## Reports

### audit_reports

A report that a model writes for a teacher, on a lab (`student_id` is null) or on one student in it. The numbers in `facts` are counted from stored rows, never by the model.

| Column | Type | Notes |
| --- | --- | --- |
| id | UUID | Primary key. |
| lab_id | UUID | Foreign key to `labs.id`. |
| student_id | UUID | Nullable. Foreign key to `users.id`. |
| status | String(16) | `running`, `done` or `failed`. |
| content | Text | Nullable. The report, in Markdown. |
| facts | JSON | Nullable. The numbers and questions that the model was given. |
| model | String(255) | Nullable. The model that wrote it. |
| error | String(255) | Nullable. Why it failed (for example `no_questions` or `interrupted`). |
| created_by | UUID | Nullable. Foreign key to `users.id`. The teacher who asked. |
| created_at | Timestamp | |
| finished_at | Timestamp | Nullable. |

## Documents

A teacher uploads a PDF. The worker reads it into pages and blocks, and indexes the blocks as chunks. The next tables hold that content. They were added by the migrations `m1_document_snapshots_v1`, `m2_structured_ingest_v1`, `m2c_structured_review_v1`, `m3a_pdf_parse_v1` and `m3b_pdf_index_v1`. The terms:

- A *source* is one uploaded file, kept as it was.
- A *revision* is one reading of one source. It starts as a draft. When it is published, it replaces the earlier published revision, which becomes *superseded*. A teacher's edit or a new reading makes a new draft and never changes an older revision.
- A *document* is the thing the teacher sees in the list. It points to its current revision.

### documents

One row per document of a lab (`lab_id` set) or of a whole class (`lab_id` null).

| Column | Type | Notes |
| --- | --- | --- |
| id | UUID | Primary key. |
| class_id | UUID | Foreign key to `classes.id`. Part of the access boundary. |
| lab_id | UUID | Nullable. Foreign key to `labs.id`. Null means class-wide. |
| filename | String(512) | The name shown to the teacher. |
| storage_path | String(1024) | Path inside the `documents_data` volume. |
| content_hash | String(64) | Hash of the file. |
| status | Enum | `pending` (default), `processing`, `indexed`, `partially_indexed`, `failed` or `needs_review`. When a revision is published, the status is `indexed` (every page was read) or `partially_indexed`. |
| error_message | Text | Nullable. The reason for `failed` or `needs_review`. |
| language | String(8) | `fr` (default) or `en`. Found from the text when a revision is indexed. It selects the full-text configuration. |
| page_count | Integer | Nullable. |
| ingest_report | JSON | Nullable. A copy of the report of the published revision (chunk count, pages not read, and so on). |
| uploaded_by | UUID | Foreign key to `users.id`. |
| lifecycle | String(16) | `active` (default), `archived` or `deleted`. |
| generation | Integer | Default 0. Goes up with each change. A request that carries an old value gets `409`. |
| current_revision_id | UUID | Nullable. The published revision. |
| structured | Boolean | Default false. True for documents read by MinerU. Older documents are false. |
| deleted_at | Timestamp | Nullable. |

### document_sources

The uploaded files. Unique per (`document_id`, `sha256`), so the same file sent twice is one source.

| Column | Type | Notes |
| --- | --- | --- |
| id | UUID | Primary key. |
| document_id | UUID | Foreign key to `documents.id`. |
| sha256 | String(64) | |
| format | String(16) | `pdf` for new uploads. |
| original_filename | String(512) | |
| storage_key | String(1024) | Unique. Where the file is stored. |
| replaces_source_id | UUID | Nullable. The source this one replaces. |
| created_by | UUID | Foreign key to `users.id`. |
| created_at | Timestamp | |

### document_revisions

The readings of a source.

| Column | Type | Notes |
| --- | --- | --- |
| id | UUID | Primary key. |
| document_id | UUID | Foreign key to `documents.id`. |
| source_version_id | UUID | The source that was read. |
| parent_revision_id | UUID | Nullable. The revision this one was made from. |
| preview_pdf_asset_id | UUID | Nullable. The PDF used to show the revision. |
| schema_version | Integer | Default 1. |
| reason | String(16) | `upload`, `reparse` or `teacher_edit`. |
| processing_state | String(16) | `queued` (default), `processing`, `parsed`, `ready`, `partial` or `failed`. `partial` means a page could not be read. |
| publication_state | String(16) | `draft` (default), `published` or `superseded`. |
| reader_version | String(64) | Which version of the reader made it. |
| config_hash | String(64) | |
| report | JSON | What happened during reading and indexing. |
| index_ready | Boolean | Default false. True when the chunks are stored. A revision cannot be published before. |
| created_by | UUID | Foreign key to `users.id`. |
| created_at | Timestamp | |
| published_at | Timestamp | Nullable. |

### document_pages

The pages of a revision.

| Column | Type | Notes |
| --- | --- | --- |
| revision_id | UUID | Part of the primary key. Foreign key to `document_revisions.id`. |
| page_id | UUID | Part of the primary key. |
| source_version_id | UUID | |
| order_index | Integer | Position in the revision, from 0. |
| source_slide_no | Integer | Page number in the uploaded file, from 1. |
| preview_page_no | Integer | Nullable. Page number in the preview PDF. Null while the mapping is not proven. |
| title | Text | Nullable. |
| state | String(16) | `usable` (default), `partial` or `unavailable`. |
| issues | JSON | Problems found on the page. |
| width, height | Float | Nullable. Page size. |
| preview_asset_id | UUID | Nullable. Foreign key to `document_assets.id`. |

### document_blocks

The blocks of text, code, formula, table or figure on a page. A teacher's edits change a block of a new draft.

| Column | Type | Notes |
| --- | --- | --- |
| revision_id | UUID | Part of the primary key. |
| block_id | UUID | Part of the primary key. |
| source_version_id | UUID | |
| page_id | UUID | |
| order_index | Integer | Position on the page, from 0. |
| kind | String(16) | `heading`, `text`, `code`, `formula`, `table`, `figure` or `navigation`. |
| content | Text | The text that search and citations use. |
| content_format | String(16) | `plain` (default), `markdown` or `latex`. |
| source_text | Text | Nullable. The text as the reader first found it. |
| source_ref | JSON | Where the block is on the page (page number, region). |
| section_path | JSON | The headings above the block. |
| content_description | Text | Nullable. A description of a picture. |
| context_description | Text | Nullable. |
| description_origin | String(16) | `none` (default), `model` or `teacher`. |
| source_completeness | String(16) | `complete`, `incomplete` or `unknown` (default). |
| index_state | String(16) | `searchable`, `needs_review` (default) or `excluded`. |
| issues | JSON | |
| edited_by_teacher | Boolean | Default false. |
| structure | JSON | Nullable. Structure that plain text cannot hold, for example a table with merged cells. |

### document_assets

The stored files of a source: page previews, picture crops and the preview PDF.

| Column | Type | Notes |
| --- | --- | --- |
| id | UUID | Primary key. |
| source_version_id | UUID | Foreign key to `document_sources.id`. |
| storage_key | String(1024) | Unique. |
| sha256 | String(64) | |
| mime | String(128) | |
| width, height | Integer | Nullable. |
| kind | String(32) | `embedded_image`, `figure_crop`, `page_preview`, `pdf_preview`, `formula_source` or `figure_preview`. |
| access_class | String(32) | `teacher_original` or `student_visible`. |

### document_block_assets

Links a block to its files. The primary key is (`revision_id`, `block_id`, `asset_id`).

| Column | Type | Notes |
| --- | --- | --- |
| revision_id | UUID | |
| block_id | UUID | |
| asset_id | UUID | Foreign key to `document_assets.id`. |
| source_version_id | UUID | |
| role | String(16) | `primary` (default) or `preview`. |
| bbox | JSON | Nullable. Region of the file that belongs to the block. |

### document_block_links

Links between two blocks of a revision (one continues or explains the other). The primary key is (`revision_id`, `from_block_id`, `to_block_id`, `relation`).

| Column | Type | Notes |
| --- | --- | --- |
| revision_id | UUID | |
| from_block_id | UUID | |
| to_block_id | UUID | Must differ from `from_block_id`. |
| relation | String(16) | `continues` or `explains`. |
| basis | String(24) | `explicit_source` or `teacher`. |
| source_ref | JSON | |

### document_reviews

Items that wait for the teacher's check. Each is tied to a revision and a block, with a candidate text from the model or from an earlier edit. Unique per (`revision_id`, `source_key`), and per (`document_id`, `source_key`) for rows without a revision.

| Column | Type | Notes |
| --- | --- | --- |
| id | UUID | Primary key. |
| document_id | UUID | Foreign key to `documents.id`. |
| source_version_id | UUID | Nullable. |
| revision_id | UUID | Nullable. |
| block_id | UUID | Nullable. |
| kind | String(32) | Nullable. `teacher_edit_unmatched` (an edit that no longer fits the new reading) or `vision_description` (a description from the vision model). |
| file_sha256 | String(64) | |
| source_key | String(64) | A stable key of the place in the file. |
| source_ref | JSON | |
| page_no | Integer | |
| bbox | JSON | Region on the page. |
| reason | Text | Why the item needs a check. |
| candidate_content | Text | Nullable. |
| status | String(16) | `pending` (default), `confirmed` or `ignored`. |
| revision | Integer | Default 1. Goes up when the item changes, so a stale decision is refused. |
| final_content | Text | Nullable. The text the teacher settled on. |
| resolved_by | UUID | Nullable. Foreign key to `users.id`. |
| resolved_at | Timestamp | Nullable. |
| created_at | Timestamp | |

### document_cleanup_jobs

One row per deleted document, so that its files and chunks are removed in the background. Citations to a deleted document stay as rows and are marked unavailable.

| Column | Type | Notes |
| --- | --- | --- |
| document_id | UUID | Primary key. Foreign key to `documents.id`. |
| state | String(16) | `pending` (default), `failed` or `complete`. |
| attempts | Integer | Default 0. |
| next_attempt_at | Timestamp | |
| last_error_code | String(64) | Nullable. |
| remaining_storage_keys | JSON | The files still to remove. |

### document_staged_objects

A file written to storage before the row that owns it exists. A cleanup removes it if the row never appears.

| Column | Type | Notes |
| --- | --- | --- |
| storage_key | String(1024) | Primary key. |
| document_id | UUID | |
| job_id | UUID | Nullable. |
| purpose | String(32) | `upload_source` or `parse_asset`. |
| lease_expires_at | Timestamp | After this time the file may be removed. |
| created_at | Timestamp | |

## Search

### doc_chunks

The passages that students' questions search: hybrid search with a vector and a full-text column. A chunk belongs to one block of one revision. `class_id` and `lab_id` are copied from the document so that every search filters on them without a join. This filter is the access boundary.

| Column | Type | Notes |
| --- | --- | --- |
| id | UUID | Primary key. |
| document_id | UUID | Foreign key to `documents.id`, `ON DELETE CASCADE`. |
| class_id | UUID | Copied from the document. |
| lab_id | UUID | Nullable. Copied from the document. |
| revision_id | UUID | Nullable. The revision the chunk belongs to. |
| block_id | UUID | Nullable. The block the chunk was cut from. |
| chunk_index | Integer | Position in the revision. |
| part_index, part_count | Integer | Nullable. Which part of the block this is, and how many parts there are. |
| content_start, content_end | Integer | Nullable. Character range of the chunk inside the block's content. Citations use it. |
| token_estimate | Integer | Nullable. |
| chunker_version | String(64) | Nullable. |
| content | Text | The text of the chunk, as searched and cited. |
| source_text | Text | Nullable. The text as first read, kept when the teacher edits `content`. |
| source_ref | JSON | Nullable. Where the text is in the file. |
| source_fingerprint | String(64) | Nullable. Fingerprint of the original text, used to carry a teacher's edit to a new reading. |
| context | Text | Nullable. Unused by the current worker. |
| section | String(512) | Nullable. The headings above the chunk. |
| embedding | vector(1024) | The embedding of `content`. |
| tsv | tsvector | Nullable. The full-text column, in the language of the document. |
| page_no | Integer | Nullable. Source page. |
| edited_by_teacher | Boolean | Default false. |
| created_at | Timestamp | |

The columns `revision_id` to `chunker_version` were added by migration `m1_document_snapshots_v1`, `source_text` and `source_ref` by `scripts/worker_page_sources.sql`, and `source_fingerprint` by `scripts/add_chunk_source_fingerprint.sql`. Indexing a revision first deletes its chunks and then writes them again, so a run can be repeated safely. The ordinary indexes on `class_id`, `lab_id`, `document_id` and `revision_id` exist. The HNSW and GIN indexes do not (see the known gap above).

## Job queue

### ingestion_jobs

The queue of the worker. A job is claimed with `FOR UPDATE SKIP LOCKED`. There is one job per revision (unique `revision_id`).

| Column | Type | Notes |
| --- | --- | --- |
| id | UUID | Primary key. |
| document_id | UUID | Foreign key to `documents.id`, `ON DELETE CASCADE`. |
| status | Enum | `queued` (default), `processing`, `done` or `failed`. |
| job_type | Enum | `ingest`, the only kind. The column stays so that the worker can dispatch another kind later. |
| task_kind | String(32) | Nullable. `pdf_parse` (read the pages), `pdf_index` (index the blocks). `odp_parse` is still allowed by the constraint, but an ODP job fails with `odp_not_supported`. |
| source_version_id | UUID | Nullable. The source to read. |
| revision_id | UUID | Nullable. The draft revision to fill. |
| payload | JSON | Nullable. While a PDF is read it holds `{"progress": {"stage", "done", "total", "started_at"}}`. The claim that holds the job writes it every 5 seconds. It is cleared when the job moves on to indexing or is given back. |
| priority | Integer | Default 100. Lower goes first. 0 is urgent and passes the worker's gates. |
| attempts | Integer | Default 0. |
| lock_token | UUID | Nullable. Who holds the claim. |
| claimed_generation | Integer | Nullable. The document generation the claim saw. |
| lease_expires_at | Timestamp | Nullable. Until when the claim may write. A result is stored only while the token, the generation and the lease still match. |
| locked_at | Timestamp | Nullable. |
| error_message | Text | Nullable. |
| created_at | Timestamp | |
| updated_at | Timestamp | |

A job whose lease ran out is claimed again. Columns from `task_kind` to `lease_expires_at` were added by migration `m2_structured_ingest_v1`.

## Research agent

These tables are written by the chat in all three answer modes. They were added by migration `m4c_research_persistence_v1`. The columns `messages.clarification` and `messages.answer_mode` and `classes.exam_mode` came later (see above).

### agent_runs

One row per chat request. A session can have only one running run at a time (unique index on `session_id` where the status is `running` or `cancelling`).

| Column | Type | Notes |
| --- | --- | --- |
| id | UUID | Primary key. |
| user_id | UUID | Foreign key to `users.id`. |
| session_id | UUID | Foreign key to `sessions.id`. |
| client_request_id | String(128) | Unique per user. The same value returns the saved run. |
| question | Text | |
| status | String(16) | `running` (default), `cancelling`, `finished`, `failed` or `cancelled`. |
| snapshot_map | JSON | Which revision of each document the run used, frozen when it starts. |
| user_message_id, assistant_message_id | UUID | Nullable. Foreign keys to `messages.id`. |
| stop_reason | String(64) | Nullable. For example `clarification` for a question back. |
| settlement_status | String(16) | `open` (default) or `settled`. Whether the quota was settled. |
| started_at | Timestamp | |
| finished_at | Timestamp | Nullable. |

### message_citations

The sources an answer cites. There is no foreign key to the document: when a document is deleted, the citation is marked `unavailable` and the row stays. Unique per (`message_id`, `label`).

| Column | Type | Notes |
| --- | --- | --- |
| id | UUID | Primary key. |
| message_id | UUID | Foreign key to `messages.id`. |
| label | String(24) | `E1`, `E2`, ... |
| document_id, revision_id, page_id | UUID | The cited place. |
| pdf_page_no | Integer | More than 0. |
| block_id | UUID | Nullable. |
| range_start, range_end | Integer | Nullable. Character range in the block. Both null for a visual observation. |
| asset_id | UUID | Nullable. The picture, for a visual observation. |
| origin | String(32) | How the evidence was found. |
| availability | String(16) | `available` (default) or `unavailable`. |

### usage_reservations

The tokens held on a student's daily quota before a model call, settled afterwards.

| Column | Type | Notes |
| --- | --- | --- |
| call_id | UUID | Primary key. |
| run_id | UUID | Foreign key to `agent_runs.id`. |
| user_id | UUID | Foreign key to `users.id`. |
| date | Date | The day of the quota. |
| reserved_tokens | Integer | |
| estimated_input_tokens, max_output_tokens | Integer | The estimate behind the reservation. |
| token_alpha, token_beta | Float | The weights used. |
| purpose | String(32) | |
| model | String(255) | |
| settled_tokens | Integer | Nullable. |
| state | String(16) | `active` (default), `unknown` or `settled`. An `unknown` reservation still counts against the quota until it is reconciled. |
| expires_at | Timestamp | |
| created_at | Timestamp | |

### model_call_usage

One row per model, embedding or rerank call of a run.

| Column | Type | Notes |
| --- | --- | --- |
| call_id | UUID | Primary key. |
| run_id | UUID | Foreign key to `agent_runs.id`. |
| purpose | String(32) | `agent`, `answer`, `answer_check`, `citation_correction`, `vision`, `embedding` or `rerank`. |
| model | String(255) | |
| input_tokens, output_tokens | Integer | Nullable. |
| usage_source | String(16) | `actual`, `estimated` or `none`. |
| token_alpha, token_beta | Float | |
| billed_tokens | Integer | |
| elapsed_seconds | Float | |
| status | String(24) | |
| created_at | Timestamp | |

### session_read_state

Not used any more. It held the follow-up source selection, which was removed. The table stays because the `m4c` migration creates it.

| Column | Type | Notes |
| --- | --- | --- |
| session_id | UUID | Primary key. Foreign key to `sessions.id`. |
| state_revision | Integer | Default 0. |
| focused_sources, last_used_citation_ids, open_question | JSON | |
| updated_at | Timestamp | |

## Tables of removed features

The tables `learner_profiles`, `router_query_log` and `agent_trace_log` belonged to the removed LangGraph chat path. No model defines them any more and nothing writes them. A database created earlier may still hold them and their rows. An administrator can drop them.
