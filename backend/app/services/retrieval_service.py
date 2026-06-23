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

import uuid
from dataclasses import dataclass
from typing import Awaitable, Callable, Hashable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models import Audience, DocChunk, Exercise

# A reranker: scores each candidate document for the query (higher = better).
RerankFn = Callable[[str, list[str]], Awaitable[list[float]]]


@dataclass(frozen=True)
class ExerciseHit:
    """A retrieval result for an exercise. Deliberately has no `solution`."""
    number: str
    statement: str
    hints: str | None


@dataclass(frozen=True)
class ChunkHit:
    """A RAG retrieval result, carrying its source for citations."""
    content: str
    page_no: int | None
    document_id: uuid.UUID
    id: uuid.UUID | None = None


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
) -> list[ChunkHit]:
    """Reorder hits by reranker score (stable on ties) and take the top-k."""
    if not hits:
        return []
    scores = await rerank_fn(query, [h.content for h in hits])
    order = sorted(range(len(hits)), key=lambda i: scores[i], reverse=True)
    return [hits[i] for i in order[:top_k]]


async def search_exercises(
    db: AsyncSession,
    lab_id: uuid.UUID,
    limit: int = 10,
) -> list[ExerciseHit]:
    """Return exercises for a lab as solution-free hits."""
    stmt = (
        select(Exercise.number, Exercise.statement, Exercise.hints)
        .where(Exercise.lab_id == lab_id)
        .order_by(Exercise.number)
        .limit(limit)
    )
    rows = (await db.execute(stmt)).all()
    return [ExerciseHit(number=r.number, statement=r.statement, hints=r.hints) for r in rows]


def _scope(stmt, lab_id: uuid.UUID, audience: Audience | None):
    """Apply the mandatory tenant filter (+ student audience filter) in SQL."""
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
) -> list[ChunkHit]:
    """Vector ANN recall (cosine), scoped to a lab (+ audience)."""
    distance = DocChunk.embedding.cosine_distance(query_embedding)
    stmt = _scope(
        select(DocChunk.id, DocChunk.content, DocChunk.page_no, DocChunk.document_id),
        lab_id, audience,
    ).order_by(distance).limit(k)
    rows = (await db.execute(stmt)).all()
    return [
        ChunkHit(id=r.id, content=r.content, page_no=r.page_no, document_id=r.document_id)
        for r in rows
    ]


async def bm25_search(
    db: AsyncSession,
    query_text: str,
    lab_id: uuid.UUID,
    *,
    audience: Audience | None = None,
    k: int = 20,
) -> list[ChunkHit]:
    """BM25-style full-text recall over the `tsv` column (Postgres only)."""
    from sqlalchemy import func

    tsquery = func.plainto_tsquery("simple", query_text)
    rank = func.ts_rank(DocChunk.tsv, tsquery)
    stmt = _scope(
        select(DocChunk.id, DocChunk.content, DocChunk.page_no, DocChunk.document_id),
        lab_id, audience,
    ).where(DocChunk.tsv.op("@@")(tsquery)).order_by(rank.desc()).limit(k)
    rows = (await db.execute(stmt)).all()
    return [
        ChunkHit(id=r.id, content=r.content, page_no=r.page_no, document_id=r.document_id)
        for r in rows
    ]


async def hybrid_search(
    db: AsyncSession,
    query_text: str,
    query_embedding: list[float],
    lab_id: uuid.UUID,
    *,
    audience: Audience | None = None,
    rerank_fn: RerankFn | None = None,
    recall_k: int = 20,
    top_k: int = 5,
) -> list[ChunkHit]:
    """Vector + BM25 recall → RRF fusion → optional rerank → top-k.

    Tenant + audience isolation is enforced in the SQL WHERE of each recall, so it
    holds regardless of fusion/rerank order — never a prompt rule.
    """
    vector_hits = await vector_search(db, query_embedding, lab_id, audience=audience, k=recall_k)
    bm25_hits = await bm25_search(db, query_text, lab_id, audience=audience, k=recall_k)

    by_id: dict[uuid.UUID, ChunkHit] = {}
    for hit in [*vector_hits, *bm25_hits]:
        by_id.setdefault(hit.id, hit)

    fused = reciprocal_rank_fusion(
        [[h.id for h in vector_hits], [h.id for h in bm25_hits]]
    )
    fused_hits = [by_id[chunk_id] for chunk_id, _ in fused if chunk_id in by_id]

    if rerank_fn is not None:
        return await apply_rerank(query_text, fused_hits, rerank_fn, top_k)
    return fused_hits[:top_k]


async def rag_search(
    db: AsyncSession,
    query_embedding: list[float],
    lab_id: uuid.UUID,
    k: int = 5,
) -> list[ChunkHit]:
    """Vector-only RAG search, scoped to a lab. (Kept for callers not yet on hybrid.)"""
    return await vector_search(db, query_embedding, lab_id, k=k)
