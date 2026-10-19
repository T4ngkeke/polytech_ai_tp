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

- **One model for the whole system.** Qwen3.8 Flash-Next, served under the name `ornith`, writes the answers, looks at pictures (the `view_image` tool) and writes figure descriptions ("Write with AI"). Slots for the agent and for vision exist in the admin page. An empty agent slot uses the main model. The vision slot has no fallback: empty means that vision is off.
- **Reranker.** `Qwen/Qwen3-Reranker-0.6B`, served by vLLM (container `edullm-reranker`). A reranker reads the question and each candidate passage and gives each one a score.
- **Embeddings.** `BAAI/bge-m3`, served by vLLM (container `edullm-embeddings`). An embedding is a list of numbers that stands for the meaning of a text. Both the question and the passages are turned into embeddings for the vector search.
- **PDF reading.** MinerU is a PDF reading tool. It has a model server (`edullm-mineru-vlm`) and a client in the worker image. See [PDF_INGESTION.md](PDF_INGESTION.md).

The addresses and names are rows in `system_configs`. The admin changes them in the LLM Config tab without a restart. The backend reads them for each request. The worker reads them at each loop and rebuilds its clients when they changed. `backend/app/services/model_routing.py` turns the rows into endpoints. `GET /api/admin/llm/servers` shows what each server serves.

## Answering a chat question

This section follows one request to `POST /api/chat/stream` (`backend/app/routers/chat.py`). There is one chat path. It is the research agent in `backend/app/agent/`.

1. **Checks.** The session must belong to the user. The user must be a member of the class that owns the lab. The one exception is the owning teacher's test-drive session. The lab must be open. The daily quota must not be used up (`429` otherwise). Without `AGENTIC_SEARCH_ENABLED` the endpoint answers `503 chat_not_enabled`. A session without a lab gets `400 session_without_lab`.
2. **Teacher rules.** The active class, lab and student rules go into the system prompt of every mode, as `[CLASS RULES]`, `[LAB RULES]` and `[STUDENT RULES]`.
3. **Mode.** The client asks for `direct`, `search` or `research`. A class in exam mode always gets `direct`.
4. **Scope.** The course files of a run are the published PDF revisions of the lab and of the class. They are fixed when the run starts. Every course search filters `class_id` and `lab_id` in SQL. The model never sends a file address or a revision.
5. **Saving and billing** (`services/research_service.py`). Before each model call, the tokens are reserved on the student's quota. After the call, the reserved amount is replaced by the usage the server reported. The answer, its citations and its mode are saved before any of it is sent. The same `client_request_id` returns the saved run and makes no second call.
6. **Streaming.** The answer goes out as server-sent events: `status`, `draft`, the checked answer, `citations`, `done`, and sometimes `clarification` or `title`. A keep-alive comment goes out every 15 seconds. The events are listed in [API.md](API.md).

The first answer of a conversation also gives the conversation its title (`services/session_titles.py`). The same model writes it, and it is not charged to the quota.

### The three answer modes

| Mode | Code | What happens |
| --- | --- | --- |
| Direct | `agent/direct.py` | One short answer from general knowledge. The model does not see the course files and cites nothing. Citation IDs copied from earlier answers are removed. |
| Search | `agent/search.py` | The system searches once with the question as written. It reads the best pages in full and the page before and after each one (up to 16 pages and 24,000 characters). One model call then answers from those pages and cites them. If the best reranker score is below a bound, the answer says that the course has no page for the question. The bound is 0.5 for Qwen3-Reranker and 0.001 for other rerankers. |
| Research | `agent/runner.py` | The model works in one conversation and calls the course tools as often as it needs, within limits. A reply without a tool call is the answer. It can end the run with a question to the student (`ask_student`). |

Search and Direct answers are short unless the teacher's rules ask for more detail.

### The course tools of Research mode

`agent/course_tools.py` defines the tools. `agent/tools.py` checks the scope and the access before each call.

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

The limits of one Research run are settings of the admin page: tool calls, picture views, research time, total time, time per model call and input tokens per call. The defaults are in [API.md](API.md). When a limit is reached, the tools are withdrawn and the model answers from what it has read. If there is no usable answer, the system lists the verified pages that were read, without a model call. The history of the conversation may use a quarter of the input tokens of a call.

## Search

This section describes the search behind the `search` tool and Search mode (`services/retrieval_service.py`).

