"""memory_service.py — what the assistant remembers about a student, per class.

Rules agreed on 2026-11-20:
- One sentence each (200 characters at most), about learning preferences and
  progress only; at most 50 per student and class.
- Kept per student and class: what is learned in one class is not used in another.
- The student sees them, deletes them for good, or turns memory off
  (``users.memory_enabled``); the class teacher sees them; exam mode uses none.

This module stores and lists them. agent/context/memory.py reads them into a research
question and writes them in a background review every few turns.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import delete, func, select

from ..models import Class, StudentMemory

CONTENT_MAX_CHARS = 200
MAX_PER_CLASS = 50


def _row(memory: StudentMemory, class_name: str | None = None) -> dict:
    return {'id': memory.id, 'class_id': memory.class_id, 'class_name': class_name,
            'content': memory.content, 'category': memory.category,
            'source_message_id': memory.source_message_id,
            'created_at': memory.created_at, 'updated_at': memory.updated_at}


async def listing(db, user_id: uuid.UUID, class_id: uuid.UUID | None = None) -> list[dict]:
    """A student's memories, newest first (optionally of one class)."""
    query = (select(StudentMemory, Class.name).join(Class, Class.id == StudentMemory.class_id)
             .where(StudentMemory.user_id == user_id))
    if class_id is not None:
        query = query.where(StudentMemory.class_id == class_id)
    rows = (await db.execute(query.order_by(StudentMemory.updated_at.desc()))).all()
    return [_row(memory, name) for memory, name in rows]


async def remove(db, user_id: uuid.UUID, memory_id: uuid.UUID | None = None,
                 class_id: uuid.UUID | None = None) -> int:
    """Delete for good: one memory, a class's, or all of a student's."""
    query = delete(StudentMemory).where(StudentMemory.user_id == user_id)
    if memory_id is not None:
        query = query.where(StudentMemory.id == memory_id)
    if class_id is not None:
        query = query.where(StudentMemory.class_id == class_id)
    result = await db.execute(query.execution_options(synchronize_session=False))
    return result.rowcount or 0


async def add(db, *, user_id: uuid.UUID, class_id: uuid.UUID, content: str,
              category: str | None = None, source_message_id: uuid.UUID | None = None,
              embedding: list[float] | None = None) -> StudentMemory:
    """Store one memory (for the agent's review). Too long, or one too many for
    this class, is a ValueError; the review keeps the list short itself."""
    text = ' '.join((content or '').split())
    if not text or len(text) > CONTENT_MAX_CHARS:
        raise ValueError('memory_length')
    count = await db.scalar(select(func.count()).select_from(StudentMemory).where(
        StudentMemory.user_id == user_id, StudentMemory.class_id == class_id))
    if count >= MAX_PER_CLASS:
        raise ValueError('memory_full')
    now = datetime.now(timezone.utc)
    memory = StudentMemory(user_id=user_id, class_id=class_id, content=text,
                           category=(category or None) and category[:64],
                           source_message_id=source_message_id, embedding=embedding,
                           created_at=now, updated_at=now)
    db.add(memory)
    await db.flush()
    return memory


async def replace(db, *, user_id: uuid.UUID, memory_id: uuid.UUID, content: str,
                  category: str | None = None, embedding: list[float] | None = None) -> StudentMemory:
    """Rewrite one memory of this student (for the agent's review)."""
    text = ' '.join((content or '').split())
    if not text or len(text) > CONTENT_MAX_CHARS:
        raise ValueError('memory_length')
    memory = await db.get(StudentMemory, memory_id)
    if memory is None or memory.user_id != user_id:
        raise ValueError('memory_not_found')
    memory.content, memory.category = text, (category or None) and category[:64]
    memory.embedding, memory.updated_at = embedding, datetime.now(timezone.utc)
    await db.flush()
    return memory


async def for_recall(db, user_id: uuid.UUID, class_id: uuid.UUID) -> list[dict]:
    """A student's memories of one class with their vectors, for the question context."""
    rows = (await db.scalars(select(StudentMemory).where(
        StudentMemory.user_id == user_id, StudentMemory.class_id == class_id))).all()
    return [{'content': m.content, 'category': m.category,
             'embedding': list(m.embedding) if m.embedding is not None else None} for m in rows]
