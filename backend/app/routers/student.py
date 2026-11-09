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
GET    /api/student/usage                      →  Day and week token usage, limits, resets.
POST   /api/student/usage/reset-request        →  Ask the teacher for a reset (at the limit).
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.app.auth import get_current_user, require_student
from backend.app.config import settings
from backend.app.database import get_db
from backend.app.models import Class, ClassStudent, Lab, User
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
from backend.app.services import quota_service, session_service
from backend.app.services.research_runs import ResearchRuns

router = APIRouter(prefix="/api/student", tags=["student"])


# ===================================================================
# POST /api/student/classes/join
# ===================================================================


@router.post("/classes/join", response_model=JoinClassResponse, status_code=201)
async def join_class(
    body: JoinClassRequest,
    current_user: User = Depends(require_student),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> JoinClassResponse:
    """
    Join a class using a 6-character invite code.

    Returns 403 for a teacher or an admin, 404 if the invite code is invalid.
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
    db: AsyncSession = Depends(get_db, scope="function"),
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
    db: AsyncSession = Depends(get_db, scope="function"),
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
    db: AsyncSession = Depends(get_db, scope="function"),
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
    # The browser can send the first chat request as soon as it sees 201.
    # Dependency cleanup may run after that response, so commit before returning.
    await db.commit()
    return SessionResponse.model_validate(session)


# ===================================================================
# GET /api/student/sessions
# ===================================================================


@router.get("/sessions", response_model=list[SessionResponse])
async def list_sessions(
    lab_id: uuid.UUID | None = Query(None, description="Filter by lab ID"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db, scope="function"),
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
    db: AsyncSession = Depends(get_db, scope="function"),
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

    response = SessionWithMessagesResponse.model_validate(session)
    factory = async_sessionmaker(db.bind, expire_on_commit=False)
    citations = (await ResearchRuns(factory).load_session_citations(
        session_id=session_id, user_id=current_user.id)
        if settings.AGENTIC_SEARCH_ENABLED else {})
    for message in response.messages:
        message.citations = citations.get(message.id, [])
    return response


# ===================================================================
# GET /api/student/usage
# ===================================================================


@router.get("/usage")
async def get_usage(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db, scope="function"),
):
    """The current user's day and week limits (quota plus teacher grants),
    what is used, whether a new question is blocked, and when it resets."""
    return await _usage_payload(db, current_user)


async def _usage_payload(db: AsyncSession, user: User) -> dict:
    usage = await quota_service.usage(db, user)
    asked = await quota_service.open_requests(db, [user.id])
    return {"used": usage.used, "limit": usage.limit,
            "week_used": usage.week_used, "week_limit": usage.week_limit,
            "blocked": usage.blocked, "resets_at": usage.resets_at.isoformat(),
            "week_resets_at": usage.week_resets_at.isoformat(),
            "reset_requested": user.id in asked}


# ===================================================================
# POST /api/student/usage/reset-request
# ===================================================================


@router.post("/usage/reset-request")
async def request_quota_reset(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db, scope="function"),
):
    """A student at the limit asks the teachers of their classes for a reset.
    One open request at a time; the teachers see it on their page (GET
    /api/teacher/quota-requests). 409 when the student is not at the limit."""
    usage = await quota_service.usage(db, current_user)
    if usage.blocked is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="not_at_limit")
    sent = await quota_service.request_reset(db, current_user)
    await db.commit()
    return {**await _usage_payload(db, current_user), "sent": sent}


# ===================================================================
# DELETE /api/student/classes/{class_id}/leave
# ===================================================================


@router.delete("/classes/{class_id}/leave", status_code=204)
async def leave_class(
    class_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db, scope="function"),
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
    db: AsyncSession = Depends(get_db, scope="function"),
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
    db: AsyncSession = Depends(get_db, scope="function"),
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

