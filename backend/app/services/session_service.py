"""
services/session_service.py — Session management business logic.

Key audit rule: Students CANNOT delete sessions (audit integrity).
Session soft-delete is only available to admin/teacher via their routers.
"""

import uuid

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.app.models import Session


async def create_session(
    db: AsyncSession,
    user_id: uuid.UUID,
    lab_id: uuid.UUID,
    title: str | None = None,
    is_test: bool = False,
) -> Session:
    """Create a new chat session scoped to a specific lab.

    [v8.0 §11] `is_test` marks a teacher test-drive session — excluded from
    analytics/LearnerProfile and allowed to preview pending_review draft hints."""
    session = Session(user_id=user_id, lab_id=lab_id, title=title, is_test=is_test)
    db.add(session)
    await db.flush()
    await db.refresh(session)
    return session


async def get_session_by_id(db: AsyncSession, session_id: uuid.UUID) -> Session:
    """Fetch a session by ID. Raises 404 if not found."""
    result = await db.execute(select(Session).where(Session.id == session_id))
    session = result.scalar_one_or_none()
    if session is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    return session


async def get_session_with_messages(db: AsyncSession, session_id: uuid.UUID) -> Session:
    """Fetch a session with eagerly-loaded messages."""
    result = await db.execute(
        select(Session)
        .where(Session.id == session_id)
        .options(selectinload(Session.messages))
    )
    session = result.scalar_one_or_none()
    if session is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    return session


async def list_sessions_for_student(
    db: AsyncSession,
    user_id: uuid.UUID,
    lab_id: uuid.UUID | None = None,
) -> list[Session]:
    """List all non-deleted sessions for a student, with optional lab filter."""
    query = (
        select(Session)
        .where(Session.user_id == user_id, Session.is_deleted.is_(False))
        .order_by(Session.created_at.desc())
    )
    if lab_id is not None:
        query = query.where(Session.lab_id == lab_id)
    result = await db.execute(query)
    return list(result.scalars().all())


async def rename_session(db: AsyncSession, session: Session, title: str) -> Session:
    """Rename a session (cosmetic only — does not affect audit)."""
    session.title = title
    db.add(session)
    await db.flush()
    await db.refresh(session)
    return session


async def soft_delete_session(db: AsyncSession, session: Session) -> None:
    """
    Soft-delete a session. Only callable by teacher/admin routers.
    Student router does NOT expose this function.
    """
    session.is_deleted = True
    db.add(session)
    await db.flush()
