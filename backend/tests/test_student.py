"""
test_student.py — Tests for student endpoints (v5 production-ready).
"""

import uuid
from datetime import date

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.auth import create_access_token, hash_password
from backend.app.database import get_db
from backend.app.main import app
from backend.app.models import (
    Class, ClassStudent, Lab, Message, SenderType,
    Session, UsageStat, User, UserRole,
)
from tests.conftest import make_user


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def seed_data(db_session: AsyncSession) -> dict:
    teacher = make_user(role=UserRole.teacher, username="teacher1")
    student1 = make_user(role=UserRole.student, username="student1")
    student2 = make_user(role=UserRole.student, username="student2")
    db_session.add_all([teacher, student1, student2])
    await db_session.flush()

    cls = Class(name="Physics 101", teacher_id=teacher.id, invite_code="TEST01")
    db_session.add(cls)
    await db_session.flush()

    lab = Lab(class_id=cls.id, name="Mechanics Lab")
    db_session.add(lab)
    await db_session.flush()

    membership = ClassStudent(class_id=cls.id, student_id=student1.id)
    db_session.add(membership)
    await db_session.flush()

    sess = Session(user_id=student1.id, lab_id=lab.id, title="Session 1")
    db_session.add(sess)
    await db_session.flush()

    m1 = Message(session_id=sess.id, sender=SenderType.user, content="What is F=ma?")
    m2 = Message(session_id=sess.id, sender=SenderType.llm, content="Newton's second law.")
    db_session.add_all([m1, m2])

    usage = UsageStat(user_id=student1.id, date=date.today(),
                      tokens_used=500, request_count=3)
    db_session.add(usage)

    await db_session.commit()
    for obj in [teacher, student1, student2, cls, lab, sess]:
        await db_session.refresh(obj)
    return {
        "teacher": teacher, "student1": student1, "student2": student2,
        "class": cls, "lab": lab, "session": sess,
    }


@pytest_asyncio.fixture
async def client_s1(db_session, seed_data):
    async def override():
        yield db_session
    app.dependency_overrides[get_db] = override
    token = create_access_token(seed_data["student1"].id)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test",
                           headers={"Authorization": f"Bearer {token}"}) as c:
        yield c
    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def client_s2(db_session, seed_data):
    async def override():
        yield db_session
    app.dependency_overrides[get_db] = override
    token = create_access_token(seed_data["student2"].id)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test",
                           headers={"Authorization": f"Bearer {token}"}) as c:
        yield c
    app.dependency_overrides.clear()


# ===================================================================
# Original endpoints
# ===================================================================

class TestJoinClass:
    async def test_student_joins(self, client_s2, seed_data):
        resp = await client_s2.post("/api/student/classes/join",
                                    json={"invite_code": "TEST01"})
        assert resp.status_code == 201
        assert resp.json()["class_name"] == "Physics 101"

    async def test_case_insensitive(self, client_s2, seed_data):
        resp = await client_s2.post("/api/student/classes/join",
                                    json={"invite_code": "test01"})
        assert resp.status_code == 201

    async def test_invalid_code_404(self, client_s1):
        resp = await client_s1.post("/api/student/classes/join",
                                    json={"invite_code": "XXXXXX"})
        assert resp.status_code == 404

    async def test_already_joined_409(self, client_s1, seed_data):
        resp = await client_s1.post("/api/student/classes/join",
                                    json={"invite_code": "TEST01"})
        assert resp.status_code == 409

    async def test_wrong_length_422(self, client_s1):
        resp = await client_s1.post("/api/student/classes/join",
                                    json={"invite_code": "ABC"})
        assert resp.status_code == 422

    async def test_unauthenticated_401(self, db_session):
        async def override():
            yield db_session
        app.dependency_overrides[get_db] = override
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.post("/api/student/classes/join",
                                json={"invite_code": "TEST01"})
        app.dependency_overrides.clear()
        assert resp.status_code == 401


class TestListJoinedClasses:
    async def test_student1_sees_joined(self, client_s1, seed_data):
        resp = await client_s1.get("/api/student/classes")
        assert resp.status_code == 200
        assert len(resp.json()) == 1
        assert resp.json()[0]["name"] == "Physics 101"

    async def test_student2_sees_empty(self, client_s2):
        resp = await client_s2.get("/api/student/classes")
        assert resp.status_code == 200
        assert resp.json() == []

    async def test_soft_deleted_class_hidden(self, client_s1, seed_data, db_session):
        cls = seed_data["class"]
        cls.is_deleted = True
        db_session.add(cls)
        await db_session.flush()
        resp = await client_s1.get("/api/student/classes")
        assert resp.status_code == 200
        assert resp.json() == []


class TestListLabsForClass:
    async def test_enrolled_sees_labs(self, client_s1, seed_data):
        resp = await client_s1.get(
            f"/api/student/classes/{seed_data['class'].id}/labs")
        assert resp.status_code == 200
        assert len(resp.json()) == 1
        assert resp.json()[0]["name"] == "Mechanics Lab"

    async def test_non_enrolled_rejected_403(self, client_s2, seed_data):
        resp = await client_s2.get(
            f"/api/student/classes/{seed_data['class'].id}/labs")
        assert resp.status_code == 403


