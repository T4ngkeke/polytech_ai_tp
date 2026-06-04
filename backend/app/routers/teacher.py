"""
routers/teacher.py — Teacher endpoints for class/lab management, rules, audit,
lifecycle control, and analytics.

All endpoints are protected by ``require_teacher`` — users whose live DB
role is ``teacher`` or ``admin`` can access them.

Endpoints
---------
POST   /api/teacher/classes                                  →  Create a class with invite code.
GET    /api/teacher/classes                                  →  List classes owned by the teacher.
POST   /api/teacher/classes/{class_id}/labs                  →  Create a lab in a class.
GET    /api/teacher/classes/{class_id}/labs                  →  List labs for a class.
PUT    /api/teacher/rules                                    →  Upsert a 3-tier rule.
GET    /api/teacher/rules                                    →  List rules (with optional filters).
GET    /api/teacher/chat-history                             →  Fetch sessions/messages with filters.
GET    /api/teacher/students                                 →  Active students + today's token consumption.
DELETE /api/teacher/classes/{class_id}/students/{student_id} →  Unenroll a student.
POST   /api/teacher/classes/{class_id}/reset-code            →  Generate new invite code.
PUT    /api/teacher/classes/{class_id}                       →  Update class name.
DELETE /api/teacher/classes/{class_id}                       →  Soft-delete a class.
PUT    /api/teacher/labs/{lab_id}                            →  Update lab name / toggle is_active.
DELETE /api/teacher/labs/{lab_id}                            →  Soft-delete a lab.
GET    /api/teacher/analytics/classes/{class_id}             →  Class-scoped token analytics.
"""

import secrets
import uuid
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.app.auth import require_teacher
from backend.app.database import get_db
from backend.app.models import (
    Class,
    ClassStudent,
    Lab,
    Message,
    Rule,
    RuleLevel,
    Session,
    UsageStat,
    User,
    UserRole,
)
from backend.app.schemas import (
    ClassAnalyticsResponse,
    ClassCreate,
    ClassResponse,
    ClassUpdateRequest,
    InviteCodeResponse,
    LabCreate,
    LabResponse,
    LabUpdateRequest,
    RuleResponse,
    RuleUpsertRequest,
    SessionWithMessagesResponse,
    StudentSummaryResponse,
    StudentUsageSummary,
)

router = APIRouter(prefix="/api/teacher", tags=["teacher"])


# ---------------------------------------------------------------------------
# Helper: Generate unique 6-character invite code
# ---------------------------------------------------------------------------

def _generate_invite_code() -> str:
    """Generate a 6-character uppercase alphanumeric code."""
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # No 0/O/1/I to avoid confusion
    return "".join(secrets.choice(alphabet) for _ in range(6))


# ===================================================================
# POST /api/teacher/classes
# ===================================================================


@router.post("/classes", response_model=ClassResponse, status_code=201)
async def create_class(
    body: ClassCreate,
    teacher: User = Depends(require_teacher),
    db: AsyncSession = Depends(get_db),
) -> ClassResponse:
    """
    Create a new class owned by the authenticated teacher.

    Automatically generates a unique 6-character invite code for
    zero-friction student joining.
    """
    # Generate unique invite code (retry on collision)
    for _ in range(10):
        code = _generate_invite_code()
        existing = await db.execute(
            select(Class).where(Class.invite_code == code)
        )
        if existing.scalar_one_or_none() is None:
            break
    else:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to generate unique invite code",
        )

    new_class = Class(
        name=body.name,
        teacher_id=teacher.id,
        invite_code=code,
    )
    db.add(new_class)
    await db.flush()
    await db.refresh(new_class)
    return ClassResponse.model_validate(new_class)


# ===================================================================
# GET /api/teacher/classes
# ===================================================================


@router.get("/classes", response_model=list[ClassResponse])
async def list_classes(
    teacher: User = Depends(require_teacher),
    db: AsyncSession = Depends(get_db),
) -> list[ClassResponse]:
    """List all non-deleted classes owned by the authenticated teacher."""
    result = await db.execute(
        select(Class)
        .where(Class.teacher_id == teacher.id, Class.is_deleted.is_(False))
        .order_by(Class.created_at.desc())
    )
    classes = result.scalars().all()
    return [ClassResponse.model_validate(c) for c in classes]


# ===================================================================
# POST /api/teacher/classes/{class_id}/labs
# ===================================================================


