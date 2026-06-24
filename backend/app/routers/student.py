"""
routers/student.py — Student session management, class-joining, and self-service endpoints.

Audit rule: a student "delete" is a SOFT delete (is_deleted=True). The session is
hidden from the student (their list/reads exclude it), but it is permanently retained
and fully visible to teacher/admin audit, flagged as deleted. Only admin can hard-delete.

Endpoints
---------
POST   /api/student/classes/join               →  Join a class via 6-char invite code.
GET    /api/student/classes                    →  List joined classes.
GET    /api/student/classes/{id}/labs           →  List labs for a joined class.
DELETE /api/student/classes/{class_id}/leave   →  Voluntarily leave a class.
POST   /api/student/labs/{lab_id}/sessions     →  Create a session scoped to a lab.
GET    /api/student/sessions                   →  List own active sessions (optional lab filter).
GET    /api/student/sessions/{session_id}      →  Fetch message history (IDOR check required).
PUT    /api/student/sessions/{session_id}      →  Rename session title (cosmetic, no audit impact).
DELETE /api/student/sessions/{session_id}      →  Soft-delete (hide) own session; retained for audit.
GET    /api/student/usage                      →  Daily token usage stats.
"""

import uuid
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.auth import get_current_user
from backend.app.database import get_db
from backend.app.models import Class, ClassStudent, Lab, User, UsageStat
from backend.app.schemas import (
    ClassResponse,
    JoinClassRequest,
    JoinClassResponse,
    LabResponse,
    SessionCreateRequest,
    SessionResponse,
    SessionUpdateRequest,
    SessionWithMessagesResponse,
)
from backend.app.services import session_service

router = APIRouter(prefix="/api/student", tags=["student"])


# ===================================================================
# POST /api/student/classes/join
# ===================================================================


@router.post("/classes/join", response_model=JoinClassResponse, status_code=201)
async def join_class(
    body: JoinClassRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> JoinClassResponse:
    """
    Join a class using a 6-character invite code.

    Returns 404 if the invite code is invalid.
    Returns 409 if the student has already joined this class.
    """
    # Look up class by invite code
    result = await db.execute(
        select(Class).where(Class.invite_code == body.invite_code.upper())
    )
    target_class = result.scalar_one_or_none()
    if target_class is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Invalid invite code",
        )

    # Check if already joined
    existing = await db.execute(
        select(ClassStudent).where(
            ClassStudent.class_id == target_class.id,
            ClassStudent.student_id == current_user.id,
        )
    )
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="You have already joined this class",
        )

    # Create the membership
    membership = ClassStudent(
        class_id=target_class.id,
        student_id=current_user.id,
    )
    db.add(membership)
    await db.flush()
    await db.refresh(membership)

    return JoinClassResponse(
        class_id=target_class.id,
        class_name=target_class.name,
        joined_at=membership.joined_at,
    )


# ===================================================================
# GET /api/student/classes
# ===================================================================


