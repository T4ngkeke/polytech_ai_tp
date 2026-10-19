> Part of the Edu-LLM documentation. Back to [README](../README.md).

# PDF ingestion

This file says how an uploaded PDF becomes searchable, and how to set up MinerU (a PDF reading tool), the database migrations and the feature flags that this needs. The overview is in [ARCHITECTURE.md](ARCHITECTURE.md).

## Processing path

A teacher uploads a PDF to a lab or to a class. The upload creates an immutable source and a draft revision. Teachers export slides and notes to PDF first. Other formats are refused with `400 only_pdf`.

The worker then runs two jobs on the revision:

1. **Read.** The worker reads each physical page with MinerU. It normalizes the result into blocks (heading, text, code, formula, table, figure) and records issues. It keeps the original PDF and a preview of each page.
2. **Index.** The same job becomes an index job. It cuts the blocks into passages, embeds them, and checks the embedding batches. A passage keeps exact character ranges into its block.

When every page was read and the index is ready, the revision is published at once. If a page could not be read, the revision stays a draft and the teacher publishes it from the document view ("Publish anyway"). Uploading or reading alone never publishes a revision when a page failed.

The reader keeps extracted text, code, formulas and tables. For a figure, it keeps the picture and takes its label from the text of the PDF in the figure region. It does not write a description of the figure. A teacher can ask the vision model for a description of one picture ("Write with AI"). That description is a candidate that the teacher checks. Missing labels, mathematical subscripts and the relations inside a diagram can be lost. The original PDF is the reference.

Teachers can inspect drafts, edit blocks, leave blocks out of search and read a page again. A change never alters a revision. It makes a new draft. The student's PDF view opens the complete course PDF at the cited page. Version and permission checks protect every reader endpoint and file. The endpoints are in [API.md](API.md).

Limits of an upload: 200 MB, at most 150 pages, no password. The nginx in front of the backend accepts a request body of 50 MB (`client_max_body_size` in `frontend/nginx.conf` and `frontend/nginx.release.conf`).

## Configuration and migration

Ingestion is off by default. Turn it on only in the environment where it should run.

1. Apply the SQL migrations. The command is a dry run first. Applied versions have checksums and run in one transaction. The command never picks a database by itself. `create_all` cannot replace it.

   ```sh
   python -m scripts.migrate_document_snapshots --database-url "$DB_URL"
   python -m scripts.migrate_document_snapshots --database-url "$DB_URL" --apply
   ```

2. Set `STRUCTURED_INGEST_ENABLED=true` and `PDF_STRUCTURED_INGEST_ENABLED=true`. Without both, an upload gets `409 structured_ingest_disabled`, and the worker has no jobs.
3. Give the worker the MinerU settings. The variables and their defaults are in [README.md](../README.md): `PDF_MINERU_PYTHON`, `PDF_MINERU_SERVER_URL`, `PDF_MINERU_CONFIG` (an absolute path), `PDF_MINERU_PAGE_TIMEOUT_SECONDS` (240), `PDF_READ_BUDGET_SECONDS` (7200), and the lease and attempt settings. A missing setting or a MinerU version other than 3.4.5 is an explicit failure of the job.

Both compose files set these for the worker.

## MinerU setup

MinerU has three parts. All of them are outside the backend image.

1. **Server.** vLLM with `opendatalab/MinerU2.5-Pro-2605-1.2B`, on the network `edullm-models` as the container `edullm-mineru-vlm`. The memory is set by size, not by a fraction. The check of a fraction fails when other processes change the shared memory during start-up. The server takes about 4.3 GB. A smaller KV cache only limits how many pages run at once. `mineru-vl-utils` 1.x (the version that MinerU 3.4.5 asks for) is installed with `pip install --no-deps --target` into the folder mounted at `/extra`.

   ```sh
   docker run -d --name edullm-mineru-vlm --gpus all --ipc=host \
     --network edullm-models -p 127.0.0.1:18003:8000 \
     -e PYTHONPATH=/extra -e OMP_NUM_THREADS=1 \
     -v /home/spark-1/edullm-mineru/extra:/extra:ro \
     -v /home/spark-1/.cache/huggingface:/root/.cache/huggingface \
     nvcr.io/nvidia/vllm:26.07-py3 \
     vllm serve opendatalab/MinerU2.5-Pro-2605-1.2B --host 0.0.0.0 --port 8000 \
     --served-model-name opendatalab/MinerU2.5-Pro-2605-1.2B \
     --gpu-memory-utilization 0.05 --kv-cache-memory-bytes 600000000 \
     --hf-overrides '{"tie_word_embeddings": true}' \
     --logits-processors mineru_vl_utils:MinerULogitsProcessor
   ```

2. **Client.** The worker image built with `--build-arg WITH_MINERU=1` has MinerU 3.4.5 at `/opt/mineru/bin/python`, with CPU torch for the local layout and OCR models. The worker calls MinerU in this separate interpreter. One page takes 15 to 20 seconds and at most 0.8 GB.
3. **Models and config.** Run `mineru-models-download -s huggingface -m pipeline` once, with `HOME` and `HF_HOME` in `/home/spark-1/edullm-mineru/home` (2.5 GB). Then write `mineru.json` with the pipeline path as the worker sees it (`/opt/mineru-data/...`) and `"model-source": "local"`. The worker mounts the folder read-only at `/opt/mineru-data`.

The MinerU server can change without a restart. The MinerU node of the admin model map stores `PDF_MINERU_SERVER_URL` in `system_configs`, and the worker uses it from its next loop. When it is empty, the worker uses the value of its environment. The client, its models and `mineru.json` are deployment settings.

## Tests

Install `backend/requirements-test.txt` for the test dependencies. The tests build their own PDF fixtures. Tests that need a live MinerU or PostgreSQL need those services, set explicitly (see "Run it" in the [README](../README.md)). Tests that need a local course corpus skip themselves when it is missing.

Course PDFs, page previews, raw model responses and logs stay local and are not committed.
