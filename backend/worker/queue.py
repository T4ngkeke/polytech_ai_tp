"""
queue.py — DB-as-queue claim logic for the ingestion worker.

The worker polls `ingestion_jobs` and atomically claims one row at a time using
PostgreSQL `FOR UPDATE SKIP LOCKED`, so multiple workers never grab the same
job. A job left in `processing` longer than the stale threshold (e.g. the worker
crashed mid-run) is reclaimable for crash recovery.
"""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models import IngestionJob

# Reclaim a `processing` job whose lock is older than this many seconds.
DEFAULT_STALE_AFTER_SECONDS = 600

# Jobs at or below this priority are "urgent" (teacher-triggered hint generation
# enqueues at priority=0) and bypass the chat-load gate so they run immediately.
URGENT_PRIORITY = 0


_HAS_URGENT_SQL = text(
    """
    SELECT 1 FROM ingestion_jobs
    WHERE status = 'queued' AND priority <= :urgent
    LIMIT 1
    """
)


async def has_urgent_job(
    db: AsyncSession, urgent_priority: int = URGENT_PRIORITY
) -> bool:
    """Whether a runnable job at/below the urgent priority is currently queued.

    The worker loop uses this to let a teacher's urgent hint job (priority=0)
    bypass the chat-load gate — so "generate hints now" runs immediately during
    class instead of waiting for a quiet window. Read-only (no claim)."""
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
        WHERE status = 'queued'
           OR (status = 'processing'
               AND locked_at < now() - make_interval(secs => :stale))
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
