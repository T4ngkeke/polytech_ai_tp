"""
retrieval_service.py — [v7] lab-scoped retrieval for the chat agent.

Two retrieval paths feed the LangGraph agent:
  * search_exercises — structured "agentic search" over Exercises.
  * (rag vector search — added next.)

Two invariants enforced here, not by callers:
  * Tenant isolation: every query is filtered by lab_id (security boundary).
  * No solution exists: v7.1 drops the `Exercise.solution` column entirely — only
    student-safe statements are stored, so there is nothing solution-shaped to leak.
"""

import logging
import re
import uuid
from dataclasses import dataclass
from typing import Awaitable, Callable, Hashable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models import Audience, DocChunk, Exercise, HintStatus

logger = logging.getLogger(__name__)

# A reranker: scores each candidate document for the query (higher = better).
RerankFn = Callable[[str, list[str]], Awaitable[list[float]]]

# [v7.3] Language → Postgres text-search config. Ingest (tsvector) and query
# (tsquery) sides MUST use the same config or matching silently dies.
_TS_CONFIGS = {"fr": "french", "en": "english"}
_WORD_RE = re.compile(r"\w+", re.UNICODE)


def ts_config_for(language: str | None) -> str:
    """Map a document/query language code to a text-search config."""
    return _TS_CONFIGS.get((language or "").lower(), "simple")


@dataclass(frozen=True)
class ExerciseHit:
    """A retrieval result for an exercise. Deliberately has no `solution`."""
    number: str
    statement: str
    hints: list[str] | None


@dataclass(frozen=True)
class ChunkHit:
    """A RAG retrieval result, carrying its source for citations."""
    content: str
    page_no: int | None
    document_id: uuid.UUID
    id: uuid.UUID | None = None
    # [v7.3] The Contextual-Retrieval context — recall matched on the augmented
    # text, so the reranker must score it too (citations still show content).
    context: str | None = None


def _augmented_text(hit: ChunkHit) -> str:
    """Text the reranker scores: same augmentation the indexes were built on."""
    return f"{hit.context}\n{hit.content}" if hit.context else hit.content


def reciprocal_rank_fusion(
    rankings: list[list[Hashable]],
    k: int = 60,
) -> list[tuple[Hashable, float]]:
    """Fuse several ranked id-lists into one, by Reciprocal Rank Fusion.

    Each list is best-first. An item's score is sum(1 / (k + rank)) across the
    lists it appears in, so items ranked high in multiple lists rise to the top.
    Returns (id, score) pairs sorted best-first.
    """
    scores: dict[Hashable, float] = {}
    for ranking in rankings:
        for rank, item in enumerate(ranking):
            scores[item] = scores.get(item, 0.0) + 1.0 / (k + rank + 1)
    return sorted(scores.items(), key=lambda kv: kv[1], reverse=True)


async def apply_rerank(
    query: str,
    hits: list[ChunkHit],
    rerank_fn: RerankFn,
    top_k: int,
    score_threshold: float | None = None,
) -> tuple[list[ChunkHit], bool]:
    """Reorder hits by reranker score, gate on an absolute threshold, take top-k.

    Returns ``(hits, all_filtered)``. [v7.3] `score_threshold` turns the
    reranker's relative ordering into an injection gate: nearest-k is not
    relevant-k, and a below-threshold chunk must not reach the prompt (false
    grounding). ``all_filtered=True`` signals the zero-context disclaimer
    branch. Ships with the threshold unset (off) until calibrated.

    [v7.2] Reranking is a best-effort enhancement, never a hard dependency: any
    failure logs a warning and degrades to fusion-only ordering (no gate — no
    scores means we never filter blind).
    [v7.3] Scoring reads the augmented text (context + content) — the same text
    recall matched on, so context-poor slide chunks aren't vetoed at the last step.
    """
    if not hits:
        return [], False
    try:
        scores = await rerank_fn(query, [_augmented_text(h) for h in hits])
    except Exception as exc:
        logger.warning(
            "Rerank failed (%s); degrading to fusion-only ordering.", repr(exc)
        )
        return hits[:top_k], False
    order = sorted(range(len(hits)), key=lambda i: scores[i], reverse=True)
    if score_threshold is not None:
        kept = [i for i in order if scores[i] >= score_threshold]
        if not kept:
            return [], True
        return [hits[i] for i in kept[:top_k]], False
    return [hits[i] for i in order[:top_k]], False


