"""
test_ingest.py — v7 worker ingestion pipeline tests.

The pipeline takes injected embed/extract functions, so these run on SQLite
with fakes (no live LLM / embedding service needed).
"""

import uuid

import pytest
from sqlalchemy import select

from backend.app.models import (
    Class,
    DocChunk,
    Document,
    DocumentStatus,
    Exercise,
    Lab,
    UserRole,
)
from backend.app.services.document_service import create_document
from backend.tests.conftest import make_user
from backend.worker.ingest import ingest_document


async def fake_embed(chunks):
    """Return a tiny deterministic vector per chunk."""
    return [[0.1, 0.2, 0.3] for _ in chunks]


async def fake_extract(text):
    return [
        {"number": "Exercise 1", "statement": "Sum two numbers.",
         "hints": "use +", "concept": None},
    ]


async def _seed_document(session, tmp_path, body: str = "para one\n\npara two"):
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
        content=body.encode(), uploaded_by=teacher.id, storage_root=tmp_path,
    )
    return doc


@pytest.mark.asyncio
async def test_ingest_writes_chunks_and_exercises_and_marks_indexed(db_session, tmp_path):
    doc = await _seed_document(db_session, tmp_path)

    await ingest_document(db_session, doc.id, embed_fn=fake_embed, extract_fn=fake_extract)

    await db_session.refresh(doc)
    assert doc.status == DocumentStatus.indexed

    chunks = (await db_session.execute(
        select(DocChunk).where(DocChunk.document_id == doc.id)
    )).scalars().all()
    assert len(chunks) == 2  # two paragraphs
    assert all(c.lab_id == doc.lab_id for c in chunks)

    exercises = (await db_session.execute(
        select(Exercise).where(Exercise.document_id == doc.id)
    )).scalars().all()
    assert len(exercises) == 1
    assert exercises[0].number == "Exercise 1"


@pytest.mark.asyncio
async def test_ingest_is_idempotent_on_rerun(db_session, tmp_path):
    doc = await _seed_document(db_session, tmp_path)

    await ingest_document(db_session, doc.id, embed_fn=fake_embed, extract_fn=fake_extract)
    await ingest_document(db_session, doc.id, embed_fn=fake_embed, extract_fn=fake_extract)

    chunks = (await db_session.execute(
        select(DocChunk).where(DocChunk.document_id == doc.id)
    )).scalars().all()
    exercises = (await db_session.execute(
        select(Exercise).where(Exercise.document_id == doc.id)
    )).scalars().all()
    assert len(chunks) == 2   # not 4
    assert len(exercises) == 1  # not 2


async def boom_extract(text):
    raise ValueError("bad pdf")


@pytest.mark.asyncio
async def test_ingest_marks_failed_and_rolls_back_on_error(db_session, tmp_path):
    doc = await _seed_document(db_session, tmp_path)

    with pytest.raises(ValueError):
        await ingest_document(db_session, doc.id, embed_fn=fake_embed, extract_fn=boom_extract)

    await db_session.refresh(doc)
    assert doc.status == DocumentStatus.failed
    assert "bad pdf" in (doc.error_message or "")

    # Partial chunk writes must not survive the failure.
    chunks = (await db_session.execute(
        select(DocChunk).where(DocChunk.document_id == doc.id)
    )).scalars().all()
    assert chunks == []
