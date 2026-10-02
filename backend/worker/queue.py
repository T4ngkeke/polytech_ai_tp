"""
queue.py — DB-as-queue claim logic for the ingestion worker.

The worker polls `ingestion_jobs` and atomically claims one row at a time using
PostgreSQL `FOR UPDATE SKIP LOCKED`, so multiple workers never grab the same
job. A job left in `processing` longer than the stale threshold (e.g. the worker
crashed mid-run) is reclaimable for crash recovery.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import and_, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models import Document, IngestionJob, JobStatus, _utcnow

# Reclaim a `processing` job whose lock is older than this many seconds.
DEFAULT_STALE_AFTER_SECONDS = 600

# Jobs at or below this priority are "urgent" (a teacher asking for a document
# enqueues at priority=0) and bypass the chat-load gate so they run immediately.
URGENT_PRIORITY = 0


_HAS_URGENT_SQL = text(
    """
    SELECT 1 FROM ingestion_jobs
    WHERE status = 'queued' AND priority <= :urgent AND task_kind IS NULL
      AND document_id IN (SELECT id FROM documents WHERE structured = false AND lifecycle = 'active')
    LIMIT 1
    """
)


async def has_urgent_job(
    db: AsyncSession, urgent_priority: int = URGENT_PRIORITY
) -> bool:
    """Whether a runnable job at/below the urgent priority is currently queued.

    The worker loop uses this to let an urgent job (priority=0) bypass BOTH the
    chat-load gate and the GPU idle gate — so "do it now" runs immediately
    during class instead of waiting for a quiet window.
    Read-only (no claim)."""
    result = await db.execute(_HAS_URGENT_SQL, {"urgent": urgent_priority})
    return result.first() is not None


_CLAIM_SQL = text(
    """
    UPDATE ingestion_jobs
    SET status = 'processing',
        locked_at = now(),
        attempts = attempts + 1,
        updated_at = now()
    WHERE id = (
        SELECT id FROM ingestion_jobs
        WHERE (status = 'queued'
           OR (status = 'processing'
               AND locked_at < now() - make_interval(secs => :stale)))
          AND task_kind IS NULL
          AND document_id IN (SELECT id FROM documents WHERE structured = false AND lifecycle = 'active')
        ORDER BY priority, created_at
        FOR UPDATE SKIP LOCKED
        LIMIT 1
    )
    RETURNING id
    """
)


async def claim_next_job(
    db: AsyncSession,
    stale_after_seconds: int = DEFAULT_STALE_AFTER_SECONDS,
) -> IngestionJob | None:
    """
    Atomically claim the next runnable job, marking it `processing`.

    Returns the claimed `IngestionJob`, or ``None`` when nothing is runnable.
    """
    result = await db.execute(_CLAIM_SQL, {"stale": stale_after_seconds})
    row = result.first()
    if row is None:
        await db.rollback()
        return None

    await db.commit()
    # The row was mutated via raw SQL; reload so the returned ORM object
    # reflects the claim instead of the session's stale cached copy.
    job = await db.get(IngestionJob, row[0])
    await db.refresh(job)
    return job


# ---------------------------------------------------------------------------
# M2-B: structured (ODP) jobs. A separate claim, so the legacy loop can never
# pick one up and this one can never pick up a legacy row.
# ---------------------------------------------------------------------------

STRUCTURED_TASK_KINDS = ("odp_parse", "pdf_parse")
# Parse + render may take a while (the render budget alone is 600 s). The lease
# must be longer than one full attempt; a worker stops writing before it ends.
DEFAULT_LEASE_SECONDS = 1800


@dataclass(frozen=True)
class StructuredClaim:
    """What a worker holds while it works. The commit re-checks every field."""
    job_id: uuid.UUID
    task_kind: str
    document_id: uuid.UUID
    source_version_id: uuid.UUID
    revision_id: uuid.UUID
    lock_token: uuid.UUID
    generation: int
    attempts: int
    lease_expires_at: datetime


async def claim_structured_job(
    db: AsyncSession, *, lease_seconds: int = DEFAULT_LEASE_SECONDS, now: datetime | None = None,
    task_kinds: tuple[str, ...] = STRUCTURED_TASK_KINDS,
) -> StructuredClaim | None:
    """Claim one queued structured job, or one whose lease has run out.

    Only the job row is locked (SKIP LOCKED), and the document is only read, so
    this never waits on a teacher action holding the document lock. The token
    is new on every claim: a worker that lost its lease can no longer commit.
    """
    now = now or _utcnow()
    job = await db.scalar(
        select(IngestionJob)
        .join(Document, Document.id == IngestionJob.document_id)
        .where(
            IngestionJob.task_kind.in_(task_kinds),
            Document.structured.is_(True),
            Document.lifecycle == "active",
            or_(IngestionJob.status == JobStatus.queued,
                and_(IngestionJob.status == JobStatus.processing,
                     IngestionJob.lease_expires_at < now)),
        )
        .order_by(IngestionJob.priority, IngestionJob.created_at)
        .limit(1)
        .with_for_update(skip_locked=True, of=IngestionJob)
        .execution_options(populate_existing=True)
    )
    if job is None:
        await db.rollback()
        return None
    generation = await db.scalar(select(Document.generation).where(Document.id == job.document_id))
    job.status = JobStatus.processing
    job.attempts += 1
    job.lock_token = uuid.uuid4()
    job.claimed_generation = generation
    job.locked_at = now
    job.lease_expires_at = now + timedelta(seconds=lease_seconds)
    claim = StructuredClaim(
        job_id=job.id, task_kind=job.task_kind, document_id=job.document_id, source_version_id=job.source_version_id,
        revision_id=job.revision_id, lock_token=job.lock_token, generation=generation,
        attempts=job.attempts, lease_expires_at=job.lease_expires_at,
    )
    await db.commit()
    return claim
