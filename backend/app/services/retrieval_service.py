"""
retrieval_service.py — lab-scoped retrieval for the chat agent.

Hybrid search over course-material chunks: vector ANN + Postgres full-text
(BM25), fused with RRF and reranked.

One invariant is enforced here rather than by callers: tenant isolation. Every
query is filtered by lab (and class, for shared material) in the SQL WHERE
clause, so no prompt can widen it.

Structured exercise search used to live here too, alongside a hint-approval gate
that decided which hints a student was allowed to see. Both went with the
Exercises table.
"""

import logging
import re
import uuid
from dataclasses import dataclass
from typing import Awaitable, Callable, Hashable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models import DocChunk

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


def _scope(stmt, lab_id: uuid.UUID, class_id: uuid.UUID | None = None):
    """Apply the mandatory tenant filter in SQL.

    With `class_id`, the scope is 'this lab OR class-wide shared (lab_id NULL)'
    bounded to the class — shared material is reachable from every lab of the
    class and never leaks across classes. Without it, strict lab-only."""
    if class_id is not None:
        stmt = stmt.where(DocChunk.class_id == class_id)
        stmt = stmt.where((DocChunk.lab_id == lab_id) | (DocChunk.lab_id.is_(None)))
    else:
        stmt = stmt.where(DocChunk.lab_id == lab_id)
    return stmt


async def vector_search(
    db: AsyncSession,
    query_embedding: list[float],
    lab_id: uuid.UUID,
    *,
    k: int = 20,
    class_id: uuid.UUID | None = None,
) -> list[ChunkHit]:
    """Vector ANN recall (cosine), scoped to a lab (+ class-wide material)."""
    distance = DocChunk.embedding.cosine_distance(query_embedding)
    stmt = _scope(
        select(DocChunk.id, DocChunk.content, DocChunk.page_no,
               DocChunk.document_id, DocChunk.context),
        lab_id, class_id,
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
        lab_id, class_id,
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
    Tenant isolation is enforced in the SQL WHERE of each recall, so it
    holds regardless of fusion/rerank order — never a prompt rule.
    """
    # [v8.0] query_embedding None = embedding endpoint degraded → BM25-only recall.
    vector_hits = (
        await vector_search(db, query_embedding, lab_id,
                            k=recall_k, class_id=class_id)
        if query_embedding is not None else []
    )
    bm25_hits = await bm25_search(
        db, query_text, lab_id, k=recall_k, language=language,
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
