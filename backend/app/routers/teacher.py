"""
routers/teacher.py — Teacher endpoints for classes and labs.

All DB/business logic is delegated to the services/ layer.
This router only: (1) authenticates, (2) verifies ownership, (3) calls service, (4) returns response.

Here: classes and labs (create, list, rename, lock, delete), invite codes, exam
mode and exam files, the Analytics tab, the test drive. An admin may use the
list, analytics, lock and delete routes on any class (teacher_access).
The other teacher routes, all under /api/teacher too:
- teacher_documents.py: uploads, document lists, deletion, TP text
- teacher_rules.py: rules and saved rules (presets)
- teacher_students.py: chat history, students, quota refills and requests
- teacher_reports.py: citation reports and the model-written reports
documentation/API.md lists every endpoint.
"""

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.auth import require_teacher
from backend.app.database import get_db
from backend.app.models import Class, Document, Lab, User
from backend.app.routers.teacher_access import (
    _class_access, _lab_access, _verify_class_ownership,
)
# Re-exported: tests and other routers import it from here.
from backend.app.routers.teacher_access import get_storage_root  # noqa: F401  pylint: disable=unused-import
from backend.app.schemas import (
    ClassCreate,
    ClassResponse,
    ClassFeaturesRequest,
    ClassUpdateRequest,
    ExamFilesRequest,
    ExamModeRequest,
    InviteCodeResponse,
    LabCreate,
    LabFeaturesRequest,
    LabResponse,
    LabUpdateRequest,
    TestSessionResponse,
)
from backend.app.services import (analytics_service, class_service, feature_service, lab_service,
                                  notification_service, session_service)

router = APIRouter(prefix="/api/teacher", tags=["teacher"])


# ===================================================================
# POST /api/teacher/classes
# ===================================================================


@router.post("/classes", response_model=ClassResponse, status_code=201)
async def create_class(
    body: ClassCreate,
    teacher: User = Depends(require_teacher),
    db: AsyncSession = Depends(get_db, scope="function"),
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
    db: AsyncSession = Depends(get_db, scope="function"),
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
    db: AsyncSession = Depends(get_db, scope="function"),
) -> LabResponse:
    """Create a new lab within a class the teacher owns."""
    await _verify_class_ownership(db, class_id, teacher)
    lab = await lab_service.create_lab(db, class_id=class_id, name=body.name)
    return (await feature_service.fill_effective(db, [LabResponse.model_validate(lab)]))[0]


# ===================================================================
# GET /api/teacher/classes/{class_id}/labs
# ===================================================================


