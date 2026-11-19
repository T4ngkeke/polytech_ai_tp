"""Teacher routes for students, quota refills, quota requests and chat history."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.app.auth import require_teacher
from backend.app.database import get_db
from backend.app.models import Class, ClassStudent, Lab, Session, UsageStat, User, UserRole
from backend.app.routers.teacher_access import _class_access, _verify_class_ownership
from backend.app.schemas import SessionWithMessagesResponse, StudentSummaryResponse
from backend.app.services import class_service, quota_service

router = APIRouter(prefix="/api/teacher", tags=["teacher"])


async def _build_student_summaries(
    db: AsyncSession, students: list[User]
) -> list[StudentSummaryResponse]:
    """Attach today's token usage to each student (per-student lookup)."""
    today = quota_service.today()
    summaries: list[StudentSummaryResponse] = []
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
# GET /api/teacher/chat-history
# ===================================================================


@router.get("/chat-history", response_model=list[SessionWithMessagesResponse])
async def get_chat_history(
    class_id: uuid.UUID | None = Query(None),
    lab_id: uuid.UUID | None = Query(None),
    student_id: uuid.UUID | None = Query(None),
    session_id: uuid.UUID | None = Query(None),
    teacher: User = Depends(require_teacher),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> list[SessionWithMessagesResponse]:
    """Fetch chat sessions and messages with flexible filters for audit.

    Includes student soft-deleted sessions (is_deleted=True): audit sees everything,
    and the `is_deleted` flag on each row lets the UI mark the hidden ones. Only an
    admin hard-delete actually removes a session.
    """
    # Ownership scope (applied unconditionally so a supplied session_id /
    # student_id can't escape it): a teacher only audits sessions in labs of the
    # classes they own.
    owned_lab_ids = (await db.execute(
        select(Lab.id).join(Class, Lab.class_id == Class.id)
        .where(Class.teacher_id == teacher.id)
    )).scalars().all()
    if not owned_lab_ids:
        return []

    query = (
        select(Session)
        .options(selectinload(Session.messages))
        .where(Session.lab_id.in_(owned_lab_ids))
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
# GET /api/teacher/classes/{class_id}/students
# ===================================================================


@router.get("/classes/{class_id}/students", response_model=list[StudentSummaryResponse])
async def list_class_students(
    class_id: uuid.UUID,
    teacher: User = Depends(require_teacher),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> list[StudentSummaryResponse]:
    """List active students enrolled in a class (its teacher, or an admin)."""
    await _class_access(db, class_id, teacher)
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
    return await _build_student_summaries(db, students)


# ===================================================================
# DELETE /api/teacher/classes/{class_id}/students/{student_id}
# ===================================================================


@router.delete("/classes/{class_id}/students/{student_id}", status_code=204)
async def unenroll_student(
    class_id: uuid.UUID,
    student_id: uuid.UUID,
    teacher: User = Depends(require_teacher),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> Response:
    """Remove a student from a class (ownership enforced)."""
    await _verify_class_ownership(db, class_id, teacher)
    await class_service.kick_student(db, class_id=class_id, student_id=student_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ===================================================================
# POST /api/teacher/classes/{class_id}/students/{student_id}/quota-refill
# ===================================================================


@router.post("/classes/{class_id}/students/{student_id}/quota-refill")
async def refill_student_quota(
    class_id: uuid.UUID,
    student_id: uuid.UUID,
    teacher: User = Depends(require_teacher),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> dict:
    """Give a student of this class a full day and a full week again.

    Usage is not erased: the teacher grants what the student used
    (quota_grants), so the statistics stay true. The student's reset
    request of today, if any, is answered.
    """
    await _verify_class_ownership(db, class_id, teacher)
    student = await db.scalar(select(User).join(ClassStudent, ClassStudent.student_id == User.id).where(
        ClassStudent.class_id == class_id, User.id == student_id, User.is_deleted.is_(False)))
    if student is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Student not in this class")
    usage = await quota_service.refill(db, student=student, by=teacher.id)
    await db.commit()
    return {"used": usage.used, "limit": usage.limit, "granted": usage.granted,
            "week_used": usage.week_used, "week_limit": usage.week_limit}


# ===================================================================
# POST /api/teacher/classes/{class_id}/quota-refill-all
# ===================================================================


@router.post("/classes/{class_id}/quota-refill-all")
async def refill_class_quota(
    class_id: uuid.UUID,
    teacher: User = Depends(require_teacher),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> dict:
    """Give every student of the class a full day and week again (usage kept).
    `refilled` counts the students who had used part of a limit."""
    await _verify_class_ownership(db, class_id, teacher)
    students = (await db.scalars(select(User).join(ClassStudent, ClassStudent.student_id == User.id).where(
        ClassStudent.class_id == class_id, User.is_deleted.is_(False)))).all()
    before = await quota_service.usages(db, list(students))
    refilled = 0
    for student in students:
        u = before[student.id]
        if u.used > u.granted or u.week_used > u.week_granted:
            refilled += 1
        await quota_service.refill(db, student=student, by=teacher.id)
    await db.commit()
    return {"students": len(students), "refilled": refilled}


# ===================================================================
# GET /api/teacher/quota-requests
# ===================================================================


async def _my_student(db: AsyncSession, student_id: uuid.UUID, teacher: User) -> None:
    """404 unless the student is in one of the teacher's classes."""
    found = await db.scalar(select(ClassStudent.student_id).join(Class, Class.id == ClassStudent.class_id).where(
        ClassStudent.student_id == student_id, Class.teacher_id == teacher.id, Class.is_deleted.is_(False)).limit(1))
    if found is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Student not in your classes")


@router.post("/quota-requests/{student_id}/ignore")
async def ignore_quota_request(
    student_id: uuid.UUID,
    teacher: User = Depends(require_teacher),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> dict:
    """Ignore the student's open request: the limits stay, and the student
    cannot ask again today. 409 no_open_request when there is none."""
    await _my_student(db, student_id, teacher)
    if not await quota_service.ignore_request(db, student_id=student_id, by=teacher.id):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="no_open_request")
    await db.commit()
    return {"ignored": True}


@router.post("/quota-requests/{student_id}/unignore")
async def unignore_quota_request(
    student_id: uuid.UUID,
    teacher: User = Depends(require_teacher),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> dict:
    """Undo an ignore of today: the request is open again."""
    await _my_student(db, student_id, teacher)
    if not await quota_service.ignore_request(db, student_id=student_id, by=teacher.id, undo=True):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="nothing_to_undo")
    await db.commit()
    return {"ignored": False}


@router.get("/quota-requests")
async def list_quota_requests(
    teacher: User = Depends(require_teacher),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> list[dict]:
    """Today's open reset requests of students in this teacher's classes,
    oldest first, with the classes they share with the teacher."""
    rows = (await db.execute(select(User, Class.id, Class.name)
        .join(ClassStudent, ClassStudent.student_id == User.id)
        .join(Class, Class.id == ClassStudent.class_id)
        .where(Class.teacher_id == teacher.id, Class.is_deleted.is_(False),
               User.is_deleted.is_(False)))).all()
    students: dict[uuid.UUID, User] = {}
    classes: dict[uuid.UUID, list[dict]] = {}
    for user, class_id, class_name in rows:
        students[user.id] = user
        classes.setdefault(user.id, []).append({"id": class_id, "name": class_name})
    asked = await quota_service.open_requests(db, list(students))
    if not asked:
        return []
    usage = await quota_service.usages(db, [students[i] for i in asked])
    out = []
    for user_id, at in sorted(asked.items(), key=lambda item: item[1]):
        u = usage[user_id]
        out.append({"user_id": user_id, "username": students[user_id].username, "asked_at": at,
                    "classes": sorted(classes[user_id], key=lambda c: c["name"]),
                    "used": u.used, "limit": u.limit, "week_used": u.week_used,
                    "week_limit": u.week_limit, "blocked": u.blocked})
    return out
