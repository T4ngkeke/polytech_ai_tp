"""
routers/teacher.py — Teacher endpoints for Edu-LLM.

All DB/business logic is delegated to the services/ layer.
This router only: (1) authenticates, (2) verifies ownership, (3) calls service, (4) returns response.

Endpoints
---------
POST   /api/teacher/classes
GET    /api/teacher/classes
POST   /api/teacher/classes/{class_id}/labs
GET    /api/teacher/classes/{class_id}/labs
GET    /api/teacher/rule-items?level=&target_id=[&class_id=]   the rules of a class, lab or student
POST   /api/teacher/rule-items
PATCH  /api/teacher/rule-items/{item_id}
DELETE /api/teacher/rule-items/{item_id}
GET    /api/teacher/rule-presets
GET    /api/teacher/chat-history
GET    /api/teacher/students
DELETE /api/teacher/classes/{class_id}/students/{student_id}
POST   /api/teacher/classes/{class_id}/students/{student_id}/quota-refill
POST   /api/teacher/classes/{class_id}/quota-refill-all
GET    /api/teacher/quota-requests
POST   /api/teacher/classes/{class_id}/reset-code
PUT    /api/teacher/classes/{class_id}
DELETE /api/teacher/classes/{class_id}
PUT    /api/teacher/labs/{lab_id}
DELETE /api/teacher/labs/{lab_id}
POST   /api/teacher/labs/{lab_id}/documents, /classes/{class_id}/documents  PDF upload
GET    /api/teacher/labs/{lab_id}/documents, /classes/{class_id}/documents   document lists
DELETE /api/teacher/documents/{document_id}
GET    /api/teacher/analytics/classes/{class_id}
GET    /api/teacher/analytics/classes/{class_id}/details?lab_id=
POST   /api/teacher/labs/{lab_id}/reports
GET    /api/teacher/labs/{lab_id}/reports?student_id=
"""

import contextlib
import os
import uuid
from datetime import date, datetime, time, timedelta, timezone
from types import SimpleNamespace
from typing import Literal

