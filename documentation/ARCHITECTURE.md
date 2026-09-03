> Part of the Edu-LLM documentation. Back to [README](../README.md).

## 6. POST `/api/chat/stream` — Agent Logic Spec

**Payload:**

```json
{
  "session_id": "uuid",
  "message": "string"
}
```

**Step-by-step Logic (LangGraph agent):**

1. **Security & Context Resolution**
   * Fetch the Session to find `lab_id`. Verify `session.user_id == current_user.id`.
   * Verify the user has joined the Class that owns this Lab — unless it is the
     owning teacher's `is_test` **test-drive** session, which bypasses the check.
   * Verify the Lab `is_active == True` (reject if locked). Resolve `class_id` —
     the tenant filter passed into every retrieval call.

2. **Rate Limit** — DB-based daily quota check; `429` if over (billed tokens; the
   router call is **not** charged to the student).

3. **LangGraph Graph Execution**
   ```
   Router — ONE ROUTER_MODEL call, json_schema output:
     {route: rag|direct, search_terms, effort, answer_seeking}
     inputs: the message (delimited as data) + the last 1-2 turns
     timeout 1-2s / failure → route=rag + degraded (routing never drags down chat)
     every decision → RouterQueryLog
        │
        ├─ rag  → embed (fail → BM25-only) → hybrid vector+BM25 → RRF
        │            → rerank(augmented) → threshold gate
        │            ├─ ≥1 survive → context injection ("possibly relevant; ignore
        │            │               if not") → Synthesize + citations
        │            └─ all filtered → disclaimer-direct ("not covered by the
        │                              material"; no injection, no citations)
        └─ direct → Synthesize
                       ↓
   Tutor — folds `effort` into the per-(student, lab) EMA and derives a coaching
     strategy. Runs on BOTH legs; skipped entirely for `is_test` sessions.
                       ↓
   Self-eval (observation window): deterministic gate kept; the LLM verdict and
     rewrite run on the ROUTER_* slot, recorded to AgentTraceLog.
                       ↓
   Synthesize (skill.md → 3-tier rules → context + citations → history → question)
   ```
   * **Two routes, not four.** `exercise` and `clarify` existed to serve the
     Exercises table: one looked a numbered exercise up, the other asked which
     number the student meant. Both are gone. A model that still emits either
     falls outside the route enum and degrades to `rag`, so a stale prompt or a
     stale fine-tune cannot smuggle them back in.
   * **Per-exercise guidance is a prompt, not a table.** A teacher who wants
     something specific said about an exercise writes it into their class or lab
     rules. Keep it short: rules are resident text, paid for on every message.
   * **Citation iron rule**: any retrieval-touched path **must** cite; the
     zero-injection disclaimer branch must **not**.
   * **The tenant filter is mandatory and in SQL**: every `rag` query filters
     `lab_id` / `class_id` **in the WHERE clause**. Material may be class-wide
     shared (`class_id AND (lab_id=X OR lab_id IS NULL)`). There is no audience
     filter any more — every uploaded file is course material the whole class can
     read, so tenant scoping carries the boundary alone.
   * **Coaching follows the student, not the route.** `effort` and
     `answer_seeking` used to be emitted only for the exercise lane. A student
     can offload on a concept question just as easily, so both signals now ride
     on whatever route the message took.
   * **Degradation matrix**: the only hard dependencies are the **main LLM** and
     the **main DB**; router→rag, embedding→BM25-only, rerank→fusion order,
     self-eval→skipped — each flagged in `AgentTraceLog` and surfaced by the
     admin health panel.

4. **Dynamic LLM Config (Zero-Downtime)**
   * Fetch active `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL`, `EMBEDDING_MODEL`,
     `RERANK_URL` / `RERANK_MODEL` / `RAG_MAX_RETRIES` / `RERANK_SCORE_THRESHOLD`
     from `SystemConfigs`. Nothing is hardcoded — generation, embeddings, and reranking are all
     config-driven HTTP. Swapping a model or threshold is a config change, not a code change.

