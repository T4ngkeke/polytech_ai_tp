# Release to the teachers (replacing v6-beta)

**Done on 2026-10-16** (see "The switch of 2026-10-16" below). The teachers'
address is unchanged: `https://192.168.168.199:18443/` (also on Tailscale,
`https://100.108.32.29:18443/`), same certificate and CA.

The teachers who used v6-beta moved to the current version on 2026-10-16.
Classes start in exam mode: answers are written directly, without the course
files, as in v6.
Teachers can then try the rest (course PDFs, Search and Research, Test drive,
Analytics, AI reports) and report what they find.

Everyone logs in again once (new JWT secret); passwords are unchanged. The
former Tailscale address on port 80 (v6-stable) is closed: use port 18443.

## The former problem in the v6 deployment

The v6 frontend on port 80 (Tailscale) sent `/api/` to the name `backend`,
which two containers answered on `edullm-v6_default` (`edullm-v6-backend` and
`edullm-v6-beta-backend-1`, two databases): requests went to both in turn.
Both are stopped; the release stack never joins `edullm-v6_default`. The
v6-beta frontend (LAN 18443) had been stopped since 2026-10-09.

## Embeddings

Since 2026-10-16 bge-m3 runs on vLLM, like the reranker and MinerU (one pinned
image; Prometheus scrapes it as "Embeddings"):

```sh
docker run -d --name edullm-embeddings --gpus all --ipc=host --restart unless-stopped \
  --network edullm-models -e HF_HOME=/hf -e HOME=/tmp --user "$(id -u):$(id -g)" \
  -v $HOME/.cache/hf-retrieval-eval:/hf \
  nvcr.io/nvidia/vllm:26.07-py3 \
  vllm serve BAAI/bge-m3 --host 0.0.0.0 --port 8000 \
  --runner pooling --gpu-memory-utilization 0.02 --max-model-len 8192 --max-num-seqs 32
```

Admin settings: `EMBEDDING_URL = http://edullm-embeddings:8000/v1`,
`EMBEDDING_MODEL = BAAI/bge-m3`. On the same text it gives the vectors of the
former Ollama server within cosine 0.9999. After a change of embedding server,
`scripts/reembed_documents.py` computes every document again (see its header).

## The switch of 2026-10-16

What runs (`docker-compose.release.yml`, project `edullm-release`, private
settings in `~/.local/share/polytech-release/.env`, mode 0600):

| Container | What |
|---|---|
| `edullm-release-frontend` | nginx with `frontend/nginx.release.conf`: HTTPS on 192.168.168.199:18443 and 100.108.32.29:18443 (v6-beta certificate, `RELEASE_TLS_DIR`), HTTP on 127.0.0.1:18080 only |
| `edullm-release-backend`, `edullm-release-worker` | this version; models on `edullm-models` (Flash-Next as `ornith`, Qwen3 reranker, bge-m3 on vLLM, MinerU) |
| `edullm-release-db` | PostgreSQL with pgvector, volume `edullm-release_release_postgres_data` |
| `edullm-release-backup` | a checked `pg_dump` every 24 h in volume `edullm-release_release_backups`, 14 days kept |

Every service has its own name: the frontend and the database also join the
dev network `polytech_ai_tp_default` (Grafana), where a generic `backend` or
`postgres` would answer for a dev container.

Steps as run:

1. Tables created once with the feature flags off, then
   `migrate_document_snapshots` (8 versions) on the empty database.
2. `migrate_from_v6` from the v6-beta database (read only): 8 accounts (1
   admin, 2 teachers, 5 students), 5 classes (all in exam mode), 4
   memberships, 5 labs, 0 rules, 5 conversations with their 18 messages,
   4 days of usage. Checksums of the accounts (id, name, password hash, role)
   and of the messages are the same on both sides. The teachers' passwords do
   not change.
3. Model settings copied from the dev database (`system_configs`).
4. `monitoring_reader` on the new database; Grafana has a second source
   "EduLLM (teachers)" (`edudb-release`), chosen by "Database" on the Answers
   dashboard (default Teachers). The System dashboard shows the four release
   containers.
5. Checked inside the network, then over HTTPS with the CA: the teachers'
   classes in exam mode, the five model slots served, a 4-page PDF indexed and
   published in 40 s, Direct and Search answers (in a temporary admin class,
   removed), Grafana refused without the admin cookie, Monitoring and LLM
   Config in a browser.
6. Stopped (containers and volumes kept): `edullm-v6-beta-backend-1`, and the
   v6-stable stack (`edullm-v6-frontend` on port 80, `edullm-v6-backend`,
   `edullm-v6-db`, test data of 2026-09-08). The v6-beta frontend had been
   stopped since 2026-10-09; its nginx file was in /tmp and is gone.
   `edullm-v6-beta-postgres-1` and its backup container still run, unchanged.

Going back to v6-beta:

```sh
docker compose -f docker-compose.release.yml --env-file ~/.local/share/polytech-release/.env stop release-frontend
cd ~/worktrees/polytech_ai_tp/v6-stable   # branch v6-beta
docker compose -f docker-compose.beta.yml --env-file ~/.local/share/polytech-v6-beta/.env up -d backend frontend
```

Anything written in the new version since the switch is then not in v6-beta.

Updating the release to a newer commit: `docker compose -f
docker-compose.release.yml --env-file … up -d --build release-backend
release-worker release-frontend` (apply new schema migrations first, as in
step 1). The release does not follow the dev stack by itself.
