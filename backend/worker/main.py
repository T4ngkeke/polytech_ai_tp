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
import json
import logging
import re
from typing import Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models import IngestionJob, JobStatus, JobType
from backend.worker.chunking import ClassifyLinesFn
from backend.worker.ingest import ContextFn, EmbedFn, ingest_document
from backend.worker.queue import claim_next_job

logger = logging.getLogger(__name__)

# How long to sleep when the queue is empty vs. when the GPU gate is closed.
IDLE_SLEEP_SECONDS = 5.0
GATE_SLEEP_SECONDS = 10.0


async def _mark_job(
    db: AsyncSession, job_id, status: JobStatus, error_message: str | None = None
) -> None:
    """None-safe terminal status write. The job row may have been reclaimed by a
    stale-lock sweep or cascade-deleted with its document while a long ingest ran;
    if it's gone, skip the update rather than crash the worker loop."""
    job = await db.get(IngestionJob, job_id)
    if job is None:
        return
    job.status = status
    job.error_message = error_message


async def process_one(
    db: AsyncSession,
    *,
    embed_fn: EmbedFn,
    context_fn: ContextFn | None = None,
    classify_fn: ClassifyLinesFn | None = None,
    run_hint_job: Callable[[AsyncSession, IngestionJob], Awaitable[None]] | None = None,
) -> bool:
    """
    Claim and process one queued job.

    Returns True if a job was processed (success or failure), False if the
    queue was empty.

    [v8.0] Dispatches on `job.job_type`: `ingest` runs the ingestion pipeline;
    `hint_generate` runs the injected `run_hint_job` (teacher-triggered hint
    generation). Both mark the job done/failed the same way.
    """
    job = await claim_next_job(db)
    if job is None:
        return False

    job_id = job.id

    try:
        if job.job_type == JobType.hint_generate:
            if run_hint_job is None:
                raise RuntimeError("no hint runner configured for hint_generate job")
            await run_hint_job(db, job)
        else:
            await ingest_document(
                db, job.document_id,
                embed_fn=embed_fn, context_fn=context_fn, classify_fn=classify_fn,
            )
        await _mark_job(db, job_id, JobStatus.done)
    except Exception as exc:  # ingest/hint already recorded the failure on its row
        await db.rollback()
        await _mark_job(db, job_id, JobStatus.failed, str(exc))

    await db.commit()
    return True


async def run_tick(
    db: AsyncSession,
    gate,
    *,
    embed_fn: EmbedFn,
    context_fn: ContextFn | None = None,
    classify_fn: ClassifyLinesFn | None = None,
    sleep_fn: Callable[[float], Awaitable[None]] = asyncio.sleep,
    idle_seconds: float = IDLE_SLEEP_SECONDS,
    gate_seconds: float = GATE_SLEEP_SECONDS,
    chat_gate=None,
    chat_load_fn: Callable[[AsyncSession], Awaitable[float]] | None = None,
    run_hint_job: Callable[[AsyncSession, IngestionJob], Awaitable[None]] | None = None,
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
        db, embed_fn=embed_fn, context_fn=context_fn, classify_fn=classify_fn,
        run_hint_job=run_hint_job,
    )
    if not processed:
        await sleep_fn(idle_seconds)
    return processed


async def run_forever(
    db: AsyncSession,
    gate,
    *,
    embed_fn: EmbedFn,
    context_fn: ContextFn | None = None,
    classify_fn: ClassifyLinesFn | None = None,
    chat_gate=None,
    chat_load_fn: Callable[[AsyncSession], Awaitable[float]] | None = None,
    run_hint_job: Callable[[AsyncSession, IngestionJob], Awaitable[None]] | None = None,
) -> None:  # pragma: no cover - thin infinite-loop glue
    """Run the ingestion worker loop forever."""
    while True:
        await run_tick(
            db, gate, embed_fn=embed_fn, context_fn=context_fn,
            classify_fn=classify_fn,
            chat_gate=chat_gate, chat_load_fn=chat_load_fn,
            run_hint_job=run_hint_job,
        )


# ---------------------------------------------------------------------------
# Production glue (not unit-tested: hits the engine / GPU / DB)
# ---------------------------------------------------------------------------

