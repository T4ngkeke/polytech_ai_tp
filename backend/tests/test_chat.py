"""
test_chat.py — Tests for POST /api/chat/stream.

Coverage
--------
- Gate checks: session ownership, class membership, quota enforcement
- The research agent is the only chat path (its answers are tested in
  test_m4d_chat_pg.py, with the teacher's rules in test_direct_mode.py)
"""

import uuid
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.auth import create_access_token
from backend.app.database import get_db
from backend.app.main import app
from backend.app.models import (
    ChatRefusal,
    Class,
    ClassStudent,
    Lab,
    Message,
    RuleItem,
    SenderType,
    Session,
    SystemConfig,
    UsageStat,
    UserRole,
)
from backend.app.services import quota_service
from tests.conftest import make_client, make_user


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def seed_chat(db_session: AsyncSession) -> dict:
    """
    Seed:
    - teacher, student1 (enrolled), student2 (NOT enrolled)
    - 1 Class, 1 Lab, student1 enrolled
    - 1 Session for student1 in the lab (with 2 old messages)
    - 1 Session for student1 without lab (legacy)
    - SystemConfig rows for LLM
    - 3-tier rules: class, lab, student
    """
    teacher = make_user(role=UserRole.teacher, username="teacher")
    student1 = make_user(role=UserRole.student, username="student1", quota=100)
    student2 = make_user(role=UserRole.student, username="student2", quota=100)

    db_session.add_all([teacher, student1, student2])
    await db_session.flush()

    cls = Class(name="Chat Class", teacher_id=teacher.id, invite_code="CHAT01")
    db_session.add(cls)
    await db_session.flush()

    lab = Lab(class_id=cls.id, name="Chat Lab")
    db_session.add(lab)
    await db_session.flush()

    # Enroll student1
    db_session.add(ClassStudent(class_id=cls.id, student_id=student1.id))
    await db_session.flush()

    # Session with lab
    sess1 = Session(user_id=student1.id, lab_id=lab.id, title="Lab Chat")
    # Session without lab (legacy)
    sess_legacy = Session(user_id=student1.id, lab_id=None, title="Legacy Chat")
    # Session for student2 (not enrolled)
    sess2 = Session(user_id=student2.id, lab_id=lab.id, title="Unauthorized")
    db_session.add_all([sess1, sess_legacy, sess2])
    await db_session.flush()

    # Old messages in sess1
    m1 = Message(session_id=sess1.id, sender=SenderType.user, content="hi")
    m2 = Message(session_id=sess1.id, sender=SenderType.llm, content="hello")
    db_session.add_all([m1, m2])

    # SystemConfig
    configs = [
        SystemConfig(key="LLM_BASE_URL", value="http://test:11434/v1"),
        SystemConfig(key="LLM_API_KEY", value="test-key"),
        SystemConfig(key="LLM_MODEL", value="test-model"),
    ]
    db_session.add_all(configs)

    # Three-tier rules
    rule_class = RuleItem(level="class", target_id=cls.id, class_id=cls.id, text="Class: Be educational.")
    rule_lab = RuleItem(level="lab", target_id=lab.id, class_id=cls.id, text="Lab: Focus on physics.")
    rule_student = RuleItem(level="student", target_id=student1.id, class_id=cls.id, text="Student: Speak French.")
    db_session.add_all([rule_class, rule_lab, rule_student])

    await db_session.commit()
    for obj in [teacher, student1, student2, cls, lab, sess1, sess_legacy, sess2]:
        await db_session.refresh(obj)

    return {
        "teacher": teacher, "student1": student1, "student2": student2,
        "class": cls, "lab": lab,
        "sess1": sess1, "sess_legacy": sess_legacy, "sess2": sess2,
        "rule_class": rule_class, "rule_lab": rule_lab, "rule_student": rule_student,
    }


