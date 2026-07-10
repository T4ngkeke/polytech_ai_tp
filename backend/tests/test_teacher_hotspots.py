"""
test_teacher_hotspots.py — [v8.0 §11C] teaching-hotspot panel.

GET /labs/{lab_id}/hotspots ranks the lab's most-asked exercises from
RouterQueryLog (route=exercise), excluding teacher test-drives. Ownership-checked.
"""

import uuid

import pytest

from backend.app.models import Class, Lab, RouterQueryLog, UserRole
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


def _log(lab_id, number, *, route="exercise", is_test=False):
    return RouterQueryLog(id=uuid.uuid4(), message="q", route=route,
                          exercise_number=number, lab_id=lab_id, is_test=is_test)


@pytest.mark.asyncio
async def test_hotspots_rank_exercises_excluding_test_drives(db_session):
    teacher, cls, lab = await _seed_lab(db_session)
    db_session.add_all([
        _log(lab.id, 3), _log(lab.id, 3), _log(lab.id, 3),  # exercise 3 asked 3×
        _log(lab.id, 1),                                     # exercise 1 asked 1×
        _log(lab.id, None, route="rag"),                     # not an exercise query
        _log(lab.id, 3, is_test=True),                       # teacher test-drive excluded
    ])
    await db_session.commit()

    client = await make_client(db_session, teacher)
    try:
        resp = await client.get(f"/api/teacher/labs/{lab.id}/hotspots")
    finally:
        await client.aclose()

    assert resp.status_code == 200
    body = resp.json()
    assert body == [
        {"exercise_number": 3, "count": 3},
        {"exercise_number": 1, "count": 1},
    ]


@pytest.mark.asyncio
async def test_hotspots_reject_foreign_lab(db_session):
    teacher, cls, lab = await _seed_lab(db_session)
    intruder = make_user(role=UserRole.teacher)
    db_session.add(intruder)
    await db_session.commit()
    client = await make_client(db_session, intruder)
    try:
        resp = await client.get(f"/api/teacher/labs/{lab.id}/hotspots")
    finally:
        await client.aclose()

    assert resp.status_code == 403
