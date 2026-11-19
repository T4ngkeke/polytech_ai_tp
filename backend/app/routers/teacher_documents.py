"""Teacher routes for uploading, listing and deleting documents, and TP text."""

import contextlib
import os
import uuid
from types import SimpleNamespace

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Response,
    UploadFile,
    status,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.auth import require_teacher
from backend.app.config import settings
from backend.app.database import get_db
from backend.app.models import Document, User
from backend.app.routers.teacher_access import _verify_class_ownership, get_storage_root
from backend.app.schemas import DocumentResponse, DocumentSummaryResponse
from backend.app.services import document_service, lab_service

router = APIRouter(prefix="/api/teacher", tags=["teacher"])


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


@router.get("/labs/{lab_id}/tp-text")
async def lab_tp_text(
    lab_id: uuid.UUID,
    teacher: User = Depends(require_teacher),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> dict:
    """Which of the lab's own files the tutor reads in full with every question
    (Search and Direct) and which it only searches: the same rule as the chat
    (agent/tp_text.plan_files), with sizes estimated from the stored text of
    each file's published revision. limit_chars: TP_CONTEXT_PERCENT of the
    agent model's context. In exam mode the chat reads only the allowed files."""
    from sqlalchemy import func as sa_func

    from backend.app.agent.tp_text import page_overhead, plan_files, tp_char_budget
    from backend.app.models import DocumentBlock
    from backend.app.services.model_clients import get_llm_config
    from backend.app.services.model_servers import context_tokens

    lab = await lab_service.get_lab_by_id(db, lab_id)
    await lab_service.verify_lab_ownership(db, lab, teacher_id=teacher.id)
    rows = (await db.execute(
        select(Document.id, Document.filename, Document.page_count,
               sa_func.coalesce(sa_func.sum(sa_func.length(DocumentBlock.content)
                                            + sa_func.coalesce(sa_func.length(DocumentBlock.content_description), 0)), 0),
               sa_func.count(DocumentBlock.block_id))
        .outerjoin(DocumentBlock, DocumentBlock.revision_id == Document.current_revision_id)
        .where(Document.lab_id == lab_id, Document.lifecycle == "active",
               Document.current_revision_id.is_not(None))
        .group_by(Document.id, Document.filename, Document.page_count))).all()
    config = await get_llm_config(db)
    context = await context_tokens(config["agent_base_url"], config["agent_model"], config["agent_api_key"])
    percent = config["research_limits"].tp_context_percent
    limit = tp_char_budget(context, percent)
    files = [{"document_id": str(doc_id), "filename": name, "pages": pages or 0,
              "chars": int(chars) + page_overhead(name, pages or 0, blocks)}
             for doc_id, name, pages, chars, blocks in rows]
    in_full = set(plan_files([(f["document_id"], f["pages"], f["chars"], f["filename"]) for f in files], limit))
    for f in files:
        f["in_full"] = f["document_id"] in in_full
    return {"limit_chars": limit, "percent": percent, "context_tokens": context,
            "files": sorted(files, key=lambda f: f["filename"]),
            "left_out": sum(not f["in_full"] for f in files)}


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
