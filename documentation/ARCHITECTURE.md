> Part of the Edu-LLM documentation. Back to [README](../README.md).

## 6. POST `/api/chat/stream` — Agent Logic Spec

**Payload:**

```json
{
  "session_id": "uuid",
  "message": "string",
  "client_request_id": "uuid (optional)",
  "mode": "direct | search | research (default research)"
}
```

The research agent answers every chat; it needs `AGENTIC_SEARCH_ENABLED` and a
session in a lab. Without the flag the endpoint answers `503 chat_not_enabled`,
for a session without a lab `400 session_without_lab`. The LangGraph path
(router, self-evaluation, query rewrite, learner coaching) was removed on
2026-10-15; its code is on branch `archive/cleanup-2026-10-15`.

1. **Security & context.** The session belongs to the user; the user is a member
   of the class that owns the lab, unless it is the owning teacher's `is_test`
   test-drive session; the lab is open. The daily quota (billed tokens) is
   checked: `429` when it is used up.
2. **Teacher rules.** Active class, lab and student rules become
   `[CLASS RULES]`, `[LAB RULES]` and `[STUDENT RULES]` in the system prompt of
   every mode.
3. **Mode.** A class in exam mode answers in `direct` mode whatever is asked.
   - `research` (`agent/runner.py`): one model conversation with the course tools
     (search, open, read, grep, view_image) and `ask_student`, within the admin
     limits; citations are verified `[E#]` evidence of the passages read.
   - `search` (`agent/search.py`): one search with the question as written, the
     found pages and their neighbours, one answer citing `[E#]`; below a best
     reranker score of 0.001 it answers "not found".
   - `direct` (`agent/direct.py`): one answer without the course files and
     without citations.
4. **Scope.** The course files of a run are the published PDF revisions of the
   lab and of the class (class-wide documents), frozen when the run starts.
   Every search filters `class_id` and `lab_id` (or `lab_id IS NULL`) in SQL.
5. **Saving and billing** (`services/research_service.py`). Each model call is
   reserved on the student's quota before it runs and settled with the reported
   usage (`model_call_usage`, billed as `prompt·TOKEN_ALPHA +
   completion·TOKEN_BETA`). The answer, its citations and its mode are saved
   (`agent_runs`, `messages`, `message_citations`) before any of it is sent. The
   same `client_request_id` returns the saved run, without a second call.
6. **Streaming.** Server-sent events: `status` (phase, mode, exam mode), `draft`
   (the reply as it is written), `draft_reset`, the checked answer, then
   `clarification` (a question back), `citations` and `done`, or `error`; a
   keep-alive comment every 15 s. See [API.md](API.md).

Models and limits come from `SystemConfigs` (admin model map) with environment
fallbacks: the default model, the agent, vision, embedding, reranker, ingest
model and the MinerU server.

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

## 8. Adaptive Tutoring — removed

Prompt-literacy coaching (an effort score per message, a smoothed level per
student and lab, a Socratic guardrail for answer-seeking messages) ran inside
the LangGraph router and was removed with it on 2026-10-15. Teachers set the
tutor's behaviour with their class, lab and student rules, which every answer
mode follows.

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

