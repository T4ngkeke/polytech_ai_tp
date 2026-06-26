"""
main.py — [v7] ingestion worker entrypoint.

The worker repeatedly:
  1. (GPU gate) only pulls work off-peak / when the GPU is idle,
  2. claims one job via FOR UPDATE SKIP LOCKED,
  3. runs the ingest pipeline,
  4. marks the job done (or failed).

`process_one` is the single, testable step. The run loop wraps it with the
GPU gate and sleeps; embed/extract default to the engine configured in
SystemConfigs (wired later).
"""

import asyncio
from typing import Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models import IngestionJob, JobStatus
from backend.worker.ingest import ContextFn, EmbedFn, ExtractFn, ingest_document
from backend.worker.queue import claim_next_job

# How long to sleep when the queue is empty vs. when the GPU gate is closed.
IDLE_SLEEP_SECONDS = 5.0
GATE_SLEEP_SECONDS = 10.0


async def process_one(
    db: AsyncSession,
    *,
    embed_fn: EmbedFn,
    extract_fn: ExtractFn,
    context_fn: ContextFn | None = None,
) -> bool:
    """
    Claim and process one ingestion job.

    Returns True if a job was processed (success or failure), False if the
    queue was empty.
    """
    job = await claim_next_job(db)
    if job is None:
        return False

    job_id = job.id
    document_id = job.document_id

    try:
        await ingest_document(
            db, document_id,
            embed_fn=embed_fn, extract_fn=extract_fn, context_fn=context_fn,
        )
        job = await db.get(IngestionJob, job_id)
        job.status = JobStatus.done
        job.error_message = None
    except Exception as exc:  # ingest already marked the document failed
        await db.rollback()
        job = await db.get(IngestionJob, job_id)
        job.status = JobStatus.failed
        job.error_message = str(exc)

    await db.commit()
    return True


async def run_tick(
    db: AsyncSession,
    gate,
    *,
    embed_fn: EmbedFn,
    extract_fn: ExtractFn,
    context_fn: ContextFn | None = None,
    sleep_fn: Callable[[float], Awaitable[None]] = asyncio.sleep,
    idle_seconds: float = IDLE_SLEEP_SECONDS,
    gate_seconds: float = GATE_SLEEP_SECONDS,
    chat_gate=None,
    chat_load_fn: Callable[[AsyncSession], Awaitable[float]] | None = None,
) -> bool:
    """
    One iteration of the worker loop.

    Pulls work only when both gates allow it, processes at most one job, and
    sleeps when gated or the queue is empty. Returns whether a job was processed.

    Gating order:
      1. `chat_gate` (primary, engine-agnostic): pause while students are actively
         chatting. `chat_load_fn(db)` samples recent chat activity from the DB.
      2. `gate` (optional GPU gate): also require the GPU idle (no-ops on a remote
         API, where there is no local GPU).

    When `chat_gate`/`chat_load_fn` are omitted, only the GPU gate applies (v7
    behaviour).
    """
    # Primary: back off while chat is busy (works for local and remote engines).
    if chat_gate is not None and chat_load_fn is not None:
        if not chat_gate.observe(await chat_load_fn(db)):
            await sleep_fn(gate_seconds)
            return False

    # Optional: GPU idle gate.
    if not gate.should_run():
        await sleep_fn(gate_seconds)
        return False

    processed = await process_one(
        db, embed_fn=embed_fn, extract_fn=extract_fn, context_fn=context_fn,
    )
    if not processed:
        await sleep_fn(idle_seconds)
    return processed


async def run_forever(
    db: AsyncSession,
    gate,
    *,
    embed_fn: EmbedFn,
    extract_fn: ExtractFn,
    context_fn: ContextFn | None = None,
    chat_gate=None,
    chat_load_fn: Callable[[AsyncSession], Awaitable[float]] | None = None,
) -> None:  # pragma: no cover - thin infinite-loop glue
    """Run the ingestion worker loop forever."""
    while True:
        await run_tick(
            db, gate, embed_fn=embed_fn, extract_fn=extract_fn, context_fn=context_fn,
            chat_gate=chat_gate, chat_load_fn=chat_load_fn,
        )


# ---------------------------------------------------------------------------
# Production glue (not unit-tested: hits the engine / GPU / DB)
# ---------------------------------------------------------------------------

# Schema the extractor is constrained to (guided JSON / structured decoding).
_EXTRACTION_SCHEMA = {
    "type": "object",
    "properties": {
        "exercises": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "number": {"type": "string"},
                    "statement": {"type": "string"},
                    "hints": {"type": ["string", "null"]},
                    "concept": {"type": ["string", "null"]},
                },
                "required": ["number", "statement"],
            },
        }
    },
    "required": ["exercises"],
}