- **Two searches on one table.** A vector search compares the question embedding with the passage embeddings (`pgvector`). A full-text search uses a `tsvector` in the language of the document, French or English. Each search takes the best 20.
- **Merge, then rerank.** The two lists are merged by reciprocal rank fusion (a method that rewards a passage that ranks high in both lists). The reranker then orders the merged list. Without a reranker address, the merged order is kept.
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
- **Deleting.** Deleting a document is quick for the teacher. The stored files and chunks are removed in the background.

## Ingestion

This section describes the worker. It is a loop that takes one job at a time and never preempts live students. The setup is in [PDF_INGESTION.md](PDF_INGESTION.md). The old worker (text extraction, chunk context written by a model, formula repair, page review) was removed.

- **Queue.** The worker takes a job with `FOR UPDATE SKIP LOCKED`, so two workers never take the same job. A claim has a lock token and a lease (30 minutes by default). A job whose lease ran out is claimed again, up to 3 attempts. A result from a lost claim, from an old generation or for a deleted document writes nothing.
- **Two jobs per upload.** The `pdf_parse` job has MinerU read each page. It stores the pages, blocks and assets as a draft. The same job then becomes a `pdf_index` job. That job cuts the blocks into passages, embeds them with bge-m3, builds the full-text vector and publishes the revision when every page was read. The language (French or English) is found from the common words of the text.
- **When to run.** The worker waits while students chat. It counts the messages of the last two minutes, with two bounds so that it does not switch on and off at one value. An optional check of GPU load (`pynvml`) does nothing on a machine without a local GPU.
- **Progress.** While a PDF waits or is read, the teacher sees the jobs ahead of it, the pages read and the time left.
- **Settings without restart.** The embedding address and the MinerU server come from `system_configs`.

## Quota and billing

This section says how tokens are counted. Each user has a daily quota (`users.daily_token_quota`). A teacher can give a student a full quota again for the day (`quota_grants`). A model call is billed as `prompt tokens × TOKEN_ALPHA + completion tokens × TOKEN_BETA`. The two weights are settings (defaults 0.2 and 1.0). Calls are reserved before they run (`usage_reservations`) and recorded after (`model_call_usage`). Embedding and rerank calls are counted too. Code: `services/research_usage.py`, `services/quota_service.py`.

## Teacher and admin tools

This section lists the tools in the web pages and where their data comes from.

- **Teacher page.** A teacher works on a class or on a lab. A class has the tabs Settings, Rules, Analytics and Documents. A lab has Settings, Rules, Analytics, Audit, Students, Documents and Test drive.
  - *Analytics* (`services/analytics_service.py`) shows counts, answers by mode, questions by day and hour, each student, the questions with a thumbs-down and the answers that failed. Teacher test sessions are left out.
  - *Audit* has three views: the conversations by student, the course use report (`services/citation_report.py`: which files, pages and passages the answers cite, and which files nobody cited) and AI reports (`services/teaching_reports.py`). An AI report is written in the background by the model, on request, from numbers that the system counted first. It is stored in `audit_reports`.
  - *Test drive* is a chat session of the teacher in a lab, shown as a student sees it.
- **Admin page.** The tabs are Users, Classes, Monitoring, LLM Config and Maintenance. Classes shows every class with its teacher, its counts and its activity, and the platform totals. LLM Config shows the model map, the research limits and the state of each model server. Maintenance prunes old data.

The shape of the token analytics of a class (`GET /api/teacher/analytics/classes/{class_id}`) is class, then lab, then student:

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
        { "user_id": "uuid", "username": "alice", "tokens_used": 3500, "request_count": 120 }
      ]
    }
  ]
}
```

## Monitoring

This section says how Grafana is reached. The admin Monitoring tab shows Grafana dashboards inside the app. Only admins get through. The tab asks the backend for a signed cookie (`POST /api/admin/monitoring/session`). For every `/grafana/` request, nginx calls `GET /api/admin/monitoring/check` (`auth_request`), and the backend lets it pass only if the cookie belongs to a user who is an admin now (`backend/app/routers/monitoring.py`, `frontend/nginx.conf`). The dashboards and the containers are in [MONITORING.md](MONITORING.md).

## Deployments

This section says which stacks exist and how they differ.

- **Dev stack.** `docker-compose.yml` with `docker-compose.override.yml`. The override turns the feature flags on, sets the MinerU settings, serves the site on port 8080 (local and Tailscale addresses) and adds the monitoring containers. See "Run it" in the [README](../README.md).
- **Teachers' stack.** `docker-compose.release.yml`, with its own database, volumes and backup, served over HTTPS. See [RELEASE_TEACHERS.md](RELEASE_TEACHERS.md).

Both stacks use the same code and the same model servers on `edullm-models`.