from fastapi import (
    APIRouter, Depends, File, Form, HTTPException, Query, Response, UploadFile, status,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from backend.app.auth import require_teacher
from backend.app.config import settings
from backend.app.database import get_db
from backend.app.models import (
    AuditReport, Class, ClassStudent, Document, Lab, RuleItem, Session,
    UsageStat, User, UserRole,
)
from backend.app.schemas import (
    ClassAnalyticsResponse,
    ClassCreate,
    ClassResponse,
    ClassUpdateRequest,
    ExamModeRequest,
    InviteCodeResponse,
    ReportRequest,
    LabCreate,
    LabResponse,
    LabUpdateRequest,
    DocumentResponse,
    DocumentSummaryResponse,
    ExamFilesRequest,
    RuleItemCreate,
    RuleItemResponse,
    RuleItemUpdate,
    SessionWithMessagesResponse,
    SkillPresetResponse,
    SkillPresetUpsertRequest,
    StudentSummaryResponse,
    TestSessionResponse,
)
from backend.app.services import (
    class_service,
    document_service,
    lab_service,
    rule_service,
    session_service,
    skill_preset_service,
    analytics_service,
    quota_service,
)
from backend.app.services.citation_report import class_citation_report, lab_citation_report

router = APIRouter(prefix="/api/teacher", tags=["teacher"])


def get_storage_root() -> str:
    """Storage root for uploaded documents (overridable in tests)."""
    return settings.DOCUMENTS_STORAGE_ROOT


async def _verify_class_ownership(db: AsyncSession, class_id: uuid.UUID, teacher: User) -> Class:
    """Fetch a class (404 if missing) and assert the teacher owns it (403 otherwise)."""
    cls = await class_service.get_class_by_id(db, class_id)
    if cls.teacher_id != teacher.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized")
    return cls


async def _rule_class(
    db: AsyncSession, level: str, target_id: uuid.UUID, class_id: uuid.UUID | None, teacher: User
) -> uuid.UUID:
    """The class a rule target belongs to, when it is one of the teacher's.

    These rules go into the chat prompt, so a teacher may touch only targets in
    their own classes (403 otherwise). A student's rules belong to one class:
    class_id is required, and the student must be in that class.
    """
    owned = None
    if level == "class":
        owned = await db.scalar(select(Class.id).where(
            Class.id == target_id, Class.teacher_id == teacher.id, Class.is_deleted.is_(False)))
    elif level == "lab":
        owned = await db.scalar(select(Lab.class_id).join(Class, Lab.class_id == Class.id).where(
            Lab.id == target_id, Class.teacher_id == teacher.id, Class.is_deleted.is_(False)))
    elif level == "student" and class_id is not None:
        owned = await db.scalar(select(Class.id).join(ClassStudent, ClassStudent.class_id == Class.id).where(
            Class.id == class_id, Class.teacher_id == teacher.id, Class.is_deleted.is_(False),
            ClassStudent.student_id == target_id))
    if owned is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized for this rule target")
    return owned


async def _owned_item(db: AsyncSession, item_id: uuid.UUID, teacher: User) -> RuleItem:
    item = await db.scalar(select(RuleItem).join(Class, Class.id == RuleItem.class_id).where(
        RuleItem.id == item_id, Class.teacher_id == teacher.id))
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Rule not found")
    return item


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
    return LabResponse.model_validate(lab)


# ===================================================================
# GET /api/teacher/classes/{class_id}/labs
# ===================================================================


@router.get("/classes/{class_id}/labs", response_model=list[LabResponse])
async def list_labs(
    class_id: uuid.UUID,
    teacher: User = Depends(require_teacher),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> list[LabResponse]:
    """List all labs for a class owned by the teacher."""
    await _verify_class_ownership(db, class_id, teacher)
    labs = await lab_service.list_labs_for_class(db, class_id=class_id)
    return [LabResponse.model_validate(l) for l in labs]


# ===================================================================
# POST /api/teacher/labs/{lab_id}/documents
# ===================================================================


@router.post("/labs/{lab_id}/documents", response_model=DocumentResponse, status_code=201)
async def upload_document(
    lab_id: uuid.UUID,
    response: Response,
    file: UploadFile = File(...),
    shared: bool = Form(False),
    mode: str = Form("new"),
    replace_document_id: uuid.UUID | None = Form(None),
    expected_generation: int | None = Form(None, ge=0),
    teacher: User = Depends(require_teacher),
    db: AsyncSession = Depends(get_db, scope="function"),
    storage_root: str = Depends(get_storage_root),
) -> DocumentResponse:
    """Upload a course document (PDF, Markdown or HTML) to a lab the teacher
    owns; enqueues ingestion.

    The form asks for the file and nothing else that matters: the language is
    found from the text when it is indexed. `shared` (older clients) makes the
    document class-wide, as an upload to /classes/{class_id}/documents does.
    There is no type to declare and no audience to pick — everything uploaded is
    course material the whole class can read.
    """
    lab = await lab_service.get_lab_by_id(db, lab_id)
    await lab_service.verify_lab_ownership(db, lab, teacher_id=teacher.id)
    return await _store_upload(db, response, scope=lab, shared=shared, file=file,
                               mode=mode, replace_document_id=replace_document_id,
                               expected_generation=expected_generation, teacher=teacher,
                               storage_root=storage_root)


@router.post("/classes/{class_id}/documents", response_model=DocumentResponse, status_code=201)
async def upload_class_document(
    class_id: uuid.UUID,
    response: Response,
    file: UploadFile = File(...),
    mode: str = Form("new"),
    replace_document_id: uuid.UUID | None = Form(None),
    expected_generation: int | None = Form(None, ge=0),
    teacher: User = Depends(require_teacher),
    db: AsyncSession = Depends(get_db, scope="function"),
    storage_root: str = Depends(get_storage_root),
) -> DocumentResponse:
    """Upload a class-wide course document (lab_id NULL): every lab of the class
    reads it, e.g. the course handout shared by all TPs."""
    await _verify_class_ownership(db, class_id, teacher)
    # The upload code needs a class id and a lab id; a class-wide document has none.
    scope = SimpleNamespace(id=None, class_id=class_id)
    return await _store_upload(db, response, scope=scope, shared=True, file=file,
                               mode=mode, replace_document_id=replace_document_id,
                               expected_generation=expected_generation, teacher=teacher,
                               storage_root=storage_root)


async def _store_upload(db, response, *, scope, shared, file, mode,
                        replace_document_id, expected_generation, teacher, storage_root):
    """One upload in a lab (scope: the Lab) or class-wide (shared). PDF only: it
    goes to the structured path (MinerU, then indexing)."""
    name = (file.filename or "").lower()
    if name.endswith(".odp"):
        # The ODP reader was removed on 2026-10-15: export the slides to PDF.
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="odp_not_supported")
    if not name.endswith(".pdf"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="only_pdf")
    if not (settings.PDF_STRUCTURED_INGEST_ENABLED and settings.STRUCTURED_INGEST_ENABLED):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="structured_ingest_disabled")
    return await _upload_structured(
        db, response, lab=scope, file=file, shared=shared, mode=mode,
        replace_document_id=replace_document_id, expected_generation=expected_generation,
        teacher=teacher, storage_root=storage_root)