async def search_exercises(
    db: AsyncSession,
    lab_id: uuid.UUID,
    limit: int = 10,
    number: int | None = None,
    audience: Audience | None = None,
    include_draft_hints: bool = False,
) -> list[ExerciseHit]:
    """Return exercises for a lab as solution-free hits.

    [v7.2] When ``number`` is given (the canonical exercise number resolved by the
    router), results are filtered to ``number_normalized == number`` so a "how do
    I do exercise II?" query surfaces only that exercise, not the whole lab. When
    ``audience`` is given (``student`` for chat), teacher-audience exercises are
    excluded in the WHERE clause — students never retrieve teacher material.

    [v8.0 §10] Red line: hints reach students ONLY after approval. This is the
    single injection gate — a hit's ``hints`` is populated only when the
    exercise is ``approved`` (or ``pending_review`` when ``include_draft_hints``
    is set, which only the teacher's test-drive session does). Statements are
    always surfaced; the answer is never stored here to begin with.
    """
    stmt = select(
        Exercise.number, Exercise.statement, Exercise.hints, Exercise.hint_status,
    ).where(Exercise.lab_id == lab_id)
    if number is not None:
        stmt = stmt.where(Exercise.number_normalized == number)
    if audience is not None:
        stmt = stmt.where(Exercise.audience == audience)
    stmt = stmt.order_by(Exercise.number).limit(limit)
    rows = (await db.execute(stmt)).all()

    visible = {HintStatus.approved}
    if include_draft_hints:
        visible.add(HintStatus.pending_review)
    return [
        ExerciseHit(
            number=r.number,
            statement=r.statement,
            hints=r.hints if r.hint_status in visible else None,
        )
        for r in rows
    ]


async def list_exercise_numbers(
    db: AsyncSession,
    lab_id: uuid.UUID,
    *,
    audience: Audience | None = None,
) -> list[tuple[str, int | None]]:
    """Every exercise number in a lab as ``(number_raw, number_normalized)``,
    ordered by the normalized number. [v8.0] One cheap SELECT powering three
    call sites: the clarify question's number list, "what comes after 3.3"
    navigation, and router DB validation (does the resolved number exist?).
    Audience-scoped in SQL — students never see teacher-audience numbers."""
    stmt = select(Exercise.number, Exercise.number_normalized).where(
        Exercise.lab_id == lab_id
    )
    if audience is not None:
        stmt = stmt.where(Exercise.audience == audience)
    stmt = stmt.order_by(Exercise.number_normalized)
    rows = (await db.execute(stmt)).all()
    return [(r.number, r.number_normalized) for r in rows]


def _scope(stmt, lab_id: uuid.UUID, audience: Audience | None,
           class_id: uuid.UUID | None = None):
    """Apply the mandatory tenant filter (+ student audience filter) in SQL.

    [v8.0] With `class_id`, the scope is 'this lab OR class-wide shared
    (lab_id NULL)' bounded to the class — shared CM is reachable from every lab
    of the class and never leaks across classes. Without it (legacy callers) it
    stays strict lab-only."""
    if class_id is not None:
        stmt = stmt.where(DocChunk.class_id == class_id)
        stmt = stmt.where((DocChunk.lab_id == lab_id) | (DocChunk.lab_id.is_(None)))
    else:
        stmt = stmt.where(DocChunk.lab_id == lab_id)
    if audience is not None:
        # Students never retrieve teacher-audience material — enforced in WHERE.
        stmt = stmt.where(DocChunk.audience == audience)
    return stmt


