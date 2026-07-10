"""
test_teacher_visibility.py — [v7.1] Phase 6 teacher ingestion-visibility endpoints.

The author is the ground-truth oracle for "was this processed well?", so a teacher can:
  * list a lab's documents with status + summary (pages / chunks / exercises) + warnings,
  * read the actual chunks (paginated, with page_no + Contextual-Retrieval context),
  * read extracted exercises (number / statement; no solution exists).
Ownership is enforced; runs on SQLite (plain reads, no vector ops).
"""

import uuid

import pytest
from sqlalchemy import select  # noqa: F401

from backend.app.main import app
from backend.app.models import (
    Audience, Class, DocChunk, Document, DocumentStatus, DocType,
    EMBEDDING_DIM, Exercise, Lab, UserRole,
)
from backend.tests.conftest import make_client, make_user


async def _seed_doc(session, *, status=DocumentStatus.indexed, chunks=2, exercises=1):
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
        storage_path="/x", content_hash=uuid.uuid4().hex, doc_type=DocType.TD,
        audience=Audience.student, status=status, page_count=3, uploaded_by=teacher.id,
    )
    session.add(doc)
    await session.flush()
    for i in range(chunks):
        session.add(DocChunk(
            id=uuid.uuid4(), document_id=doc.id, class_id=cls.id, lab_id=lab.id,
            doc_type=DocType.TD, audience=Audience.student, chunk_index=i,
            content=f"chunk {i} body", context=f"ctx {i}", section=f"Exercice {i}",
            embedding=[0.0] * EMBEDDING_DIM, page_no=i + 1,
        ))
    for i in range(exercises):
        session.add(Exercise(
            id=uuid.uuid4(), document_id=doc.id, class_id=cls.id, lab_id=lab.id,
            number=f"Exercice {i}", statement=f"statement {i}", hints=["a hint"],
        ))
    await session.commit()
    return teacher, cls, lab, doc


@pytest.mark.asyncio
async def test_list_lab_documents_with_status_and_summary(db_session):
    teacher, cls, lab, doc = await _seed_doc(db_session, chunks=2, exercises=1)
    client = await make_client(db_session, teacher)
    try:
        resp = await client.get(f"/api/teacher/labs/{lab.id}/documents")
    finally:
        await client.aclose()

    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    row = body[0]
    assert row["filename"] == "td.pdf"
    assert row["status"] == "indexed"
    assert row["doc_type"] == "TD"
    assert row["page_count"] == 3
    assert row["chunk_count"] == 2
    assert row["exercise_count"] == 1


@pytest.mark.asyncio
async def test_needs_review_document_surfaces_warning(db_session):
    teacher, cls, lab, doc = await _seed_doc(
        db_session, status=DocumentStatus.needs_review, chunks=0, exercises=0)
    doc.error_message = "Low character yield — looks scanned."
    db_session.add(doc)
    await db_session.commit()

    client = await make_client(db_session, teacher)
    try:
        resp = await client.get(f"/api/teacher/labs/{lab.id}/documents")
    finally:
        await client.aclose()

    row = resp.json()[0]
    assert row["status"] == "needs_review"
    assert "scanned" in (row["error_message"] or "")


@pytest.mark.asyncio
async def test_get_document_chunks_with_context_and_page(db_session):
    teacher, cls, lab, doc = await _seed_doc(db_session, chunks=3)
    client = await make_client(db_session, teacher)
    try:
        resp = await client.get(f"/api/teacher/documents/{doc.id}/chunks")
    finally:
        await client.aclose()

    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 3
    assert body[0]["content"] == "chunk 0 body"
    assert body[0]["context"] == "ctx 0"
    assert body[0]["page_no"] == 1
    assert body[0]["section"] == "Exercice 0"


@pytest.mark.asyncio
async def test_get_document_chunks_is_paginated(db_session):
    teacher, cls, lab, doc = await _seed_doc(db_session, chunks=5)
    client = await make_client(db_session, teacher)
    try:
        resp = await client.get(f"/api/teacher/documents/{doc.id}/chunks?offset=2&limit=2")
    finally:
        await client.aclose()

    body = resp.json()
    assert [c["chunk_index"] for c in body] == [2, 3]


@pytest.mark.asyncio
async def test_get_document_exercises_has_no_solution(db_session):
    teacher, cls, lab, doc = await _seed_doc(db_session, exercises=2)
    client = await make_client(db_session, teacher)
    try:
        resp = await client.get(f"/api/teacher/documents/{doc.id}/exercises")
    finally:
        await client.aclose()

    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 2
    assert body[0]["number"] == "Exercice 0"
    assert body[0]["statement"] == "statement 0"
    assert "solution" not in body[0]
    # [v8.0] the DocumentManager badge/blind flag need the lifecycle fields.
    assert "hint_status" in body[0]
    assert "hint_source" in body[0]


@pytest.mark.asyncio
async def test_document_chunks_ownership_enforced(db_session):
    teacher, cls, lab, doc = await _seed_doc(db_session)
    intruder = make_user(role=UserRole.teacher)
    db_session.add(intruder)
    await db_session.commit()

    client = await make_client(db_session, intruder)
    try:
        resp = await client.get(f"/api/teacher/documents/{doc.id}/chunks")
    finally:
        await client.aclose()

    assert resp.status_code in (403, 404)
