"""
routers/teacher.py — Teacher endpoints for Edu-LLM v7.

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
PUT    /api/teacher/documents/{document_id}/chunks/{chunk_id}      [v7.2] edit + re-index
PUT    /api/teacher/documents/{document_id}/exercises/{exercise_id} [v7.2] edit
GET    /api/teacher/analytics/classes/{class_id}
"""

import contextlib
import os
import uuid
from datetime import date, datetime, time, timedelta, timezone

from fastapi import (
    APIRouter, Depends, File, Form, HTTPException, Query, Response, UploadFile, status,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.app.auth import require_teacher
from backend.app.config import settings
from backend.app.database import get_db
from backend.app.models import (
    Class, ClassStudent, Lab, RuleLevel, Session, DocumentReview,
    SystemConfig, UsageStat, User, UserRole,
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
    ChunkResponse,
    ChunkUpdateRequest,
    DocumentResponse,
    DocumentReviewRequest, DocumentReviewResponse,
    DocumentSummaryResponse,
    ApplySkillRequest,
    RuleResponse,
    RuleUpsertRequest,
    SessionWithMessagesResponse,
    SkillPresetResponse,
    SkillPresetUpsertRequest,
    StudentSummaryResponse,
    TestSessionResponse,
)
from backend.app.services import (
    class_service,
    document_service,
    document_reviews,
    lab_service,
    router_service,
    rule_service,
    session_service,
    skill_preset_service,
    analytics_service,
)
from backend.app.services.citation_report import lab_citation_report

router = APIRouter(prefix="/api/teacher", tags=["teacher"])


def get_storage_root() -> str:
    """Storage root for uploaded documents (overridable in tests)."""
    return settings.DOCUMENTS_STORAGE_ROOT


async def get_embed_fn(db: AsyncSession = Depends(get_db)) -> document_service.EmbedFn:
    """[v7.2] Build the config-driven embedder used to re-index an edited chunk.

    A FastAPI dependency so tests can override it with a fake (no live embedding
    server needed), mirroring `get_db` / `get_storage_root`.
    """
    from openai import AsyncOpenAI

    from backend.app.services.model_routing import resolve_model_routing

    rows = (await db.execute(select(SystemConfig))).scalars().all()
    routing = resolve_model_routing({r.key: r.value for r in rows})
    client = AsyncOpenAI(
        api_key=routing.embedding.api_key, base_url=routing.embedding.base_url
    )

    async def embed(texts: list[str]) -> list[list[float]]:
        resp = await client.embeddings.create(
            model=routing.embedding.model, input=texts, encoding_format="float"
        )
        return [item.embedding for item in resp.data]

    return embed


async def _verify_class_ownership(db: AsyncSession, class_id: uuid.UUID, teacher: User) -> Class:
    """Fetch a class (404 if missing) and assert the teacher owns it (403 otherwise)."""
    cls = await class_service.get_class_by_id(db, class_id)
    if cls.teacher_id != teacher.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized")
    return cls


async def _verify_rule_target_ownership(
    db: AsyncSession, level: RuleLevel, target_id: uuid.UUID, teacher: User
) -> None:
    """[v7.2] A teacher may only read/write rules for a target inside one of their
    own classes — these rules are injected into the chat prompt, so cross-tenant
    writes would let one teacher hijack another's tutor behavior."""
    if level == RuleLevel.class_:
        owned = (await db.execute(
            select(Class.id).where(Class.id == target_id, Class.teacher_id == teacher.id)
        )).scalar_one_or_none()
    elif level == RuleLevel.lab:
        owned = (await db.execute(
            select(Lab.id).join(Class, Lab.class_id == Class.id)
            .where(Lab.id == target_id, Class.teacher_id == teacher.id)
        )).scalar_one_or_none()
    else:  # student — must be enrolled in a class the teacher owns
        owned = (await db.execute(
            select(ClassStudent.student_id).join(Class, ClassStudent.class_id == Class.id)
            .where(ClassStudent.student_id == target_id, Class.teacher_id == teacher.id)
        )).first()
    if owned is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized for this rule target",
        )


