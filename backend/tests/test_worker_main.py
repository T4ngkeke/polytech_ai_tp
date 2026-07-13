"""
test_worker_main.py — v7 worker step that ties the queue to the pipeline.

`process_one` claims one job (FOR UPDATE SKIP LOCKED → Postgres) and runs the
ingest pipeline, marking the job done/failed. Uses pg_session.

[v8.0] Exercise segmentation is LLM-primary via `classify_fn` (per-page line
classifier); regex is a log-only cross-check and a fallback. A classifier
failure never fails a job — the regex segmentation is kept.
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
    JobType,
    Lab,
    UserRole,
)
from backend.app.services.document_service import create_document
from backend.tests.conftest import make_user
from backend.worker.main import _parse_classified_lines, process_one


async def fake_embed(chunks):
    return [[0.0] * EMBEDDING_DIM for _ in chunks]


async def boom_embed(chunks):
    raise ValueError("embedding service down")


async def boom_classify(numbered_page, context):
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
async def test_process_one_dispatches_hint_generate_to_injected_runner(pg_session, tmp_path):
    """[v8.0 §10] The worker dispatches on job_type: a hint_generate job routes to
    the injected hint runner, never the ingest path. (embed_fn=boom proves the
    ingest branch is not taken — it would raise and fail the job.)"""
    doc = await _seed_job(pg_session, tmp_path)  # also enqueues an ingest job (priority 100)
    hint_job = IngestionJob(
        id=uuid.uuid4(), document_id=doc.id, job_type=JobType.hint_generate,
        payload={"document_id": str(doc.id)}, priority=0,  # urgent → claimed first
    )
    pg_session.add(hint_job)
    await pg_session.commit()

    seen = {}

    async def fake_run_hint_job(db, job):
        seen["job_id"] = job.id
        seen["job_type"] = job.job_type

    processed = await process_one(
        pg_session, embed_fn=boom_embed, run_hint_job=fake_run_hint_job,
    )

    assert processed is True
    assert seen["job_id"] == hint_job.id
    assert seen["job_type"] == JobType.hint_generate
    await pg_session.refresh(hint_job)
    assert hint_job.status == JobStatus.done
    await pg_session.refresh(doc)
    assert doc.status != DocumentStatus.indexed  # ingest path was NOT taken


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
async def test_process_one_done_when_classify_fails(pg_session, tmp_path):
    """[v8.0] The per-page classifier failing must not fail the job — the regex
    segmentation is kept as a fallback and the job completes."""
    body = b"Exercice 1\nCalculer la somme.\n\nExercice 2\nCalculer le produit.\n"
    doc = await _seed_job(pg_session, tmp_path, body=body)

    processed = await process_one(
        pg_session, embed_fn=fake_embed, classify_fn=boom_classify,
    )

    assert processed is True
    await pg_session.refresh(doc)
    assert doc.status == DocumentStatus.indexed

    job = (await pg_session.execute(
        IngestionJob.__table__.select().where(IngestionJob.document_id == doc.id)
    )).first()
    assert job.status == JobStatus.done


# ---------------------------------------------------------------------------
# _parse_classified_lines — robust JSON parsing (engine-agnostic, pure function)
# ---------------------------------------------------------------------------

def test_parse_classified_clean_json():
    out = _parse_classified_lines(
        '{"lines": [{"line_no": 1, "prefix": "Exercice 1", "kind": "exercise_heading"}]}'
    )
    assert out == [{"line_no": 1, "prefix": "Exercice 1", "kind": "exercise_heading"}]


def test_parse_classified_empty_or_whitespace_returns_empty():
    assert _parse_classified_lines("") == []
    assert _parse_classified_lines("   \n  ") == []
    assert _parse_classified_lines(None) == []


def test_parse_classified_strips_code_fence():
    fenced = '```json\n{"lines": [{"line_no": 2, "prefix": "II", "kind": "section_heading"}]}\n```'
    assert _parse_classified_lines(fenced) == [
        {"line_no": 2, "prefix": "II", "kind": "section_heading"}
    ]


def test_parse_classified_extracts_json_from_prose():
    prose = 'Sure! Here they are:\n{"lines": []}\nHope that helps.'
    assert _parse_classified_lines(prose) == []


def test_parse_classified_garbage_returns_empty():
    assert _parse_classified_lines("I could not find any exercises.") == []
    assert _parse_classified_lines("{not valid json") == []


def test_parse_classified_non_list_or_non_dict_items_filtered():
    assert _parse_classified_lines('{"lines": "oops"}') == []
    assert _parse_classified_lines(
        '{"lines": [{"line_no": 1, "prefix": "a", "kind": "subquestion"}, 42, null]}'
    ) == [{"line_no": 1, "prefix": "a", "kind": "subquestion"}]
