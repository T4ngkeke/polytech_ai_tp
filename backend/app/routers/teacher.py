"""
routers/teacher.py — Teacher endpoints for Edu-LLM v6.

All DB/business logic is delegated to the services/ layer.
This router only: (1) authenticates, (2) verifies ownership, (3) calls service, (4) returns response.

Endpoints
---------
POST   /api/teacher/classes
GET    /api/teacher/classes
POST   /api/teacher/classes/{class_id}/labs
GET    /api/teacher/classes/{class_id}/labs
PUT    /api/teacher/rules
GET    /api/teacher/rules
GET    /api/teacher/chat-history
GET    /api/teacher/students
DELETE /api/teacher/classes/{class_id}/students/{student_id}
POST   /api/teacher/classes/{class_id}/reset-code
PUT    /api/teacher/classes/{class_id}
DELETE /api/teacher/classes/{class_id}
PUT    /api/teacher/labs/{lab_id}
DELETE /api/teacher/labs/{lab_id}
GET    /api/teacher/analytics/classes/{class_id}
"""

import uuid
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.app.auth import require_teacher
from backend.app.database import get_db
from backend.app.models import Class, ClassStudent, Lab, Session, UsageStat, User, UserRole
from backend.app.schemas import (
    ClassAnalyticsResponse,
    ClassCreate,
    ClassResponse,
    ClassUpdateRequest,
    InviteCodeResponse,
    LabCreate,
    LabResponse,
    LabUpdateRequest,
    LabUsageSummary,
    RuleResponse,
    RuleUpsertRequest,
    SessionWithMessagesResponse,
    StudentSummaryResponse,
    StudentUsageSummary,
)
from backend.app.services import class_service, lab_service, rule_service, analytics_service

router = APIRouter(prefix="/api/teacher", tags=["teacher"])


# ===================================================================
# POST /api/teacher/classes
# ===================================================================


@router.post("/classes", response_model=ClassResponse, status_code=201)
async def create_class(
    body: ClassCreate,
    teacher: User = Depends(require_teacher),
    db: AsyncSession = Depends(get_db),
) -> ClassResponse:
    """Create a new class owned by the authenticated teacher."""
    new_class = await class_service.create_class(db, teacher_id=teacher.id, name=body.name)
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
    classes = await class_service.list_classes_for_teacher(db, teacher_id=teacher.id)
    return [ClassResponse.model_validate(c) for c in classes]


# ===================================================================
# POST /api/teacher/classes/{class_id}/labs
# ===================================================================


