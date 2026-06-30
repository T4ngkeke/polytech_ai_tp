"""
test_worker_main.py — v7 worker step that ties the queue to the pipeline.

`process_one` claims one job (FOR UPDATE SKIP LOCKED → Postgres) and runs the
ingest pipeline, marking the job done/failed. Uses pg_session.
"""

import uuid

import pytest

from backend.app.models import (
    Audience,
    Class,
    DocType,
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
from backend.worker.main import _parse_exercises, process_one


async def fake_embed(chunks):
    return [[0.0] * EMBEDDING_DIM for _ in chunks]


async def fake_extract(text):
    return [{"number": "Ex 1", "statement": "do it", "hints": None,
             "concept": None}]


async def boom_extract(text):
    raise ValueError("bad pdf")


async def boom_embed(chunks):
    raise ValueError("embedding service down")


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
        content=b"Exercice 1\npara one\n\npara two", uploaded_by=teacher.id,
        storage_root=tmp_path, doc_type=DocType.TD, audience=Audience.student,
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
    """A genuine indexing failure (embedding service down) fails the job."""
    doc = await _seed_job(pg_session, tmp_path)

    processed = await process_one(pg_session, embed_fn=boom_embed, extract_fn=fake_extract)

    assert processed is True
    await pg_session.refresh(doc)
    assert doc.status == DocumentStatus.failed

    job = (await pg_session.execute(
        IngestionJob.__table__.select().where(IngestionJob.document_id == doc.id)
    )).first()
    assert job.status == JobStatus.failed
    assert "embedding service down" in (job.error_message or "")


@pytest.mark.asyncio
async def test_process_one_done_when_extraction_fails(pg_session, tmp_path):
    """[v7.2] A failing extractor must not fail the job: chunks index and the job
    completes with 0 exercises (exercise extraction is best-effort)."""
    doc = await _seed_job(pg_session, tmp_path)

    processed = await process_one(pg_session, embed_fn=fake_embed, extract_fn=boom_extract)

    assert processed is True
    await pg_session.refresh(doc)
    assert doc.status == DocumentStatus.indexed

    job = (await pg_session.execute(
        IngestionJob.__table__.select().where(IngestionJob.document_id == doc.id)
    )).first()
    assert job.status == JobStatus.done


# ---------------------------------------------------------------------------
# _parse_exercises — robust JSON parsing (engine-agnostic, pure function)
# ---------------------------------------------------------------------------

def test_parse_exercises_clean_json():
    out = _parse_exercises('{"exercises": [{"number": "1", "statement": "do it"}]}')
    assert out == [{"number": "1", "statement": "do it"}]


def test_parse_exercises_empty_or_whitespace_returns_empty():
    assert _parse_exercises("") == []
    assert _parse_exercises("   \n  ") == []
    assert _parse_exercises(None) == []


def test_parse_exercises_strips_code_fence():
    fenced = '```json\n{"exercises": [{"number": "II", "statement": "x"}]}\n```'
    assert _parse_exercises(fenced) == [{"number": "II", "statement": "x"}]


def test_parse_exercises_extracts_json_from_prose():
    prose = 'Sure! Here are the exercises:\n{"exercises": []}\nHope that helps.'
    assert _parse_exercises(prose) == []


def test_parse_exercises_garbage_returns_empty():
    assert _parse_exercises("I could not find any exercises.") == []
    assert _parse_exercises("{not valid json") == []


def test_parse_exercises_non_list_exercises_returns_empty():
    assert _parse_exercises('{"exercises": "oops"}') == []
