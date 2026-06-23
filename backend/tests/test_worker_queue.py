"""
test_worker_queue.py — v7 ingestion worker DB-as-queue tests.

These exercise real PostgreSQL semantics (FOR UPDATE SKIP LOCKED, intervals)
and therefore use the `pg_session` fixture, which skips when no pgvector
Postgres is reachable.
"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from backend.app.models import (
    Class,
    Document,
    IngestionJob,
    JobStatus,
    UserRole,
)
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from backend.tests.conftest import TEST_PG_URL, make_user
from backend.worker.queue import _CLAIM_SQL, claim_next_job


async def _seed_job(session, *, priority: int = 100) -> IngestionJob:
    teacher = make_user(role=UserRole.teacher)
    session.add(teacher)
    await session.flush()

    cls = Class(
        id=uuid.uuid4(),
        name="Algorithms",
        teacher_id=teacher.id,
        invite_code=uuid.uuid4().hex[:6],
    )
    session.add(cls)
    await session.flush()

    doc = Document(
        id=uuid.uuid4(),
        class_id=cls.id,
        filename="td3.pdf",
        storage_path="/data/documents/td3.pdf",
        content_hash=uuid.uuid4().hex,
        uploaded_by=teacher.id,
    )
    session.add(doc)
    await session.flush()

    job = IngestionJob(id=uuid.uuid4(), document_id=doc.id, priority=priority)
    session.add(job)
    await session.commit()
    return job


@pytest.mark.asyncio
async def test_claim_next_job_marks_processing(pg_session):
    job = await _seed_job(pg_session)

    claimed = await claim_next_job(pg_session)

    assert claimed is not None
    assert claimed.id == job.id
    assert claimed.status == JobStatus.processing
    assert claimed.attempts == 1
    assert claimed.locked_at is not None


@pytest.mark.asyncio
async def test_claim_returns_none_when_no_queued_jobs(pg_session):
    claimed = await claim_next_job(pg_session)
    assert claimed is None


@pytest.mark.asyncio
async def test_stale_processing_job_is_reclaimed(pg_session):
    job = await _seed_job(pg_session)
    # Simulate a worker that claimed this job then crashed long ago.
    job.status = JobStatus.processing
    job.locked_at = datetime.now(timezone.utc) - timedelta(seconds=1000)
    await pg_session.commit()

    claimed = await claim_next_job(pg_session, stale_after_seconds=600)

    assert claimed is not None
    assert claimed.id == job.id
    assert claimed.status == JobStatus.processing


@pytest.mark.asyncio
async def test_fresh_processing_job_is_not_reclaimed(pg_session):
    job = await _seed_job(pg_session)
    # A job another worker is actively running (locked just now).
    job.status = JobStatus.processing
    job.locked_at = datetime.now(timezone.utc) - timedelta(seconds=5)
    await pg_session.commit()

    claimed = await claim_next_job(pg_session, stale_after_seconds=600)

    assert claimed is None


@pytest.mark.asyncio
async def test_skip_locked_prevents_double_claim(pg_session):
    job = await _seed_job(pg_session)

    # Worker A claims the row inside an open transaction and holds the lock.
    engine_a = create_async_engine(TEST_PG_URL, poolclass=NullPool)
    conn_a = await engine_a.connect()
    trans_a = await conn_a.begin()
    locked = (await conn_a.execute(_CLAIM_SQL, {"stale": 600})).first()
    assert locked is not None and locked[0] == job.id

    try:
        # Worker B sees the only candidate locked → SKIP LOCKED yields nothing.
        claimed_b = await claim_next_job(pg_session)
        assert claimed_b is None
    finally:
        await trans_a.rollback()
        await conn_a.close()
        await engine_a.dispose()