@router.post("/classes/{class_id}/labs", response_model=LabResponse, status_code=201)
async def create_lab(
    class_id: uuid.UUID,
    body: LabCreate,
    teacher: User = Depends(require_teacher),
    db: AsyncSession = Depends(get_db),
) -> LabResponse:
    """Create a new lab within a class the teacher owns."""
    # Ownership check
    cls = await class_service.get_class_by_id(db, class_id)
    if cls.teacher_id != teacher.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized")
    lab = await lab_service.create_lab(db, class_id=class_id, name=body.name)
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
    cls = await class_service.get_class_by_id(db, class_id)
    if cls.teacher_id != teacher.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized")
    labs = await lab_service.list_labs_for_class(db, class_id=class_id)
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
    """Create or update a 3-tier rule based on (level, target_id)."""
    r = await rule_service.upsert_rule(
        db,
        level=body.level,
        target_id=body.target_id,
        rules_text=body.rules_text,
        is_active=body.is_active,
    )
    return RuleResponse.model_validate(r)


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
    rules = await rule_service.list_rules(db, level=level, target_id=target_id)
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
    """Fetch chat sessions and messages with flexible filters for audit."""
    query = (
        select(Session)
        .where(Session.is_deleted.is_(False))
        .options(selectinload(Session.messages))
    )

    if session_id is not None:
        query = query.where(Session.id == session_id)
    else:
        if student_id is not None:
            query = query.where(Session.user_id == student_id)
        if lab_id is not None:
            query = query.where(Session.lab_id == lab_id)
        elif class_id is not None:
            lab_result = await db.execute(
                select(Lab.id).where(Lab.class_id == class_id)
            )
            lab_ids = [row[0] for row in lab_result.all()]
            if lab_ids:
                query = query.where(Session.lab_id.in_(lab_ids))
            else:
                return []

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
    """List active students with their current daily token consumption."""
    today = date.today()
    result = await db.execute(
        select(User).where(User.role == UserRole.student, User.is_deleted.is_(False))
    )
    students = result.scalars().all()

    summaries = []
    for student in students:
        usage_result = await db.execute(
            select(UsageStat).where(UsageStat.user_id == student.id, UsageStat.date == today)
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
# GET /api/teacher/classes/{class_id}/students
# ===================================================================


@router.get("/classes/{class_id}/students", response_model=list[StudentSummaryResponse])
async def list_class_students(
    class_id: uuid.UUID,
    teacher: User = Depends(require_teacher),
    db: AsyncSession = Depends(get_db),
) -> list[StudentSummaryResponse]:
    """List active students enrolled in a specific class."""
    # Ownership check
    cls = await class_service.get_class_by_id(db, class_id)
    if cls.teacher_id != teacher.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized")

    today = date.today()
    result = await db.execute(
        select(User)
        .join(ClassStudent, ClassStudent.student_id == User.id)
        .where(
            ClassStudent.class_id == class_id,
            User.role == UserRole.student,
            User.is_deleted.is_(False)
        )
    )
    students = result.scalars().all()

    summaries = []
    for student in students:
        usage_result = await db.execute(
            select(UsageStat).where(UsageStat.user_id == student.id, UsageStat.date == today)
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
    """Remove a student from a class (ownership enforced)."""
    cls = await class_service.get_class_by_id(db, class_id)
    if cls.teacher_id != teacher.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized")
    await class_service.kick_student(db, class_id=class_id, student_id=student_id)
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
    cls = await class_service.get_class_by_id(db, class_id)
    if cls.teacher_id != teacher.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized")
    code = await class_service.reset_invite_code(db, cls)
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
    """Rename a class (ownership enforced)."""
    cls = await class_service.get_class_by_id(db, class_id)
    if cls.teacher_id != teacher.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized")
    cls = await class_service.rename_class(db, cls, name=body.name)
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
    """Soft-delete a class (ownership enforced)."""
    cls = await class_service.get_class_by_id(db, class_id)
    if cls.teacher_id != teacher.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized")
    await class_service.soft_delete_class(db, cls)
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
    """Update a lab's name and/or toggle its is_active status (ownership enforced)."""
    lab = await lab_service.get_lab_by_id(db, lab_id)
    await lab_service.verify_lab_ownership(db, lab, teacher_id=teacher.id)
    lab = await lab_service.update_lab(db, lab, name=body.name, is_active=body.is_active)
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
    """Soft-delete a lab (ownership enforced)."""
    lab = await lab_service.get_lab_by_id(db, lab_id)
    await lab_service.verify_lab_ownership(db, lab, teacher_id=teacher.id)
    await lab_service.soft_delete_lab(db, lab)
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
    """Hierarchical token usage: class → lab → student breakdown."""
    cls = await class_service.get_class_by_id(db, class_id)
    if cls.teacher_id != teacher.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized")

    result = await analytics_service.get_class_analytics(db, class_id=class_id, class_name=cls.name)

    return ClassAnalyticsResponse(
        class_id=result.class_id,
        class_name=result.class_name,
        total_tokens=result.total_tokens,
        total_requests=result.total_requests,
        labs=[
            LabUsageSummary(
                lab_id=lab.lab_id,
                lab_name=lab.lab_name,
                tokens=lab.tokens,
                requests=lab.requests,
                students=[
                    StudentUsageSummary(
                        user_id=s.user_id,
                        username=s.username,
                        tokens_used=s.tokens_used,
                        request_count=s.request_count,
                    )
                    for s in lab.students
                ],
            )
            for lab in result.labs
        ],
    )
