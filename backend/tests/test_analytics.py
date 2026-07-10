"""
test_analytics.py — [v8.0 §11A] class analytics excludes teacher test-drives.

Test-drive sessions (is_test) are the teacher previewing their own lab; their
tokens/requests must not inflate the class usage report.
"""

import uuid

import pytest

from backend.app.models import (
    Class, Lab, Message, SenderType, Session, UserRole,
)
from backend.app.services.analytics_service import get_class_analytics
from backend.tests.conftest import make_user


@pytest.mark.asyncio
async def test_class_analytics_excludes_test_drive_sessions(db_session):
    teacher = make_user(role=UserRole.teacher)
    student = make_user(role=UserRole.student)
    db_session.add_all([teacher, student])
    await db_session.flush()
    cls = Class(id=uuid.uuid4(), name="Algo", teacher_id=teacher.id,
                invite_code=uuid.uuid4().hex[:6])
    db_session.add(cls)
    await db_session.flush()
    lab = Lab(id=uuid.uuid4(), class_id=cls.id, name="Lab 1")
    db_session.add(lab)
    await db_session.flush()

    student_sess = Session(id=uuid.uuid4(), user_id=student.id, lab_id=lab.id)
    test_sess = Session(id=uuid.uuid4(), user_id=teacher.id, lab_id=lab.id, is_test=True)
    db_session.add_all([student_sess, test_sess])
    await db_session.flush()
    db_session.add_all([
        Message(id=uuid.uuid4(), session_id=student_sess.id, sender=SenderType.llm,
                content="real", billed_tokens=10),
        Message(id=uuid.uuid4(), session_id=test_sess.id, sender=SenderType.llm,
                content="preview", billed_tokens=999),
    ])
    await db_session.commit()

    report = await get_class_analytics(db_session, cls.id, cls.name)

    assert report.total_requests == 1    # only the student's exchange
    assert report.total_tokens == 10      # the test-drive's 999 is excluded
