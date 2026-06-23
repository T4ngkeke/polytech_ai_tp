"""
test_learner_service.py — v7 prompt-literacy learner profile.

record_effort smooths the effort score (EMA = sliding window) per (student, lab)
and derives a coaching level via hysteresis. Cold start is neutral; teacher
override is respected. Structured values only — no free-text judgments.
"""

import uuid

import pytest

from backend.app.models import CoachingLevel, Lab, Class, UserRole
from backend.app.services import learner_service
from backend.tests.conftest import make_user


async def _student_and_lab(session):
    student = make_user(role=UserRole.student)
    teacher = make_user(role=UserRole.teacher)
    session.add_all([student, teacher])
    await session.flush()
    cls = Class(id=uuid.uuid4(), name="Algo", teacher_id=teacher.id,
                invite_code=uuid.uuid4().hex[:6])
    session.add(cls)
    await session.flush()
    lab = Lab(id=uuid.uuid4(), class_id=cls.id, name="Lab 1")
    session.add(lab)
    await session.flush()
    return student, lab


@pytest.mark.asyncio
async def test_first_score_creates_neutral_profile(db_session):
    student, lab = await _student_and_lab(db_session)

    profile = await learner_service.record_effort(
        db_session, user_id=student.id, lab_id=lab.id, score=0.2,
    )

    assert profile.samples == 1
    assert 0.15 <= profile.effort_ema <= 0.25
    assert profile.coaching_level == CoachingLevel.neutral  # cold start


@pytest.mark.asyncio
async def test_sustained_low_effort_lowers_coaching_after_warmup(db_session):
    student, lab = await _student_and_lab(db_session)

    profile = None
    for _ in range(4):  # past warmup, EMA well below low watermark
        profile = await learner_service.record_effort(
            db_session, user_id=student.id, lab_id=lab.id, score=0.1,
        )

    assert profile.samples == 4
    assert profile.coaching_level == CoachingLevel.low


@pytest.mark.asyncio
async def test_teacher_override_pins_level(db_session):
    student, lab = await _student_and_lab(db_session)
    profile = await learner_service.record_effort(
        db_session, user_id=student.id, lab_id=lab.id, score=0.9,
    )
    profile.teacher_override = True
    profile.coaching_level = CoachingLevel.high
    await db_session.commit()

    # Even with sustained low scores, the pinned level must not change.
    for _ in range(5):
        profile = await learner_service.record_effort(
            db_session, user_id=student.id, lab_id=lab.id, score=0.05,
        )

    assert profile.teacher_override is True
    assert profile.coaching_level == CoachingLevel.high