5. **SSE Streaming & Safe Async Write**
   * Stream tokens via SSE from the synthesize payload. After the token stream, emit a final
     **`citations`** event (`document_id` / `filename` / `page_no`, deduped) and a **`done`**
     event (the client uses `done` to stop streaming and refresh quota). *(An optional throttled
     `tip` event for prompt-literacy coaching is deferred to v7.2.)*
   * Use `BackgroundTasks` to insert User message, LLM message (with tokens), and Upsert
     `usage_stats`. **Token accounting sums the Synthesize node's usage** plus any
     extra LLM calls made during routing / self-eval.
   * **Cost-aware quota.** Raw `prompt_tokens` / `completion_tokens` are still recorded
     per message, but the quota is charged on **billed tokens** =
     `prompt·TOKEN_ALPHA + completion·TOKEN_BETA` (admin-set, default `0.2`/`1.0`). The daily
     quota check (`429`) compares accumulated **billed** tokens against `daily_token_quota`, and
     `GET /api/student/usage` reports billed-vs-quota. The per-message `billed_tokens` is also
     stored on the LLM `Message` so class/lab analytics reconcile with the quota (re-deriving it
     later would be wrong once an admin changes the weights). The streaming `create()` call sets
     `stream_options={"include_usage": True}` so real token counts arrive — without it usage
     would be omitted and billing would fall back to a flat estimate.

---

## 7. Ingestion Pipeline

A deferred, load-aware pipeline that never preempts live students.
**No Airflow, no k8s, no LibreOffice.**

Every document takes the same path. There is no type to declare and nothing to
branch on: an upload becomes chunks, and that is all it becomes.

- **`worker` service** (in docker-compose): shares the backend image/code/DB; the
  only difference is the entrypoint — a polling loop instead of uvicorn. It
  reuses the same DB-as-queue pattern already used for rate limiting.