# [v8.0] Schema the per-page line classifier is constrained to: for each
# structural line, its number (code-owned), a verbatim prefix, and its role.
# The model NEVER emits an exercise number — only which line carries the label.
_CLASSIFY_SCHEMA = {
    "type": "object",
    "properties": {
        "lines": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "line_no": {"type": "integer"},
                    "prefix": {"type": "string"},
                    "kind": {
                        "type": "string",
                        "enum": [
                            "exercise_heading",
                            "section_heading",
                            "subquestion",
                            "toc_entry",
                        ],
                    },
                },
                "required": ["line_no", "prefix", "kind"],
            },
        }
    },
    "required": ["lines"],
}

_CLASSIFY_SYSTEM = (
    "You segment a French/English programming problem sheet. Each input line is "
    "prefixed with its line number ('12| ...'). Return, for every STRUCTURAL line, "
    "an object {line_no, prefix, kind} where prefix is the first few words of that "
    "line copied verbatim and kind is one of:\n"
    "- exercise_heading: a top-level exercise title ('Exercice 3', 'Problème 2', 'Q4').\n"
    "- section_heading: a part title ('I - Codage', 'Partie 2').\n"
    "- subquestion: a sub-item inside an exercise ('(1).', 'a)', '1.a').\n"
    "- toc_entry: a table-of-contents line — the 'Contenu' / 'Sommaire' / 'Table "
    "des matières' heading AND every entry listed under it (usually near the top of "
    "page 1), EVEN when an entry reads like a section title ('I - ...', 'II - ...'). "
    "The SAME title text often appears twice: once in the table of contents "
    "(toc_entry) and again later where the actual section begins (section_heading).\n"
    "Do NOT classify the document title line — a line like 'TD 1 - ...', 'TP 2 - ...', "
    "'CM 3 - ...' naming the whole sheet — as any heading; leave it out.\n"
    "Classify every candidate structural line; leave ordinary prose out. Never "
    "invent an exercise number — only report which line holds a printed label. "
    "The rolling context tells you which exercise/section is currently open, so a "
    "line opening a page is a subquestion, not a new heading, when it continues one. "
    "Respond with JSON only, matching the provided schema."
)


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


def _parse_classified_lines(content: str | None) -> list[dict]:
    """Parse the per-page classifier response into a list of {line_no, prefix,
    kind} dicts.

    Robust to engines that don't *guarantee* structured output: empty /
    whitespace, code-fenced JSON, or JSON wrapped in prose all degrade to ``[]``
    rather than crashing the worker (an empty page result → that page found no
    structural lines). Non-dict items are dropped; per-field validation happens
    downstream in `validate_page_classification`.
    """
    if not content or not content.strip():
        return []
    text = content.strip()

    # Strip a ```json ... ``` / ``` ... ``` code fence if the model added one.
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z0-9]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text).strip()

    # If prose surrounds the JSON, slice to the first {...} object.
    if not text.startswith("{"):
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            return []
        text = text[start:end + 1]

    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        logger.warning("Line classifier returned non-JSON output; treating page as empty.")
        return []
    if not isinstance(data, dict):
        return []
    lines = data.get("lines", [])
    if not isinstance(lines, list):
        return []
    return [item for item in lines if isinstance(item, dict)]


def _make_real_classify_fn(client, model: str) -> ClassifyLinesFn:  # pragma: no cover
    """[v8.0] The per-page line classifier: given a line-numbered page and the
    rolling context, the LLM labels each structural line's role by line number
    (it never emits an exercise number). Boundary reconciliation, slicing, and
    numbering all stay deterministic downstream."""

    async def classify(numbered_page: str, context: str) -> list[dict]:
        user = (f"Rolling context:\n{context}\n\n" if context else "") + (
            "Classify the structural lines of this page:\n" + numbered_page
        )
        resp = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": _CLASSIFY_SYSTEM},
                {"role": "user", "content": user},
            ],
            response_format={
                "type": "json_schema",
                "json_schema": {"name": "lines", "schema": _CLASSIFY_SCHEMA},
            },
        )
        return _parse_classified_lines(resp.choices[0].message.content)
    return classify


