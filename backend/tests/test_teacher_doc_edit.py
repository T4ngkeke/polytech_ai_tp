"""
test_teacher_doc_edit.py — [v7.2] teacher edits chunks + exercises.

PUT /api/teacher/documents/{id}/chunks/{chunk_id}     — re-embed + re-index
PUT /api/teacher/documents/{id}/exercises/{ex_id}      — correct extraction

Runs on SQLite via make_client; the embedder is an overridable dependency so no
live embedding server is needed. Ownership is enforced via the document's class.
"""

import uuid

import pytest
from sqlalchemy import select

from backend.app.main import app
from backend.app.models import (
    Audience, Class, DocChunk, Document, DocumentStatus, DocType, Exercise, Lab, UserRole,
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
        id=uuid.uuid4(), class_id=cls.id, lab_id=lab.id, filename="td.pdf",
        storage_path="/tmp/td.pdf", content_hash="hash123",
        doc_type=DocType.TD, audience=Audience.student,
        status=DocumentStatus.indexed, uploaded_by=teacher.id,
    )
    session.add(doc)
    await session.flush()
    chunk = DocChunk(
        id=uuid.uuid4(), document_id=doc.id, class_id=cls.id, lab_id=lab.id,
        doc_type=DocType.TD, audience=Audience.student, chunk_index=0,
        content="old text", context="section ctx", embedding=[0.1, 0.2, 0.3],
    )
    session.add(chunk)
    ex = Exercise(
        id=uuid.uuid4(), document_id=doc.id, class_id=cls.id, lab_id=lab.id,
        audience=Audience.student, number="Exercice 1", number_normalized=1,
        statement="old statement", hints=None,
    )
    session.add(ex)
    await session.commit()
    return teacher, doc, chunk, ex


@pytest.mark.asyncio
async def test_teacher_edits_chunk_reembeds(db_session):
    teacher, doc, chunk, _ = await _seed_doc_with_artifacts(db_session)
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
async def test_teacher_edits_exercise_renormalizes_number(db_session):
    teacher, doc, _, ex = await _seed_doc_with_artifacts(db_session)
    client = await make_client(db_session, teacher)
    try:
        resp = await client.put(
            f"/api/teacher/documents/{doc.id}/exercises/{ex.id}",
            json={"number": "Exercice III", "statement": "fixed statement"},
        )
    finally:
        await client.aclose()
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    body = resp.json()
    assert body["number"] == "Exercice III"
    assert body["statement"] == "fixed statement"

    await db_session.refresh(ex)
    assert ex.statement == "fixed statement"
    assert ex.number_normalized == 3  # re-derived from the new label
    # [v7.3] The correction is flagged so re-ingestion preserves it.
    assert ex.edited_by_teacher is True


@pytest.mark.asyncio
async def test_teacher_edit_uses_heading_normalizer_like_ingest(db_session):
    """[v8.0] A teacher edit must re-derive number_normalized with the SAME
    normalizer ingest/answers use (`normalize_heading_number`): read the leading
    ordinal token, not the first digit anywhere. A heading with a title digit
    ("III - Base 2 et base 16") is exercise 3 (roman), not 2 — otherwise the
    edit silently breaks corrigé answer pairing (paired by number_normalized)."""
    teacher, doc, _, ex = await _seed_doc_with_artifacts(db_session)
    client = await make_client(db_session, teacher)
    try:
        resp = await client.put(
            f"/api/teacher/documents/{doc.id}/exercises/{ex.id}",
            json={"number": "III - Base 2 et base 16", "statement": "s"},
        )
    finally:
        await client.aclose()
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    await db_session.refresh(ex)
    assert ex.number_normalized == 3  # heading semantics (roman III), not 2


@pytest.mark.asyncio
async def test_chunk_edit_rejected_for_foreign_teacher(db_session):
    _, doc, chunk, _ = await _seed_doc_with_artifacts(db_session)
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
    teacher, doc, _, _ = await _seed_doc_with_artifacts(db_session)
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


@pytest.mark.asyncio
async def test_exercise_edit_rejected_for_foreign_teacher(db_session):
    _, doc, _, ex = await _seed_doc_with_artifacts(db_session)
    intruder = make_user(role=UserRole.teacher)
    db_session.add(intruder)
    await db_session.commit()

    client = await make_client(db_session, intruder)
    try:
        resp = await client.put(
            f"/api/teacher/documents/{doc.id}/exercises/{ex.id}",
            json={"statement": "hijack"},
        )
    finally:
        await client.aclose()
        app.dependency_overrides.clear()

    assert resp.status_code == 404
    await db_session.refresh(ex)
    assert ex.statement == "old statement"
