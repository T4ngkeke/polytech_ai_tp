"""
routers/teacher.py — Teacher audit & control endpoints.

Contract endpoints:
  GET /api/teacher/students                        — list active students (is_deleted=False)
  GET /api/teacher/sessions/{student_id}           — full message history for a student
  PUT /api/teacher/sessions/{session_id}/poison    — toggle is_poison_active + set prompt
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_teacher
from app.database import get_db
from app.models import Message, Session, User, UserRole
from app.schemas import MessageResponse, PoisonUpdate, SessionResponse, UserResponse

router = APIRouter(prefix="/api/teacher", tags=["teacher"])


# ---------------------------------------------------------------------------
# GET /api/teacher/students
# ---------------------------------------------------------------------------


@router.get("/students", response_model=list[UserResponse])
async def list_active_students(
    current_user: Annotated[User, Depends(require_teacher)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> list[User]:
    """
    List all active (non-deleted) users whose role is 'student'.
    Admins and teachers are excluded from this view.
    """
    result = await db.execute(
        select(User)
        .where(User.role == UserRole.student, User.is_deleted.is_(False))
        .order_by(User.username)
    )
    return list(result.scalars().all())


# ---------------------------------------------------------------------------
# GET /api/teacher/sessions/{student_id}
# ---------------------------------------------------------------------------


@router.get("/sessions/{student_id}", response_model=list[MessageResponse])
async def get_student_chat_history(
    student_id: uuid.UUID,
    current_user: Annotated[User, Depends(require_teacher)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> list[Message]:
    """
    Return the full message history (all sessions) for a given student,
    ordered chronologically. Returns an empty list if the student has no messages.
    """
    # Verify the target user exists and is a student.
    student_result = await db.execute(select(User).where(User.id == student_id))
    student: User | None = student_result.scalar_one_or_none()

    if student is None or student.role is not UserRole.student:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Student not found."
        )

    # Collect all session IDs for this student (including deleted sessions —
    # teachers can audit everything).
    session_ids_result = await db.execute(
        select(Session.id).where(Session.user_id == student_id)
    )
    session_ids = [row[0] for row in session_ids_result.all()]

    if not session_ids:
        return []

    # Fetch all messages across those sessions, ordered by creation time.
    messages_result = await db.execute(
        select(Message)
        .where(Message.session_id.in_(session_ids))
        .order_by(Message.created_at)
    )
    return list(messages_result.scalars().all())


# ---------------------------------------------------------------------------
# PUT /api/teacher/sessions/{session_id}/poison
# ---------------------------------------------------------------------------


@router.put("/sessions/{session_id}/poison", response_model=SessionResponse)
async def toggle_poison(
    session_id: uuid.UUID,
    payload: PoisonUpdate,
    current_user: Annotated[User, Depends(require_teacher)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> Session:
    """
    Toggle is_poison_active and update poison_prompt for a specific session.
    Teachers can set this on any student session.
    """
    result = await db.execute(select(Session).where(Session.id == session_id))
    session: Session | None = result.scalar_one_or_none()

    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Session not found."
        )

    session.is_poison_active = payload.is_poison_active
    session.poison_prompt = payload.poison_prompt

    await db.commit()
    await db.refresh(session)
    return session
