> Part of the Edu-LLM documentation. Back to [README](../README.md).

# Worker

This file lists the files of the PDF worker. Its steps, its settings and the MinerU setup are in [PDF_INGESTION.md](PDF_INGESTION.md); its place in the system is in [ARCHITECTURE.md](ARCHITECTURE.md).

The worker reads PDFs only. The old worker (text cleaning in `parsing.py`, splitting in `chunking.py`, search notes written by a model for each passage, page reading with a vision model, formula repair, and ODP, Markdown and HTML import) was removed; its code is kept on the branch `archive/cleanup-2026-10-15`.

## The code

`backend/worker/` holds these files:

| File | What it does |
| --- | --- |
| `main.py` | The loop. It waits while students chat, then takes one job and runs it. It picks up new model addresses without a restart. |
| `gpu_gate.py` | Says whether the worker may work now: it pauses while the chat is busy, and also while the GPU is busy (no effect without a local GPU). |
| `queue.py` | Takes a job with `FOR UPDATE SKIP LOCKED`. Each claim has a lock token and a lease; a job whose lease ran out can be taken again. |
| `structured_ingest.py` | The reading job: MinerU reads each page, and the pages, blocks and pictures are stored as a draft. Its `sweep` is the housekeeping between jobs (below). |
| `structured_index.py` | The index job: cuts the blocks into passages, makes the embeddings and the full-text vectors, and publishes the revision when every page was read. |
| `picture_captions.py` | Two or three French sentences on each picture, written by the main model, so that search finds the page (see [PICTURE_CAPTIONS.md](PICTURE_CAPTIONS.md)). |
| `pdf_reader.py`, `pdf_mineru.py`, `pdf_mineru_cli.py` | Call MinerU and turn its result into pages and blocks. |
| `document_ir.py` | The data structures of pages, blocks and pictures. |

The jobs are rows of `ingestion_jobs` (see [DATABASE.md](DATABASE.md)). Old rows without `task_kind` are never taken.

## Housekeeping

Between jobs, `structured_ingest.sweep` does two things:

- It removes the staged files whose writer's lease has passed (an upload or a job that never finished).
- It runs the due cleanup jobs of deleted documents (`services/document_cleanup.run_due_cleanups`, up to 10 at a time, one transaction each): the stored files, then the pages, blocks, chunks and revisions. A failed attempt is tried again later. Until November 2026 nothing ran these jobs.

At start, the worker also removes the job folders that a crashed worker left in `STRUCTURED_TASK_ROOT`.
