"""
routers/teacher.py — Teacher audit & control endpoints.

Contract endpoints:
  GET /api/teacher/students                        — list active students
  GET /api/teacher/sessions/{student_id}           — full chat history for a student
  PUT /api/teacher/sessions/{session_id}/poison    — toggle poison + set prompt
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_teacher
from app.database import get_db
from app.models import User
from app.schemas import MessageResponse, PoisonUpdate, SessionResponse, UserResponse

router = APIRouter(prefix="/api/teacher", tags=["teacher"])


@router.get("/students", response_model=list[UserResponse])
async def list_active_students(
    current_user: Annotated[User, Depends(require_teacher)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> list[UserResponse]:
    """List all active (non-deleted) students."""
    return [{"status": "not implemented", "endpoint": "GET /api/teacher/students"}]


@router.get("/sessions/{student_id}", response_model=list[MessageResponse])
async def get_student_chat_history(
    student_id: uuid.UUID,
    current_user: Annotated[User, Depends(require_teacher)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> list[MessageResponse]:
    """Fetch full message history for a specific student."""
    return [
        {
            "status": "not implemented",
            "endpoint": f"GET /api/teacher/sessions/{student_id}",
        }
    ]


@router.put("/sessions/{session_id}/poison", response_model=SessionResponse)
async def toggle_poison(
    session_id: uuid.UUID,
    payload: PoisonUpdate,
    current_user: Annotated[User, Depends(require_teacher)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> SessionResponse:
    """Toggle is_poison_active and update poison_prompt for a session."""
    return {
        "status": "not implemented",
        "endpoint": f"PUT /api/teacher/sessions/{session_id}/poison",
    }
