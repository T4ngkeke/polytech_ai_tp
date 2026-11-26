"""test_exam_hides_old_chats.py — during an exam, a student does not see the
conversations started before it (services/session_service.visible_in_exam),
in the list, when opening one, in the search and in the export, and cannot
go on with one. They come back when exam mode is off; the teacher's audit
sees them all the time."""
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from backend.app.models import Class, ClassStudent, Lab, Message, SenderType, Session, UserRole
from backend.tests.conftest import make_client, make_user


async def _seed(db):
    teacher, student = make_user(role=UserRole.teacher), make_user(username="lea")
    db.add_all([teacher, student])
    await db.flush()
    cls = Class(id=uuid.uuid4(), name="Algo", teacher_id=teacher.id, invite_code=uuid.uuid4().hex[:6])
    db.add(cls)
    await db.flush()
    lab = Lab(id=uuid.uuid4(), class_id=cls.id, name="TP1")
    db.add_all([lab, ClassStudent(class_id=cls.id, student_id=student.id)])
    await db.flush()
    before = Session(id=uuid.uuid4(), user_id=student.id, lab_id=lab.id, title="Pointeurs",
                     created_at=datetime.now(timezone.utc) - timedelta(hours=1))
    db.add(before)
    await db.flush()
    db.add(Message(session_id=before.id, sender=SenderType.user, content="Qu'est-ce qu'un pointeur ?"))
    await db.commit()
    return teacher, student, cls, lab, before


@pytest.mark.asyncio
async def test_an_exam_hides_the_conversations_from_before_it(db_session):
    teacher, student, cls, lab, before = await _seed(db_session)
    async with await make_client(db_session, teacher) as client:
        on = await client.put(f"/api/teacher/classes/{cls.id}/exam-mode", json={"exam_mode": True})
        assert on.status_code == 200
    await db_session.refresh(cls)
    assert cls.exam_mode and cls.exam_mode_since is not None
    during = Session(id=uuid.uuid4(), user_id=student.id, lab_id=lab.id, title="Examen")
    db_session.add(during)
    await db_session.commit()

    async with await make_client(db_session, student) as client:
        listed = (await client.get(f"/api/student/sessions?lab_id={lab.id}")).json()
        assert [s["id"] for s in listed] == [str(during.id)]
        assert (await client.get(f"/api/student/sessions/{before.id}")).status_code == 404
        assert (await client.get(f"/api/student/sessions/{during.id}")).status_code == 200
        assert (await client.get("/api/student/search", params={"q": "pointeur"})).json() == []
        exported = (await client.get("/api/student/export")).text
        assert "Pointeurs" not in exported and "Examen" in exported
        # Going on with it is refused like a missing conversation.
        resp = await client.post("/api/chat/stream", json={"session_id": str(before.id), "message": "Et ensuite ?"})
        assert resp.status_code == 404
        assert (await client.get(f"/api/chat/sessions/{before.id}/history-start")).status_code == 404

    # The teacher's audit still has it.
    async with await make_client(db_session, teacher) as client:
        audit = (await client.get("/api/teacher/chat-history", params={"lab_id": str(lab.id), "student_id": str(student.id)})).json()
        assert str(before.id) in {s["id"] for s in audit}
        off = await client.put(f"/api/teacher/classes/{cls.id}/exam-mode", json={"exam_mode": False})
        assert off.status_code == 200
    await db_session.refresh(cls)
    assert cls.exam_mode_since is None

    async with await make_client(db_session, student) as client:
        listed = (await client.get(f"/api/student/sessions?lab_id={lab.id}")).json()
        assert {s["id"] for s in listed} == {str(before.id), str(during.id)}
        assert (await client.get(f"/api/student/sessions/{before.id}")).status_code == 200
        assert len((await client.get("/api/student/search", params={"q": "pointeur"})).json()) == 1
