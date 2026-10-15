# Monitoring

The admin page has a **Monitoring** tab. It shows two Grafana dashboards inside
the app, without Grafana's menus:

| Dashboard | Shows | Source |
|---|---|---|
| System | Which containers run; memory, swap, CPU, GPU use, temperature and power; for each model server the requests running and waiting, KV cache use, time to first token, queue time and tokens per second; memory and CPU by container | Prometheus |
| Answers | Questions, median time, tool limit reached, failures, questions asked back, thumbs down; questions per hour and answer time by mode; a table by mode; why runs stopped; the latest 200 questions; model calls by purpose; document jobs | PostgreSQL |

The Answers dashboard leaves teacher test sessions out unless "Sessions" is set
to "With teacher tests". "Open in Grafana" opens the full Grafana page in a new
tab.

## What runs

Five containers in `docker-compose.override.yml` (the v9 dev stack):

| Container | Role | Memory limit |
|---|---|---|
| `edullm-grafana` | Draws the dashboards; served by the frontend under `/grafana/` | 512 MB |
| `edullm-prometheus` | Records the metrics every 15 s and keeps 15 days | 1 GB |
| `edullm-node-exporter` | Memory, swap and CPU of the machine | 128 MB |
| `edullm-gpu-exporter` | GPU use, temperature and power (from `nvidia-smi`) | 128 MB |
| `edullm-cadvisor` | Which containers run, and their memory and CPU | 256 MB |

Together they used about 180 MB on 2026-10-15. None of them has a port on the
host. The model servers (Flash-Next, reranker, MinerU) already publish vLLM
metrics; Prometheus reads them on the network `edullm-models`.

On the DGX Spark the GPU has no memory of its own: it uses the main memory, so
"Memory available" is also what is left for the models.

## Access

Grafana is shown **without a login**, as a viewer. This is acceptable only
because the frontend of this stack listens on the local and Tailscale addresses
only. Anyone who can open the app address can open `/grafana/`.

A viewer can send any SQL to the PostgreSQL source, so the limit is the database
login `grafana_reader`:

- it reads only the columns in `monitoring/reader_grants.sql`: no user names,
  no passwords, no answer text, no course text (the questions are included);
- every transaction is read-only, and a query stops after 10 s.

Before the app is opened to the internet, or Grafana is moved to a public
address, Grafana must require the app's admin login. That is not built yet.

Editing in Grafana needs the Grafana admin login (`admin`, password in `.env`).
The dashboards are files in `monitoring/grafana/dashboards`; changes made in the
Grafana editor are lost on restart, so edit or export the JSON files instead.

## Setup

1. Create `.env` next to `docker-compose.yml` (it is ignored by git):

   ```
   GRAFANA_ADMIN_PASSWORD=<random>
   GRAFANA_DB_PASSWORD=<random, 16 characters or more>
   ```

2. Create the database login (run again after a password change):

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
- Links from the latest questions to the conversation in the app.