async def _upload_structured(db, response, *, lab, file, shared, mode,
                             replace_document_id, expected_generation, teacher, storage_root):
    """A structured PDF upload: new document or replacement, one parse job each."""
    from backend.app.services import structured_upload
    from backend.app.services.document_access import DocumentNotFound
    from backend.app.services.document_versions import VersionConflict

    # With the flag off no new structured content is written; legacy formats
    # keep working and already stored structured documents stay readable.
    if not settings.STRUCTURED_INGEST_ENABLED:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="structured_ingest_disabled")
    limit = structured_upload.max_upload_bytes()
    content = await file.read(limit + 1)
    if len(content) > limit:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="file_too_large")
    try:
        result = await structured_upload.upload_pdf(
            db, teacher_id=teacher.id, lab=lab, shared=shared, filename=file.filename,
            content=content, storage_root=storage_root, mode=mode,
            replace_document_id=replace_document_id, expected_generation=expected_generation)
    except structured_upload.UploadRejected as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from None
    except DocumentNotFound:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found") from None
    except VersionConflict as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from None
    # Same content sent again to the same document: nothing new was created.
    response.status_code = status.HTTP_201_CREATED if result.created else status.HTTP_200_OK
    doc, rev = result.document, result.revision
    return DocumentResponse(
        id=doc.id, class_id=doc.class_id, lab_id=doc.lab_id, filename=doc.filename,
        status=doc.status.value, structured=True, generation=doc.generation,
        source_version_id=rev.source_version_id, revision_id=rev.id,
        processing_state=rev.processing_state, job_id=result.job.id if result.job else None)


# ===================================================================
# Teacher ingestion visibility
# ===================================================================


