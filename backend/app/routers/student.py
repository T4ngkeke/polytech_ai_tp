"""
routers/student.py — Student session management endpoints.

Contract endpoints:
  POST /api/student/sessions              — create a new chat session
  GET  /api/student/sessions              — list own active sessions
  GET  /api/student/sessions/{session_id} — fetch message history (IDOR check)
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.database import get_db
from app.models import Message, Session, User
from app.schemas import MessageResponse, SessionResponse

router = APIRouter(prefix="/api/student", tags=["student"])


# ---------------------------------------------------------------------------
# POST /api/student/sessions
# ---------------------------------------------------------------------------


@router.post("/sessions", response_model=SessionResponse, status_code=201)
async def create_session(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> Session:
    """Create a new chat session owned by the authenticated user."""
    session = Session(user_id=current_user.id)
    db.add(session)
    await db.commit()
    await db.refresh(session)
    return session


# ---------------------------------------------------------------------------
# GET /api/student/sessions
# ---------------------------------------------------------------------------


@router.get("/sessions", response_model=list[SessionResponse])
async def list_sessions(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> list[Session]:
    """
    List the authenticated user's own active (non-deleted) sessions,
    ordered from newest to oldest.
    """
    result = await db.execute(
        select(Session)
        .where(Session.user_id == current_user.id, Session.is_deleted.is_(False))
        .order_by(Session.created_at.desc())
    )
    return list(result.scalars().all())


# ---------------------------------------------------------------------------
# GET /api/student/sessions/{session_id}
# ---------------------------------------------------------------------------


@router.get("/sessions/{session_id}", response_model=list[MessageResponse])
async def get_session_history(
    session_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> list[Message]:
    """
    Return the message history for a session.

    IDOR protection: raises 403 if the session does not belong to the
    authenticated user, and 404 if the session is soft-deleted or missing —
    never leak that another user's session_id exists.
    """
    # Fetch the session.
    result = await db.execute(select(Session).where(Session.id == session_id))
    session: Session | None = result.scalar_one_or_none()

    # 404 for missing or soft-deleted sessions.
    if session is None or session.is_deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Session not found."
        )

    # IDOR check: ownership must match the token's user.
    if session.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied.",
        )

    # Return messages ordered chronologically.
    messages_result = await db.execute(
        select(Message)
        .where(Message.session_id == session_id)
        .order_by(Message.created_at)
    )
    return list(messages_result.scalars().all())
