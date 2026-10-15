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
     found pages and their neighbours, one short answer citing `[E#]`; below a
     best reranker score of 0.001 it answers "not found".
   - `direct` (`agent/direct.py`): one short answer without the course files
     and without citations.
   The Search and Direct answers are short unless the teacher's rules ask for
   more detail.
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

A deferred, load-aware worker that never preempts live students. Details:
[PDF_INGESTION.md](PDF_INGESTION.md).

- **Input = PDF.** Teachers export slides and notes to PDF before upload
  (1 slide = 1 page). An ODP, Markdown or HTML upload is refused
  (`odp_not_supported`, `only_pdf`). The pre-MinerU pipeline (pymupdf text,
  chunk context written by an ingest model, VLM formula repair, the page
  reader and its page reviews) was removed on 2026-10-15; its code is on
  branch `archive/cleanup-2026-10-15`. Documents it indexed stay listed and
  can be deleted, but the research agent reads only structured revisions.
- **`worker` service** (in docker-compose): shares the backend code and DB; its
  entrypoint is a polling loop (`backend/worker/main.py`).
- **One structured job per revision** (`FOR UPDATE SKIP LOCKED`, a lock token
  and a lease): MinerU reads each page (`structured_ingest.py`), the parsed
  draft is stored as pages, blocks and assets, then the same job indexes it
  (`structured_index.py`: exact character ranges, BGE-M3 embeddings, a BM25
  `tsvector` in the document's language). A draft becomes searchable only when
  the teacher publishes it.
- **"When to run" lives in the worker** (`gpu_gate.py`): the primary signal is
  live chat load (recent messages in the DB, with hysteresis); the GPU idle
  check (`pynvml`) is an optional extra condition that no-ops without a local
  GPU. No external cron.
- **Hot-reloaded settings:** the embedding endpoint and the MinerU server come
  from `SystemConfigs` (admin model map) and change without a worker restart.
- **Crash recovery:** a job whose lease ran out is claimed again; a stale
  result (another claim, a new generation, a deleted document) writes nothing.

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

