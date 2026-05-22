"""
routers/teacher.py — Teacher endpoints for student audit and rule management.

All endpoints are protected by ``require_teacher`` — users whose live DB
role is ``teacher`` or ``admin`` can access them.

Endpoints
---------
GET  /api/teacher/students                  →  Active students + today's token consumption.
GET  /api/teacher/sessions/{student_id}     →  Full chat history for a student.
POST /api/teacher/rules                     →  Create a new pedagogical rule.
PUT  /api/teacher/rules/{rule_id}/toggle    →  Toggle is_active on a rule.
"""

import json
import uuid
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.app.auth import require_teacher
from backend.app.database import get_db
from backend.app.models import (
    Session,
    TeacherRule,
    UsageStat,
    User,
    UserRole,
)
from backend.app.schemas import (
    SessionWithMessagesResponse,
    StudentSummaryResponse,
    TeacherRuleCreateRequest,
    TeacherRuleResponse,
    TeacherRuleToggleResponse,
)

router = APIRouter(prefix="/api/teacher", tags=["teacher"])


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
# GET /api/teacher/sessions/{student_id}
# ===================================================================


@router.get(
    "/sessions/{student_id}",
    response_model=list[SessionWithMessagesResponse],
)
async def get_student_sessions(
    student_id: uuid.UUID,
    _teacher: User = Depends(require_teacher),
    db: AsyncSession = Depends(get_db),
) -> list[SessionWithMessagesResponse]:
    """
    Fetch all sessions and messages for a specific student.

    Returns 404 if the student does not exist or is deleted.
    """
    # Verify the student exists and is active
    result = await db.execute(
        select(User).where(
            User.id == student_id,
            User.is_deleted.is_(False),
            User.role == UserRole.student,
        )
    )
    student = result.scalar_one_or_none()
    if student is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Student not found",
        )

    # Fetch sessions with messages eagerly loaded
    sessions_result = await db.execute(
        select(Session)
        .where(
            Session.user_id == student_id,
            Session.is_deleted.is_(False),
        )
        .options(selectinload(Session.messages))
        .order_by(Session.created_at.desc())
    )
    sessions = sessions_result.scalars().all()

    return [
        SessionWithMessagesResponse.model_validate(s) for s in sessions
    ]


# ===================================================================
# POST /api/teacher/rules
# ===================================================================


@router.post("/rules", response_model=TeacherRuleResponse, status_code=201)
async def create_rule(
    body: TeacherRuleCreateRequest,
    teacher: User = Depends(require_teacher),
    db: AsyncSession = Depends(get_db),
) -> TeacherRuleResponse:
    """
    Create a new TeacherRule.

    If ``student_id`` is omitted, the rule applies to all students.
    The ``teacher_id`` is set automatically from the authenticated user.
    """
    # Serialize to JSON string so it works with both PostgreSQL JSON
    # columns and SQLite Text columns (used in the test suite).
    rules_data = (
        json.dumps(body.rules_json)
        if not isinstance(body.rules_json, str)
        else body.rules_json
    )
    rule = TeacherRule(
        teacher_id=teacher.id,
        student_id=body.student_id,
        rules_json=rules_data,
        is_active=body.is_active,
    )
    db.add(rule)
    await db.flush()
    await db.refresh(rule)
    return TeacherRuleResponse.model_validate(rule)


# ===================================================================
# PUT /api/teacher/rules/{rule_id}/toggle
# ===================================================================


@router.put(
    "/rules/{rule_id}/toggle",
    response_model=TeacherRuleToggleResponse,
)
async def toggle_rule(
    rule_id: uuid.UUID,
    _teacher: User = Depends(require_teacher),
    db: AsyncSession = Depends(get_db),
) -> TeacherRuleToggleResponse:
    """Toggle the ``is_active`` flag for a specific TeacherRule."""
    result = await db.execute(
        select(TeacherRule).where(TeacherRule.id == rule_id)
    )
    rule = result.scalar_one_or_none()
    if rule is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Rule not found",
        )

    rule.is_active = not rule.is_active
    db.add(rule)
    await db.flush()
    await db.refresh(rule)
    return TeacherRuleToggleResponse(id=rule.id, is_active=rule.is_active)


# ===================================================================
# GET /api/teacher/rules
# ===================================================================


@router.get("/rules", response_model=list[TeacherRuleResponse])
async def list_rules(
    teacher: User = Depends(require_teacher),
    db: AsyncSession = Depends(get_db),
) -> list[TeacherRuleResponse]:
    """Return all TeacherRule rows created by the authenticated teacher."""
    result = await db.execute(
        select(TeacherRule).where(TeacherRule.teacher_id == teacher.id)
    )
    rules = result.scalars().all()
    return [TeacherRuleResponse.model_validate(r) for r in rules]