@router.get("/labs/{lab_id}/documents", response_model=list[DocumentSummaryResponse])
async def list_lab_documents(
    lab_id: uuid.UUID,
    teacher: User = Depends(require_teacher),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> list[DocumentSummaryResponse]:
    """List the documents a lab uses with status + processing summary (ownership
    enforced): the class-wide documents first (scope "class"), then the lab's own."""
    lab = await lab_service.get_lab_by_id(db, lab_id)
    await lab_service.verify_lab_ownership(db, lab, teacher_id=teacher.id)
    return _document_summaries(await document_service.list_lab_documents(db, lab_id, lab.class_id))


@router.get("/classes/{class_id}/documents", response_model=list[DocumentSummaryResponse])
async def list_class_documents(
    class_id: uuid.UUID,
    teacher: User = Depends(require_teacher),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> list[DocumentSummaryResponse]:
    """The class-wide documents of a class the teacher owns."""
    await _verify_class_ownership(db, class_id, teacher)
    return _document_summaries(await document_service.list_class_documents(db, class_id))


def _document_summaries(rows) -> list[DocumentSummaryResponse]:
    return [
        DocumentSummaryResponse(
            id=r["doc"].id,
            filename=r["doc"].filename,
            status=r["doc"].status.value,
            page_count=r["doc"].page_count,
            error_message=r["doc"].error_message,
            chunk_count=r["chunk_count"],
            ingest_report=r["doc"].ingest_report,
            lifecycle=r["doc"].lifecycle,
            generation=r["doc"].generation,
            structured=r["doc"].structured,
            current_revision_id=r["doc"].current_revision_id,
            latest_revision=r["latest_revision"],
            scope=r["scope"],
        )
        for r in rows
    ]


async def _owned_document_or_404(db, document_id, teacher):
    doc = await document_service.get_owned_document(db, document_id, teacher.id)
    if doc is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
    return doc


@router.delete(
    "/documents/{document_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_document(
    document_id: uuid.UUID,
    expected_generation: int | None = Query(default=None, ge=0),
    teacher: User = Depends(require_teacher),
    db: AsyncSession = Depends(get_db, scope="function"),
):
    """Delete an uploaded document and everything derived from it — its chunks
    and ingestion jobs — plus the stored file. Ownership-checked."""
    doc = await _owned_document_or_404(db, document_id, teacher)
    if doc.structured:
        from fastapi.responses import JSONResponse
        from backend.app.services.document_cleanup import mark_deleted
        from backend.app.services.document_versions import VersionConflict
        if expected_generation is None:
            raise HTTPException(422, "expected_generation_required")
        try:
            await mark_deleted(db, document_id=doc.id, teacher_id=teacher.id,
                               expected_generation=expected_generation)
            await db.commit()
        except VersionConflict as exc:
            await db.rollback()
            raise HTTPException(409, str(exc)) from None
        return JSONResponse(status_code=202, content={"document_id": str(doc.id), "state": "pending"})
    storage_path = await document_service.delete_document(db, doc)
    await db.commit()
    # Best-effort file cleanup after the row is gone (never fail the request for a
    # missing/already-removed file).
    with contextlib.suppress(OSError):
        os.remove(storage_path)


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


# ===================================================================
# Rules: a list per class, lab or student (rule_service)
# ===================================================================


@router.get("/rule-items", response_model=list[RuleItemResponse])
async def list_rule_items(
    level: Literal["class", "lab", "student"],
    target_id: uuid.UUID,
    class_id: uuid.UUID | None = None,
    teacher: User = Depends(require_teacher),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> list[RuleItemResponse]:
    """The rules of a class, a lab, or a student in one class (class_id), in order."""
    owned_class = await _rule_class(db, level, target_id, class_id, teacher)
    items = await rule_service.list_items(db, level=level, target_id=target_id, class_id=owned_class)
    return [RuleItemResponse.model_validate(i) for i in items]


@router.post("/rule-items", response_model=RuleItemResponse, status_code=201)
async def add_rule_item(
    body: RuleItemCreate,
    teacher: User = Depends(require_teacher),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> RuleItemResponse:
    """Add a rule at the end of the list. 409 too_many_rules past 30."""
    owned_class = await _rule_class(db, body.level, body.target_id, body.class_id, teacher)
    try:
        item = await rule_service.add_item(db, level=body.level, target_id=body.target_id,
                                           class_id=owned_class, text=body.text, is_active=body.is_active)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from None
    await db.commit()
    return RuleItemResponse.model_validate(item)


@router.patch("/rule-items/{item_id}", response_model=RuleItemResponse)
async def update_rule_item(
    item_id: uuid.UUID,
    body: RuleItemUpdate,
    teacher: User = Depends(require_teacher),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> RuleItemResponse:
    """Change the text of a rule, or switch it on or off."""
    item = await _owned_item(db, item_id, teacher)
    item = await rule_service.update_item(db, item, text=body.text, is_active=body.is_active)
    await db.commit()
    return RuleItemResponse.model_validate(item)


@router.delete("/rule-items/{item_id}", status_code=204)
async def delete_rule_item(
    item_id: uuid.UUID,
    teacher: User = Depends(require_teacher),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> Response:
    item = await _owned_item(db, item_id, teacher)
    await db.delete(item)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/rule-presets")
async def rule_presets(teacher: User = Depends(require_teacher)) -> list[dict]:
    """Ready-made rules to add with one click (rule_service.BUILT_IN)."""
    return rule_service.BUILT_IN


# ===================================================================
# Skill presets — a teacher's own rules to reuse (the rule lists add them)
# ===================================================================


@router.get("/skill-presets", response_model=list[SkillPresetResponse])
async def list_skill_presets(
    teacher: User = Depends(require_teacher),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> list[SkillPresetResponse]:
    presets = await skill_preset_service.list_presets(db, owner_teacher_id=teacher.id)
    return [SkillPresetResponse.model_validate(p) for p in presets]


@router.post("/skill-presets", response_model=SkillPresetResponse, status_code=201)
async def create_skill_preset(
    body: SkillPresetUpsertRequest,
    teacher: User = Depends(require_teacher),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> SkillPresetResponse:
    preset = await skill_preset_service.create_preset(
        db, owner_teacher_id=teacher.id, name=body.name, content=body.content)
    return SkillPresetResponse.model_validate(preset)


@router.put("/skill-presets/{preset_id}", response_model=SkillPresetResponse)
async def update_skill_preset(
    preset_id: uuid.UUID,
    body: SkillPresetUpsertRequest,
    teacher: User = Depends(require_teacher),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> SkillPresetResponse:
    try:
        preset = await skill_preset_service.update_preset(
            db, owner_teacher_id=teacher.id, preset_id=preset_id,
            name=body.name, content=body.content)
    except LookupError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Preset not found")
    except PermissionError:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not the owner")
    return SkillPresetResponse.model_validate(preset)


@router.delete("/skill-presets/{preset_id}", status_code=204)
async def delete_skill_preset(
    preset_id: uuid.UUID,
    teacher: User = Depends(require_teacher),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> Response:
    try:
        await skill_preset_service.delete_preset(
            db, owner_teacher_id=teacher.id, preset_id=preset_id)
    except LookupError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Preset not found")
    except PermissionError:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not the owner")
    return Response(status_code=status.HTTP_204_NO_CONTENT)




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
# GET /api/teacher/labs/{lab_id}/citation-report
# ===================================================================


@router.get("/labs/{lab_id}/citation-report")
async def get_citation_report(
    lab_id: uuid.UUID,
    start: date | None = Query(None, description="First day included (UTC)."),
    end: date | None = Query(None, description="Last day included (UTC)."),
    teacher: User = Depends(require_teacher),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> dict:
    """Which course files, pages and passages the lab's research answers cite,
    with the students and their questions (audit; ownership enforced).
    Shape: backend/app/services/citation_report.py."""
    lab = await lab_service.get_lab_by_id(db, lab_id)
    await lab_service.verify_lab_ownership(db, lab, teacher_id=teacher.id)
    return await lab_citation_report(db, lab, **_report_period(start, end))


@router.get("/classes/{class_id}/citation-report")
async def get_class_citation_report(
    class_id: uuid.UUID,
    start: date | None = Query(None, description="First day included (UTC)."),
    end: date | None = Query(None, description="Last day included (UTC)."),
    teacher: User = Depends(require_teacher),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> dict:
    """The lab report for all labs of the class together; each question names its lab."""
    await _verify_class_ownership(db, class_id, teacher)
    labs = await lab_service.list_labs_for_class(db, class_id)
    return await class_citation_report(db, class_id, labs, **_report_period(start, end))


def _report_period(start: date | None, end: date | None) -> dict:
    """[start, end] in days (UTC) as the report's [start, end) datetimes."""
    if start and end and end < start:
        raise HTTPException(status_code=422, detail="end is before start")
    day = lambda d: datetime.combine(d, time.min, tzinfo=timezone.utc)
    return {'start': day(start) if start else None, 'end': day(end) + timedelta(days=1) if end else None}


# ===================================================================
# GET /api/teacher/students
# ===================================================================


@router.get("/students", response_model=list[StudentSummaryResponse])
async def list_students(
    _teacher: User = Depends(require_teacher),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> list[StudentSummaryResponse]:
    """List active students with their current daily token consumption."""
    result = await db.execute(
        select(User).where(User.role == UserRole.student, User.is_deleted.is_(False))
    )
    students = result.scalars().all()
    return await _build_student_summaries(db, students)


# ===================================================================
# GET /api/teacher/classes/{class_id}/students
# ===================================================================


@router.get("/classes/{class_id}/students", response_model=list[StudentSummaryResponse])
async def list_class_students(
    class_id: uuid.UUID,
    teacher: User = Depends(require_teacher),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> list[StudentSummaryResponse]:
    """List active students enrolled in a specific class."""
    await _verify_class_ownership(db, class_id, teacher)
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
    allowed in the exam (PUT .../exam-files); with none, every lab answers in
    direct mode only. The chat endpoint enforces it."""
    cls = await _verify_class_ownership(db, class_id, teacher)
    cls.exam_mode = body.exam_mode
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
    """Soft-delete a class (ownership enforced)."""
    cls = await _verify_class_ownership(db, class_id, teacher)
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
    db: AsyncSession = Depends(get_db, scope="function"),
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
    db: AsyncSession = Depends(get_db, scope="function"),
) -> ClassAnalyticsResponse:
    """Hierarchical token usage: class → lab → student breakdown."""
    cls = await _verify_class_ownership(db, class_id, teacher)
    result = await analytics_service.get_class_analytics(db, class_id=class_id, class_name=cls.name)
    return ClassAnalyticsResponse.from_analytics(result)


@router.get("/analytics/classes/{class_id}/details")
async def get_class_analytics_details(
    class_id: uuid.UUID,
    lab_id: uuid.UUID | None = Query(None),
    teacher: User = Depends(require_teacher),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> dict:
    """Activity, students, quotas and answers that went wrong, for a class or one of its labs."""
    await _verify_class_ownership(db, class_id, teacher)
    if lab_id is not None:
        lab = await lab_service.get_lab_by_id(db, lab_id)
        if lab.class_id != class_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lab not found")
    return await analytics_service.get_teaching_analytics(db, class_id, lab_id)


# ===================================================================
# Reports a model writes for the teacher (a lab, or one student in it)
# ===================================================================


async def _report_lab(db, lab_id, teacher, student_id):
    lab = await lab_service.get_lab_by_id(db, lab_id)
    await lab_service.verify_lab_ownership(db, lab, teacher_id=teacher.id)
    if student_id is not None and not await db.scalar(select(Session.id).where(
            Session.lab_id == lab.id, Session.user_id == student_id).limit(1)):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No conversation of this student in this lab")
    return lab


async def _latest_report(db, lab_id, student_id):
    return await db.scalar(select(AuditReport).where(
        AuditReport.lab_id == lab_id,
        AuditReport.student_id == student_id if student_id else AuditReport.student_id.is_(None))
        .order_by(AuditReport.created_at.desc()).limit(1))


@router.post("/labs/{lab_id}/reports", status_code=202)
async def create_report(
    lab_id: uuid.UUID,
    body: ReportRequest,
    teacher: User = Depends(require_teacher),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> dict:
    """Start a report on the lab, or on one student with `student_id`. The
    model writes it in the background (tens of seconds); poll the GET."""
    from backend.app.agent.model import ResearchModel
    from backend.app.services import teaching_reports
    from backend.app.services.model_clients import get_llm_config, make_embed_fn
    from backend.app.services.model_routing import Endpoint

    lab = await _report_lab(db, lab_id, teacher, body.student_id)
    latest = await _latest_report(db, lab.id, body.student_id)
    if latest is not None and latest.status == "running" and not teaching_reports.stale(latest):
        return teaching_reports.public(latest)          # one at a time
    config = await get_llm_config(db)
    agent = Endpoint(config["agent_base_url"], config["agent_api_key"], config["agent_model"])
    if not agent.model or not agent.base_url:
        raise HTTPException(503, "agent_model_not_configured")
    report = AuditReport(lab_id=lab.id, student_id=body.student_id, created_by=teacher.id)
    db.add(report)
    await db.commit()
    await db.refresh(report)
    teaching_reports.start_report(
        async_sessionmaker(db.bind, expire_on_commit=False), report.id, ResearchModel(agent),
        model_name=agent.model,
        embed_fn=make_embed_fn(config) if config.get("embedding_model") else None)
    return teaching_reports.public(report)


@router.get("/labs/{lab_id}/reports")
async def get_report(
    lab_id: uuid.UUID,
    student_id: uuid.UUID | None = Query(None),
    teacher: User = Depends(require_teacher),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> dict:
    """The latest report on the lab (or on one student), or `{"report": null}`."""
    from backend.app.services import teaching_reports
    lab = await _report_lab(db, lab_id, teacher, student_id)
    return {"report": teaching_reports.public(await _latest_report(db, lab.id, student_id))}
