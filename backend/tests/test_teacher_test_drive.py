"""
test_teacher_test_drive.py — [v8.0 §11A] teacher test-drive session.

POST /labs/{lab_id}/test-session creates an is_test session the teacher owns, so
they can chat as a preview (membership bypassed, draft hints visible, writes
excluded from analytics/LearnerProfile). Ownership-checked.
"""

import uuid

import pytest

from backend.app.models import Class, Lab, Session, UserRole
from backend.tests.conftest import make_client, make_user


async def _teacher_with_lab(session):
    teacher = make_user(role=UserRole.teacher)
    session.add(teacher)
    await session.flush()
    cls = Class(id=uuid.uuid4(), name="Algo", teacher_id=teacher.id,
                invite_code=uuid.uuid4().hex[:6])
    session.add(cls)
    await session.flush()
    lab = Lab(id=uuid.uuid4(), class_id=cls.id, name="Lab 1")
    session.add(lab)
    await session.commit()
    return teacher, cls, lab


@pytest.mark.asyncio
async def test_create_test_session_for_own_lab(db_session):
    teacher, cls, lab = await _teacher_with_lab(db_session)
    client = await make_client(db_session, teacher)
    try:
        resp = await client.post(f"/api/teacher/labs/{lab.id}/test-session")
    finally:
        await client.aclose()

    assert resp.status_code == 201
    session_id = uuid.UUID(resp.json()["session_id"])
    session = await db_session.get(Session, session_id)
    assert session.is_test is True
    assert session.user_id == teacher.id
    assert session.lab_id == lab.id


@pytest.mark.asyncio
async def test_create_test_session_rejects_foreign_lab(db_session):
    teacher, cls, lab = await _teacher_with_lab(db_session)
    intruder = make_user(role=UserRole.teacher)
    db_session.add(intruder)
    await db_session.commit()
    client = await make_client(db_session, intruder)
    try:
        resp = await client.post(f"/api/teacher/labs/{lab.id}/test-session")
    finally:
        await client.aclose()

    assert resp.status_code == 403
