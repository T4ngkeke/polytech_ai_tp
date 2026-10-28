> Part of the Edu-LLM documentation. Back to [README](../README.md).

# The teachers' installation

This file describes the installation that the teachers use (`docker-compose.release.yml`), how to update it, and how the teachers moved to it from v6-beta. The dev stack is described under "Run it" in the [README](../README.md).

## Address and access

The teachers open `https://<LAN_IP>/` (on Tailscale, `https://<TAILSCALE_IP>/`). The two addresses are `RELEASE_LAN_IP` and `RELEASE_TAILSCALE_IP` in the private env file. It is HTTPS on the default port 443, with the certificate and CA of v6-beta. The v6-beta port 18443 still works, so old links keep working. Plain `http://` on port 80 redirects to HTTPS.

New classes start in exam mode: answers are written directly, without the course files, as in v6. A teacher turns exam mode off for a class to try the rest: course PDFs, Search and Research modes, Test drive, Analytics and AI reports.

## What runs

The stack is the Compose project `edullm-release`. The private settings are in `~/.local/share/polytech-release/.env` (mode 0600): `RELEASE_DB_PASSWORD`, `RELEASE_JWT_SECRET`, `RELEASE_TLS_DIR`, `RELEASE_LAN_IP`, `RELEASE_TAILSCALE_IP`.

| Container | What |
| --- | --- |
| `edullm-release-frontend` | nginx with `frontend/nginx.release.conf`. HTTPS on `<LAN_IP>` and `<TAILSCALE_IP>`, on ports 443 and 18443. Port 80 redirects to HTTPS. Plain HTTP is on 127.0.0.1:18080 only. It also passes `/grafana/` to Grafana for admins (see [MONITORING.md](MONITORING.md)). |
| `edullm-release-backend`, `edullm-release-worker` | The same code as the dev stack, with the feature flags on and `NEW_CLASS_EXAM_MODE` on. The worker is built with MinerU. |
| `edullm-release-db` | PostgreSQL with `pgvector`, in the volume `edullm-release_release_postgres_data`. |
| `edullm-release-backup` | A checked `pg_dump` every 24 hours in the volume `edullm-release_release_backups`. Dumps are kept for 14 days. |

The backend and the worker use the model servers on the network `edullm-models`: Qwen3.8 Flash-Next (served as `ornith`), the reranker, the embeddings and MinerU. How to start those servers is in [MODEL_SERVERS.md](MODEL_SERVERS.md), [PDF_INGESTION.md](PDF_INGESTION.md) and the README. The model settings are in the release database (`system_configs`) and are changed in the admin page.

Every service has its own name (`release-*`, `edullm-release-*`). The frontend and the database also join the dev network `polytech_ai_tp_default`, where Grafana runs. A generic name such as `backend` or `postgres` would answer there twice, with the dev container and the release container. This is what went wrong in the v6 deployment: two backends answered to one name, and requests went to both in turn.

## Update

The release does not follow the dev stack by itself. To ship a newer commit:

1. Apply new schema migrations first (`scripts/migrate_document_snapshots.py`, see [PDF_INGESTION.md](PDF_INGESTION.md)).
2. Rebuild and restart:

   ```sh
   docker compose -f docker-compose.release.yml --env-file ~/.local/share/polytech-release/.env \
     up -d --build release-backend release-worker release-frontend
   ```

After a change of the embedding server, `scripts/reembed_documents.py` computes every document vector again (see the header of the script).

## The move from v6-beta (2026-10-16)

This section records how the teachers moved, in case it must be undone.

- The tables were created once with the feature flags off. Then `migrate_from_v6` copied the data from the v6-beta database, which it only read: 8 accounts (1 admin, 2 teachers, 5 students), 5 classes (all set to exam mode), 4 memberships, 5 labs, 5 conversations with their 18 messages and 4 days of usage. The checksums of the accounts and of the messages are the same on both sides. The teachers' passwords did not change. Everyone logged in again once, because the signing secret is new.
- The model settings were copied from the dev database.
- The v6-stable stack (`edullm-v6-frontend` on port 80, `edullm-v6-backend`, `edullm-v6-db`) and `edullm-v6-beta-backend-1` were stopped. Their containers and volumes are kept. `edullm-v6-beta-postgres-1` and its backup container still run, unchanged.

To go back to v6-beta:

```sh
docker compose -f docker-compose.release.yml --env-file ~/.local/share/polytech-release/.env stop release-frontend
cd ~/worktrees/polytech_ai_tp/v6-stable   # branch v6-beta
docker compose -f docker-compose.beta.yml --env-file ~/.local/share/polytech-v6-beta/.env up -d backend frontend
```

Anything written in the new version since the switch is then not in v6-beta.