@router.get("/classes", response_model=list[ClassResponse])
async def list_joined_classes(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[ClassResponse]:
    """List all classes the student has joined."""
    result = await db.execute(
        select(Class)
        .join(ClassStudent, ClassStudent.class_id == Class.id)
        .where(
            ClassStudent.student_id == current_user.id,
            Class.is_deleted.is_(False),
        )
        .order_by(Class.created_at.desc())
    )
    classes = result.scalars().all()
    return [ClassResponse.model_validate(c) for c in classes]


# ===================================================================
# GET /api/student/classes/{class_id}/labs
# ===================================================================


@router.get("/classes/{class_id}/labs", response_model=list[LabResponse])
async def list_labs_for_class(
    class_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[LabResponse]:
    """List labs for a class the student has joined."""
    # Verify membership
    membership = await db.execute(
        select(ClassStudent).where(
            ClassStudent.class_id == class_id,
            ClassStudent.student_id == current_user.id,
        )
    )
    if membership.scalar_one_or_none() is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You are not a member of this class",
        )

    result = await db.execute(
        select(Lab)
        .where(Lab.class_id == class_id)
        .order_by(Lab.created_at.desc())
    )
    labs = result.scalars().all()
    return [LabResponse.model_validate(l) for l in labs]


# ===================================================================
# POST /api/student/labs/{lab_id}/sessions
# ===================================================================


@router.post(
    "/labs/{lab_id}/sessions",
    response_model=SessionResponse,
    status_code=201,
)
async def create_session_in_lab(
    lab_id: uuid.UUID,
    body: SessionCreateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> SessionResponse:
    """
    Create a new chat session within a specific lab.

    Validates that the student belongs to the class that owns this lab.
    """
    # Fetch the lab and verify it exists
    lab_result = await db.execute(select(Lab).where(Lab.id == lab_id))
    lab = lab_result.scalar_one_or_none()
    if lab is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Lab not found",
        )

    # Verify student is a member of the class that owns this lab
    membership = await db.execute(
        select(ClassStudent).where(
            ClassStudent.class_id == lab.class_id,
            ClassStudent.student_id == current_user.id,
        )
    )
    if membership.scalar_one_or_none() is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You are not a member of the class that owns this lab",
        )

    session = await session_service.create_session(
        db, user_id=current_user.id, lab_id=lab_id, title=body.title
    )
    return SessionResponse.model_validate(session)


# ===================================================================
# GET /api/student/sessions
# ===================================================================


@router.get("/sessions", response_model=list[SessionResponse])
async def list_sessions(
    lab_id: uuid.UUID | None = Query(None, description="Filter by lab ID"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[SessionResponse]:
    """Return all non-deleted sessions belonging to the authenticated user."""
    sessions = await session_service.list_sessions_for_student(
        db, user_id=current_user.id, lab_id=lab_id
    )
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
    session = await session_service.get_session_with_messages(db, session_id)

    if session.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to view this session",
        )

    # A session the student soft-deleted is hidden from them (the "deleted" illusion);
    # it remains retained and visible to teacher/admin audit.
    if session.is_deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found",
        )

    return SessionWithMessagesResponse.model_validate(session)


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
        "limit": current_user.daily_token_quota,
    }


# ===================================================================
# DELETE /api/student/classes/{class_id}/leave
# ===================================================================


@router.delete("/classes/{class_id}/leave", status_code=204)
async def leave_class(
    class_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Student voluntarily leaves a class."""
    mem_result = await db.execute(
        select(ClassStudent).where(
            ClassStudent.class_id == class_id,
            ClassStudent.student_id == current_user.id,
        )
    )
    membership = mem_result.scalar_one_or_none()
    if membership is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="You are not a member of this class",
        )

    await db.delete(membership)
    await db.flush()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ===================================================================
# PUT /api/student/sessions/{session_id}
# ===================================================================


@router.put("/sessions/{session_id}", response_model=SessionResponse)
async def update_session_title(
    session_id: uuid.UUID,
    body: SessionUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> SessionResponse:
    """Update the title of a session owned by the authenticated user."""
    session = await session_service.get_session_by_id(db, session_id)
    if session.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to modify this session",
        )
    if session.is_deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found",
        )

    session = await session_service.rename_session(db, session, body.title)
    return SessionResponse.model_validate(session)


# ===================================================================
# DELETE /api/student/sessions/{session_id}
# ===================================================================


@router.delete("/sessions/{session_id}", status_code=204)
async def soft_delete_own_session(
    session_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Soft-delete (hide) the student's own session.

    Sets is_deleted=True: the session disappears from the student's own views, but is
    permanently retained and remains fully visible to teacher/admin audit (flagged as
    deleted). Only admin can hard-delete. Idempotent on an already-hidden session.
    """
    session = await session_service.get_session_by_id(db, session_id)
    if session.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to modify this session",
        )
    await session_service.soft_delete_session(db, session)
    return Response(status_code=status.HTTP_204_NO_CONTENT)