@pytest_asyncio.fixture
async def client1(db_session: AsyncSession, seed_chat) -> AsyncClient:
    async def override_get_db():
        yield db_session
    app.dependency_overrides[get_db] = override_get_db
    token = create_access_token(seed_chat["student1"].id)
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://test",
        headers={"Authorization": f"Bearer {token}"},
    ) as c:
        yield c
    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def client2(db_session: AsyncSession, seed_chat) -> AsyncClient:
    async def override_get_db():
        yield db_session
    app.dependency_overrides[get_db] = override_get_db
    token = create_access_token(seed_chat["student2"].id)
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://test",
        headers={"Authorization": f"Bearer {token}"},
    ) as c:
        yield c
    app.dependency_overrides.clear()


# ===================================================================
# 1. Gate checks
# ===================================================================


class TestGateChecks:
    async def test_unauthenticated_returns_401(self, db_session):
        async def override_get_db():
            yield db_session
        app.dependency_overrides[get_db] = override_get_db
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.post("/api/chat/stream", json={
                "session_id": str(uuid.uuid4()), "message": "hi",
            })
        app.dependency_overrides.clear()
        assert resp.status_code == 401

    async def test_session_not_found_returns_404(self, client1):
        resp = await client1.post("/api/chat/stream", json={
            "session_id": str(uuid.uuid4()), "message": "hi",
        })
        assert resp.status_code == 404

    async def test_idor_session_returns_404(self, client1, seed_chat):
        """student1 tries to use student2's session."""
        resp = await client1.post("/api/chat/stream", json={
            "session_id": str(seed_chat["sess2"].id), "message": "hi",
        })
        assert resp.status_code == 404

    async def test_non_member_rejected_403(self, client2, seed_chat):
        """student2 (not enrolled) tries to chat in a lab session."""
        resp = await client2.post("/api/chat/stream", json={
            "session_id": str(seed_chat["sess2"].id), "message": "hi",
        })
        assert resp.status_code == 403

    async def test_owner_test_session_bypasses_membership(self, db_session, seed_chat):
        """The owning teacher's is_test session bypasses the student
        membership check — they aren't enrolled in their own class, but a
        test-drive is theirs to open."""
        teacher = seed_chat["teacher"]
        test_sess = Session(user_id=teacher.id, lab_id=seed_chat["lab"].id,
                            title="Test drive", is_test=True)
        db_session.add(test_sess)
        await db_session.commit()

        async def override_get_db():
            yield db_session
        app.dependency_overrides[get_db] = override_get_db
        token = create_access_token(teacher.id)
        transport = ASGITransport(app=app)
        try:
            async with AsyncClient(
                transport=transport, base_url="http://test",
                headers={"Authorization": f"Bearer {token}"},
            ) as c:
                resp = await c.post("/api/chat/stream", json={
                    "session_id": str(test_sess.id), "message": "hi",
                })
            # Not 403: past the membership check (the agent is off in this test).
            assert resp.status_code == 503 and resp.json()["detail"] == "chat_not_enabled"
        finally:
            app.dependency_overrides.clear()

    async def test_quota_exceeded_returns_429(self, client1, seed_chat, db_session):
        # Max out the quota (the quota day is the date in Paris).
        usage = UsageStat(user_id=seed_chat["student1"].id, date=quota_service.today(), tokens_used=100)
        db_session.add(usage)
        await db_session.commit()

        resp = await client1.post("/api/chat/stream", json={
            "session_id": str(seed_chat["sess1"].id), "message": "hi",
        })
        assert resp.status_code == 429 and resp.json()["detail"] == "day_quota_reached"
        refusal = await db_session.scalar(select(ChatRefusal))
        assert refusal.reason == "day_quota" and refusal.user_id == seed_chat["student1"].id
        assert refusal.lab_id == seed_chat["sess1"].lab_id

    async def test_week_quota_blocks_with_room_left_today(self, client1, seed_chat, db_session):
        student = seed_chat["student1"]
        student.weekly_token_quota = 50
        db_session.add(UsageStat(user_id=student.id, date=quota_service.today(), tokens_used=60))
        await db_session.commit()
        body = (await client1.get("/api/student/usage")).json()
        assert body["blocked"] == "week" and body["used"] == 60 and body["limit"] == 100
        resp = await client1.post("/api/chat/stream", json={
            "session_id": str(seed_chat["sess1"].id), "message": "hi",
        })
        assert resp.status_code == 429 and resp.json()["detail"] == "week_quota_reached"

    async def test_teacher_refill_lets_the_student_ask_again(self, client1, seed_chat, db_session):
        # The student used the whole quota (100); the teacher refills it.
        db_session.add(UsageStat(user_id=seed_chat["student1"].id, date=quota_service.today(), tokens_used=100))
        await db_session.commit()
        body = (await client1.get("/api/student/usage")).json()
        assert (body["used"], body["limit"], body["blocked"], body["reset_requested"]) == (100, 100, "day", False)
        class_id, student1, student2 = seed_chat["class"].id, seed_chat["student1"], seed_chat["student2"]
        # The student asks for a reset: one open request at a time.
        asked = await client1.post("/api/student/usage/reset-request")
        twice = await client1.post("/api/student/usage/reset-request")
        assert asked.json()["sent"] is True and asked.json()["reset_requested"] is True
        assert twice.json()["sent"] is False
        teacher = await make_client(db_session, seed_chat["teacher"])
        async with teacher:
            requests = (await teacher.get("/api/teacher/quota-requests")).json()
            refill = await teacher.post(f"/api/teacher/classes/{class_id}/students/{student1.id}/quota-refill")
            again = await teacher.post(f"/api/teacher/classes/{class_id}/students/{student1.id}/quota-refill")
            outside = await teacher.post(f"/api/teacher/classes/{class_id}/students/{student2.id}/quota-refill")
            after = (await teacher.get("/api/teacher/quota-requests")).json()
        assert [(r["username"], r["blocked"], r["used"]) for r in requests] == [("student1", "day", 100)]
        assert requests[0]["classes"] == [{"id": str(class_id), "name": "Chat Class"}]
        assert after == []                                                  # answered by the refill
        assert {k: refill.json()[k] for k in ("used", "limit", "granted")} == {"used": 100, "limit": 200, "granted": 100}
        assert {k: again.json()[k] for k in ("used", "limit", "granted")} == {"used": 100, "limit": 200, "granted": 100}
        assert refill.json()["week_limit"] == 25_000_000 + 100              # the week is full again too
        assert outside.status_code == 404                                    # not in this class
        stranger = make_user(role=UserRole.teacher)
        db_session.add(stranger)
        await db_session.commit()
        other = await make_client(db_session, stranger)
        async with other:
            foreign = await other.post(f"/api/teacher/classes/{class_id}/students/{student1.id}/quota-refill")
            foreign_all = await other.post(f"/api/teacher/classes/{class_id}/quota-refill-all")
            assert (await other.get("/api/teacher/quota-requests")).json() == []
        assert foreign.status_code in (403, 404) and foreign_all.status_code in (403, 404)
        # Usage is not rewritten; the student is past the quota check (the agent is off here).
        usage = await db_session.scalar(select(UsageStat).where(UsageStat.user_id == student1.id))
        assert usage.tokens_used == 100
        client1.headers["Authorization"] = f"Bearer {create_access_token(student1.id)}"
        body = (await client1.get("/api/student/usage")).json()
        assert (body["used"], body["limit"], body["blocked"]) == (100, 200, None)
        assert (await client1.post("/api/student/usage/reset-request")).status_code == 409
        resp = await client1.post("/api/chat/stream", json={
            "session_id": str(seed_chat["sess1"].id), "message": "hi",
        })
        assert resp.status_code == 503 and resp.json()["detail"] == "chat_not_enabled"
        # At the limit again the same day: a new request is possible after the reset.
        usage.tokens_used = 200
        await db_session.commit()
        assert (await client1.post("/api/student/usage/reset-request")).json()["sent"] is True

    async def test_teacher_can_ignore_a_request(self, client1, seed_chat, db_session):
        student1 = seed_chat["student1"]
        db_session.add(UsageStat(user_id=student1.id, date=quota_service.today(), tokens_used=100))
        await db_session.commit()
        assert (await client1.post("/api/student/usage/reset-request")).json()["reset_state"] == "asked"
        teacher = await make_client(db_session, seed_chat["teacher"])
        async with teacher:
            ignored = await teacher.post(f"/api/teacher/quota-requests/{student1.id}/ignore")
            twice = await teacher.post(f"/api/teacher/quota-requests/{student1.id}/ignore")
            assert (await teacher.get("/api/teacher/quota-requests")).json() == []
        assert ignored.status_code == 200 and twice.status_code == 409
        # The limits stay, and the student cannot ask again today.
        body = (await client1.get("/api/student/usage")).json()
        assert (body["blocked"], body["reset_state"], body["reset_requested"]) == ("day", "ignored", False)
        again = (await client1.post("/api/student/usage/reset-request")).json()
        assert again["sent"] is False and again["reset_state"] == "ignored"
        # Undo: the request is open again.
        teacher = await make_client(db_session, seed_chat["teacher"])
        async with teacher:
            assert (await teacher.post(f"/api/teacher/quota-requests/{student1.id}/unignore")).status_code == 200
            assert [r["username"] for r in (await teacher.get("/api/teacher/quota-requests")).json()] == ["student1"]
            refill = await teacher.post(f"/api/teacher/classes/{seed_chat['class'].id}/students/{student1.id}/quota-refill")
        assert refill.status_code == 200
        assert (await client1.get("/api/student/usage")).json()["reset_state"] == "reset"
        # Another teacher cannot ignore it.
        stranger = make_user(role=UserRole.teacher)
        db_session.add(stranger)
        await db_session.commit()
        other = await make_client(db_session, stranger)
        async with other:
            assert (await other.post(f"/api/teacher/quota-requests/{student1.id}/ignore")).status_code == 404

    async def test_teacher_refills_the_whole_class(self, client1, seed_chat, db_session):
        student1, student2 = seed_chat["student1"], seed_chat["student2"]
        db_session.add(UsageStat(user_id=student1.id, date=quota_service.today(), tokens_used=100))
        await db_session.commit()
        teacher = await make_client(db_session, seed_chat["teacher"])
        async with teacher:
            out = await teacher.post(f"/api/teacher/classes/{seed_chat['class'].id}/quota-refill-all")
        assert out.status_code == 200
        assert out.json()["refilled"] == 1 and out.json()["students"] >= 1
        u1 = await quota_service.usage(db_session, student1)
        u2 = await quota_service.usage(db_session, student2)
        assert u1.blocked is None and u1.limit == 200 and u1.used == 100
        assert u2.granted == 0                                              # nothing used, nothing granted

    async def test_locked_lab_rejected_403(self, client1, seed_chat, db_session):
        # A locked (is_active=False) lab must reject chat.
        lab = seed_chat["lab"]
        lab.is_active = False
        db_session.add(lab)
        await db_session.commit()

        resp = await client1.post("/api/chat/stream", json={
            "session_id": str(seed_chat["sess1"].id), "message": "hi",
        })
        assert resp.status_code == 403

    async def test_deleted_lab_rejected_403(self, client1, seed_chat, db_session):
        lab = seed_chat["lab"]
        lab.is_deleted = True
        db_session.add(lab)
        await db_session.commit()

        resp = await client1.post("/api/chat/stream", json={
            "session_id": str(seed_chat["sess1"].id), "message": "hi",
        })
        assert resp.status_code == 403


# ===================================================================
# 2. Three-tier rule injection


# ===================================================================
# The research agent is the only chat path
# ===================================================================


class TestResearchAgentOnly:
    async def test_chat_needs_the_research_agent(self, client1, seed_chat):
        """With AGENTIC_SEARCH_ENABLED off there is no other path: a clear 503."""
        resp = await client1.post("/api/chat/stream", json={
            "session_id": str(seed_chat["sess1"].id), "message": "hi"})
        assert resp.status_code == 503 and resp.json()["detail"] == "chat_not_enabled"

    async def test_session_without_lab_is_refused(self, client1, seed_chat, monkeypatch):
        from backend.app.config import settings
        monkeypatch.setattr(settings, "AGENTIC_SEARCH_ENABLED", True)
        resp = await client1.post("/api/chat/stream", json={
            "session_id": str(seed_chat["sess_legacy"].id), "message": "hi"})
        assert resp.status_code == 400 and resp.json()["detail"] == "session_without_lab"
