# Monitoring

The admin page has a **Monitoring** tab. It shows three Grafana dashboards inside
the app, without Grafana's menus:

| Dashboard | Shows | Source |
|---|---|---|
| System | Which containers of the teachers' installation and which model servers run; memory, swap, free disk, time since the last backup, CPU, GPU use, temperature and power; memory and CPU by container. The dev containers are in a folded row at the bottom | Prometheus |
| Answers | Questions, median time by mode, tool limit reached (research only), failures, questions asked back, thumbs down; questions per hour and answer time by mode; a table by mode; why runs stopped. Limits: questions refused before they started (by reason), students at a limit now, reset requests, resets this week, token use by class (no names). The latest 200 questions; model calls by purpose; document jobs; reading PDFs (MinerU): time per page, wait before reading, time until students can use the file | PostgreSQL |
| Model servers | Four health panels on top: requests waiting, time to first token (95%), KV cache used, paused requests. Folded below, for tuning: speed (time to first token, time between tokens, whole request, tokens per second, where the time of an LLM request goes), caches, MTP (speculative decoding), request sizes and the cache settings each server started with. Every panel has an (i) that says what it means | Prometheus |

The Answers dashboard has two choices at the top. "Database" picks the dev
database or the teachers' database (the default is Teachers). "Sessions" leaves
teacher test sessions out, unless it is set to "With teacher tests". "Open in
Grafana" opens the full Grafana page in a new tab.

## What runs

Five containers in `docker-compose.override.yml` (the dev stack):

| Container | Role | Memory limit |
|---|---|---|
| `edullm-grafana` | Draws the dashboards; served by the frontend under `/grafana/` | 512 MB |
| `edullm-prometheus` | Records the metrics every 15 s and keeps 15 days | 1 GB |
| `edullm-node-exporter` | Memory, swap, disk and CPU of the machine; the time of the last backup (a file in `/var/lib/edullm-metrics`) | 128 MB |
| `edullm-gpu-exporter` | GPU use, temperature and power (from `nvidia-smi`) | 128 MB |
| `edullm-cadvisor` | Which containers run, and their memory and CPU | 256 MB |

Together they used about 180 MB on 2026-10-15. None of them has a port on the
host. The model servers (Flash-Next, reranker, embeddings, MinerU) already
publish vLLM metrics; Prometheus reads them on the network `edullm-models`.

The teachers' stack ([RELEASE_TEACHERS.md](RELEASE_TEACHERS.md)) has no
monitoring containers of its own. Its frontend sends `/grafana/` to this
Grafana over the network `polytech_ai_tp_default`, with the same admin check.
Grafana reads its database through the second data source "EduLLM (teachers)".

The release backup container writes `/var/lib/edullm-metrics/backup.prom`
after each good backup (time and size). node-exporter reads that folder, so
"Last backup" turns orange after 26 hours without a good backup.

A question refused before it starts (a limit reached, a locked lab, a student
no longer in the class) has no run; the chat writes it to `chat_refusals` so
that it can be counted. The time a PDF took to read is stored on its revision
(`parse_started_at`, `parse_finished_at`).

On the DGX Spark the GPU has no memory of its own: it uses the main memory, so
"Memory available" is also what is left for the models.

## Access

Only admins of the app see Grafana. When the Monitoring tab opens, the app asks
the backend for a monitoring cookie (`POST /api/admin/monitoring/session`,
admins only). The cookie is valid for `/grafana/` only, for 8 hours, and is
HttpOnly; it is signed for this use only, so an app login token does not work
in its place. For every `/grafana/` request, nginx asks the backend
(`auth_request` to `/api/admin/monitoring/check`), which lets it through only
when the cookie belongs to a user who is an admin now. A user who is demoted or
deleted loses access at once. Behind that check Grafana shows its pages as a
viewer, without a login of its own.

A viewer can send any SQL to the PostgreSQL source, so the database login
`grafana_reader` is still limited:

- it reads only the columns in `monitoring/reader_grants.sql`: no user names,
  no passwords, no answer text, no course text (the questions are included).
  From `users` it reads only the role and the two quotas;
- every transaction is read-only, and a query stops after 10 s.

Editing in Grafana needs the Grafana admin login (`admin`, password in `.env`).
The dashboards are files in `monitoring/grafana/dashboards`; changes made in the
Grafana editor are lost on restart, so edit or export the JSON files instead.

The browser check (`scripts/check_layout_ui.py`) makes sure `/grafana/` answers
401 without the cookie. To also check the dashboards, set `MONITORING_COOKIE`
to an admin's monitoring token.

## Setup

1. Create `.env` next to `docker-compose.yml` (it is ignored by git):

   ```
   GRAFANA_ADMIN_PASSWORD=<random>
   GRAFANA_DB_PASSWORD=<random, 16 characters or more>
   ```

2. Create the database login (run again after a password change, and after a
   change of `monitoring/reader_grants.sql`, on both databases):

   ```
   set -a; . ./.env; set +a
   .venv/bin/python -m scripts.monitoring_reader --database-url postgresql+asyncpg://USER:PASSWORD@127.0.0.1:5432/edudb
   ```

3. Start the containers, and rebuild the frontend once for the `/grafana/` route:

   ```
   docker compose up -d grafana prometheus node-exporter gpu-exporter cadvisor
   docker compose up -d --build --no-deps frontend
   ```

Without these containers the app works as before, and the Monitoring tab says
that monitoring is not running.

## Not done yet

- Alerts (for example a model server down for 5 minutes). Grafana can send them
  once a destination (e-mail or a chat tool) is chosen.
- Signing out of the app does not remove the monitoring cookie; it ends after
  8 hours, and it stops working at once if the user is no longer an admin.
- Links from the latest questions to the conversation in the app.
