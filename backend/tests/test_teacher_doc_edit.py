"""
test_teacher_doc_edit.py — teacher corrects an extracted chunk.

PUT /api/teacher/documents/{id}/chunks/{chunk_id}     — re-embed + re-index

The exercise-edit endpoint went with the Exercises table.

Runs on SQLite via make_client; the embedder is an overridable dependency so no
live embedding server is needed. Ownership is enforced via the document's class.
"""

import uuid

import pytest
from sqlalchemy import select

from backend.app.main import app
from backend.app.models import (
    Class, DocChunk, Document, DocumentStatus, Lab, UserRole,
)
from backend.app.routers.teacher import get_embed_fn
from backend.tests.conftest import make_client, make_user


async def fake_embed(texts):
    # A distinctive vector so the test can assert the re-embed actually ran.
    return [[0.5, 0.6, 0.7] for _ in texts]


async def _seed_doc_with_artifacts(session):
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
    doc = Document(
        id=uuid.uuid4(), class_id=cls.id, lab_id=lab.id, filename="cm.pdf",
        storage_path="/tmp/cm.pdf", content_hash="hash123",
        status=DocumentStatus.indexed, uploaded_by=teacher.id,
    )
    session.add(doc)
    await session.flush()
    chunk = DocChunk(
        id=uuid.uuid4(), document_id=doc.id, class_id=cls.id, lab_id=lab.id,
        chunk_index=0,
        content="old text", context="section ctx", embedding=[0.1, 0.2, 0.3],
    )
    session.add(chunk)
    await session.commit()
    return teacher, doc, chunk


@pytest.mark.asyncio
async def test_teacher_edits_chunk_reembeds(db_session):
    teacher, doc, chunk = await _seed_doc_with_artifacts(db_session)
    app.dependency_overrides[get_embed_fn] = lambda: fake_embed
    client = await make_client(db_session, teacher)
    try:
        resp = await client.put(
            f"/api/teacher/documents/{doc.id}/chunks/{chunk.id}",
            json={"content": "corrected text"},
        )
    finally:
        await client.aclose()
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    assert resp.json()["content"] == "corrected text"

    await db_session.refresh(chunk)
    assert chunk.content == "corrected text"
    # The re-embed ran: the stored vector is the fake's, not the original.
    assert list(chunk.embedding) == [0.5, 0.6, 0.7]
    # [v7.3] The correction is flagged so re-ingestion preserves it.
    assert chunk.edited_by_teacher is True


@pytest.mark.asyncio
async def test_chunk_edit_rejected_for_foreign_teacher(db_session):
    _, doc, chunk = await _seed_doc_with_artifacts(db_session)
    intruder = make_user(role=UserRole.teacher)
    db_session.add(intruder)
    await db_session.commit()

    app.dependency_overrides[get_embed_fn] = lambda: fake_embed
    client = await make_client(db_session, intruder)
    try:
        resp = await client.put(
            f"/api/teacher/documents/{doc.id}/chunks/{chunk.id}",
            json={"content": "hijack"},
        )
    finally:
        await client.aclose()
        app.dependency_overrides.clear()

    assert resp.status_code == 404  # not the owner → document invisible
    await db_session.refresh(chunk)
    assert chunk.content == "old text"


@pytest.mark.asyncio
async def test_chunk_not_in_document_404(db_session):
    teacher, doc, _ = await _seed_doc_with_artifacts(db_session)
    app.dependency_overrides[get_embed_fn] = lambda: fake_embed
    client = await make_client(db_session, teacher)
    try:
        resp = await client.put(
            f"/api/teacher/documents/{doc.id}/chunks/{uuid.uuid4()}",
            json={"content": "x"},
        )
    finally:
        await client.aclose()
        app.dependency_overrides.clear()

    assert resp.status_code == 404


