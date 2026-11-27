# The Python runner

The research agent's `execute_code` (`backend/app/agent/tools/code.py`) runs here when the backend has `PYTHON_RUNNER_URL`. The student's own Run button still runs in the browser.

- `server.mjs`: a small HTTP server. `POST /run {code}` answers `{status, stdout, stderr, result}`. Runs wait in a queue, 2 at a time (`MAX_PARALLEL`); a run is killed after 60 s (`RUN_SECONDS`).
- `run.cjs`: one run. A fresh Pyodide (Python compiled to WebAssembly) in its own Node process; the packages the code imports are loaded from the local files. A matplotlib figure is a `data:image/png;base64,...` line in stdout, as in the browser.
- `Dockerfile`: the same Pyodide and packages as the browser (`frontend/scripts/fetch-pyodide.mjs`, every file checked against its hash).
- `fetch-font.mjs`: Noto Sans SC (SIL Open Font License 1.1, its license file beside it in `/fonts`), checked against its hash. Pyodide's matplotlib has only DejaVu Sans, so Chinese text was drawn as boxes. `run.cjs` copies the font into Pyodide when the code uses matplotlib and sets DejaVu Sans first, then Noto Sans SC for the characters DejaVu lacks. The browser's Run button has no such font yet.

## What a run can reach

Python can reach Node's objects through Pyodide's JavaScript proxies (for example a function's `constructor`). So there are several walls:

1. `import js` sees only the timers, and the `pyodide_js` module is removed before the code runs.
2. Node's permission model on the run's process: read only Pyodide's folder, `run.cjs` and the font; no file writes, no child processes, no worker threads. The environment is empty.
3. The container (`docker-compose.yml`): read-only file system, no capabilities, no new privileges, 64 processes, 1.5 GB, on the internal network `sandbox` with the backend only.

Checked on dev on 2026-11-26, from inside a run: `child_process`, reading `/etc/passwd`, writing a file and starting a worker were refused (`ERR_ACCESS_DENIED`); the environment held only `PYODIDE_DIR`; the internet, the database, the model servers and SearXNG could not be reached; the backend's `/api/health` could. An endless loop was stopped at the time limit, a 3 GB allocation failed with `MemoryError`, and the runner (process 1) survived a `SIGKILL` sent from a run.

Known limit: two runs at the same time are the same user, so one could stop the other.
