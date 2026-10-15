"""
queue.py — DB-as-queue claim logic for the document worker.

The worker claims one structured job at a time with PostgreSQL
`FOR UPDATE SKIP LOCKED`, so several workers never take the same job. A claim
carries a lock token and a lease: a job whose lease ran out (the worker
crashed) can be claimed again.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models import Document, IngestionJob, JobStatus, _utcnow

# ---------------------------------------------------------------------------
# Structured jobs. Rows of the removed pre-MinerU pipeline (task_kind NULL)
# are never claimed.
# ---------------------------------------------------------------------------

# odp_parse stays claimable so that an ODP job left from before 2026-10-15
# fails as odp_not_supported instead of waiting forever.
STRUCTURED_TASK_KINDS = ("odp_parse", "pdf_parse")
# A MinerU parse may take a while (up to 240 s per page). The lease
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