@router.post(
    "/classes/{class_id}/labs",
    response_model=LabResponse,
    status_code=201,
)
async def create_lab(
    class_id: uuid.UUID,
    body: LabCreate,
    teacher: User = Depends(require_teacher),
    db: AsyncSession = Depends(get_db),
) -> LabResponse:
    """
    Create a new lab within a class.

    Verifies that the class exists and is owned by the authenticated teacher.
    """
    result = await db.execute(
        select(Class).where(
            Class.id == class_id,
            Class.teacher_id == teacher.id,
            Class.is_deleted.is_(False),
        )
    )
    parent_class = result.scalar_one_or_none()
    if parent_class is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Class not found or not owned by you",
        )

    lab = Lab(
        class_id=class_id,
        name=body.name,
    )
    db.add(lab)
    await db.flush()
    await db.refresh(lab)
    return LabResponse.model_validate(lab)


# ===================================================================
# GET /api/teacher/classes/{class_id}/labs
# ===================================================================


@router.get("/classes/{class_id}/labs", response_model=list[LabResponse])
async def list_labs(
    class_id: uuid.UUID,
    teacher: User = Depends(require_teacher),
    db: AsyncSession = Depends(get_db),
) -> list[LabResponse]:
    """List all labs for a class owned by the teacher."""
    # Verify ownership
    result = await db.execute(
        select(Class).where(
            Class.id == class_id,
            Class.teacher_id == teacher.id,
            Class.is_deleted.is_(False),
        )
    )
    if result.scalar_one_or_none() is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Class not found or not owned by you",
        )

    labs_result = await db.execute(
        select(Lab)
        .where(Lab.class_id == class_id, Lab.is_deleted.is_(False))
        .order_by(Lab.created_at.desc())
    )
    labs = labs_result.scalars().all()
    return [LabResponse.model_validate(l) for l in labs]


# ===================================================================
# PUT /api/teacher/rules
# ===================================================================


@router.put("/rules", response_model=RuleResponse)
async def upsert_rule(
    body: RuleUpsertRequest,
    _teacher: User = Depends(require_teacher),
    db: AsyncSession = Depends(get_db),
) -> RuleResponse:
    """
    Create or update a 3-tier rule based on (level, target_id).

    If a rule with the same level + target_id exists, update its
    ``rules_text`` and ``is_active``. Otherwise, create a new rule.
    """
    result = await db.execute(
        select(Rule).where(
            Rule.level == body.level,
            Rule.target_id == body.target_id,
        )
    )
    rule = result.scalar_one_or_none()

    if rule is not None:
        rule.rules_text = body.rules_text
        rule.is_active = body.is_active
    else:
        rule = Rule(
            level=body.level,
            target_id=body.target_id,
            rules_text=body.rules_text,
            is_active=body.is_active,
        )
        db.add(rule)

    await db.flush()
    await db.refresh(rule)
    return RuleResponse.model_validate(rule)


# ===================================================================
# GET /api/teacher/rules
# ===================================================================


@router.get("/rules", response_model=list[RuleResponse])
async def list_rules(
    level: str | None = Query(None, description="Filter by level: class, lab, student"),
    target_id: uuid.UUID | None = Query(None, description="Filter by target ID"),
    _teacher: User = Depends(require_teacher),
    db: AsyncSession = Depends(get_db),
) -> list[RuleResponse]:
    """List rules with optional level and target_id filters."""
    query = select(Rule)
    if level is not None:
        query = query.where(Rule.level == level)
    if target_id is not None:
        query = query.where(Rule.target_id == target_id)

    result = await db.execute(query)
    rules = result.scalars().all()
    return [RuleResponse.model_validate(r) for r in rules]


# ===================================================================
# GET /api/teacher/chat-history
# ===================================================================


@router.get("/chat-history", response_model=list[SessionWithMessagesResponse])
async def get_chat_history(
    class_id: uuid.UUID | None = Query(None),
    lab_id: uuid.UUID | None = Query(None),
    student_id: uuid.UUID | None = Query(None),
    session_id: uuid.UUID | None = Query(None),
    _teacher: User = Depends(require_teacher),
    db: AsyncSession = Depends(get_db),
) -> list[SessionWithMessagesResponse]:
    """
    Fetch chat sessions and messages with flexible filters.

    Filters: ``class_id``, ``lab_id``, ``student_id``, ``session_id``.
    Joins through Lab → Class to scope by class when needed.
    """
    query = (
        select(Session)
        .where(Session.is_deleted.is_(False))
        .options(selectinload(Session.messages))
    )

    # If filtering by specific session, just return that
    if session_id is not None:
        query = query.where(Session.id == session_id)
    else:
        if student_id is not None:
            query = query.where(Session.user_id == student_id)

        if lab_id is not None:
            query = query.where(Session.lab_id == lab_id)
        elif class_id is not None:
            # Find all lab IDs belonging to this class, then filter sessions
            lab_result = await db.execute(
                select(Lab.id).where(Lab.class_id == class_id)
            )
            lab_ids = [row[0] for row in lab_result.all()]
            if lab_ids:
                query = query.where(Session.lab_id.in_(lab_ids))
            else:
                return []  # No labs in this class

    query = query.order_by(Session.created_at.desc())
    result = await db.execute(query)
    sessions = result.scalars().all()

    return [SessionWithMessagesResponse.model_validate(s) for s in sessions]


