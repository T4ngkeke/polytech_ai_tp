"""
document_service.py — the teacher's document lists, ownership and deletion.

Uploads go through structured_upload.py (PDF → MinerU → index); this module
lists what a lab or class uses and deletes a document with what came from it.
"""

import uuid

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models import (
    Class, DocChunk, Document, DocumentReview, DocumentRevision, IngestionJob,
)

async def _count(db: AsyncSession, model, document_id: uuid.UUID) -> int:
    stmt = select(func.count()).select_from(model).where(model.document_id == document_id)
    return (await db.execute(stmt)).scalar_one()


async def list_lab_documents(db: AsyncSession, lab_id: uuid.UUID,
                             class_id: uuid.UUID | None = None) -> list[dict]:
    """Documents a lab uses, with status + processing summary (pages/chunks).

    With class_id, the class-wide documents (lab_id NULL) come first: the
    research tools of every lab of the class read them too. Each summary
    names its scope, "class" or "lab".
    """
    where = Document.lab_id == lab_id
    if class_id is not None:
        where = where | ((Document.class_id == class_id) & Document.lab_id.is_(None))
    return await _summaries(db, where)


async def list_class_documents(db: AsyncSession, class_id: uuid.UUID) -> list[dict]:
    """The class-wide documents of a class (lab_id NULL), with their summaries."""
    return await _summaries(db, (Document.class_id == class_id) & Document.lab_id.is_(None))


async def _summaries(db: AsyncSession, where) -> list[dict]:
    docs = (await db.execute(
        select(Document).where(where, Document.lifecycle != "deleted")
        .order_by(Document.lab_id.is_not(None), Document.id)
    )).scalars().all()

    summaries: list[dict] = []
    for doc in docs:
        if doc.structured:
            chunk_count = (await db.scalar(select(func.count()).select_from(DocChunk).where(
                DocChunk.document_id == doc.id, DocChunk.revision_id == doc.current_revision_id
            ))) if doc.current_revision_id else 0
        else:
            chunk_count = await _count(db, DocChunk, doc.id)
        latest = None
        if doc.structured:
            rev = await db.scalar(select(DocumentRevision).where(DocumentRevision.document_id == doc.id)
                                  .order_by(DocumentRevision.created_at.desc(), DocumentRevision.id.desc()).limit(1))
            if rev is not None:
                latest = {"id": str(rev.id), "processing_state": rev.processing_state,
                          "publication_state": rev.publication_state,
                          "parse_quality": (rev.report or {}).get("parse_quality"),
                          "error_code": (rev.report or {}).get("error_code")}
        summaries.append({
            "doc": doc,
            "chunk_count": chunk_count,
            "latest_revision": latest,
            "scope": "class" if doc.lab_id is None else "lab",
        })
    return summaries


async def get_owned_document(
    db: AsyncSession, document_id: uuid.UUID, teacher_id: uuid.UUID
) -> Document | None:
    """Return the document only if it belongs to a class the teacher owns."""
    stmt = (
        select(Document)
        .join(Class, Class.id == Document.class_id)
        .where(Document.id == document_id, Class.teacher_id == teacher_id, Document.lifecycle != "deleted")
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def delete_document(db: AsyncSession, document: Document) -> str:
    """Delete a document and every artifact derived from it — its chunks and its
    ingestion jobs — then the row itself. Children are removed explicitly (not
    left to DB cascade) so the behaviour is identical on SQLite and Postgres.

    Returns the `storage_path` so the caller can remove the stored file after the
    transaction commits (filesystem side effects stay in the router)."""
    if document.structured:
        raise ValueError("structured_document_requires_cleanup_job")
    storage_path = document.storage_path
    doc_id = document.id
    await db.execute(delete(DocumentReview).where(DocumentReview.document_id == doc_id))
    await db.execute(delete(DocChunk).where(DocChunk.document_id == doc_id))
    await db.execute(delete(IngestionJob).where(IngestionJob.document_id == doc_id))
    await db.delete(document)
    await db.flush()
    return storage_path