def _make_real_context_fn(client, model: str) -> ContextFn:  # pragma: no cover
    """Contextual Retrieval: situate a chunk within its section/slide.

    [v7.2] The ``scope`` is the chunk's own section/page, not the whole document,
    so the context is accurate on long docs and fits a small model. A modest cap
    guards against a pathologically large single section.
    """
    _PROMPT = (
        "Here is a section of a course document:\n<section>\n{scope}\n</section>\n\n"
        "Here is a chunk from it:\n<chunk>\n{chunk}\n</chunk>\n\n"
        "Give a short, standalone sentence situating this chunk within the section "
        "(topic) to improve retrieval. Answer with the sentence only."
    )

    async def context(scope: str, chunk_text: str) -> str:
        resp = await client.chat.completions.create(
            model=model,
            messages=[{
                "role": "user",
                "content": _PROMPT.format(scope=scope[:4000], chunk=chunk_text),
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
    """Resolve the worker's model routing from SystemConfigs.

    [v7.2] The off-peak worker uses the dedicated ``INGEST_*`` endpoint/model when
    set (a cheap 30B), falling back to the main LLM — so per-chunk Contextual
    Retrieval doesn't consume the expensive chat model's RPM pool. Embedding uses
    its own resolved endpoint too.
    """
    from sqlalchemy import select

    from backend.app.models import SystemConfig
    from backend.app.services.model_routing import resolve_model_routing

    rows = (await db.execute(select(SystemConfig))).scalars().all()
    routing = resolve_model_routing({r.key: r.value for r in rows})
    return {
        # Ingestion endpoint (context generation + exercise extraction).
        "base_url": routing.ingest.base_url,
        "api_key": routing.ingest.api_key,
        "model": routing.ingest.model,
        # Embedding endpoint (independently configurable).
        "embedding_base_url": routing.embedding.base_url,
        "embedding_api_key": routing.embedding.api_key,
        "embedding_model": routing.embedding.model,
        # Hint endpoint (the big model, off-peak) for teacher-triggered generation.
        "hint_base_url": routing.hint.base_url,
        "hint_api_key": routing.hint.api_key,
        "hint_model": routing.hint.model,
    }


def _make_real_hint_fn(client, model: str):  # pragma: no cover
    """A hint-model call: assembled prompt → raw reply. One helper serves the
    classify / generate / judge steps of the hint workflow (thinking left on)."""
    async def hint(prompt: str) -> str:
        resp = await client.chat.completions.create(
            model=model, messages=[{"role": "user", "content": prompt}],
        )
        return resp.choices[0].message.content or ""
    return hint


async def main() -> None:  # pragma: no cover - entrypoint
    """`python -m backend.worker.main` — run the ingestion worker."""
    from openai import AsyncOpenAI

    from backend.app.database import AsyncSessionLocal
    from backend.worker.gpu_gate import ChatLoadGate, GpuGate

    async with AsyncSessionLocal() as db:
        cfg = await _load_config(db)
        # Ingestion client (may be a separate cheap engine from chat); embedding
        # may live on its own endpoint too.
        ingest_client = AsyncOpenAI(api_key=cfg["api_key"], base_url=cfg["base_url"])
        embed_client = AsyncOpenAI(
            api_key=cfg["embedding_api_key"], base_url=cfg["embedding_base_url"]
        )
        embed_fn = _make_real_embed_fn(embed_client, cfg["embedding_model"])
        classify_fn = _make_real_classify_fn(ingest_client, cfg["model"])
        context_fn = _make_real_context_fn(ingest_client, cfg["model"])

        # Hint generation (teacher-triggered) runs on the big model, off-peak.
        from backend.worker.hint_jobs import run_hint_job as _run_hint_job
        from backend.worker.hints import generate_hints_for_exercise
        hint_client = AsyncOpenAI(api_key=cfg["hint_api_key"], base_url=cfg["hint_base_url"])
        hint_fn = _make_real_hint_fn(hint_client, cfg["hint_model"])

        async def generate_fn(statement: str, answer_text: str):
            return await generate_hints_for_exercise(
                statement, answer_text,
                classify_fn=hint_fn, generate_fn=hint_fn, judge_fn=hint_fn,
            )

        async def run_hint_job(db_, job):
            await _run_hint_job(db_, job, generate_fn=generate_fn)

        # Primary gate: live chat load (engine-agnostic). Optional: GPU idle
        # (no-ops on a remote API where _pynvml_util reads idle).
        chat_gate = ChatLoadGate()
        gate = GpuGate(util_fn=_pynvml_util)
        await run_forever(
            db, gate, embed_fn=embed_fn, context_fn=context_fn,
            classify_fn=classify_fn,
            chat_gate=chat_gate, chat_load_fn=_recent_chat_count,
            run_hint_job=run_hint_job,
        )


if __name__ == "__main__":  # pragma: no cover
    asyncio.run(main())
