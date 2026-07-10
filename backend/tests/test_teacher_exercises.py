"""
test_teacher_exercises.py — [v8.0 §10] manual exercise add/delete.

POST   /documents/{id}/exercises          — add a missed exercise
DELETE /documents/{id}/exercises/{eid}     — remove a phantom exercise

Both are ownership-checked. A hand-added exercise is edited_by_teacher (ingest
preserves it) and its number_normalized is derived by the shared normalizer.
"""

import uuid

import pytest
from sqlalchemy import select

from backend.app.models import (
    Class, Document, DocType, Exercise, HintStatus, Lab, UserRole,
)
from backend.tests.conftest import make_client, make_user


async def _seed_doc(session):
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
    doc = Document(id=uuid.uuid4(), class_id=cls.id, lab_id=lab.id, filename="td.pdf",
                   storage_path="/x", content_hash=uuid.uuid4().hex,
                   uploaded_by=teacher.id, doc_type=DocType.TD)
    session.add(doc)
    await session.commit()
    return teacher, doc


@pytest.mark.asyncio
async def test_add_exercise_derives_normalized_number(db_session):
    teacher, doc = await _seed_doc(db_session)
    client = await make_client(db_session, teacher)
    try:
        resp = await client.post(
            f"/api/teacher/documents/{doc.id}/exercises",
            json={"number": "Exercice 4", "statement": "Reverse a list."},
        )
    finally:
        await client.aclose()

    assert resp.status_code == 201
    body = resp.json()
    assert body["number"] == "Exercice 4"

    ex = (await db_session.execute(
        select(Exercise).where(Exercise.document_id == doc.id)
    )).scalar_one()
    assert ex.number_normalized == 4          # shared normalizer
    assert ex.edited_by_teacher is True        # hand-added → ingest preserves it
    assert ex.hint_status == HintStatus.none
    assert ex.lab_id == doc.lab_id


@pytest.mark.asyncio
async def test_delete_exercise_removes_it(db_session):
    teacher, doc = await _seed_doc(db_session)
    ex = Exercise(id=uuid.uuid4(), document_id=doc.id, class_id=doc.class_id,
                  lab_id=doc.lab_id, number="9", number_normalized=9,
                  statement="phantom")
    db_session.add(ex)
    await db_session.commit()

    client = await make_client(db_session, teacher)
    try:
        resp = await client.delete(
            f"/api/teacher/documents/{doc.id}/exercises/{ex.id}"
        )
    finally:
        await client.aclose()

    assert resp.status_code == 204
    remaining = (await db_session.execute(
        select(Exercise).where(Exercise.document_id == doc.id)
    )).scalars().all()
    assert remaining == []


@pytest.mark.asyncio
async def test_add_exercise_rejects_foreign_document(db_session):
    teacher, doc = await _seed_doc(db_session)
    intruder = make_user(role=UserRole.teacher)
    db_session.add(intruder)
    await db_session.commit()
    client = await make_client(db_session, intruder)
    try:
        resp = await client.post(
            f"/api/teacher/documents/{doc.id}/exercises",
            json={"number": "1", "statement": "x"},
        )
    finally:
        await client.aclose()

    assert resp.status_code == 404
