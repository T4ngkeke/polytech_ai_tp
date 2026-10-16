# Release to the teachers (replacing v6-beta)

The teachers who use v6-beta move to the current version. Classes start in
exam mode: answers are written directly, without the course files, as in v6.
Teachers can then try the rest (course PDFs, Search and Research, Test drive,
Analytics, AI reports) and report what they find.

## What is ready

| Part | Where |
|---|---|
| New classes start in exam mode (setting `NEW_CLASS_EXAM_MODE`, on by default) | `backend/app/services/class_service.py` |
| Copy accounts, classes, memberships, labs and rules from v6; every copied class in exam mode | `scripts/migrate_from_v6.py` |
| Grafana open to app admins only | `backend/app/routers/monitoring.py`, `frontend/nginx.conf`, [MONITORING.md](MONITORING.md) |

`migrate_from_v6.py` was run on a copy of the v6-beta database on 2026-10-15:
8 accounts (1 admin, 2 teachers, 5 students), 5 classes, 4 memberships, 5 labs,
0 rules. The copied accounts were identical (ids, names, password hashes,
roles, quotas), every class was in exam mode, and a second run was refused.
Conversations are not copied (18 messages, all tests).

## Still to do before the switch

1. A compose file for the new stack, from `docker-compose.beta.yml` (branch
   `v6-beta`): PostgreSQL with pgvector, backend and worker of this version, the
   `edullm-models` network (Flash-Next, reranker, MinerU, embeddings), the flags
   `STRUCTURED_INGEST_ENABLED`, `PDF_STRUCTURED_INGEST_ENABLED`,
   `AGENTIC_SEARCH_ENABLED` and `STRUCTURED_VISION_ENABLED` ("Write with AI" on a
   picture), and the TLS nginx config with the `/grafana/` and
   `/_monitoring_auth` blocks of `frontend/nginx.conf`.
2. A second PostgreSQL source in Grafana for the new database (the dev stack's
   Grafana and Prometheus are shared; the System dashboard already covers the
   whole machine).

## Steps

1. Start the new stack's database and backend once, so the tables exist. Apply
   the schema migrations (dry run first):

   ```
   python -m scripts.migrate_document_snapshots --database-url NEW_URL
   python -m scripts.migrate_document_snapshots --database-url NEW_URL --apply
   ```

2. Copy from v6-beta (read only on the v6 side; dry run first):

   ```
   python -m scripts.migrate_from_v6 --source V6_URL --target NEW_URL
   python -m scripts.migrate_from_v6 --source V6_URL --target NEW_URL --apply
   ```

   The target must have no user yet: do not run the bootstrap of the beta
   compose file, the admin account comes from v6.

3. Set the models in Admin > LLM Config (the model map), or in the private
   environment file.
4. Create the Grafana login on the new database (`scripts/monitoring_reader.py`)
   and add it as a data source.
5. Check with a teacher account: the classes are there and in exam mode, a
   student can ask, a PDF upload is indexed and published.
6. Switch: stop the v6-beta frontend and start the new one on the same LAN
   address and port (`https://192.168.168.199:18443/`) with the same
   certificate, so teachers keep their address and their CA.
7. Going back: the v6-beta database volume is not touched; stop the new
   frontend and start the v6-beta one again.

## Known problem in the current v6 deployment

The v6 frontend on port 80 (Tailscale) sends `/api/` to the name `backend`.
On the network `edullm-v6_default` two containers answer to that name:
`edullm-v6-backend` (database `edullm-v6-db`) and `edullm-v6-beta-backend-1`
(the beta database). nginx sends requests to both in turn, so a user can be
logged in on one and refused by the other. The new stack must not join
`edullm-v6_default`; the old entry should be stopped at the switch.
