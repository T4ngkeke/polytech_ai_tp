"""
main.py — the document worker.

The worker repeatedly:
  1. waits while students are chatting (the chat-load gate) or the GPU is busy
     (the optional GPU gate),
  2. claims one structured job — a PDF to read with MinerU
     (structured_ingest.py) or a parsed draft to index (structured_index.py) —
     and runs it,
  3. removes expired staged files when there is nothing to do.

`run_tick` is the single, testable step. The embedding client and the MinerU
server are rebuilt from SystemConfig when an admin changes them, without a
restart. The pre-MinerU ingestion pipeline (page reader, chunk context,
formula repair) was removed on 2026-10-15.
"""

import asyncio
import logging
from dataclasses import dataclass
from typing import Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.config import settings
from backend.worker.structured_index import EmbedFn

logger = logging.getLogger(__name__)

# How long to sleep when the queue is empty vs. when a gate is closed.
IDLE_SLEEP_SECONDS = 5.0
GATE_SLEEP_SECONDS = 10.0


async def run_tick(
    db: AsyncSession,
    gate,
    *,
    embed_fn: EmbedFn,
    sleep_fn: Callable[[float], Awaitable[None]] = asyncio.sleep,
    idle_seconds: float = IDLE_SLEEP_SECONDS,
    gate_seconds: float = GATE_SLEEP_SECONDS,
    chat_gate=None,
    chat_load_fn: Callable[[AsyncSession], Awaitable[float]] | None = None,
    structured=None,
) -> bool:
    """
    One iteration of the worker loop.

    Pulls work only when both gates allow it, processes at most one job, and
    sleeps when gated or when there is nothing to do. Returns whether a job ran.

    Gating order:
      1. `chat_gate` (primary, engine-agnostic): pause while students are actively
         chatting. `chat_load_fn(db)` samples recent chat activity from the DB.
      2. `gate` (optional GPU gate): also require the GPU idle (no-ops on a remote
         API, where there is no local GPU).
    """
    if chat_gate is not None and chat_load_fn is not None:
        if not chat_gate.observe(await chat_load_fn(db)):
            await sleep_fn(gate_seconds)
            return False

    if not gate.should_run():
        await sleep_fn(gate_seconds)
        return False

    # Structured jobs run only while structured writes are enabled.
    if structured is not None and settings.STRUCTURED_INGEST_ENABLED:
        from backend.worker.structured_ingest import process_structured_job, sweep
        from backend.worker.structured_index import process_index_job

        if await process_structured_job(db, settings=structured):
            return True
        if await process_index_job(db, embed_fn=embed_fn,
                                   lease_seconds=structured.lease_seconds,
                                   max_attempts=structured.max_attempts):
            return True
        await sweep(db, structured)

    await sleep_fn(idle_seconds)
    return False


@dataclass
class WorkerFns:
    """The model-touching callables the worker loop needs, all bound to one
    SystemConfig snapshot. Rebuilt when the config changes (hot-reload)."""
    embed_fn: EmbedFn
    # MinerU server set in the admin map; empty = PDF_MINERU_SERVER_URL.
    mineru_server_url: str = ""
    # Picture captions after reading (picture_captions.py); None = off.
    captions: object = None


def with_mineru_server(structured, url: str):
    """The structured settings of this tick: the admin's MinerU server when one
    is set, so it can change without a worker restart."""
    if structured is None or not url:
        return structured
    from dataclasses import replace
    return replace(structured, pdf_server_url=url)


def with_captions(structured, captions):
    """The structured settings of this tick with the admin's caption settings."""
    if structured is None:
        return structured
    from dataclasses import replace
    return replace(structured, captions=captions)


def make_fn_builder(load_config=None, build_fns=None):
    """Return an async ``build(db)`` that reloads SystemConfig on every call and
    rebuilds the model clients ONLY when the config changed — so an admin edit to
    the embedding config or the MinerU server takes effect in the worker WITHOUT
    a restart, while the (heavier) client construction still runs only on a change.

    `load_config` / `build_fns` are injected in tests; production uses
    `_load_config` (reads the DB) + `_build_worker_fns` (builds real clients)."""
    _load = load_config or _load_config
    _build = build_fns or _build_worker_fns
    state: dict = {"cfg": None, "fns": None}

    async def build(db):
        cfg = await _load(db)
        if cfg != state["cfg"]:
            if state["cfg"] is not None:
                logger.info("Worker SystemConfig changed — reloading model clients.")
            fns = _build(cfg)
            state["cfg"] = dict(cfg)
            state["fns"] = fns
        return state["fns"]

    return build