# ===================================================================
# GET /api/teacher/students
# ===================================================================


@router.get("/students", response_model=list[StudentSummaryResponse])
async def list_students(
    _teacher: User = Depends(require_teacher),
    db: AsyncSession = Depends(get_db),
) -> list[StudentSummaryResponse]:
    """
    List active students with their current daily token consumption.

    Joins ``users`` with ``usage_stats`` for today's date.
    Students with no usage today show ``tokens_used_today=0``.
    """
    today = date.today()

    # Fetch all active students
    result = await db.execute(
        select(User).where(
            User.role == UserRole.student,
            User.is_deleted.is_(False),
        )
    )
    students = result.scalars().all()

    summaries = []
    for student in students:
        # Get today's usage for this student (if any)
        usage_result = await db.execute(
            select(UsageStat).where(
                UsageStat.user_id == student.id,
                UsageStat.date == today,
            )
        )
        usage = usage_result.scalar_one_or_none()

        summaries.append(
            StudentSummaryResponse(
                id=student.id,
                username=student.username,
                daily_token_quota=student.daily_token_quota,
                tokens_used_today=usage.tokens_used if usage else 0,
                request_count_today=usage.request_count if usage else 0,
            )
        )

    return summaries


# ===================================================================
# DELETE /api/teacher/classes/{class_id}/students/{student_id}
# ===================================================================


@router.delete("/classes/{class_id}/students/{student_id}", status_code=204)
async def unenroll_student(
    class_id: uuid.UUID,
    student_id: uuid.UUID,
    teacher: User = Depends(require_teacher),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Remove a student from a class (delete the ClassStudent row)."""
    # Verify class ownership
    cls_result = await db.execute(
        select(Class).where(
            Class.id == class_id,
            Class.teacher_id == teacher.id,
            Class.is_deleted.is_(False),
        )
    )
    if cls_result.scalar_one_or_none() is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Class not found or not owned by you",
        )

    # Find the membership
    mem_result = await db.execute(
        select(ClassStudent).where(
            ClassStudent.class_id == class_id,
            ClassStudent.student_id == student_id,
        )
    )
    membership = mem_result.scalar_one_or_none()
    if membership is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Student is not enrolled in this class",
        )

    await db.delete(membership)
    await db.flush()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ===================================================================
# POST /api/teacher/classes/{class_id}/reset-code
# ===================================================================


@router.post("/classes/{class_id}/reset-code", response_model=InviteCodeResponse)
async def reset_invite_code(
    class_id: uuid.UUID,
    teacher: User = Depends(require_teacher),
    db: AsyncSession = Depends(get_db),
) -> InviteCodeResponse:
    """Generate and save a new 6-character invite code for a class."""
    cls_result = await db.execute(
        select(Class).where(
            Class.id == class_id,
            Class.teacher_id == teacher.id,
            Class.is_deleted.is_(False),
        )
    )
    cls = cls_result.scalar_one_or_none()
    if cls is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Class not found or not owned by you",
        )

    # Generate unique code (retry on collision)
    for _ in range(10):
        code = _generate_invite_code()
        existing = await db.execute(
            select(Class).where(Class.invite_code == code)
        )
        if existing.scalar_one_or_none() is None:
            break
    else:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to generate unique invite code",
        )

    cls.invite_code = code
    db.add(cls)
    await db.flush()
    return InviteCodeResponse(invite_code=code)


# ===================================================================
# PUT /api/teacher/classes/{class_id}
# ===================================================================


@router.put("/classes/{class_id}", response_model=ClassResponse)
async def update_class(
    class_id: uuid.UUID,
    body: ClassUpdateRequest,
    teacher: User = Depends(require_teacher),
    db: AsyncSession = Depends(get_db),
) -> ClassResponse:
    """Update the name of a class."""
    cls_result = await db.execute(
        select(Class).where(
            Class.id == class_id,
            Class.teacher_id == teacher.id,
            Class.is_deleted.is_(False),
        )
    )
    cls = cls_result.scalar_one_or_none()
    if cls is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Class not found or not owned by you",
        )

    cls.name = body.name
    db.add(cls)
    await db.flush()
    await db.refresh(cls)
    return ClassResponse.model_validate(cls)


# ===================================================================
# DELETE /api/teacher/classes/{class_id}
# ===================================================================


@router.delete("/classes/{class_id}", status_code=204)
async def soft_delete_class(
    class_id: uuid.UUID,
    teacher: User = Depends(require_teacher),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Soft-delete a class: sets ``is_deleted = True``."""
    cls_result = await db.execute(
        select(Class).where(
            Class.id == class_id,
            Class.teacher_id == teacher.id,
            Class.is_deleted.is_(False),
        )
    )
    cls = cls_result.scalar_one_or_none()
    if cls is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Class not found or not owned by you",
        )

    cls.is_deleted = True
    db.add(cls)
    await db.flush()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ===================================================================
