"""Immediate structured revocation plus retryable cleanup of M1-owned artifacts.

No caller-supplied absolute paths are accepted. Caller commits mark_deleted
before running cleanup, so filesystem retries never delay revocation.
"""
from datetime import timedelta
from pathlib import Path
from sqlalchemy import delete, select, update
from datetime import timezone
from backend.app.models import (
    Document, DocumentSource, DocumentRevision, DocumentAsset, DocumentPage,
    DocumentBlock, DocumentBlockAsset, DocumentBlockLink, DocumentCleanupJob,
    DocChunk, DocumentReview, DocumentStagedObject, IngestionJob, _utcnow,
)
from backend.app.services import document_storage
from backend.app.services.document_access import owned_document
from backend.app.services.document_versions import _cas, storage_key, VersionConflict


async def mark_deleted(db, *, document_id, teacher_id, expected_generation):
    doc = await owned_document(db, document_id, teacher_id)
    if doc.lifecycle == 'deleted':
        return await db.get(DocumentCleanupJob, doc.id)
    if doc.lifecycle not in ('active', 'archived'):
        raise VersionConflict('source_unavailable')
    await _cas(db, doc, expected_generation, expected_lifecycle=doc.lifecycle,
               lifecycle='deleted', current_revision_id=None, deleted_at=_utcnow())
    sources = list((await db.scalars(select(DocumentSource).where(DocumentSource.document_id == doc.id))).all())
    assets = list((await db.scalars(select(DocumentAsset).where(
        DocumentAsset.source_version_id.in_([s.id for s in sources])))).all())
    job = DocumentCleanupJob(document_id=doc.id,
        remaining_storage_keys=sorted({s.storage_key for s in sources} | {a.storage_key for a in assets}))
    db.add(job)
    # These IDs cannot be claimed by the legacy worker after revocation.
    await db.execute(delete(IngestionJob).where(IngestionJob.document_id == doc.id))
    await db.flush()
    return job


async def run_cleanup(db, *, document_id, storage_root):
    """One idempotent attempt. Row lock serializes cleaners; no external network."""
    doc = await db.scalar(select(Document).where(Document.id == document_id)
        .with_for_update().execution_options(populate_existing=True))
    job = await db.scalar(select(DocumentCleanupJob).where(DocumentCleanupJob.document_id == document_id)
        .with_for_update().execution_options(populate_existing=True))
    if doc is None or job is None or doc.lifecycle != 'deleted':
        raise VersionConflict('not_deleted')
    if job.state == 'complete':
        return job
    job.attempts += 1
    root = Path(storage_root).resolve()
    remaining = list(job.remaining_storage_keys)
    try:
        for key in list(remaining):
            storage_key(key)
            path = root / key
            # Resolve symlinks BEFORE unlink: never follow a path outside the root.
            if not path.resolve().is_relative_to(root) or path.resolve() == root:
                raise ValueError('unsafe_storage_key')
            path.unlink(missing_ok=True)
            remaining.remove(key)
    except (OSError, ValueError):
        job.remaining_storage_keys = remaining
        job.state, job.last_error_code = 'failed', 'asset_cleanup_failed'
        job.next_attempt_at = _utcnow() + timedelta(seconds=min(3600, 30 * 2 ** min(job.attempts, 7)))
        await db.flush()
        return job
    # M2: files written by an upload or a worker that never committed. One whose
    # writer may still be inside its lease is not touched yet; the cleanup is
    # not complete until it is gone, so no file of a deleted document survives.
    staged = list((await db.scalars(select(DocumentStagedObject).where(
        DocumentStagedObject.document_id == doc.id))).all())
    now = _utcnow()
    waiting = [row for row in staged if aware(row.lease_expires_at) > now]
    try:
        for row in staged:
            if row not in waiting:
                document_storage.remove_object(root, row.storage_key)
                await db.delete(row)
    except (OSError, ValueError, document_storage.StorageError):
        waiting = waiting or staged
    if waiting:
        job.remaining_storage_keys = remaining
        job.state, job.last_error_code = 'failed', 'staged_objects_pending'
        job.next_attempt_at = max(aware(row.lease_expires_at) for row in waiting) + timedelta(seconds=1)
        await db.flush()
        return job
    rev_ids = list((await db.scalars(select(DocumentRevision.id).where(
        DocumentRevision.document_id == doc.id))).all())
    source_ids = list((await db.scalars(select(DocumentSource.id).where(
        DocumentSource.document_id == doc.id))).all())
    for model in (DocumentReview, DocChunk, IngestionJob):
        await db.execute(delete(model).where(model.document_id == doc.id))
    for model in (DocumentBlockLink, DocumentBlockAsset, DocumentBlock, DocumentPage):
        await db.execute(delete(model).where(model.revision_id.in_(rev_ids)))
    # Remove revisions before assets, but sources only after both. Self-references
    # are deleted within each statement and cannot point outside this document.
    await db.execute(delete(DocumentRevision).where(DocumentRevision.document_id == doc.id))
    await db.execute(delete(DocumentAsset).where(DocumentAsset.source_version_id.in_(source_ids)))
    await db.execute(delete(DocumentSource).where(DocumentSource.document_id == doc.id))
    doc.filename = doc.storage_path = doc.content_hash = ''
    doc.error_message = doc.ingest_report = doc.page_count = None
    doc.language = ''
    job.state, job.last_error_code, job.remaining_storage_keys = 'complete', None, []
    await db.flush()
    return job


def aware(value):
    """SQLite hands back naive datetimes; everything stored here is UTC."""
    return value.replace(tzinfo=timezone.utc) if value is not None and value.tzinfo is None else value


async def sweep_staged_objects(db, *, storage_root, now=None, limit=100):
    """Remove staged files whose writer's lease has passed. Idempotent.

    A key that a source or asset row owns is only dropped from the ledger; its
    file stays. Caller commits. Returns the number of ledger rows handled.
    """
    now = now or _utcnow()
    rows = list((await db.scalars(select(DocumentStagedObject)
        .where(DocumentStagedObject.lease_expires_at < now)
        .order_by(DocumentStagedObject.lease_expires_at).limit(limit)
        .with_for_update(skip_locked=True))).all())
    handled = 0
    for row in rows:
        owned = await db.scalar(select(DocumentSource.id).where(DocumentSource.storage_key == row.storage_key)) \
            or await db.scalar(select(DocumentAsset.id).where(DocumentAsset.storage_key == row.storage_key))
        try:
            if not owned:
                document_storage.remove_object(storage_root, row.storage_key)
        except (OSError, document_storage.StorageError, ValueError):
            continue  # stays in the ledger; the next sweep tries again
        await db.delete(row)
        handled += 1
    await db.flush()
    return handled
