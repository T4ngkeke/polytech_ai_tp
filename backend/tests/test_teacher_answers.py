"""
test_teacher_answers.py — [v8.0 §10] teacher answer-view endpoint.

GET /labs/{lab_id}/answers — the teacher reads uploaded answer text and each
answer's pairing state (which exercise it links to, or null if unpaired). This
is the teacher/decision-B surface; the student path never touches Answers
(test_answers_isolation). Ownership-checked.
"""

import uuid

import pytest

from backend.app.models import (
    Answer, Class, Document, DocType, Exercise, Lab, UserRole,
)
from backend.tests.conftest import make_client, make_user


async def _seed_lab(session):
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
    return teacher, cls, lab


@pytest.mark.asyncio
async def test_list_lab_answers_returns_text_and_pairing(db_session):
    teacher, cls, lab = await _seed_lab(db_session)
    td = Document(id=uuid.uuid4(), class_id=cls.id, lab_id=lab.id, filename="td.pdf",
                  storage_path="/x", content_hash=uuid.uuid4().hex,
                  uploaded_by=teacher.id, doc_type=DocType.TD)
    corrige = Document(id=uuid.uuid4(), class_id=cls.id, lab_id=lab.id, filename="c.pdf",
                       storage_path="/y", content_hash=uuid.uuid4().hex,
                       uploaded_by=teacher.id, doc_type=DocType.corrige)
    db_session.add_all([td, corrige])
    await db_session.flush()
    ex = Exercise(id=uuid.uuid4(), document_id=td.id, class_id=cls.id, lab_id=lab.id,
                  number="1", number_normalized=1, statement="s")
    db_session.add(ex)
    await db_session.flush()
    db_session.add_all([
        Answer(id=uuid.uuid4(), document_id=corrige.id, number_raw="1",
               number_normalized=1, answer_text="42", exercise_id=ex.id),   # paired
        Answer(id=uuid.uuid4(), document_id=corrige.id, number_raw="2",
               number_normalized=2, answer_text="merge sort"),             # unpaired
    ])
    await db_session.commit()

    client = await make_client(db_session, teacher)
    try:
        resp = await client.get(f"/api/teacher/labs/{lab.id}/answers")
    finally:
        await client.aclose()

    assert resp.status_code == 200
    body = sorted(resp.json(), key=lambda a: a["number_normalized"])
    assert len(body) == 2
    assert body[0]["answer_text"] == "42"
    assert body[0]["exercise_id"] == str(ex.id)   # paired
    assert body[1]["answer_text"] == "merge sort"
    assert body[1]["exercise_id"] is None          # unpaired ambiguity is visible


@pytest.mark.asyncio
async def test_list_lab_answers_rejects_foreign_lab(db_session):
    teacher, cls, lab = await _seed_lab(db_session)
    intruder = make_user(role=UserRole.teacher)
    db_session.add(intruder)
    await db_session.commit()
    client = await make_client(db_session, intruder)
    try:
        resp = await client.get(f"/api/teacher/labs/{lab.id}/answers")
    finally:
        await client.aclose()

    assert resp.status_code == 403
