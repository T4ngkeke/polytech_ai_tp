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

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models import DocChunk, Exercise


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


async def rag_search(
    db: AsyncSession,
    query_embedding: list[float],
    lab_id: uuid.UUID,
    k: int = 5,
) -> list[ChunkHit]:
    """Return the k nearest document chunks (cosine), scoped to a lab."""
    distance = DocChunk.embedding.cosine_distance(query_embedding)
    stmt = (
        select(DocChunk.content, DocChunk.page_no, DocChunk.document_id)
        .where(DocChunk.lab_id == lab_id)
        .order_by(distance)
        .limit(k)
    )
    rows = (await db.execute(stmt)).all()
    return [
        ChunkHit(content=r.content, page_no=r.page_no, document_id=r.document_id)
        for r in rows
    ]
