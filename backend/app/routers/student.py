"""
routers/student.py — Student session management endpoints.

Endpoints
---------
POST /api/student/sessions                   →  Create a new chat session.
GET  /api/student/sessions                   →  List own active sessions.
GET  /api/student/sessions/{session_id}      →  Fetch message history (IDOR check required).
"""

from datetime import date
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.app.auth import get_current_user
from backend.app.database import get_db
from backend.app.models import Session, User, UsageStat
from backend.app.schemas import (
    SessionCreateRequest,
    SessionResponse,
    SessionWithMessagesResponse,
)

router = APIRouter(prefix="/api/student", tags=["student"])


# ===================================================================
# POST /api/student/sessions
# ===================================================================


@router.post("/sessions", response_model=SessionResponse, status_code=201)
async def create_session(
    body: SessionCreateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> SessionResponse:
    """Create a new chat session for the authenticated user."""
    session = Session(
        user_id=current_user.id,
        title=body.title,
    )
    db.add(session)
    await db.flush()
    await db.refresh(session)
    return SessionResponse.model_validate(session)


# ===================================================================
# GET /api/student/usage
# ===================================================================

@router.get("/usage")
async def get_usage(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return the current user's daily token quota limit and used amount."""
    today = date.today()
    result = await db.execute(
        select(UsageStat).where(
            UsageStat.user_id == current_user.id,
            UsageStat.date == today,
        )
    )
    usage = result.scalar_one_or_none()
    
    return {
        "used": usage.tokens_used if usage else 0,
        "limit": current_user.daily_token_quota
    }


# ===================================================================
# GET /api/student/sessions
# ===================================================================


@router.get("/sessions", response_model=list[SessionResponse])
async def list_sessions(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[SessionResponse]:
    """Return all non-deleted sessions belonging to the authenticated user."""
    result = await db.execute(
        select(Session)
        .where(
            Session.user_id == current_user.id,
            Session.is_deleted.is_(False),
        )
        .order_by(Session.created_at.desc())
    )
    sessions = result.scalars().all()
    return [SessionResponse.model_validate(s) for s in sessions]


# ===================================================================
# GET /api/student/sessions/{session_id}
# ===================================================================


@router.get(
    "/sessions/{session_id}",
    response_model=SessionWithMessagesResponse,
)
async def get_session(
    session_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> SessionWithMessagesResponse:
    """
    Return the message history for a session.

    IDOR check: verify that session.user_id == current_user.id before
    returning any data. Returns 403 if the session belongs to another user.
    """
    result = await db.execute(
        select(Session)
        .where(Session.id == session_id)
        .options(selectinload(Session.messages))
    )
    session = result.scalar_one_or_none()

    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found",
        )

    if session.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to view this session",
        )

    return SessionWithMessagesResponse.model_validate(session)
