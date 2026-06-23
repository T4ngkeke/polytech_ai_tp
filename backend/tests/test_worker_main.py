"""
test_worker_main.py — v7 worker step that ties the queue to the pipeline.

`process_one` claims one job (FOR UPDATE SKIP LOCKED → Postgres) and runs the
ingest pipeline, marking the job done/failed. Uses pg_session.
"""

import uuid

import pytest

from backend.app.models import (
    Class,
    Document,
    DocumentStatus,
    EMBEDDING_DIM,
    IngestionJob,
    JobStatus,
    Lab,
    UserRole,
)
from backend.app.services.document_service import create_document
from backend.tests.conftest import make_user
from backend.worker.main import process_one


async def fake_embed(chunks):
    return [[0.0] * EMBEDDING_DIM for _ in chunks]


async def fake_extract(text):
    return [{"number": "Ex 1", "statement": "do it", "hints": None,
             "concept": None}]


async def boom_extract(text):
    raise ValueError("bad pdf")


async def _seed_job(session, tmp_path):
    teacher = make_user(role=UserRole.teacher)
    session.add(teacher)
    await session.flush()
    cls = Class(id=uuid.uuid4(), name="Algo", teacher_id=teacher.id,
                invite_code=uuid.uuid4().hex[:6])
    session.add(cls)
    await session.flush()
    lab = Lab(id=uuid.uuid4(), class_id=cls.id, name="Lab 1")
    session.add(lab)
    await session.flush()
    doc = await create_document(
        session, class_id=cls.id, lab_id=lab.id, filename="doc.txt",
        content=b"para one\n\npara two", uploaded_by=teacher.id, storage_root=tmp_path,
    )
    return doc


@pytest.mark.asyncio
async def test_process_one_runs_job_and_marks_done(pg_session, tmp_path):
    doc = await _seed_job(pg_session, tmp_path)

    processed = await process_one(pg_session, embed_fn=fake_embed, extract_fn=fake_extract)

    assert processed is True
    await pg_session.refresh(doc)
    assert doc.status == DocumentStatus.indexed

    job = (await pg_session.execute(
        IngestionJob.__table__.select().where(IngestionJob.document_id == doc.id)
    )).first()
    assert job.status == JobStatus.done


@pytest.mark.asyncio
async def test_process_one_returns_false_when_queue_empty(pg_session):
    processed = await process_one(pg_session, embed_fn=fake_embed, extract_fn=fake_extract)
    assert processed is False


@pytest.mark.asyncio
async def test_process_one_marks_job_failed_on_error(pg_session, tmp_path):
    doc = await _seed_job(pg_session, tmp_path)

    processed = await process_one(pg_session, embed_fn=fake_embed, extract_fn=boom_extract)

    assert processed is True
    await pg_session.refresh(doc)
    assert doc.status == DocumentStatus.failed

    job = (await pg_session.execute(
        IngestionJob.__table__.select().where(IngestionJob.document_id == doc.id)
    )).first()
    assert job.status == JobStatus.failed
    assert "bad pdf" in (job.error_message or "")