async def _build_student_summaries(
    db: AsyncSession, students: list[User]
) -> list[StudentSummaryResponse]:
    """Attach today's token usage to each student (per-student lookup)."""
    today = date.today()
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
    db: AsyncSession = Depends(get_db),
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
    language: str = Form("fr"),
    shared: bool = Form(False),
    mode: str = Form("new"),
    replace_document_id: uuid.UUID | None = Form(None),
    expected_generation: int | None = Form(None, ge=0),
    teacher: User = Depends(require_teacher),
    db: AsyncSession = Depends(get_db),
    storage_root: str = Depends(get_storage_root),
) -> DocumentResponse:
    """Upload a course document (PDF, Markdown or HTML) to a lab the teacher
    owns; enqueues ingestion.

    The form asks for the file and nothing else that matters: `language` selects
    the BM25 text-search config and `shared` makes the document class-wide.
    There is no type to declare and no audience to pick — everything uploaded is
    course material the whole class can read.
    """
    lab = await lab_service.get_lab_by_id(db, lab_id)
    await lab_service.verify_lab_ownership(db, lab, teacher_id=teacher.id)

    if (file.filename or "").lower().endswith(".pdf") and settings.PDF_STRUCTURED_INGEST_ENABLED and settings.STRUCTURED_INGEST_ENABLED:
        return await _upload_structured(
            db, response, lab=lab, file=file, language=language, shared=shared, mode=mode,
            replace_document_id=replace_document_id, expected_generation=expected_generation,
            teacher=teacher, storage_root=storage_root, fmt="pdf")
    if (file.filename or "").lower().endswith(".odp"):
        if settings.PDF_STRUCTURED_INGEST_ENABLED:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="odp_not_supported")
        return await _upload_structured(
            db, response, lab=lab, file=file, language=language, shared=shared, mode=mode,
            replace_document_id=replace_document_id, expected_generation=expected_generation,
            teacher=teacher, storage_root=storage_root, fmt="odp")
    # Replacing a file is part of the structured path only (M2-B, ODP).
    if mode != "new" or replace_document_id is not None or expected_generation is not None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="replace_requires_structured_odp")

    # PDF (slides exported to PDF), Markdown or HTML — no other formats.
    if not (file.filename or "").lower().endswith((".pdf", ".md", ".html", ".htm")):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only PDF, Markdown or HTML uploads are supported. Export slides to PDF first.",
        )

    # Shared → lab_id NULL, reachable from every lab of the class.
    doc_lab_id = None if shared else lab.id

    content = await file.read()
    doc = await document_service.create_document(
        db,
        class_id=lab.class_id,
        lab_id=doc_lab_id,
        filename=file.filename,
        content=content,
        uploaded_by=teacher.id,
        storage_root=storage_root,
        language=language,
    )
    return DocumentResponse.model_validate(doc)


async def _upload_structured(db, response, *, lab, file, language, shared, mode,
                             replace_document_id, expected_generation, teacher, storage_root, fmt):
    """[M2-B] ODP upload: new document or replacement, one parse job each."""
    from backend.app.services import structured_upload
    from backend.app.services.document_access import DocumentNotFound
    from backend.app.services.document_versions import VersionConflict

    # With the flag off no new structured content is written; legacy formats
    # keep working and already stored structured documents stay readable.
    if not settings.STRUCTURED_INGEST_ENABLED:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="structured_ingest_disabled")
    limit = structured_upload.max_upload_bytes(fmt=fmt)
    content = await file.read(limit + 1)
    if len(content) > limit:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="file_too_large")
    try:
        result = await (structured_upload.upload_pdf if fmt == "pdf" else structured_upload.upload_odp)(
            db, teacher_id=teacher.id, lab=lab, shared=shared, filename=file.filename,
            content=content, language=language, storage_root=storage_root, mode=mode,
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
    db: AsyncSession = Depends(get_db),
) -> list[DocumentSummaryResponse]:
    """List a lab's documents with status + processing summary (ownership enforced)."""
    lab = await lab_service.get_lab_by_id(db, lab_id)
    await lab_service.verify_lab_ownership(db, lab, teacher_id=teacher.id)
    rows = await document_service.list_lab_documents(db, lab_id)
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
        )
        for r in rows
    ]


async def _owned_document_or_404(db, document_id, teacher):
    doc = await document_service.get_owned_document(db, document_id, teacher.id)
    if doc is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
    return doc


@router.get("/documents/{document_id}/chunks", response_model=list[ChunkResponse])
async def get_document_chunks(
    document_id: uuid.UUID,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    teacher: User = Depends(require_teacher),
    db: AsyncSession = Depends(get_db),
) -> list[ChunkResponse]:
    """Paginated chunk inspector for a document the teacher owns."""
    await _owned_document_or_404(db, document_id, teacher)
    chunks = await document_service.get_document_chunks(
        db, document_id, offset=offset, limit=limit
    )
    return [ChunkResponse.model_validate(c) for c in chunks]


@router.put("/documents/{document_id}/chunks/{chunk_id}", response_model=ChunkResponse)
async def update_document_chunk(
    document_id: uuid.UUID,
    chunk_id: uuid.UUID,
    body: ChunkUpdateRequest,
    teacher: User = Depends(require_teacher),
    db: AsyncSession = Depends(get_db),
    embed_fn: document_service.EmbedFn = Depends(get_embed_fn),
) -> ChunkResponse:
    """[v7.2] Correct a mis-split chunk's text. The edit re-embeds + re-indexes the
    chunk so retrieval reflects the correction. Ownership enforced via the document."""
    owned = await _owned_document_or_404(db, document_id, teacher)
    if owned.structured:
        raise HTTPException(409, "edit_complete_block_required")
    chunk = await document_service.get_chunk_in_document(db, document_id, chunk_id)
    if chunk is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Chunk not found")
    chunk = await document_service.update_chunk(db, chunk, content=body.content, embed_fn=embed_fn)
    return ChunkResponse.model_validate(chunk)