async def vector_search(
    db: AsyncSession,
    query_embedding: list[float],
    lab_id: uuid.UUID,
    *,
    audience: Audience | None = None,
    k: int = 20,
    class_id: uuid.UUID | None = None,
) -> list[ChunkHit]:
    """Vector ANN recall (cosine), scoped to a lab (+ audience, + class-wide CM)."""
    distance = DocChunk.embedding.cosine_distance(query_embedding)
    stmt = _scope(
        select(DocChunk.id, DocChunk.content, DocChunk.page_no,
               DocChunk.document_id, DocChunk.context),
        lab_id, audience, class_id,
    ).order_by(distance).limit(k)
    rows = (await db.execute(stmt)).all()
    return [
        ChunkHit(id=r.id, content=r.content, page_no=r.page_no,
                 document_id=r.document_id, context=r.context)
        for r in rows
    ]


async def bm25_search(
    db: AsyncSession,
    query_text: str,
    lab_id: uuid.UUID,
    *,
    audience: Audience | None = None,
    k: int = 20,
    language: str = "fr",
    class_id: uuid.UUID | None = None,
) -> list[ChunkHit]:
    """BM25-style full-text recall over the `tsv` column (Postgres only).

    [v7.3] OR semantics: `plainto_tsquery` ANDs every word, so one extra word
    in a natural-language question zeroed the recall — the BM25 leg was
    silently dead. Content words are ORed; ts_rank rewards multi-matches.
    The language config enables stemming ('fonctions' matches 'fonction').
    """
    from sqlalchemy import func

    tokens = _WORD_RE.findall(query_text)
    if not tokens:
        return []
    tsquery = func.to_tsquery(ts_config_for(language), " | ".join(tokens))
    rank = func.ts_rank(DocChunk.tsv, tsquery)
    stmt = _scope(
        select(DocChunk.id, DocChunk.content, DocChunk.page_no,
               DocChunk.document_id, DocChunk.context),
        lab_id, audience, class_id,
    ).where(DocChunk.tsv.op("@@")(tsquery)).order_by(rank.desc()).limit(k)
    rows = (await db.execute(stmt)).all()
    return [
        ChunkHit(id=r.id, content=r.content, page_no=r.page_no,
                 document_id=r.document_id, context=r.context)
        for r in rows
    ]


async def hybrid_search(
    db: AsyncSession,
    query_text: str,
    query_embedding: list[float] | None,
    lab_id: uuid.UUID,
    *,
    audience: Audience | None = None,
    rerank_fn: RerankFn | None = None,
    recall_k: int = 20,
    top_k: int = 5,
    score_threshold: float | None = None,
    language: str = "fr",
    class_id: uuid.UUID | None = None,
) -> tuple[list[ChunkHit], bool]:
    """Vector + BM25 recall → RRF fusion → optional rerank + threshold → top-k.

    Returns ``(hits, all_filtered)`` — the flag signals that the [v7.3] score
    threshold rejected everything (→ zero-context disclaimer branch).
    Tenant + audience isolation is enforced in the SQL WHERE of each recall, so it
    holds regardless of fusion/rerank order — never a prompt rule.
    """
    # [v8.0] query_embedding None = embedding endpoint degraded → BM25-only recall.
    vector_hits = (
        await vector_search(db, query_embedding, lab_id, audience=audience,
                            k=recall_k, class_id=class_id)
        if query_embedding is not None else []
    )
    bm25_hits = await bm25_search(
        db, query_text, lab_id, audience=audience, k=recall_k, language=language,
        class_id=class_id,
    )

    by_id: dict[uuid.UUID, ChunkHit] = {}
    for hit in [*vector_hits, *bm25_hits]:
        by_id.setdefault(hit.id, hit)

    fused = reciprocal_rank_fusion(
        [[h.id for h in vector_hits], [h.id for h in bm25_hits]]
    )
    fused_hits = [by_id[chunk_id] for chunk_id, _ in fused if chunk_id in by_id]

    if rerank_fn is not None:
        return await apply_rerank(
            query_text, fused_hits, rerank_fn, top_k, score_threshold=score_threshold,
        )
    return fused_hits[:top_k], False


async def rag_search(
    db: AsyncSession,
    query_embedding: list[float],
    lab_id: uuid.UUID,
    k: int = 5,
) -> list[ChunkHit]:
    """Vector-only RAG search, scoped to a lab. (Kept for callers not yet on hybrid.)"""
    return await vector_search(db, query_embedding, lab_id, k=k)
