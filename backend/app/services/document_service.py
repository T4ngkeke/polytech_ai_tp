"""
document_service.py — course-document upload (producer side) and the teacher's
read/edit surface over what ingestion produced.

Stores uploaded bytes under a storage root, registers a `Document` in the
`pending` state, and enqueues an `IngestionJob` for the worker to pick up.
"""

import hashlib
import uuid
from pathlib import Path
from typing import Awaitable, Callable

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models import (
    Class, DocChunk, Document, DocumentReview, DocumentRevision, IngestionJob,
)

EmbedFn = Callable[[list[str]], Awaitable[list[list[float]]]]


async def create_document(
    db: AsyncSession,
    *,
    class_id: uuid.UUID,
    lab_id: uuid.UUID | None,
    filename: str,
    content: bytes,
    uploaded_by: uuid.UUID,
    storage_root: str | Path,
    language: str = "fr",
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
                Document.structured.is_(False),
                Document.lifecycle == "active",
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
        language=language,
        uploaded_by=uploaded_by,
    )
    db.add(doc)
    await db.flush()

    db.add(IngestionJob(id=uuid.uuid4(), document_id=doc.id))
    await db.commit()
    await db.refresh(doc)
    return doc


# ---------------------------------------------------------------------------
# Teacher ingestion visibility (read side)
# ---------------------------------------------------------------------------


async def _count(db: AsyncSession, model, document_id: uuid.UUID) -> int:
    stmt = select(func.count()).select_from(model).where(model.document_id == document_id)
    return (await db.execute(stmt)).scalar_one()


async def list_lab_documents(db: AsyncSession, lab_id: uuid.UUID) -> list[dict]:
    """Documents in a lab with their status + processing summary (pages/chunks)."""
    docs = (await db.execute(
        select(Document).where(Document.lab_id == lab_id, Document.lifecycle != "deleted").order_by(Document.id)
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


# ---------------------------------------------------------------------------
# Teacher edits — correct chunking mistakes in place.
#
# The author is the ground-truth oracle for "was this processed well?", so they
# can fix a mis-split chunk. Editing one re-embeds and re-indexes it so
# retrieval stays consistent with the corrected text.
# ---------------------------------------------------------------------------

def _augmented(context: str | None, content: str) -> str:
    """Text fed to the vector + BM25 indexes — must match worker/ingest._augmented."""
    return f"{context}\n{content}" if context else content


def _tsv_value(db: AsyncSession, text: str, language: str = "fr"):
    """BM25 tsvector (Postgres only; None elsewhere) — matches ingest._tsv_value.

    [v7.3] Config from the document's language: edit-side rebuilds must use the
    same config as ingest or the corrected chunk stops matching."""
    from backend.app.services.retrieval_service import ts_config_for

    if db.bind is not None and db.bind.dialect.name == "postgresql":
        return func.to_tsvector(ts_config_for(language), text)
    return None


async def get_chunk_in_document(
    db: AsyncSession, document_id: uuid.UUID, chunk_id: uuid.UUID
) -> DocChunk | None:
    """Fetch a chunk only if it belongs to the given document (scopes the edit)."""
    # Same parent lock as worker publication: never edit a row being replaced.
    await db.scalar(select(Document).where(Document.id == document_id).with_for_update())
    stmt = select(DocChunk).execution_options(populate_existing=True).where(
        DocChunk.id == chunk_id, DocChunk.document_id == document_id
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def update_chunk(
    db: AsyncSession, chunk: DocChunk, *, content: str, embed_fn: EmbedFn
) -> DocChunk:
    """Replace a chunk's text and rebuild its retrieval artifacts.

    The original `content` is what citations/the inspector show; the vector and
    BM25 index are built over the *augmented* text (existing context + new
    content), so the teacher's correction is what students actually retrieve.
    """
    augmented = _augmented(chunk.context, content)
    embeddings = await embed_fn([augmented])
    parent = await db.get(Document, chunk.document_id)
    chunk.content = content
    chunk.embedding = embeddings[0]
    chunk.tsv = _tsv_value(db, augmented, parent.language if parent else "fr")
    # [v7.3] Flag the correction so idempotent re-ingestion preserves it.
    chunk.edited_by_teacher = True
    db.add(chunk)
    await db.flush()
    await db.refresh(chunk)
    return chunk


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