def _make_real_embed_fn(client, model: str) -> EmbedFn:  # pragma: no cover
    async def embed(texts: list[str]) -> list[list[float]]:
        # encoding_format="float" is mandatory: the OpenAI SDK otherwise defaults
        # to "base64", which some OpenAI-compatible servers (e.g. Albert) reject
        # with a 500. Float is universally supported.
        resp = await client.embeddings.create(
            model=model, input=texts, encoding_format="float"
        )
        return [item.embedding for item in resp.data]
    return embed


def _make_real_extract_fn(client, model: str) -> ExtractFn:  # pragma: no cover
    import json

    async def extract(text: str) -> list[dict]:
        resp = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "Extract every exercise as JSON."},
                {"role": "user", "content": text},
            ],
            extra_body={"guided_json": _EXTRACTION_SCHEMA},
        )
        data = json.loads(resp.choices[0].message.content)
        return data.get("exercises", [])
    return extract


def _make_real_context_fn(client, model: str) -> ContextFn:  # pragma: no cover
    """Contextual Retrieval: situate a chunk within its document (one short sentence)."""
    _PROMPT = (
        "Here is a course document:\n<document>\n{doc}\n</document>\n\n"
        "Here is a chunk from it:\n<chunk>\n{chunk}\n</chunk>\n\n"
        "Give a short, standalone sentence situating this chunk within the document "
        "(section/topic) to improve retrieval. Answer with the sentence only."
    )

    async def context(full_text: str, chunk_text: str) -> str:
        resp = await client.chat.completions.create(
            model=model,
            messages=[{
                "role": "user",
                "content": _PROMPT.format(doc=full_text[:8000], chunk=chunk_text),
            }],
        )
        return (resp.choices[0].message.content or "").strip()
    return context


# Window over which recent chat activity is counted for the ChatLoadGate.
CHAT_LOAD_WINDOW_SECONDS = 120


async def _recent_chat_count(db: AsyncSession) -> float:  # pragma: no cover - DB I/O glue
    """Count messages written in the last `CHAT_LOAD_WINDOW_SECONDS` — a proxy for
    live chat activity. Engine-agnostic: it never inspects the inference backend."""
    from datetime import datetime, timedelta, timezone

    from sqlalchemy import func, select

    from backend.app.models import Message

    cutoff = datetime.now(timezone.utc) - timedelta(seconds=CHAT_LOAD_WINDOW_SECONDS)
    result = await db.execute(
        select(func.count(Message.id)).where(Message.created_at >= cutoff)
    )
    return float(result.scalar_one())


def _pynvml_util() -> float:  # pragma: no cover
    """Current GPU utilization %, or 0.0 (assume idle) if pynvml is unavailable."""
    try:
        import pynvml
        pynvml.nvmlInit()
        handle = pynvml.nvmlDeviceGetHandleByIndex(0)
        return float(pynvml.nvmlDeviceGetUtilizationRates(handle).gpu)
    except Exception:
        return 0.0


async def _load_config(db: AsyncSession) -> dict:  # pragma: no cover
    """Read LLM/embedding config from SystemConfigs, falling back to settings."""
    from sqlalchemy import select

    from backend.app.config import settings
    from backend.app.models import SystemConfig

    rows = (await db.execute(select(SystemConfig))).scalars().all()
    cfg = {r.key: r.value for r in rows}
    return {
        "base_url": cfg.get("LLM_BASE_URL", settings.LLM_BASE_URL),
        "api_key": cfg.get("LLM_API_KEY", settings.LLM_API_KEY),
        "model": cfg.get("LLM_MODEL", settings.LLM_MODEL),
        "embedding_model": cfg.get("EMBEDDING_MODEL", "bge-m3"),
    }


async def main() -> None:  # pragma: no cover - entrypoint
    """`python -m backend.worker.main` — run the ingestion worker."""
    from openai import AsyncOpenAI

    from backend.app.database import AsyncSessionLocal
    from backend.worker.gpu_gate import ChatLoadGate, GpuGate

    async with AsyncSessionLocal() as db:
        cfg = await _load_config(db)
        client = AsyncOpenAI(api_key=cfg["api_key"], base_url=cfg["base_url"])
        embed_fn = _make_real_embed_fn(client, cfg["embedding_model"])
        extract_fn = _make_real_extract_fn(client, cfg["model"])
        context_fn = _make_real_context_fn(client, cfg["model"])
        # Primary gate: live chat load (engine-agnostic). Optional: GPU idle
        # (no-ops on a remote API where _pynvml_util reads idle).
        chat_gate = ChatLoadGate()
        gate = GpuGate(util_fn=_pynvml_util)
        await run_forever(
            db, gate, embed_fn=embed_fn, extract_fn=extract_fn, context_fn=context_fn,
            chat_gate=chat_gate, chat_load_fn=_recent_chat_count,
        )


if __name__ == "__main__":  # pragma: no cover
    asyncio.run(main())
