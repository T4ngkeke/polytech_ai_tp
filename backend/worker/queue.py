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
