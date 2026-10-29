# Edu-LLM: Class-Lab Tutor

Edu-LLM is a web application for a controlled classroom. Teachers upload course PDFs. Students ask questions in a chat, and an AI model answers from those PDFs and cites the pages it used.

This file says what the project is, what it does today and how to run it. The details are in the files listed under [Documentation](#documentation).

## What it does

- **Three roles.** An admin manages users, classes, models and monitoring. A teacher owns classes and labs. A student joins a class with an invite code and chats inside a lab. Each role has its own workspace; a user who opens another role's page is sent back to their own.
- **Class and lab.** A class has an invite code (6 characters). A lab is one activity inside a class (for example one practical session). A chat session always belongs to one lab.
- **PDF only.** A teacher uploads a PDF to a lab, or to the whole class. A worker reads each page with MinerU (a PDF reading tool), splits the text into passages and indexes them. The new version is published at once when every page was read. Otherwise it stays a draft that the teacher checks and publishes. The teacher can edit blocks, exclude blocks and reread a page.
- **Three answer modes.** In *Direct* mode the model answers without the course files. In *Search* mode the system makes one search and the model answers from the pages found. In *Research* mode the model searches and reads several times, and may ask the student a question back. Search and Research answers cite the pages they used, and the chat shows each page with the passage highlighted.
- **Exam mode.** A class in exam mode answers in Direct mode only, whatever the student chooses. New classes start in exam mode (setting `NEW_CLASS_EXAM_MODE`); the teacher turns it off.
- **Teacher rules.** A teacher writes rules at three levels: class, lab and student. They are added to the instructions of the model in every answer mode.
- **Quota.** Each user has a daily token quota. A teacher can give a student a full quota again for the day.
- **Teacher tools.** Analytics for a class or a lab; a report on which course files and pages the answers cite; AI-written reports on a lab or on one student; a test drive that shows answers as students see them; chat history, with the sessions that students deleted.
- **Admin tools.** Users (also CSV import), classes with their teacher and activity, a model map with the state of each model server, a Monitoring tab with Grafana, and data pruning.

## How it is built

- **Stack.** FastAPI, PostgreSQL with `pgvector`, React and Docker Compose. There is no message queue: the `ingestion_jobs` table is the queue.
- **Search.** Vector search (`pgvector`) and full-text search (`tsvector`) run on one table. The results are merged and then reranked by a model.
- **Models.** Every model is reached through an OpenAI-compatible HTTP endpoint. The addresses and names are stored in the database (`system_configs`) and the admin changes them at run time. The current deployment runs one model for the whole system (Qwen3.8 Flash-Next, served under the name `flash-next`) for answers, vision and figure descriptions, with `Qwen3-Reranker` as reranker and `bge-m3` for embeddings (both on vLLM). The image has no torch.
- **Worker.** A second container runs `backend/worker`. It reads PDFs while students are not chatting, and chat always goes first.
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
| [documentation/ARCHITECTURE.md](documentation/ARCHITECTURE.md) | How a chat request is answered, how a PDF is ingested, the analytics shape. |
| [documentation/PDF_INGESTION.md](documentation/PDF_INGESTION.md) | The MinerU PDF pipeline: setup, migrations, limits. |
| [documentation/RELEASE_TEACHERS.md](documentation/RELEASE_TEACHERS.md) | The installation that the teachers use (`docker-compose.release.yml`). |
| [documentation/MONITORING.md](documentation/MONITORING.md) | Grafana, Prometheus and the admin-only access check. |

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
│   │   ├── routers/           # auth, admin, monitoring, teacher, student, chat,
│   │   │                      # document_versions, document_reader
│   │   ├── services/          # business logic: classes, labs, rules, quota, analytics,
│   │   │                      # reports, document reading and editing, research runs, model clients
│   │   └── agent/             # the three answer modes: runner.py (Research),
│   │                          # search.py (Search), direct.py (Direct), and their tools
│   ├── worker/                # PDF ingestion worker: queue, MinerU, indexing, load gate
│   ├── tests/                 # pytest suite
│   ├── seed.py                # first-time data: settings, demo accounts, one class
│   ├── requirements.txt
│   └── Dockerfile             # shared by backend and worker
├── frontend/
│   ├── src/
│   │   ├── pages/             # Login, Register, Account, Chat, Teacher, Admin, SourcePdf
│   │   ├── components/        # sidebar, document manager, analytics, reports, PDF preview...
│   │   ├── lib/               # API client, stream events, answer modes, citations
│   │   └── store/             # Zustand stores
│   ├── nginx.conf             # nginx for the dev stack
│   ├── nginx.release.conf     # nginx for the release stack (HTTPS)
│   └── Dockerfile
├── monitoring/                # Grafana dashboards, Prometheus config, read-only database grants
├── scripts/                   # SQL migrations, migrate_document_snapshots.py, benchmark and check scripts
├── documentation/             # the files listed above, plus plans and test reports
├── docker-compose.yml         # PostgreSQL, backend, worker, frontend
├── docker-compose.override.yml # the dev stack: ports, feature flags, MinerU, monitoring
└── docker-compose.release.yml # the teachers' stack
```

## Run it

### With Docker

You need Docker with Compose, and a model server that speaks the OpenAI API. The containers reach the host as `host.docker.internal`. The model names and addresses are set later in the admin page, not in the image.

```bash
docker compose build
docker compose up -d postgres
```

On a new database, create the tables and the demo data once. Then apply the SQL migrations from the host. The backend refuses to start with the feature flags on until they are applied (see [PDF_INGESTION.md](documentation/PDF_INGESTION.md)).

```bash
docker compose run --rm --no-deps backend python -m backend.seed
python -m scripts.migrate_document_snapshots --database-url "$DB_URL"           # dry run
python -m scripts.migrate_document_snapshots --database-url "$DB_URL" --apply
docker compose up -d
```

`$DB_URL` looks like `postgresql+asyncpg://postgres:postgres@127.0.0.1:5432/edudb`.

`docker compose` also reads `docker-compose.override.yml`. It turns the feature flags on, sets the MinerU settings, serves the site on port 8080 (local and Tailscale addresses only) and adds the monitoring containers. Those need `GRAFANA_ADMIN_PASSWORD` and `GRAFANA_DB_PASSWORD` in an untracked `.env` file (see [MONITORING.md](documentation/MONITORING.md)). Without the override file, the site is on port 80 and the flags are off.

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
| `STRUCTURED_INGEST_ENABLED`, `PDF_STRUCTURED_INGEST_ENABLED` | `false` | Both must be `true` to accept PDF uploads (otherwise `409 structured_ingest_disabled`). |
| `AGENTIC_SEARCH_ENABLED` | `false` | Must be `true` for the chat to answer (otherwise `503 chat_not_enabled`). |
| `NEW_CLASS_EXAM_MODE` | `true` | New classes start in exam mode. |
| `STRUCTURED_VISION_ENABLED` | `false` | Allows "Write with AI" descriptions of pictures. Turn it on only after the vision model was checked to read images. |

The worker also reads these:

| Variable | Default | Meaning |
| --- | --- | --- |
| `PDF_MINERU_PYTHON` | none | Python of the MinerU environment (`/opt/mineru/bin/python` in the worker image built with `WITH_MINERU=1`). |
| `PDF_MINERU_SERVER_URL` | none | MinerU model server. The admin can switch it in the model map. |
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
