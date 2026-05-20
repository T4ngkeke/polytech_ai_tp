"""
routers/student.py — Student session management endpoints.

Contract endpoints:
  POST /api/student/sessions              — create a new chat session
  GET  /api/student/sessions              — list own active sessions
  GET  /api/student/sessions/{session_id} — fetch message history (IDOR check)
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.database import get_db
from app.models import User
from app.schemas import MessageResponse, SessionResponse

router = APIRouter(prefix="/api/student", tags=["student"])


@router.post("/sessions", response_model=SessionResponse, status_code=201)
async def create_session(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> SessionResponse:
    """Create a new chat session for the authenticated student."""
    return {"status": "not implemented", "endpoint": "POST /api/student/sessions"}


@router.get("/sessions", response_model=list[SessionResponse])
async def list_sessions(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> list[SessionResponse]:
    """List own active (non-deleted) sessions."""
    return [{"status": "not implemented", "endpoint": "GET /api/student/sessions"}]


@router.get("/sessions/{session_id}", response_model=list[MessageResponse])
async def get_session_history(
    session_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> list[MessageResponse]:
    """
    Fetch message history for a session.
    IDOR check required: session must belong to current_user.
    """
    return [
        {
            "status": "not implemented",
            "endpoint": f"GET /api/student/sessions/{session_id}",
        }
    ]
