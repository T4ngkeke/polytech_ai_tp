"""
test_worker_main.py — the worker step that ties the queue to the pipeline.

`process_one` claims one job (FOR UPDATE SKIP LOCKED → Postgres) and runs the
ingest pipeline, marking the job done/failed. Uses pg_session.

There is one job type now. Hint generation is gone, and so is the per-page LLM
line classifier that segmented exercises — with it the `classify_fn` injection
point and the `_parse_classified_lines` parser it fed.
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


async def boom_embed(chunks):
    raise ValueError("embedding service down")


async def _seed_job(session, tmp_path, *, body: bytes = b"para one\n\npara two"):
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
        content=body, uploaded_by=teacher.id, storage_root=tmp_path,
    )
    return doc


@pytest.mark.asyncio
async def test_process_one_runs_job_and_marks_done(pg_session, tmp_path):
    doc = await _seed_job(pg_session, tmp_path)

    processed = await process_one(pg_session, embed_fn=fake_embed)

    assert processed is True
    await pg_session.refresh(doc)
    assert doc.status == DocumentStatus.indexed

    job = (await pg_session.execute(
        IngestionJob.__table__.select().where(IngestionJob.document_id == doc.id)
    )).first()
    assert job.status == JobStatus.done


@pytest.mark.asyncio
async def test_process_one_returns_false_when_queue_empty(pg_session):
    processed = await process_one(pg_session, embed_fn=fake_embed)
    assert processed is False


@pytest.mark.asyncio
async def test_process_one_marks_job_failed_on_error(pg_session, tmp_path):
    """A genuine indexing failure (embedding service down) fails the job."""
    doc = await _seed_job(pg_session, tmp_path, body=b"Un paragraphe de cours.")

    processed = await process_one(pg_session, embed_fn=boom_embed)

    assert processed is True
    await pg_session.refresh(doc)
    assert doc.status == DocumentStatus.failed

    job = (await pg_session.execute(
        IngestionJob.__table__.select().where(IngestionJob.document_id == doc.id)
    )).first()
    assert job.status == JobStatus.failed
    assert "embedding service down" in (job.error_message or "")


def test_classifier_plumbing_is_gone():
    """The per-page line classifier only ever fed exercise segmentation."""
    import backend.worker.main as worker_main

    assert not hasattr(worker_main, "_parse_classified_lines")
    assert not hasattr(worker_main, "run_hint_job")
