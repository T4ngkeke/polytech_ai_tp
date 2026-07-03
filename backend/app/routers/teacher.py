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

import uuid
from datetime import date

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
    Audience, Class, ClassStudent, DocType, Lab, RuleLevel, Session,
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
    DocExerciseResponse,
    ExerciseUpdateRequest,
    DocumentResponse,
    DocumentSummaryResponse,
    ApplySkillRequest,
    RuleResponse,
    RuleUpsertRequest,
    SessionWithMessagesResponse,
    SkillPresetResponse,
    SkillPresetUpsertRequest,
    StudentSummaryResponse,
)
from backend.app.services import (
    class_service,
    document_service,
    lab_service,
    rule_service,
    skill_preset_service,
    analytics_service,
)

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
    file: UploadFile = File(...),
    doc_type: DocType = Form(...),
    audience: Audience = Form(...),
    language: str = Form("fr"),
    teacher: User = Depends(require_teacher),
    db: AsyncSession = Depends(get_db),
    storage_root: str = Depends(get_storage_root),
) -> DocumentResponse:
    """Upload a PDF course document to a lab the teacher owns; enqueues ingestion.

    `doc_type` (CM/TD/TP) and `audience` (student/teacher) are required — they are the
    deterministic ingestion routing signal and the student-retrieval audience filter.
    """
    lab = await lab_service.get_lab_by_id(db, lab_id)
    await lab_service.verify_lab_ownership(db, lab, teacher_id=teacher.id)

    # Input is PDF only — instructors export slides to PDF before upload.
    if not (file.filename or "").lower().endswith(".pdf"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only PDF uploads are supported. Export slides to PDF first.",
        )

    content = await file.read()
    doc = await document_service.create_document(
        db,
        class_id=lab.class_id,
        lab_id=lab.id,
        filename=file.filename,
        content=content,
        uploaded_by=teacher.id,
        storage_root=storage_root,
        doc_type=doc_type,
        audience=audience,
        language=language,
    )
    return DocumentResponse.model_validate(doc)


# ===================================================================
# [v7.1] Phase 6 — teacher ingestion visibility
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
            doc_type=r["doc"].doc_type.value if r["doc"].doc_type else None,
            audience=r["doc"].audience.value if r["doc"].audience else None,
            status=r["doc"].status.value,
            page_count=r["doc"].page_count,
            error_message=r["doc"].error_message,
            chunk_count=r["chunk_count"],
            exercise_count=r["exercise_count"],
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


@router.get("/documents/{document_id}/exercises", response_model=list[DocExerciseResponse])
async def get_document_exercises(
    document_id: uuid.UUID,
    teacher: User = Depends(require_teacher),
    db: AsyncSession = Depends(get_db),
) -> list[DocExerciseResponse]:
    """Extracted exercises (statements only) for a document the teacher owns."""
    await _owned_document_or_404(db, document_id, teacher)
    exercises = await document_service.get_document_exercises(db, document_id)
    return [DocExerciseResponse.model_validate(e) for e in exercises]


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
    await _owned_document_or_404(db, document_id, teacher)
    chunk = await document_service.get_chunk_in_document(db, document_id, chunk_id)
    if chunk is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Chunk not found")
    chunk = await document_service.update_chunk(db, chunk, content=body.content, embed_fn=embed_fn)
    return ChunkResponse.model_validate(chunk)


@router.put(
    "/documents/{document_id}/exercises/{exercise_id}", response_model=DocExerciseResponse
)
async def update_document_exercise(
    document_id: uuid.UUID,
    exercise_id: uuid.UUID,
    body: ExerciseUpdateRequest,
    teacher: User = Depends(require_teacher),
    db: AsyncSession = Depends(get_db),
) -> DocExerciseResponse:
    """[v7.2] Correct an extracted exercise (number/statement/hints). No solution
    field exists. Ownership enforced via the document."""
    await _owned_document_or_404(db, document_id, teacher)
    exercise = await document_service.get_exercise_in_document(db, document_id, exercise_id)
    if exercise is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Exercise not found")
    exercise = await document_service.update_exercise(
        db, exercise, number=body.number, statement=body.statement, hints=body.hints
    )
    return DocExerciseResponse.model_validate(exercise)


# ===================================================================
# PUT /api/teacher/rules
# ===================================================================


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
