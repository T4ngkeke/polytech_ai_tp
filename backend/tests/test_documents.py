"""
test_documents.py — ingestion data model tests.

Covers the Document state-machine table (pending → processing → indexed → failed).
"""

import uuid

import pytest

from backend.app.models import (
    Class,
    DocChunk,
    Document,
    DocumentStatus,
    IngestionJob,
    JobStatus,
    Lab,
    UserRole,
)
from backend.tests.conftest import make_user


async def _make_teacher_and_class(db_session):
    """Persist a teacher + owned class, returning (teacher, class)."""
    teacher = make_user(role=UserRole.teacher)
    db_session.add(teacher)
    await db_session.flush()

    cls = Class(
        id=uuid.uuid4(),
        name="Algorithms",
        teacher_id=teacher.id,
        invite_code="ABC123",
    )
    db_session.add(cls)
    await db_session.flush()
    return teacher, cls


@pytest.mark.asyncio
async def test_document_defaults_to_pending_status(db_session):
    teacher, cls = await _make_teacher_and_class(db_session)

    doc = Document(
        id=uuid.uuid4(),
        class_id=cls.id,
        filename="td3.pdf",
        storage_path="/data/documents/td3.pdf",
        content_hash="deadbeef",
        uploaded_by=teacher.id,
    )
    db_session.add(doc)
    await db_session.flush()
    await db_session.refresh(doc)

    assert doc.status == DocumentStatus.pending


@pytest.mark.asyncio
async def test_document_lab_id_is_optional_for_class_wide_material(db_session):
    teacher, cls = await _make_teacher_and_class(db_session)

    doc = Document(
        id=uuid.uuid4(),
        class_id=cls.id,
        lab_id=None,
        filename="syllabus.pdf",
        storage_path="/data/documents/syllabus.pdf",
        content_hash="cafe",
        uploaded_by=teacher.id,
    )
    db_session.add(doc)
    await db_session.flush()
    await db_session.refresh(doc)

    assert doc.lab_id is None


@pytest.mark.asyncio
async def test_ingestion_job_defaults_to_queued(db_session):
    teacher, cls = await _make_teacher_and_class(db_session)
    doc = Document(
        id=uuid.uuid4(),
        class_id=cls.id,
        filename="td3.pdf",
        storage_path="/data/documents/td3.pdf",
        content_hash="deadbeef",
        uploaded_by=teacher.id,
    )
    db_session.add(doc)
    await db_session.flush()

    job = IngestionJob(id=uuid.uuid4(), document_id=doc.id)
    db_session.add(job)
    await db_session.flush()
    await db_session.refresh(job)

    assert job.status == JobStatus.queued
    assert job.attempts == 0


@pytest.mark.asyncio
async def test_docchunk_stores_embedding_and_lab_scope(db_session):
    teacher, cls = await _make_teacher_and_class(db_session)
    lab = Lab(id=uuid.uuid4(), class_id=cls.id, name="Threads Lab")
    db_session.add(lab)
    await db_session.flush()

    doc = Document(
        id=uuid.uuid4(),
        class_id=cls.id,
        lab_id=lab.id,
        filename="threads.pdf",
        storage_path="/data/documents/threads.pdf",
        content_hash="beef",
        uploaded_by=teacher.id,
    )
    db_session.add(doc)
    await db_session.flush()

    chunk = DocChunk(
        id=uuid.uuid4(),
        document_id=doc.id,
        class_id=cls.id,
        lab_id=lab.id,
        chunk_index=0,
        content="Multithreading lets a process run multiple threads concurrently.",
        embedding=[0.1, 0.2, 0.3],
        page_no=1,
    )
    db_session.add(chunk)
    await db_session.flush()
    await db_session.refresh(chunk)

    assert chunk.lab_id == lab.id
    assert list(chunk.embedding) == [0.1, 0.2, 0.3]


# ---------------------------------------------------------------------------
# Markdown ingestion: _default_parse splits an .md file into heading
# pseudo-pages and still runs the character-yield gate (a renamed junk file
# must not slip through). Other non-PDF suffixes keep the single-page test
# fallback untouched.
# ---------------------------------------------------------------------------