async def run_forever(
    db: AsyncSession,
    gate,
    *,
    build_fns,
    chat_gate=None,
    chat_load_fn: Callable[[AsyncSession], Awaitable[float]] | None = None,
    structured=None,
) -> None:  # pragma: no cover - thin infinite-loop glue
    """Run the worker loop forever, hot-reloading the config each tick
    (`build_fns(db)` re-reads SystemConfig and rebuilds clients on change)."""
    while True:
        fns = await build_fns(db)
        await run_tick(
            db, gate, embed_fn=fns.embed_fn,
            chat_gate=chat_gate, chat_load_fn=chat_load_fn,
            structured=with_captions(with_mineru_server(structured, fns.mineru_server_url), fns.captions),
        )


# ---------------------------------------------------------------------------
# Production glue (not unit-tested: hits the engine / GPU / DB)
# ---------------------------------------------------------------------------

def _make_real_embed_fn(client, model: str) -> EmbedFn:  # pragma: no cover
    async def embed(texts: list[str]) -> list[list[float]]:
        # encoding_format="float" is mandatory: the OpenAI SDK otherwise defaults
        # to "base64", which some OpenAI-compatible servers (e.g. Albert) reject
        # with a 500. Float is universally supported.
        resp = await client.embeddings.create(
            model=model, input=texts, encoding_format="float"
        )
        items = sorted(resp.data, key=lambda item: item.index)
        if [item.index for item in items] != list(range(len(texts))):
            raise ValueError("Embedding response indices do not match the input batch.")
        return [item.embedding for item in items]
    return embed


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
    """The worker's settings from SystemConfigs: the embedding endpoint (indexing),
    the MinerU server set in the admin map and the picture caption settings."""
    from sqlalchemy import select

    from backend.app.models import SystemConfig
    from backend.app.services.model_routing import resolve_model_routing

    rows = (await db.execute(select(SystemConfig))).scalars().all()
    routing = resolve_model_routing({r.key: r.value for r in rows})
    return {
        "embedding_base_url": routing.embedding.base_url,
        "embedding_api_key": routing.embedding.api_key,
        "embedding_model": routing.embedding.model,
        "mineru_server_url": next((r.value.strip() for r in rows if r.key == "PDF_MINERU_SERVER_URL"), ""),
        "vlm": (routing.vlm.base_url, routing.vlm.api_key, routing.vlm.model),
        "captions": {r.key: r.value for r in rows if r.key.startswith("PICTURE_CAPTION")},
    }


def _build_worker_fns(cfg: dict) -> WorkerFns:  # pragma: no cover
    """Construct the embedding client from one SystemConfig snapshot. Called by
    the fn-builder only when the config changed."""
    from openai import AsyncOpenAI

    embed_client = AsyncOpenAI(
        api_key=cfg["embedding_api_key"], base_url=cfg["embedding_base_url"]
    )
    from backend.app.services.model_routing import Endpoint
    from backend.worker.picture_captions import settings_from_config

    return WorkerFns(
        embed_fn=_make_real_embed_fn(embed_client, cfg["embedding_model"]),
        mineru_server_url=cfg.get("mineru_server_url") or "",
        captions=settings_from_config(cfg.get("captions") or {}, Endpoint(*(cfg.get("vlm") or ("", "", "")))),
    )


async def main() -> None:  # pragma: no cover - entrypoint
    """`python -m backend.worker.main` — run the document worker."""
    # INFO so operational events (config hot-reload, job activity) are visible;
    # the default root level is WARNING, which would hide the reload notice.
    logging.basicConfig(level=logging.INFO)

    from backend.app.database import AsyncSessionLocal
    from backend.worker.gpu_gate import ChatLoadGate, GpuGate

    async with AsyncSessionLocal() as db:
        build_fns = make_fn_builder()
        chat_gate = ChatLoadGate()
        gate = GpuGate(util_fn=_pynvml_util)
        structured = None
        if settings.STRUCTURED_INGEST_ENABLED:
            from backend.worker.structured_ingest import StructuredSettings, sweep_task_root

            structured = StructuredSettings.from_env(settings.DOCUMENTS_STORAGE_ROOT)
            # Job folders a crashed run left behind; a live job is never this old.
            sweep_task_root(structured.task_root, older_than_seconds=structured.lease_seconds)
        else:
            logger.warning("STRUCTURED_INGEST_ENABLED is off: the worker has no jobs to run.")
        await run_forever(
            db, gate, build_fns=build_fns,
            chat_gate=chat_gate, chat_load_fn=_recent_chat_count, structured=structured,
        )


if __name__ == "__main__":  # pragma: no cover
    asyncio.run(main())
