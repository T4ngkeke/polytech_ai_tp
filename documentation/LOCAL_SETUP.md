> Part of the Edu-LLM documentation. Back to [README](../README.md).

# Local Docker Start and Checks

This file explains how to start the development environment with Docker on your machine, which models you must set up, and how to check that it works. The environment that teachers use is a separate one. See [RELEASE_TEACHERS.md](RELEASE_TEACHERS.md).

The development environment is made of `docker-compose.yml` and `docker-compose.override.yml`. `docker compose` reads both files automatically. They contain the database, the backend, the worker and the frontend. The override file also turns on the feature switches, sets up MinerU (the tool that reads PDFs) and adds the monitoring containers. The model addresses and names are stored in the database. You change them in the admin page, and you do not need to rebuild the images after a change.

## First start

First, create a `.env` file in the project root (git does not track it). Write the two passwords that monitoring needs. See [MONITORING.md](MONITORING.md) for details.

```text
GRAFANA_ADMIN_PASSWORD=<random string>
GRAFANA_DB_PASSWORD=<random string of 16 characters or more>
```

Then build the images and start the database first:

```bash
docker compose build
docker compose up -d postgres
docker compose exec postgres pg_isready -U postgres -d edudb
```

When the database accepts connections, run the initialization once on a **brand new database**. It creates the tables and the demo data:

```bash
docker compose run --rm --no-deps backend python -m backend.seed
```

Then run the SQL migration. Without the migration, a backend with the feature switch on refuses to start. Do a dry run first, then apply it (see [README](../README.md) for how to write `DB_URL`):

```bash
python -m scripts.migrate_document_snapshots --database-url "$DB_URL"
python -m scripts.migrate_document_snapshots --database-url "$DB_URL" --apply
```

Last, start all the services:

```bash
docker compose up -d
```

`seed.py` inserts fixed user names and settings, so you must not run it at every start. For later starts, `docker compose up -d` is enough.

## Ports

The ports of the development environment come from the override file. They are open only on the local machine and on the Tailscale address:

- Website: `http://localhost:8080`
- Backend API documentation: `http://localhost:8000/docs`

The teacher environment already uses ports 80 and 443 on the server. So on the server, do not start the frontend with `docker-compose.yml` alone, because it maps to port 80. Either add the override file (the frontend is then on 8080), or write your own compose override file that maps the frontend to another local port, for example `127.0.0.1:8088`.

## Demo accounts

| User name | Initial password | Role |
| --- | --- | --- |
| admin | admin123 | Administrator |
| teacher | teacher123 | Teacher |
| student1 | student123 | Student |
| student2 | student123 | Student |

The invitation code of the default class is `ABC123`. These are local demo accounts. Do not use them outside your machine.

## Models

The system uses the same model for chat, for looking at images and for image captions. It is Qwen3.8 Flash-Next, and the service name is `flash-next`. Retrieval uses `Qwen/Qwen3-Reranker-0.6B` (reranking) and `BAAI/bge-m3` (embeddings). All these models run in separate containers. They are connected to the Docker network `edullm-models`. PDF reading uses the MinerU server `edullm-mineru-vlm`. See [PDF_INGESTION.md](PDF_INGESTION.md) for how to start it. See [MODEL_SERVERS.md](MODEL_SERVERS.md) for how to start the main model server.

Every model server checks one shared key; how it is made and loaded is in [MODEL_SERVERS.md](MODEL_SERVERS.md#the-key).

After the first start, open the LLM Config page with the administrator account. Fill in the address and the name of each model, and the key in the API Key field of the main model (the other nodes use it when their own field is empty). Use the container name for the model address, for example `http://m4-flashnext-v030:8000/v1`. The page also shows the status of each model server. See [DATABASE.md](DATABASE.md) for the meaning of each setting.

The backend and the worker of the development environment must be able to reach this network. So create it first, then connect the model containers to it:

```bash
docker network create edullm-models
docker network connect edullm-models <model container name>
```

### Embedding service

Embedding turns the text of a textbook into a series of numbers. Search uses these numbers to find passages with a similar meaning. It does not write answers for students.

The embedding service runs bge-m3 with vLLM:

```bash
docker run -d --name edullm-embeddings --gpus all --ipc=host --restart unless-stopped \
  --network edullm-models -e HF_HOME=/hf -e HOME=/tmp --user "$(id -u):$(id -g)" \
  -v $HOME/.cache/hf-retrieval-eval:/hf \
  -v $HOME/.local/share/edullm-models/edullm_key_check.py:/usr/local/lib/python3.12/dist-packages/edullm_key_check.py:ro \
  --env-file $HOME/.local/share/edullm-models/key.env \
  nvcr.io/nvidia/vllm:26.07-py3 \
  vllm serve BAAI/bge-m3 --host 0.0.0.0 --port 8000 \
  --runner pooling --gpu-memory-utilization 0.02 --max-model-len 8192 --max-num-seqs 32 \
  --middleware edullm_key_check.KeyCheck
```

Fill in this in the admin page: `EMBEDDING_URL = http://edullm-embeddings:8000/v1`, `EMBEDDING_MODEL = BAAI/bge-m3`. After you change the embedding service, use `scripts/reembed_documents.py` to compute the embeddings of all documents again. The embeddings must match the model that search uses.

`docker-compose.yml` also has an optional Ollama embedding container (profile `local-embeddings`). The current environment does not use it.

## Checking the running state

```bash
docker compose ps
docker compose logs --tail=50 backend worker
curl http://localhost:8080/api/health
```

A working home page and health check only show that the website is available. To check search, do the following:

1. With the teacher account, upload a small PDF in a lab course.
2. Wait until the document is published. The document is published automatically after every page has been read.
3. With a student account, ask a question that can only be answered by reading this PDF. Choose Search or Research mode.
4. Check that the answer cites the right pages.

A new class is in exam mode by default, which has only Direct mode. To try Search and Research, first turn off exam mode in the class settings.

## Tests

The tests use a separate PostgreSQL test database. The database name must end with `_test`, because the tests drop and rebuild the tables in it. Tests that need PostgreSQL are skipped automatically when `TEST_PG_URL` is not set.

```bash
TEST_PG_URL=postgresql+asyncpg://postgres:postgres@127.0.0.1:5432/edudb_worker_test \
  .venv/bin/python -m pytest backend/tests -q
```

In the tests, all model calls are replaced with fake ones, so no model server is needed.

## Browser check script

`scripts/check_layout_ui.py` uses Playwright and Chromium. It checks the layout of the student, teacher and admin pages at phone and desktop widths, in light and dark themes. It also checks that `/grafana/` is closed when there is no cookie. It intercepts all API calls, does not call any model and does not write to the database. The default address is `http://127.0.0.1:8080`, and you can change it with `UI_TEST_URL`. The old `check_admin_config.py` and `check_teacher_test_drive.py` were deleted in November 2026 (the pages they checked have changed).

For the frontend's own unit tests, lint and build, see [frontend/README.md](../frontend/README.md).
