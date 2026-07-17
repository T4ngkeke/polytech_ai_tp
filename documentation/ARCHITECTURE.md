> Part of the Edu-LLM documentation. Back to [README](../README.md).

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

