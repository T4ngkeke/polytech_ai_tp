"""
test_analytics.py — class analytics excludes teacher test-drives.

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


@pytest.mark.asyncio
async def test_teaching_analytics_students_activity_and_problems(db_session):
    from datetime import date, datetime, timedelta, timezone
    from backend.app.models import AgentRun, ClassStudent, MessageFeedback, QuotaGrant, UsageStat
    from backend.app.services.analytics_service import get_teaching_analytics

    teacher = make_user(role=UserRole.teacher)
    active, idle = make_user(username="active", quota=100), make_user(username="idle", quota=100)
    db_session.add_all([teacher, active, idle])
    await db_session.flush()
    cls = Class(id=uuid.uuid4(), name="Algo", teacher_id=teacher.id, invite_code=uuid.uuid4().hex[:6])
    db_session.add(cls)
    await db_session.flush()
    lab, other = Lab(id=uuid.uuid4(), class_id=cls.id, name="TP1"), Lab(id=uuid.uuid4(), class_id=cls.id, name="TP2")
    db_session.add_all([lab, other, ClassStudent(class_id=cls.id, student_id=active.id),
                        ClassStudent(class_id=cls.id, student_id=idle.id)])
    await db_session.flush()
    # Tuesday 2026-10-13 14:30 in Paris is 12:30 UTC.
    asked = datetime(2026, 10, 13, 12, 30, tzinfo=timezone.utc)
    now = asked + timedelta(days=1)
    sess, sess2 = Session(id=uuid.uuid4(), user_id=active.id, lab_id=lab.id), Session(id=uuid.uuid4(), user_id=active.id, lab_id=other.id)
    drive = Session(id=uuid.uuid4(), user_id=teacher.id, lab_id=lab.id, is_test=True)
    db_session.add_all([sess, sess2, drive])
    await db_session.flush()
    db_session.add_all([
        Message(session_id=sess.id, sender=SenderType.user, content="Qu'est-ce qu'un arbre ?", created_at=asked),
        Message(session_id=sess.id, sender=SenderType.llm, content="...", billed_tokens=30, answer_mode="search",
                feedback=MessageFeedback.down, created_at=asked + timedelta(seconds=5)),
        Message(session_id=sess2.id, sender=SenderType.user, content="Et un graphe ?", created_at=asked),
        Message(session_id=sess2.id, sender=SenderType.llm, content="...", billed_tokens=20, answer_mode="research",
                created_at=asked + timedelta(seconds=5)),
        Message(session_id=drive.id, sender=SenderType.user, content="test drive", created_at=asked),
        AgentRun(user_id=active.id, session_id=sess2.id, client_request_id="r1", question="Et un graphe ?",
                 status="finished", stop_reason="budget_exhausted", started_at=asked),
        UsageStat(user_id=active.id, date=date.today(), tokens_used=150),
        QuotaGrant(user_id=active.id, date=date.today(), tokens=40, granted_by=teacher.id),
    ])
    await db_session.commit()

    report = await get_teaching_analytics(db_session, cls.id, now=now)
    o = report["overview"]
    assert (o["members"], o["active_7d"], o["active_ever"], o["questions"], o["sessions"]) == (2, 1, 1, 2, 2)
    assert (o["thumbs_down"], o["at_limit_today"]) == (1, 1)            # 150 used >= 100 + 40
    assert report["modes"] == {"search": 1, "research": 1}
    assert report["heatmap"][1][14] == 2                                  # Tuesday, 14 h Paris
    assert sum(d["questions"] for d in report["daily"]) == 2
    by_name = {s["username"]: s for s in report["students"]}
    assert by_name["idle"]["questions"] == 0 and not by_name["idle"]["at_limit"]
    assert (by_name["active"]["limit_today"], by_name["active"]["granted_today"]) == (140, 40)
    assert report["thumbs_down"][0]["question"] == "Qu'est-ce qu'un arbre ?"
    assert [p["reason"] for p in report["problems"]] == ["Tool limit reached"]

    # One lab: the other lab's question and its problem are left out.
    only = await get_teaching_analytics(db_session, cls.id, lab.id, now=now)
    assert only["overview"]["questions"] == 1 and only["problems"] == []
