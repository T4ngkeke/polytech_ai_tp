> Part of the Edu-LLM documentation. Back to [README](../README.md).

# Architecture

This file says how the parts of Edu-LLM work together: what runs, how a chat question is answered, how a PDF becomes searchable, and how models, quota and monitoring fit in. The endpoints are in [API.md](API.md) and the tables are in [DATABASE.md](DATABASE.md).

## What runs

This section lists the containers and what each one does.

| Part | What it does |
| --- | --- |
| Frontend | nginx serves the React app and passes `/api/` to the backend. It also passes `/grafana/` to Grafana for admins (see [Monitoring](#monitoring)). |
| Backend | FastAPI. It answers every HTTP request, runs the chat, and writes to PostgreSQL. Code: `backend/app/`. |
| Worker | The same image and code as the backend, with another start command. It reads uploaded PDFs and indexes them. Code: `backend/worker/`. |
| PostgreSQL | One database with `pgvector`. It holds the data, the search indexes and the job queue. |
| Model servers | They are separate containers on the Docker network `edullm-models`. Each one speaks the OpenAI HTTP format. The backend and the worker call them. |

There is no message queue. The `ingestion_jobs` table is the queue. The backend never reads a PDF itself.

## Models

This section says which models the system uses and where their settings live. See [DATABASE.md](DATABASE.md) for the setting keys.

- **One model for the whole system.** Qwen3.8 Flash-Next, served under the name `flash-next`, writes the answers, looks at pictures (the `view_image` tool) and writes figure descriptions ("Write with AI"). Slots for the agent and for vision exist in the admin page. An empty agent slot uses the main model. The vision slot has no fallback: empty means that vision is off.
- **Reranker.** `Qwen/Qwen3-Reranker-0.6B`, served by vLLM (container `edullm-reranker`). A reranker reads the question and each candidate passage and gives each one a score.
- **Embeddings.** `BAAI/bge-m3`, served by vLLM (container `edullm-embeddings`). An embedding is a list of numbers that stands for the meaning of a text. Both the question and the passages are turned into embeddings for the vector search.
- **PDF reading.** MinerU is a PDF reading tool. It has a model server (`edullm-mineru-vlm`) and a client in the worker image. See [PDF_INGESTION.md](PDF_INGESTION.md).

The addresses and names are rows in `system_configs`. The admin changes them in the LLM Config tab without a restart. The backend reads them for each request. The worker reads them at each loop and rebuilds its clients when they changed. `backend/app/services/model_routing.py` turns the rows into endpoints. `GET /api/admin/llm/servers` shows what each server serves.

**One client per endpoint.** The backend and the worker keep one HTTP client per model endpoint and event loop (`services/shared_clients.py`) and close them at shutdown. A client made for each call and never closed is closed later by the garbage collector. Under plain asyncio its late close can remove a new connection from the event loop, which then waits for its timeout. This made reranker calls fail after 30 seconds in scripts and tests.

## Answering a chat question

This section follows one request to `POST /api/chat/stream` (`backend/app/routers/chat.py`). There is one chat path. It is the research agent in `backend/app/agent/`.

1. **Checks.** The session must belong to the user. The user must be a member of the class that owns the lab. The one exception is the owning teacher's test-drive session. The lab must be open. The day and week limits must not be reached (`429` otherwise). This is the only quota check: a question that starts always finishes, and the next one is refused. Each refusal is stored in `chat_refusals`. Without `AGENTIC_SEARCH_ENABLED` the endpoint answers `503 chat_not_enabled`. A session without a lab gets `400 session_without_lab`.
2. **Teacher rules.** The active class, lab and student rules go into the system prompt of every answer, as numbered lists under `[CLASS RULES]`, `[LAB RULES]` and `[STUDENT RULES]`. A first line says that the rules take precedence over the student's requests, and that when two rules contradict, a student rule wins over a lab rule and a lab rule over a class rule (`rule_service.teaching_rules_text`). The model is told not to mention a contradiction to the student. Rules of one list can still contradict each other; the teacher page can ask `POST /api/teacher/rule-items/check`, where the model names such pairs (`services/rule_check.py`). Each turn's tool results end with a reminder that repeats the teacher's rules, because after many course pages the rules at the start were often not followed (a class rule "Always answer in English" with a French question: 0 of 4 runs before, 4 of 4 with the rules repeated). A student's rules are those of this class. The list of course files names the files of this TP first, under their own heading, then the class files; for a question about the TP itself the model reads the TP files first.
3. **Exam mode.** A class in exam mode keeps only the files its teacher allowed in the exam (`documents.exam_allowed`): the agent looks only in them, with an `[EXAM MODE]` note in the prompt. With no allowed file, the run has no tools at all: they are withdrawn before the first model call, and a short note tells the model to answer from general knowledge and cite nothing (stop reason `exam_no_files`). Web search, Python, uploads and deep research are off in exam mode (`services/feature_service.py`).
4. **Scope.** The course files of a run are the published PDF revisions of the lab and of the class. They are fixed when the run starts. Every course search filters `class_id` and `lab_id` in SQL. The model never sends a file address or a revision.
5. **Saving and billing** (`services/research_service.py`). Before each model call, the tokens are reserved on the student's quota. After the call, the reserved amount is replaced by the usage the server reported. The answer and its citations are saved before any of it is sent. The same `client_request_id` returns the saved run and makes no second call.
6. **Streaming.** The answer goes out as server-sent events: `status`, `draft`, the checked answer, `citations`, `done`, and sometimes `clarification` or `title`. A keep-alive comment goes out every 15 seconds. The events are listed in [API.md](API.md).

**Answer language.** The courses are in French, and students ask in French or English, sometimes in another language. With long French pages in the prompt, the model often answered English questions in French when it was only told to answer in the student's language. So one sentence at the end of the last user message names the language: "Write the answer in German." A small model call before the answer chooses the name (`agent/language.py`, purpose `answer_language`, billed like the other calls, about 0.3 s). It sees the teacher's rules and the student's last messages, and no course text, so the French pages cannot pull it. It answers the language the teacher's rules ask for, else the language of the last message, else, when the last message shows none (code, a number, "malloc?"), that of the earlier messages, and French when there are none. If the call fails, the sentence asks for the language the student writes in unless the teacher's rules set one. Until November 2026 lists of French and English words chose the language; they sent German questions to English or French answers. Measured on 2 TPs in the three answer modes of the time: word lists answered 2 of 28 German and 0 of 14 Spanish questions in the right language, one sentence without a language name 42 of 56 English questions, the small call 140 of 140. A teacher rule such as "answer in the language of the course" is not understood by it: it does not see the course. The sentence is not in the system message, so the cached prefix does not change.

The first answer of a conversation also gives the conversation its title (`services/session_titles.py`). The same model writes it, and it is not charged to the quota.

### One way to answer

The research agent (`agent/runner.py`) answers every question. The model works in one conversation and calls the course tools as often as it needs, within limits. A reply without a tool call is the answer. It can end the run with a question to the student (`ask_student`). It decides by itself whether to search: a greeting needs no tool, a course question gets searched. An answer that used no tool and cites nothing gets one check turn ("is this really the answer?"); an answer that used web search or Python does not.

Until 2026-11-26 the student chose one of three modes: Direct (one answer without the course), Search (one search, then one answer) and Research (the agent). The new interface of 2026-11-23 sent Research only, so Direct and Search were removed, with the TP text that they read in full (`TP_CONTEXT_PERCENT`). Old answers keep their mode in `messages.answer_mode`. The agent finds the TP itself: its file list names the TP files first.

### The course tools

`agent/course_tools.py` defines the tools. `agent/tools.py` checks the scope and the access before each call: the session must still be open to the student, and each document must still be readable at the revision fixed for the run (`check_access`). A call that touches several documents (a search, the file list) checks the session once and then each document. Before an answer is saved, the documents it cites are checked again the same way (`result.cited_sources`); if access changed during the run, the student gets "Course access changed. Please retry." instead of the answer (stop reason `access_revoked`).

| Tool | What it does |
| --- | --- |
| `search` | Hybrid search (see [Search](#search)). The results are leads, not proof. |
| `open` | Lists the pages of a document with a short summary each. |
| `read` | Reads the text of one page or one block. The result carries the evidence IDs. |
| `grep` | Finds a literal text in one document or in all course files. |
| `view_image` | Asks the vision model a narrow question about a picture. |
| `ask_student` | Ends the run with a question and 2 to 4 short options. The answer is the student's next message. It is not a course tool. |

Tools refer to documents, pages and blocks by short handles such as `d1`, `d1.p20` and `b3`. The handles are removed from the text that the student sees.

### Evidence and citations

`agent/evidence.py` keeps the evidence of a run. Only what the model read with `read`, or looked at with `view_image`, becomes evidence with an ID such as `E1`. Search, open and grep results are leads and get no ID. An answer may cite only IDs that the tools returned in this run, or IDs of earlier answers in the same conversation that still name the same pages. An unknown ID is removed. In Research mode, the model gets one correction turn when it cites a wrong ID, writes a handle in the answer, or cites nothing although it read pages. The cited passages are saved in `message_citations`. The chat shows each cited page with the passage highlighted.

### Limits and history

The limits of one research run are settings of the admin page: tool calls, picture views, research time, total time, time per model call and input tokens per call. The defaults are in [API.md](API.md). When a limit is reached, the tools are withdrawn and the model answers from what it has read. If there is no usable answer, the system lists the verified pages that were read, without a model call. The input tokens of a call are the room for reading, on top of the conversation history; the total stays 6,000 tokens under the model's context.

**The conversation history.** Each question sends the earlier messages of the session that are still safe to send (an answer whose cited file is gone is left out). They may use `HISTORY_CONTEXT_PERCENT` of the model's context (admin page, default 30%: about 39k tokens, 118,000 characters, with the 128k server; it was 8,000 tokens before November 2026). Past the limit the oldest turns go, but not one per question: the history may start only at fixed points of the conversation, one every 30% of the limit counted from its first message, and the earliest point after which the rest fits is used (`agent/history.py`). The start then stays the same for several questions, so the model server can reuse its cache, and the history never starts with an answer. The last 400 messages are read; the size of the older ones is counted, so the points stay where they are in a longer session. Only the answers still sent may have their `[E#]` labels cited again: a label of an answer the model no longer sees is unknown to the check. A question may have at most 20,000 characters. `GET /api/chat/sessions/{id}/history-start` gives the first message still sent, for the line the chat page draws.

## Search

This section describes the search behind the `search` tool (`services/retrieval_service.py`).

- **Two searches on one table.** A vector search compares the question embedding with the passage embeddings (`pgvector`). A full-text search uses a `tsvector` in the language of the document, French or English. Each search takes the best 20.
- **Merge, then rerank.** The two lists are merged by reciprocal rank fusion (a method that rewards a passage that ranks high in both lists). The reranker then orders the merged list. Without a reranker address, the merged order is kept. A reranker call waits at most 3 seconds for a connection and tries a second time once. If it still fails, the merged order is kept and a warning is logged.
- **Without embeddings.** When the embedding server fails, the search uses the full-text list alone.
- **Access in SQL.** Each search filters on `class_id`, `lab_id` and the revisions fixed for the run. A prompt cannot widen it.
- **No index on the search columns.** Neither the vector index nor the full-text index is created by code. See the known gaps in [README.md](../README.md).

## Documents and versions

This section says how a PDF becomes searchable and how a change is published. The worker side is in [Ingestion](#ingestion).

- **PDF only.** Teachers upload a PDF to a lab or to a whole class. Other formats are refused. The limits are 200 MB and 150 pages, and no password. The same file sent again to the same document creates nothing.
- **Revisions.** A revision is one reading of one uploaded file. It has pages, blocks (a heading, a text, a code, a formula, a table or a figure), assets (pictures) and chunks (the passages that are searched). A new revision starts as a draft. Students and the agent read only published revisions.
- **Publishing.** The worker publishes a revision at once when every page was read. If a page failed, the revision stays a draft and the teacher publishes it from the document view.
- **Edits make new drafts.** A teacher edit, an exclusion of a block or a new reading never changes a revision. It makes a new draft. Each change names the document `generation`, a counter that rises with every change. A stale generation gets `409`.
- **Reviews.** A picture description from the vision model, and a teacher edit that no longer fits after a new reading, wait as review items that the teacher confirms or ignores.
- **Citations stay readable.** A citation points to a revision. If a newer revision exists, the student sees that. If the teacher changed the cited text, the citation says so. The endpoints are in the Documents section of [API.md](API.md).
- **Deleting.** Deleting a document is quick for the teacher: students and the agent lose access at once. The stored files and chunks are removed in the background by the worker (see [Ingestion](#ingestion)).

## Ingestion

This section describes the worker. It is a loop that takes one job at a time and never preempts live students. The setup is in [PDF_INGESTION.md](PDF_INGESTION.md). The old worker (text extraction, chunk context written by a model, formula repair, page review) was removed.

- **Queue.** The worker takes a job with `FOR UPDATE SKIP LOCKED`, so two workers never take the same job. A claim has a lock token and a lease (30 minutes by default). A job whose lease ran out is claimed again, up to 3 attempts. A result from a lost claim, from an old generation or for a deleted document writes nothing.
- **Two jobs per upload.** The `pdf_parse` job has MinerU read each page. It stores the pages, blocks and assets as a draft. The same job then becomes a `pdf_index` job. That job cuts the blocks into passages, embeds them with bge-m3, builds the full-text vector and publishes the revision when every page was read. The language (French or English) is found from the common words of the text.
- **When to run.** The worker waits while students chat. It counts the messages of the last two minutes, with two bounds so that it does not switch on and off at one value. An optional check of GPU load (`pynvml`) does nothing on a machine without a local GPU.
- **Progress.** While a PDF waits or is read, the teacher sees the jobs ahead of it, the pages read and the time left.
- **Settings without restart.** The embedding address and the MinerU server come from `system_configs`.
- **Housekeeping.** Between jobs the worker removes staged files that no upload finished, and runs the due cleanup jobs of deleted documents (`document_cleanup.run_due_cleanups`, up to 10 at a time, each in its own transaction). A cleanup that fails is tried again later, waiting longer each time. Until November 2026 nothing ran these jobs, so the files of deleted documents stayed on disk; the worker now removes them at its first round. `scripts/cleanup_document_snapshots.py` runs the same function by hand, for a stack without a worker.

## Quota and billing

This section says how tokens are counted. Each user has a daily and a weekly quota (`users.daily_token_quota`, default 5 000 000; `users.weekly_token_quota`, default 25 000 000). A day starts at midnight Paris time, a week on Monday. A student at a limit can ask for a reset (`quota_reset_requests`); a teacher or an admin gives a full day and week again (`quota_grants`), and usage is kept. A model call is billed as `prompt tokens × TOKEN_ALPHA + completion tokens × TOKEN_BETA`. The two weights are settings (defaults 0.2 and 1.0). Calls are reserved before they run (`usage_reservations`) and recorded after (`model_call_usage`). Embedding and rerank calls are counted too. Code: `services/research_usage.py`, `services/quota_service.py`.

## Teacher and admin tools

This section lists the tools in the web pages and where their data comes from.

- **Teacher page.** A teacher works on a class or on a lab. A class has the tabs Settings, Rules, Analytics and Documents. A lab has Settings, Rules, Analytics, Audit, Students, Documents and Test drive.
  - *Analytics* (`services/analytics_service.py`) shows counts (with how many answers with a course citation had their page opened by the student: `citation_opens`), questions by day and hour, each student, the questions with a thumbs-down and the answers that failed. Teacher test sessions are left out.
  - *Audit* has three views: the conversations by student, the course use report (`services/citation_report.py`: which files, pages and passages the answers cite, and which files nobody cited) and AI reports (`services/teaching_reports.py`). An AI report is written in the background by the model, on request, from numbers that the system counted first. It is stored in `audit_reports`.
  - *Test drive* is a chat session of the teacher in a lab, shown as a student sees it.
- **Admin page.** The tabs are Users, Classes, Documents, Monitoring, LLM Config and Maintenance. Classes shows every class with its teacher, its counts and its activity, and the platform totals. Documents lists every course file with its state. LLM Config (since 2026-11-19) has five parts and one save bar: the model servers with the shared key and a Test button each (`POST /api/admin/llm/servers/check` tests the values on the page before they are saved), what each feature uses (with switches for image views and reranking, and a second chat model for student answers or image views), answer limits with the token and character count of each context share, PDF upload (picture captions) and billing. It saves only what changed, and asks before the admin leaves the tab with unsaved changes. The mapping between the page and the stored settings is in `frontend/src/lib/llmSettings.js`. Maintenance prunes old data.
- **Shared routes.** For one class, the admin page uses the teacher routes (labs, students, analytics, rename or lock a lab, delete a class or a lab). They let the class owner and any admin through (`routers/teacher_access.py`); every other teacher route stays for the owner only. Until November 2026 the admin had its own copy of each of these routes.

The code of the three pages is split by tab: `pages/Teacher.jsx`, `pages/Admin.jsx` and `pages/Chat.jsx` hold the page frame and its state, and the panels are in `pages/teacher/`, `pages/admin/` and `pages/chat/`. On the server, the teacher routes are split the same way: `routers/teacher.py` (classes, labs, exam mode, analytics, test drive), `teacher_documents.py`, `teacher_rules.py`, `teacher_students.py` and `teacher_reports.py`, all under `/api/teacher`.

## Monitoring

This section says how Grafana is reached. The admin Monitoring tab shows Grafana dashboards inside the app. Only admins get through. The tab asks the backend for a signed cookie (`POST /api/admin/monitoring/session`). For every `/grafana/` request, nginx calls `GET /api/admin/monitoring/check` (`auth_request`), and the backend lets it pass only if the cookie belongs to a user who is an admin now (`backend/app/routers/monitoring.py`, `frontend/nginx.conf`). The dashboards and the containers are in [MONITORING.md](MONITORING.md).

## Deployments

This section says which stacks exist and how they differ.

- **Dev stack.** `docker-compose.yml` with `docker-compose.override.yml`. The override turns the feature flags on, sets the MinerU settings, serves the site on port 8080 (local and Tailscale addresses) and adds the monitoring containers. See "Run it" in the [README](../README.md).
- **Teachers' stack.** `docker-compose.release.yml`, with its own database, volumes and backup, served over HTTPS. See [RELEASE_TEACHERS.md](RELEASE_TEACHERS.md).

Both stacks use the same code and the same model servers on `edullm-models`.
