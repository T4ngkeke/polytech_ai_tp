# Edu-LLM: Class-Lab Tutor

Edu-LLM is a web application for a controlled classroom. Teachers upload course PDFs. Students ask questions in a chat, and an AI model answers from those PDFs and cites the pages it used.

This file says what the project is, what it does today and how to run it. The details are in the files listed under [Documentation](#documentation).

## What it does

- **Three roles.** An admin manages users, classes, models and monitoring. A teacher owns classes and labs. A student joins a class with an invite code and chats inside a lab. Each role has its own workspace; a user who opens another role's page is sent back to their own.
- **Class and lab.** A class has an invite code (6 characters). A lab is one activity inside a class (for example one practical session). A chat session always belongs to one lab.
- **PDF only.** A teacher uploads a PDF to a lab, or to the whole class. A worker reads each page with MinerU (a PDF reading tool), splits the text into passages and indexes them. An uploaded file then waits as a draft: the teacher checks it and publishes it, and only then students and the agent use it (since 2026-11-26). A teacher's edit or a new reading is published at once when every page was read. The teacher can edit blocks, exclude blocks and reread a page.
- **One way to answer.** The research agent decides by itself whether to search the course: a greeting needs no search, a course question does. It searches and reads several times, and may ask the student a question back. An answer cites the pages it used, and the chat shows each page with the passage highlighted. The student can turn on deep research (more time and more tool calls), web search, Python and file uploads, when the teacher allows them. (Until 2026-11-26 there were three modes, Direct, Search and Research.)
- **Exam mode.** In a class in exam mode, the agent looks only in the files the teacher allowed for the exam. With no allowed file, it has no tools at all and answers from general knowledge, without citations. Web search, Python, uploads and deep research are off. The teacher turns it on for an exam. New classes start without it (setting `NEW_CLASS_EXAM_MODE`, off).
- **Teacher rules.** A teacher writes rules at three levels: class, lab and student. They are added to the instructions of the model for every answer. The model can point out two rules of one list that contradict each other.
- **The TP files first.** The files of a lab (the TP statement) come first in the list of course files the agent sees. For a question about the TP itself ("what do we do?", "question 3?") it reads them first.
- **Answer language.** A small model call names the language of each answer, from the teacher's rules and the student's last messages. The answer is written in that language even when the course pages are in French.
- **Conversation history.** Each answer sees the earlier messages of the session, within a share of the model's context. Past the limit the oldest turns go, several at once, and the chat page draws a line where the history the model sees begins. A question may have at most 20,000 characters.
- **Quota.** Each user has a daily token quota. A teacher can give a student a full quota again for the day.
- **Teacher tools.** Analytics for a class or a lab; a report on which course files and pages the answers cite; AI-written reports on a lab or on one student; a test drive that shows answers as students see them; chat history, with the sessions that students deleted.
- **Admin tools.** Users (also CSV import and password reset), classes with their teacher and activity, every course document, model settings (servers with one shared key and a test button, what each feature uses, answer limits, billing) with the state of each model server, a Monitoring tab with Grafana, and data pruning.

## How it is built

- **Stack.** FastAPI, PostgreSQL with `pgvector`, React and Docker Compose. There is no message queue: the `ingestion_jobs` table is the queue.
- **Search.** Vector search (`pgvector`) and full-text search (`tsvector`) run on one table. The results are merged and then reranked by a model.
- **Models.** Every model is reached through an OpenAI-compatible HTTP endpoint. The addresses and names are stored in the database (`system_configs`) and the admin changes them at run time. The current deployment runs one model for the whole system (Qwen3.8 Flash-Next, served under the name `flash-next`) for answers, vision and figure descriptions, with `Qwen3-Reranker` as reranker and `bge-m3` for embeddings (both on vLLM). The image has no torch. The model servers refuse any request without their key, which the admin sets in the same page (see [MODEL_SERVERS.md](documentation/MODEL_SERVERS.md#the-key)).
- **Worker.** A second container runs `backend/worker`. It reads PDFs while students are not chatting, and chat always goes first. Between jobs it also removes the files of deleted documents.
- **One client per model endpoint.** The backend and the worker share one HTTP client per model endpoint (`services/shared_clients.py`). Failed model calls are written to the `service_failures` table, which the monitoring shows.
- **Access rules in SQL.** Every course search filters on `class_id` and `lab_id` in the SQL query, not in the prompt. A prompt cannot widen it.
- **Pedagogy in the prompt.** The teacher rules shape how the tutor answers. They are not a security boundary.
- **Soft delete.** When a student deletes a session, it is hidden from the student but kept, and teachers and admins still see it. Only an admin can delete it for good.
- **Login token.** The token holds only the user id. The role and the quota are read from the database on every request.

### Known gaps

- The vector index (HNSW) and the full-text index (GIN) are not created by any code. Both searches read the whole table. Results are correct, but they will slow down as the material grows. See [DATABASE.md](documentation/DATABASE.md).
- There is no migration tool for ordinary tables. The tables are created with `create_all`. Changes to existing tables are SQL files applied by `scripts/migrate_document_snapshots.py`.
- `backend/requirements.txt` still lists some packages that no code imports any more.

## Documentation

| File | What it covers |
| --- | --- |
| [documentation/API.md](documentation/API.md) | Every HTTP endpoint, who may call it, and the chat stream events. |
| [documentation/DATABASE.md](documentation/DATABASE.md) | Every table and column, and the settings stored in the database. |
| [documentation/ARCHITECTURE.md](documentation/ARCHITECTURE.md) | How a chat request is answered, how a PDF is ingested, the teacher and admin pages. |
| [documentation/PDF_INGESTION.md](documentation/PDF_INGESTION.md) | The MinerU PDF pipeline: setup, migrations, limits. |
| [documentation/WORKER.md](documentation/WORKER.md) | The files of the PDF worker and what each one does. |
| [documentation/MODEL_SERVERS.md](documentation/MODEL_SERVERS.md) | How the main model server is started and replaced, and the key that every model server checks. |
| [documentation/LOCAL_SETUP.md](documentation/LOCAL_SETUP.md) | The dev stack step by step, the models to set and how to check that it works. |
| [documentation/RELEASE_TEACHERS.md](documentation/RELEASE_TEACHERS.md) | The installation that the teachers use (`docker-compose.release.yml`). |
| [documentation/MONITORING.md](documentation/MONITORING.md) | Grafana, Prometheus and the admin-only access check. |
| [documentation/M4_AGENT_EVALUATION.md](documentation/M4_AGENT_EVALUATION.md) | The benchmark of the research agent (and of the old answer modes), its results and how to run it. |
| [backend/app/agent/README.md](backend/app/agent/README.md) | The files of the chat agent. |
| [frontend/README.md](frontend/README.md) | The files of the web app, and its checks. |

## Directory tree

```text
polytech_ai_tp/
├── backend/
│   ├── app/
│   │   ├── main.py            # FastAPI entry point, startup checks, router mounting
│   │   ├── config.py          # settings read from the environment
│   │   ├── database.py        # SQLAlchemy engine and session
│   │   ├── models.py          # tables (see documentation/DATABASE.md)
│   │   ├── schemas.py         # request and response shapes
│   │   ├── schema_versions.py # migrations that the backend requires at startup
│   │   ├── auth.py            # token creation, get_current_user, require_* checks
│   │   ├── routers/           # auth, admin, monitoring, student, chat, document_versions,
│   │   │                      # document_reader, and the teacher area: teacher (classes, labs,
│   │   │                      # exam, analytics, test drive), teacher_documents, teacher_rules,
│   │   │                      # teacher_students, teacher_reports, teacher_access (shared checks)
│   │   ├── services/          # business logic: classes, labs, rules, quota, analytics,
│   │   │                      # reports, document reading and editing, research runs, model clients
│   │   └── agent/             # the research agent: runner.py (the loop); tools/ (course,
│   │                          # web, Python, tasks, access checks); context/ (history,
│   │                          # summary, memory, attachments, answer language)
│   ├── worker/                # PDF ingestion worker: queue, MinerU, indexing, load gate
│   ├── tests/                 # pytest suite
│   ├── seed.py                # first-time data: settings, demo accounts, one class
│   ├── requirements.txt
│   └── Dockerfile             # shared by backend and worker
├── frontend/
│   ├── src/
│   │   ├── pages/             # Login, Register, Account, ChoosePassword, Chat, Teacher,
│   │   │                      # Admin, SourcePdf; the parts of Chat, Teacher and Admin
│   │   │                      # are in pages/chat/, pages/teacher/ and pages/admin/
│   │   ├── components/        # sidebar, document manager, analytics, reports, PDF preview...
│   │   ├── lib/               # API client, stream events, citations, settings (with *.test.js)
│   │   └── store/             # Zustand stores
│   ├── nginx.conf             # nginx for the dev stack
│   ├── nginx.release.conf     # nginx for the release stack (HTTPS)
│   └── Dockerfile
├── model_servers/             # the key check that the vLLM model servers load
├── python_runner/             # the agent's Python on the server: Pyodide, one process per run
├── monitoring/                # Grafana dashboards, Prometheus config, read-only database grants
├── scripts/                   # SQL migrations, migrate_document_snapshots.py, benchmark and check scripts
├── documentation/             # the files listed above, plus plans and test reports
├── docker-compose.yml         # PostgreSQL, backend, worker, frontend
├── docker-compose.override.yml # the dev stack: ports, MinerU, web search, monitoring
└── docker-compose.release.yml # the teachers' stack
```

## Run it

### With Docker

You need Docker with Compose, and a model server that speaks the OpenAI API. The containers reach the host as `host.docker.internal`. The model names and addresses are set later in the admin page, not in the image.

```bash
docker compose build
docker compose up -d postgres
```

On a new database, create the tables and the demo data once. Then apply the SQL migrations from the host. On PostgreSQL the backend refuses to start until they are applied (see [PDF_INGESTION.md](documentation/PDF_INGESTION.md)).

```bash
docker compose run --rm --no-deps backend python -m backend.seed
python -m scripts.migrate_document_snapshots --database-url "$DB_URL"           # dry run
python -m scripts.migrate_document_snapshots --database-url "$DB_URL" --apply
docker compose up -d
```

`$DB_URL` looks like `postgresql+asyncpg://postgres:postgres@127.0.0.1:5432/edudb`.

`docker compose` also reads `docker-compose.override.yml`. It sets the MinerU settings, serves the site on port 8080 (local and Tailscale addresses only) and adds the monitoring containers. Those need `GRAFANA_ADMIN_PASSWORD` and `GRAFANA_DB_PASSWORD` in an untracked `.env` file (see [MONITORING.md](documentation/MONITORING.md)). Without the override file, the site is on port 80.

Demo accounts that `seed.py` creates (change them outside a local test):

| User | Password | Role |
| --- | --- | --- |
| `admin` | `admin123` | admin |
| `teacher` | `teacher123` | teacher |
| `student1`, `student2` | `student123` | student |

The default class has the invite code `ABC123`. Set the models in the admin page after the start.

The teachers do not use this stack. They use `docker-compose.release.yml` over HTTPS, described in [RELEASE_TEACHERS.md](documentation/RELEASE_TEACHERS.md).

### Without Docker (development)

1. Start PostgreSQL with `pgvector`: `docker compose up -d postgres`.
2. Backend, from the project root: `pip install -r backend/requirements.txt`, then `python -m backend.seed`, then `uvicorn backend.app.main:app --reload --port 8000`.
3. Worker, in another terminal: `python -m backend.worker.main`.
4. Frontend: `cd frontend && npm install && npm run dev`. The site is at `http://localhost:5173`.

### Settings in the environment

`backend/app/config.py` reads these from the environment or from `backend/.env`.

| Variable | Default | Meaning |
| --- | --- | --- |
| `DATABASE_URL` | local PostgreSQL | Database address (`postgresql+asyncpg://...`). |
| `JWT_SECRET` | `change-me-in-production` | Key that signs the login tokens. Change it. |
| `JWT_ALGORITHM`, `ACCESS_TOKEN_EXPIRE_MINUTES` | `HS256`, `480` | Token settings. |
| `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL` | a local vLLM address (`http://localhost:8001/v1`) | Used only while `system_configs` has no value for them. |
| `DOCUMENTS_STORAGE_ROOT` | `/data/documents` | Where uploaded files are stored. Backend and worker share this volume. |
| `PYTHON_RUNNER_URL` | none | The Python runner of the agent's `execute_code` (`python_runner/`, the `python-runner` service: `http://edullm-python-runner:8000`). Empty: the model's code runs in the student's browser. |
| `NEW_CLASS_EXAM_MODE` | `false` | New classes start in exam mode. Off since 2026-11-26: uploaded files wait for the teacher to publish them. |
| `STRUCTURED_VISION_ENABLED` | `false` | Allows "Write with AI" descriptions of pictures. Turn it on only after the vision model was checked to read images. |

The worker also reads these:

| Variable | Default | Meaning |
| --- | --- | --- |
| `PDF_MINERU_PYTHON` | none | Python of the MinerU environment (`/opt/mineru/bin/python` in the worker image built with `WITH_MINERU=1`). |
| `PDF_MINERU_SERVER_URL` | none | MinerU model server. The admin can switch it in the model settings. |
| `PDF_MINERU_API_KEY` | none | Key of the MinerU server. Used only when the model settings have no key for MinerU and no main model key. |
| `PDF_MINERU_CONFIG` | none | Absolute path of `mineru.json`. |
| `PDF_MINERU_PAGE_TIMEOUT_SECONDS` | `240` | Time allowed for one page. |
| `PDF_READ_BUDGET_SECONDS` | `7200` | Time allowed for reading one PDF. |
| `STRUCTURED_LEASE_SECONDS`, `STRUCTURED_MAX_ATTEMPTS` | `1800`, `3` | How long a worker holds a job, and how often a job is tried. |
| `STRUCTURED_TASK_ROOT` | system temp folder | Working folder of the worker. |

The model addresses, model names, search limits and token weights are not environment variables. They are rows in `system_configs`, listed in [DATABASE.md](documentation/DATABASE.md).

### Data volumes

- `postgres_data`: the PostgreSQL data.
- `documents_data`: the uploaded PDFs and the files made from them. `DOCUMENTS_STORAGE_ROOT` points into it.

### Tests

```bash
python -m pytest backend/tests -q
```

Tests that need PostgreSQL with `pgvector` skip themselves unless `TEST_PG_URL` is set. The database name must end with `_test`, because the tests drop and create tables in it. All model calls in the tests are replaced by fakes, so no model server is needed.

The web app has its own tests and checks, listed in [frontend/README.md](frontend/README.md):

```bash
cd frontend && node --test src/lib/*.test.js && npm run lint && npm run build
```