class TestCreateSessionInLab:
    async def test_enrolled_creates(self, client_s1, seed_data):
        resp = await client_s1.post(
            f"/api/student/labs/{seed_data['lab'].id}/sessions",
            json={"title": "My Chat"},
        )
        assert resp.status_code == 201
        assert resp.json()["title"] == "My Chat"
        assert resp.json()["is_deleted"] is False

    async def test_non_enrolled_rejected_403(self, client_s2, seed_data):
        resp = await client_s2.post(
            f"/api/student/labs/{seed_data['lab'].id}/sessions",
            json={"title": "X"},
        )
        assert resp.status_code == 403

    async def test_nonexistent_lab_404(self, client_s1):
        resp = await client_s1.post(
            f"/api/student/labs/{uuid.uuid4()}/sessions", json={"title": "X"})
        assert resp.status_code == 404

    async def test_null_title_allowed(self, client_s1, seed_data):
        resp = await client_s1.post(
            f"/api/student/labs/{seed_data['lab'].id}/sessions", json={})
        assert resp.status_code == 201
        assert resp.json()["title"] is None


class TestListSessions:
    async def test_student1_sees_own(self, client_s1, seed_data):
        resp = await client_s1.get("/api/student/sessions")
        assert resp.status_code == 200
        assert len(resp.json()) >= 1
        assert all(s["user_id"] == str(seed_data["student1"].id) for s in resp.json())

    async def test_student2_sees_empty(self, client_s2):
        resp = await client_s2.get("/api/student/sessions")
        assert resp.status_code == 200
        assert resp.json() == []

    async def test_filter_by_lab(self, client_s1, seed_data):
        resp = await client_s1.get(
            f"/api/student/sessions?lab_id={seed_data['lab'].id}")
        assert resp.status_code == 200
        assert all(s["lab_id"] == str(seed_data["lab"].id) for s in resp.json())

    async def test_nonexistent_lab_empty(self, client_s1):
        resp = await client_s1.get(
            f"/api/student/sessions?lab_id={uuid.uuid4()}")
        assert resp.status_code == 200
        assert resp.json() == []


class TestGetSession:
    async def test_get_own_session(self, client_s1, seed_data):
        resp = await client_s1.get(
            f"/api/student/sessions/{seed_data['session'].id}")
        assert resp.status_code == 200
        assert len(resp.json()["messages"]) == 2

    async def test_idor_returns_403(self, client_s2, seed_data):
        resp = await client_s2.get(
            f"/api/student/sessions/{seed_data['session'].id}")
        assert resp.status_code == 403

    async def test_nonexistent_404(self, client_s1):
        resp = await client_s1.get(f"/api/student/sessions/{uuid.uuid4()}")
        assert resp.status_code == 404


class TestUsage:
    async def test_student1_sees_usage(self, client_s1):
        resp = await client_s1.get("/api/student/usage")
        assert resp.status_code == 200
        assert resp.json()["used"] == 500

    async def test_student2_sees_zero(self, client_s2):
        resp = await client_s2.get("/api/student/usage")
        assert resp.status_code == 200
        assert resp.json()["used"] == 0


# ===================================================================
# New endpoints
# ===================================================================

class TestLeaveClass:
    async def test_student_leaves(self, client_s1, seed_data, db_session):
        cid = seed_data["class"].id
        resp = await client_s1.delete(f"/api/student/classes/{cid}/leave")
        assert resp.status_code == 204
        r = await db_session.execute(select(ClassStudent).where(
            ClassStudent.class_id == cid,
            ClassStudent.student_id == seed_data["student1"].id))
        assert r.scalar_one_or_none() is None

    async def test_not_member_404(self, client_s2, seed_data):
        resp = await client_s2.delete(
            f"/api/student/classes/{seed_data['class'].id}/leave")
        assert resp.status_code == 404

    async def test_nonexistent_class_404(self, client_s1):
        resp = await client_s1.delete(
            f"/api/student/classes/{uuid.uuid4()}/leave")
        assert resp.status_code == 404

    async def test_class_gone_from_list_after_leaving(
            self, client_s1, seed_data):
        await client_s1.delete(
            f"/api/student/classes/{seed_data['class'].id}/leave")
        resp = await client_s1.get("/api/student/classes")
        assert resp.status_code == 200
        assert resp.json() == []


class TestUpdateSessionTitle:
    async def test_updates_title(self, client_s1, seed_data):
        resp = await client_s1.put(
            f"/api/student/sessions/{seed_data['session'].id}",
            json={"title": "New Title"},
        )
        assert resp.status_code == 200
        assert resp.json()["title"] == "New Title"

    async def test_idor_returns_403(self, client_s2, seed_data):
        resp = await client_s2.put(
            f"/api/student/sessions/{seed_data['session'].id}",
            json={"title": "Hack"},
        )
        assert resp.status_code == 403

    async def test_nonexistent_404(self, client_s1):
        resp = await client_s1.put(
            f"/api/student/sessions/{uuid.uuid4()}",
            json={"title": "X"},
        )
        assert resp.status_code == 404


class TestSoftDeleteSession:
    async def test_soft_deletes(self, client_s1, seed_data, db_session):
        sid = seed_data["session"].id
        resp = await client_s1.delete(f"/api/student/sessions/{sid}")
        assert resp.status_code == 204
        await db_session.refresh(seed_data["session"])
        assert seed_data["session"].is_deleted is True

    async def test_idor_returns_403(self, client_s2, seed_data):
        resp = await client_s2.delete(
            f"/api/student/sessions/{seed_data['session'].id}")
        assert resp.status_code == 403

    async def test_already_deleted_404(self, client_s1, seed_data, db_session):
        sess = seed_data["session"]
        sess.is_deleted = True
        db_session.add(sess)
        await db_session.flush()
        resp = await client_s1.delete(f"/api/student/sessions/{sess.id}")
        assert resp.status_code == 404

    async def test_hidden_from_session_list(self, client_s1, seed_data):
        sid = seed_data["session"].id
        await client_s1.delete(f"/api/student/sessions/{sid}")
        resp = await client_s1.get("/api/student/sessions")
        assert resp.status_code == 200
        ids = [s["id"] for s in resp.json()]
        assert str(sid) not in ids
