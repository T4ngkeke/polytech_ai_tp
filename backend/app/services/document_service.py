"""
document_service.py — [v7] course-document upload (producer side).

Stores uploaded bytes under a storage root, registers a `Document` in the
`pending` state, and enqueues an `IngestionJob` for the worker to pick up.
"""

import hashlib
import uuid
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models import Document, IngestionJob


async def create_document(
    db: AsyncSession,
    *,
    class_id: uuid.UUID,
    lab_id: uuid.UUID | None,
    filename: str,
    content: bytes,
    uploaded_by: uuid.UUID,
    storage_root: str | Path,
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
        uploaded_by=uploaded_by,
    )
    db.add(doc)
    await db.flush()

    db.add(IngestionJob(id=uuid.uuid4(), document_id=doc.id))
    await db.commit()
    await db.refresh(doc)
    return doc