@router.delete(
    "/documents/{document_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_document(
    document_id: uuid.UUID,
    expected_generation: int | None = Query(default=None, ge=0),
    teacher: User = Depends(require_teacher),
    db: AsyncSession = Depends(get_db),
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
    db: AsyncSession = Depends(get_db),
) -> TestSessionResponse:
    """[v8.0 §11A] Open a test-drive session on a lab the teacher owns — chat as a
    preview (membership bypassed, writes excluded from
    analytics/LearnerProfile)."""
    lab = await lab_service.get_lab_by_id(db, lab_id)
    await lab_service.verify_lab_ownership(db, lab, teacher_id=teacher.id)
    session = await session_service.create_session(
        db, user_id=teacher.id, lab_id=lab_id, title="Test drive", is_test=True
    )
    await db.commit()
    return TestSessionResponse(session_id=session.id)


@router.put("/rules", response_model=RuleResponse)
async def upsert_rule(
    body: RuleUpsertRequest,
    teacher: User = Depends(require_teacher),
    db: AsyncSession = Depends(get_db),
) -> RuleResponse:
    """Create or update a 3-tier rule based on (level, target_id)."""
    await _verify_rule_target_ownership(db, body.level, body.target_id, teacher)
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
    teacher: User = Depends(require_teacher),
    db: AsyncSession = Depends(get_db),
) -> list[RuleResponse]:
    """List rules with optional level and target_id filters."""
    # [v7.2] When asking for a specific target's rule, verify the teacher owns it.
    if target_id is not None and level is not None:
        await _verify_rule_target_ownership(db, RuleLevel(level), target_id, teacher)
    rules = await rule_service.list_rules(db, level=level, target_id=target_id)
    return [RuleResponse.model_validate(r) for r in rules]


# ===================================================================
# [v7.2] Skill presets — instructor-style library + apply-to-class
# ===================================================================


@router.get("/skill-presets", response_model=list[SkillPresetResponse])
async def list_skill_presets(
    teacher: User = Depends(require_teacher),
    db: AsyncSession = Depends(get_db),
) -> list[SkillPresetResponse]:
    presets = await skill_preset_service.list_presets(db, owner_teacher_id=teacher.id)
    return [SkillPresetResponse.model_validate(p) for p in presets]


@router.post("/skill-presets", response_model=SkillPresetResponse, status_code=201)
async def create_skill_preset(
    body: SkillPresetUpsertRequest,
    teacher: User = Depends(require_teacher),
    db: AsyncSession = Depends(get_db),
) -> SkillPresetResponse:
    preset = await skill_preset_service.create_preset(
        db, owner_teacher_id=teacher.id, name=body.name, content=body.content)
    return SkillPresetResponse.model_validate(preset)


@router.put("/skill-presets/{preset_id}", response_model=SkillPresetResponse)
async def update_skill_preset(
    preset_id: uuid.UUID,
    body: SkillPresetUpsertRequest,
    teacher: User = Depends(require_teacher),
    db: AsyncSession = Depends(get_db),
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
    db: AsyncSession = Depends(get_db),
) -> Response:
    try:
        await skill_preset_service.delete_preset(
            db, owner_teacher_id=teacher.id, preset_id=preset_id)
    except LookupError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Preset not found")
    except PermissionError:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not the owner")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/classes/{class_id}/skill", response_model=RuleResponse)
