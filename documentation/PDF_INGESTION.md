# Structured PDF ingestion

This change delivers the document foundation and PDF worker through M3. The
student agent loop, durable research runs and M4 accounting are developed
separately on `feature/agentic-search`.

## Processing path

A structured PDF upload creates an immutable source and a draft revision. The
worker extracts each physical page with MinerU, normalizes blocks, records
issues and retains the original PDF. A separate index job creates bounded,
source-linked chunks and validates embedding batches. Publication requires a
ready index and verified page mapping; uploading or parsing alone does not
publish a revision.

The `mineru-text` policy keeps extracted text, code, formulas and tables. It
replaces generated figure descriptions with native labels from the region and
retains the figure asset. It does not mean text-only ingestion. Missing labels,
figures, mathematical scripts and diagram relationships remain known extraction
limitations; the original PDF is the reference for review.

Teachers can inspect drafts, edit blocks and review issues before publication.
Version, tenant and permission checks protect reader endpoints and file access.
The student PDF view opens the complete authorized source at its resolved
physical page. This milestone does not establish autonomous search quality.

## Configuration and migration

Structured writes remain disabled by default. Explicitly apply the incremental
M1, M2, M2-C, M3-A and M3-B migrations before enabling them. For example, supply a
database URL explicitly and run:

```sh
python -m scripts.migrate_document_snapshots --database-url "$INGESTION_DB_URL"
python -m scripts.migrate_document_snapshots --database-url "$INGESTION_DB_URL" --apply
```

The first command is a read-only dry-run. Applied versions have checksums and
are serialized in a transaction. These commands do not select a production
database implicitly. Existing installations require the explicit migration;
`create_all` cannot replace it.

Set `STRUCTURED_INGEST_ENABLED=true` and `PDF_STRUCTURED_INGEST_ENABLED=true`
only in the intended environment. The PDF worker needs `PDF_MINERU_PYTHON`,
`PDF_MINERU_SERVER_URL` and an absolute `PDF_MINERU_CONFIG` path. Extraction runs
in a separate interpreter with MinerU 3.4.5 and locally provisioned models.
`PDF_MINERU_PAGE_TIMEOUT_SECONDS` defaults to 240. Missing configuration or a
version mismatch is an explicit failure.

### MinerU setup (v9 test stack, 2026-10-14)

Three parts, all outside the backend image:

1. **Server.** vLLM with MinerU2.5-Pro-2605-1.2B, next to the agent model on
   one DGX Spark. Memory is set by size, not by fraction: the profiling check
   of a fraction fails when other processes change the shared memory during
   start-up. It takes about 4.3 GB; a smaller KV cache only limits how many
   pages run at once. `mineru-vl-utils` 1.x (the version MinerU 3.4.5 asks for)
   is installed with `pip install --no-deps --target` into the mounted folder.

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

2. **Client.** The worker image built with `--build-arg WITH_MINERU=1` has
   MinerU 3.4.5 at `/opt/mineru/bin/python`, with CPU torch for the local
   layout and OCR models. One page takes 15–20 s and at most 0.8 GB.
3. **Models and config.** `mineru-models-download -s huggingface -m pipeline`
   run once with `HOME` and `HF_HOME` in `/home/spark-1/edullm-mineru/home`
   (2.5 GB), then `mineru.json` with the pipeline path as the worker sees it
   (`/opt/mineru-data/...`) and `"model-source": "local"`. The worker mounts
   the folder read-only at `/opt/mineru-data` (see docker-compose.override.yml).

Check: four rich pages (tables, a figure) gave the same content list, bounding
boxes included, as the September 2026 run.

The server can change without a restart: the MinerU node of the admin model
map stores `PDF_MINERU_SERVER_URL` in SystemConfig, and the worker uses it from
its next tick (empty: the worker's environment value). The client, its models
and `mineru.json` stay deployment settings.

ODP parsing and an isolated LibreOffice renderer remain available for historical
fixtures and source-to-PDF experiments. ODP feature development is frozen;
there is no requirement to implement a custom reader for every source format.
The renderer Dockerfile and test Compose file run separately from application
services. They do not automatically install MinerU in the worker image.

## Validation and experimental artifacts

Install `backend/requirements-test.txt` for the offline test dependencies.
Offline tests generate deterministic document fixtures. Live MinerU and
PostgreSQL tests require explicit isolated services. Real-course tests that
lack their local corpus are skipped; this is not a live-model acceptance result.

Course PDFs/ODPs, preview images, raw model responses, JSONL logs and generated
reports remain local. Small frozen checks and input manifests used by benchmark
scripts are tracked separately; JSON is a storage format, not a reason to commit
every output. The retained benchmark definitions still require matching local
course files. Chinese Markdown reports and plans are excluded from this commit.

Use a separate artifact store or release archive for complete experimental
outputs, with hashes and environment metadata when sharing a reproducible run.
Automatic source, range and permission checks do not establish semantic accuracy
or human acceptance. Historical results are not rewritten by this Git split.