@router.get("/classes/{class_id}/labs", response_model=list[LabResponse])
async def list_labs(
    class_id: uuid.UUID,
    teacher: User = Depends(require_teacher),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> list[LabResponse]:
    """List all labs of a class (its teacher, or an admin)."""
    await _class_access(db, class_id, teacher)
    labs = await lab_service.list_labs_for_class(db, class_id=class_id)
    return await feature_service.fill_effective(db, [LabResponse.model_validate(l) for l in labs])


# ===================================================================
# POST /api/teacher/classes/{class_id}/reset-code
# ===================================================================


@router.post("/classes/{class_id}/reset-code", response_model=InviteCodeResponse)
async def reset_invite_code(
    class_id: uuid.UUID,
    teacher: User = Depends(require_teacher),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> InviteCodeResponse:
    """Generate and save a new 6-character invite code for a class."""
    cls = await _verify_class_ownership(db, class_id, teacher)
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
    db: AsyncSession = Depends(get_db, scope="function"),
) -> ClassResponse:
    """Rename a class (ownership enforced)."""
    cls = await _verify_class_ownership(db, class_id, teacher)
    cls = await class_service.rename_class(db, cls, name=body.name)
    return ClassResponse.model_validate(cls)


@router.put("/classes/{class_id}/exam-mode", response_model=ClassResponse)
async def set_exam_mode(
    class_id: uuid.UUID,
    body: ExamModeRequest,
    teacher: User = Depends(require_teacher),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> ClassResponse:
    """Turn exam mode on or off. On, students search and open only the files
    allowed in the exam (PUT .../exam-files); with none, the agent answers
    without the course. The chat endpoint enforces it."""
    cls = await _verify_class_ownership(db, class_id, teacher)
    if cls.exam_mode != body.exam_mode:
        await notification_service.notify_class_students(
            db, cls.id, 'exam_mode', class_id=cls.id, class_name=cls.name, on=body.exam_mode)
    if cls.exam_mode != body.exam_mode:
        # Students do not see the conversations from before it while it lasts.
        cls.exam_mode_since = datetime.now(timezone.utc) if body.exam_mode else None
    cls.exam_mode = body.exam_mode
    await db.commit()
    await db.refresh(cls)
    return ClassResponse.model_validate(cls)


@router.put("/classes/{class_id}/features", response_model=ClassResponse)
async def set_class_features(
    class_id: uuid.UUID,
    body: ClassFeaturesRequest,
    teacher: User = Depends(require_teacher),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> ClassResponse:
    """Switch some features of a class on or off (upload, web, code, deep);
    the ones left out keep their value. Labs may choose otherwise
    (PUT /labs/{id}/features); exam mode turns them all off."""
    cls = await _verify_class_ownership(db, class_id, teacher)
    cls.features = feature_service.updated_class(cls.features, body.model_dump(exclude_none=True))
    await db.commit()
    await db.refresh(cls)
    return ClassResponse.model_validate(cls)


# ===================================================================
# GET/PUT /api/teacher/classes/{class_id}/exam-files
# ===================================================================


async def _exam_files(db: AsyncSession, class_id: uuid.UUID) -> list[dict]:
    rows = (await db.execute(
        select(Document.id, Document.filename, Document.exam_allowed, Document.status,
               Lab.id, Lab.name)
        .outerjoin(Lab, Lab.id == Document.lab_id)
        .where(Document.class_id == class_id, Document.lifecycle == "active",
               Document.structured.is_(True),
               (Document.lab_id.is_(None)) | (Lab.is_deleted.is_(False)))
        .order_by(Lab.name.is_not(None), Lab.name, Document.filename))).all()
    return [{"id": doc_id, "filename": filename, "exam_allowed": allowed,
             "status": getattr(state, "value", state), "lab_id": lab_id, "lab_name": lab_name}
            for doc_id, filename, allowed, state, lab_id, lab_name in rows]


@router.get("/classes/{class_id}/exam-files")
async def list_exam_files(
    class_id: uuid.UUID,
    teacher: User = Depends(require_teacher),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> list[dict]:
    """The class's files (whole class and each lab), and which ones students
    may still search and open in exam mode."""
    await _verify_class_ownership(db, class_id, teacher)
    return await _exam_files(db, class_id)


@router.put("/classes/{class_id}/exam-files")
async def set_exam_files(
    class_id: uuid.UUID,
    body: ExamFilesRequest,
    teacher: User = Depends(require_teacher),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> list[dict]:
    """Set the files allowed in exam mode: the given ones, no others.
    An id that is not a file of this class is ignored."""
    await _verify_class_ownership(db, class_id, teacher)
    allowed = set(body.document_ids)
    for doc in (await db.scalars(select(Document).where(
            Document.class_id == class_id, Document.lifecycle == "active"))).all():
        doc.exam_allowed = doc.id in allowed
    await db.commit()
    return await _exam_files(db, class_id)


# ===================================================================
# DELETE /api/teacher/classes/{class_id}
# ===================================================================


@router.delete("/classes/{class_id}", status_code=204)
async def soft_delete_class(
    class_id: uuid.UUID,
    teacher: User = Depends(require_teacher),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> Response:
    """Soft-delete a class (its teacher, or an admin)."""
    cls = await _class_access(db, class_id, teacher)
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
    db: AsyncSession = Depends(get_db, scope="function"),
) -> LabResponse:
    """Update a lab's name and/or toggle its is_active status (its teacher, or an admin)."""
    lab = await _lab_access(db, lab_id, teacher)
    was_active = lab.is_active
    lab = await lab_service.update_lab(db, lab, name=body.name, is_active=body.is_active)
    if lab.is_active != was_active:
        cls = await db.get(Class, lab.class_id)
        await notification_service.notify_class_students(
            db, lab.class_id, 'lab_locked', lab_id=lab.id, lab_name=lab.name,
            class_id=lab.class_id, class_name=cls.name if cls else None, locked=not lab.is_active)
    return (await feature_service.fill_effective(db, [LabResponse.model_validate(lab)]))[0]


# ===================================================================
# PUT /api/teacher/labs/{lab_id}/features
# ===================================================================


@router.put("/labs/{lab_id}/features", response_model=LabResponse)
async def set_lab_features(
    lab_id: uuid.UUID,
    body: LabFeaturesRequest,
    teacher: User = Depends(require_teacher),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> LabResponse:
    """This lab's own choice for some features ('on', 'off', or 'inherit' to
    follow the class); the features left out keep their choice."""
    lab = await _lab_access(db, lab_id, teacher)
    lab.feature_overrides = feature_service.updated_lab(
        lab.feature_overrides, body.model_dump(exclude_none=True))
    await db.commit()
    await db.refresh(lab)
    return (await feature_service.fill_effective(db, [LabResponse.model_validate(lab)]))[0]


# ===================================================================
# DELETE /api/teacher/labs/{lab_id}
# ===================================================================


@router.delete("/labs/{lab_id}", status_code=204)
async def soft_delete_lab(
    lab_id: uuid.UUID,
    teacher: User = Depends(require_teacher),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> Response:
    """Soft-delete a lab (its teacher, or an admin)."""
    lab = await _lab_access(db, lab_id, teacher)
    await lab_service.soft_delete_lab(db, lab)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ===================================================================
# GET /api/teacher/analytics/classes/{class_id}
# ===================================================================


@router.get("/analytics/classes/{class_id}/details")
async def get_class_analytics_details(
    class_id: uuid.UUID,
    lab_id: uuid.UUID | None = Query(None),
    teacher: User = Depends(require_teacher),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> dict:
    """Activity, students, quotas and answers that went wrong, for a class or one
    of its labs (its teacher, or an admin)."""
    await _class_access(db, class_id, teacher)
    if lab_id is not None:
        await lab_service.get_lab_in_class(db, lab_id, class_id)
    return await analytics_service.get_teaching_analytics(db, class_id, lab_id)


@router.post(
    "/labs/{lab_id}/test-session", response_model=TestSessionResponse, status_code=201
)
async def create_test_session(
    lab_id: uuid.UUID,
    teacher: User = Depends(require_teacher),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> TestSessionResponse:
    """Open a test-drive session on a lab the teacher owns — chat as a
    preview (membership bypassed, writes excluded from
    analytics and the citation report)."""
    lab = await lab_service.get_lab_by_id(db, lab_id)
    await lab_service.verify_lab_ownership(db, lab, teacher_id=teacher.id)
    session = await session_service.create_session(
        db, user_id=teacher.id, lab_id=lab_id, title="Test drive", is_test=True
    )
    await db.commit()
    return TestSessionResponse(session_id=session.id)