async def apply_class_skill(
    class_id: uuid.UUID,
    body: ApplySkillRequest,
    teacher: User = Depends(require_teacher),
    db: AsyncSession = Depends(get_db),
) -> RuleResponse:
    """Apply a preset (snapshot-copy) or ad-hoc content to a class's skill rule."""
    await _verify_class_ownership(db, class_id, teacher)

    if body.preset_id is not None:
        try:
            rule = await skill_preset_service.apply_preset_to_class(
                db, owner_teacher_id=teacher.id, class_id=class_id, preset_id=body.preset_id)
        except LookupError:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Preset not found")
        except PermissionError:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not the owner")
    elif body.content and body.content.strip():
        rule = await rule_service.upsert_rule(
            db, level=RuleLevel.class_, target_id=class_id, rules_text=body.content)
    else:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="Provide preset_id or non-empty content")

    return RuleResponse.model_validate(rule)


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
    db: AsyncSession = Depends(get_db),
) -> list[SessionWithMessagesResponse]:
    """Fetch chat sessions and messages with flexible filters for audit.

    Includes student soft-deleted sessions (is_deleted=True): audit sees everything,
    and the `is_deleted` flag on each row lets the UI mark the hidden ones. Only an
    admin hard-delete actually removes a session.
    """
    # [v7.2] Ownership scope (applied unconditionally so a supplied session_id /
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
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Which course files, pages and passages the lab's research answers cite,
    with the students and their questions (audit; ownership enforced).
    Shape: backend/app/services/citation_report.py."""
    lab = await lab_service.get_lab_by_id(db, lab_id)
    await lab_service.verify_lab_ownership(db, lab, teacher_id=teacher.id)
    if start and end and end < start:
        raise HTTPException(status_code=422, detail="end is before start")
    day = lambda d: datetime.combine(d, time.min, tzinfo=timezone.utc)
    return await lab_citation_report(db, lab, start=day(start) if start else None,
                                     end=day(end) + timedelta(days=1) if end else None)


# ===================================================================
# GET /api/teacher/students
# ===================================================================


@router.get("/students", response_model=list[StudentSummaryResponse])
async def list_students(
    _teacher: User = Depends(require_teacher),
    db: AsyncSession = Depends(get_db),
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
    db: AsyncSession = Depends(get_db),
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
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Remove a student from a class (ownership enforced)."""
    await _verify_class_ownership(db, class_id, teacher)
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
    db: AsyncSession = Depends(get_db),
) -> ClassResponse:
    """Rename a class (ownership enforced)."""
    cls = await _verify_class_ownership(db, class_id, teacher)
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
    cls = await _verify_class_ownership(db, class_id, teacher)
    result = await analytics_service.get_class_analytics(db, class_id=class_id, class_name=cls.name)
    return ClassAnalyticsResponse.from_analytics(result)


# Visual review endpoints: scope comes exclusively from the owned document.


def _review_conflict(exc):
    # Machine-readable code so the page can say what happened and what helps.
    return HTTPException(409,{'code':exc.code,'message':str(exc)})


@router.get('/documents/{document_id}/reviews', response_model=list[DocumentReviewResponse])
async def list_document_reviews(document_id: uuid.UUID,
    teacher: User = Depends(require_teacher), db: AsyncSession = Depends(get_db)):
    await _owned_document_or_404(db,document_id,teacher)
    # Legacy PDF reviews only; structured ones are listed per revision (M2-C).
    rows=(await db.scalars(select(DocumentReview).where(DocumentReview.document_id==document_id,
        DocumentReview.revision_id.is_(None)).order_by(DocumentReview.page_no,DocumentReview.id))).all()
    return [DocumentReviewResponse.model_validate(row) for row in rows]


@router.get('/documents/{document_id}/reviews/{review_id}/image')
async def document_review_image(document_id: uuid.UUID, review_id: uuid.UUID,
    teacher: User = Depends(require_teacher), db: AsyncSession = Depends(get_db)):
    doc=await _owned_document_or_404(db,document_id,teacher)
    if doc.structured:
        raise HTTPException(409,'structured_review_endpoint_required')
    row=await db.scalar(select(DocumentReview).where(DocumentReview.id==review_id,
        DocumentReview.document_id==document_id))
    if row is None:raise HTTPException(404,'Review not found')
    try:
        image=document_reviews.render_review_image(doc,row)
    except document_reviews.ReviewConflict as exc:
        raise _review_conflict(exc) from exc
    return Response(content=image,media_type='image/png',headers={'Cache-Control':'private, no-store'})


@router.put('/documents/{document_id}/reviews/{review_id}', response_model=DocumentReviewResponse)
async def resolve_document_review(document_id: uuid.UUID, review_id: uuid.UUID, body: DocumentReviewRequest,
    teacher: User = Depends(require_teacher), db: AsyncSession = Depends(get_db),
    embed_fn: document_service.EmbedFn = Depends(get_embed_fn)):
    doc=await _owned_document_or_404(db,document_id,teacher)
    if doc.structured:
        raise HTTPException(409,'structured_review_endpoint_required')
    try:
        row=await document_reviews.resolve_review(db,doc,review_id,revision=body.revision,
            status=body.status,content=body.content,teacher_id=teacher.id,embed_fn=embed_fn)
    except document_reviews.ReviewConflict as exc:
        raise _review_conflict(exc) from exc
    except document_reviews.EmbeddingUnavailable as exc:
        # Nothing was written; the teacher keeps the text and can retry.
        raise HTTPException(503,{'code':'embedding_unavailable','message':str(exc)}) from exc
    except LookupError as exc:
        raise HTTPException(404,str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422,str(exc)) from exc
    return DocumentReviewResponse.model_validate(row)
