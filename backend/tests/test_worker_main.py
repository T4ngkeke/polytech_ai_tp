"""
test_worker_main.py — v7 worker step that ties the queue to the pipeline.

`process_one` claims one job (FOR UPDATE SKIP LOCKED → Postgres) and runs the
ingest pipeline, marking the job done/failed. Uses pg_session.

[v7.3] The full-text LLM extractor is gone: exercises are built
deterministically from segmentation labels, and `resegment_fn` is the only
LLM hook (consulted on numbering anomalies; its failure never fails a job).
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
from backend.worker.main import _parse_anchors, process_one


async def fake_embed(chunks):
    return [[0.0] * EMBEDDING_DIM for _ in chunks]


async def boom_embed(chunks):
    raise ValueError("embedding service down")


async def boom_resegment(text):
    raise ValueError("aux model down")


async def _seed_job(
    session, tmp_path, *,
    doc_type: DocType = DocType.TD,
    body: bytes = b"Exercice 1\npara one\n\npara two",
):
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
        content=body, uploaded_by=teacher.id,
        storage_root=tmp_path, doc_type=doc_type, audience=Audience.student,
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
    """A genuine indexing failure (embedding service down) fails the job.
    CM body — under the strict split only the chunk path calls the embedder."""
    doc = await _seed_job(pg_session, tmp_path, doc_type=DocType.CM,
                          body=b"Un paragraphe de cours.")

    processed = await process_one(pg_session, embed_fn=boom_embed)

    assert processed is True
    await pg_session.refresh(doc)
    assert doc.status == DocumentStatus.failed

    job = (await pg_session.execute(
        IngestionJob.__table__.select().where(IngestionJob.document_id == doc.id)
    )).first()
    assert job.status == JobStatus.failed
    assert "embedding service down" in (job.error_message or "")


@pytest.mark.asyncio
async def test_process_one_done_when_resegment_fails(pg_session, tmp_path):
    """[v7.3] The re-segmentation LLM failing must not fail the job — the
    regex segmentation is kept and the job completes."""
    oversplit = b"Exercice 1\nCalculer :\n1. la somme\n2. le produit\n"
    doc = await _seed_job(pg_session, tmp_path, body=oversplit)

    processed = await process_one(
        pg_session, embed_fn=fake_embed, resegment_fn=boom_resegment,
    )

    assert processed is True
    await pg_session.refresh(doc)
    assert doc.status == DocumentStatus.indexed

    job = (await pg_session.execute(
        IngestionJob.__table__.select().where(IngestionJob.document_id == doc.id)
    )).first()
    assert job.status == JobStatus.done


# ---------------------------------------------------------------------------
# _parse_anchors — robust JSON parsing (engine-agnostic, pure function)
# ---------------------------------------------------------------------------

def test_parse_anchors_clean_json():
    out = _parse_anchors('{"anchors": ["Exercice 1", "Exercice 2"]}')
    assert out == ["Exercice 1", "Exercice 2"]


def test_parse_anchors_empty_or_whitespace_returns_empty():
    assert _parse_anchors("") == []
    assert _parse_anchors("   \n  ") == []
    assert _parse_anchors(None) == []


def test_parse_anchors_strips_code_fence():
    fenced = '```json\n{"anchors": ["Exercice II"]}\n```'
    assert _parse_anchors(fenced) == ["Exercice II"]


def test_parse_anchors_extracts_json_from_prose():
    prose = 'Sure! Here are the boundaries:\n{"anchors": []}\nHope that helps.'
    assert _parse_anchors(prose) == []


def test_parse_anchors_garbage_returns_empty():
    assert _parse_anchors("I could not find any exercises.") == []
    assert _parse_anchors("{not valid json") == []


def test_parse_anchors_non_list_or_non_string_items_filtered():
    assert _parse_anchors('{"anchors": "oops"}') == []
    assert _parse_anchors('{"anchors": ["ok", 42, null]}') == ["ok"]
