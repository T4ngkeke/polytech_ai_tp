"""
test_document_service.py — v7 document upload (producer side) tests.

Pure DB + filesystem, so these run on SQLite (db_session). The service stores
the uploaded bytes under a storage root and enqueues an ingestion job.
"""

import uuid
from pathlib import Path

import pytest
from sqlalchemy import select

from backend.app.models import (
    Class,
    Document,
    DocumentStatus,
    IngestionJob,
    JobStatus,
    UserRole,
)
from backend.app.services.document_service import create_document
from backend.tests.conftest import make_user


async def _make_class(session):
    teacher = make_user(role=UserRole.teacher)
    session.add(teacher)
    await session.flush()
    cls = Class(
        id=uuid.uuid4(), name="Algorithms",
        teacher_id=teacher.id, invite_code=uuid.uuid4().hex[:6],
    )
    session.add(cls)
    await session.flush()
    return teacher, cls


@pytest.mark.asyncio
async def test_create_document_persists_pending_and_enqueues_job(db_session, tmp_path):
    teacher, cls = await _make_class(db_session)

    doc = await create_document(
        db_session,
        class_id=cls.id,
        lab_id=None,
        filename="td3.pdf",
        content=b"exercise sheet contents",
        uploaded_by=teacher.id,
        storage_root=tmp_path,
    )

    assert doc.status == DocumentStatus.pending
    assert Path(doc.storage_path).read_bytes() == b"exercise sheet contents"

    jobs = (
        await db_session.execute(
            select(IngestionJob).where(IngestionJob.document_id == doc.id)
        )
    ).scalars().all()
    assert len(jobs) == 1
    assert jobs[0].status == JobStatus.queued


@pytest.mark.asyncio
async def test_create_document_dedups_identical_content(db_session, tmp_path):
    teacher, cls = await _make_class(db_session)
    kwargs = dict(
        class_id=cls.id, lab_id=None, filename="td3.pdf",
        content=b"same bytes", uploaded_by=teacher.id, storage_root=tmp_path,
    )

    first = await create_document(db_session, **kwargs)
    second = await create_document(db_session, **kwargs)

    assert second.id == first.id  # same document returned, not a duplicate

    docs = (await db_session.execute(select(Document))).scalars().all()
    jobs = (await db_session.execute(select(IngestionJob))).scalars().all()
    assert len(docs) == 1
    assert len(jobs) == 1  # unchanged content → not re-enqueued