- **Input = PDF, Markdown or HTML.** Instructors export slides (`.ppt/.pptx`) to
  PDF themselves before upload (1 slide = 1 page). An `.md` file is read as text
  and split into **heading pseudo-pages** (shallowest heading level present;
  ``` fences never split), then runs the same character-yield gate and downstream
  pipeline. An `.html/.htm` file is first converted to markdown-ish text
  (`html_to_markdown`): headings → `#`-lines, `<pre>` fenced, page chrome
  stripped, and the **original LaTeX recovered** from KaTeX/MathJax markup
  (`annotation x-tex` / `script math/tex`), so formulas that mangle in a printed
  PDF arrive intact.
- **VLM garbled-formula repair** (`vlm.py`, PDFs only, opt-in via `VLM_MODEL`):
  HTML-printed PDFs mangle math (`2^15` flattens to `215`). Detection is
  **deterministic** (spike-validated per-line rules: word-char ratio, math-symbol
  density, mojibake — code lines and dot-leaders exempt); adjacent hits merge
  into a region, whose **bbox is cropped from the PDF page** (pymupdf) and sent
  to the vision model with a **transcribe-only** prompt (LaTeX for math,
  `[illisible]` for unreadable, never invent).

  The safety check is that the model **reads, it never writes**: a transcription
  carrying prose the region never had is refused and the original kept. LaTeX
  commands are ignored when judging this, since a real repair introduces them by
  definition. Every region lands in `ingest_report.vlm_repairs` (before/after)
  for teacher review, and no failure ever breaks ingestion. Empty `VLM_MODEL` =
  feature off — it never falls back to a text model.
- **Per-job pipeline (concurrency = 1, `FOR UPDATE SKIP LOCKED`):**
  1. **Parse + input gate** (`parsing.py`): pymupdf text extraction; a cheap
     **character-yield gate** (chars/page below threshold, or a high garbage /
     non-word ratio) marks the document `needs_review` and hands it back to the
     teacher — never silently ingest garbage.
  2. **Clean once, upstream**: repeated header/footer lines stripped (two narrow
     rules — byte-identical repeats and pure pagination lines); TOC pages dropped
     (they otherwise match every query). Everything downstream sees clean pages.
  3. **Chunking** (`chunking.py`): code-aware paragraph split (a blank line
     inside an indented block is not a break), document-level paragraph dedup,
     then size control — merge below 120 chars, sentence-split above 1600.
  4. **Contextual Retrieval**: per chunk, the worker LLM generates the chunk's
     in-document context and prepends it; the **augmented** text
     (`context + content`) is embedded and BM25-indexed, while the original
     `content` is stored for citation. This is what makes a context-poor slide
     findable. The context is generated from the chunk's **own local scope** —
     its section when there is one, otherwise its page (1 slide = 1 page) — so a
     long document never gets a context hallucinated from its opening. Generation
     uses `INGEST_MODEL` (defaults to `LLM_MODEL`), so a cheap off-peak model can
     do this without touching the chat model. Calls run concurrently, bounded.
  5. **Build the BM25 column** (`tsvector`, config selected by the document's
     `language`) and record page numbers for citation.
  6. **Report** (`ingest_report`): chunk count, estimated token spend, whether it
     went over the configured budget, and any VLM repairs. The budget is
     **observed, never enforced** — an over-budget document still indexes fully.
- **Teacher-edit protection**: a chunk the teacher corrected (`edited_by_teacher`)
  survives the idempotent rebuild instead of being wiped, matched back by
  `chunk_index`.
- **What used to be here.** The pipeline also built structured `Exercises` rows
  and, from a `corrigé`, `Answers` rows — via an LLM-primary segmenter (a
  per-page line classifier with a regex cross-check), numbering-anomaly
  detection, a content-hash boundary cache and a teacher compare-and-confirm
  step. All of it existed because a row was a hard claim the system then served
  with confidence. With no rows to build, none of it has a job.
- **Config-driven engine, optionally split:** embeddings and Contextual-Retrieval
  generation are read from `SystemConfigs` — nothing hardcoded, no torch. By
  default they fall back to the one chat engine, but an admin can point
  `INGEST_MODEL` / `INGEST_BASE_URL` at a separate cheap model so ingestion
  doesn't share the chat model's RPM pool. These calls run entirely on the
  **off-peak worker** and never preempt students.
- **"When to run" lives in the worker's Python** (`gpu_gate.py`): the **primary
  signal is live chat load** — the worker counts recent `Messages` in the DB and
  pauses ingestion while students are actively chatting (sliding window +
  hysteresis to avoid flapping). **GPU utilization is an optional additional
  condition**: on a local engine the worker also requires the GPU idle via
  `pynvml`; with a remote API there is no local GPU, so `pynvml` reads idle and
  that condition no-ops. The same gate therefore works for both. At night or in a
  no-class window it may pull freely. No external cron.
- **Urgent jobs bypass both gates.** A job at `priority=0` is claimed first and
  skips the chat-load and GPU gates — "do it now" runs during class instead of
  waiting for a quiet window.
- **Crash recovery & idempotency:** stale `processing` jobs past a timeout are
  re-claimed; a re-run deletes the document's old chunks before rebuilding, so
  the result is consistent after a strategy change.

---

## 8. Adaptive Tutoring — Prompt-Literacy Coaching

> **Teaching goal:** teach students **how to ask** — a learnable skill. We judge
> the most observable signal, *how the student asks*, not their mastery. The
> heavy Learner-Model / BKT-DKT / per-concept stack is intentionally dropped.

Both signals come from the one router call, so coaching costs no extra request.
They apply on whatever route the message took: a student can offload on a concept
question as easily as on an exercise.

- **(a) Effort / clarity — windowed, formative.** The router scores each message
  `low` / `mid` / `high` (a bare demand with no attempt; some context but little
  reasoning; a real attempt with reasoning or a specific error). That feeds a
  **smoothed sliding window** (EMA) per *(student, lab)*, and the trend sets a
  *coaching level* that adjusts the tutor's scaffolding. **It never gates, it
  always answers.** A poorly-formed question is never refused — only nudged
  toward more thinking and less copy-paste.

- **(b) Answer-seeking — per-message guardrail.** The router flags a message that
  demands the answer and shows no sign of the student's own attempt. That forces
  a **strict Socratic response**: guiding questions only, never the final answer.
  It **still responds** — the limit is on handing over the answer, not on
  replying. A genuine "I think it is X because Y, right?" is not flagged; the
  signal keys on the **absence of an attempt**, not on the word "answer".

- **Governance:** store **structured score + controlled-vocabulary strategy**
  only — never insulting free text. Profiles are teacher-visible,
  teacher-overridable and transparent to the student.

- **Test-drive sessions are excluded.** A teacher previewing their own lab never
  writes to a student's profile.

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

