"""
document_service.py — [v7] course-document upload (producer side).

Stores uploaded bytes under a storage root, registers a `Document` in the
`pending` state, and enqueues an `IngestionJob` for the worker to pick up.
"""

import hashlib
import uuid
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models import (
    Audience, Class, DocChunk, Document, DocType, Exercise, IngestionJob,
)


async def create_document(
    db: AsyncSession,
    *,
    class_id: uuid.UUID,
    lab_id: uuid.UUID | None,
    filename: str,
    content: bytes,
    uploaded_by: uuid.UUID,
    storage_root: str | Path,
    doc_type: DocType | None = None,
    audience: Audience | None = None,
) -> Document:
    """Persist an uploaded document and enqueue it for ingestion."""
    content_hash = hashlib.sha256(content).hexdigest()

    # Dedup: identical content in the same scope is unchanged — return the
    # existing document without re-storing or re-enqueueing.
    existing = (
        await db.execute(
            select(Document).where(
                Document.class_id == class_id,
                Document.lab_id == lab_id,
                Document.content_hash == content_hash,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    root = Path(storage_root)
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{content_hash}_{filename}"
    path.write_bytes(content)

    doc = Document(
        id=uuid.uuid4(),
        class_id=class_id,
        lab_id=lab_id,
        filename=filename,
        storage_path=str(path),
        content_hash=content_hash,
        doc_type=doc_type,
        audience=audience,
        uploaded_by=uploaded_by,
    )
    db.add(doc)
    await db.flush()

    db.add(IngestionJob(id=uuid.uuid4(), document_id=doc.id))
    await db.commit()
    await db.refresh(doc)
    return doc


# ---------------------------------------------------------------------------
# [v7.1] Phase 6 — teacher ingestion visibility (read side)
# ---------------------------------------------------------------------------


async def _count(db: AsyncSession, model, document_id: uuid.UUID) -> int:
    stmt = select(func.count()).select_from(model).where(model.document_id == document_id)
    return (await db.execute(stmt)).scalar_one()


async def list_lab_documents(db: AsyncSession, lab_id: uuid.UUID) -> list[dict]:
    """Documents in a lab with their status + processing summary (pages/chunks/exercises)."""
    docs = (await db.execute(
        select(Document).where(Document.lab_id == lab_id).order_by(Document.id)
    )).scalars().all()

    summaries: list[dict] = []
    for doc in docs:
        summaries.append({
            "doc": doc,
            "chunk_count": await _count(db, DocChunk, doc.id),
            "exercise_count": await _count(db, Exercise, doc.id),
        })
    return summaries


async def get_owned_document(
    db: AsyncSession, document_id: uuid.UUID, teacher_id: uuid.UUID
) -> Document | None:
    """Return the document only if it belongs to a class the teacher owns."""
    stmt = (
        select(Document)
        .join(Class, Class.id == Document.class_id)
        .where(Document.id == document_id, Class.teacher_id == teacher_id)
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def get_document_chunks(
    db: AsyncSession, document_id: uuid.UUID, *, offset: int = 0, limit: int = 50
) -> list[DocChunk]:
    """Paginated chunks for a document, in source order — the inspector surface."""
    stmt = (
        select(DocChunk)
        .where(DocChunk.document_id == document_id)
        .order_by(DocChunk.chunk_index)
        .offset(offset)
        .limit(limit)
    )
    return list((await db.execute(stmt)).scalars().all())


async def get_document_exercises(
    db: AsyncSession, document_id: uuid.UUID
) -> list[Exercise]:
    """Extracted exercises (statements only — no solution exists)."""
    stmt = (
        select(Exercise)
        .where(Exercise.document_id == document_id)
        .order_by(Exercise.number)
    )
    return list((await db.execute(stmt)).scalars().all())
