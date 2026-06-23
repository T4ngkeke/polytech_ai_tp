# Edu-LLM v7 — Dev Progress & Plan

> Method: strict TDD (RED → GREEN → verify). SQLite for pure-ORM logic;
> live pgvector Postgres (`pg_session`, port 5433) for SKIP LOCKED + vector ANN.

## Done
- **Data model** (`backend/app/models.py`): `Document`, `IngestionJob`, `DocChunk`,
  `Exercise` (+ `DocumentStatus`/`JobStatus`); dialect-aware `GUID` / `EmbeddingVector`.
- **Worker claim loop** (`backend/worker/queue.py`): `claim_next_job` — `FOR UPDATE SKIP
  LOCKED`, stale-lock reclaim, no double-claim.
- **Retrieval** (`backend/app/services/retrieval_service.py`): `search_exercises`
  (lab-scoped, solution-free), `rag_search` (pgvector cosine).
- Tests: `test_documents.py`, `test_worker_queue.py`, `test_retrieval.py`. Suite: 184 passed.

### A. Document upload service (producer side) — `backend/app/services/document_service.py` ✅
- `create_document(...)` stores file under storage root, hashes content, inserts
  `Document(pending)`, enqueues `IngestionJob(queued)`. Dedup on (class, lab, hash).
- TODO: `delete_document` cascade (deferred).

### B. Worker ingest pipeline — `backend/worker/ingest.py` ✅
- `ingest_document(db, document_id, *, embed_fn, extract_fn)` — DI so tests use fakes.
- Idempotent (clears prior chunks/exercises); writes lab/class-scoped `DocChunk` +
  `Exercise`; marks `indexed`; on error rolls back partial writes → `failed` + message.
- Tests: write, idempotent re-run, failure-path rollback.

## Done (this round — simple/independent first)
- **`backend/worker/gpu_gate.py`** ✅ — `GpuGate.should_run()`: off-peak passthrough +
  windowed-average + hysteresis (start/stop watermarks). Injected `util_fn`/`is_offpeak_fn`.
- **Teacher upload endpoint** ✅ — `POST /api/teacher/labs/{id}/documents` (ownership-checked)
  → `create_document`. Added `DocumentResponse`, `settings.DOCUMENTS_STORAGE_ROOT`,
  overridable `get_storage_root` dep.
- **`backend/worker/main.py::process_one`** ✅ — claim → ingest → mark job `done`/`failed`;
  returns False on empty queue.

## Done — Stage 3 LangGraph agent ✅
- **`backend/app/agent/router.py`** — `classify_intent` (rules/regex): exercise→agentic_search,
  concept→rag, else→direct.
- **`backend/app/agent/prompt.py`** — `build_system_prompt`: persona → context → Class/Lab/
  Student rules LAST (constraints win).
- **`backend/app/agent/graph.py`** — `build_agent(db, embed_fn)`: real LangGraph `StateGraph`
  `START→router→[agentic_search|rag|direct]→synthesize→END`, builds `messages_payload`.
  Lab-scoped retrieval; agentic_search never includes `solution`.
- **`/api/chat/stream`** rewired: gather rules+history → run agent → stream payload via the
  existing SSE (kept proven streaming + background save). All 10 prior chat tests still green.

## Done — worker run loop ✅
- `backend/worker/main.py`: `run_tick(db, gate, *, embed_fn, extract_fn, sleep_fn)` — gates on
  GpuGate, processes ≤1 job, sleeps when closed/idle (tested both branches). `run_forever`
  thin glue. Plus production glue (`# pragma: no cover`): real embed/extract (guided JSON
  schema), `_pynvml_util`, `main()` entrypoint → `python -m backend.worker.main`.

## Done — Stage 6: Prompt-Literacy Coaching ✅
- `agent/lazy.py::detect_answer_seeking` — answer-demand AND no own attempt (false-positive guarded).
- `agent/effort.py::assess_effort` — 0–1 heuristic (attempt / exercise ref / concrete error / prose).
- `models.CoachingLevel` + `LearnerProfile` (per student+lab) + `services/learner_service.record_effort`
  — EMA window + hysteresis coaching level + warmup neutral + teacher override.
- Wired into `agent/graph.py` via a `tutor` node (START→tutor→router): answer-seeking →
  Socratic guardrail (last/strongest in prompt); coaching level → neutral strategy text.
- `build_system_prompt` extended with `coaching_strategy` + `answer_seeking` (placed last).

## (superseded) Stage 6 plan — Prompt-Literacy Coaching
Rules-first, no LLM. Two layers (see README §8 / v7 doc Feature 6). Decisions locked:
A2 (persist per student+lab) · B2 (silent-mostly + throttled tip) · C (pure heuristics) ·
D (neutral cold start) · E (governance: structured only, teacher-overridable).

TDD targets (ascending dependency):
1. **`agent/lazy.py::detect_answer_seeking(message) -> bool`** — "give me the answer to Q2"
   = True; "I think it's X because Y, right?" = False (keys on absence of own attempt).
   *Include false-positive guard tests.*
2. **`agent/effort.py::assess_effort(message) -> float`** (0–1) — heuristics: code-dump vs.
   explanation ratio, "what I tried", concrete error, exercise ref.
3. **`LearnerProfile` model + `learner_service`** — EMA update (smoothed window),
   `coaching_level` via hysteresis, throttle marker; per (student, lab); teacher-overridable.
4. **Wire into agent**: `detect_answer_seeking` → state flag → synthesize injects strict
   Socratic guardrail; effort EMA trend → student-tier coaching strategy + throttled tip.

## Done — deployment wiring ✅
- `docker-compose.yml`: postgres → `pgvector/pgvector:pg16`; added **worker** service +
  shared `documents_data` volume + `DOCUMENTS_STORAGE_ROOT`.
- `CREATE EXTENSION IF NOT EXISTS vector` before `create_all` (main.py lifespan + seed.py, pg only).
- `seed.py` seeds `EMBEDDING_MODEL`; worker `main()` loads config from SystemConfigs (fallback settings).
- `requirements.txt`: + `langgraph`, `nvidia-ml-py`, `pgvector`.

## Remaining (genuinely optional)
- Pick a **1024-dim embedding model** (or change `EMBEDDING_DIM`) so it matches the column.
- Endpoint-level RAG integration test (PG-backed client + mocked embeddings/LLM).

## Note: SSE approach
Agent assembles the payload; token streaming stays in the endpoint (AsyncOpenAI) rather than
`graph.astream_events`. Same behavior, far simpler + keeps the proven SSE/quota path. Can move
streaming into a graph node later if multi-step streaming is needed.

## Later (unchanged from staged plan)
Stage 3 LangGraph chat rewrite · Stage 4 gpu_gate scheduling · Stage 5 teacher document UI ·
Stage 6 adaptive tutoring (deferred).

## Follow-ups / debt
- Ephemeral test DB container `edullm-test-db` (port 5433) is disposable.
- Title strings still say "v5/v6" in a few FastAPI/docstring spots (cosmetic).