# PUT /api/teacher/labs/{lab_id}
# ===================================================================


@router.put("/labs/{lab_id}", response_model=LabResponse)
async def update_lab(
    lab_id: uuid.UUID,
    body: LabUpdateRequest,
    teacher: User = Depends(require_teacher),
    db: AsyncSession = Depends(get_db),
) -> LabResponse:
    """Update a lab's name and/or toggle its is_active status."""
    # Fetch lab
    lab_result = await db.execute(
        select(Lab).where(Lab.id == lab_id, Lab.is_deleted.is_(False))
    )
    lab = lab_result.scalar_one_or_none()
    if lab is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Lab not found",
        )

    # Verify ownership through class
    cls_result = await db.execute(
        select(Class).where(
            Class.id == lab.class_id,
            Class.teacher_id == teacher.id,
        )
    )
    if cls_result.scalar_one_or_none() is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to modify this lab",
        )

    if body.name is not None:
        lab.name = body.name
    if body.is_active is not None:
        lab.is_active = body.is_active

    db.add(lab)
    await db.flush()
    await db.refresh(lab)
    return LabResponse.model_validate(lab)


# ===================================================================
# DELETE /api/teacher/labs/{lab_id}
# ===================================================================


@router.delete("/labs/{lab_id}", status_code=204)
async def soft_delete_lab(
    lab_id: uuid.UUID,
    teacher: User = Depends(require_teacher),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Soft-delete a lab: sets ``is_deleted = True``."""
    lab_result = await db.execute(
        select(Lab).where(Lab.id == lab_id, Lab.is_deleted.is_(False))
    )
    lab = lab_result.scalar_one_or_none()
    if lab is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Lab not found",
        )

    # Verify ownership through class
    cls_result = await db.execute(
        select(Class).where(
            Class.id == lab.class_id,
            Class.teacher_id == teacher.id,
        )
    )
    if cls_result.scalar_one_or_none() is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to delete this lab",
        )

    lab.is_deleted = True
    db.add(lab)
    await db.flush()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ===================================================================
# GET /api/teacher/analytics/classes/{class_id}
# ===================================================================


@router.get("/analytics/classes/{class_id}", response_model=ClassAnalyticsResponse)
async def get_class_analytics(
    class_id: uuid.UUID,
    teacher: User = Depends(require_teacher),
    db: AsyncSession = Depends(get_db),
) -> ClassAnalyticsResponse:
    """
    Fetch granular token usage for students inside a specific class.

    Aggregates from Message tokens through Session → Lab → Class.
    """
    # Verify class ownership
    cls_result = await db.execute(
        select(Class).where(
            Class.id == class_id,
            Class.teacher_id == teacher.id,
            Class.is_deleted.is_(False),
        )
    )
    if cls_result.scalar_one_or_none() is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Class not found or not owned by you",
        )

    # Get all lab IDs for this class
    lab_result = await db.execute(
        select(Lab.id).where(Lab.class_id == class_id)
    )
    lab_ids = [row[0] for row in lab_result.all()]

    if not lab_ids:
        return ClassAnalyticsResponse(
            class_id=class_id,
            total_tokens=0,
            total_requests=0,
            students=[],
        )

    # Aggregate tokens from messages in sessions belonging to these labs
    # Group by user_id
    stats_result = await db.execute(
        select(
            Session.user_id,
            func.coalesce(func.sum(Message.total_tokens), 0),
            func.count(Message.id),
        )
        .join(Message, Message.session_id == Session.id)
        .where(Session.lab_id.in_(lab_ids))
        .group_by(Session.user_id)
    )
    user_stats = stats_result.all()

    total_tokens = sum(r[1] for r in user_stats)
    total_requests = sum(r[2] for r in user_stats)

    # Fetch user details for each student
    students = []
    for user_id, tokens, req_count in user_stats:
        user_result = await db.execute(
            select(User).where(User.id == user_id)
        )
        user = user_result.scalar_one_or_none()
        if user:
            students.append(
                StudentUsageSummary(
                    user_id=user.id,
                    username=user.username,
                    tokens_used=tokens,
                    request_count=req_count,
                )
            )

    return ClassAnalyticsResponse(
        class_id=class_id,
        total_tokens=total_tokens,
        total_requests=total_requests,
        students=students,
    )
